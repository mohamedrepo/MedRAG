"""Re-ingest the three syndicate lists with the fixed category mapping.

The syndicate parser used to (a) label the 50%-share column 'member' because
the RTL-mangled header cell 'العضو' sat above it, and (b) label the 40%
column 'parents' from the 'الوالدين' label — so every record carried TWO
member values (the 50% share and the true full price) and member comparisons
picked the halved share. The parser now derives categories from percentages
and column position only, treats the rightmost numeric column as the full
member price, and skips 0 cells ('scheme does not cover').

This script deletes the three documents' derived rows, re-ingests them, then
rebuilds the centre clones that copy their records (مركز الطاهرة، مركز النيل
للاشعه، معمل البرج، معمل المختبر). Back up data/medrag.db first.
"""
import sys
from pathlib import Path

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.ingestion.pipeline import IngestionPipeline
from app.models import (
    IngestionReport,
    PriceRecord,
    PriceValue,
    PricingModifier,
    SourceDocument,
    Tariff,
)

FILENAMES = [
    "أشعة 2026 قاهرة.pdf",
    "تحاليل 2026 قاهرة.pdf",
    "عيادات خارجية  2026 القاهرة.pdf",
]

# clone providers whose records copy the re-ingested documents
CLONE_PROVIDERS = ["مركز الطاهرة", "مركز النيل للاشعه", "معمل البرج", "معمل المختبر"]


def delete_document_rows(db, doc_id: str) -> int:
    tariff_ids = [t.id for t in db.execute(select(Tariff).where(Tariff.document_id == doc_id)).scalars()]
    record_ids = []
    if tariff_ids:
        record_ids = [r.id for r in db.execute(select(PriceRecord).where(PriceRecord.tariff_id.in_(tariff_ids))).scalars()]
    if record_ids:
        db.execute(delete(PricingModifier).where(PricingModifier.price_record_id.in_(record_ids)))
        db.execute(delete(PriceValue).where(PriceValue.price_record_id.in_(record_ids)))
        db.execute(delete(PriceRecord).where(PriceRecord.id.in_(record_ids)))
    if tariff_ids:
        db.execute(delete(Tariff).where(Tariff.id.in_(tariff_ids)))
    db.execute(delete(IngestionReport).where(IngestionReport.document_id == doc_id))
    db.execute(delete(SourceDocument).where(SourceDocument.id == doc_id))
    return len(record_ids)


def delete_clone_records_for_docs(db, filenames: list[str]) -> int:
    """Remove clone-provider records whose tariff came from the given docs."""
    removed = 0
    from app.models import Provider
    for name in CLONE_PROVIDERS:
        p = db.execute(select(Provider).where(Provider.name == name)).scalar_one_or_none()
        if p is None:
            continue
        tariff_ids = [t.id for t in db.execute(select(Tariff).where(Tariff.provider_id == p.id)).scalars()]
        if not tariff_ids:
            continue
        recs = db.execute(
            select(PriceRecord)
            .join(Tariff, PriceRecord.tariff_id == Tariff.id)
            .where(Tariff.id.in_(tariff_ids), Tariff.document_id.is_not(None))
        ).scalars().all()
        doomed = [r for r in recs if r.tariff and r.tariff.document and r.tariff.document.original_filename in filenames]
        if not doomed:
            continue
        ids = [r.id for r in doomed]
        db.execute(delete(PricingModifier).where(PricingModifier.price_record_id.in_(ids)))
        db.execute(delete(PriceValue).where(PriceValue.price_record_id.in_(ids)))
        db.execute(delete(PriceRecord).where(PriceRecord.id.in_(ids)))
        removed += len(ids)
        print(f"  clone {name}: removed {len(ids)} stale records")
    return removed


def main() -> None:
    db = SessionLocal()
    try:
        print("step 1: remove clone-provider records copied from the three lists")
        n = delete_clone_records_for_docs(db, FILENAMES)
        db.commit()
        print(f"  removed {n} clone records total")

        print("step 2: delete + re-ingest the three documents")
        for filename in FILENAMES:
            doc = db.execute(
                select(SourceDocument).where(SourceDocument.original_filename == filename)
            ).scalar_one_or_none()
            if doc is None:
                print(f"  {filename}: not found, skipped")
                continue
            path = Path(doc.storage_path)
            removed = delete_document_rows(db, doc.id)
            db.commit()
            result = IngestionPipeline(SessionLocal()).ingest_file(path, original_filename=filename)
            stats = result.get("stats") or {}
            print(f"  {filename}: removed {removed}, re-ingested {stats.get('price_records')} records, "
                  f"{stats.get('price_values')} values, duplicate={result.get('duplicate')}")
    finally:
        db.close()

    print("step 3: rebuild centre clones")
    import subprocess
    for script in ("scripts/associate_syndicate_centres.py", "scripts/associate_labs.py"):
        r = subprocess.run([sys.executable, script], capture_output=True, text=True)
        tail = [l for l in r.stdout.splitlines() if l.strip()][-4:]
        print(f"  {script}: " + " | ".join(tail))


if __name__ == "__main__":
    main()
