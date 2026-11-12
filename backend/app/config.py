"""MedRAG application configuration."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "MedRAG"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    API_PREFIX: str = "/api/v1"

    # Database: SQLite by default (zero-dependency local run).
    # Production: postgresql+psycopg://medrag:medrag@localhost:5432/medrag
    DATABASE_URL: str = f"sqlite:///{(BASE_DIR / 'data' / 'medrag.db').as_posix()}"
    DB_ECHO: bool = False

    # Use pgvector / pg_trgm / FTS when the URL is PostgreSQL.
    ENABLE_PGVECTOR: bool = True

    STORAGE_DIR: str = str(BASE_DIR / "storage")
    UPLOAD_DIR: str = str(BASE_DIR / "storage" / "documents")
    EXPORT_DIR: str = str(BASE_DIR / "storage" / "exports")
    LOG_DIR: str = str(BASE_DIR / "storage" / "logs")
    DATA_DIR: str = str(BASE_DIR / "data")

    SECRET_KEY: str = "change-me-in-production-9f2c1e7a"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "medrag-admin"
    ADMIN_EMAIL: str = "admin@medrag.local"

    # Embeddings: local deterministic hasher by default.
    # Optional OpenAI-compatible endpoint (LM Studio, Ollama, Azure, etc.)
    EMBEDDING_PROVIDER: str = "local"  # local | openai
    EMBEDDING_API_BASE: str = "http://localhost:1234/v1"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-multilingual"
    EMBEDDING_DIM: int = 256

    # Optional LLM for extraction assistance (never used to sort prices).
    LLM_PROVIDER: str = "none"  # none | openai
    LLM_API_BASE: str = "http://localhost:1234/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""

    OCR_ENABLED: bool = True
    OCR_LANGUAGES: str = "ara+eng"
    MIN_TEXT_CHARS_PAGE: int = 80

    DEFAULT_CURRENCY: str = "EGP"
    DEFAULT_COUNTRY: str = "Egypt"

    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"

    MAX_UPLOAD_MB: int = 200
    ALLOWED_EXTENSIONS: str = "pdf,xlsx,xls,csv,docx,doc,pptx,txt,html,jpg,jpeg,png,tiff,tif"

    LOG_LEVEL: str = "INFO"

    def ensure_dirs(self) -> None:
        for p in (self.UPLOAD_DIR, self.EXPORT_DIR, self.LOG_DIR, self.DATA_DIR):
            Path(p).mkdir(parents=True, exist_ok=True)

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.CORS_ORIGINS.split(",") if x.strip()]

    @property
    def allowed_ext_list(self) -> list[str]:
        return [x.strip().lower() for x in self.ALLOWED_EXTENSIONS.split(",") if x.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
