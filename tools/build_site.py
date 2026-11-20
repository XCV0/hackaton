"""Данные для сайта: build_site.py <csv_dir> <site_dir>. Пишет site/data.js (window.DATA) и копирует паспорт."""
import sys, csv, json, pathlib
src, site = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
rd = lambda n: list(csv.DictReader(open(src / f"{n}.csv", encoding="utf-8-sig", newline="")))
tables = {n: rd(n) for n in ("publications", "materials", "devices", "layers", "measurements", "results", "issues")}
drop = {"results": {"cited_publication_id"}}  # оставляем всё нужное, убираем пустые ключи ради размера
out = {}
for n, rows in tables.items():
    out[n] = [{k: v for k, v in r.items() if v not in ("", None)} for r in rows]
passport = json.load(open("docs/passport_area1.json", encoding="utf-8"))
(site / "data.js").write_text("window.DATA=" + json.dumps({"tables": out, "passport": passport}, ensure_ascii=False, separators=(",", ":")) + ";", encoding="utf-8")
print({k: len(v) for k, v in out.items()}, (site / "data.js").stat().st_size // 1024, "КБ")
