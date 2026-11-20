"""Конфигурации предметных областей: config/domains/<domain>/{schema,rules}.yaml + версии в БД (активная версия важнее файла)."""
import yaml, pathlib, hashlib
from . import db

DOMAINS_DIR = db.ROOT / "config" / "domains"
FILE = {"schema": "schema.yaml", "rules": "rules.yaml"}


def list_domains():
    return sorted(p.name for p in DOMAINS_DIR.iterdir() if (p / "schema.yaml").exists()) if DOMAINS_DIR.exists() else []


def _file_body(domain, kind):
    return (DOMAINS_DIR / domain / FILE[kind]).read_text(encoding="utf-8")


def seed_versions():
    """Первичная запись версий из файлов (версия из поля version)."""
    for d in list_domains():
        for kind in ("schema", "rules"):
            body = _file_body(d, kind)
            v = int(yaml.safe_load(body).get("version", 1))
            if not db.q1("SELECT 1 FROM config_versions WHERE domain=? AND kind=? AND version=?", (d, kind, v)):
                has_active = db.q1("SELECT 1 FROM config_versions WHERE domain=? AND kind=? AND active=1", (d, kind))
                db.ex("INSERT INTO config_versions(domain,kind,version,body,author,ts,active,comment) VALUES(?,?,?,?,?,?,?,?)",
                      (d, kind, v, body, "system", db.now(), 0 if has_active else 1, "из файла"))


def get(domain, kind):
    try:
        row = db.q1("SELECT body, version FROM config_versions WHERE domain=? AND kind=? AND active=1 ORDER BY version DESC", (domain, kind))
    except Exception:  # БД ещё не создана (импорт модулей в тестах): берём файл
        row = None
    if row:
        return yaml.safe_load(row["body"])
    return yaml.safe_load(_file_body(domain, kind))


def load_domain(domain):
    """{'domain','schema','rules','version': 'schema vN / rules vM'}"""
    s, r = get(domain, "schema"), get(domain, "rules")
    return {"domain": domain, "schema": s, "rules": r, "version": f"schema v{s.get('version', 1)} / rules v{r.get('version', 1)}"}


def save_version(domain, kind, body_yaml, author, comment=""):
    """Валидирует YAML, создаёт новую версию (v+1) и делает её активной. Возвращает номер версии."""
    parsed = yaml.safe_load(body_yaml)
    if not isinstance(parsed, dict):
        raise ValueError("Конфигурация должна быть YAML-словарём")
    if kind == "schema" and "entities" not in parsed:
        raise ValueError("В схеме нет раздела entities")
    if kind == "rules" and "rules" not in parsed:
        raise ValueError("В правилах нет раздела rules")
    last = db.q1("SELECT MAX(version) v FROM config_versions WHERE domain=? AND kind=?", (domain, kind))["v"] or 0
    ver = last + 1
    parsed["version"] = ver
    body = yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False, width=140)
    with db.tx() as c:
        c.execute("UPDATE config_versions SET active=0 WHERE domain=? AND kind=?", (domain, kind))
        c.execute("INSERT INTO config_versions(domain,kind,version,body,author,ts,active,comment) VALUES(?,?,?,?,?,?,1,?)", (domain, kind, ver, body, author, db.now(), comment))
    db.audit(author, "config.save", f"{domain}/{kind}", {"version": ver, "comment": comment})
    return ver


def activate(domain, kind, version, author):
    if not db.q1("SELECT 1 FROM config_versions WHERE domain=? AND kind=? AND version=?", (domain, kind, version)):
        raise ValueError("Нет такой версии")
    with db.tx() as c:
        c.execute("UPDATE config_versions SET active=0 WHERE domain=? AND kind=?", (domain, kind))
        c.execute("UPDATE config_versions SET active=1 WHERE domain=? AND kind=? AND version=?", (domain, kind, version))
    db.audit(author, "config.activate", f"{domain}/{kind}", {"version": version})


def columns(domain_cfg, entity):
    return domain_cfg["schema"]["entities"][entity]["columns"]
