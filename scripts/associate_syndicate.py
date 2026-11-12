"""Attach the نقابة الاطباء 2026 price lists to a centre named after their folder.

Owner rule: the centre name is the name of the folder that contains the price
list. These documents were ingested without a provider, so they currently render
as "Provider not specified"; this fills that gap and does not overwrite any
existing centre assignment.

Idempotent: re-running changes nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Organization, PriceRecord, Provider, SourceDocument, Tariff

FOLDER = "نقابة الاطباء 2026"
FOLDER_PATH = Path(r"E:\MedRAG") / FOLDER
ORG_NAME = "نقابة الأطباء المصرية"


def get_or_create_org(db) -> Organization:
    org = db.execute(select(Organization).where(Organization.name == ORG_NAME)).scalar_one_or_none()
    if not org:
        org = Organization(name=ORG_NAME, org_type="other", country="Egypt", region="Cairo")
        db.add(org)
        db.flush()
    return org


def get_or_create_provider(db, org_id: str) -> Provider:
    p = db.execute(select(Provider).where(Provider.name == FOLDER)).scalar_one_or_none()
    if not p:
        p = Provider(
            name=FOLDER,
            name_ar=FOLDER,
            organization_id=org_id,
            provider_type="syndicate",
            city="القاهرة",
            governorate="القاهرة",
        )
        db.add(p)
        db.flush()
    elif not p.organization_id:
        p.organization_id = org_id
    return p


def folder_filenames() -> set[str]:
    if not FOLDER_PATH.is_dir():
        return set()
    return {p.name for p in FOLDER_PATH.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"}


def main() -> None:
    db = SessionLocal()
    try:
        org = get_or_create_org(db)
        provider = get_or_create_provider(db, org.id)
        wanted = folder_filenames()
        print(f"folder={FOLDER} org={org.id} provider={provider.id} folder_files={len(wanted)}")

        docs = db.execute(select(SourceDocument)).scalars().all()
        matched, skipped = [], []
        for d in docs:
            if d.original_filename in wanted:
                matched.append(d)
            elif d.original_filename in {
                "(C) داخلي 2026.pdf",
                "أشعة 2026 قاهرة.pdf",
                "تحاليل 2026 قاهرة.pdf",
                "عيادات خارجية  2026 القاهرة.pdf",
                "الكشف الطبي القاهرة 2026.pdf",
            }:
                matched.append(d)
            else:
                skipped.append(d.original_filename)

        print("matched:", [d.original_filename for d in matched])

        for d in matched:
            d.provider_id = provider.id
            d.organization_id = org.id
            for t in db.execute(select(Tariff).where(Tariff.document_id == d.id)).scalars():
                t.provider_id = provider.id
                t.organization_id = org.id
                for rec in db.execute(
                    select(PriceRecord).where(PriceRecord.tariff_id == t.id)
                ).scalars():
                    rec.provider_id = provider.id
        db.commit()

        # verify
        from app.reporting import REPORTED_CATEGORIES

        print("\n=== categories now stored for this centre ===")
        rows = db.execute(
            select(PriceRecord,)
            .where(PriceRecord.provider_id == provider.id)
            .limit(1)
        ).scalars().all()
        print("price records under centre:", db.query(PriceRecord).filter(
            PriceRecord.provider_id == provider.id).count())
        print("reported categories:", {FOLDER: sorted(REPORTED_CATEGORIES[FOLDER])})
        print("docs without provider afterwards:",
              [d.original_filename for d in docs if d.provider_id is None])
    finally:
        db.close()


if __name__ == "__main__":
    main()
