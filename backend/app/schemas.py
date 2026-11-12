"""Pydantic API schemas."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    username: str
    role: str


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None
    role: str
    is_active: bool

    class Config:
        from_attributes = True


class AliasIn(BaseModel):
    alias: str
    examination_id: str


class AliasOut(BaseModel):
    id: str
    examination_id: str
    original_alias: str
    normalized_alias: str
    language: str
    alias_type: str
    confidence: float
    approval_status: str

    class Config:
        from_attributes = True


class ExaminationOut(BaseModel):
    id: str
    canonical_code: str
    name_en: str
    name_ar: str | None
    category: str | None
    subcategory: str | None
    attributes: dict[str, Any] | None
    status: str

    class Config:
        from_attributes = True


class SearchRow(BaseModel):
    rank: int
    price: float | None
    currency: str
    price_category: str
    original_name: str
    original_code: str | None
    provider_name: str | None
    provider_name_ar: str | None
    branch: str | None
    city: str | None
    governorate: str | None
    provider_unspecified: bool
    tariff_year: int | None
    effective_date: str | None
    exam_code: str
    exam_en: str | None
    exam_ar: str | None
    source_page: int | None
    source_sheet: str | None
    review_status: str
    extraction_confidence: float
    document_title: str | None
    conditions: str | None


class PriceValueIn(BaseModel):
    price_category: str
    value: float
    currency: str = "EGP"
    modifier_type: str = "base"


class PriceCorrectIn(BaseModel):
    price_value_id: str
    new_value: float
    reason: str


class DocumentOut(BaseModel):
    id: str
    original_filename: str
    file_format: str
    title: str | None
    organization_id: str | None
    provider_id: str | None
    tariff_year: int | None
    extraction_status: str
    validation_status: str
    page_count: int
    uploaded_at: datetime
    checksum: str

    class Config:
        from_attributes = True


class DocumentPatch(BaseModel):
    title: str | None = None
    provider_id: str | None = None
    organization_id: str | None = None
    tariff_year: int | None = None
    effective_date: date | None = None
    expiration_date: date | None = None
    geographic_scope: str | None = None
    notes: str | None = None


class ReviewOut(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    reason: str
    detail: dict[str, Any] | None
    priority: int
    status: str

    class Config:
        from_attributes = True


class ResolveIn(BaseModel):
    names: list[str]


class StatsOut(BaseModel):
    documents: int
    examinations: int
    approved_aliases: int
    proposed_aliases: int
    price_records: int
    price_values: int
    providers: int
    organizations: int
    needs_review: int
    pending_reviews: int
