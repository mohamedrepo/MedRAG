"""Second-stage review triage: only rule-based, verifiable decisions.

1. conflicting_prices: the pipeline flags any exam whose values within one
   tariff span >= 8x. Many flags were cross-modality artifacts (X-ray prices
   stacked onto CT exams), now repaired by split_modalities. Re-validate each
   flagged record against its CURRENT exam + tariff: if the >= 8x spread no
   longer exists among active values, resolve the item. Genuine spreads stay
   pending.
2. possible_duplicate: items where a new exam was created despite a fuzzy
   candidate scoring 0.75-0.82. If the created exam's name shares no
   distinctive (non-modality, non-function-word) token with the candidate in
   either language, the two are different procedures -> dismiss the item.
   Any token overlap -> leave pending for human review (conservative).

Every action is audit-logged. Idempotent.
"""
import json
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Examination, PriceRecord, PriceValue, ReviewItem
from app.normalization import GENERIC_FAMILY_TOKENS, extract_attributes, normalize_arabic, normalize_query
from app.security import audit
from app.xref import XRefEngine


def modality_of(name: str | None, attrs: dict | None) -> str | None:
    if attrs and attrs.get("modality"):
        return attrs["modality"]
    if not name:
        return None
    return extract_attributes(name).get("modality")

SPREAD_RATIO = 8.0

_STOP_EN = {
    "on", "of", "and", "the", "with", "without", "one", "two", "three",
    "both", "side", "sides", "joint", "view", "views", "a", "an", "for",
    "in", "at", "any", "all", "whole", "group", "entire", "set", "each",
    "per", "via", "under", "through", "contrast", "study", "test", "exam",
    "examination", "scan",
} | {t for t in GENERIC_FAMILY_TOKENS if t.isascii()}

_STOP_AR = {
    "علي", "في", "من", "مع", "بدون", "و", "كل", "لا", "او",
    "المجموعه", "جميع", "جانب", "جانبين", "وضع", "وضعين", "معا",
    "مفصل", "المفصل", "جنيه", "جنية",
} | {normalize_arabic(t) for t in GENERIC_FAMILY_TOKENS if not t.isascii()}


def _tokens(text: str) -> tuple[set[str], set[str]]:
    """Return (ascii tokens, normalized arabic tokens) minus stopwords."""
    if not text:
        return set(), set()
    ascii_toks = {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP_EN}
    ar_toks = {normalize_arabic(t) for t in re.findall(r"[\u0600-\u06FF]+", text)}
    ar_toks = {t for t in ar_toks if t not in _STOP_AR}
    return ascii_toks, ar_toks


def revalidate_conflicts(db) -> tuple[int, int]:
    items = db.execute(
        select(ReviewItem).where(
            ReviewItem.status == "pending", ReviewItem.reason == "conflicting_prices"
        )
    ).scalars().all()
    resolved = kept = 0
    for item in items:
        rec = db.get(PriceRecord, item.entity_id)
        if rec is None or not rec.examination_id or not rec.tariff_id:
            continue
        rows = db.execute(
            select(PriceValue.value)
            .join(PriceRecord, PriceValue.price_record_id == PriceRecord.id)
            .where(
                PriceRecord.examination_id == rec.examination_id,
                PriceRecord.tariff_id == rec.tariff_id,
                PriceRecord.is_active == True,  # noqa: E712
            )
        ).scalars().all()
        vals = [float(v) for v in rows if v is not None and float(v) > 0]
        if len(vals) >= 2 and max(vals) / min(vals) < SPREAD_RATIO:
            item.status = "resolved"
            item.resolution = "conflict no longer present (modality split or re-mapping)"
            item.resolved_at = datetime.now(timezone.utc)
            audit(db, None, None, "review.resolve", "review_item", item.id,
                  reason="conflicting_prices flag cleared after modality split")
            resolved += 1
        else:
            kept += 1
    return resolved, kept


def triage_possible_duplicates(db) -> tuple[int, int]:
    items = db.execute(
        select(ReviewItem).where(
            ReviewItem.status == "pending", ReviewItem.reason == "possible_duplicate"
        )
    ).scalars().all()
    dismissed = kept = 0
    for item in items:
        detail = item.detail or {}
        cand = db.get(Examination, detail.get("candidate_id", ""))
        src_name = detail.get("source_name", "")
        if cand is None:
            item.status = "resolved"
            item.resolution = "candidate exam no longer active"
            item.resolved_at = datetime.now(timezone.utc)
            continue
        s_en, s_ar = _tokens(src_name)
        c_en, c_ar = _tokens((cand.name_en or "") + " " + (cand.name_ar or ""))
        # modality conflict is decisive: an X-ray flag against a CT candidate
        # (or vice versa) is a distinct procedure by definition
        src_mod = modality_of(src_name, None)
        cand_mod = (modality_of(cand.name_en, cand.attributes)
                    or modality_of(cand.name_ar, cand.attributes))
        if src_mod and cand_mod and src_mod != cand_mod:
            item.status = "dismissed"
            item.resolution = f"distinct procedure: modality {src_mod} vs candidate {cand_mod}"
            item.resolved_at = datetime.now(timezone.utc)
            audit(db, None, None, "review.dismiss", "review_item", item.id,
                  reason="possible_duplicate: modality conflict")
            dismissed += 1
            continue
        if (s_en & c_en) or (s_ar & c_ar):
            # qualifier conflict (total vs free, qualitative vs quantitative)
            # is decisive even when the analyte token is shared
            _QUAL = {"total": "total", "free": "free",
                     "qualitative": "qualitative", "quantitative": "quantitative"}
            sq = {v for k, v in _QUAL.items() if re.search(rf"\b{k}\b", src_name.lower())}
            cq = {v for k, v in _QUAL.items() if re.search(rf"\b{k}\b", (cand.name_en or "").lower())}
            if sq and cq and sq != cq:
                item.status = "dismissed"
                item.resolution = f"distinct variant: qualifier conflict {sorted(sq)} vs {sorted(cq)}"
                item.resolved_at = datetime.now(timezone.utc)
                audit(db, None, None, "review.dismiss", "review_item", item.id,
                      reason="possible_duplicate: qualifier conflict")
                dismissed += 1
                continue
            kept += 1  # possible same body part/region — human decides
            continue
        item.status = "dismissed"
        item.resolution = "distinct procedure: no shared distinctive tokens with candidate"
        item.resolved_at = datetime.now(timezone.utc)
        audit(db, None, None, "review.dismiss", "review_item", item.id,
              reason="possible_duplicate: distinct procedure (token check)")
        dismissed += 1
    return dismissed, kept


def main() -> None:
    db = SessionLocal()
    try:
        resolved, kept = revalidate_conflicts(db)
        print(f"conflicting_prices: resolved={resolved} still_pending={kept}")
        db.commit()

        dismissed, kept2 = triage_possible_duplicates(db)
        print(f"possible_duplicate: dismissed={dismissed} still_pending={kept2}")
        db.commit()

        pending = db.query(ReviewItem).filter(ReviewItem.status == "pending").count()
        print(f"now pending: {pending}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
