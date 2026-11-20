"""Эталон (gold) и оценка качества извлечения: P/R/F1 по полям, сводная микро-F1, регрессия."""
import csv, json, pathlib
from . import db, config

SKIP_FIELDS = {"publication_id", "duplicate_of", "note", "source_fragment"}  # служебные/свободный текст; расширяется schema.eval.skip_fields
FINAL_STATUSES = ("verified", "flagged", "excluded")
REGRESSION_DROP = 0.01


GOLD_DDL = "CREATE TABLE IF NOT EXISTS gold_records(pub_id TEXT NOT NULL, domain TEXT NOT NULL, entity TEXT NOT NULL, rid TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(domain, pub_id, entity, rid))"


def ensure_tables():
    c = db.conn()
    cols = c.execute("PRAGMA table_info(gold_records)").fetchall()
    if cols:
        pk = {r["name"]: r["pk"] for r in cols}
        if pk.get("domain", 0) == 0:  # старая схема PK(pub_id, entity, rid): пересоздаём с сохранением данных
            rows = c.execute("SELECT pub_id,domain,entity,rid,data FROM gold_records").fetchall()
            c.execute("DROP TABLE gold_records")
            c.execute(GOLD_DDL)
            c.executemany("INSERT OR REPLACE INTO gold_records(pub_id,domain,entity,rid,data) VALUES(?,?,?,?,?)", [tuple(r) for r in rows])
            c.commit()
            return
    c.execute(GOLD_DDL)
    c.commit()


def pub_stem(p):
    """Имя файла без расширения (publication_id во внешних файлах); запасной вариант — внутренний id."""
    fn = (p.get("filename") or "").strip()
    return pathlib.PurePath(fn).stem if fn else p["id"]


def resolve_pub(domain, ref):
    """Внутренний pub_id по значению из CSV/запроса: id в области, id-<область>, имя файла без расширения."""
    ref = (ref or "").strip()
    if not ref:
        return None
    for pid in (ref, "%s-%s" % (ref, domain)):
        r = db.q1("SELECT id FROM publications WHERE domain=? AND id=?", (domain, pid))
        if r:
            return r["id"]
    for r in db.q("SELECT id,filename FROM publications WHERE domain=?", (domain,)):
        if pub_stem(r) == ref:
            return r["id"]
    return None


def stems(domain, ids):
    m = {r["id"]: pub_stem(r) for r in db.q("SELECT id,filename FROM publications WHERE domain=?", (domain,))}
    return [m.get(i, i) for i in ids]


# ---------- gold ----------

def import_gold(domain, directory):
    ensure_tables()
    cfg = config.load_domain(domain)
    ents = cfg["schema"]["entities"]
    d = pathlib.Path(directory).expanduser()
    if not d.is_dir():
        raise ValueError("Папка не найдена: %s" % directory)
    imported, skipped, pubs, unresolved = {}, [], set(), []
    for f in sorted(d.glob("*.csv")):
        ent = f.stem
        if ent not in ents:
            skipped.append({"file": f.name, "reason": "сущности «%s» нет в схеме области" % ent})
            continue
        idcol = ents[ent].get("id")
        with open(f, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if rows and "publication_id" not in rows[0]:
            skipped.append({"file": f.name, "reason": "нет колонки publication_id"})
            continue
        by_pub = {}
        for i, row in enumerate(rows, 1):
            ref = row["publication_id"].strip()
            pid = resolve_pub(domain, ref)
            if pid is None:
                if ref not in unresolved:
                    unresolved.append(ref)
                pid = ref
            by_pub.setdefault(pid, []).append((i, row))
        with db.tx() as c:
            for pid, lst in by_pub.items():
                c.execute("DELETE FROM gold_records WHERE domain=? AND pub_id=? AND entity=?", (domain, pid, ent))
                seen = set()
                for i, row in lst:
                    data = {k: (v or "").strip() for k, v in row.items() if k and k != "publication_id"}
                    rid = data.get(idcol, "") if idcol else ""
                    if ent == "publications" and not rid:
                        rid = pid
                    rid = rid or "G%03d" % i
                    if rid in seen:
                        rid = "%s#%d" % (rid, i)
                    seen.add(rid)
                    c.execute("INSERT OR REPLACE INTO gold_records(pub_id,domain,entity,rid,data) VALUES(?,?,?,?,?)", (pid, domain, ent, rid, db.jd(data)))
                pubs.add(pid)
        imported[ent] = len(rows)
    return {"imported": imported, "pubs": stems(domain, sorted(pubs)), "pub_ids": sorted(pubs), "unresolved": unresolved, "skipped": skipped}


def _with_source(row):
    d = db.jl(row["data"], {})
    if row.get("source_page") is not None and not d.get("source_page"):
        d["source_page"] = str(row["source_page"])
    if row.get("source_fragment") and not d.get("source_fragment"):
        d["source_fragment"] = row["source_fragment"]
    return d


def gold_from_publication(pub_id, domain=None):
    ensure_tables()
    pub = db.q1("SELECT domain FROM publications WHERE id=?", (pub_id,))
    if not pub:
        raise LookupError("Публикация не найдена")
    n = 0
    with db.tx() as c:
        c.execute("DELETE FROM gold_records WHERE domain=? AND pub_id=?", (pub["domain"], pub_id))
        for r in db.q("SELECT entity,rid,data,source_page,source_fragment FROM records WHERE pub_id=? AND status!='rejected'", (pub_id,)):
            data = _with_source(r)
            if str(data.get("duplicate_of", "") or "").strip():
                continue
            data.pop("publication_id", None)
            c.execute("INSERT OR REPLACE INTO gold_records(pub_id,domain,entity,rid,data) VALUES(?,?,?,?,?)", (pub_id, pub["domain"], r["entity"], r["rid"], db.jd(data)))
            n += 1
    return n


def list_gold(domain, pub_id=None, entity=None):
    ensure_tables()
    sql, args = "SELECT pub_id,domain,entity,rid,data FROM gold_records WHERE domain=?", [domain]
    if pub_id:
        sql += " AND pub_id=?"; args.append(resolve_pub(domain, pub_id) or pub_id)
    if entity:
        sql += " AND entity=?"; args.append(entity)
    sm = dict(zip(*[iter([x for p in db.q("SELECT id,filename FROM publications WHERE domain=?", (domain,)) for x in (p["id"], pub_stem(p))])] * 2))
    return [{**r, "publication_id": sm.get(r["pub_id"], r["pub_id"]), "data": db.jl(r["data"], {})} for r in db.q(sql + " ORDER BY pub_id, entity, rid", args)]


def find_gold(pub_ref, entity, rid, domain=None):
    ensure_tables()
    doms = [domain] if domain else [r["domain"] for r in db.q("SELECT DISTINCT domain FROM gold_records")]
    hits = []
    for d in doms:
        pid = resolve_pub(d, pub_ref) or pub_ref
        if db.q1("SELECT 1 FROM gold_records WHERE domain=? AND pub_id=? AND entity=? AND rid=?", (d, pid, entity, rid)):
            hits.append((d, pid))
    return hits


def patch_gold(pub_id, entity, rid, data, domain):
    ensure_tables()
    row = db.q1("SELECT data FROM gold_records WHERE domain=? AND pub_id=? AND entity=? AND rid=?", (domain, pub_id, entity, rid))
    if not row:
        raise LookupError("Эталонная запись не найдена")
    cur = db.jl(row["data"], {})
    cur.update({k: ("" if v is None else str(v)) for k, v in data.items()})
    db.ex("UPDATE gold_records SET data=? WHERE domain=? AND pub_id=? AND entity=? AND rid=?", (db.jd(cur), domain, pub_id, entity, rid))
    return cur


# ---------- сравнение значений ----------

def _num(v):
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _s(v):
    return " ".join(str(v if v is not None else "").lower().split())


def values_equal(a, b, numeric_tol=False):
    a, b = _s(a), _s(b)
    if a == b:
        return True
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return False
    diff = abs(x - y)
    if diff <= 1e-6:
        return True
    return bool(numeric_tol and diff <= 0.005 * max(abs(x), abs(y)))


def _eval_cfg(schema):
    ev = schema.get("eval") or {}
    return ev.get("match_fields") or {}, set(ev.get("value_fields") or []), SKIP_FIELDS | set(ev.get("skip_fields") or [])


def _match(gold_rows, pred_rows, mfields, vfields, fields=None):
    """Жадное однозначное сопоставление по match_fields. mfields == ['*'] — по наибольшему числу совпавших непустых полей
    (для сущностей со свободными названиями, где точный ключ не определён). Возвращает (пары, не найденные gold, лишние pred)."""
    used, pairs, miss = set(), [], []
    for g in gold_rows:
        hit = None
        if list(mfields) == ["*"]:
            best = 0
            for i, p in enumerate(pred_rows):
                if i in used:
                    continue
                score = sum(1 for f in (fields or []) if _s(g.get(f, "")) and values_equal(g.get(f, ""), p.get(f, ""), f in vfields))
                if score > best:
                    best, hit = score, i
        else:
            for i, p in enumerate(pred_rows):
                if i in used:
                    continue
                if all(values_equal(g.get(f, ""), p.get(f, ""), f in vfields) for f in mfields):
                    hit = i
                    break
        if hit is None:
            miss.append(g)
        else:
            used.add(hit)
            pairs.append((g, pred_rows[hit]))
    return pairs, miss, [p for i, p in enumerate(pred_rows) if i not in used]


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def evaluate(cfg, gold, pred):
    """gold/pred: {pub_id: {entity: [data,...]}}. Возвращает разбор по сущностям/полям и сводку."""
    schema = cfg["schema"]
    mf, vfields, skip = _eval_cfg(schema)
    ents = schema["entities"]
    result_entity = schema.get("result_entity")
    per_ent = {}
    for ent, mfields in mf.items():
        if ent not in ents:
            continue
        refs = set((ents[ent].get("refs") or {}).keys())
        fields = [c for c in ents[ent]["columns"] if c != ents[ent].get("id") and c not in refs and c not in skip]
        acc = {f: [0, 0, 0] for f in fields}
        rows = {"gold": 0, "pred": 0, "matched": 0}
        for pid in gold:
            g_rows = gold[pid].get(ent, [])
            p_rows = pred.get(pid, {}).get(ent, [])
            pairs, miss, extra = _match(g_rows, p_rows, mfields, vfields, fields)
            rows["gold"] += len(g_rows); rows["pred"] += len(p_rows); rows["matched"] += len(pairs)
            for g, p in pairs:
                for f in fields:
                    gv, pv = _s(g.get(f, "")), _s(p.get(f, ""))
                    if not gv and not pv:
                        continue
                    if gv and pv and values_equal(gv, pv, f in vfields):
                        acc[f][0] += 1
                    else:
                        if pv:
                            acc[f][1] += 1
                        if gv:
                            acc[f][2] += 1
            for g in miss:
                for f in fields:
                    if _s(g.get(f, "")):
                        acc[f][2] += 1
            for p in extra:
                for f in fields:
                    if _s(p.get(f, "")):
                        acc[f][1] += 1
        ftab = {f: _prf(*v) for f, v in acc.items() if any(v)}
        tp = sum(v[0] for v in acc.values()); fp = sum(v[1] for v in acc.values()); fn = sum(v[2] for v in acc.values())
        per_ent[ent] = {**_prf(tp, fp, fn), "rows": rows, "fields": ftab}
    tp = sum(e["tp"] for e in per_ent.values()); fp = sum(e["fp"] for e in per_ent.values()); fn = sum(e["fn"] for e in per_ent.values())
    total = _prf(tp, fp, fn)
    key = per_ent.get(result_entity) or {}
    return {"entities": per_ent, "precision": total["precision"], "recall": total["recall"], "f1": total["f1"], "tp": tp, "fp": fp, "fn": fn,
            "key_entity": {"entity": result_entity, "precision": key.get("precision"), "recall": key.get("recall"), "f1": key.get("f1")}}


# ---------- прогон ----------

def corpus_coverage(domain):
    tot = db.q1("SELECT COUNT(*) n FROM publications WHERE domain=?", (domain,))["n"]
    if not tot:
        return None
    ok = db.q1("SELECT COUNT(*) n FROM publications WHERE domain=? AND status IN ('verified','flagged','excluded')", (domain,))["n"]
    return round(ok / tot, 4)


def quote_valid_share(domain, pub_ids=None):
    sql = "SELECT SUM(quote_verified=1) ok, COUNT(*) n FROM records WHERE domain=? AND quote_verified IS NOT NULL"
    args = [domain]
    if pub_ids:
        sql += " AND pub_id IN (%s)" % ",".join("?" * len(pub_ids))
        args += list(pub_ids)
    r = db.q1(sql, args)
    return round((r["ok"] or 0) / r["n"], 4) if r and r["n"] else None


def _current_model():
    r = db.q1("SELECT value FROM settings WHERE key='model'")
    return r["value"] if r else ""


def run(domain, pub_ids=None, note="", user="system"):
    ensure_tables()
    cfg = config.load_domain(domain)
    gold_pubs = [r["pub_id"] for r in db.q("SELECT DISTINCT pub_id FROM gold_records WHERE domain=? ORDER BY pub_id", (domain,))]
    if pub_ids:
        want = list(dict.fromkeys(pub_ids))
        res = {w: (resolve_pub(domain, w) or w) for w in want}
        skipped = [w for w in want if res[w] not in gold_pubs]
        pubs = sorted({res[w] for w in want if res[w] in gold_pubs})
    else:
        pubs, skipped = gold_pubs, []
    if not pubs:
        raise ValueError("Нет эталонных записей для выбранных публикаций: сначала импортируйте gold")
    gold, pred = {}, {}
    for r in db.q("SELECT pub_id,entity,data FROM gold_records WHERE domain=? AND pub_id IN (%s)" % ",".join("?" * len(pubs)), [domain] + pubs):
        gold.setdefault(r["pub_id"], {}).setdefault(r["entity"], []).append(db.jl(r["data"], {}))
    for r in db.q("SELECT pub_id,entity,data,source_page,source_fragment FROM records WHERE domain=? AND status!='rejected' AND pub_id IN (%s) ORDER BY id" % ",".join("?" * len(pubs)), [domain] + pubs):
        d = _with_source(r)
        if str(d.get("duplicate_of", "") or "").strip():
            continue
        pred.setdefault(r["pub_id"], {}).setdefault(r["entity"], []).append(d)
    m = evaluate(cfg, gold, pred)
    m.update({"pub_ids": stems(domain, pubs), "skipped_pubs": skipped, "quote_valid_share": quote_valid_share(domain, pubs), "coverage": corpus_coverage(domain),
              "n_pubs": len(pubs), "regression": False, "prev_run_id": None, "prev_f1": None, "delta": None})
    prev = None
    for r in db.q("SELECT id,pub_ids,metrics FROM eval_runs WHERE domain=? ORDER BY id DESC", (domain,)):
        if sorted(db.jl(r["pub_ids"], [])) == pubs:
            prev = r
            break
    if prev:
        pf = (db.jl(prev["metrics"], {}) or {}).get("f1")
        m["prev_run_id"], m["prev_f1"] = prev["id"], pf
        if pf is not None and m["f1"] is not None:
            m["delta"] = round(m["f1"] - pf, 4)
            m["regression"] = (pf - m["f1"]) > REGRESSION_DROP
    rid = db.ex("INSERT INTO eval_runs(domain,ts,model,config_version,note,pub_ids,metrics) VALUES(?,?,?,?,?,?,?)",
                (domain, db.now(), _current_model(), cfg["version"], note or "", db.jd(pubs), db.jd(m)))
    db.audit(user, "quality.run", domain, {"run": rid, "f1": m["f1"], "regression": m["regression"]})
    return {"id": rid, "domain": domain, "ts": db.now(), "config_version": cfg["version"], "note": note or "", **m}


def run_summary(row):
    m = db.jl(row["metrics"], {}) or {}
    return {"id": row["id"], "domain": row["domain"], "ts": row["ts"], "model": row["model"], "config_version": row["config_version"], "note": row["note"],
            "pub_ids": stems(row["domain"], db.jl(row["pub_ids"], [])), "f1": m.get("f1"), "precision": m.get("precision"), "recall": m.get("recall"),
            "key_f1": (m.get("key_entity") or {}).get("f1"), "quote_valid_share": m.get("quote_valid_share"), "coverage": m.get("coverage"),
            "regression": bool(m.get("regression"))}


def list_runs(domain=None):
    rows = db.q("SELECT * FROM eval_runs WHERE domain=? ORDER BY id DESC", (domain,)) if domain else db.q("SELECT * FROM eval_runs ORDER BY id DESC")
    return [run_summary(r) for r in rows]


def get_run(run_id):
    r = db.q1("SELECT * FROM eval_runs WHERE id=?", (run_id,))
    if not r:
        return None
    return {**run_summary(r), **(db.jl(r["metrics"], {}) or {})}
