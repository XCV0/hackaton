"""Общие фикстуры: временная БД, генерация мини-PDF без сторонних библиотек."""
import pathlib, sys, threading, pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db, config, llm, pipeline  # noqa: E402


def make_pdf(path, pages):
    """Минимальный валидный PDF (Helvetica, только латиница): pages — список текстов (строки через \\n)."""
    objs = []
    n = len(pages)
    kids = " ".join("%d 0 R" % (4 + 2 * i) for i in range(n))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append("<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n))
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        lines = []
        y = 780
        for ln in text.split("\n"):
            ln = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append("BT /F1 10 Tf 40 %d Td (%s) Tj ET" % (y, ln))
            y -= 14
        stream = "\n".join(lines)
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (5 + 2 * i))
        objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream.encode("latin-1")), stream))
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("latin-1")
    xref = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode()
    for o in offs:
        out += ("%010d 00000 n \n" % o).encode()
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)).encode()
    pathlib.Path(path).write_bytes(bytes(out))
    return str(path)


FILL = 2 * "The experimental section describes the preparation of thin films by spin coating and thermal annealing in a nitrogen glovebox. "


def page_text(*lines):
    """Страница с >=200 символами текста (слой есть) + заданные строки."""
    return "\n".join(list(lines) + [FILL[i:i + 90] for i in range(0, len(FILL), 90)])


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "data" / "app.db")
    monkeypatch.setattr(db, "_local", threading.local())
    db.init_db()
    config.seed_versions()
    monkeypatch.setattr(llm, "RETRY_SLEEP", 0)
    monkeypatch.setattr(pipeline, "_started", True)
    yield tmp_path
    pipeline.wait_idle(10)
    c = getattr(db._local, "c", None)
    if c is not None:
        c.close()
