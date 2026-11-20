"""Сопоставление результатов по schema.compare.criteria. Пути вида `device.active_area` резолвятся через refs схемы."""
from collections import deque
from . import db

NOT_STATED = "значение не указано в статье"


def entity_by_prefix(schema, prefix):
    ents = schema["entities"]
    for cand in (prefix, prefix + "s", prefix + "es"):
        if cand in ents:
            return cand
    for n in ents:
        if n.startswith(prefix):
            return n
    return None


def _ref_graph(schema):
    g = {}
    for name, e in schema["entities"].items():
        for fld, target in (e.get("refs") or {}).items():
            g.setdefault(name, []).append((fld, target))
    return g


def ref_paths(schema, src, dst, maxlen=4):
    """Все простые цепочки [(поле, целевая сущность), ...] от src к dst по refs, от коротких к длинным."""
    if src == dst:
        return [[]]
    g = _ref_graph(schema)
    out = []

    def dfs(cur, path, seen):
        if len(path) >= maxlen:
            return
        for fld, tgt in g.get(cur, []):
            if tgt in seen:
                continue
            np = path + [(fld, tgt)]
            if tgt == dst:
                out.append(np)
            else:
                dfs(tgt, np, seen | {tgt})
    dfs(src, [], {src})
    return sorted(out, key=len)


class Resolver:
    """Ленивая загрузка записей публикаций: (pub, entity, rid) -> data."""

    def __init__(self, schema):
        self.schema = schema
        self._pubs = {}

    def _load(self, pub_id):
        if pub_id not in self._pubs:
            m = {}
            for r in db.q("SELECT entity,rid,data FROM records WHERE pub_id=?", (pub_id,)):
                m[(r["entity"], r["rid"])] = db.jl(r["data"], {})
            self._pubs[pub_id] = m
        return self._pubs[pub_id]

    def get(self, pub_id, entity, rid):
        return self._load(pub_id).get((entity, str(rid).strip()))

    def related(self, pub_id, entity, data, ref_field):
        target = self.schema["entities"][entity]["refs"][ref_field]
        for rid in str(data.get(ref_field, "") or "").split(";"):
            rid = rid.strip()
            if rid:
                d = self.get(pub_id, target, rid)
                if d is not None:
                    return target, d
        return target, None

    def resolve(self, rec, path):
        """Значение критерия для записи: путь `<сущность>.<поле>`; пусто, если цепочка связей не разрешилась."""
        prefix, _, fld = path.partition(".")
        target = entity_by_prefix(self.schema, prefix)
        if not target:
            return ""
        if rec["entity"] == target:
            return str(rec["data"].get(fld, "") or "").strip()
        # пробуем все цепочки связей; первое непустое значение выигрывает (result.material_id, иначе result->device->material)
        for chain in ref_paths(self.schema, rec["entity"], target):
            data, ent = rec["data"], rec["entity"]
            for f, tgt in chain:
                _, data = self.related(rec["pub_id"], ent, data, f)
                if data is None:
                    break
                ent = tgt
            if data is not None:
                v = str(data.get(fld, "") or "").strip()
                if v:
                    return v
        return ""


def _num(v):
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _norm(v):
    s = " ".join(str(v).lower().split())
    n = _num(s)
    return n if n is not None else s


def check_pair(criteria, ra, rb):
    """Причины несопоставимости пары записей (ra, rb: {'resolved': {...}})."""
    reasons = []
    for c in criteria:
        name = c["name"]
        a, b = ra["resolved"].get(name, ""), rb["resolved"].get(name, "")
        base = c.get("reason") or name
        if a == "" and b == "" and c.get("allow_empty"):
            continue  # критерий явно допускает «не указано у обеих сторон»
        if a == "" or b == "":
            reasons.append({"criterion": name, "reason": "%s: %s" % (base, NOT_STATED), "a_value": a, "b_value": b})
            continue
        mode = c.get("mode", "equal")
        if mode == "ratio_within":
            x, y = _num(a), _num(b)
            lim = float(c.get("param", 2.0))
            if x is None or y is None or x <= 0 or y <= 0:
                reasons.append({"criterion": name, "reason": "%s: значения не являются положительными числами" % base, "a_value": a, "b_value": b})
            elif max(x, y) / min(x, y) > lim:
                reasons.append({"criterion": name, "reason": "%s: отличаются более чем в %g раз" % (base, lim), "a_value": a, "b_value": b})
        else:
            if _norm(a) != _norm(b):
                reasons.append({"criterion": name, "reason": base, "a_value": a, "b_value": b})
    return reasons


def prepare(domain_cfg, recs):
    comp = domain_cfg["schema"].get("compare") or {}
    criteria = comp.get("criteria") or []
    res = Resolver(domain_cfg["schema"])
    out = []
    for r in recs:
        out.append({**r, "resolved": {c["name"]: res.resolve(r, c["path"]) for c in criteria}})
    return criteria, out


def compare(domain_cfg, recs):
    """recs: [{'id','pub_id','entity','rid','data',...}] -> результат контракта POST /compare."""
    criteria, items = prepare(domain_cfg, recs)
    blocked, bad = [], set()
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            reasons = check_pair(criteria, items[i], items[j])
            if reasons:
                blocked.append({"a": items[i]["id"], "b": items[j]["id"], "reasons": reasons})
                bad.add((i, j))
    groups = []
    for i in range(n):
        for g in groups:
            if all(((min(i, k), max(i, k)) not in bad) for k in g):
                g.append(i)
                break
        else:
            groups.append([i])
    return {"comparable": n >= 2 and not blocked,
            "rows": [{"record": {k: v for k, v in it.items() if k != "resolved"}, "resolved": it["resolved"]} for it in items],
            "blocked": blocked,
            "groups": [[items[k]["id"] for k in g] for g in groups]}


def candidates(domain_cfg, base, pool):
    """Записи из pool, сопоставимые с base по всем критериям."""
    criteria, items = prepare(domain_cfg, [base] + pool)
    b = items[0]
    return [it["id"] for it in items[1:] if it["id"] != b["id"] and not check_pair(criteria, b, it)]
