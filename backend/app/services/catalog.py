"""Canonical catalog helpers."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Examination


def next_canonical_code(db: Session, category: str) -> str:
    prefixes = {
        "Laboratory": "LAB",
        "Radiology": "RAD",
        "Dental": "DEN",
        "Clinic": "PRC",
        "Inpatient": "PRC",
        "Procedure": "PRC",
    }
    prefix = prefixes.get(category, "OTH")
    # count existing with prefix
    n = db.execute(
        select(func.count(Examination.id)).where(Examination.canonical_code.like(f"{prefix}-%"))
    ).scalar() or 0
    # find a free number
    existing = set(
        db.execute(
            select(Examination.canonical_code).where(Examination.canonical_code.like(f"{prefix}-%"))
        ).scalars().all()
    )
    i = n + 1
    while f"{prefix}-{i:06d}" in existing:
        i += 1
    return f"{prefix}-{i:06d}"
