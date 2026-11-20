"""FastAPI-приложение. Запуск: .venv/bin/uvicorn app.server:app --port 8765"""
import contextlib
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from . import db, config, auth, evaluation, fts, migrate, passport
from .api import session, public, edits, admin

ROOT = db.ROOT


def startup():
    db.init_db()
    config.seed_versions()
    evaluation.ensure_tables()
    auth.ensure_admin()
    try:
        migrate.migrate_if_empty()
    except Exception as e:
        print("migrate failed:", e)
    try:
        passport.write_all()
    except Exception as e:
        print("passport failed:", e)
    try:
        from . import pipeline
        if hasattr(pipeline, "mark_interrupted"):
            pipeline.mark_interrupted()
    except Exception as e:
        print("pipeline.mark_interrupted skipped:", e)


@contextlib.asynccontextmanager
async def lifespan(app):
    startup()
    yield


app = FastAPI(title="Извлечение данных из публикаций", lifespan=lifespan)
for r in (session.router, public.router, edits.router, admin.router):
    app.include_router(r)
app.mount("/", StaticFiles(directory=str(ROOT / "site"), html=True), name="site")
