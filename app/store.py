"""Файловое хранилище: data/pubs/<ID>/{meta.json, tables.json, paper.pdf?}; настройки в data/settings.json."""
import json, os, pathlib, shutil, threading, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PUBS = DATA / "pubs"
SETTINGS = DATA / "settings.json"
LOCK = threading.RLock()
TABLES = ["publications", "materials", "devices", "layers", "measurements", "results", "issues"]
DEFAULTS = {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": "", "workers": 3}
PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
    "lmstudio": {"base_url": "http://127.0.0.1:1234/v1", "model": "huihui-qwen3.5-9b-claude-4.6-opus-abliterated"},
}


def init():
    PUBS.mkdir(parents=True, exist_ok=True)


def load_settings(public=False):
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
    except Exception:
        pass
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env and not s.get("api_key"):
        s["api_key"] = env
    if public:
        s["key_set"] = bool(s.get("api_key"))
        s.pop("api_key", None)
    return s


def save_settings(patch):
    with LOCK:
        s = load_settings()
        for k in ("provider", "base_url", "model", "workers"):
            if k in patch and patch[k] not in (None, ""):
                s[k] = patch[k]
        if patch.get("api_key"):
            s["api_key"] = patch["api_key"].strip()
        if patch.get("clear_key"):
            s["api_key"] = ""
        DATA.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.chmod(SETTINGS, 0o600)
        except OSError:
            pass


def safe_id(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", pathlib.Path(name).stem).strip("_")
    return s[:40] or "paper"


def unique_id(base):
    pid, n = base, 2
    while (PUBS / pid).exists():
        pid = f"{base}_{n}"
        n += 1
    return pid


def pub_dir(pid):
    return PUBS / pid


def read_meta(pid):
    try:
        return json.loads((PUBS / pid / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def write_meta(pid, meta):
    with LOCK:
        d = PUBS / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def update_meta(pid, **kw):
    with LOCK:
        m = read_meta(pid) or {}
        m.update(kw)
        write_meta(pid, m)
        return m


def write_tables(pid, tables):
    with LOCK:
        (PUBS / pid / "tables.json").write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")


def read_tables(pid):
    try:
        return json.loads((PUBS / pid / "tables.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def list_ids():
    return sorted(p.name for p in PUBS.iterdir() if p.is_dir()) if PUBS.exists() else []


def all_data():
    out = {t: [] for t in TABLES}
    for pid in list_ids():
        tb = read_tables(pid)
        if not tb:
            continue
        for t in TABLES:
            for r in tb.get(t, []):
                out[t].append({k: v for k, v in r.items() if v not in ("", None)})
    return out


def delete_pub(pid):
    with LOCK:
        shutil.rmtree(PUBS / pid, ignore_errors=True)
