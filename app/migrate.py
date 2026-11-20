"""Импорт прежних data/pubs/*/{meta.json,tables.json} в БД. Запуск: python -m app.migrate"""
import json, pathlib
from . import db, config, fts

PUBS_DIR = db.ROOT / "data" / "pubs"


def _int(v):
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return None


def migrate(pubs_dir=None, domain="solar"):
    d = pathlib.Path(pubs_dir or PUBS_DIR)
    if not d.is_dir():
        return {"pubs": 0, "records": 0, "issues": 0}
    cfg = config.load_domain(domain)
    schema = cfg["schema"]
    result_entity = schema.get("result_entity")
    pub_ent = schema.get("publication_entity", "publications")
    n_pubs = n_rec = n_iss = 0
    for p in sorted(x for x in d.iterdir() if x.is_dir()):
        try:
            tables = json.loads((p / "tables.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = {}
        try:
            meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        pid = p.name
        if db.q1("SELECT 1 FROM publications WHERE id=?", (pid,)):
            continue
        pub = (tables.get(pub_ent) or [{}])[0]
        in_scope = str(pub.get("in_scope", "true")).lower() != "false"
        n_issues_src = tables.get("issues") or []
        status = "excluded" if not in_scope else ("flagged" if n_issues_src else "verified")
        pdf = meta.get("pdf_path") or ""
        with db.tx() as c:
            c.execute("INSERT INTO publications(id,domain,filename,pdf_path,doi,title,year,authors,in_scope,exclusion_reason,status,message,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (pid, domain, meta.get("filename", pid + ".pdf"), pdf, pub.get("DOI", ""), pub.get("title", ""), pub.get("year", ""), pub.get("authors", ""),
                       1 if in_scope else 0, pub.get("exclusion_reason", ""), status, "Импортировано из пилотного извлечения", db.now(), db.now()))
            for ent, rows in tables.items():
                if ent in (pub_ent, "issues") or ent not in schema["entities"]:
                    continue
                idcol = schema["entities"][ent].get("id")
                for i, row in enumerate(rows, 1):
                    data = {k: v for k, v in row.items() if v not in ("", None)}
                    rid = str(row.get(idcol) or "%s%03d" % (ent[:1].upper(), i))
                    sp, sf = (_int(row.get("source_page")), row.get("source_fragment") or None) if ent == result_entity else (None, None)
                    c.execute("INSERT OR IGNORE INTO records(pub_id,domain,entity,rid,data,status,source_page,source_fragment,quote_verified,version,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (pid, domain, ent, rid, db.jd(data), "extracted", sp, sf, None, 1, db.now(), db.now()))
                    n_rec += 1
            for i, row in enumerate(n_issues_src, 1):
                ids = [x.strip() for x in str(row.get("record_ids", "")).replace(",", ";").split(";") if x.strip()]
                c.execute("INSERT INTO issues(pub_id,domain,rule_id,severity,record_ids,description,source_page,source_fragment,origin,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (pid, domain, row.get("check_name", ""), "warning", db.jd(ids), row.get("description", ""), _int(row.get("source_page")), row.get("source_fragment", ""), "model", "open", db.now(), db.now()))
                n_iss += 1
        n_pubs += 1
    fts.reindex()
    return {"pubs": n_pubs, "records": n_rec, "issues": n_iss}


def migrate_if_empty():
    if db.q1("SELECT 1 FROM records LIMIT 1"):
        return None
    return migrate()


if __name__ == "__main__":
    db.init_db()
    config.seed_versions()
    print(migrate())
