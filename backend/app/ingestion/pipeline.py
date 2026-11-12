"""Ingestion pipeline: register → parse → xref → persist → report.

Accuracy rules enforced here:
* missing prices are not coerced to zero
* codes are not mistaken for prices
* original names are preserved
* low-confidence rows go to the review queue, not silently into the approved catalog
* every price keeps page / sheet / cell provenance
"""
from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.embeddings import get_embedder, pack_vector
from app.ingestion.base import ParsedDoc, RawRow, file_checksum, sniff_format, split_name_bilingual
from app.ingestion.office_parsers import parse_csv, parse_docx, parse_image, parse_txt_or_html, parse_xlsx
from app.ingestion.pdf_parsers import parse_pdf
from app.models import (
    Examination,
    IngestionReport,
    Organization,
    PriceRecord,
    PriceValue,
    PricingModifier,
    Provider,
    ReviewItem,
    SourceDocument,
    Tariff,
)
from app.normalization import extract_attributes, normalize_query
from app.xref import XRefEngine

PARSE_THRESHOLD = 0.55  # below → review queue
MIN_NAME_LEN = 3
MIN_PRICE = 0  # 0 is a valid price only if the source literally has 0; we still keep it


class IngestionPipeline:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.xref = XRefEngine(db)
        self.embedder = get_embedder()

    # ------------------------------------------------------------------
    def get_or_create_organization(self, name: str | None, org_type: str | None = None) -> Organization | None:
        if not name:
            return None
        name = name.strip()
        org = self.db.execute(
            select(Organization).where(Organization.name == name)
        ).scalar_one_or_none()
        if not org:
            org = Organization(name=name, org_type=org_type or "other", country=settings.DEFAULT_COUNTRY)
            self.db.add(org)
            self.db.flush()
        return org

    def get_or_create_provider(
        self,
        name: str | None,
        organization_id: str | None = None,
        city: str | None = None,
        governorate: str | None = None,
        provider_type: str | None = None,
        branch_name: str | None = None,
    ) -> Provider | None:
        if not name:
            return None
        name = name.strip()
        prov = self.db.execute(
            select(Provider).where(Provider.name == name, Provider.branch_name == (branch_name or ""))
        ).scalar_one_or_none()
        if not prov:
            prov = Provider(
                name=name,
                organization_id=organization_id,
                city=city,
                governorate=governorate or city,
                provider_type=provider_type,
                branch_name=branch_name,
            )
            self.db.add(prov)
            self.db.flush()
        elif organization_id and not prov.organization_id:
            prov.organization_id = organization_id
        return prov

    # ------------------------------------------------------------------
    def register_file(self, path: Path, *, original_filename: str | None = None) -> tuple[SourceDocument, bool]:
        """Returns (document, is_duplicate)."""
        checksum = file_checksum(path)
        existing = self.db.execute(
            select(SourceDocument).where(SourceDocument.checksum == checksum)
        ).scalar_one_or_none()
        if existing:
            return existing, True

        fmt = sniff_format(original_filename or path.name)
        dest = Path(settings.UPLOAD_DIR) / f"{checksum[:16]}_{path.name}"
        if path.resolve() != dest.resolve() and not dest.exists():
            shutil.copy2(path, dest)

        doc = SourceDocument(
            original_filename=original_filename or path.name,
            file_format=fmt,
            file_size=path.stat().st_size,
            checksum=checksum,
            title=path.stem,
            storage_path=str(dest),
            extraction_status="processing",
        )
        self.db.add(doc)
        self.db.flush()
        return doc, False

    # ------------------------------------------------------------------
    def parse(self, doc: SourceDocument) -> ParsedDoc:
        path = Path(doc.storage_path)
        fmt = doc.file_format
        if fmt == "pdf":
            parsed = parse_pdf(path)
        elif fmt in {"xlsx", "xls"}:
            parsed = parse_xlsx(path)
        elif fmt == "csv":
            parsed = parse_csv(path)
        elif fmt == "docx":
            parsed = parse_docx(path)
        elif fmt == "image":
            parsed = parse_image(path)
        elif fmt in {"txt", "html"}:
            parsed = parse_txt_or_html(path)
        else:
            parsed = ParsedDoc(rows=[], title=doc.title, warnings=[f"unsupported format {fmt}"])
        return parsed

    # ------------------------------------------------------------------
    def apply_metadata(self, doc: SourceDocument, parsed: ParsedDoc) -> tuple[Provider | None, Organization | None]:
        org = self.get_or_create_organization(parsed.organization_hint)
        # Provider may be unknown — explicit field, admin can fix later.
        prov = None
        if parsed.provider_hint:
            prov = self.get_or_create_provider(
                parsed.provider_hint,
                organization_id=org.id if org else None,
                city=parsed.city_hint,
                governorate=parsed.city_hint,
                provider_type=self._provider_type(parsed),
            )
        doc.organization_id = org.id if org else None
        doc.provider_id = prov.id if prov else None
        if parsed.year_hint:
            doc.tariff_year = parsed.year_hint
        if parsed.title:
            doc.title = parsed.title
        if parsed.page_count:
            doc.page_count = parsed.page_count
        doc.geographic_scope = parsed.geographic_scope or doc.geographic_scope
        if parsed.effective_date:
            try:
                doc.effective_date = date.fromisoformat(parsed.effective_date)
            except Exception:
                pass
        if parsed.expiration_date:
            try:
                doc.expiration_date = date.fromisoformat(parsed.expiration_date)
            except Exception:
                pass
        self.db.flush()
        return prov, org

    def _provider_type(self, parsed: ParsedDoc) -> str | None:
        cats = {r.category for r in parsed.rows if r.category}
        if "Radiology" in cats:
            return "radiology"
        if "Laboratory" in cats:
            return "lab"
        if "Dental" in cats:
            return "dental"
        return "hospital"

    # ------------------------------------------------------------------
    def persist_rows(self, doc: SourceDocument, parsed: ParsedDoc, provider: Provider | None, org: Organization | None) -> dict[str, Any]:
        tariff = Tariff(
            provider_id=provider.id if provider else None,
            organization_id=org.id if org else None,
            document_id=doc.id,
            title=parsed.title or doc.title,
            year=parsed.year_hint or doc.tariff_year,
            effective_date=doc.effective_date,
            expiration_date=doc.expiration_date,
            geographic_scope=parsed.geographic_scope or "Egypt",
            currency=parsed.currency,
            status="active",
        )
        self.db.add(tariff)
        self.db.flush()

        stats = {
            "raw_rows": len(parsed.rows),
            "price_records": 0,
            "price_values": 0,
            "needs_review": 0,
            "unmatched_names": 0,
            "proposed_aliases": 0,
            "new_examinations": 0,
            "skipped": 0,
        }
        issues: list[dict] = []
        seen_names: dict[str, int] = {}

        for i, raw in enumerate(parsed.rows):
            name = (raw.name or "").strip()
            if not name or len(name) < MIN_NAME_LEN or not normalize_query(name):
                stats["skipped"] += 1
                continue
            if not raw.prices and not raw.modifiers:
                # name without price — still useful as alias evidence, no price record
                res = self.xref.resolve(name, use_cache=False)
                if res.examination:
                    self.xref.propose_alias(
                        name, res.examination.id, source_document_id=doc.id,
                        source_page=raw.page, confidence=min(0.8, res.confidence + 0.1),
                    )
                    stats["proposed_aliases"] += 1
                else:
                    stats["unmatched_names"] += 1
                stats["skipped"] += 1
                continue

            # Dedup identical (name, code, first price) within doc
            # rows that only carry modifiers have no base price to key on
            first_value = raw.prices[0].value if raw.prices else "modifiers_only"
            key = normalize_query(name) + "|" + str(raw.code or "") + "|" + str(first_value)
            seen_names[key] = seen_names.get(key, 0) + 1
            if seen_names[key] > 1:
                stats["skipped"] += 1
                issues.append({"type": "duplicate_row", "name": name, "page": raw.page})
                continue

            res = self.xref.resolve(name, use_cache=False)
            if not res.examination and raw.name_en and raw.name_en != name:
                res = self.xref.resolve(raw.name_en, use_cache=False)
            if not res.examination and raw.name_ar and raw.name_ar != name:
                res = self.xref.resolve(raw.name_ar, use_cache=False)
            # Mixed bilingual source names: try the Latin half even when the
            # full string failed, and vice versa.
            if not res.examination:
                ar_part, en_part = split_name_bilingual(name)
                if en_part and en_part != raw.name_en:
                    res = self.xref.resolve(en_part, use_cache=False)
                if not res.examination and ar_part and ar_part != raw.name_ar:
                    res = self.xref.resolve(ar_part, use_cache=False)
            exam = res.examination
            attrs = extract_attributes(name)
            merged_attrs: dict = {}
            if exam and exam.attributes:
                merged_attrs.update(exam.attributes)
            merged_attrs.update(attrs)
            if raw.attributes:
                merged_attrs.update(raw.attributes)

            conf = raw.confidence
            if exam:
                conf = max(conf, min(0.9, res.confidence))

            # A priced row is always stored. Unresolved identity goes to review,
            # it is never silently dropped and never invented as a new exam
            # when a strong candidate already exists.
            if not exam:
                from app.services.catalog import next_canonical_code
                ar, en = raw.name_ar, raw.name_en
                if not ar and not en:
                    ar, en = split_name_bilingual(name)
                exam = Examination(
                    canonical_code=next_canonical_code(self.db, raw.category or "Other"),
                    name_en=en or name,
                    name_ar=ar,
                    category=raw.category,
                    subcategory=raw.subcategory,
                    attributes=merged_attrs,
                )
                self.db.add(exam)
                self.db.flush()
                exam.embedding = pack_vector(self.embedder.embed_one(f"{exam.name_en} {exam.name_ar} {name}"))
                exam.embedding_model = self.embedder.name
                stats["new_examinations"] += 1
                res_strategy = "new_from_source"
                # proposed alias so the admin can merge later
                self.xref.propose_alias(
                    name, exam.id, source_document_id=doc.id, source_page=raw.page,
                    confidence=0.6, auto_approve=False,
                )
                stats["proposed_aliases"] += 1

            # Alias
            alias_conf = 0.9 if res.strategy in ("exact_alias", "collapsed_alias", "canonical_name", "acronym") else 0.65
            self.xref.propose_alias(
                name, exam.id, source_document_id=doc.id, source_page=raw.page,
                confidence=alias_conf, auto_approve=res.confidence >= 0.92,
            )
            if res.confidence < 0.92:
                stats["proposed_aliases"] += 1

            rec = PriceRecord(
                examination_id=exam.id,
                provider_id=provider.id if provider else None,
                tariff_id=tariff.id,
                original_name=name,
                original_code=raw.code,
                name_ar=raw.name_ar or exam.name_ar,
                name_en=raw.name_en or exam.name_en,
                attributes=merged_attrs,
                category=exam.category or raw.category,
                subcategory=raw.subcategory or exam.subcategory,
                currency=raw.prices[0].currency if raw.prices else "EGP",
                conditions=raw.conditions,
                source_page=raw.page,
                source_sheet=raw.sheet,
                source_cell=raw.cell,
                extraction_confidence=round(conf, 3),
                review_status="auto" if conf >= 0.7 else "needs_review",
            )
            self.db.add(rec)
            self.db.flush()

            for p in raw.prices:
                self.db.add(PriceValue(
                    price_record_id=rec.id,
                    price_category=p.category,
                    value=p.value,
                    currency=p.currency,
                    modifier_type=p.modifier_type,
                    conditions=p.conditions,
                ))
                stats["price_values"] += 1
            for m in raw.modifiers:
                self.db.add(PricingModifier(
                    price_record_id=rec.id,
                    mod_type=m.mod_type,
                    label=m.label,
                    value=m.value,
                    is_percentage=m.is_percentage,
                    conditions=m.conditions,
                    source_page=m.source_page or raw.page,
                ))

            if conf < PARSE_THRESHOLD:
                rec.review_status = "needs_review"
                self._queue_review(
                    "price_record", rec.id, "low_confidence",
                    {"name": name, "confidence": conf, "page": raw.page},
                    priority=4,
                )
                stats["needs_review"] += 1
            elif not provider:
                # Searchable with "Provider not specified"; admin can associate later.
                rec.review_status = "auto"
            stats["price_records"] += 1

        # conflicting codes / multiple prices same exam+provider
        self._post_validate(tariff, provider, stats, issues)
        return stats, issues

    def _post_validate(self, tariff: Tariff, provider: Provider | None, stats: dict, issues: list) -> None:
        # same exam, same provider, wildly different prices
        from sqlalchemy import func
        rows = self.db.execute(
            select(PriceRecord.examination_id, PriceValue.value)
            .join(PriceValue, PriceValue.price_record_id == PriceRecord.id)
            .where(PriceRecord.tariff_id == tariff.id)
        ).all()
        by_exam: dict[str, list[float]] = {}
        for eid, val in rows:
            if eid and val is not None:
                by_exam.setdefault(eid, []).append(float(val))
        for eid, vals in by_exam.items():
            if len(vals) >= 2:
                lo, hi = min(vals), max(vals)
                if lo > 0 and hi / lo >= 8:
                    issues.append({
                        "type": "conflicting_prices",
                        "examination_id": eid,
                        "min": lo,
                        "max": hi,
                    })
                    self._queue_review(
                        "price_record", eid, "conflicting_prices",
                        {"min": lo, "max": hi}, priority=2,
                    )

    def _queue_review(self, entity_type: str, entity_id: str, reason: str, detail: dict | None, priority: int = 5) -> None:
        self.db.add(ReviewItem(
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            detail=detail,
            priority=priority,
        ))

    # ------------------------------------------------------------------
    def ingest_file(self, path: Path, *, original_filename: str | None = None) -> dict[str, Any]:
        doc, dup = self.register_file(path, original_filename=original_filename)
        # Persist the registration before the risky part: a parser or persistence
        # failure must leave a document row behind, not erase it.
        self.db.commit()
        if dup:
            return {
                "document_id": doc.id,
                "duplicate": True,
                "message": f"Checksum already ingested: {doc.original_filename}",
            }
        try:
            parsed = self.parse(doc)
            provider, org = self.apply_metadata(doc, parsed)
            stats, issues = self.persist_rows(doc, parsed, provider, org)
            self.xref.invalidate()
            doc.extraction_status = "completed" if stats["price_records"] else "partial"
            if parsed.needs_ocr_pages and not parsed.rows:
                doc.extraction_status = "partial"
            doc.validation_status = "auto_validated" if stats["needs_review"] == 0 else "needs_review"
            report = IngestionReport(
                document_id=doc.id,
                stats={**stats, "parser": parsed.parser, "pages": parsed.page_count,
                       "needs_ocr_pages": parsed.needs_ocr_pages[:50]},
                issues=issues[:200],
                warnings=parsed.warnings[:100],
            )
            self.db.add(report)
            self.db.commit()
            return {
                "document_id": doc.id,
                "duplicate": False,
                "parser": parsed.parser,
                "title": doc.title,
                "organization": org.name if org else None,
                "provider": provider.name if provider else None,
                "stats": stats,
                "issues": issues[:20],
                "warnings": parsed.warnings[:10],
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.db.rollback()
            doc.extraction_status = "failed"
            self.db.add(IngestionReport(
                document_id=doc.id,
                stats={"error": str(e)},
                issues=[{"type": "exception", "message": str(e)}],
                warnings=[],
            ))
            self.db.commit()
            return {"document_id": doc.id, "error": str(e), "duplicate": False}

    def ingest_directory(self, dir_path: Path, recursive: bool = True) -> list[dict[str, Any]]:
        results = []
        pattern = "**/*" if recursive else "*"
        files = [p for p in dir_path.glob(pattern) if p.is_file() and p.suffix.lower().lstrip(".") in settings.allowed_ext_list]
        # deterministic order
        files.sort(key=lambda p: str(p))
        for p in files:
            r = self.ingest_file(p, original_filename=p.name)
            r["path"] = str(p)
            results.append(r)
        return results
