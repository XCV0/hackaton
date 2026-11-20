"""Правки (annotator+): записи, модерация замечаний, gold и оценка качества."""
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from .. import db, auth, config, evaluation, fts
from . import common as C

router = APIRouter(prefix="/api")
ann = Depends(auth.annotator)


def _entity_cfg(domain, entity):
    cfg = C.cfg_or_404(domain)
    ents = cfg["schema"]["entities"]
    if entity not in ents:
        raise HTTPException(400, "Сущности «%s» нет в схеме области" % entity)
    return cfg, ents[entity]


class PatchIn(BaseModel):
    data: dict
    comment: str = ""


def _history(rec_id, user, action, before, after, comment):
    db.ex("INSERT INTO record_history(record_id,user,action,before,after,comment,ts) VALUES(?,?,?,?,?,?,?)",
          (rec_id, user, action, db.jd(before), db.jd(after), comment or "", db.now()))


@router.patch("/records/{rec_id}")
def patch_record(rec_id: int, body: PatchIn, user=ann):
    r = C.rec_or_404(rec_id)
    _, ecfg = _entity_cfg(r["domain"], r["entity"])
    new = C.clean_data(ecfg, body.data)
    if not new:
        raise HTTPException(400, "Нет полей для изменения")
    idcol = ecfg.get("id")
    if idcol in new and new[idcol] != r["rid"]:
        raise HTTPException(400, "Идентификатор записи изменять нельзя")
    new.pop("publication_id", None)
    old = db.jl(r["data"], {})
    before = {k: old.get(k, "") for k in new}
    merged = {**old, **{k: v for k, v in new.items() if v != "" or k in old}}
    sets, args = ["data=?", "status='manual'", "version=version+1", "updated=?"], [db.jd(merged), db.now()]
    if "source_page" in new:
        sets.append("source_page=?"); args.append(int(new["source_page"]) if new["source_page"] else None)
    if "source_fragment" in new:
        sets += ["source_fragment=?", "quote_verified=NULL"]; args.append(new["source_fragment"] or None)
    db.ex("UPDATE records SET %s WHERE id=?" % ",".join(sets), args + [rec_id])
    _history(rec_id, user["name"], "edit", {**before, "_status": r["status"]}, new, body.comment)
    db.audit(user["name"], "record.edit", "%s/%s/%s" % (r["pub_id"], r["entity"], r["rid"]), {"fields": list(new), "comment": body.comment})
    warns = C.after_change(r["pub_id"])
    from .public import record as get_record
    out = get_record(rec_id)
    out["warnings"] = warns
    return out


class CreateIn(BaseModel):
    pub_id: str
    entity: str
    data: dict = {}


def _next_rid(pub_id, entity, schema):
    prefix = "R" if entity == schema.get("result_entity") else entity[:2].upper()
    mx = 0
    for r in db.q("SELECT rid FROM records WHERE pub_id=? AND entity=?", (pub_id, entity)):
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", r["rid"])
        if m:
            mx = max(mx, int(m.group(1)))
    return "%s%03d" % (prefix, mx + 1)


@router.post("/records")
def create_record(body: CreateIn, user=ann):
    p = C.pub_or_404(body.pub_id)
    cfg, ecfg = _entity_cfg(p["domain"], body.entity)
    if body.entity == cfg["schema"].get("publication_entity", "publications"):
        raise HTTPException(400, "Записи публикаций создаются конвейером, а не вручную")
    data = C.clean_data(ecfg, body.data)
    rid = _next_rid(body.pub_id, body.entity, cfg["schema"])
    data[ecfg["id"]] = rid
    data["publication_id"] = body.pub_id
    sp = int(data["source_page"]) if data.get("source_page") else None
    sf = data.get("source_fragment") or None
    rec_id = db.ex("INSERT INTO records(pub_id,domain,entity,rid,data,status,source_page,source_fragment,quote_verified,version,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (body.pub_id, p["domain"], body.entity, rid, db.jd(data), "manual", sp, sf, None, 1, db.now(), db.now()))
    _history(rec_id, user["name"], "create", {}, data, "")
    db.audit(user["name"], "record.create", "%s/%s/%s" % (body.pub_id, body.entity, rid), "")
    warns = C.after_change(body.pub_id)
    from .public import record as get_record
    out = get_record(rec_id)
    out["warnings"] = warns
    return out


class ActionIn(BaseModel):
    action: str
    comment: str = ""


@router.post("/records/{rec_id}/action")
def record_action(rec_id: int, body: ActionIn, user=ann):
    r = C.rec_or_404(rec_id)
    a = body.action
    if a not in ("confirm", "reject", "restore"):
        raise HTTPException(400, "action: confirm, reject или restore")
    if a == "restore":
        if r["status"] != "rejected":
            raise HTTPException(400, "Восстановить можно только отклонённую запись")
        prev = db.q1("SELECT before FROM record_history WHERE record_id=? AND action='reject' ORDER BY id DESC", (rec_id,))
        ps = (db.jl(prev["before"], {}) or {}).get("status") if prev else None
        new = ps if ps and ps != "rejected" else "manual"
    elif a == "confirm":
        if r["status"] == "rejected":
            raise HTTPException(400, "Сначала восстановите отклонённую запись")
        new = "verified"
    else:
        new = "rejected"
    if new == r["status"]:
        raise HTTPException(400, "Запись уже в статусе «%s»" % new)
    db.ex("UPDATE records SET status=?, version=version+1, updated=? WHERE id=?", (new, db.now(), rec_id))
    _history(rec_id, user["name"], a, {"status": r["status"]}, {"status": new}, body.comment)
    db.audit(user["name"], "record." + a, "%s/%s/%s" % (r["pub_id"], r["entity"], r["rid"]), body.comment)
    warns = C.after_change(r["pub_id"])
    cur = db.q1("SELECT status FROM records WHERE id=?", (rec_id,))
    if a == "confirm" and cur["status"] != "verified":  # конвейер не должен отменять подтверждение человека
        db.ex("UPDATE records SET status='verified' WHERE id=?", (rec_id,))
    from .public import record as get_record
    out = get_record(rec_id)
    out["warnings"] = warns
    return out


class ResolveIn(BaseModel):
    status: str
    comment: str = ""


@router.post("/issues/{issue_id}/resolve")
def resolve_issue(issue_id: int, body: ResolveIn, user=ann):
    i = db.q1("SELECT * FROM issues WHERE id=?", (issue_id,))
    if not i:
        raise HTTPException(404, "Замечание не найдено")
    if body.status not in ("accepted", "fixed", "kept"):
        raise HTTPException(400, "status: accepted, fixed или kept")
    db.ex("UPDATE issues SET status=?, comment=?, resolved_by=?, updated=? WHERE id=?", (body.status, body.comment, user["name"], db.now(), issue_id))
    db.audit(user["name"], "issue.resolve", str(issue_id), {"status": body.status, "comment": body.comment})
    C.refresh_pub_status(i["pub_id"])
    return C.issue_out(db.q1("SELECT * FROM issues WHERE id=?", (issue_id,)))


# ---------- gold и качество ----------

class GoldImportIn(BaseModel):
    domain: str
    dir: str


@router.post("/gold/import")
def gold_import(body: GoldImportIn, user=ann):
    C.cfg_or_404(body.domain)
    try:
        res = evaluation.import_gold(body.domain, body.dir)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.audit(user["name"], "gold.import", body.domain, {"dir": body.dir, **{k: res[k] for k in ("imported", "pubs")}})
    return res


class GoldPubIn(BaseModel):
    pub_id: str


@router.post("/gold/from_publication")
def gold_from_pub(body: GoldPubIn, user=ann):
    C.pub_or_404(body.pub_id)
    n = evaluation.gold_from_publication(body.pub_id)
    db.audit(user["name"], "gold.from_publication", body.pub_id, {"records": n})
    return {"pub_id": body.pub_id, "records": n}


@router.get("/gold")
def gold_list(domain: str, pub: str = None, entity: str = None, user=ann):
    C.cfg_or_404(domain)
    return evaluation.list_gold(domain, pub, entity)


class GoldPatchIn(BaseModel):
    data: dict


@router.patch("/gold/{pub_id}/{entity}/{rid}")
def gold_patch(pub_id: str, entity: str, rid: str, body: GoldPatchIn, domain: str = None, user=ann):
    hits = evaluation.find_gold(pub_id, entity, rid, domain)
    if not hits:
        raise HTTPException(404, "Эталонная запись не найдена")
    if len(hits) > 1:
        raise HTTPException(400, "Запись есть в нескольких областях: укажите ?domain=")
    d, pid = hits[0]
    _, ecfg = _entity_cfg(d, entity)
    data = C.clean_data(ecfg, body.data)
    cur = evaluation.patch_gold(pid, entity, rid, data, d)
    db.audit(user["name"], "gold.edit", "%s/%s/%s/%s" % (d, pub_id, entity, rid), list(data))
    return {"pub_id": pid, "publication_id": pub_id, "domain": d, "entity": entity, "rid": rid, "data": cur}


class QualityIn(BaseModel):
    domain: str
    pub_ids: list[str] = []
    note: str = ""


@router.post("/quality/run")
def quality_run(body: QualityIn, user=ann):
    C.cfg_or_404(body.domain)
    try:
        return evaluation.run(body.domain, body.pub_ids or None, body.note, user["name"])
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/quality/runs")
def quality_runs(domain: str = None, user=ann):
    return evaluation.list_runs(domain)


@router.get("/quality/runs/{run_id}")
def quality_run_get(run_id: int, user=ann):
    r = evaluation.get_run(run_id)
    if not r:
        raise HTTPException(404, "Прогон не найден")
    return r
