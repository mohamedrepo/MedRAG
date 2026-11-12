"""Database engine and session management.

Supports SQLite (local development) and PostgreSQL + pgvector (production).
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            echo=settings.DB_ECHO,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return engine

    engine = create_engine(url, echo=settings.DB_ECHO, pool_pre_ping=True, pool_size=10, max_overflow=20)

    if settings.is_postgres and settings.ENABLE_PGVECTOR:
        @event.listens_for(engine, "connect")
        def _pg_extensions(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            for ext in ("vector", "pg_trgm", "unaccent"):
                try:
                    cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
                except Exception:
                    dbapi_conn.rollback()
            dbapi_conn.commit()
            cur.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if settings.is_postgres:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def drop_all() -> None:
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
