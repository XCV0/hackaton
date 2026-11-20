"""LLM-клиент: OpenAI-совместимый /chat/completions. Настройки — в таблице settings, журнал — в llm_calls."""
import json, os, re, time
import httpx
from . import db

PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "json_mode": True},
    "lmstudio": {"base_url": "http://127.0.0.1:1234/v1", "model": "local-model", "json_mode": False},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3.5:4b", "json_mode": True},
    "orcarouter": {"base_url": "https://api.orcarouter.ai/v1", "model": "deepseek/deepseek-v4-flash-free", "json_mode": False},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash", "json_mode": False},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "json_mode": True},
    "custom": {"base_url": "", "model": "", "json_mode": False},
}
DEFAULTS = {"provider": "deepseek", "workers": 3}
API_KEY_ENV = {"deepseek": "DEEPSEEK_API_KEY", "groq": "GROQ_API_KEY"}
FIXED_ENDPOINTS = {"groq", "ollama"}
RETRIES = 3
RETRY_SLEEP = 3.0       # секунд * номер попытки; в тестах обнуляется
TIMEOUT = 600
GROQ_MAX_RETRY_AFTER = 30
MAX_TOKENS = 8000


class LLMError(Exception):
    pass


# ---------- настройки ----------
def _raw_settings():
    return {r["key"]: r["value"] for r in db.q("SELECT key, value FROM settings")}


def _cast(key, v):
    if v is None:
        return v
    if key == "workers":
        try:
            return max(1, int(v))
        except Exception:
            return DEFAULTS["workers"]
    if v in ("true", "false"):
        return v == "true"
    return v


def effective_settings():
    """Все настройки с подстановкой пресета и ключа из env (с ключом!). Только для внутреннего использования."""
    raw = _raw_settings()
    s = {k: _cast(k, v) for k, v in raw.items()}
    provider = s.get("provider") or DEFAULTS["provider"]
    preset = PRESETS.get(provider, PRESETS["custom"])
    s["provider"] = provider
    if provider in FIXED_ENDPOINTS or not s.get("base_url"):
        s["base_url"] = preset["base_url"]
    s["model"] = s.get("model") or preset["model"]
    s["workers"] = 1 if provider == "ollama" else s.get("workers") or DEFAULTS["workers"]
    key_provider = s.pop("api_key_provider", None) or provider
    env_name = API_KEY_ENV.get(provider)
    saved_key = raw.get("api_key", "") if key_provider == provider and provider != "ollama" else ""
    s["api_key"] = saved_key or (os.environ.get(env_name, "").strip() if env_name else "")
    s["json_mode"] = preset["json_mode"]
    return s


def get_settings(public=False):
    s = effective_settings()
    s.pop("json_mode", None)
    if public:
        s["key_set"] = bool(s.get("api_key"))
        s.pop("api_key", None)
    return s


def save_settings(patch):
    """Сохраняет только переданные ключи. Пустой api_key = не менять; patch['clear_api_key']=True удаляет ключ.
    При смене provider на пресет без явных base_url/model подставляются значения пресета."""
    patch = dict(patch or {})
    clear = patch.pop("clear_api_key", False)
    patch.pop("api_key_provider", None)
    prov = patch.get("provider")
    if prov and prov not in PRESETS:
        raise ValueError("Неизвестный провайдер: %s (допустимы %s)" % (prov, ", ".join(PRESETS)))
    if "workers" in patch:
        try:
            patch["workers"] = max(1, min(32, int(patch["workers"])))
        except Exception:
            raise ValueError("workers должно быть целым числом")
    with db.tx() as c:
        c.execute("BEGIN IMMEDIATE")
        raw = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
        old_provider = raw.get("provider") or DEFAULTS["provider"]
        provider = prov or old_provider
        if prov in PRESETS and prov != "custom" and prov != old_provider:
            for k in ("base_url", "model"):
                if not patch.get(k):
                    patch[k] = PRESETS[prov][k]
        if not patch.get("api_key") or provider == "ollama":
            patch.pop("api_key", None)
        if patch.get("api_key"):
            patch["api_key_provider"] = provider
        elif raw.get("api_key") and not raw.get("api_key_provider"):
            patch["api_key_provider"] = old_provider
        for k, v in patch.items():
            if v is None:
                continue
            val = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, val))
        if clear:
            c.execute("DELETE FROM settings WHERE key IN ('api_key', 'api_key_provider')")
    return get_settings(public=True)


# ---------- разбор JSON ----------
def parse_json(text):
    """JSON из ответа модели; усечённый ответ чинится: берутся все полные элементы массивов верхнего уровня и полные скалярные поля."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    if start < 0:
        raise LLMError("Модель вернула не-JSON ответ")
    dec = json.JSONDecoder()
    out, i, n = {}, start + 1, len(text)
    while i < n:
        while i < n and text[i] in " \n\r\t,":
            i += 1
        if i >= n or text[i] != '"':
            break
        try:
            key, i = dec.raw_decode(text, i)
        except Exception:
            break
        while i < n and text[i] in " \n\r\t":
            i += 1
        if i >= n or text[i] != ":":
            break
        i += 1
        while i < n and text[i] in " \n\r\t":
            i += 1
        try:
            val, j = dec.raw_decode(text, i)
            out[key] = val
            i = j
            continue
        except Exception:
            pass
        if i < n and text[i] == "[":        # усечённый массив: достаём полные объекты
            items, k = [], i + 1
            while True:
                while k < n and text[k] in " \n\r\t,":
                    k += 1
                if k >= n or text[k] not in '{["':
                    break
                try:
                    obj, k = dec.raw_decode(text, k)
                except Exception:
                    break
                items.append(obj)
            out[key] = items
        break
    if not out:
        raise LLMError("Модель вернула не-JSON ответ")
    return out


# ---------- вызов ----------
def _log(pub_id, stage, model, prompt, response, ok, error, ms):
    try:
        db.ex("INSERT INTO llm_calls(pub_id,stage,model,prompt,response,prompt_chars,response_chars,ok,error,ms,ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (pub_id, stage, model, prompt, response, len(prompt or ""), len(response or ""), 1 if ok else 0, error, ms, db.now()))
    except Exception:
        pass


def _chat(s, system, user, max_tokens=MAX_TOKENS, json_mode=None):
    """Один запрос с ретраями. Возвращает (текст ответа, ms). Бросает LLMError с понятным текстом."""
    base = (s["base_url"] or "").rstrip("/")
    if not base:
        raise LLMError("Не задан адрес API (base_url). Укажите его в настройках модели.")
    provider = s["provider"]
    if provider in API_KEY_ENV and not s["api_key"]:
        name = "Groq" if provider == "groq" else "DeepSeek"
        raise LLMError(f"Не задан API-ключ {name}. Укажите его в разделе «Модель и ключ» или в {API_KEY_ENV[provider]}.")
    headers = {"Content-Type": "application/json"}
    if s["api_key"]:
        headers["Authorization"] = "Bearer " + s["api_key"]
    body = {"model": s["model"], "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    options = {"timeout": TIMEOUT}
    if provider == "groq":
        body["max_completion_tokens"] = body.pop("max_tokens")
        if s["model"] in ("openai/gpt-oss-120b", "openai/gpt-oss-20b"):
            body["reasoning_effort"] = "low"
        options["timeout"] = httpx.Timeout(90, connect=5)
    elif provider == "ollama":
        body["reasoning_effort"] = "none"
        options.update(trust_env=False, timeout=httpx.Timeout(TIMEOUT, connect=3))
    if s["json_mode"] if json_mode is None else json_mode:
        body["response_format"] = {"type": "json_object"}
    t0, last = time.time(), None
    for a in range(RETRIES + 1):
        delay = RETRY_SLEEP * (a + 1)
        try:
            r = httpx.post(base + "/chat/completions", headers=headers, json=body, **options)
        except httpx.HTTPError as e:
            last = "сеть: %s" % (str(e) or type(e).__name__)
        else:
            code = r.status_code
            if code in (401, 403):
                raise LLMError("API отклонил ключ (HTTP %d). Проверьте API-ключ и права доступа в настройках." % code)
            if code == 402:
                raise LLMError("Недостаточно средств на аккаунте провайдера (HTTP 402). Пополните баланс.")
            if code == 404:
                raise LLMError("API не нашёл модель или адрес (HTTP 404): проверьте base_url и название модели «%s»." % s["model"])
            if code == 429 or code >= 500:
                last = "HTTP %d" % code
                if provider == "groq" and code == 429:
                    retry_after = r.headers.get("Retry-After", "")
                    if retry_after.isascii() and retry_after.isdecimal():
                        seconds = int(retry_after)
                        if seconds > GROQ_MAX_RETRY_AFTER:
                            raise LLMError(f"Лимит Groq (HTTP 429). API просит подождать {seconds} с. Можно выбрать Ollama и повторить извлечение статьи.")
                        delay = max(delay, seconds)
            elif code >= 400:
                raise LLMError("API вернул ошибку HTTP %d: %s" % (code, r.text[:300]))
            else:
                try:
                    msg = r.json()["choices"][0]["message"]["content"] or ""
                except Exception:
                    raise LLMError("Неожиданный формат ответа API: %s" % r.text[:300])
                return msg, int((time.time() - t0) * 1000)
        if a < RETRIES:
            time.sleep(delay)
    raise LLMError("Модель не ответила после %d попыток (%s). Лимит запросов или недоступность сервиса — повторите позже." % (RETRIES + 1, last))


def call_json(system, user, pub_id=None, stage=None, max_tokens=MAX_TOKENS):
    s = effective_settings()
    prompt = "[system]\n%s\n\n[user]\n%s" % (system, user)
    resp, t0 = "", time.time()
    try:
        resp, _ = _chat(s, system, user, max_tokens)
        data = parse_json(resp)
        if not isinstance(data, (dict, list)):
            raise LLMError("Модель вернула JSON не того вида")
        if isinstance(data, list):
            data = {"items": data}
    except LLMError as e:
        _log(pub_id, stage, s["model"], prompt, resp, False, str(e), int((time.time() - t0) * 1000))
        raise
    _log(pub_id, stage, s["model"], prompt, resp, True, None, int((time.time() - t0) * 1000))
    return data


def test_connection():
    """Проверка настроек: короткий запрос. {'ok':bool,'message':str,'ms':int,'model':str}"""
    s = effective_settings()
    try:
        demo_provider = s["provider"] in FIXED_ENDPOINTS
        resp, ms = _chat(s, "Отвечай только JSON.", 'Верни JSON {"ok": true}', max_tokens=512 if demo_provider else 30)
        if demo_provider:
            try:
                probe = json.loads(resp)
            except (TypeError, ValueError):
                probe = None
            if not isinstance(probe, dict) or probe.get("ok") is not True:
                raise LLMError("Модель ответила, но не вернула ожидаемый JSON. Проверьте модель перед запуском извлечения.")
        return {"ok": True, "message": "Соединение работает", "ms": ms, "model": s["model"], "reply": resp[:100]}
    except LLMError as e:
        return {"ok": False, "message": str(e), "ms": 0, "model": s["model"]}
