"""Associate المختبر و البرج lists with both معمل البرج and معمل المختبر.

The three PDFs in that folder are one shared tariff. We keep the original
records under معمل البرج and clone a parallel tariff for معمل المختبر so
price comparison shows both centers at the same numbers.
Idempotent: re-running does not duplicate.
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    Organization,
    PriceRecord,
    PriceValue,
    PricingModifier,
    Provider,
    SourceDocument,
    Tariff,
)


LAB_DOCS = [
    "Others 2026.pdf",
    "القائمة المكملة 2026.pdf",
    "المؤسسة العلاجية 2023.pdf",  # the lab booklet inside المختبر و البرج
]


def get_or_create_org(db: Session, name: str) -> Organization:
    org = db.execute(select(Organization).where(Organization.name == name)).scalar_one_or_none()
    if not org:
        org = Organization(name=name, org_type="lab", country="Egypt", region="Cairo")
        db.add(org)
        db.flush()
    return org


def get_or_create_provider(db: Session, name: str, name_ar: str, org_id: str) -> Provider:
    p = db.execute(select(Provider).where(Provider.name == name)).scalar_one_or_none()
    if not p:
        p = Provider(
            name=name,
            name_ar=name_ar,
            organization_id=org_id,
            provider_type="lab",
            city="القاهرة",
            governorate="القاهرة",
        )
        db.add(p)
        db.flush()
    elif not p.organization_id:
        p.organization_id = org_id
    return p


def clone_tariff_for(db: Session, src: Tariff, provider: Provider) -> Tariff:
    existing = db.execute(
        select(Tariff).where(
            Tariff.document_id == src.document_id,
            Tariff.provider_id == provider.id,
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    t = Tariff(
        provider_id=provider.id,
        organization_id=src.organization_id,
        document_id=src.document_id,
        title=src.title,
        effective_date=src.effective_date,
        expiration_date=src.expiration_date,
        geographic_scope=src.geographic_scope,
        year=src.year,
        currency=src.currency,
        status=src.status,
        notes="Shared list — معمل البرج ومعمل المختبر",
    )
    db.add(t)
    db.flush()
    return t


def clone_record(db: Session, rec: PriceRecord, tariff_id: str, provider_id: str) -> None:
    exists = db.execute(
        select(PriceRecord.id).where(
            PriceRecord.tariff_id == tariff_id,
            PriceRecord.original_name == rec.original_name,
            PriceRecord.original_code == rec.original_code,
        )
    ).scalar_one_or_none()
    if exists:
        return
    new = PriceRecord(
        examination_id=rec.examination_id,
        provider_id=provider_id,
        tariff_id=tariff_id,
        original_name=rec.original_name,
        original_code=rec.original_code,
        name_ar=rec.name_ar,
        name_en=rec.name_en,
        attributes=rec.attributes,
        category=rec.category,
        subcategory=rec.subcategory,
        currency=rec.currency,
        unit=rec.unit,
        conditions=rec.conditions,
        source_page=rec.source_page,
        source_sheet=rec.source_sheet,
        source_cell=rec.source_cell,
        extraction_confidence=rec.extraction_confidence,
        review_status=rec.review_status,
    )
    db.add(new)
    db.flush()
    for v in rec.values:
        db.add(PriceValue(
            price_record_id=new.id,
            price_category=v.price_category,
            value=v.value,
            currency=v.currency,
            modifier_type=v.modifier_type,
            conditions=v.conditions,
        ))
    for m in rec.modifiers:
        db.add(PricingModifier(
            price_record_id=new.id,
            mod_type=m.mod_type,
            label=m.label,
            value=m.value,
            is_percentage=m.is_percentage,
            conditions=m.conditions,
            source_page=m.source_page,
        ))


def main() -> None:
    db = SessionLocal()
    try:
        org = get_or_create_org(db, "المختبر والبرج")
        borg = get_or_create_provider(db, "معمل البرج", "معمل البرج", org.id)
        mokh = get_or_create_provider(db, "معمل المختبر", "معمل المختبر", org.id)
        print("org", org.id, "borg", borg.id, "mokhtabar", mokh.id)

        docs = db.execute(
            select(SourceDocument).where(SourceDocument.original_filename.in_(LAB_DOCS))
        ).scalars().all()
        # Also match by storage path containing المختبر
        all_docs = db.execute(select(SourceDocument)).scalars().all()
        for d in all_docs:
            if d.original_filename in LAB_DOCS or "المختبر" in (d.storage_path or ""):
                if d not in docs:
                    docs.append(d)

        print("docs", [d.original_filename for d in docs])
        n_cloned = 0
        for d in docs:
            d.organization_id = org.id
            if not d.provider_id:
                d.provider_id = borg.id
            d.notes = "قائمة سعر واحدة لمعمل البرج ومعمل المختبر (نفس الأسعار)"
            tariffs = db.execute(select(Tariff).where(Tariff.document_id == d.id)).scalars().all()
            if not tariffs:
                print("  no tariffs for", d.original_filename)
                continue
            # Ensure first tariff is البرج
            primary = tariffs[0]
            primary.provider_id = borg.id
            primary.organization_id = org.id
            for rec in db.execute(select(PriceRecord).where(PriceRecord.tariff_id == primary.id)).scalars():
                rec.provider_id = borg.id
            # Clone to المختبر
            t2 = clone_tariff_for(db, primary, mokh)
            recs = db.execute(select(PriceRecord).where(PriceRecord.tariff_id == primary.id)).scalars().all()
            cloned_here = 0
            for rec in recs:
                exists = db.execute(
                    select(PriceRecord.id).where(
                        PriceRecord.tariff_id == t2.id,
                        PriceRecord.original_name == rec.original_name,
                        PriceRecord.original_code == rec.original_code,
                    )
                ).scalar_one_or_none()
                if exists:
                    continue
                clone_record(db, rec, t2.id, mokh.id)
                cloned_here += 1
                n_cloned += 1
            print(f"  {d.original_filename}: borg_recs={len(recs)} mokhtabar_new={cloned_here}")

        db.commit()
        print("cloned records total", n_cloned)
        print("providers", db.query(Provider).count())
        print("price_records", db.query(PriceRecord).count())
    finally:
        db.close()


if __name__ == "__main__":
    main()
