"""Физические проверки области 1: results/materials/devices -> issues.csv.
Использование: validate.py <dir_with_csv> [out_csv]   (по умолчанию out/issues_auto.csv; входную папку не трогает)
Правила: pce_consistency (PCE vs Voc*Jsc*FF/Pin, порог 3% отн.), voc_limit (Voc<Eg/q),
range (Jsc/FF/PCE), eqe_consistency (Jsc vs Jsc_EQE >5%). Исходные записи не изменяются."""
import sys, csv, pathlib, collections

d = pathlib.Path(sys.argv[1])
rd = lambda n: list(csv.DictReader(open(d / n, encoding="utf-8-sig"))) if (d / n).exists() else []
res, mats, devs, meas = rd("results.csv"), rd("materials.csv"), rd("devices.csv"), rd("measurements.csv")
num = lambda x: float(x) if x not in ("", None) else None
mat_eg = {(m["publication_id"], m["material_id"]): num(m["bandgap_value"]) for m in mats}

# группировка: (публикация, прибор, измерение, агрегация) -> метрики; дубликаты пропускаем
g = collections.defaultdict(dict)
for r in res:
    if r["duplicate_of"] or r["result_origin"] == "cited" or r["extraction_status"] != "extracted":
        continue
    v = num(r["normalized_value"] or r["metric_value"])
    if v is None:
        continue
    g[(r["publication_id"], r["device_id"], r["measurement_id"], r["aggregation"], r["time_point"])][r["metric_name"]] = (v, r)

issues, cnt = [], collections.Counter()
def add(pub, recs, name, desc, ref):
    cnt[pub] += 1
    issues.append(dict(publication_id=pub, issue_id=f"A{cnt[pub]}", record_ids=";".join(r["result_id"] for r in recs),
                       check_name=name, description=desc, source_page=ref["source_page"], source_fragment=ref["source_fragment"]))

for (pub, dev, ms, agg, tp), m in g.items():
    if tp: continue
    P = lambda k: m[k][0] if k in m else None
    R = lambda k: m[k][1]
    pce, voc, jsc, ff = P("PCE"), P("Voc"), P("Jsc"), P("FF")
    if None not in (pce, voc, jsc, ff) and pce > 0:
        calc = voc * jsc * ff / 100.0   # при Pin=100 мВт/см2, PCE в %
        if abs(calc - pce) / pce > 0.03:
            add(pub, [R(k) for k in ("PCE", "Voc", "Jsc", "FF")], "jv_consistency",
                f"{dev}: PCE={pce}% , расчёт Voc*Jsc*FF={calc:.2f}% (расхождение {abs(calc-pce)/pce*100:.1f}% > 3%)", R("PCE"))
    if ff is not None and not (10 <= ff <= 90):
        add(pub, [R("FF")], "range", f"{dev}: FF={ff}% вне 10-90%", R("FF"))
    if jsc is not None and not (0 < jsc < 50):
        add(pub, [R("Jsc")], "range", f"{dev}: Jsc={jsc} мА/см2 вне 0-50", R("Jsc"))
    if pce is not None and not (0 < pce < 35):
        add(pub, [R("PCE")], "range", f"{dev}: PCE={pce}% вне 0-35", R("PCE"))
    if voc is not None:
        mid = next((x["absorber_material_id"] for x in devs if x["publication_id"] == pub and x["device_id"] == dev), None)
        eg = mat_eg.get((pub, mid))
        if eg and voc >= eg:
            add(pub, [R("Voc")], "voc_limit", f"{dev}: Voc={voc} В >= Eg/q={eg} эВ", R("Voc"))
    je = P("Jsc_EQE")
    if jsc and je and abs(je - jsc) / jsc > 0.05:
        add(pub, [R("Jsc"), R("Jsc_EQE")], "eqe_consistency", f"{dev}: Jsc(JV)={jsc}, Jsc(EQE)={je} расходятся >5%", R("Jsc"))

out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "out/issues_auto.csv"); out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["publication_id","issue_id","record_ids","check_name","description","source_page","source_fragment"])
    w.writeheader(); w.writerows(issues)
print(f"{len(issues)} замечаний -> {out}", dict(collections.Counter(i["check_name"] for i in issues)))
