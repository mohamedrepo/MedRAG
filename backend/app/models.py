"""SQLAlchemy models for MedRAG.

PostgreSQL is the authoritative store for identities, prices and validity.
Vector embeddings are discovery aids, never the source of truth for prices.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def uid() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Auth / audit
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # admin | reviewer | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


# ---------------------------------------------------------------------------
# Organizations & providers
# ---------------------------------------------------------------------------
class Organization(Base):
    """Issuing body, payer, syndicate, university hospital authority, etc."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(255), index=True)
    name_ar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    org_type: Mapped[str | None] = mapped_column(String(40))  # syndicate | hospital | lab | payer | university | other
    contact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), default="Egypt")
    region: Mapped[str | None] = mapped_column(String(80))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    providers: Mapped[list[Provider]] = relationship(back_populates="organization")


class Provider(Base):
    """Actual medical center / branch performing the service.

    Never assume the issuing organization is the provider.
    """

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    name_ar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255))
    provider_type: Mapped[str | None] = mapped_column(String(40))  # hospital | lab | radiology | dental | clinic | other
    location: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(80), index=True)
    governorate: Mapped[str | None] = mapped_column(String(80), index=True)
    contact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    organization: Mapped[Organization | None] = relationship(back_populates="providers")


# ---------------------------------------------------------------------------
# Source documents
# ---------------------------------------------------------------------------
class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    original_filename: Mapped[str] = mapped_column(String(512))
    file_format: Mapped[str] = mapped_column(String(20), index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"), nullable=True)
    tariff_year: Mapped[int | None] = mapped_column(Integer, index=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    geographic_scope: Mapped[str | None] = mapped_column(String(80))  # Cairo / Giza / national
    version: Mapped[str | None] = mapped_column(String(40))
    storage_path: Mapped[str] = mapped_column(String(1024))
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending | processing | completed | partial | failed | duplicate
    validation_status: Mapped[str] = mapped_column(String(20), default="pending")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    tariffs: Mapped[list[Tariff]] = relationship(back_populates="document")


# ---------------------------------------------------------------------------
# Canonical examination catalog
# ---------------------------------------------------------------------------
class Examination(Base):
    __tablename__ = "examinations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    canonical_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # LAB-000001
    name_en: Mapped[str] = mapped_column(String(512), index=True)
    name_ar: Mapped[str | None] = mapped_column(String(512), index=True, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    subcategory: Mapped[str | None] = mapped_column(String(80), index=True)
    # Distinct technical variant flags — never auto-merge these.
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # keys: contrast, views, laterality, quantification, region, method, notes
    standard_code: Mapped[str | None] = mapped_column(String(40), index=True)
    standard_system: Mapped[str | None] = mapped_column(String(40))  # LOINC | CPT | local
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | merged | retired
    merged_into_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("examinations.id"), nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(Text, nullable=True)  # hex/base64 vector
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    search_text: Mapped[str | None] = mapped_column(Text)  # normalized concat for FTS
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    aliases: Mapped[list[ExaminationAlias]] = relationship(back_populates="examination", cascade="all,delete-orphan")
    price_records: Mapped[list["PriceRecord"]] = relationship(back_populates="examination")


class ExaminationAlias(Base):
    __tablename__ = "examination_aliases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    examination_id: Mapped[str] = mapped_column(ForeignKey("examinations.id"), index=True)
    original_alias: Mapped[str] = mapped_column(String(512))
    normalized_alias: Mapped[str] = mapped_column(String(512), index=True)
    language: Mapped[str] = mapped_column(String(8), default="en")  # en | ar | mixed
    alias_type: Mapped[str] = mapped_column(String(24), default="original")
    # abbreviation | full_name | transliteration | misspelling | original | manual | code
    source_document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    approval_status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)
    # approved | proposed | rejected
    created_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    examination: Mapped[Examination] = relationship(back_populates="aliases")

    __table_args__ = (
        Index("ix_alias_norm_status", "normalized_alias", "approval_status"),
        Index("ix_alias_exam", "examination_id", "normalized_alias"),
    )


# ---------------------------------------------------------------------------
# Tariffs & prices
# ---------------------------------------------------------------------------
class Tariff(Base):
    __tablename__ = "tariffs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(512))
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    geographic_scope: Mapped[str | None] = mapped_column(String(80), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="EGP")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | superseded | draft
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    document: Mapped[SourceDocument] = relationship(back_populates="tariffs")
    provider: Mapped[Provider | None] = relationship()
    organization: Mapped[Organization | None] = relationship()
    price_records: Mapped[list["PriceRecord"]] = relationship(back_populates="tariff")


class PriceRecord(Base):
    """One row in a source price list, linked to a canonical examination."""

    __tablename__ = "price_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    examination_id: Mapped[str | None] = mapped_column(ForeignKey("examinations.id"), nullable=True, index=True)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"), nullable=True, index=True)
    tariff_id: Mapped[str] = mapped_column(ForeignKey("tariffs.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(512))
    original_code: Mapped[str | None] = mapped_column(String(64), index=True)
    name_ar: Mapped[str | None] = mapped_column(String(512))
    name_en: Mapped[str | None] = mapped_column(String(512))
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), index=True)  # lab | radiology | dental | procedure
    subcategory: Mapped[str | None] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(8), default="EGP")
    unit: Mapped[str | None] = mapped_column(String(40))
    conditions: Mapped[str | None] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_sheet: Mapped[str | None] = mapped_column(String(128))
    source_cell: Mapped[str | None] = mapped_column(String(32))
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.5)
    review_status: Mapped[str] = mapped_column(String(16), default="auto", index=True)
    # auto | needs_review | approved | corrected | rejected
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    examination: Mapped[Examination | None] = relationship(back_populates="price_records")
    tariff: Mapped[Tariff] = relationship(back_populates="price_records")
    provider: Mapped[Provider | None] = relationship()
    values: Mapped[list[PriceValue]] = relationship(back_populates="record", cascade="all,delete-orphan")
    modifiers: Mapped[list["PricingModifier"]] = relationship(back_populates="record", cascade="all,delete-orphan")

    __table_args__ = (
        Index("ix_price_exam_tariff", "examination_id", "tariff_id"),
        Index("ix_price_provider", "provider_id"),
    )


class PriceValue(Base):
    """A single monetary value. One examination may have many columns.

    Never collapse multiple price categories into one number.
    """

    __tablename__ = "price_values"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    price_record_id: Mapped[str] = mapped_column(ForeignKey("price_records.id"), index=True)
    price_category: Mapped[str] = mapped_column(String(80), index=True)
    # cash | member | member_family | parents | project_50 | project_60 | project_40
    # vip | first_class | second_class | insurance | contracted | other
    value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EGP")
    modifier_type: Mapped[str] = mapped_column(String(24), default="base")  # base | contrast | extra_view | doctor | admin
    conditions: Mapped[str | None] = mapped_column(Text)
    is_patient_price: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    record: Mapped[PriceRecord] = relationship(back_populates="values")


class PricingModifier(Base):
    """Surcharges, contrast, extra views, consumables, percentages, etc."""

    __tablename__ = "pricing_modifiers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    price_record_id: Mapped[str] = mapped_column(ForeignKey("price_records.id"), index=True)
    mod_type: Mapped[str] = mapped_column(String(32), index=True)
    # surcharge | discount | contrast | extra_view | consumable | doctor_fees
    # admin | conditional | percentage | infection_control | film_3d
    label: Mapped[str] = mapped_column(String(255))
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    is_percentage: Mapped[bool] = mapped_column(Boolean, default=False)
    conditions: Mapped[str | None] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    record: Mapped[PriceRecord] = relationship(back_populates="modifiers")


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------
class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    entity_type: Mapped[str] = mapped_column(String(24), index=True)  # price_record | alias | examination | mapping
    entity_id: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(String(255))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)  # 1 high
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | resolved | dismissed
    resolution: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(32))


class IngestionReport(Base):
    __tablename__ = "ingestion_reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(String(32), index=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    issues: Mapped[list[Any] | None] = mapped_column(JSON)
    warnings: Mapped[list[Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SearchCache(Base):
    """Resolved query -> examination id, to keep repeat lookups in milliseconds."""

    __tablename__ = "search_cache"

    normalized_query: Mapped[str] = mapped_column(String(256), primary_key=True)
    examination_id: Mapped[str | None] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    strategy: Mapped[str | None] = mapped_column(String(24))
    hits: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


# Useful composite indexes
Index("ix_alias_norm", ExaminationAlias.normalized_alias)
Index("ix_exam_cat", Examination.category, Examination.subcategory)
Index("ix_doc_status", SourceDocument.extraction_status)
Index("ix_tariff_year", Tariff.year, Tariff.status)
Index("ix_pv_cat_val", PriceValue.price_category, PriceValue.value)
