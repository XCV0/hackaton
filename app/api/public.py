"""Публичные эндпоинты (GET, роль researcher и выше): области, статистика, публикации, записи, поиск, сравнение, замечания, экспорт, паспорт, PDF."""
import io, csv, json, zipfile, pathlib
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
from pydantic import BaseModel
from .. import db, config, auth, fts, compare as cmp, passport as pp, evaluation
from . import common as C

router = APIRouter(prefix="/api", dependencies=[Depends(auth.current_user)])


@router.get("/domains")
def domains():
    out = []
    for d in config.list_domains():
        c = config.load_domain(d)
        out.append({"domain": d, "title": c["schema"].get("title", d), "version": c["version"]})
    return out


@router.get("/stats")
def stats(domain: str = None):
    domain = domain or C.default_domain()
    w, a = ("WHERE domain=?", (domain,)) if domain else ("", ())
    by = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM publications %s GROUP BY status" % w, a)}
    pubs = sum(by.values())
    done = sum(by.get(s, 0) for s in C.FINAL)
    in_scope = pubs - by.get("excluded", 0)
    and_ = "AND" if w else "WHERE"
    recs = db.q1("SELECT SUM(status!='rejected') ok, SUM(status='rejected') rej FROM records %s" % w, a)
    rules = {}
    for r in db.q("SELECT rule_id, SUM(status='open') o, COUNT(*) n FROM issues %s GROUP BY rule_id" % w, a):
        rules[r["rule_id"] or ""] = {"open": r["o"] or 0, "total": r["n"]}
    qv = db.q1("SELECT SUM(quote_verified=1) ok, COUNT(*) n FROM records %s %s quote_verified IS NOT NULL" % (w, and_), a)
    ocr = db.q1("SELECT COUNT(*) n FROM publications %s %s needs_ocr=1 AND status='needs_ocr'" % (w, and_), a)["n"]
    last = None
    if domain:
        runs = evaluation.list_runs(domain)
        last = runs[0] if runs else None
    return {"pubs": pubs, "by_status": by, "coverage": round(done / pubs, 4) if pubs else None,
            "verified_share": round(by.get("verified", 0) / in_scope, 4) if in_scope else None,
            "records": recs["ok"] or 0, "rejected": recs["rej"] or 0, "issues_by_rule": rules,
            "quote_valid_share": round(qv["ok"] / qv["n"], 4) if qv["n"] else None, "ocr_queue": ocr, "last_eval": last}


def _pub_out(p, n_rec, n_iss):
    return {"id": p["id"], "domain": p["domain"], "title": p["title"] or "", "year": p["year"] or "", "doi": p["doi"] or "", "status": p["status"],
            "in_scope": None if p["in_scope"] is None else bool(p["in_scope"]), "pages": p["pages"], "needs_ocr": bool(p["needs_ocr"]),
            "n_records": n_rec, "n_issues_open": n_iss, "message": p["message"] or "", "split": p["split"] or "", "filename": p["filename"]}


@router.get("/publications")
def publications(domain: str = None, status: str = None, q: str = None, split: str = None):
    sql, args = "SELECT * FROM publications WHERE 1=1", []
    for col, v in (("domain", domain), ("status", status), ("split", split)):
        if v:
            sql += " AND %s=?" % col
            args.append(v)
    if q:
        sql += " AND (id LIKE ? OR title LIKE ? OR doi LIKE ? OR authors LIKE ?)"
        args += ["%" + q + "%"] * 4
    rows = db.q(sql + " ORDER BY id", args)
    nrec = {r["pub_id"]: r["n"] for r in db.q("SELECT pub_id, COUNT(*) n FROM records WHERE status!='rejected' GROUP BY pub_id")}
    niss = {r["pub_id"]: r["n"] for r in db.q("SELECT pub_id, COUNT(*) n FROM issues WHERE status='open' GROUP BY pub_id")}
    return [_pub_out(p, nrec.get(p["id"], 0), niss.get(p["id"], 0)) for p in rows]


@router.get("/publications/{pub_id}")
def publication(pub_id: str, include_rejected: int = 0):
    p = C.pub_or_404(pub_id)
    sql = "SELECT * FROM records WHERE pub_id=?" + ("" if include_rejected else " AND status!='rejected'") + " ORDER BY id"
    rows = db.q(sql, (pub_id,))
    idx = C.issue_index([pub_id])
    recs = {}
    for r in rows:
        recs.setdefault(r["entity"], []).append(C.rec_out(r, idx))
    issues = [C.issue_out(i) for i in db.q("SELECT * FROM issues WHERE pub_id=? ORDER BY id", (pub_id,))]
    order = C.stages()
    st = {s["stage"]: s for s in db.q("SELECT stage,status,attempt,message,started,finished FROM stage_runs WHERE pub_id=?", (pub_id,))}
    stages = [st[s] for s in order if s in st]
    pub = {k: v for k, v in p.items()}
    pub["in_scope"] = None if p["in_scope"] is None else bool(p["in_scope"])
    pub["needs_ocr"] = bool(p["needs_ocr"])
    pub["has_pdf"] = bool(p["pdf_path"] and pathlib.Path(p["pdf_path"]).exists())
    pub.pop("pdf_path", None)
    return {"publication": pub, "records": recs, "issues": issues, "stages": stages}


def _metric_field(schema):
    for c in (schema.get("compare") or {}).get("criteria", []):
        if c.get("name") == "metric" and "." in c.get("path", ""):
            return c["path"].split(".", 1)[1]
    return "metric_name"


@router.get("/records")
def records(domain: str = None, entity: str = None, pub: str = None, q: str = None, status: str = None, metric: str = None, origin: str = None,
            dups: int = 0, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    cfg = C.cfg_or_404(domain) if domain else None
    if entity is None and cfg:
        entity = cfg["schema"].get("result_entity")
    where, args = [], []
    if domain:
        where.append("domain=?"); args.append(domain)
    if entity and entity != "all":
        where.append("entity=?"); args.append(entity)
    if pub:
        where.append("pub_id=?"); args.append(pub)
    if status:
        where.append("status=?"); args.append(status)
    else:
        where.append("status!='rejected'")
    if metric:
        where.append("json_extract(data, '$.%s')=?" % (_metric_field(cfg["schema"]) if cfg else "metric_name")); args.append(metric)
    if origin:
        where.append("json_extract(data, '$.result_origin')=?"); args.append(origin)
    if not dups:
        where.append("COALESCE(json_extract(data, '$.duplicate_of'),'')=''")
    if q:
        mq = fts.match_query(q)
        if mq:
            where.append("EXISTS (SELECT 1 FROM records_fts f WHERE f.pub_id=records.pub_id AND f.entity=records.entity AND f.rid=records.rid AND records_fts MATCH ?)")
            args.append(mq)
    w = " AND ".join(where) or "1=1"
    total = db.q1("SELECT COUNT(*) n FROM records WHERE " + w, args)["n"]
    rows = db.q("SELECT * FROM records WHERE %s ORDER BY pub_id, id LIMIT ? OFFSET ?" % w, args + [limit, offset])
    idx = C.issue_index(sorted({r["pub_id"] for r in rows}))
    return {"total": total, "items": [C.rec_out(r, idx) for r in rows]}


def _context(domain, row):
    schema = config.load_domain(domain)["schema"]
    res = cmp.Resolver(schema)
    ctx, todo = {}, [(row["entity"], db.jl(row["data"], {}))]
    seen = 0
    while todo and seen < 40:
        ent, data = todo.pop()
        seen += 1
        for fld, target in ((schema["entities"].get(ent) or {}).get("refs") or {}).items():
            for rid in str(data.get(fld, "") or "").split(";"):
                rid = rid.strip()
                if rid and rid not in ctx.get(target, {}):
                    d = res.get(row["pub_id"], target, rid)
                    if d is not None:
                        ctx.setdefault(target, {})[rid] = d
                        todo.append((target, d))
    return ctx


@router.get("/records/{rec_id}")
def record(rec_id: int):
    r = C.rec_or_404(rec_id)
    idx = C.issue_index([r["pub_id"]])
    rec = C.rec_out(r, idx)
    hist = db.q("SELECT id,user,action,before,after,comment,ts FROM record_history WHERE record_id=? ORDER BY id DESC", (rec_id,))
    for h in hist:
        h["before"], h["after"] = db.jl(h["before"], {}), db.jl(h["after"], {})
    issues = [C.issue_out(i) for i in db.q("SELECT * FROM issues WHERE id IN (%s)" % ",".join("?" * len(rec["issue_ids"])), rec["issue_ids"])] if rec["issue_ids"] else []
    return {"record": rec, "context": _context(r["domain"], r), "history": hist, "issues": issues}


@router.get("/records/{rec_id}/history")
def record_history(rec_id: int):
    C.rec_or_404(rec_id)
    hist = db.q("SELECT id,user,action,before,after,comment,ts FROM record_history WHERE record_id=? ORDER BY id DESC", (rec_id,))
    for h in hist:
        h["before"], h["after"] = db.jl(h["before"], {}), db.jl(h["after"], {})
    return hist


@router.get("/search")
def search(q: str = "", domain: str = None, limit: int = Query(50, ge=1, le=200)):
    return {"items": fts.search(q, domain, limit)}


class CompareIn(BaseModel):
    domain: str
    record_ids: list[int]


def _recs_by_ids(domain, ids):
    rows = {r["id"]: r for r in db.q("SELECT * FROM records WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)} if ids else {}
    out = []
    for i in ids:
        r = rows.get(i)
        if not r:
            raise HTTPException(404, "Запись не найдена: %s" % i)
        if r["domain"] != domain:
            raise HTTPException(400, "Запись %s относится к другой области (%s)" % (i, r["domain"]))
        out.append(C.rec_out(r))
    return out


@router.post("/compare")
def compare(body: CompareIn):
    cfg = C.cfg_or_404(body.domain)
    ids = list(dict.fromkeys(body.record_ids))
    if len(ids) < 2:
        raise HTTPException(400, "Для сопоставления выберите не меньше двух записей")
    if len(ids) > 60:
        raise HTTPException(400, "Слишком много записей (максимум 60)")
    recs = _recs_by_ids(body.domain, ids)
    ent = (cfg["schema"].get("compare") or {}).get("entity")
    for r in recs:
        if ent and r["entity"] != ent:
            raise HTTPException(400, "Сопоставляются только записи сущности «%s» (запись %s: «%s»)" % (ent, r["id"], r["entity"]))
        if r["status"] == "rejected":
            raise HTTPException(400, "Запись %s отклонена и не может сопоставляться" % r["id"])
    return cmp.compare(cfg, recs)


@router.get("/compare/candidates")
def compare_candidates(record_id: int, limit: int = Query(200, ge=1, le=1000)):
    r = C.rec_or_404(record_id)
    cfg = C.cfg_or_404(r["domain"])
    ent = (cfg["schema"].get("compare") or {}).get("entity")
    if ent and r["entity"] != ent:
        raise HTTPException(400, "Сопоставляются только записи сущности «%s»" % ent)
    pool = [C.rec_out(x) for x in db.q("SELECT * FROM records WHERE domain=? AND entity=? AND status!='rejected' AND id!=? AND COALESCE(json_extract(data,'$.duplicate_of'),'')='' ORDER BY id",
                                         (r["domain"], r["entity"], record_id))]
    return cmp.candidates(cfg, C.rec_out(r), pool)[:limit]


@router.get("/issues")
def issues(domain: str = None, pub: str = None, status: str = None, rule: str = None, severity: str = None):
    sql, args = "SELECT * FROM issues WHERE 1=1", []
    for col, v in (("domain", domain), ("pub_id", pub), ("status", status), ("rule_id", rule), ("severity", severity)):
        if v:
            sql += " AND %s=?" % col
            args.append(v)
    return [C.issue_out(i) for i in db.q(sql + " ORDER BY id", args)]


@router.get("/export")
def export(domain: str, format: str = "csv", status: str = "verified", pubs: str = None):
    cfg = C.cfg_or_404(domain)
    if format not in ("csv", "json"):
        raise HTTPException(400, "format: csv или json")
    if status not in ("verified", "all"):
        raise HTTPException(400, "status: verified или all")
    pl = [p.strip() for p in pubs.split(",") if p.strip()] if pubs else None
    statuses = ["verified"] if status == "verified" else None
    schema = cfg["schema"]
    if format == "csv":
        tables = C.tables_flat(domain, statuses, pl, include_excluded=(status == "all"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for ent, e in schema["entities"].items():
                s = io.StringIO()
                w = csv.DictWriter(s, fieldnames=e["columns"], extrasaction="ignore")
                w.writeheader()
                w.writerows(tables.get(ent, []))
                z.writestr(ent + ".csv", "﻿" + s.getvalue())
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="export_%s.zip"' % domain})
    where, args = "domain=? AND status!='rejected'", [domain]
    if statuses:
        where += " AND status='verified'"
    if pl:
        where += " AND pub_id IN (%s)" % ",".join("?" * len(pl))
        args += pl
    tables = {}
    stem_of = {p["id"]: evaluation.pub_stem(p) for p in db.q("SELECT id,filename FROM publications WHERE domain=?", (domain,))}
    for r in db.q("SELECT * FROM records WHERE %s ORDER BY pub_id,id" % where, args):
        tables.setdefault(r["entity"], []).append({"pub_id": r["pub_id"], "publication_id": stem_of.get(r["pub_id"], r["pub_id"]), "rid": r["rid"], "status": r["status"], "quote_verified": None if r["quote_verified"] is None else bool(r["quote_verified"]),
                                                   "source": {"page": r["source_page"], "fragment": r["source_fragment"]}, "data": db.jl(r["data"], {})})
    pub_ids = sorted({x["pub_id"] for v in tables.values() for x in v})
    plist = [{**p, "publication_id": evaluation.pub_stem(p)} for p in db.q("SELECT id,filename,title,year,doi,authors,status FROM publications WHERE domain=? ORDER BY id", (domain,)) if p["id"] in pub_ids]
    body = {"domain": domain, "version": cfg["version"], "status_filter": status, "publications": plist, "tables": tables}
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="export_%s.json"' % domain})


@router.get("/passport/{domain}.md")
def passport_md(domain: str):
    C.cfg_or_404(domain)
    return Response(pp.markdown(domain), media_type="text/markdown; charset=utf-8")


@router.get("/passport/{domain}")
def passport_json(domain: str):
    C.cfg_or_404(domain)
    return pp.passport(domain)


@router.get("/pdf/{pub_id}")
def pdf(pub_id: str):
    p = C.pub_or_404(pub_id)
    path = pathlib.Path(p["pdf_path"]) if p["pdf_path"] else None
    if not path or not path.exists():
        raise HTTPException(404, "PDF-файл не найден")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@router.get("/pdf/{pub_id}/page/{n}/text")
def pdf_page_text(pub_id: str, n: int):
    C.pub_or_404(pub_id)
    r = db.q1("SELECT page,text,ocr FROM pages WHERE pub_id=? AND page=?", (pub_id, n))
    if not r:
        raise HTTPException(404, "Страница %s не найдена" % n)
    return {"pub_id": pub_id, "page": n, "text": r["text"] or "", "ocr": bool(r["ocr"])}


@router.get("/data")
def data(domain: str = None):
    domain = domain or C.default_domain()
    C.cfg_or_404(domain)
    return {"tables": C.tables_flat(domain, None, None, include_excluded=True), "passport": pp.passport(domain)}
