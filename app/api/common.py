"""Общие хелперы API: конфиги, сериализация записей, ленивый доступ к чужим модулям."""
import re
from fastapi import HTTPException
from .. import db, config, evaluation

STAGES_DEFAULT = ["register", "parse", "segment", "extract", "verify", "normalize", "validate", "publish"]
FINAL = ("verified", "flagged", "excluded")


def cfg_or_404(domain):
    if not domain or domain not in config.list_domains():
        raise HTTPException(404, "Предметная область не найдена: %s" % domain)
    return config.load_domain(domain)


def default_domain():
    d = config.list_domains()
    return d[0] if d else None


def pipeline_mod():
    try:
        from .. import pipeline
        return pipeline
    except Exception:
        return None


def need_pipeline():
    p = pipeline_mod()
    if p is None:
        raise HTTPException(503, "Модуль конвейера недоступен")
    return p


def stages():
    p = pipeline_mod()
    return list(getattr(p, "STAGES", STAGES_DEFAULT)) if p else STAGES_DEFAULT


def pub_or_404(pub_id):
    p = db.q1("SELECT * FROM publications WHERE id=?", (pub_id,))
    if not p:
        raise HTTPException(404, "Публикация не найдена: %s" % pub_id)
    return p


def issue_index(pub_ids):
    """{(pub, rid): [issue_id,...]} по открытым замечаниям."""
    idx = {}
    if not pub_ids:
        return idx
    for i in db.q("SELECT id,pub_id,record_ids FROM issues WHERE pub_id IN (%s)" % ",".join("?" * len(pub_ids)), list(pub_ids)):
        for rid in db.jl(i["record_ids"], []) or []:
            idx.setdefault((i["pub_id"], str(rid)), []).append(i["id"])
    return idx


def rec_out(row, idx=None):
    return {"id": row["id"], "pub_id": row["pub_id"], "domain": row["domain"], "entity": row["entity"], "rid": row["rid"], "data": db.jl(row["data"], {}),
            "status": row["status"], "source_page": row["source_page"], "source_fragment": row["source_fragment"],
            "quote_verified": None if row["quote_verified"] is None else bool(row["quote_verified"]),
            "version": row["version"], "updated": row["updated"],
            "issue_ids": (idx or {}).get((row["pub_id"], row["rid"]), [])}


def rec_or_404(rec_id):
    r = db.q1("SELECT * FROM records WHERE id=?", (rec_id,))
    if not r:
        raise HTTPException(404, "Запись не найдена: %s" % rec_id)
    return r


def issue_out(i):
    return {"id": i["id"], "pub_id": i["pub_id"], "domain": i["domain"], "rule_id": i["rule_id"], "severity": i["severity"], "origin": i["origin"], "status": i["status"],
            "comment": i["comment"] or "", "description": i["description"], "record_ids": db.jl(i["record_ids"], []), "source_page": i["source_page"],
            "source_fragment": i["source_fragment"], "created": i["created"]}


def refresh_pub_status(pub_id):
    """Пересчёт статуса публикации после ручных действий (если она уже прошла конвейер)."""
    p = db.q1("SELECT status,in_scope FROM publications WHERE id=?", (pub_id,))
    if not p or p["status"] not in ("verified", "flagged") or p["in_scope"] == 0:
        return
    open_n = db.q1("SELECT COUNT(*) n FROM issues WHERE pub_id=? AND status='open' AND severity IN ('error','warning')", (pub_id,))["n"]
    rej = db.q1("SELECT COUNT(*) n FROM records WHERE pub_id=? AND status='rejected'", (pub_id,))["n"]
    st = "flagged" if (open_n or rej) else "verified"
    if st != p["status"]:
        db.ex("UPDATE publications SET status=?, updated=? WHERE id=?", (st, db.now(), pub_id))


def after_change(pub_id):
    """Перепроверка публикации и обновление индекса. Возвращает список предупреждений."""
    from .. import fts
    warns = []
    p = pipeline_mod()
    if p is None:
        warns.append("Модуль конвейера недоступен: правка сохранена, проверка правил не запускалась")
    else:
        try:
            p.revalidate(pub_id)
        except Exception as e:
            warns.append("Правка сохранена, но перепроверка правил не удалась: %s" % e)
    try:
        refresh_pub_status(pub_id)
        fts.reindex(pub_id)
    except Exception as e:
        warns.append("Не удалось обновить поисковый индекс: %s" % e)
    return warns


# ---------- плоские таблицы (для экспорта и /data) ----------

PUB_KEYMAP = {"publication_id": "id", "doi": "doi"}


def pub_row(cols, p):
    out = {}
    for c in cols:
        k = PUB_KEYMAP.get(c.lower(), c.lower())
        v = p.get(k, "")
        if c == "publication_id":
            v = evaluation.pub_stem(p)
        if c == "in_scope":
            v = "" if p.get("in_scope") is None else ("true" if p["in_scope"] else "false")
        out[c] = "" if v is None else str(v)
    return out


def issue_row(cols, i, n, stem=None):
    m = {"publication_id": stem or i["pub_id"], "issue_id": "I%d" % n, "record_ids": ";".join(db.jl(i["record_ids"], [])), "check_name": i["rule_id"] or "",
         "description": i["description"] or "", "source_page": i["source_page"] if i["source_page"] is not None else "", "source_fragment": i["source_fragment"] or ""}
    return {c: ("" if m.get(c) is None else str(m.get(c, ""))) for c in cols}


def tables_flat(domain, statuses=None, pubs=None, include_excluded=False):
    """{entity: [плоская строка по columns схемы]}. statuses=None — все кроме rejected."""
    cfg = config.load_domain(domain)
    schema = cfg["schema"]
    pubs_sql = "SELECT * FROM publications WHERE domain=?"
    args = [domain]
    if pubs:
        pubs_sql += " AND id IN (%s)" % ",".join("?" * len(pubs))
        args += list(pubs)
    all_pubs = db.q(pubs_sql + " ORDER BY id", args)
    ids = [p["id"] for p in all_pubs]
    stems = {p["id"]: evaluation.pub_stem(p) for p in all_pubs}
    out = {}
    have = set()
    rec_sql = "SELECT pub_id,entity,rid,data FROM records WHERE domain=? AND status!='rejected'"
    rargs = [domain]
    if statuses:
        rec_sql += " AND status IN (%s)" % ",".join("?" * len(statuses))
        rargs += list(statuses)
    rows_by_ent = {}
    for r in db.q(rec_sql + " ORDER BY pub_id, id", rargs):
        if r["pub_id"] in ids:
            rows_by_ent.setdefault(r["entity"], []).append(r)
            have.add(r["pub_id"])
    pub_ent = schema.get("publication_entity", "publications")
    for ent, e in schema["entities"].items():
        cols = e["columns"]
        if ent == pub_ent:
            out[ent] = [pub_row(cols, p) for p in all_pubs if p["id"] in have or (include_excluded and p["status"] == "excluded")]
        elif ent == "issues":
            lst = [i for i in db.q("SELECT * FROM issues WHERE domain=? AND status!='fixed' ORDER BY pub_id,id", (domain,)) if i["pub_id"] in have]
            out[ent] = [issue_row(cols, i, n, stems.get(i["pub_id"])) for n, i in enumerate(lst, 1)]
        else:
            rows = []
            for r in rows_by_ent.get(ent, []):
                d = db.jl(r["data"], {})
                d["publication_id"] = stems.get(r["pub_id"], r["pub_id"])
                rows.append({c: ("" if d.get(c) is None else str(d.get(c, ""))) for c in cols})
            out[ent] = rows
    return out


# ---------- валидация значений ----------

def clean_value(entity_cfg, field, value):
    """Приводит значение поля к строке по типу из схемы; ошибки — понятные HTTPException 400."""
    f = (entity_cfg.get("fields") or {}).get(field) or {}
    t = f.get("type", "string")
    if value is None:
        return ""
    if isinstance(value, bool):
        value = "true" if value else "false"
    s = str(value).strip()
    if s == "":
        return ""
    if t == "enum":
        if s not in f.get("vocab", []):
            raise HTTPException(400, "Поле «%s»: значение «%s» не из словаря (%s)" % (field, s, ", ".join(map(str, f.get("vocab", [])))))
    elif t in ("number", "int"):
        try:
            x = float(s.replace(",", "."))
        except ValueError:
            raise HTTPException(400, "Поле «%s» должно быть числом, получено «%s»" % (field, s))
        if t == "int":
            if x != int(x):
                raise HTTPException(400, "Поле «%s» должно быть целым числом" % field)
            return str(int(x))
        return s.replace(",", ".")
    elif t == "bool":
        if s.lower() not in ("true", "false", "1", "0", "да", "нет"):
            raise HTTPException(400, "Поле «%s» должно быть true/false" % field)
        return "true" if s.lower() in ("true", "1", "да") else "false"
    return s


def clean_data(entity_cfg, data):
    cols = entity_cfg["columns"]
    out = {}
    for k, v in (data or {}).items():
        if k not in cols:
            raise HTTPException(400, "Неизвестное поле «%s» для этой сущности" % k)
        out[k] = clean_value(entity_cfg, k, v)
    return out
