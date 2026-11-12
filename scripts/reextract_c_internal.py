"""(C) داخلي 2026 re-extraction.

The first ingestion read this file's cost-component columns (surgeon,
anesthesia, assistants, OR room) as if they were member/parents/project price
categories, which produced impossible values and 1,155 wrongly-labelled prices.

This script removes that document's derived rows and re-ingests it with the
component parser, so the four amounts become pricing_modifiers and the file
contributes no member price (it has none).

The original PDF is never modified. A database backup is taken first.
"""
from __future__ import annotations

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
    Provider,
    SourceDocument,
    Tariff,
)

FILENAME = "(C) داخلي 2026.pdf"
FOLDER = "نقابة الاطباء 2026"
FALLBACK_PATH = Path(r"E:\MedRAG") / FOLDER / FILENAME


def main() -> None:
    db = SessionLocal()
    try:
        doc = db.execute(
            select(SourceDocument).where(SourceDocument.original_filename == FILENAME)
        ).scalar_one_or_none()
        if doc is None:
            # an earlier failed run already removed the row: register it again
            path = FALLBACK_PATH
            doc_id = None
            print(f"document row missing — re-registering from {path}")
        else:
            path = Path(doc.storage_path)
            doc_id = doc.id
        if not path.is_file():
            print(f"stored file missing: {path}")
            return

        if doc_id is not None:
            print("backing up database first…")
            subprocess.run([sys.executable, r"E:\MedRAG\scripts\backup.py"], check=True)

        tariff_ids = (
            [t.id for t in db.execute(select(Tariff).where(Tariff.document_id == doc_id)).scalars()]
            if doc_id else []
        )
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
        if doc_id is not None:
            db.execute(delete(IngestionReport).where(IngestionReport.document_id == doc_id))
            db.execute(delete(SourceDocument).where(SourceDocument.id == doc_id))
        db.commit()

        print("re-ingesting with the component parser…")
        result = IngestionPipeline(SessionLocal()).ingest_file(path, original_filename=FILENAME)
        print("result:", {k: result.get(k) for k in ("parser", "stats", "duplicate", "error")})

        # re-attach the centre (folder name) to the new document
        db2 = SessionLocal()
        try:
            new_doc = db2.execute(
                select(SourceDocument).where(SourceDocument.original_filename == FILENAME)
            ).scalar_one()
            provider = db2.execute(
                select(Provider).where(Provider.name == FOLDER)
            ).scalar_one_or_none()
            if provider:
                new_doc.provider_id = provider.id
                new_doc.organization_id = provider.organization_id
                for t in db2.execute(select(Tariff).where(Tariff.document_id == new_doc.id)).scalars():
                    t.provider_id = provider.id
                    for rec in db2.execute(
                        select(PriceRecord).where(PriceRecord.tariff_id == t.id)
                    ).scalars():
                        rec.provider_id = provider.id
                db2.commit()
                print(f"attached to centre: {FOLDER}")

            recs = db2.execute(
                select(PriceRecord).where(
                    PriceRecord.tariff_id.in_(
                        select(Tariff.id).where(Tariff.document_id == new_doc.id)
                    )
                )
            ).scalars().all()
            mods = 0
            vals = 0
            for r in recs:
                mods += len(r.modifiers)
                vals += len(r.values)
            print(f"records={len(recs)} price_values={vals} pricing_modifiers={mods}")
            for r in recs[:5]:
                print("  ", r.original_name[:40], "| prices:", len(r.values),
                      "| mods:", {m.mod_type: float(m.value) for m in r.modifiers})
        finally:
            db2.close()
    finally:
        db.close()


if __name__ == "__main__":
    main()
