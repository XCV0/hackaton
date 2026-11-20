"""OCR для сканов: tesseract + рендер страниц (pypdfium2 / PyMuPDF / pdf2image). Ничего не выдумываем: нет инструментов — понятная ошибка."""
import shutil, subprocess, tempfile, pathlib
from . import db

INSTALL_HINT = "Установите: brew install tesseract tesseract-lang"
LAYER_MIN_CHARS = 200


def _renderer():
    """Имя доступного рендерера страниц или None."""
    for name, mod in (("pypdfium2", "pypdfium2"), ("pymupdf", "fitz"), ("pdf2image", "pdf2image")):
        try:
            __import__(mod)
            return name
        except Exception:
            continue
    return None


def tesseract_langs():
    exe = shutil.which("tesseract")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=30).stdout.split()
    except Exception:
        return []
    return [l for l in ("eng", "rus") if l in out]


def ocr_available():
    return bool(shutil.which("tesseract")) and _renderer() is not None


def _render_page(pdf_path, idx, dpi=250):
    """Страница idx (с 0) -> PIL.Image."""
    r = _renderer()
    if r == "pypdfium2":
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            return doc[idx].render(scale=dpi / 72).to_pil()
        finally:
            doc.close()
    if r == "pymupdf":
        import fitz
        from PIL import Image
        import io
        doc = fitz.open(str(pdf_path))
        pix = doc[idx].get_pixmap(dpi=dpi)
        return Image.open(io.BytesIO(pix.tobytes("png")))
    from pdf2image import convert_from_path
    return convert_from_path(str(pdf_path), dpi=dpi, first_page=idx + 1, last_page=idx + 1)[0]


def _ocr_image(img, lang):
    exe = shutil.which("tesseract")
    with tempfile.TemporaryDirectory() as td:
        f = pathlib.Path(td) / "p.png"
        img.save(str(f))
        res = subprocess.run([exe, str(f), "stdout", "-l", lang], capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError("tesseract: " + (res.stderr or "ошибка")[:200])
    return res.stdout


def run_ocr(pub_id):
    """Распознаёт страницы без текстового слоя, пишет в pages (ocr=1), ставит статью в очередь заново."""
    from . import pipeline
    pub = db.q1("SELECT * FROM publications WHERE id=?", (pub_id,))
    if not pub:
        return {"ok": False, "error": "Публикация не найдена: %s" % pub_id}
    if not shutil.which("tesseract"):
        return {"ok": False, "error": "OCR недоступен: не найден tesseract. " + INSTALL_HINT}
    if _renderer() is None:
        return {"ok": False, "error": "OCR недоступен: нет библиотеки рендера PDF. Установите: .venv/bin/pip install pypdfium2 pillow"}
    langs = tesseract_langs()
    if not langs:
        return {"ok": False, "error": "У tesseract нет языковых пакетов eng/rus. " + INSTALL_HINT}
    lang = "+".join(langs)
    pdf = pub.get("pdf_path")
    if not pdf or not pathlib.Path(pdf).exists():
        return {"ok": False, "error": "Файл PDF не найден: %s" % pdf}
    n = pub.get("pages") or 0
    if not n:
        from .pipeline import _inspect_pdf
        n = _inspect_pdf(pdf)["pages"]
    have = {r["page"]: r for r in db.q("SELECT page, text, ocr FROM pages WHERE pub_id=?", (pub_id,))}
    todo = [p for p in range(1, n + 1) if len(((have.get(p) or {}).get("text") or "").strip()) < LAYER_MIN_CHARS]
    db.ex("UPDATE publications SET ocr_status='running', message=?, updated=? WHERE id=?", ("OCR: страниц %d" % len(todo), db.now(), pub_id))
    done = 0
    try:
        for p in todo:
            text = _ocr_image(_render_page(pdf, p - 1), lang).strip()
            db.ex("INSERT INTO pages(pub_id,page,text,ocr) VALUES(?,?,?,1) ON CONFLICT(pub_id,page) DO UPDATE SET text=excluded.text, ocr=1", (pub_id, p, text))
            done += 1
            if done % 5 == 0:
                db.ex("UPDATE publications SET message=?, updated=? WHERE id=?", ("OCR: %d из %d страниц" % (done, len(todo)), db.now(), pub_id))
    except Exception as e:
        db.ex("UPDATE publications SET ocr_status='failed', status='error', message=?, updated=? WHERE id=?", ("Ошибка OCR: %s" % e, db.now(), pub_id))
        return {"ok": False, "error": "Ошибка OCR на странице %d: %s" % (todo[done] if done < len(todo) else 0, e)}
    rows = db.q("SELECT text FROM pages WHERE pub_id=?", (pub_id,))
    text_pages = sum(1 for r in rows if len((r["text"] or "").strip()) >= LAYER_MIN_CHARS)
    if text_pages < max(1, int(n * (1 - pipeline.NEEDS_OCR_SHARE))):
        db.ex("UPDATE publications SET ocr_status='failed', status='error', text_pages=?, message=?, updated=? WHERE id=?",
              (text_pages, "OCR не дал достаточно текста (%d из %d страниц)" % (text_pages, n), db.now(), pub_id))
        return {"ok": False, "error": "OCR не дал достаточно текста: %d из %d страниц" % (text_pages, n)}
    db.ex("UPDATE publications SET ocr_status='done', needs_ocr=0, text_pages=?, status='registered', message=?, updated=? WHERE id=?",
          (text_pages, "OCR выполнен (%d страниц)" % done, db.now(), pub_id))
    pipeline._set_stage(pub_id, "parse", "done", "текст получен через OCR (%d страниц)" % done)
    pipeline.enqueue(pub_id)
    return {"ok": True, "pages_ocr": done, "lang": lang, "pub_id": pub_id}
