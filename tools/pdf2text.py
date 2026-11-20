"""PDF -> постраничный текст. Использование: pdf2text.py <dir_with_pdfs> <out_dir>
Страницы без текстового слоя помечаются в _scan_report.txt (нужен OCR / чтение изображений)."""
import sys, pathlib, re
from pypdf import PdfReader

src, dst = map(pathlib.Path, sys.argv[1:3])
report = []
for pdf in sorted(src.glob("*.pdf")):
    out = dst / pdf.stem
    out.mkdir(parents=True, exist_ok=True)
    r = PdfReader(str(pdf))
    empty = []
    for i, p in enumerate(r.pages, 1):
        t = re.sub("[\ud800-\udfff]", "", p.extract_text() or "").strip()
        (out / f"p{i:02d}.txt").write_text(t, encoding="utf-8")
        if len(t) < 200:
            empty.append(i)
    report.append(f"{pdf.stem}\tpages={len(r.pages)}\tno_text_pages={len(empty)}")
(dst / "_scan_report.txt").write_text("\n".join(report), encoding="utf-8")
print("\n".join(report))
