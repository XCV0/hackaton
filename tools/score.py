"""Сверка results с эталоном. score.py <pred_dir> <gold_dir> <ID,ID,...>
Совпадение записи: (публикация, метрика, нормализованное значение) — без привязки к имени прибора
(идентификаторы произвольны). Считает precision/recall по основным метрикам (primary, без duplicate_of)."""
import sys, csv, pathlib, collections
pred, gold, ids = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3].split(",")
MAIN = {"PCE","Voc","Jsc","FF","Rs","Rsh","HI","Jsc_EQE","stabilized_PCE","T80","retained_PCE"}
def load(p, pid):
    f = p / "results.csv"
    if not f.exists(): f = p / pid / "results.csv"
    if not f.exists(): return None
    out = collections.Counter()
    for r in csv.DictReader(open(f, encoding="utf-8-sig")):
        if r["publication_id"] != pid or r["metric_name"] not in MAIN or r["duplicate_of"] or r["extraction_status"] != "extracted": continue
        try: v = round(float(r["normalized_value"] or r["metric_value"]), 3)
        except ValueError: continue
        out[(r["metric_name"], v, r["result_origin"])] += 1
    return out
tp = fp = fn = 0
for pid in ids:
    g, p = load(gold, pid), load(pred, pid)
    if p is None: print(pid, "нет предсказания"); continue
    t = sum((g & p).values()); a = sum(p.values()); b = sum(g.values())
    tp += t; fp += a - t; fn += b - t
    miss = [k for k in (g - p)][:8]
    print(f"{pid}: gold={b} pred={a} совпало={t} P={t/a if a else 0:.2f} R={t/b if b else 0:.2f}  пропущено: {miss}")
print(f"ИТОГО P={tp/(tp+fp) if tp+fp else 0:.2f} R={tp/(tp+fn) if tp+fn else 0:.2f}")
