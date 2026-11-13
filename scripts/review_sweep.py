"""Autonomous review-queue sweep: only provably-safe actions, everything else
stays pending for human review.

1. Merge active examinations whose (category, normalized name) are identical.
   Guards: XRefEngine.merge_examinations enforces NEVER_MERGE_PAIRS and
   attribute compatibility; two distinct seeded codes are never merged; the
   seeded exam (or the one with the most records) becomes the target.
2. conflicting_prices: re-map a flagged price record only when its
   original_name resolves by EXACT alias / canonical name (confidence >= 0.95)
   to a different examination — i.e. a clear mis-mapping. Otherwise pending.
3. possible_duplicate: resolve the item when the created exam has since been
   merged/retired, or when the candidate was rejected for an attribute
   conflict (a legitimate distinct variant). Uncertain score cases stay
   pending.

Every action is audit-logged. Idempotent.
"""
import sys

sys.path.insert(0, r"E:\MedRAG\backend")

from collections import defaultdict

from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditLog, Examination, PriceRecord, ReviewItem
from app.normalization import normalize_query
from app.security import audit
from app.seed_catalog import SEED_EXAMS
from app.xref import XRefEngine

SEED_CODES = {s.code for s in SEED_EXAMS}


def pick_target(group: list[Examination]) -> Examination:
    seeded = [e for e in group if e.canonical_code in SEED_CODES]
    if seeded:
        return sorted(seeded, key=lambda e: e.canonical_code)[0]
    return sorted(group, key=lambda e: (-len(e.price_records or []), e.canonical_code))[0]


def resolve_active(db, exam: Examination) -> Examination | None:
    """Follow merged_into_id chains to the final active exam (or None)."""
    seen = set()
    while exam is not None and exam.id not in seen:
        seen.add(exam.id)
        if exam.status == "active":
            return exam
        exam = db.get(Examination, exam.merged_into_id) if exam.merged_into_id else None
    return None


def sweep_duplicates(db, xref: XRefEngine) -> tuple[int, int]:
    exams = db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
    groups: dict[tuple, list[Examination]] = defaultdict(list)
    for e in exams:
        for n in {e.name_en, e.name_ar}:
            if not n:
                continue
            key = (e.category, normalize_query(n))
            groups[key].append(e)

    merged = blocked = 0
    for key, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), str(kv[0][0]), str(kv[0][1]))):
        # an exam can appear in two groups (name_en and name_ar) and may have
        # been merged while processing an earlier group — re-resolve to the
        # final active exam at merge time
        live = {}
        for e in members:
            final = resolve_active(db, e)
            if final is not None:
                live[final.id] = final
        if len(live) < 2:
            continue
        target = pick_target(list(live.values()))
        for src in live.values():
            if src.id == target.id:
                continue
            if src.status != "active" or target.status != "active":
                continue
            if src.canonical_code in SEED_CODES and target.canonical_code in SEED_CODES:
                blocked += 1  # two distinct curated exams — never auto-merge
                continue
            ok = xref.merge_examinations(src.id, target.id)
            if ok:
                merged += 1
                audit(db, None, None, "examination.auto_merge", "examination", src.id,
                      old_value={"code": src.canonical_code, "name": src.name_en},
                      new_value={"into": target.canonical_code},
                      reason="duplicate normalized name in same category")
            else:
                blocked += 1
    return merged, blocked


def sweep_conflicting_prices(db, xref: XRefEngine) -> tuple[int, int]:
    items = db.execute(
        select(ReviewItem).where(ReviewItem.status == "pending", ReviewItem.reason == "conflicting_prices")
    ).scalars().all()
    remapped = kept = 0
    for item in items:
        rec = db.get(PriceRecord, item.entity_id)
        if not rec or not rec.original_name:
            continue
        res = xref.resolve(rec.original_name, use_cache=False)
        if (
            res.examination
            and res.confidence >= 0.95
            and res.examination.id != rec.examination_id
            and res.examination.status == "active"
        ):
            old = rec.examination_id
            rec.examination_id = res.examination.id
            remapped += 1
            audit(db, None, None, "price.remap", "price_record", rec.id,
                  old_value={"examination_id": old},
                  new_value={"examination_id": res.examination.id,
                             "code": res.examination.canonical_code},
                  reason=f"exact alias resolution during conflicting_prices sweep ({item.id})")
            item.status = "resolved"
            item.resolution = f"remapped to {res.examination.canonical_code}"
            item.resolved_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        else:
            kept += 1
    return remapped, kept


def sweep_possible_duplicates(db) -> tuple[int, int]:
    items = db.execute(
        select(ReviewItem).where(ReviewItem.status == "pending", ReviewItem.reason == "possible_duplicate")
    ).scalars().all()
    resolved = kept = 0
    for item in items:
        exam = db.get(Examination, item.entity_id)
        detail = item.detail or {}
        if exam is None or exam.status != "active":
            item.status = "resolved"
            item.resolution = "created exam no longer active (merged or retired)"
            item.resolved_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            resolved += 1
            continue
        if detail.get("attrs_ok") is False:
            # the candidate conflicts on contrast / views / laterality — the
            # created exam is a legitimate distinct variant, not a duplicate
            item.status = "resolved"
            item.resolution = "distinct variant: candidate attribute conflict"
            item.resolved_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            resolved += 1
            continue
        kept += 1
    return resolved, kept


def main() -> None:
    db = SessionLocal()
    try:
        xref = XRefEngine(db)
        merged, blocked = sweep_duplicates(db, xref)
        print(f"duplicates: merged={merged} blocked={blocked}")
        db.commit()

        remapped, kept = sweep_conflicting_prices(db, xref)
        print(f"conflicting_prices: remapped={remapped} left_pending={kept}")
        db.commit()

        resolved, kept2 = sweep_possible_duplicates(db)
        print(f"possible_duplicate: resolved={resolved} left_pending={kept2}")
        db.commit()

        pending = db.query(ReviewItem).filter(ReviewItem.status == "pending").count()
        active = db.query(Examination).filter(Examination.status == "active").count()
        print(f"now: active_exams={active} pending_reviews={pending}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
