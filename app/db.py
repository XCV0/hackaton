"""SQLite: схема и тонкие хелперы. Один файл data/app.db, WAL, внешние ключи выключены (гибкость правок), JSON хранится текстом."""
import sqlite3, json, pathlib, time, threading, contextlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "app.db"
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','annotator','researcher')), pw_hash TEXT, salt TEXT, token TEXT UNIQUE, created TEXT);
CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY, domain TEXT NOT NULL, filename TEXT, pdf_path TEXT, sha256 TEXT, doi TEXT, title TEXT, year TEXT, authors TEXT,
  pages INTEGER, text_pages INTEGER, needs_ocr INTEGER DEFAULT 0, ocr_status TEXT DEFAULT '', in_scope INTEGER, exclusion_reason TEXT,
  status TEXT NOT NULL DEFAULT 'registered', split TEXT DEFAULT '', message TEXT DEFAULT '', created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS stage_runs(id INTEGER PRIMARY KEY, pub_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER DEFAULT 1, message TEXT DEFAULT '', started TEXT, finished TEXT, UNIQUE(pub_id, stage));
CREATE TABLE IF NOT EXISTS pages(pub_id TEXT NOT NULL, page INTEGER NOT NULL, text TEXT, ocr INTEGER DEFAULT 0, PRIMARY KEY(pub_id, page));
CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, pub_id TEXT NOT NULL, domain TEXT NOT NULL, entity TEXT NOT NULL, rid TEXT NOT NULL, data TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'extracted', source_page INTEGER, source_fragment TEXT, quote_verified INTEGER, version INTEGER DEFAULT 1, created TEXT, updated TEXT, UNIQUE(pub_id, entity, rid));
CREATE INDEX IF NOT EXISTS ix_records_pub ON records(pub_id, entity);
CREATE TABLE IF NOT EXISTS record_history(id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL, user TEXT, action TEXT, before TEXT, after TEXT, comment TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY, pub_id TEXT NOT NULL, domain TEXT NOT NULL, rule_id TEXT, rule_version INTEGER, severity TEXT DEFAULT 'warning', record_ids TEXT DEFAULT '[]',
  description TEXT, source_page INTEGER, source_fragment TEXT, origin TEXT DEFAULT 'rule', status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','accepted','fixed','kept')), comment TEXT DEFAULT '', resolved_by TEXT, created TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS ix_issues_pub ON issues(pub_id, status);
CREATE TABLE IF NOT EXISTS llm_calls(id INTEGER PRIMARY KEY, pub_id TEXT, stage TEXT, model TEXT, prompt TEXT, response TEXT, prompt_chars INTEGER, response_chars INTEGER, ok INTEGER, error TEXT, ms INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS eval_runs(id INTEGER PRIMARY KEY, domain TEXT, ts TEXT, model TEXT, config_version TEXT, note TEXT, pub_ids TEXT, metrics TEXT);
CREATE TABLE IF NOT EXISTS config_versions(id INTEGER PRIMARY KEY, domain TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('schema','rules')), version INTEGER NOT NULL, body TEXT NOT NULL, author TEXT, ts TEXT, active INTEGER DEFAULT 0, comment TEXT DEFAULT '', UNIQUE(domain, kind, version));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, user TEXT, action TEXT, target TEXT, detail TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS corpus_sources(id INTEGER PRIMARY KEY, domain TEXT NOT NULL, path TEXT NOT NULL, split TEXT DEFAULT '', added TEXT, last_scan TEXT, UNIQUE(domain, path));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(rid UNINDEXED, pub_id UNINDEXED, entity UNINDEXED, text, tokenize='unicode61');
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        _local.c = c
    return c


def init_db():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def ex(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid


@contextlib.contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


def jl(s, default=None):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def jd(o):
    return json.dumps(o, ensure_ascii=False)


def audit(user, action, target="", detail=""):
    ex("INSERT INTO audit(user,action,target,detail,ts) VALUES(?,?,?,?,?)", (user, action, target, detail if isinstance(detail, str) else jd(detail), now()))
