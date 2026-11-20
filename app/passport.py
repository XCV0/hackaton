"""Паспорт набора данных: целиком генерируется из schema.yaml + rules.yaml (без ручного текста о полях)."""
import json
from . import config, db

DOCS_DIR = db.ROOT / "docs"


def _vocab_note(f):
    return f.get("vocab") or []


def passport(domain):
    cfg = config.load_domain(domain)
    s, r = cfg["schema"], cfg["rules"]
    ents = []
    for name, e in s["entities"].items():
        fields = e.get("fields") or {}
        refs = e.get("refs") or {}
        cols = []
        for c in e["columns"]:
            f = fields.get(c) or {}
            col = {"name": c, "type": f.get("type", "string")}
            if c == e.get("id"):
                col["role"] = "id"
            if f.get("vocab"):
                col["vocab"] = list(f["vocab"])
            if "default" in f:
                col["default"] = f["default"]
            if f.get("description"):
                col["description"] = f["description"]
            if c in refs:
                col["ref"] = refs[c]
            cols.append(col)
        ents.append({"name": name, "id": e.get("id"), "parent": e.get("parent"), "structure": bool(e.get("structure")),
                     "is_result": name == s.get("result_entity"), "columns": cols})
    metrics = [{"name": k, "unit": v.get("unit"), "kind": v.get("kind"), "conversions": v.get("conversions") or {}}
               for k, v in (s.get("metrics") or {}).items()]
    rules = [{"id": x.get("id"), "type": x.get("type"), "severity": x.get("severity"), "description": x.get("description", ""),
              "params": x.get("params") or {}} for x in r.get("rules", [])]
    comp = s.get("compare") or {}
    return {"domain": domain, "title": s.get("title", domain), "scope": s.get("scope", ""),
            "version": cfg["version"], "schema_version": s.get("version", 1), "rules_version": r.get("version", 1),
            "result_entity": s.get("result_entity"), "entities": ents, "metrics": metrics,
            "scale_required": bool(s.get("scale_required", False)), "rules": rules,
            "compare": {"entity": comp.get("entity"), "title": comp.get("title"), "criteria": comp.get("criteria", [])},
            "eval": s.get("eval") or {}, "extraction_hints": (s.get("extraction") or {}).get("hints", [])}


def _cell(x):
    return str(x).replace("|", "\\|").replace("\n", " ")


def markdown(domain):
    p = passport(domain)
    L = ["# Паспорт набора данных: %s" % p["title"], "",
         "Область: `%s`. Версия конфигурации: **%s**. Документ сгенерирован автоматически из `schema.yaml` и `rules.yaml`." % (domain, p["version"]), ""]
    if p["scope"]:
        L += ["## Границы области", "", p["scope"], ""]
    L += ["## Сущности и поля", ""]
    for e in p["entities"]:
        extra = []
        if e["is_result"]:
            extra.append("ключевая сущность")
        if e["parent"]:
            extra.append("родитель: %s" % e["parent"])
        L += ["### %s%s" % (e["name"], " (%s)" % ", ".join(extra) if extra else ""), "",
              "| Поле | Тип | Допустимые значения | По умолчанию | Связь | Описание |", "|---|---|---|---|---|---|"]
        for c in e["columns"]:
            L.append("| %s | %s | %s | %s | %s | %s |" % (_cell(c["name"] + (" (id)" if c.get("role") == "id" else "")), c["type"],
                                                       _cell(", ".join(map(str, c.get("vocab", [])))), _cell(c.get("default", "")),
                                                       _cell(c.get("ref", "")), _cell(c.get("description", ""))))
        L.append("")
    if p["metrics"]:
        L += ["## Метрики и единицы", "", "| Метрика | Единица | Вид | Пересчёт в единицу |", "|---|---|---|---|"]
        for m in p["metrics"]:
            conv = ", ".join("1 %s = %s %s" % (u, k, m["unit"]) for u, k in m["conversions"].items())
            L.append("| %s | %s | %s | %s |" % (_cell(m["name"]), _cell(m["unit"] or ""), _cell(m["kind"] or ""), _cell(conv)))
        L.append("")
    L += ["## Правила проверки", ""]
    if p["rules"]:
        L += ["| Правило | Тип | Уровень | Описание | Параметры и пороги |", "|---|---|---|---|---|"]
        for x in p["rules"]:
            L.append("| %s | %s | %s | %s | `%s` |" % (_cell(x["id"]), _cell(x["type"]), _cell(x["severity"]), _cell(x["description"]),
                                                       _cell(json.dumps(x["params"], ensure_ascii=False))))
    else:
        L.append("Правила не заданы.")
    L.append("")
    if p["compare"]["criteria"]:
        L += ["## Критерии сопоставимости результатов", "", "| Критерий | Путь | Режим | Параметр | Причина блокировки |", "|---|---|---|---|---|"]
        for c in p["compare"]["criteria"]:
            L.append("| %s | %s | %s | %s | %s |" % (_cell(c.get("name")), _cell(c.get("path")), _cell(c.get("mode")), _cell(c.get("param", "")), _cell(c.get("reason", ""))))
        L.append("")
    if p["eval"].get("match_fields"):
        L += ["## Оценка качества", "", "Сопоставление записей с эталоном по полям:", ""]
        for e, fl in p["eval"]["match_fields"].items():
            L.append("- %s: %s" % (e, ", ".join(fl)))
        if p["eval"].get("value_fields"):
            L.append("- числовые поля (допуск 1e-6 или 0,5 %%): %s" % ", ".join(p["eval"]["value_fields"]))
        L.append("")
    if p["extraction_hints"]:
        L += ["## Подсказки извлечения", ""] + ["- %s" % h for h in p["extraction_hints"]] + [""]
    return "\n".join(L)


def write_all():
    """Пересоздаёт docs/passport_<domain>.md для всех областей."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in config.list_domains():
        try:
            path = DOCS_DIR / ("passport_%s.md" % d)
            path.write_text(markdown(d), encoding="utf-8")
            out.append(str(path))
        except Exception as e:  # паспорт не должен ронять сервер
            print("passport", d, "failed:", e)
    return out
