"""Нормализация записей и движок правил. Полностью предметно-независимо: всё из schema.yaml / rules.yaml.

Публичный интерфейс:
    normalize_record(cfg, entity, data) -> dict
    normalize_all(cfg, tables) -> dict
    run_rules(cfg, tables) -> list[issue]
    RULE_TYPES = {type: handler}
"""
import ast
import collections
import logging
import math
import operator
import re

log = logging.getLogger("rules")

# общие (непредметные) имена полей записи результата; можно переопределить в schema (metric_field)
METRIC_FIELD = "metric_name"
DEFAULT_CONTEXT_SKIP = ("time_point",)   # записи «в момент времени» не участвуют в расчётах группы (переопределяется skip_if_set)

FRACTION_WORDS = ("fraction", "ratio", "доля", "доли", "долиединицы", "frac")
PERCENT_WORDS = ("%", "percent", "процент", "pct")


# ---------------------------------------------------------------- числа и единицы
_NUM = re.compile(r"^[~≈]?\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)$")
_RANGE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:[–—-]|\bto\b|\bдо\b)\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


def parse_number(v):
    """(число|None, единица_из_текста). Мусор, диапазоны, NaN/inf -> (None, '')."""
    if v is None or isinstance(v, bool):
        return None, ""
    if isinstance(v, (int, float)):
        return (float(v), "") if math.isfinite(v) else (None, "")
    s = str(v).strip()
    if not s:
        return None, ""
    s = s.replace("−", "-").replace("–", "-").replace("—", "-").replace(" ", " ").replace(" ", " ")
    s = s.split("±")[0].strip()
    s = re.sub(r"^[↓↑]", "", s)
    if _RANGE.match(s.replace(",", ".")):
        return None, ""
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)
    m = _NUM.match(s)
    if not m:
        return None, ""
    try:
        x = float(m.group(1))
    except ValueError:
        return None, ""
    if not math.isfinite(x):
        return None, ""
    rest = m.group(2).strip()
    # хвост допустим только как единица (без цифр-диапазонов/знаков сравнения/второго числа)
    if rest and (re.search(r"\d\s*[-–]\s*\d", rest) or re.match(r"^[<>=≤≥]", rest)):
        return None, ""
    return x, rest


def fnum(v):
    return parse_number(v)[0]


def fmt(x, digits=6):
    """Строка числа без хвостовых нулей."""
    if x is None:
        return ""
    s = ("%.*f" % (digits, x)).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def canon_unit(u):
    s = str(u or "").strip().lower()
    for a, b in (("µ", "u"), ("μ", "u"), ("²", "2"), ("⁻", "-"), ("−", "-"), ("·", ""), ("⋅", ""), ("*", ""), (" ", ""), ("^", ""), ("ω", "ohm"), ("Ω".lower(), "ohm")):
        s = s.replace(a, b)
    s = re.sub(r"([a-z]+)cm-2", r"\1/cm2", s)
    s = re.sub(r"([a-z]+)m-2", r"\1/m2", s)
    return s


def _is_fraction_unit(cu):
    return any(w in cu for w in FRACTION_WORDS)


def _is_percent_unit(cu):
    return any(w in cu for w in PERCENT_WORDS)


def _metric_cfg(cfg, name):
    metrics = (cfg.get("schema") or {}).get("metrics") or {}
    if name in metrics:
        return name, metrics[name] or {}
    low = str(name or "").strip().lower()
    for k, v in metrics.items():
        if str(k).lower() == low:
            return k, v or {}
    return None, None


def _convert(cfg, mname, mcfg, val, unit, rv_unit_text):
    """-> (value|None, unit_out). Пусто = пересчёт невозможен/небезопасен."""
    scale_req = bool((cfg.get("schema") or {}).get("scale_required"))
    cu = canon_unit(unit or rv_unit_text)
    kind = (mcfg or {}).get("kind")
    target = (mcfg or {}).get("unit", "")
    conv = {canon_unit(k): float(v) for k, v in ((mcfg or {}).get("conversions") or {}).items()}
    if mcfg is None:
        # метрика без правил
        if scale_req:
            if _is_percent_unit(cu):
                return val, "%"
            if _is_fraction_unit(cu):
                return val, "fraction"
            return None, ""
        return val, (unit or rv_unit_text or "")
    if kind == "percent":
        if _is_percent_unit(cu):
            return val, "%"
        if _is_fraction_unit(cu):
            return val * 100, "%"
        if not cu and not scale_req and val <= 1:
            return val * 100, "%"      # единица не указана: доля, если ≤1
        if not cu and not scale_req:
            return val, "%"
        return None, ""
    if conv:
        if not cu:
            if scale_req:
                return None, ""
            return val, target
        if cu in conv:
            return val * conv[cu], target
        if cu == canon_unit(target):
            return val, target
        return None, ""
    if target:
        if not cu:
            return (None, "") if scale_req else (val, target)
        if cu == canon_unit(target):
            return val, target
        return None, ""
    return val, (unit or rv_unit_text or "")


def _scale_of(u):
    cu = canon_unit(u)
    return "%" if _is_percent_unit(cu) and not _is_fraction_unit(cu) else ("fraction" if _is_fraction_unit(cu) else None)


def _convert_scale(val, raw_unit, text_unit, metric_unit):
    """Области со scale_required: пересчёт только между известными шкалами (% / доля). Целевая шкала — metric_unit, если она известна."""
    src = _scale_of(raw_unit) or _scale_of(text_unit) or _scale_of(metric_unit)
    if not src:
        return None, ""
    dst = _scale_of(metric_unit) or src
    if src == dst:
        return val, dst
    return (val * 100 if dst == "%" else val / 100), dst


def normalize_record(cfg, entity, data):
    """Копия записи; для сущности результатов считает normalized_* и metric_*; raw_* не трогает."""
    out = dict(data or {})
    schema = cfg.get("schema") or {}
    if entity != schema.get("result_entity"):
        return out
    mfield = schema.get("metric_field", METRIC_FIELD)
    raw_v = out.get("raw_value")
    src_unit_is_raw = True
    if raw_v in (None, ""):
        raw_v, src_unit_is_raw = out.get("metric_value"), False
    val, text_unit = parse_number(raw_v)
    if val is None:
        out["normalized_value"], out["normalized_unit"] = "", ""
        return out
    unit = out.get("raw_unit") if src_unit_is_raw else out.get("metric_unit")
    mname, mcfg = _metric_cfg(cfg, out.get(mfield))
    if mcfg is None and schema.get("scale_required"):
        nv, nu = _convert_scale(val, str(unit or "") if src_unit_is_raw else "", text_unit, str(out.get("metric_unit") or ""))
    else:
        nv, nu = _convert(cfg, mname, mcfg, val, str(unit or ""), text_unit)
    if nv is None or not math.isfinite(nv):
        out["normalized_value"], out["normalized_unit"] = "", ""
        return out
    out["normalized_value"], out["normalized_unit"] = fmt(nv), nu
    out["metric_value"], out["metric_unit"] = out["normalized_value"], nu
    return out


def normalize_all(cfg, tables):
    return {ent: [normalize_record(cfg, ent, r) for r in rows] for ent, rows in (tables or {}).items()}


# ---------------------------------------------------------------- безопасный вычислитель
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def expr_names(expr):
    return [n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)]


def safe_eval(expr, env):
    """Считает выражение только из чисел, имён, + - * / ** и скобок. ValueError при недопустимом узле; None при делении на 0."""
    env = {k.lower(): v for k, v in env.items()}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id.lower() not in env:
                raise KeyError(n.id)
            return float(env[n.id.lower()])
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 10:
                raise ValueError("степень слишком велика")
            return _BIN[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        raise ValueError("недопустимое выражение: " + type(n).__name__)

    try:
        r = ev(ast.parse(expr, mode="eval"))
    except ZeroDivisionError:
        return None
    return r if math.isfinite(r) else None


# ---------------------------------------------------------------- контекст правил
def _rx(p, extra=0):
    """Компиляция regex; встроенные (?i) в середине (недопустимы в 3.11+) заменяются флагом."""
    flags = extra
    if "(?i)" in p:
        p, flags = p.replace("(?i)", ""), flags | re.I
    return re.compile(p, flags)


class Ctx:
    def __init__(self, cfg, tables):
        self.cfg = cfg
        self.schema = cfg.get("schema") or {}
        self.version = (cfg.get("rules") or {}).get("version", 1)
        self.ents = self.schema.get("entities") or {}
        self.result_entity = self.schema.get("result_entity")
        self.mfield = self.schema.get("metric_field", METRIC_FIELD)
        self.tables = {}
        for ent, rows in (tables or {}).items():
            self.tables[ent] = [r for r in (rows or []) if str(r.get("status") or "").lower() != "rejected"]

    def id_field(self, ent):
        return (self.ents.get(ent) or {}).get("id")

    def rid(self, rec, ent=None):
        f = self.id_field(ent or self.result_entity)
        return str(rec.get(f) or "") if f else ""

    def results(self, entity=None, with_dups=False, primary_only=False):
        rows = self.tables.get(entity or self.result_entity, [])
        out = []
        for r in rows:
            if not with_dups and str(r.get("duplicate_of") or "").strip():
                continue
            if primary_only and str(r.get("result_origin") or "").strip().lower() == "cited":
                continue
            out.append(r)
        return out

    def by_id(self, ent):
        f = self.id_field(ent)
        return {str(r.get(f)): r for r in self.tables.get(ent, []) if f and r.get(f)}

    def mname(self, rec):
        return str(rec.get(self.mfield) or "").strip().lower()

    def value(self, rec):
        v = fnum(rec.get("normalized_value"))
        return v if v is not None else fnum(rec.get("metric_value"))

    def unit(self, rec):
        for k in ("normalized_unit", "metric_unit", "raw_unit"):
            if str(rec.get(k) or "").strip():
                return str(rec[k]).strip()
        return ""

    def issue(self, rule, recs, description, severity=None):
        first = recs[0] if recs else {}
        return {
            "rule_id": rule["id"], "rule_version": self.version,
            "severity": severity or rule.get("severity", "warning"),
            "record_ids": [self.rid(r) for r in recs if self.rid(r)] if recs else [],
            "description": description,
            "source_page": first.get("source_page", "") if first else "",
            "source_fragment": first.get("source_fragment", "") if first else "",
        }

    def group(self, recs, group_by, skip_if_set=()):
        g = collections.OrderedDict()
        for r in recs:
            if any(str(r.get(f) or "").strip() for f in skip_if_set):
                continue
            key = tuple(str(r.get(f) or "") for f in group_by)
            if group_by and not any(key):
                continue
            g.setdefault(key, []).append(r)
        return g

    def label(self, group_by, key):
        parts = [f"{f}={v}" for f, v in zip(group_by, key) if v]
        return ", ".join(parts) or "запись"

    def metric_values(self, recs):
        m = {}
        for r in recs:
            v = self.value(r)
            if v is not None:
                m[self.mname(r)] = (v, r)
        return m


def _g(x):
    return f"{x:.5g}"


def _skip(p):
    return tuple(p.get("skip_if_set", DEFAULT_CONTEXT_SKIP))


# ---------------------------------------------------------------- правила
def rule_formula_consistency(ctx, rule):
    p = rule.get("params") or {}
    target, expr, tol = str(p["target"]).lower(), p["expr"], float(p.get("tolerance_rel", 0.03))
    defaults = {k.lower(): float(v) for k, v in (p.get("defaults") or {}).items()}
    gb = p.get("group_by") or []
    names = [n for n in dict.fromkeys(expr_names(expr))]
    out = []
    for key, recs in ctx.group(ctx.results(primary_only=True), gb, _skip(p)).items():
        mv = ctx.metric_values(recs)
        if target not in mv:
            continue
        env, used, defaulted = {}, [], []
        for n in names:
            if n.lower() in mv:
                env[n] = mv[n.lower()][0]
                used.append(mv[n.lower()][1])
            elif n.lower() in defaults:
                env[n] = defaults[n.lower()]
                defaulted.append(f"{n}={_g(defaults[n.lower()])}")
            else:
                env = None
                break
        if env is None:
            continue
        t, trec = mv[target]
        if t == 0:
            continue
        calc = safe_eval(expr, env)
        if calc is None:
            continue
        d = abs(calc - t) / abs(t)
        if d > tol:
            recs_ = [trec] + [r for r in used if r is not trec]
            desc = (f"{ctx.label(gb, key)}: {p['target']}={_g(t)}, расчёт по {expr}={calc:.2f} "
                    f"(расхождение {d * 100:.1f}% > {tol * 100:g}%"
                    + (f"; принято {', '.join(defaulted)}" if defaulted else "") + ")")
            out.append(ctx.issue(rule, recs_, desc))
    return out


def rule_bound(ctx, rule):
    p = rule.get("params") or {}
    metrics = p.get("metrics") or [p["metric"]]
    metrics = {str(m).lower() for m in metrics}
    lo, hi = p.get("min"), p.get("max")
    excl = bool(p.get("exclusive"))
    out = []
    for r in ctx.results():
        if ctx.mname(r) not in metrics:
            continue
        v = ctx.value(r)
        if v is None:
            continue
        bad = False
        if lo is not None and (v <= lo if excl else v < lo):
            bad = True
        if hi is not None and (v >= hi if excl else v > hi):
            bad = True
        if bad:
            u = ctx.unit(r)
            cond = "строго между" if excl else "в пределах"
            out.append(ctx.issue(rule, [r], f"{r.get(ctx.mfield)}={_g(v)}{(' ' + u) if u else ''} вне допустимого диапазона "
                                 f"({cond} {lo}–{hi})"))
    return out


def _resolve_right(ctx, rec, rf):
    """'materials via devices.absorber_material_id' -> строка целевой сущности или None."""
    m = re.match(r"^\s*(\w+)\s+via\s+(\w+)\.(\w+)\s*$", rf or "")
    if not m:
        return None
    target_ent, mid_ent, mid_field = m.groups()
    refs = (ctx.ents.get(ctx.result_entity) or {}).get("refs") or {}
    link = next((f for f, e in refs.items() if e == mid_ent), None)
    if not link or not rec.get(link):
        return None
    mid = ctx.by_id(mid_ent).get(str(rec[link]))
    if not mid or not mid.get(mid_field):
        return None
    return ctx.by_id(target_ent).get(str(mid[mid_field]))


_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def rule_inequality(ctx, rule):
    p = rule.get("params") or {}
    left = str(p["left"]).lower()
    op = p.get("op", "<")
    right_name = p["right"]
    out = []
    for r in ctx.results(primary_only=True):
        if ctx.mname(r) != left:
            continue
        lv = ctx.value(r)
        if lv is None:
            continue
        if p.get("right_from"):
            row = _resolve_right(ctx, r, p["right_from"])
            rv = fnum(row.get(right_name)) if row else None
            src = f"{right_name} ({row.get(ctx.id_field(p['right_from'].split()[0]), '')})" if row else right_name
        else:
            rv, src = fnum(r.get(right_name)), right_name
            if rv is None:
                rv = fnum(right_name)
        if rv is None:
            continue
        if not _OPS[op](lv, rv):
            u = ctx.unit(r)
            out.append(ctx.issue(rule, [r], f"{p['left']}={_g(lv)}{(' ' + u) if u else ''} нарушает условие {p['left']} {op} {src} "
                                 f"(= {_g(rv)}); найдено {p['left']} {'>=' if op == '<' else 'не ' + op} {_g(rv)}"))
    return out


def rule_pair_diff(ctx, rule):
    p = rule.get("params") or {}
    a, b, tol = str(p["a"]).lower(), str(p["b"]).lower(), float(p.get("tolerance_rel", 0.05))
    gb = p.get("group_by") or []
    out = []
    for key, recs in ctx.group(ctx.results(primary_only=True), gb, _skip(p)).items():
        mv = ctx.metric_values(recs)
        if a not in mv or b not in mv:
            continue
        (va, ra), (vb, rb) = mv[a], mv[b]
        if va == 0:
            continue
        d = abs(va - vb) / abs(va)
        if d > tol:
            out.append(ctx.issue(rule, [ra, rb], f"{ctx.label(gb, key)}: {p['a']}={_g(va)}, {p['b']}={_g(vb)} "
                                 f"расходятся на {d * 100:.1f}% (> {tol * 100:g}%)"))
    return out


def rule_champion_vs_mean(ctx, rule):
    p = rule.get("params") or {}
    metrics = [str(m).lower() for m in p.get("metrics", [])]
    gb = p.get("group_by") or []
    excess = p.get("max_rel_excess")
    f, cv, mv_ = p.get("champion_field", "aggregation"), p.get("champion_value", "champion"), p.get("mean_value", "mean")
    out = []
    for key, recs in ctx.group(ctx.results(primary_only=True), gb, _skip(p)).items():
        for m in metrics:
            champ = mean = None
            for r in recs:
                if ctx.mname(r) != m or ctx.value(r) is None:
                    continue
                a = str(r.get(f) or "").strip().lower()
                if a == cv:
                    champ = r
                elif a == mv_:
                    mean = r
            if not champ or not mean:
                continue
            c, mn = ctx.value(champ), ctx.value(mean)
            name = champ.get(ctx.mfield)
            if c < mn:
                out.append(ctx.issue(rule, [champ, mean], f"{ctx.label(gb, key)}: {name} лучшего прибора ({_g(c)}) меньше среднего ({_g(mn)})"))
            elif excess is not None and mn > 0 and (c - mn) / mn > float(excess):
                out.append(ctx.issue(rule, [champ, mean], f"{ctx.label(gb, key)}: {name} лучшего прибора ({_g(c)}) превышает среднее ({_g(mn)}) "
                                     f"на {(c - mn) / mn * 100:.0f}% (> {float(excess) * 100:g}%)"))
    return out


def rule_separate_values(ctx, rule):
    p = rule.get("params") or {}
    ent, fld, gb = p.get("entity") or ctx.result_entity, p["field"], p.get("group_by") or []
    out = []
    for key, recs in ctx.group(ctx.tables.get(ent, []), gb).items():
        vals = {str(r.get(fld) or "").strip() for r in recs} - {""}
        if len(vals) > 1:
            ids = [r for r in recs if str(r.get(fld) or "").strip()]
            out.append(ctx.issue(rule, ids, f"Значения поля {fld} различаются ({', '.join(sorted(vals))}): записи хранятся и сравниваются раздельно"
                                 + (f" ({ctx.label(gb, key)})" if gb else "")))
    return out


_DEF_SKIP_NAME = r"(?i)(change|decrease|increase|improv|delta|diff|gain|drop|\bsd\b|std|p-value|relative|speedup)"


def rule_metric_scale(ctx, rule):
    p = rule.get("params") or {}
    by_unit = {canon_unit(k): v for k, v in (p.get("by_unit") or {}).items()}
    scales = {canon_unit(k): v for k, v in (p.get("scales") or {}).items()}
    name_rx = [_rx(k) for k in (p.get("by_metric_regex") or {})]
    skip_rx = _rx(p.get("skip_name_regex", _DEF_SKIP_NAME))
    out = []
    for r in ctx.results():
        name = str(r.get(ctx.mfield) or "")
        if name_rx and not any(x.search(name) for x in name_rx):
            continue
        if skip_rx.search(name):
            continue
        v = ctx.value(r)
        if v is None:
            continue
        cu = canon_unit(ctx.unit(r))
        rng = by_unit.get(cu) or scales.get(cu)
        if not rng:
            continue      # шкала неизвестна — не пересчитываем и не проверяем
        lo, hi = rng
        if v < lo or v > hi:
            hint = ""
            if cu in ("fraction", "ratio") and 1 < v <= 100:
                hint = " (похоже на проценты, но единица указана как доля)"
            out.append(ctx.issue(rule, [r], f"{name}={_g(v)} {ctx.unit(r)} вне шкалы {lo}–{hi}{hint}"))
    return out


def rule_k_required(ctx, rule):
    p = rule.get("params") or {}
    rx = _rx(p["name_regex"])
    kf = p.get("k_field", "metric_k")
    out = []
    for r in ctx.results():
        name = str(r.get(ctx.mfield) or "")
        m = rx.search(name)
        if not m:
            continue
        digits = next((g for g in m.groups() if g and g.isdigit()), None)
        if digits is None:
            d = re.search(r"@\s*(\d+)", name)
            digits = d.group(1) if d else None
        have = str(r.get(kf) or "").strip()
        if not have:
            extra = f" (в имени метрики указано k={digits}, но {kf} пусто)" if digits and p.get("extract_k_from_name", True) else ""
            out.append(ctx.issue(rule, [r], f"Для метрики «{name}» не указано k ({kf}){extra}"))
        elif digits and fnum(have) is not None and fnum(have) != float(digits):
            out.append(ctx.issue(rule, [r], f"Для метрики «{name}» {kf}={have} не совпадает с k={digits} из имени метрики"))
    return out


def rule_link_integrity(ctx, rule):
    p = rule.get("params") or {}
    required, optional = p.get("required") or [], set(p.get("optional_ok") or [])
    refs = (ctx.ents.get(ctx.result_entity) or {}).get("refs") or {}
    idx = {ent: set(ctx.by_id(ent)) for ent in set(refs.values())}
    out = []
    for r in ctx.results(primary_only=True):
        probs = []
        for f in required:
            val = str(r.get(f) or "").strip()
            if not val:
                if f not in optional:
                    probs.append(f"{f} не указан")
            elif f in refs and val not in idx.get(refs[f], set()):
                probs.append(f"{f}={val} ссылается на несуществующую запись {refs[f]}")
        if probs:
            out.append(ctx.issue(rule, [r], "Результат не связан с записями публикации: " + "; ".join(probs)))
    return out


def rule_no_merge(ctx, rule):
    """Информационное: фиксирует, что разные эксперименты/варианты метрик не объединяются. Ничего не объединяет."""
    p = rule.get("params") or {}
    key = p.get("key") or []
    combos = collections.OrderedDict()
    for r in ctx.results(primary_only=True):
        combos.setdefault(tuple(str(r.get(f) or "") for f in key), r)
    if len(combos) < 2:
        return []
    recs = list(combos.values())
    return [ctx.issue(rule, recs[:50], f"{len(combos)} различных комбинаций ({', '.join(key)}): значения хранятся и сравниваются раздельно, не объединяются")]


RULE_TYPES = {
    "formula_consistency": rule_formula_consistency,
    "bound": rule_bound,
    "inequality": rule_inequality,
    "pair_diff": rule_pair_diff,
    "champion_vs_mean": rule_champion_vs_mean,
    "separate_values": rule_separate_values,
    "metric_scale": rule_metric_scale,
    "k_required": rule_k_required,
    "link_integrity": rule_link_integrity,
    "no_merge": rule_no_merge,
}


def run_rules(cfg, tables):
    ctx = Ctx(cfg, tables)
    out = []
    for rule in (cfg.get("rules") or {}).get("rules", []) or []:
        fn = RULE_TYPES.get(rule.get("type"))
        if not fn:
            log.warning("Неизвестный тип правила %r (id=%s) — пропущено", rule.get("type"), rule.get("id"))
            continue
        try:
            out.extend(fn(ctx, rule))
        except Exception as e:      # некорректные параметры одного правила не должны ронять остальные
            log.warning("Правило %s (%s) не выполнено: %s", rule.get("id"), rule.get("type"), e)
    return out
