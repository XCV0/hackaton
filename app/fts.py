"""Полнотекстовый индекс FTS5 (records_fts): текст записи = значения полей + название публикации."""
import re
from . import db

PUB_ENTITY = "publication"


def _record_text(data, title):
    vals = [str(v) for k, v in data.items() if v not in (None, "") and k != "publication_id"]
    return " ".join(vals + [title or ""])


def reindex(pub_id=None):
    """Перестраивает индекс одной публикации (или всего корпуса). Отклонённые записи в индекс не попадают."""
    c = db.conn()
    pubs = [db.q1("SELECT id,title,authors,year,doi FROM publications WHERE id=?", (pub_id,))] if pub_id else db.q("SELECT id,title,authors,year,doi FROM publications")
    with db.tx() as c:
        if pub_id:
            c.execute("DELETE FROM records_fts WHERE pub_id=?", (pub_id,))
        else:
            c.execute("DELETE FROM records_fts")
        for p in pubs:
            if not p:
                continue
            c.execute("INSERT INTO records_fts(rid,pub_id,entity,text) VALUES(?,?,?,?)",
                      (p["id"], p["id"], PUB_ENTITY, " ".join(str(x) for x in (p["title"], p["authors"], p["year"], p["doi"]) if x)))
            for r in c.execute("SELECT rid,entity,data FROM records WHERE pub_id=? AND status!='rejected'", (p["id"],)).fetchall():
                c.execute("INSERT INTO records_fts(rid,pub_id,entity,text) VALUES(?,?,?,?)",
                          (r["rid"], p["id"], r["entity"], _record_text(db.jl(r["data"], {}), p["title"])))


def match_query(q):
    toks = re.findall(r"\w+", q or "", flags=re.UNICODE)
    return " ".join('"%s"*' % t for t in toks)


def search(q, domain=None, limit=50):
    mq = match_query(q)
    if not mq:
        return []
    sql = ("SELECT f.rid, f.pub_id, f.entity, snippet(records_fts, 3, '«', '»', '…', 14) AS snip, r.id AS rec_id "
           "FROM records_fts f JOIN publications p ON p.id=f.pub_id "
           "LEFT JOIN records r ON r.pub_id=f.pub_id AND r.entity=f.entity AND r.rid=f.rid "
           "WHERE records_fts MATCH ?")
    args = [mq]
    if domain:
        sql += " AND p.domain=?"
        args.append(domain)
    sql += " ORDER BY rank LIMIT ?"
    args.append(int(limit))
    out = []
    for r in db.q(sql, args):
        if r["entity"] == PUB_ENTITY:
            out.append({"kind": "publication", "pub_id": r["pub_id"], "entity": "publications", "rid": r["rid"], "id": None, "snippet": r["snip"]})
        else:
            out.append({"kind": "record", "pub_id": r["pub_id"], "entity": r["entity"], "rid": r["rid"], "id": r["rec_id"], "snippet": r["snip"]})
    return out
