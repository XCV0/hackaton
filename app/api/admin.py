"""Администрирование (admin): пользователи, корпус, конвейер, LLM-журнал, OCR, конфиги, настройки, аудит."""
import re, pathlib, threading
import yaml
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from .. import db, auth, config, passport as pp, fts
from . import common as C

router = APIRouter(prefix="/api", dependencies=[Depends(auth.admin)])
adm = Depends(auth.admin)

# ---------- пользователи ----------


class UserIn(BaseModel):
    name: str
    password: str
    role: str = "researcher"


class UserPatch(BaseModel):
    role: str = None
    password: str = None


def _user_out(u):
    return {"name": u["name"], "role": u["role"], "created": u["created"]}


def _admins():
    return db.q1("SELECT COUNT(*) n FROM users WHERE role='admin'")["n"]


@router.get("/users")
def users_list():
    return [_user_out(u) for u in db.q("SELECT name,role,created FROM users ORDER BY id")]


@router.post("/users")
def users_create(body: UserIn, user=adm):
    try:
        auth.create_user(body.name, body.password, body.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.audit(user["name"], "user.create", body.name, {"role": body.role})
    return _user_out(db.q1("SELECT name,role,created FROM users WHERE name=?", (body.name.strip(),)))


@router.patch("/users/{name}")
def users_patch(name: str, body: UserPatch, user=adm):
    u = db.q1("SELECT * FROM users WHERE name=?", (name,))
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    if body.role is not None:
        if body.role not in auth.ROLES:
            raise HTTPException(400, "Роль должна быть admin, annotator или researcher")
        if u["role"] == "admin" and body.role != "admin" and _admins() <= 1:
            raise HTTPException(400, "Нельзя понизить последнего администратора")
        db.ex("UPDATE users SET role=? WHERE name=?", (body.role, name))
    if body.password is not None:
        if len(body.password) < 6:
            raise HTTPException(400, "Пароль должен быть не короче 6 символов")
        h, salt = auth.hash_password(body.password)
        db.ex("UPDATE users SET pw_hash=?, salt=?, token=? WHERE name=?", (h, salt, auth.new_token(), name))
    db.audit(user["name"], "user.update", name, {"role": body.role, "password": bool(body.password)})
    return _user_out(db.q1("SELECT name,role,created FROM users WHERE name=?", (name,)))


@router.delete("/users/{name}")
def users_delete(name: str, user=adm):
    u = db.q1("SELECT * FROM users WHERE name=?", (name,))
    if not u:
        raise HTTPException(404, "Пользователь не найден")
    if u["name"] == user["name"]:
        raise HTTPException(400, "Нельзя удалить самого себя")
    if u["role"] == "admin" and _admins() <= 1:
        raise HTTPException(400, "Нельзя удалить последнего администратора")
    db.ex("DELETE FROM users WHERE name=?", (name,))
    db.audit(user["name"], "user.delete", name, "")
    return {"ok": True}


# ---------- корпус ----------

class SourceIn(BaseModel):
    domain: str
    path: str
    split: str = ""


class ScanIn(BaseModel):
    domain: str
    path: str = None


@router.get("/corpus")
def corpus(domain: str = None):
    srcs = db.q("SELECT * FROM corpus_sources %s ORDER BY id" % ("WHERE domain=?" if domain else ""), (domain,) if domain else ())
    summary = db.q("SELECT domain, status, COUNT(*) n FROM publications GROUP BY domain, status")
    inv = None
    p = C.pipeline_mod()
    if p and domain and hasattr(p, "inventory_report"):
        try:
            inv = p.inventory_report(domain)
        except Exception as e:
            inv = {"error": str(e)}
    return {"sources": srcs, "summary": summary, "inventory": inv}


@router.post("/corpus/sources")
def corpus_add(body: SourceIn, user=adm):
    C.cfg_or_404(body.domain)
    path = pathlib.Path(body.path).expanduser()
    if not path.is_dir():
        raise HTTPException(400, "Папка не найдена: %s" % body.path)
    try:
        db.ex("INSERT INTO corpus_sources(domain,path,split,added) VALUES(?,?,?,?)", (body.domain, str(path), body.split, db.now()))
    except Exception:
        raise HTTPException(400, "Этот источник уже добавлен")
    db.audit(user["name"], "corpus.source", str(path), {"domain": body.domain, "split": body.split})
    return db.q1("SELECT * FROM corpus_sources WHERE domain=? AND path=?", (body.domain, str(path)))


@router.post("/corpus/scan")
def corpus_scan(body: ScanIn, user=adm):
    C.cfg_or_404(body.domain)
    p = C.need_pipeline()
    if body.path:
        srcs = [{"path": body.path, "split": (db.q1("SELECT split FROM corpus_sources WHERE domain=? AND path=?", (body.domain, body.path)) or {}).get("split", "")}]
    else:
        srcs = db.q("SELECT path,split FROM corpus_sources WHERE domain=?", (body.domain,))
    if not srcs:
        raise HTTPException(400, "Нет источников: добавьте папку с PDF")
    res = {"added": [], "skipped": [], "duplicates": [], "unreadable": []}
    for s in srcs:
        if not pathlib.Path(s["path"]).is_dir():
            raise HTTPException(400, "Папка не найдена: %s" % s["path"])
        r = p.scan_source(body.domain, s["path"], s.get("split", ""))
        for k in res:
            res[k] += r.get(k, [])
        db.ex("UPDATE corpus_sources SET last_scan=? WHERE domain=? AND path=?", (db.now(), body.domain, s["path"]))
    db.audit(user["name"], "corpus.scan", body.domain, {k: len(v) for k, v in res.items()})
    return res


@router.get("/corpus/inventory")
def corpus_inventory(domain: str = None):
    p = C.need_pipeline()
    return p.inventory_report(domain)


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...), domain: str = Form(...), user=adm):
    C.cfg_or_404(domain)
    p = C.need_pipeline()
    out = []
    for f in files:
        name = f.filename or "paper.pdf"
        data = await f.read()
        if not name.lower().endswith(".pdf") or data[:4] != b"%PDF":
            out.append({"file": name, "error": "Файл не похож на PDF"})
            continue
        try:
            pid = p.register_pdf(domain, data=data, filename=name)
            p.enqueue(pid)
            out.append({"file": name, "id": pid})
        except Exception as e:
            out.append({"file": name, "error": str(e)})
    db.audit(user["name"], "upload", domain, {"files": [o["file"] for o in out]})
    return out


# ---------- конвейер ----------

@router.get("/jobs")
def jobs(domain: str = None):
    rows = db.q("SELECT id,domain,title,filename,status,message,needs_ocr,updated FROM publications %s ORDER BY id" % ("WHERE domain=?" if domain else ""), (domain,) if domain else ())
    st = {}
    for s in db.q("SELECT pub_id,stage,status,attempt,message,started,finished FROM stage_runs"):
        st.setdefault(s["pub_id"], {})[s["stage"]] = s
    order = C.stages()
    return [{**r, "needs_ocr": bool(r["needs_ocr"]), "stages": [st[r["id"]][s] for s in order if s in st.get(r["id"], {})]} for r in rows]


class RunIn(BaseModel):
    pub_ids: list[str] = []
    domain: str = None
    all: bool = False
    from_stage: str = None


@router.post("/jobs/run")
def jobs_run(body: RunIn, user=adm):
    p = C.need_pipeline()
    if body.from_stage and body.from_stage not in C.stages():
        raise HTTPException(400, "Неизвестная стадия: %s (доступны: %s)" % (body.from_stage, ", ".join(C.stages())))
    if body.all:
        if not body.domain:
            raise HTTPException(400, "Для запуска всех статей укажите domain")
        ids = [r["id"] for r in db.q("SELECT id FROM publications WHERE domain=?", (body.domain,))]
    else:
        ids = body.pub_ids
        missing = [i for i in ids if not db.q1("SELECT 1 FROM publications WHERE id=?", (i,))]
        if missing:
            raise HTTPException(404, "Публикации не найдены: %s" % ", ".join(missing))
    if not ids:
        raise HTTPException(400, "Не выбрано ни одной публикации")
    p.enqueue_many(ids, body.from_stage)
    db.audit(user["name"], "jobs.run", body.domain or "", {"n": len(ids), "from_stage": body.from_stage})
    return {"queued": ids}


@router.post("/jobs/{pub_id}/retry")
def jobs_retry(pub_id: str, user=adm):
    C.pub_or_404(pub_id)
    p = C.need_pipeline()
    p.enqueue(pub_id, None)
    db.audit(user["name"], "jobs.retry", pub_id, "")
    return {"queued": [pub_id]}


@router.get("/llm_calls")
def llm_calls(pub: str = None, limit: int = 100):
    sql, args = "SELECT id,pub_id,stage,model,prompt_chars,response_chars,ok,error,ms,ts FROM llm_calls", []
    if pub:
        sql += " WHERE pub_id=?"; args.append(pub)
    return db.q(sql + " ORDER BY id DESC LIMIT ?", args + [max(1, min(limit, 1000))])


@router.get("/llm_calls/{call_id}")
def llm_call(call_id: int):
    r = db.q1("SELECT * FROM llm_calls WHERE id=?", (call_id,))
    if not r:
        raise HTTPException(404, "Вызов модели не найден")
    return r


# ---------- OCR ----------

class OcrIn(BaseModel):
    pub_ids: list[str]


@router.get("/ocr/queue")
def ocr_queue():
    return db.q("SELECT id,title,filename,pages,text_pages,ocr_status,status FROM publications WHERE needs_ocr=1 ORDER BY id")


@router.get("/ocr/status")
def ocr_status():
    p = C.pipeline_mod()
    avail = bool(p and p.ocr_available())
    return {"available": avail, "queue": db.q1("SELECT COUNT(*) n FROM publications WHERE needs_ocr=1 AND status='needs_ocr'")["n"],
            "message": "" if avail else "OCR недоступен: установите tesseract/ocrmypdf"}


@router.post("/ocr/run")
def ocr_run(body: OcrIn, user=adm):
    p = C.need_pipeline()
    if not p.ocr_available():
        raise HTTPException(400, "OCR недоступен: установите tesseract или ocrmypdf")
    for i in body.pub_ids:
        C.pub_or_404(i)

    def work():
        for i in body.pub_ids:
            try:
                p.run_ocr(i)
            except Exception as e:
                db.ex("UPDATE publications SET message=? WHERE id=?", ("OCR: %s" % e, i))
    threading.Thread(target=work, daemon=True).start()
    db.audit(user["name"], "ocr.run", "", body.pub_ids)
    return {"started": body.pub_ids}


# ---------- конфиги ----------

def validate_schema(parsed):
    ents = parsed.get("entities")
    if not isinstance(ents, dict) or not ents:
        raise ValueError("В схеме нет сущностей (entities)")
    for n, e in ents.items():
        if not isinstance(e, dict) or not isinstance(e.get("columns"), list) or not e["columns"]:
            raise ValueError("Сущность «%s»: нужен непустой список columns" % n)
        if e.get("id") and e["id"] not in e["columns"]:
            raise ValueError("Сущность «%s»: id «%s» отсутствует в columns" % (n, e["id"]))
        for f, spec in (e.get("fields") or {}).items():
            if f not in e["columns"]:
                raise ValueError("Сущность «%s»: поле «%s» описано, но его нет в columns" % (n, f))
            if (spec or {}).get("type") == "enum" and not (spec or {}).get("vocab"):
                raise ValueError("Поле «%s.%s»: для enum нужен vocab" % (n, f))
        for f, t in (e.get("refs") or {}).items():
            if t not in ents:
                raise ValueError("Сущность «%s»: ссылка %s указывает на неизвестную сущность «%s»" % (n, f, t))
    if parsed.get("result_entity") and parsed["result_entity"] not in ents:
        raise ValueError("result_entity «%s» не найден среди сущностей" % parsed["result_entity"])
    from ..compare import entity_by_prefix
    for c in (parsed.get("compare") or {}).get("criteria", []):
        if not c.get("name") or "." not in str(c.get("path", "")):
            raise ValueError("Критерий сопоставления: нужны name и path вида сущность.поле")
        if not entity_by_prefix(parsed, c["path"].split(".")[0]):
            raise ValueError("Критерий «%s»: сущность из пути «%s» не найдена" % (c["name"], c["path"]))
        if c.get("mode", "equal") not in ("equal", "ratio_within"):
            raise ValueError("Критерий «%s»: режим должен быть equal или ratio_within" % c["name"])


def validate_rules(parsed):
    rules = parsed.get("rules")
    if not isinstance(rules, list):
        raise ValueError("В правилах нет списка rules")
    seen = set()
    for r in rules:
        if not isinstance(r, dict) or not r.get("id") or not r.get("type"):
            raise ValueError("Каждое правило должно иметь id и type")
        if r["id"] in seen:
            raise ValueError("Повторяющийся id правила: %s" % r["id"])
        seen.add(r["id"])


def _validate(kind, text):
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(400, "Ошибка YAML: %s" % e)
    if not isinstance(parsed, dict):
        raise HTTPException(400, "Конфигурация должна быть YAML-словарём")
    try:
        (validate_schema if kind == "schema" else validate_rules)(parsed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return parsed


def _kind(kind):
    if kind not in ("schema", "rules"):
        raise HTTPException(404, "Тип конфигурации: schema или rules")
    return kind


def _body(domain, kind):
    r = db.q1("SELECT body FROM config_versions WHERE domain=? AND kind=? AND active=1 ORDER BY version DESC", (domain, kind))
    return r["body"] if r else config._file_body(domain, kind)


@router.get("/config/{domain}")
def config_get(domain: str):
    C.cfg_or_404(domain)
    vers = db.q("SELECT kind,version,author,ts,active,comment FROM config_versions WHERE domain=? ORDER BY kind, version DESC", (domain,))
    for v in vers:
        v["active"] = bool(v["active"])
    return {"schema_yaml": _body(domain, "schema"), "rules_yaml": _body(domain, "rules"), "versions": vers}


class ConfigIn(BaseModel):
    yaml: str
    comment: str = ""


@router.put("/config/{domain}/{kind}")
def config_put(domain: str, kind: str, body: ConfigIn, user=adm):
    C.cfg_or_404(domain)
    _kind(kind)
    parsed = _validate(kind, body.yaml)
    if kind == "schema":
        parsed.setdefault("domain", domain)
    try:
        ver = config.save_version(domain, kind, body.yaml, user["name"], body.comment)
    except ValueError as e:
        raise HTTPException(400, str(e))
    pp.write_all()
    return {"domain": domain, "kind": kind, "version": ver}


class ActivateIn(BaseModel):
    version: int


@router.post("/config/{domain}/{kind}/activate")
def config_activate(domain: str, kind: str, body: ActivateIn, user=adm):
    C.cfg_or_404(domain)
    _kind(kind)
    try:
        config.activate(domain, kind, body.version, user["name"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    pp.write_all()
    return {"domain": domain, "kind": kind, "version": body.version}


class DomainIn(BaseModel):
    domain: str
    schema_yaml: str
    rules_yaml: str


@router.post("/config/domains")
def config_new_domain(body: DomainIn, user=adm):
    d = body.domain.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", d):
        raise HTTPException(400, "Имя области: латиница в нижнем регистре, цифры и _, 2-32 символа")
    if d in config.list_domains():
        raise HTTPException(400, "Область «%s» уже существует" % d)
    s = _validate("schema", body.schema_yaml)
    _validate("rules", body.rules_yaml)
    s["domain"] = d
    r = yaml.safe_load(body.rules_yaml)
    r["domain"] = d
    s.setdefault("version", 1)
    r.setdefault("version", 1)
    folder = config.DOMAINS_DIR / d
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "schema.yaml").write_text(yaml.safe_dump(s, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    (folder / "rules.yaml").write_text(yaml.safe_dump(r, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")
    config.seed_versions()
    pp.write_all()
    db.audit(user["name"], "config.domain", d, "")
    return {"domain": d, "version": config.load_domain(d)["version"]}


# ---------- настройки ----------

PRESETS = {"deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
           "lmstudio": {"base_url": "http://127.0.0.1:1234/v1", "model": "local-model"},
           "orcarouter": {"base_url": "https://api.orcarouter.ai/v1", "model": "deepseek/deepseek-v4-flash-free"},
           "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash"},
           "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"}}
DEFAULTS = {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "workers": "3", "require_login": "false"}
SETTING_KEYS = ("provider", "base_url", "model", "workers", "require_login")


def _llm():
    try:
        from .. import llm
        return llm
    except Exception:
        return None


def _read_settings():
    s = dict(DEFAULTS)
    for r in db.q("SELECT key,value FROM settings"):
        s[r["key"]] = r["value"]
    return s


@router.get("/settings")
def settings_get():
    m = _llm()
    if m and hasattr(m, "get_settings"):
        s = dict(m.get_settings(public=True))
    else:
        s = _read_settings()
        s["key_set"] = bool(s.pop("api_key", ""))
    s.setdefault("require_login", _read_settings().get("require_login", "false"))
    s.pop("api_key", None)
    s["presets"] = PRESETS
    return s


@router.post("/settings")
def settings_set(patch: dict, user=adm):
    if "workers" in patch:
        try:
            if not 1 <= int(patch["workers"]) <= 16:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(400, "workers: целое число от 1 до 16")
    if "require_login" in patch:
        v = str(patch["require_login"]).lower()
        if v not in ("true", "false", "1", "0"):
            raise HTTPException(400, "require_login: true или false")
        db.ex("INSERT OR REPLACE INTO settings(key,value) VALUES('require_login',?)", ("true" if v in ("true", "1") else "false",))
    rest = {k: v for k, v in patch.items() if k != "require_login"}
    m = _llm()
    if m and hasattr(m, "save_settings"):
        m.save_settings(rest)
    else:
        for k in SETTING_KEYS + ("api_key",):
            if k in rest and k != "require_login" and rest[k] not in (None, ""):
                db.ex("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, str(rest[k]).strip()))
        if rest.get("clear_key"):
            db.ex("DELETE FROM settings WHERE key='api_key'")
    db.audit(user["name"], "settings.update", "", {k: ("***" if k == "api_key" else v) for k, v in patch.items()})
    return settings_get()


@router.post("/settings/test")
def settings_test():
    m = _llm()
    if not m or not hasattr(m, "test_connection"):
        raise HTTPException(503, "Модуль LLM недоступен")
    try:
        r = m.test_connection()
        return r if isinstance(r, dict) else {"ok": True, "reply": r}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/audit")
def audit_list(limit: int = 200):
    return db.q("SELECT id,user,action,target,detail,ts FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),))
