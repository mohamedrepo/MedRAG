"""Repair wrong short-name mappings (Vitamin K/A/E → Vitamin D) and similar.

Also flags implausibly cheap lab/imaging rows (≤ 5 EGP) for review.
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.embeddings import get_embedder, pack_vector
from app.models import Examination, PriceRecord, ReviewItem, SearchCache
from app.services.catalog import next_canonical_code


# original_name (exact) → canonical EN/AR
FIXTURES = {
    "Vitamin K1": ("Vitamin K1", "فيتامين ك 1", "Laboratory", "Hormones"),
    "Vitamin K2": ("Vitamin K2", "فيتامين ك 2", "Laboratory", "Hormones"),
    "VIT A,": ("Vitamin A", "فيتامين أ", "Laboratory", "Hormones"),
    "VIT E": ("Vitamin E", "فيتامين هـ", "Laboratory", "Hormones"),
}


def get_or_create_exam(db: Session, en: str, ar: str, cat: str, sub: str | None) -> Examination:
    e = db.execute(select(Examination).where(Examination.name_en == en)).scalar_one_or_none()
    if not e:
        from app.xref import XRefEngine

        e = Examination(
            canonical_code=next_canonical_code(db, cat),
            name_en=en,
            name_ar=ar,
            category=cat,
            subcategory=sub,
        )
        db.add(e)
        db.flush()
        emb = get_embedder()
        e.embedding = pack_vector(emb.embed_one(f"{en} {ar}"))
        e.embedding_model = emb.name
        x = XRefEngine(db)
        x.propose_alias(en, e.id, alias_type="full_name", confidence=1.0, auto_approve=True)
        x.propose_alias(ar, e.id, alias_type="full_name", confidence=1.0, auto_approve=True)
    return e


def main() -> None:
    db = SessionLocal()
    try:
        n_fixed = 0
        for orig, (en, ar, cat, sub) in FIXTURES.items():
            exam = get_or_create_exam(db, en, ar, cat, sub)
            recs = db.execute(
                select(PriceRecord).where(PriceRecord.original_name == orig)
            ).scalars().all()
            for r in recs:
                old = r.examination_id
                r.examination_id = exam.id
                r.name_en = en
                r.name_ar = ar
                r.review_status = "corrected"
                n_fixed += 1
                print(f"  relink {orig!r} -> {en} (was {str(old)[:8]})")

        # Flag implausibly cheap prices (TOC / index artifacts)
        from app.models import PriceValue
        cheap = 0
        rec_ids = db.execute(
            select(PriceRecord.id)
            .join(PriceValue, PriceValue.price_record_id == PriceRecord.id)
            .where(PriceValue.value <= 5, PriceRecord.review_status == "auto")
        ).scalars().all()
        for rid in rec_ids:
            rec = db.get(PriceRecord, rid)
            rec.review_status = "needs_review"
            db.add(ReviewItem(
                entity_type="price_record",
                entity_id=rid,
                reason="implausible_low_price",
                detail={"name": rec.original_name, "page": rec.source_page},
                priority=2,
            ))
            cheap += 1
        print("flagged cheap", cheap, "fixed mappings", n_fixed)

        db.query(SearchCache).delete()
        db.commit()
        print("cache cleared")
    finally:
        db.close()


if __name__ == "__main__":
    main()
