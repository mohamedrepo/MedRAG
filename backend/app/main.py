"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import settings
from app.database import create_all, engine
from app.models import User
from app.security import hash_password
from app.xref import XRefEngine
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal


def _setup_logging() -> None:
    settings.ensure_dirs()
    handler = RotatingFileHandler(
        Path(settings.LOG_DIR) / "medrag.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").addHandler(handler)


def _seed_admin() -> None:
    db: Session = SessionLocal()
    try:
        admin = db.execute(select(User).where(User.username == settings.ADMIN_USERNAME)).scalar_one_or_none()
        if not admin:
            db.add(User(
                username=settings.ADMIN_USERNAME,
                email=settings.ADMIN_EMAIL,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role="admin",
            ))
            db.add(User(
                username="reviewer",
                email="reviewer@medrag.local",
                password_hash=hash_password("medrag-reviewer"),
                role="reviewer",
            ))
            db.add(User(
                username="viewer",
                email="viewer@medrag.local",
                password_hash=hash_password("medrag-viewer"),
                role="viewer",
            ))
            db.commit()
    finally:
        db.close()


def _seed_catalog() -> None:
    db: Session = SessionLocal()
    try:
        if db.execute(select(User).limit(1)).scalar_one_or_none() is None:
            return
        xref = XRefEngine(db)
        # seed if catalog empty
        from app.models import Examination
        if db.query(Examination).count() == 0:
            xref.seed_catalog()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    create_all()
    _seed_admin()
    _seed_catalog()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Bilingual medical services price intelligence & comparison platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logging.getLogger("medrag.http").info("%s %s", request.method, request.url.path)
    try:
        resp = await call_next(request)
        return resp
    except Exception as e:
        logging.getLogger("medrag.http").exception("unhandled error")
        return JSONResponse({"detail": "Internal server error", "error": str(e)}, status_code=500)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


app.include_router(router, prefix=settings.API_PREFIX)

# Serve built frontend if present
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
