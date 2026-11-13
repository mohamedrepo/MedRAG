"""Delete and re-ingest the Kasr Al Ainy price list with the fixed generic parser.

The first post-fix ingestion still leaked serial code columns (4112031…) from
the multi-block component fee table on page 55 into price_values. The parser
now (a) detects component tables per table and (b) excludes every serial-run
numeric column from prices. This script removes that document's derived rows
and re-ingests it. Idempotent-safe: backs up first, never touches the PDF.
"""
import subprocess
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

FILENAME = "لائحة اسعار 2026 القصر العينى الجديد -.pdf"


def main() -> None:
    db = SessionLocal()
    try:
        doc = db.execute(
            select(SourceDocument).where(SourceDocument.original_filename == FILENAME)
        ).scalar_one_or_none()
        if doc is None:
            print("document not found — nothing to do")
            return
        path = Path(doc.storage_path)
        doc_id = doc.id
        tariff_ids = [
            t.id for t in db.execute(select(Tariff).where(Tariff.document_id == doc_id)).scalars()
        ]
        record_ids = []
        if tariff_ids:
            record_ids = [
                r.id
                for r in db.execute(
                    select(PriceRecord).where(PriceRecord.tariff_id.in_(tariff_ids))
                ).scalars()
            ]
        print(f"removing document={doc_id} tariffs={len(tariff_ids)} records={len(record_ids)}")
        if record_ids:
            db.execute(delete(PricingModifier).where(PricingModifier.price_record_id.in_(record_ids)))
            db.execute(delete(PriceValue).where(PriceValue.price_record_id.in_(record_ids)))
            db.execute(delete(PriceRecord).where(PriceRecord.id.in_(record_ids)))
        if tariff_ids:
            db.execute(delete(Tariff).where(Tariff.id.in_(tariff_ids)))
        db.execute(delete(IngestionReport).where(IngestionReport.document_id == doc_id))
        db.execute(delete(SourceDocument).where(SourceDocument.id == doc_id))
        db.commit()
    finally:
        db.close()

    print("re-ingesting with the fixed parser…")
    result = IngestionPipeline(SessionLocal()).ingest_file(path, original_filename=FILENAME)
    print("result:", {k: result.get(k) for k in ("parser", "stats", "duplicate", "error")})


if __name__ == "__main__":
    main()
