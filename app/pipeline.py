"""Конвейер PDF -> записи: register, parse, segment, extract, verify, normalize, validate, publish.
Предметной области в коде нет: промпты, сущности, словари и связи берутся из config/domains/<domain>/schema.yaml."""
import hashlib, json, pathlib, re, shutil, threading, traceback
from concurrent.futures import ThreadPoolExecutor
from pypdf import PdfReader

from . import db, config, llm, verify as vf
from .llm import LLMError  # noqa: F401  (совместимость: pipeline.LLMError)

STAGES = ["register", "parse", "segment", "extract", "verify", "normalize", "validate", "publish"]
NEEDS_OCR_SHARE = 0.4        # доля страниц без слоя, выше которой нужен OCR
LAYER_MIN_CHARS = 200
MAX_TABLE_PAGES = 80         # pdfplumber-таблицы дороги: на длинных PDF не извлекаем
MAX_STRUCT_WINDOWS = 3
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", re.I)
# служебные поля результата: короткий ключ в ответе модели -> колонка (добавляются, только если колонка есть в схеме)
ALIAS = {"page": "source_page", "section": "source_section", "table": "source_table", "figure": "source_figure",
         "fragment": "source_fragment", "origin": "result_origin", "cited_ref": "cited_publication_id"}
ALIAS_HINT = {
    "source_page": "целое: номер страницы из маркера PAGE",
    "source_section": "раздел статьи, если виден",
    "source_table": "например Table 1, если значение из таблицы",
    "source_figure": "например Fig. 2, если значение с рисунка",
    "source_fragment": "ДОСЛОВНАЯ цитата со страницы, до 150 символов, содержащая это значение; копируй символ в символ, не перефразируй",
    "cited_publication_id": "для cited: ссылка на цитируемую работу как в тексте, например [12]; иначе пусто",
}
# вычисляемые кодом (модуль rules) колонки; не спрашиваются у модели, если в сущности есть raw_value
COMPUTED = ("normalized_value", "normalized_unit", "metric_value", "metric_unit")
ALWAYS_CODE = ("publication_id", "duplicate_of")

_started = False
_locks = {}
_locks_guard = threading.Lock()
_pool = {"ex": None, "n": 0}
_inflight = set()
_inflight_guard = threading.Lock()


class StageError(Exception):
    pass


class StageStop(Exception):
    """Штатная остановка конвейера (needs_ocr / excluded): не ошибка."""
    def __init__(self, pub_status, message):
        super().__init__(message)
        self.pub_status, self.message = pub_status, message


# ====================================================================== утилиты БД
def _data_dir():
    return pathlib.Path(db.DB_PATH).parent


def _set_pub(pub_id, **kw):
    kw["updated"] = db.now()
    db.ex("UPDATE publications SET %s WHERE id=?" % ",".join("%s=?" % k for k in kw), (*kw.values(), pub_id))


def _set_stage(pub_id, stage, status, message="", new_attempt=False):
    t = db.now()
    row = db.q1("SELECT id, attempt FROM stage_runs WHERE pub_id=? AND stage=?", (pub_id, stage))
    if not row:
        db.ex("INSERT INTO stage_runs(pub_id,stage,status,attempt,message,started,finished) VALUES(?,?,?,?,?,?,?)",
              (pub_id, stage, status, 1, message, t, t if status in ("done", "error") else None))
        return
    att = row["attempt"] + 1 if new_attempt else row["attempt"]
    if status == "running":
        db.ex("UPDATE stage_runs SET status=?,attempt=?,message=?,started=?,finished=NULL WHERE id=?", (status, att, message, t, row["id"]))
    else:
        db.ex("UPDATE stage_runs SET status=?,attempt=?,message=?,finished=? WHERE id=?",
              (status, att, message, t if status in ("done", "error") else None, row["id"]))


def recover_interrupted():
    """Зависшие после перезапуска процесса: running-стадии и статьи в processing -> error."""
    msg = "прервано перезапуском"
    db.ex("UPDATE stage_runs SET status='error', message=?, finished=? WHERE status='running'", (msg, db.now()))
    db.ex("UPDATE publications SET status='error', message=?, updated=? WHERE status='processing'", (msg, db.now()))
    db.ex("UPDATE publications SET ocr_status='failed', message=? WHERE ocr_status='running'", (msg,))


def _ensure_started():
    global _started
    if not _started:
        _started = True
        try:
            recover_interrupted()
        except Exception:
            pass


def _lock(pub_id):
    with _locks_guard:
        return _locks.setdefault(pub_id, threading.Lock())


def _cfg(pub):
    return config.load_domain(pub["domain"])


def _ents(cfg):
    return cfg["schema"]["entities"]


def _id_col(cfg, ent):
    return _ents(cfg)[ent]["id"]


def _result_ent(cfg):
    return cfg["schema"].get("result_entity", "results")


def _pub_ent(cfg):
    return cfg["schema"].get("publication_entity", "publications")


def _struct_ents(cfg):
    return [n for n, e in _ents(cfg).items() if e.get("structure")]


def _pages(pub_id):
    rows = db.q("SELECT page, text FROM pages WHERE pub_id=? ORDER BY page", (pub_id,))
    n = max([r["page"] for r in rows], default=0)
    out = [""] * n
    for r in rows:
        out[r["page"] - 1] = r["text"] or ""
    return out


# ====================================================================== PDF
def _clean(t):
    return re.sub("[\ud800-\udfff\x00]", "", t or "").strip()


def _extract_pages(path):
    """Текст по страницам: pypdf, при провале — pdfplumber."""
    pages = None
    try:
        r = PdfReader(str(path))
        pages = []
        for p in r.pages:
            try:
                pages.append(_clean(p.extract_text()))
            except Exception:
                pages.append("")
    except Exception:
        pages = None
    if pages is None or (pages and not any(pages)):
        try:
            import pdfplumber
            with pdfplumber.open(str(path)) as pdf:
                alt = [_clean(p.extract_text()) for p in pdf.pages]
            if pages is None or any(alt):
                pages = alt
        except Exception:
            if pages is None:
                raise
    return pages


def _table_blocks(path, n_pages):
    """{page: '[TABLE]...'} из pdfplumber; ошибки тихо игнорируются (текст страницы остаётся)."""
    out = {}
    if n_pages > MAX_TABLE_PAGES:
        return out
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for i, p in enumerate(pdf.pages, 1):
                try:
                    tabs = p.extract_tables()
                except Exception:
                    continue
                blocks = []
                for t in tabs or []:
                    rows = [" | ".join(_clean(c).replace("\n", " ") for c in row if c is not None) for row in t if row]
                    rows = [r for r in rows if r.strip(" |")]
                    if len(rows) >= 2:
                        blocks.append("[TABLE]\n" + "\n".join(rows) + "\n[/TABLE]")
                if blocks:
                    out[i] = "\n".join(blocks)
    except Exception:
        pass
    return out


def _find_doi(text):
    m = DOI_RE.search(text or "")
    return m.group(1).rstrip(".)]").lower() if m else ""


def _inspect_pdf(path):
    """Лёгкий осмотр: страницы, число страниц с текстовым слоем, DOI. readable=False при нечитаемом файле."""
    try:
        r = PdfReader(str(path))
        n = len(r.pages)
        texts = []
        for p in r.pages:
            try:
                texts.append(_clean(p.extract_text()))
            except Exception:
                texts.append("")
        if n == 0:
            return {"readable": False, "error": "PDF без страниц", "pages": 0, "text_pages": 0, "doi": ""}
        tp = sum(1 for t in texts if len(t) >= LAYER_MIN_CHARS)
        return {"readable": True, "pages": n, "text_pages": tp, "doi": _find_doi(" ".join(texts[:3])),
                "needs_ocr": (n - tp) / n > NEEDS_OCR_SHARE}
    except Exception as e:
        return {"readable": False, "error": str(e)[:150] or type(e).__name__, "pages": 0, "text_pages": 0, "doi": ""}


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ====================================================================== регистрация и инвентаризация
def _new_pub_id(domain, stem):
    stem = re.sub(r"[^\w.\-]+", "_", stem, flags=re.U).strip("._") or "pub"
    for cand in (stem, "%s-%s" % (stem, domain)):
        if not db.q1("SELECT 1 FROM publications WHERE id=?", (cand,)):
            return cand
    i = 2
    while db.q1("SELECT 1 FROM publications WHERE id=?", ("%s-%s-%d" % (stem, domain, i),)):
        i += 1
    return "%s-%s-%d" % (stem, domain, i)


def register_pdf(domain, path=None, data=None, filename=None, split="", pub_id=None):
    """Запоминает PDF (path — по месту, data — байты, сохраняются в data/uploads/<domain>/) и создаёт publications. Возвращает pub_id."""
    if data is not None:
        fn = re.sub(r"[^\w.\-]+", "_", filename or "upload.pdf", flags=re.U)
        d = _data_dir() / "uploads" / domain
        d.mkdir(parents=True, exist_ok=True)
        target = d / fn
        k = 1
        while target.exists():
            target = d / ("%s_%d%s" % (target.stem.rsplit("_", 1)[0] if k > 1 else target.stem, k, ".pdf"))
            k += 1
        target.write_bytes(data)
        path = str(target)
    if not path:
        raise ValueError("Нужен path или data")
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError("Файл не найден: %s" % path)
    filename = filename or p.name
    sha = _sha(p)
    t = db.now()
    if pub_id and db.q1("SELECT 1 FROM publications WHERE id=?", (pub_id,)):
        db.ex("UPDATE publications SET domain=?,filename=?,pdf_path=?,sha256=?,split=?,status='registered',message='',updated=? WHERE id=?",
              (domain, filename, str(p), sha, split, t, pub_id))
        return pub_id
    pub_id = pub_id or _new_pub_id(domain, p.stem)
    db.ex("INSERT INTO publications(id,domain,filename,pdf_path,sha256,status,split,message,created,updated) VALUES(?,?,?,?,?,'registered',?,'',?,?)",
          (pub_id, domain, filename, str(p), sha, split, t, t))
    return pub_id


def scan_source(domain, path, split=""):
    """Рекурсивный обход папки: новые PDF регистрируются (известные по sha256 пропускаются), дубли и нечитаемые идут в отчёт."""
    root = pathlib.Path(path)
    rep = {"added": [], "skipped": [], "duplicates": [], "unreadable": []}
    if not root.exists():
        raise FileNotFoundError("Папка не найдена: %s" % path)
    files = [root] if root.is_file() else sorted(f for f in root.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf" and not f.name.startswith("."))
    seen = {}
    for f in files:
        sha = _sha(f)
        known = db.q1("SELECT id, pdf_path FROM publications WHERE domain=? AND sha256=? ORDER BY rowid", (domain, sha))
        if known:
            if known["pdf_path"] == str(f):
                rep["skipped"].append({"path": str(f), "pub_id": known["id"]})
            else:
                rep["duplicates"].append({"path": str(f), "duplicate_of": known["id"], "reason": "тот же sha256"})
            continue
        if sha in seen:
            rep["duplicates"].append({"path": str(f), "duplicate_of": seen[sha], "reason": "тот же sha256 в этой папке"})
            continue
        info = _inspect_pdf(f)
        if not info["readable"]:
            rep["unreadable"].append({"path": str(f), "error": info.get("error", "")})
            continue
        pid = register_pdf(domain, path=str(f), split=split)
        _set_pub(pid, pages=info["pages"], text_pages=info["text_pages"], doi=info["doi"])
        seen[sha] = pid
        rep["added"].append(pid)
    db.ex("UPDATE corpus_sources SET last_scan=? WHERE domain=? AND path=?", (db.now(), domain, str(path)))
    return rep


def inventory_report(domain=None):
    """JSON-отчёт по зарегистрированным публикациям: области -> папки, слой, needs_ocr, DOI, дубли по хешу и DOI; + текстовая сводка."""
    rows = db.q("SELECT * FROM publications" + (" WHERE domain=?" if domain else "") + " ORDER BY domain, rowid", (domain,) if domain else ())

    def agg(items):
        pages = sum(r["pages"] or 0 for r in items)
        tp = sum(r["text_pages"] or 0 for r in items)
        return {"files": len(items), "pages": pages, "text_pages": tp, "layer_share": round(tp / pages, 3) if pages else None,
                "needs_ocr": sum(1 for r in items if r["needs_ocr"] or r["status"] == "needs_ocr" or
                                 (r["pages"] and r["text_pages"] is not None and (r["pages"] - (r["text_pages"] or 0)) / r["pages"] > NEEDS_OCR_SHARE)),
                "with_doi": sum(1 for r in items if r["doi"]),
                "unreadable": sum(1 for r in items if r["status"] == "error" and not r["pages"])}

    rep = {"domains": {}, "duplicates": {"hash": [], "doi": []}}
    for d in sorted({r["domain"] for r in rows}):
        items = [r for r in rows if r["domain"] == d]
        folders = {}
        for r in items:
            folders.setdefault(str(pathlib.Path(r["pdf_path"]).parent) if r["pdf_path"] else "(без пути)", []).append(r)
        splits = {}
        for r in items:
            splits.setdefault(r["split"] or "", []).append(r)
        rep["domains"][d] = {**agg(items), "folders": {k: agg(v) for k, v in folders.items()}, "splits": {k or "(не задан)": agg(v) for k, v in splits.items()},
                             "by_status": {s: sum(1 for r in items if r["status"] == s) for s in sorted({r["status"] for r in items})}}
        for key, out in (("sha256", "hash"), ("doi", "doi")):
            g = {}
            for r in items:
                if r[key]:
                    g.setdefault(r[key], []).append(r["id"])
            for k, ids in g.items():
                if len(ids) > 1:
                    rep["duplicates"][out].append({"domain": d, "key": k, "pub_ids": ids})
    lines = []
    for d, v in rep["domains"].items():
        lines.append("%s: файлов %d, страниц %d, слой %s, нужен OCR %d, с DOI %d, нечитаемых %d" % (
            d, v["files"], v["pages"], "%d%%" % round(100 * v["layer_share"]) if v["layer_share"] is not None else "н/д", v["needs_ocr"], v["with_doi"], v["unreadable"]))
        for k, f in v["folders"].items():
            lines.append("  %s: %d файлов, %d стр." % (k, f["files"], f["pages"]))
    lines.append("дубли: по хешу %d, по DOI %d" % (len(rep["duplicates"]["hash"]), len(rep["duplicates"]["doi"])))
    rep["summary"] = "\n".join(lines)
    return rep


# ====================================================================== стадии: register / parse / segment
def _st_register(c):
    pub = c["pub"]
    path = pub["pdf_path"]
    if not path or not pathlib.Path(path).exists():
        raise StageError("Файл PDF не найден: %s" % path)
    sha = pub["sha256"] or _sha(path)
    info = _inspect_pdf(path)
    if not info["readable"]:
        raise StageError("PDF нечитаем: %s" % info.get("error", ""))
    doi = pub["doi"] or info["doi"]
    _set_pub(pub["id"], sha256=sha, pages=info["pages"], doi=doi)
    dup = db.q1("SELECT id FROM publications WHERE domain=? AND id!=? AND rowid < (SELECT rowid FROM publications WHERE id=?) "
                "AND NOT (status='error' AND message LIKE 'дубликат%') AND (sha256=? OR (?!='' AND doi=?)) ORDER BY rowid",
                (pub["domain"], pub["id"], pub["id"], sha, doi, doi))
    if dup:
        raise StageError("дубликат %s" % dup["id"])
    return "%d стр., DOI %s" % (info["pages"], doi or "не найден")


def _st_parse(c):
    pub = c["pub"]
    pid = pub["id"]
    pages = _extract_pages(pub["pdf_path"])
    if not pages:
        raise StageError("PDF без страниц")
    keep = {r["page"] for r in db.q("SELECT page FROM pages WHERE pub_id=? AND ocr=1", (pid,))} if pub["ocr_status"] == "done" else set()
    tables = _table_blocks(pub["pdf_path"], len(pages))
    with db.tx() as cn:
        cn.execute("DELETE FROM pages WHERE pub_id=? AND ocr=0", (pid,))
        for i, t in enumerate(pages, 1):
            if i in keep:
                continue
            if i in tables:
                t = (t + "\n" if t else "") + tables[i]
            cn.execute("INSERT OR REPLACE INTO pages(pub_id,page,text,ocr) VALUES(?,?,?,0)", (pid, i, t))
    rows = db.q("SELECT page, text FROM pages WHERE pub_id=?", (pid,))
    tp = sum(1 for r in rows if len((r["text"] or "").strip()) >= LAYER_MIN_CHARS)
    n = len(pages)
    _set_pub(pid, pages=n, text_pages=tp)
    if (n - tp) / n > NEEDS_OCR_SHARE and pub["ocr_status"] != "done":
        _set_pub(pid, needs_ocr=1, status="needs_ocr", ocr_status="queued",
                 message="Нужен OCR: текстовый слой есть на %d из %d страниц" % (tp, n))
        raise StageStop("needs_ocr", "нужен OCR: слой на %d из %d страниц" % (tp, n))
    _set_pub(pid, needs_ocr=0)
    return "%d стр., со слоем %d, таблиц-блоков %d" % (n, tp, len(tables))


_HEAD_WORDS = r"abstract|introduction|background|methods?|materials and methods|experimental(?: section| details)?|results?|results and discussion|discussion|conclusions?|summary|acknowledg\w+|supplementary\w*|аннотация|введение|материалы и методы|методы|результаты|результаты и обсуждение|обсуждение|заключение|выводы"
_HEAD_RE = re.compile(r"^\s*(?:(?:\d{1,2}(?:\.\d{1,2}){0,2}\.?|[IVX]{1,5}\.)\s+)?(%s)\s*[.:]?\s*$" % _HEAD_WORDS, re.I)
_NUM_HEAD_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2}\.?)\s+([A-ZА-ЯЁ][^\n]{2,90})$")
_REF_RE = re.compile(r"^\s*(?:\d+\.?\s+)?(references?|bibliography|literature cited|литература|список литературы|references and notes)\s*:?\s*$", re.I | re.M)


def _heading(line):
    s = line.strip()
    if not s or len(s) > 100:
        return None
    if _HEAD_RE.match(s):
        return s
    m = _NUM_HEAD_RE.match(s)
    if m and not re.search(r"[.!?]$", s) and len(s.split()) <= 12:
        return s
    if s.isupper() and 4 <= len(s) <= 60 and re.search(r"[A-ZА-ЯЁ]{4}", s):
        return s
    return None


def segment_pages(pages, chunk_chars):
    """-> {'cut_page','sections','chunks':[{'p0','p1','text'}]}; список литературы отрезается."""
    n = len(pages)
    cut_page, cut_pos = n, None
    for i in range(n // 2, n):
        m = _REF_RE.search(pages[i])
        if m:
            cut_page, cut_pos = i + 1, m.start()
            break
    use = list(pages[:cut_page])
    if cut_pos is not None:
        use[-1] = use[-1][:cut_pos]
    sections = []
    for i, t in enumerate(use, 1):
        for line in t.splitlines():
            h = _heading(line)
            if h:
                sections.append({"title": h, "page": i})
    blocks = []
    for i, t in enumerate(use, 1):
        if not t.strip():
            continue
        head = "=== PAGE %d ===\n" % i
        if len(t) + len(head) <= chunk_chars:
            blocks.append((i, head + t))
        else:
            piece, k = "", 0
            for line in t.splitlines(keepends=True):
                if piece and len(piece) + len(line) > chunk_chars - 40:
                    blocks.append((i, head.replace(" ===", " (часть %d) ===" % (k + 1)) + piece)); piece, k = "", k + 1
                piece += line
            if piece:
                blocks.append((i, head.replace(" ===", " (часть %d) ===" % (k + 1)) + piece))
    chunks, cur, size = [], [], 0
    for pg, txt in blocks:
        if cur and size + len(txt) > chunk_chars:
            chunks.append(cur); cur, size = [], 0
        cur.append((pg, txt)); size += len(txt) + 1
    if cur:
        chunks.append(cur)
    return {"cut_page": cut_page, "sections": sections,
            "chunks": [{"p0": ch[0][0], "p1": ch[-1][0], "text": "\n".join(t for _, t in ch)} for ch in chunks]}


def _seg_path(pub_id):
    return _data_dir() / "cache" / pub_id / "segments.json"


def _chunk_chars(cfg):
    return int((cfg["schema"].get("extraction") or {}).get("chunk_chars", 24000))


def _st_segment(c):
    pages = _pages(c["pub"]["id"])
    if not any(p.strip() for p in pages):
        raise StageError("нет текста для разбиения: parse не дал страниц")
    seg = segment_pages(pages, _chunk_chars(c["cfg"]))
    f = _seg_path(c["pub"]["id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(seg, ensure_ascii=False), encoding="utf-8")
    return "разделов %d, кусков %d, литература с стр. %d" % (len(seg["sections"]), len(seg["chunks"]), seg["cut_page"] + 1 if seg["cut_page"] < len(pages) else 0)


# ====================================================================== промпты из схемы
def _vocab(cfg, ent, col):
    f = (_ents(cfg)[ent].get("fields") or {}).get(col) or {}
    return f.get("vocab") if f.get("type") == "enum" else None


def _col_spec(cfg, ent, col):
    e = _ents(cfg)[ent]
    f = (e.get("fields") or {}).get(col) or {}
    refs = e.get("refs") or {}
    if col in refs:
        return "id (поле id) записи «%s» из этой же публикации; одно значение; пусто, если связи нет" % refs[col]
    if col in ALIAS_HINT:
        spec = ALIAS_HINT[col]
    else:
        t = f.get("type", "string")
        if t == "enum":
            spec = "|".join(str(v) for v in f.get("vocab", [])) + "| (пусто, если в статье не сказано)"
        elif t in ("number", "float"):
            spec = "число (строкой, как в статье)"
        elif t == "int":
            spec = "целое число"
        elif t == "bool":
            spec = "true|false|"
        else:
            spec = "строка (пусто, если не указано)"
        if f.get("description"):
            spec += "; " + f["description"]
    if f.get("default") not in (None, ""):
        spec += "; по умолчанию %s" % f["default"]
    if col == "result_origin" and not f.get("vocab"):
        spec = "primary|cited"
    return spec


def _prompt_key(cfg, ent, col):
    inv = {v: k for k, v in ALIAS.items()}
    return inv.get(col, col)


def _fmt_example(pairs):
    return "{" + ", ".join('"%s": "%s"' % (k, v.replace('"', "'")) for k, v in pairs) + "}"


def _rid_prefix(cfg, ent):
    return str(_ents(cfg)[ent].get("rid_prefix") or ent[:1].upper())


def build_system(cfg):
    s = cfg["schema"]
    hints = (s.get("extraction") or {}).get("hints") or []
    txt = ("Ты извлекаешь структурированные данные из научных статей. Предметная область: %s. Что относится к области: %s "
           "Ничего не выдумывай и не достраивай: если значения нет в тексте статьи — пустая строка. Значения копируй так, как они написаны. "
           "Отвечай только одним JSON-объектом." % (s.get("title", cfg["domain"]), s.get("scope", "")))
    if hints:
        txt += "\nПравила области:\n" + "\n".join("- " + h for h in hints)
    return txt


def _struct_template(cfg, ent):
    e = _ents(cfg)[ent]
    idc = e["id"]
    pairs = [("id", "короткий id записи, уникальный в этой статье, например %s1" % _rid_prefix(cfg, ent))]
    for col in e["columns"]:
        if col in ("publication_id", idc):
            continue
        pairs.append((col, _col_spec(cfg, ent, col)))
    return _fmt_example(pairs)


def _pub_fields(cfg):
    pe = _pub_ent(cfg)
    return [c for c in _ents(cfg)[pe]["columns"] if c != _ents(cfg)[pe]["id"]]


def build_structure_prompt(cfg, text, prior=None):
    pe = _pub_ent(cfg)
    pf = _pub_fields(cfg)
    head = ", ".join('"%s": "%s"' % (c, _col_spec(cfg, pe, c).replace('"', "'")) for c in pf)
    ents = _struct_ents(cfg)
    body = ",\n".join(' "%s": [%s]' % (n, _struct_template(cfg, n)) for n in ents)
    if prior:
        intro = ("Статья ниже разбита по страницам (маркеры === PAGE n ===). Это ПРОДОЛЖЕНИЕ статьи. Уже извлечено:\n%s\n"
                 "Верни только НОВЫЕ записи, которых ещё нет (id продолжай, не повторяй существующие); ссылки на уже существующие id допустимы." % prior)
    else:
        intro = "Статья ниже разбита по страницам (маркеры === PAGE n ===)."
    return ("%s\nВерни JSON:\n{%s,\n%s}\nПравила: если статья вне области (in_scope=false) — укажи причину в exclusion_reason, остальные массивы верни пустыми. "
            "Связи между записями задавай через поле id записи. Каждая запись — отдельный объект.\n\nТЕКСТ СТАТЬИ:\n%s" % (intro, head, body, text))


def _result_cols(cfg):
    ent = _result_ent(cfg)
    e = _ents(cfg)[ent]
    cols = e["columns"]
    comp = set(((cfg["schema"].get("extraction") or {}).get("computed_fields")) or COMPUTED) if "raw_value" in cols else set()
    return [c for c in cols if c != e["id"] and c not in ALWAYS_CODE and c not in comp]


def _struct_summary(cfg, tables):
    ref_targets = set((_ents(cfg)[_result_ent(cfg)].get("refs") or {}).values())
    lines = []
    for n in _struct_ents(cfg):
        if n not in ref_targets or not tables.get(n):
            continue
        idc = _id_col(cfg, n)
        lines.append("%s:" % n)
        for r in tables[n]:
            vals = {k: v for k, v in r.items() if v and k not in ("publication_id", idc)}
            lines.append("  %s: %s" % (r[idc], json.dumps(vals, ensure_ascii=False)))
    return "\n".join(lines)


def build_results_prompt(cfg, struct_text, text, p0, p1):
    ent = _result_ent(cfg)
    pairs = [(_prompt_key(cfg, ent, c), _col_spec(cfg, ent, c)) for c in _result_cols(cfg)]
    ex = _fmt_example(pairs)
    metrics = cfg["schema"].get("metrics") or {}
    mtxt = ""
    if metrics:
        mtxt = "Известные показатели и целевые единицы (raw_* оставляй как в статье): " + "; ".join(
            "%s (%s)" % (k, (v or {}).get("unit", "")) for k, v in metrics.items()) + ".\n"
    return ("Структура статьи уже извлечена (id записей используй для связей):\n%s\n\nНиже часть статьи (страницы %d-%d). "
            "Извлеки из неё ВСЕ значения результатов с источником. Верни JSON:\n"
            '{"%s": [%s],\n "issues": [{"check": "contradiction|champion_vs_mean|unit_inconsistency|missing_conditions", "description": "что не сходится, с числами", "page": "число", "fragment": "дословная цитата"}]}\n'
            "Правила: каждое значение — отдельный элемент; значения из таблицы и повторы в тексте записывай ВСЕ (повторы код пометит сам); "
            "значения из чужих работ помечай как cited, связи у них пустые; page — номер из маркера PAGE; fragment копируй символ в символ; "
            "если значения в статье нет — пустая строка, ничего не достраивай.\n%s\nТЕКСТ:\n%s" % (struct_text or "(нет)", p0, p1, ent, ex, mtxt, text))


# ====================================================================== разбор ответа модели
def _sval(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "; ".join(x for x in (_sval(i) for i in v) if x)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def _ci_get(d, key):
    if key in d:
        return d[key]
    lk = key.lower()
    for k, v in d.items():
        if str(k).lower() == lk:
            return v
    return None


def _norm_enum(val, vocab):
    if not val:
        return "", False
    for v in vocab:
        if str(v) == val:
            return str(v), False
    low = re.sub(r"\s+", " ", val).strip().lower()
    for v in vocab:
        if str(v).lower() == low:
            return str(v), False
    return "", True


def _norm_bool(val):
    low = val.strip().lower()
    if low in ("true", "yes", "да", "1"):
        return "true"
    if low in ("false", "no", "нет", "0"):
        return "false"
    return ""


def _as_int(v):
    try:
        return int(float(str(v).strip().replace(",", ".")))
    except Exception:
        return None


def _rows(resp, key):
    v = _ci_get(resp, key) if isinstance(resp, dict) else None
    if v is None and isinstance(resp, dict):
        lists = [x for x in resp.values() if isinstance(x, list) and x and isinstance(x[0], dict)]
        v = lists[0] if len(lists) == 1 and key != "issues" else None
    return [r for r in (v or []) if isinstance(r, dict)]


def _clean_row(cfg, ent, raw, cols, warns):
    """Строка модели -> data-dict по колонкам: приведение типов/словарей; ключи модели — колонки или короткие алиасы."""
    e = _ents(cfg)[ent]
    fields = e.get("fields") or {}
    out, extra_note = {}, []
    for col in cols:
        v = _ci_get(raw, col)
        if v is None:
            alias = _prompt_key(cfg, ent, col)
            v = _ci_get(raw, alias) if alias != col else None
        s = _sval(v)
        f = fields.get(col) or {}
        t = f.get("type", "string")
        if s and t == "enum" and f.get("vocab"):
            s2, bad = _norm_enum(s, f["vocab"])
            if bad:
                extra_note.append("%s: «%s» (вне словаря)" % (col, s))
                warns.append("вне словаря %s.%s=%s" % (ent, col, s))
            s = s2
        elif s and t == "bool":
            s = _norm_bool(s)
        elif s and t == "int" and _as_int(s) is not None and re.fullmatch(r"-?\d+([.,]0+)?", s.strip()):
            s = str(_as_int(s))
        out[col] = s
    if extra_note and "note" in cols:
        out["note"] = (out.get("note", "") + ("; " if out.get("note") else "") + "; ".join(extra_note))
    return out


def _num(v):
    s = str(v or "").replace("−", "-").replace(",", ".").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def mark_duplicates(cfg, rows):
    """rows — data-dicts результатов. Одинаковые ключи (связи + метрика + значение + origin) -> повторы получают duplicate_of основного
    (основной: из таблицы, иначе с меньшей страницей, иначе первый). Ключ можно переопределить в schema extraction.dedup_fields."""
    ent = _result_ent(cfg)
    e = _ents(cfg)[ent]
    cols = e["columns"]
    idc = e["id"]
    if "duplicate_of" not in cols:
        return rows
    custom = (cfg["schema"].get("extraction") or {}).get("dedup_fields")
    comp = set(COMPUTED)
    keycols = list(custom) if custom else (
        list((e.get("refs") or {}).keys()) + [c for c in cols if c.startswith(("metric_", "time_")) and c not in comp] +
        [c for c in ("result_origin", "cited_publication_id") if c in cols])
    keycols = [c for c in keycols if c in cols]
    groups = {}
    for i, r in enumerate(rows):
        r["duplicate_of"] = ""
        raw = r.get("raw_value", "")
        if not str(raw).strip():
            continue
        n = _num(raw)
        key = (n if n is not None else str(raw).strip().lower(),) + tuple(str(r.get(c, "")).strip().lower() for c in keycols)
        groups.setdefault(key, []).append(i)
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        def rank(i):
            r = rows[i]
            pg = _as_int(r.get("source_page"))
            return (0 if str(r.get("source_table", "")).strip() else 1, pg if pg is not None else 10 ** 6, i)
        main = min(idxs, key=rank)
        for i in idxs:
            if i != main:
                rows[i]["duplicate_of"] = rows[main][idc]
    return rows


class _Rids:
    """Присвоение rid: id модели (если корректен и уникален) либо префикс+номер; не пересекается с ручными записями."""
    def __init__(self, cfg, ent, taken):
        self.cfg, self.ent, self.taken, self.n = cfg, ent, set(taken), 0
        self.wide = ent == _result_ent(cfg)

    def new(self, model_id=""):
        mid = re.sub(r"\s+", "", _sval(model_id))
        if mid and re.fullmatch(r"[\w\-.]{1,24}", mid, re.U) and mid not in self.taken:
            self.taken.add(mid)
            return mid
        while True:
            self.n += 1
            r = _rid_prefix(self.cfg, self.ent) + (("%03d" if self.wide else "%d") % self.n)
            if r not in self.taken:
                self.taken.add(r)
                return r


def _manual_rids(pub_id):
    out = {}
    for r in db.q("SELECT entity, rid FROM records WHERE pub_id=? AND status='manual'", (pub_id,)):
        out.setdefault(r["entity"], set()).add(r["rid"])
    return out


def _resolve_refs(cfg, ent, data, idmaps, warns):
    for col, target in (_ents(cfg)[ent].get("refs") or {}).items():
        if col not in data or not data[col]:
            continue
        raw = data[col]
        first = re.split(r"[;,]\s*", raw)[0].strip() if raw else ""
        mp = idmaps.get(target, {})
        new = mp.get(first.lower())
        if new is None:
            warns.append("ссылка %s.%s=%s не найдена в %s" % (ent, col, raw, target))
            new = ""
        data[col] = new


def _insert_record(cn, pub, ent, rid, data, status="extracted"):
    t = db.now()
    sp = _as_int(data.get("source_page")) if "source_page" in data else None
    cn.execute("INSERT OR REPLACE INTO records(pub_id,domain,entity,rid,data,status,source_page,source_fragment,quote_verified,version,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
               (pub["id"], pub["domain"], ent, rid, db.jd(data), status, sp, data.get("source_fragment") or None, None, 1, t, t))


def _wipe_extracted(cn, pub_id):
    cn.execute("DELETE FROM records_fts WHERE pub_id=?", (pub_id,))
    cn.execute("DELETE FROM records WHERE pub_id=? AND status!='manual'", (pub_id,))
    cn.execute("DELETE FROM issues WHERE pub_id=? AND origin IN ('model','verify','rule')", (pub_id,))


# ====================================================================== extract
def _load_chunks(c):
    f = _seg_path(c["pub"]["id"])
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))["chunks"]
        except Exception:
            pass
    return segment_pages(_pages(c["pub"]["id"]), _chunk_chars(c["cfg"]))["chunks"]


def _st_extract(c):
    pub, cfg = c["pub"], c["cfg"]
    pid = pub["id"]
    chunks = _load_chunks(c)
    if not chunks:
        raise StageError("нет текста для извлечения")
    system = build_system(cfg)
    s_ents = _struct_ents(cfg)
    ents = _ents(cfg)
    pe, re_ = _pub_ent(cfg), _result_ent(cfg)

    # --- шаг 1: структура (окна = первые куски; следующие окна дополняют)
    meta, raw_struct = {}, {n: [] for n in s_ents}
    for wi, ch in enumerate(chunks[:MAX_STRUCT_WINDOWS]):
        prior = None
        if wi:
            prior = json.dumps({n: [{k: v for k, v in r.items() if v} for r in rows] for n, rows in raw_struct.items()}, ensure_ascii=False)
        resp = llm.call_json(system, build_structure_prompt(cfg, ch["text"], prior), pid, "extract:structure")
        if wi == 0:
            meta = resp if isinstance(resp, dict) else {}
            ins = _ci_get(meta, "in_scope")
            if isinstance(ins, str):
                ins = ins.strip().lower() in ("true", "yes", "да", "1")
            if ins is False:
                reason = _sval(_ci_get(meta, "exclusion_reason")) or "вне области"
                return _finish_excluded(c, meta, reason)
        for n in s_ents:
            seen = {re.sub(r"\s+", "", _sval(_ci_get(r, "id"))).lower() for r in raw_struct[n]}
            for r in _rows(resp, n):
                mid = re.sub(r"\s+", "", _sval(_ci_get(r, "id"))).lower()
                if wi and mid and mid in seen:
                    continue        # повтор уже извлечённой записи в следующем окне
                seen.add(mid)
                raw_struct[n].append(r)
        if not any(raw_struct.values()) and wi == 0 and not _rows(resp, re_):
            break  # структуры нет — дальше окна не нужны, результаты всё равно спросим по кускам

    warns = []
    taken = _manual_rids(pid)
    idmaps, rids_by, struct_rows = {}, {}, {}
    for n in s_ents:                      # 1-й проход: rid для всех, 2-й — связи
        alloc = _Rids(cfg, n, taken.get(n, ()))
        idmaps[n], rids_by[n] = {}, []
        for raw in raw_struct[n]:
            rid = alloc.new(_ci_get(raw, "id"))
            rids_by[n].append((rid, raw))
            mid = re.sub(r"\s+", "", _sval(_ci_get(raw, "id"))).lower()
            if mid:
                idmaps[n].setdefault(mid, rid)
    for n in s_ents:
        rows = []
        for rid, raw in rids_by[n]:
            cols = [col for col in ents[n]["columns"] if col not in ("publication_id", ents[n]["id"])]
            d = {"publication_id": pid, ents[n]["id"]: rid}
            d.update(_clean_row(cfg, n, raw, cols, warns))
            _resolve_refs(cfg, n, d, idmaps, warns)
            rows.append(d)
        struct_rows[n] = rows

    # --- шаг 2: результаты по кускам
    struct_text = _struct_summary(cfg, struct_rows)
    cols = _result_cols(cfg)
    res_raw, model_issues = [], []
    for ch in chunks:
        resp = llm.call_json(system, build_results_prompt(cfg, struct_text, ch["text"], ch["p0"], ch["p1"]), pid, "extract:results")
        res_raw.extend(_rows(resp, re_))
        model_issues.extend(_rows(resp, "issues"))
    alloc = _Rids(cfg, re_, taken.get(re_, ()))
    res_rows = []
    for raw in res_raw:
        d = {"publication_id": pid, ents[re_]["id"]: alloc.new("")}
        d.update(_clean_row(cfg, re_, raw, cols, warns))
        _resolve_refs(cfg, re_, d, idmaps, warns)
        if "result_origin" in ents[re_]["columns"] and not d.get("result_origin"):
            d["result_origin"] = "cited" if d.get("cited_publication_id") else "primary"
        if "extraction_status" in ents[re_]["columns"] and not d.get("extraction_status"):
            d["extraction_status"] = "extracted"
        res_rows.append(d)
    mark_duplicates(cfg, res_rows)

    # --- публикация
    pcols = _pub_fields(cfg)
    pdata = {"publication_id": pid}
    for col in pcols:
        pdata[col] = _sval(_ci_get(meta, col))
    for col, v in (("title", pub["title"]), ("year", pub["year"]), ("DOI", pub["doi"]), ("authors", pub["authors"])):
        if col in pcols and not pdata.get(col) and v:
            pdata[col] = v
    if "in_scope" in pcols:
        pdata["in_scope"] = "true"
    with db.tx() as cn:
        _wipe_extracted(cn, pid)
        _insert_record(cn, pub, pe, pid, pdata)
        for n in s_ents:
            for d in struct_rows[n]:
                _insert_record(cn, pub, n, d[ents[n]["id"]], d)
        for d in res_rows:
            _insert_record(cn, pub, re_, d[ents[re_]["id"]], d)
        for it in model_issues:
            t = db.now()
            cn.execute("INSERT INTO issues(pub_id,domain,rule_id,rule_version,severity,record_ids,description,source_page,source_fragment,origin,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, pub["domain"], _sval(it.get("check")) or "model_issue", None, "warning", "[]", _sval(it.get("description")),
                        _as_int(it.get("page")), _sval(it.get("fragment")) or None, "model", "open", t, t))
    _set_pub(pid, in_scope=1, exclusion_reason="", title=pub["title"] or pdata.get("title", ""), year=pub["year"] or pdata.get("year", ""),
             authors=pub["authors"] or pdata.get("authors", ""), doi=pub["doi"] or pdata.get("DOI", "").lower())
    n_dup = sum(1 for d in res_rows if d.get("duplicate_of"))
    msg = "структура: %s; результатов %d (повторов %d); замечаний модели %d" % (
        ", ".join("%s %d" % (n, len(struct_rows[n])) for n in s_ents), len(res_rows), n_dup, len(model_issues))
    if warns:
        msg += "; предупреждений %d (%s)" % (len(warns), "; ".join(warns[:3]))
    return msg


def _finish_excluded(c, meta, reason):
    pub, cfg = c["pub"], c["cfg"]
    pid, pe = pub["id"], _pub_ent(c["cfg"])
    pcols = _pub_fields(cfg)
    pdata = {"publication_id": pid}
    for col in pcols:
        pdata[col] = _sval(_ci_get(meta, col))
    pdata["in_scope"] = "false"
    pdata["exclusion_reason"] = reason
    with db.tx() as cn:
        _wipe_extracted(cn, pid)
        _insert_record(cn, pub, pe, pid, pdata)
    _set_pub(pid, in_scope=0, exclusion_reason=reason, status="excluded", message="вне области: " + reason,
             title=pub["title"] or pdata.get("title", ""), year=pub["year"] or pdata.get("year", ""))
    raise StageStop("excluded", "вне области: " + reason)


# ====================================================================== verify / normalize / validate / publish
def _tables(pub_id, cfg, include_rejected=False):
    t = {n: [] for n in _ents(cfg)}
    for r in db.q("SELECT entity, rid, data, status FROM records WHERE pub_id=? ORDER BY id", (pub_id,)):
        if r["entity"] not in t or (r["status"] == "rejected" and not include_rejected):
            continue
        d = db.jl(r["data"], {}) or {}
        d[_id_col(cfg, r["entity"])] = r["rid"]
        t[r["entity"]].append(d)
    return t


def _st_verify(c):
    pub, cfg = c["pub"], c["cfg"]
    pid = pub["id"]
    pages = _pages(pid)
    ents = _ents(cfg)
    db.ex("DELETE FROM issues WHERE pub_id=? AND origin='verify' AND status='open'", (pid,))
    done_before = {(i["rule_id"], rid) for i in db.q("SELECT rule_id, record_ids FROM issues WHERE pub_id=? AND origin='verify'", (pid,)) for rid in (db.jl(i["record_ids"], []) or [])}
    ok_n = bad_n = skip_n = 0
    for r in db.q("SELECT * FROM records WHERE pub_id=? AND status!='manual'", (pid,)):
        ent = r["entity"]
        if "source_fragment" not in ents.get(ent, {}).get("columns", []):
            continue
        if r["status"] == "rejected" and r["quote_verified"] != 0:
            continue                      # отклонено человеком — не трогаем
        d = db.jl(r["data"], {}) or {}
        has_value = bool(str(d.get("raw_value", "")).strip()) or str(d.get("extraction_status", "")) == "extracted"
        if not has_value:
            skip_n += 1
            continue
        frag = d.get("source_fragment", "")
        ok, found = vf.verify_quote(pages, d.get("source_page"), frag)
        t = db.now()
        if ok:
            if found != _as_int(d.get("source_page")) and "source_page" in d:
                d["source_page"] = str(found)
            status = "extracted" if r["status"] == "rejected" else r["status"]
            db.ex("UPDATE records SET status=?, quote_verified=1, source_page=?, data=?, updated=? WHERE id=?", (status, found, db.jd(d), t, r["id"]))
            ok_n += 1
            if str(d.get("raw_value", "")).strip() and not vf.number_in_quote(d["raw_value"], frag) and ("value_not_in_quote", r["rid"]) not in done_before:
                _add_issue(pub, "value_not_in_quote", "warning", [r["rid"]], "Значение «%s» не найдено в цитате" % d["raw_value"], found, frag)
        else:
            db.ex("UPDATE records SET status='rejected', quote_verified=0, updated=? WHERE id=?", (t, r["id"]))
            bad_n += 1
            if ("citation_not_found", r["rid"]) not in done_before:
                why = "цитата пустая" if not str(frag).strip() else "цитата не найдена в тексте статьи"
                _add_issue(pub, "citation_not_found", "warning", [r["rid"]], "Значение отклонено: %s" % why, _as_int(d.get("source_page")), frag)
    return "цитат подтверждено %d, отклонено %d, без значения %d" % (ok_n, bad_n, skip_n)


def _add_issue(pub, rule_id, severity, rids, desc, page, frag, origin="verify", version=None):
    t = db.now()
    db.ex("INSERT INTO issues(pub_id,domain,rule_id,rule_version,severity,record_ids,description,source_page,source_fragment,origin,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (pub["id"], pub["domain"], rule_id, version, severity, db.jd(rids), desc, _as_int(page), frag or None, origin, "open", t, t))


def _rules():
    try:
        from . import rules
    except ImportError as e:
        raise StageError("модуль app/rules.py недоступен: %s" % e)
    return rules


def _st_normalize(c):
    rules = _rules()
    pid, cfg = c["pub"]["id"], c["cfg"]
    tables = _tables(pid, cfg)
    before = json.dumps(tables, sort_keys=True, ensure_ascii=False)
    out = rules.normalize_all(cfg, tables)
    if json.dumps(out, sort_keys=True, ensure_ascii=False) == before:
        return "без изменений"
    ch = 0
    old = {(r["entity"], r["rid"]): r for r in db.q("SELECT id, entity, rid, data FROM records WHERE pub_id=?", (pid,))}
    for ent, rows in out.items():
        idc = _id_col(cfg, ent)
        for d in rows:
            rec = old.get((ent, d.get(idc)))
            if not rec:
                continue
            nd = {k: ("" if v is None else v if isinstance(v, str) else _sval(v)) for k, v in d.items() if k != idc}
            cur = db.jl(rec["data"], {}) or {}
            nd[idc] = cur.get(idc, d.get(idc))
            if nd != cur:
                db.ex("UPDATE records SET data=?, updated=? WHERE id=?", (db.jd({**cur, **nd}), db.now(), rec["id"]))
                ch += 1
    return "обновлено записей: %d" % ch


def _st_validate(c):
    rules = _rules()
    pub, cfg = c["pub"], c["cfg"]
    pid = pub["id"]
    tables = _tables(pid, cfg)
    issues = rules.run_rules(cfg, tables) or []
    t = db.now()
    with db.tx() as cn:
        cn.execute("DELETE FROM issues WHERE pub_id=? AND origin='rule' AND status='open'", (pid,))
        kept = {(i["rule_id"], i["record_ids"]) for i in cn.execute("SELECT rule_id, record_ids FROM issues WHERE pub_id=? AND origin='rule'", (pid,)).fetchall()}
        n_new = 0
        for i in issues:
            rids = list(i.get("record_ids") or [])
            if (i.get("rule_id"), db.jd(rids)) in kept:
                continue
            cn.execute("INSERT INTO issues(pub_id,domain,rule_id,rule_version,severity,record_ids,description,source_page,source_fragment,origin,status,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (pid, pub["domain"], i.get("rule_id"), i.get("rule_version"), i.get("severity", "warning"), db.jd(rids), i.get("description", ""),
                        _as_int(i.get("source_page")), i.get("source_fragment") or None, "rule", "open", t, t))
            n_new += 1
    _sync_flags(pid, cfg)
    return "замечаний: %d" % n_new


def _sync_flags(pid, cfg):
    """Записи результатов с открытыми замечаниями (error|warning) -> flagged; без замечаний flagged -> extracted."""
    flagged = set()
    for i in db.q("SELECT record_ids FROM issues WHERE pub_id=? AND status='open' AND severity IN ('error','warning')", (pid,)):
        flagged.update(db.jl(i["record_ids"], []) or [])
    re_ = _result_ent(cfg)
    for r in db.q("SELECT id, rid, status FROM records WHERE pub_id=? AND entity=?", (pid, re_)):
        if r["status"] in ("extracted", "verified") and r["rid"] in flagged:
            db.ex("UPDATE records SET status='flagged' WHERE id=?", (r["id"],))
        elif r["status"] == "flagged" and r["rid"] not in flagged:
            db.ex("UPDATE records SET status='extracted' WHERE id=?", (r["id"],))


def _reindex(pid):
    try:
        from . import fts
        fts.reindex(pid)
        return
    except ImportError:
        pass
    with db.tx() as cn:
        cn.execute("DELETE FROM records_fts WHERE pub_id=?", (pid,))
        for r in cn.execute("SELECT entity, rid, data FROM records WHERE pub_id=? AND status!='rejected'", (pid,)).fetchall():
            d = db.jl(r["data"], {}) or {}
            cn.execute("INSERT INTO records_fts(rid,pub_id,entity,text) VALUES(?,?,?,?)", (r["rid"], pid, r["entity"],
                       " ".join(str(v) for k, v in d.items() if v and k != "publication_id")))


def _st_publish(c):
    pub, cfg = c["pub"], c["cfg"]
    pid = pub["id"]
    cur = db.q1("SELECT in_scope FROM publications WHERE id=?", (pid,))
    if cur and cur["in_scope"] == 0:
        _set_pub(pid, status="excluded")
        return "вне области"
    _sync_flags(pid, cfg)
    open_ids = set()
    for i in db.q("SELECT record_ids FROM issues WHERE pub_id=? AND status='open' AND severity IN ('error','warning')", (pid,)):
        open_ids.update(db.jl(i["record_ids"], []) or [])
    re_ = _result_ent(cfg)
    with db.tx() as cn:
        for r in cn.execute("SELECT id, entity, rid, status, quote_verified FROM records WHERE pub_id=?", (pid,)).fetchall():
            if r["status"] == "extracted" and r["quote_verified"] != 0 and not (r["entity"] == re_ and r["rid"] in open_ids):
                cn.execute("UPDATE records SET status='verified' WHERE id=?", (r["id"],))
    _reindex(pid)
    n_open = db.q1("SELECT COUNT(*) n FROM issues WHERE pub_id=? AND status='open' AND severity IN ('error','warning')", (pid,))["n"]
    n_rej = db.q1("SELECT COUNT(*) n FROM records WHERE pub_id=? AND status='rejected'", (pid,))["n"]
    n_rec = db.q1("SELECT COUNT(*) n FROM records WHERE pub_id=? AND entity=? AND status!='rejected'", (pid, re_))["n"]
    status = "flagged" if (n_open or n_rej) else "verified"
    msg = "результатов %d, открытых замечаний %d, отклонено %d" % (n_rec, n_open, n_rej)
    _set_pub(pid, status=status, message=msg)
    return msg


_FUNCS = {"register": _st_register, "parse": _st_parse, "segment": _st_segment, "extract": _st_extract,
          "verify": _st_verify, "normalize": _st_normalize, "validate": _st_validate, "publish": _st_publish}


# ====================================================================== запуск
def run_publication(pub_id, from_stage=None, stop_after=None):
    """Синхронный запуск. from_stage=None — с первой не done стадии. Возвращает итоговый статус публикации."""
    _ensure_started()
    if from_stage is not None and from_stage not in STAGES:
        raise ValueError("Неизвестная стадия: %s" % from_stage)
    with _lock(pub_id):
        pub = db.q1("SELECT * FROM publications WHERE id=?", (pub_id,))
        if not pub:
            raise KeyError("Публикация не найдена: %s" % pub_id)
        done = {r["stage"]: r["status"] for r in db.q("SELECT stage, status FROM stage_runs WHERE pub_id=?", (pub_id,))}
        if from_stage:
            for s in STAGES[STAGES.index(from_stage):]:
                _set_stage(pub_id, s, "pending", "")
            start = STAGES.index(from_stage)
        else:
            start = next((i for i, s in enumerate(STAGES) if done.get(s) != "done"), len(STAGES))
            if pub["status"] == "needs_ocr" and pub["ocr_status"] in ("queued", "running"):
                return "needs_ocr"
            if start >= len(STAGES):
                return pub["status"]
        _set_pub(pub_id, status="processing", message="")
        for s in STAGES[start:]:
            pub = db.q1("SELECT * FROM publications WHERE id=?", (pub_id,))
            _set_stage(pub_id, s, "running", "", new_attempt=True)
            try:
                cfg = config.load_domain(pub["domain"])
                msg = _FUNCS[s]({"pub": pub, "cfg": cfg}) or ""
                _set_stage(pub_id, s, "done", msg)
            except StageStop as st:
                _set_stage(pub_id, s, "done" if st.pub_status == "excluded" else "error", st.message)
                if st.pub_status == "excluded":
                    for rest in STAGES[STAGES.index(s) + 1:]:
                        _set_stage(pub_id, rest, "done", "пропущено: вне области")
                    _set_pub(pub_id, status="excluded")
                return st.pub_status
            except Exception as e:
                text = str(e) or type(e).__name__
                if not isinstance(e, (StageError, LLMError)):
                    text = "%s: %s" % (type(e).__name__, text)
                    traceback.print_exc()
                _set_stage(pub_id, s, "error", text)
                _set_pub(pub_id, status="error", message="%s: %s" % (s, text))
                return "error"
            if stop_after == s:
                break
        return db.q1("SELECT status FROM publications WHERE id=?", (pub_id,))["status"]


def revalidate(pub_id):
    """Синхронно normalize+validate+publish (после правки записей)."""
    return run_publication(pub_id, from_stage="normalize")


# ====================================================================== очередь
def _get_pool():
    n = int(llm.effective_settings().get("workers") or 3)
    if _pool["ex"] is None or _pool["n"] != n:
        _pool["ex"] = ThreadPoolExecutor(max_workers=n, thread_name_prefix="pipe")
        _pool["n"] = n
    return _pool["ex"]


def _job(pub_id, from_stage):
    try:
        run_publication(pub_id, from_stage)
    except Exception as e:
        try:
            _set_pub(pub_id, status="error", message=str(e))
        except Exception:
            pass
    finally:
        with _inflight_guard:
            _inflight.discard(pub_id)


def enqueue(pub_id, from_stage=None):
    """Ставит публикацию в пул потоков (workers из settings). False, если она уже в очереди."""
    _ensure_started()
    with _inflight_guard:
        if pub_id in _inflight:
            return False
        _inflight.add(pub_id)
    _set_pub(pub_id, message="в очереди")
    _get_pool().submit(_job, pub_id, from_stage)
    return True


def enqueue_many(pub_ids, from_stage=None):
    return [p for p in pub_ids if enqueue(p, from_stage)]


def wait_idle(timeout=60):
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        with _inflight_guard:
            if not _inflight:
                return True
        time.sleep(0.05)
    return False


# ====================================================================== OCR
def ocr_available():
    from . import ocr
    return ocr.ocr_available()


def run_ocr(pub_id):
    from . import ocr
    return ocr.run_ocr(pub_id)
