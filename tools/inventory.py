"""Инвентаризация корпуса: python tools/inventory.py <domain_name>=<dir> ... -> out/inventory.json
Хеш SHA-256, число страниц, текстовый слой, DOI (с первых страниц), дубли по хешу и DOI."""
import sys, json, hashlib, re, pathlib, collections
from pypdf import PdfReader
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", re.I)
rep = {"domains": {}, "duplicates": {"hash": [], "doi": []}}
seen_h, seen_d = {}, {}
for arg in sys.argv[1:]:
    name, d = arg.split("=", 1)
    files = []
    for pdf in sorted(pathlib.Path(d).rglob("*.pdf")):
        row = {"file": str(pdf), "size": pdf.stat().st_size, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
        try:
            r = PdfReader(str(pdf)); row["pages"] = len(r.pages)
            txt = [(r.pages[i].extract_text() or "") for i in range(min(len(r.pages), 3))]
            allp = sum(1 for p in r.pages if len((p.extract_text() or "").strip()) >= 200)
            row["text_pages"] = allp; row["needs_ocr"] = allp < max(1, int(len(r.pages) * 0.6))
            m = DOI.search(" ".join(txt)); row["doi"] = m.group(1).rstrip(".)") .lower() if m else None
            row["readable"] = True
        except Exception as e:
            row.update(readable=False, error=str(e)[:120])
        h, doi = row["sha256"], row.get("doi")
        if h in seen_h: rep["duplicates"]["hash"].append([seen_h[h], row["file"]])
        else: seen_h[h] = row["file"]
        if doi:
            if doi in seen_d and seen_d[doi] != row["file"]: rep["duplicates"]["doi"].append([seen_d[doi], row["file"], doi])
            else: seen_d[doi] = row["file"]
        files.append(row)
    rep["domains"][name] = {"dir": d, "files": files}
out = pathlib.Path("out/inventory.json"); out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
for n, v in rep["domains"].items():
    f = v["files"]
    print(f"{n}: файлов {len(f)}, нечитаемых {sum(not x['readable'] for x in f)}, нужен OCR {sum(x.get('needs_ocr', False) for x in f)}, с DOI {sum(bool(x.get('doi')) for x in f)}, страниц {sum(x.get('pages', 0) for x in f)}")
print("дубли по хешу:", len(rep["duplicates"]["hash"]), "| по DOI:", len(rep["duplicates"]["doi"]))
