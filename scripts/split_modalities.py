"""Split examinations whose price records span multiple imaging modalities.

The lexical xref stage used to merge 'X-Ray Paranasal Sinuses' into
'CT Paranasal Sinuses' because both share 'أشعة' — modality is now a
protected attribute (normalization.extract_attributes + GENERIC_FAMILY_TOKENS),
and this script repairs the damage in the existing data.

For every active exam whose active records carry a modality different from
the exam's own modality:
  - resolve the foreign-modality records' representative name first; if an
    existing active exam of that modality matches (confidence >= 0.92), move
    the records there;
  - otherwise create a new canonical exam and move the records into it,
    proposing (pending) aliases for each distinct original name;
  - queue a review item and audit-log the action.

Also cleans up: price records still pointing at merged/missing exams
(follow merged_into_id), and review items whose record no longer exists.
Idempotent.
"""
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    Examination,
    ExaminationAlias,
    PriceRecord,
    ReviewItem,
)
from app.normalization import extract_attributes, normalize_query
from app.security import audit
from app.xref import XRefEngine

_MIN_EXISTING_CONF = 0.92


def modality_of(name: str | None, attrs: dict | None) -> str | None:
    if attrs and attrs.get("modality"):
        return attrs["modality"]
    if not name:
        return None
    return extract_attributes(name).get("modality")


def representative(records: list[PriceRecord]) -> PriceRecord:
    """Most common name wins; prefer records that carry parsed names."""
    counts = Counter(r.original_name for r in records)
    best = max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
    for r in records:
        if r.original_name == best:
            return r
    return records[0]


def sweep_splits(db) -> tuple[int, int]:
    xref = XRefEngine(db)
    exams = db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
    split_exams = moved_records = 0
    for exam in exams:
        recs = [r for r in (exam.price_records or []) if r.is_active]
        if len(recs) < 2:
            continue
        em = modality_of(exam.name_en, exam.attributes) or modality_of(exam.name_ar, exam.attributes)
        foreign: dict[str, list[PriceRecord]] = {}
        for r in recs:
            m = modality_of(r.original_name, None)
            if m and m != em:
                foreign.setdefault(m, []).append(r)
        if not foreign:
            continue
        split_exams += 1
        for m, group in sorted(foreign.items()):
            rep = representative(group)
            res = xref.resolve(rep.original_name, use_cache=False)
            target = None
            if (
                res.examination
                and res.examination.id != exam.id
                and res.examination.status == "active"
                and res.confidence >= _MIN_EXISTING_CONF
            ):
                em2 = modality_of(res.examination.name_en, res.examination.attributes) \
                    or modality_of(res.examination.name_ar, res.examination.attributes)
                if em2 == m:
                    target = res.examination
            if target is None:
                name_en = rep.name_en or rep.original_name
                name_ar = rep.name_ar
                from app.services.catalog import next_canonical_code
                target = Examination(
                    canonical_code=next_canonical_code(db, exam.category or "Other"),
                    name_en=name_en,
                    name_ar=name_ar,
                    category=exam.category,
                    subcategory=exam.subcategory,
                    attributes=extract_attributes(name_en or name_ar or rep.original_name),
                )
                db.add(target)
                db.flush()
                for orig in {r.original_name for r in group if r.original_name}:
                    exists = db.execute(
                        select(ExaminationAlias).where(
                            ExaminationAlias.examination_id == target.id,
                            ExaminationAlias.normalized_alias == normalize_query(orig),
                        )
                    ).scalar_one_or_none()
                    if exists:
                        continue
                    db.add(ExaminationAlias(
                        examination_id=target.id,
                        original_alias=orig,
                        normalized_alias=normalize_query(orig),
                        language="mixed",
                        alias_type="original",
                        confidence=1.0,
                        approval_status="proposed",
                    ))
                audit(db, None, None, "examination.split", "examination", target.id,
                      new_value={"from": exam.canonical_code, "modality": m,
                                 "records": len(group)},
                      reason="records carried a different imaging modality")
            for r in group:
                r.examination_id = target.id
            moved_records += len(group)
            db.add(ReviewItem(
                entity_type="examination",
                entity_id=target.id,
                reason="exam_split",
                priority=2,
                detail={"from_exam": exam.canonical_code, "modality": m,
                        "moved_records": len(group),
                        "name": target.name_en or target.name_ar},
            ))
        xref.invalidate()
    return split_exams, moved_records


def sweep_orphans(db) -> tuple[int, int]:
    """Re-point records at exams that were merged (follow the chain)."""
    xref = XRefEngine(db)
    fixed = missing = 0
    recs = db.execute(select(PriceRecord).where(PriceRecord.examination_id.is_not(None))).scalars().all()
    for r in recs:
        exam = db.get(Examination, r.examination_id)
        if exam is None:
            missing += 1
            continue
        if exam.status == "active":
            continue
        final = None
        seen = set()
        cur = exam
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            if cur.status == "active":
                final = cur
                break
            cur = db.get(Examination, cur.merged_into_id) if cur.merged_into_id else None
        if final is not None:
            r.examination_id = final.id
            fixed += 1
        else:
            r.examination_id = None  # unmapped; resolve/review can re-map later
            missing += 1
    if fixed or missing:
        xref.invalidate()
    return fixed, missing


def sweep_stale_items(db) -> int:
    """Resolve review items whose price record no longer exists."""
    items = db.execute(
        select(ReviewItem).where(
            ReviewItem.status == "pending",
            ReviewItem.entity_type == "price_record",
            ReviewItem.reason.in_(("conflicting_prices", "implausible_low_price", "low_confidence")),
        )
    ).scalars().all()
    n = 0
    for item in items:
        if db.get(PriceRecord, item.entity_id) is None:
            item.status = "resolved"
            item.resolution = "record no longer exists (document re-ingested)"
            item.resolved_at = datetime.now(timezone.utc)
            n += 1
    return n


def main() -> None:
    db = SessionLocal()
    try:
        n_exams, n_recs = sweep_splits(db)
        print(f"modality splits: exams={n_exams} records_moved={n_recs}")
        db.commit()

        fixed, missing = sweep_orphans(db)
        print(f"orphans: re-pointed={fixed} unmapped={missing}")
        db.commit()

        stale = sweep_stale_items(db)
        print(f"stale review items resolved: {stale}")
        db.commit()

        pending = db.query(ReviewItem).filter(ReviewItem.status == "pending").count()
        active = db.query(Examination).filter(Examination.status == "active").count()
        print(f"now: active_exams={active} pending_reviews={pending}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
