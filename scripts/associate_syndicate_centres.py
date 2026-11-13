"""Attach the syndicate 2026 أشعة/تحاليل lists to مركز الطاهرة and مركز النيل للاشعه.

The identical PDFs sit in three folders (نقابة الاطباء 2026, مركز الطاهرة,
مركز النيل للاشعه) — one shared tariff, three centres. Owner rule: the centre
is named after the folder that holds the price list. The records already live
under نقابة الاطباء 2026; this clones a parallel tariff per centre so price
comparison shows all three at the same numbers.

Idempotent: re-running changes nothing. The original records are not modified.
"""
import importlib.util
import sys

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    Organization,
    PriceRecord,
    Provider,
    SourceDocument,
    Tariff,
)

# scripts/ is not a package — load the clone helpers by path
_spec = importlib.util.spec_from_file_location(
    "associate_labs", r"E:\MedRAG\scripts\associate_labs.py"
)
_mod = importlib.util.module_from_spec(_spec)
import sys as _sys

_sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
clone_record = _mod.clone_record
clone_tariff_for = _mod.clone_tariff_for

ORG_NAME = "نقابة الأطباء المصرية"
SYNDICATE_PROVIDER = "نقابة الاطباء 2026"

# centre name -> the source documents (owned by the syndicate provider) that
# carry the tariff this centre reports
CENTRES = {
    "مركز الطاهرة": ["أشعة 2026 قاهرة.pdf", "تحاليل 2026 قاهرة.pdf"],
    "مركز النيل للاشعه": ["أشعة 2026 قاهرة.pdf", "تحاليل 2026 قاهرة.pdf"],
}


def get_or_create_org(db: Session, name: str) -> Organization:
    org = db.execute(select(Organization).where(Organization.name == name)).scalar_one_or_none()
    if not org:
        org = Organization(name=name, org_type="syndicate", country="Egypt", region="Cairo")
        db.add(org)
        db.flush()
    return org


def get_or_create_provider(db: Session, name: str, org_id: str) -> Provider:
    p = db.execute(select(Provider).where(Provider.name == name)).scalar_one_or_none()
    if not p:
        p = Provider(
            name=name,
            name_ar=name,
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


def main() -> None:
    db: Session = SessionLocal()
    try:
        org = get_or_create_org(db, ORG_NAME)
        syndicate = db.execute(
            select(Provider).where(Provider.name == SYNDICATE_PROVIDER)
        ).scalar_one_or_none()
        if not syndicate:
            print(f"source provider {SYNDICATE_PROVIDER!r} not found — run associate_syndicate.py first")
            return

        for centre, filenames in CENTRES.items():
            provider = get_or_create_provider(db, centre, org.id)
            docs = db.execute(
                select(SourceDocument).where(
                    SourceDocument.provider_id == syndicate.id,
                    SourceDocument.original_filename.in_(filenames),
                )
            ).scalars().all()
            print(f"centre={centre} provider={provider.id} source_docs={[d.original_filename for d in docs]}")
            total = 0
            for d in docs:
                for t in db.execute(select(Tariff).where(Tariff.document_id == d.id)).scalars():
                    if t.provider_id != syndicate.id:
                        continue  # already a clone for another centre
                    new_t = clone_tariff_for(db, t, provider)
                    recs = db.execute(
                        select(PriceRecord).where(PriceRecord.tariff_id == t.id)
                    ).scalars().all()
                    for rec in recs:
                        clone_record(db, rec, new_t.id, provider.id)
                    total += len(recs)
            print(f"  cloned {total} records under {centre}")
        db.commit()

        for centre in CENTRES:
            p = db.execute(select(Provider).where(Provider.name == centre)).scalar_one()
            n = db.query(PriceRecord).filter(PriceRecord.provider_id == p.id).count()
            print(f"verify {centre}: {n} price records")
    finally:
        db.close()


if __name__ == "__main__":
    main()
