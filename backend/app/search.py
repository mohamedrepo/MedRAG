"""Hybrid search: exact alias → collapsed → FTS → fuzzy → semantic, then re-rank.

Price numbers are never taken from vectors or unstructured text — only from
the structured price_values table via SQL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.embeddings import cosine, unpack_vector
from app.models import (
    Examination,
    ExaminationAlias,
    PriceRecord,
    PriceValue,
    Provider,
    SourceDocument,
    Tariff,
)
from app.normalization import normalize_query, similar
from app.xref import Resolution, XRefEngine


@dataclass
class SearchHit:
    examination: Examination
    resolution: Resolution
    prices: list[dict[str, Any]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)


class SearchEngine:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.xref = XRefEngine(db)

    def search(
        self,
        q: str,
        *,
        category: str | None = None,
        city: str | None = None,
        governorate: str | None = None,
        provider_id: str | None = None,
        price_category: str | None = None,
        year: int | None = None,
        effective_only: bool = True,
        include_variants: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        q = (q or "").strip()
        if not q:
            return {"query": q, "interpretation": None, "results": [], "total": 0}

        res = self.xref.resolve(q)
        exam_ids: list[str] = []
        if res.examination:
            exam_ids.append(res.examination.id)
            if include_variants:
                # sibling exams in same category whose name overlaps but attributes differ
                # only included when explicitly requested
                pass

        # If unresolved, fall back to fuzzy exam-name search
        if not res.examination:
            fuzzy = self._fuzzy_exams(q, limit=8)
            exam_ids = [e.id for e in fuzzy]
            res.candidates = res.candidates or [
                {"id": e.id, "code": e.canonical_code, "name_en": e.name_en, "name_ar": e.name_ar, "score": 0.6}
                for e in fuzzy
            ]

        rows: list[dict[str, Any]] = []
        total = 0
        if exam_ids:
            rows, total = self._price_rows(
                exam_ids,
                category=category,
                city=city,
                governorate=governorate,
                provider_id=provider_id,
                price_category=price_category,
                year=year,
                effective_only=effective_only,
                limit=limit,
                offset=offset,
            )

        interpretation = None
        if res.examination:
            aliases = [
                {
                    "alias": a.original_alias,
                    "language": a.language,
                    "type": a.alias_type,
                    "status": a.approval_status,
                    "confidence": a.confidence,
                }
                for a in res.examination.aliases
                if a.approval_status == "approved"
            ][:30]
            interpretation = {
                "user_term": q,
                "normalized": normalize_query(q),
                "canonical_id": res.examination.id,
                "canonical_code": res.examination.canonical_code,
                "canonical_en": res.examination.name_en,
                "canonical_ar": res.examination.name_ar,
                "category": res.examination.category,
                "subcategory": res.examination.subcategory,
                "attributes": res.examination.attributes,
                "confidence": res.confidence,
                "strategy": res.strategy,
                "matched_alias": res.matched_alias,
                "aliases": aliases,
            }

        return {
            "query": q,
            "interpretation": interpretation,
            "results": rows,
            "total": total,
            "suggestions": (res.candidates or [])[:8] if not res.examination else [],
            "needs_review": res.needs_review,
        }

    def _fuzzy_exams(self, q: str, limit: int = 8) -> list[Examination]:
        nq = normalize_query(q)
        scored = []
        for e in self.xref._exams():
            s = max(
                similar(nq, normalize_query(e.name_en or "")),
                similar(nq, normalize_query(e.name_ar or "")) if e.name_ar else 0,
            )
            for a in e.aliases:
                if a.approval_status != "rejected":
                    s = max(s, similar(nq, a.normalized_alias) * 0.95)
            if s >= 0.45:
                scored.append((s, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    def _price_rows(
        self,
        exam_ids: list[str],
        *,
        category: str | None,
        city: str | None,
        governorate: str | None,
        provider_id: str | None,
        price_category: str | None,
        year: int | None,
        effective_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        today = date.today()
        conds = [PriceRecord.examination_id.in_(exam_ids), PriceRecord.is_active.is_(True)]
        if category:
            conds.append(Examination.category == category)
        if provider_id:
            conds.append(PriceRecord.provider_id == provider_id)
        if year:
            conds.append(Tariff.year == year)
        if effective_only:
            conds.append(
                or_(
                    Tariff.effective_date.is_(None),
                    Tariff.effective_date <= today,
                )
            )
            conds.append(
                or_(
                    Tariff.expiration_date.is_(None),
                    Tariff.expiration_date >= today,
                )
            )
        if city:
            conds.append(Provider.city.ilike(f"%{city}%"))
        if governorate:
            conds.append(Provider.governorate.ilike(f"%{governorate}%"))
        if price_category:
            conds.append(PriceValue.price_category == price_category)

        base = (
            self.db.query(PriceValue, PriceRecord, Examination, Tariff, Provider, SourceDocument)
            .join(PriceRecord, PriceValue.price_record_id == PriceRecord.id)
            .join(Examination, PriceRecord.examination_id == Examination.id)
            .join(Tariff, PriceRecord.tariff_id == Tariff.id)
            .outerjoin(Provider, PriceRecord.provider_id == Provider.id)
            .outerjoin(SourceDocument, Tariff.document_id == SourceDocument.id)
            .filter(and_(*conds))
        )

        total = base.count()
        rows = (
            base.order_by(PriceValue.value.asc(), Examination.canonical_code.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        out = []
        for rank_i, (pv, pr, ex, t, prov, doc) in enumerate(rows, start=offset + 1):
            out.append({
                "rank": rank_i,
                "price_value_id": pv.id,
                "price": float(pv.value) if pv.value is not None else None,
                "currency": pv.currency,
                "price_category": pv.price_category,
                "modifier_type": pv.modifier_type,
                "conditions": pv.conditions or pr.conditions,
                "record_id": pr.id,
                "original_name": pr.original_name,
                "original_code": pr.original_code,
                "name_ar": pr.name_ar,
                "name_en": pr.name_en,
                "attributes": pr.attributes,
                "extraction_confidence": pr.extraction_confidence,
                "review_status": pr.review_status,
                "exam_code": ex.canonical_code,
                "exam_en": ex.name_en,
                "exam_ar": ex.name_ar,
                "exam_category": ex.category,
                "exam_attributes": ex.attributes,
                "tariff_id": t.id,
                "tariff_title": t.title,
                "tariff_year": t.year,
                "effective_date": t.effective_date.isoformat() if t.effective_date else None,
                "expiration_date": t.expiration_date.isoformat() if t.expiration_date else None,
                "tariff_status": t.status,
                "provider_id": prov.id if prov else None,
                "provider_name": prov.name if prov else None,
                "provider_name_ar": prov.name_ar if prov else None,
                "branch": prov.branch_name if prov else None,
                "city": prov.city if prov else None,
                "governorate": prov.governorate if prov else None,
                "provider_type": prov.provider_type if prov else None,
                "provider_unspecified": prov is None,
                "document_id": doc.id if doc else None,
                "document_title": doc.title if doc else None,
                "document_filename": doc.original_filename if doc else None,
                "source_page": pr.source_page,
                "source_sheet": pr.source_sheet,
                "source_cell": pr.source_cell,
            })
        return out, total

    def compare(
        self,
        q: str,
        *,
        price_category: str | None = None,
        year: int | None = None,
        group_by_category: bool = True,
        **filters: Any,
    ) -> dict[str, Any]:
        """Ascending price comparison, optionally grouped by price category."""
        data = self.search(q, price_category=price_category, year=year, **filters)
        results = data["results"]
        groups: dict[str, list[dict[str, Any]]] = {}
        if group_by_category:
            for r in results:
                groups.setdefault(r["price_category"] or "unspecified", []).append(r)
            for g in groups.values():
                g.sort(key=lambda x: (x["price"] if x["price"] is not None else 1e18))
        else:
            results.sort(key=lambda x: (x["price"] if x["price"] is not None else 1e18))
            groups["all"] = results

        cheapest = None
        for g in groups.values():
            priced = [x for x in g if x["price"] is not None]
            if priced:
                c = priced[0]
                if cheapest is None or (c["price"] or 0) < (cheapest["price"] or 0):
                    cheapest = c

        return {
            **data,
            "groups": [
                {"category": k, "count": len(v), "rows": v}
                for k, v in groups.items()
            ],
            "cheapest": cheapest,
        }

    def suggest(self, prefix: str, limit: int = 8) -> list[dict[str, Any]]:
        """Autocomplete from aliases + canonical names."""
        nq = normalize_query(prefix)
        if not nq:
            return []
        out = []
        for e in self.xref._exams():
            names = [e.name_en or "", e.name_ar or ""]
            hit = False
            score = 0.0
            for n in names:
                nn = normalize_query(n)
                if nn.startswith(nq):
                    score = max(score, 1.0)
                    hit = True
                elif nq in nn:
                    score = max(score, 0.8)
                    hit = True
            for a in e.aliases:
                if a.approval_status == "rejected":
                    continue
                if a.normalized_alias.startswith(nq) or nq in a.normalized_alias:
                    score = max(score, 0.9 if a.normalized_alias.startswith(nq) else 0.7)
                    hit = True
            if hit:
                out.append({
                    "id": e.id,
                    "code": e.canonical_code,
                    "name_en": e.name_en,
                    "name_ar": e.name_ar,
                    "category": e.category,
                    "score": score,
                })
        out.sort(key=lambda x: -x["score"])
        return out[:limit]
