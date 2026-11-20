"""Заливка уже извлечённых dev-статей в хранилище: python -m app.seed <csv_dir> <pdf_dir>"""
import sys, csv, pathlib, time
from . import store
src, pdfdir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
store.init()
rd = lambda t: list(csv.DictReader(open(src / f"{t}.csv", encoding="utf-8-sig", newline="")))
tb = {t: rd(t) for t in store.TABLES}
ids = sorted({p["publication_id"] for p in tb["publications"]})
for pid in ids:
    store.pub_dir(pid).mkdir(parents=True, exist_ok=True)
    store.write_tables(pid, {t: [r for r in tb[t] if r["publication_id"] == pid] for t in store.TABLES})
    pdf = pdfdir / f"{pid}.pdf"
    store.write_meta(pid, {"id": pid, "filename": f"{pid}.pdf", "status": "done", "progress": 1.0, "source": "dev-seed", "message": "Импортировано из пилотного извлечения",
                           "pdf_path": str(pdf) if pdf.exists() else "", "uploaded": time.strftime("%Y-%m-%d %H:%M:%S"), "model": "Claude (агенты)"})
print("seeded", len(ids))
