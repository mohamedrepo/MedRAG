"""Post-ingest data cleanup: junk-exam split, name_en fixup, word-order merge.

Run after ingestion (and after scripts/apply_repairs.py). Idempotent.

1. Junk-exam split — exams whose names carry no letters ('( )', '10 ) (',
   digits only) are parser debris. Their price records are real, so each
   record is re-cleaned and re-resolved: a strong match (>= 0.88) moves it
   to that exam, otherwise a properly named exam is created. Emptied junk
   exams are retired.

2. name_en fixup — exams whose name_en is punctuation/digit debris but whose
   name_ar is readable get name_en := name_ar.

3. Word-order merge — active exams whose normalized token sets are identical
   (order scrambled by RTL extraction) are the same service; the emptier one
   is merged into the one with more records. Pure prepositions are ignored
   for the token set; SPATIAL words (فوق/تحت/أمام/خلف) are differentiators
   (above/below the elbow are different procedures) and never ignored.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select

from app.database import SessionLocal
from app.embeddings import pack_vector
from app.models import Examination, PriceRecord, ReviewItem
from app.normalization import normalize_query, token_set
from app.security import audit
from app.services.catalog import next_canonical_code
from app.xref import XRefEngine
from app.ingestion.base import clean_service_name

_LETTERS = re.compile(r"[A-Za-z\u0600-\u06ff]")
_GARBLE = re.compile(r"[\u063b-\u063f]")  # broken-CMap glyph families

# prepositions whose position varies between visual and logical order
_WORDORDER_STOP = {
    "من", "على", "في", "مع", "عند", "الى", "إلى", "خلال",
    "of", "the", "and", "or", "in", "on", "for", "with",
}
# spatial words are DIFFERENTIATORS — never treat them as stopwords
assert "فوق" not in _WORDORDER_STOP and "تحت" not in _WORDORDER_STOP


def _has_letters(s: str | None) -> bool:
    return bool(s and _LETTERS.search(s))


def _is_garbled(s: str | None) -> bool:
    return bool(s and _GARBLE.search(s))


def _is_junky(s: str | None) -> bool:
    return not _has_letters((s or "").strip())


def _get_or_create_exam(db, xref: XRefEngine, cleaned: str, category: str | None,
                        subcategory: str | None, created: dict) -> Examination:
    key = normalize_query(cleaned)
    target = created.get(key)
    if target:
        return target
    from app.normalization import extract_attributes, split_bilingual
    ar, en = split_bilingual(cleaned)
    if en and not _has_letters(en):
        en = None  # bare digits/debris — keep the full name instead
    target = Examination(
        canonical_code=next_canonical_code(db, category or "Other"),
        name_en=en or cleaned, name_ar=ar,
        category=category or "Other", subcategory=subcategory,
        attributes=extract_attributes(cleaned),
    )
    db.add(target)
    db.flush()
    target.embedding = pack_vector(xref.embedder.embed_one(
        f"{target.name_en} {target.name_ar or ''} {cleaned}"))
    target.embedding_model = xref.embedder.name
    created[key] = target
    return target


def split_junk_exams(db, xref: XRefEngine, stats: dict) -> None:
    now = datetime.now(timezone.utc)
    junky = [e for e in db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
             if _is_junky(e.name_en or "") and _is_junky(e.name_ar or "")]
    if not junky:
        return
    from app.ingestion.base import clean_service_name

    created: dict[str, Examination] = {}
    claimed: dict[str, str] = {}
    for exam in junky:
        recs = db.execute(select(PriceRecord).where(
            PriceRecord.examination_id == exam.id, PriceRecord.is_active == True)).scalars().all()
        for r in recs:
            cleaned = clean_service_name(r.original_name) or ""
            if _is_junky(cleaned):
                continue  # stays on the junk exam, still flagged for review
            res = xref.resolve(cleaned, use_cache=False)
            target = None
            if (res.examination and res.examination.status == "active"
                    and res.examination.id != exam.id and res.confidence >= 0.88
                    and not _is_junky(res.examination.name_en or res.examination.name_ar or "")):
                target = res.examination
                stats["matched_existing"] += 1
            else:
                key = normalize_query(cleaned)
                target = created.get(key)
                if not target:
                    from app.normalization import extract_attributes, split_bilingual
                    ar, en = split_bilingual(cleaned)
                    target = Examination(
                        canonical_code=next_canonical_code(db, exam.category or "Other"),
                        name_en=en or cleaned, name_ar=ar,
                        category=exam.category or "Other", subcategory=exam.subcategory,
                        attributes=extract_attributes(cleaned),
                    )
                    db.add(target)
                    db.flush()
                    target.embedding = pack_vector(xref.embedder.embed_one(
                        f"{target.name_en} {target.name_ar or ''} {cleaned}"))
                    target.embedding_model = xref.embedder.name
                    created[key] = target
                    stats["created"] += 1
                if claimed.get(exam.id) is None:
                    claimed[exam.id] = key
            r.examination_id = target.id
            stats["repointed"] += 1
            xref.propose_alias(r.original_name or cleaned, target.id, alias_type="full_name",
                               confidence=0.9, auto_approve=True, source_page=r.source_page)
        left = db.execute(select(PriceRecord).where(
            PriceRecord.examination_id == exam.id, PriceRecord.is_active == True)).scalars().all()
        if not left:
            exam.status = "retired"
            stats["retired"] += 1
            for item in db.execute(select(ReviewItem).where(
                    ReviewItem.entity_type == "examination", ReviewItem.entity_id == exam.id,
                    ReviewItem.reason == "junk_exam_name", ReviewItem.status == "pending")).scalars():
                item.status = "resolved"
                item.resolution = "records split to proper exams; exam retired"
                item.resolved_at = now
        db.commit()


def fix_name_en(db, stats: dict) -> None:
    exams = db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
    for e in exams:
        if _has_letters(e.name_en or ""):
            continue
        if not _has_letters(e.name_ar or ""):
            continue
        e.name_en = (e.name_ar or "").strip()
        stats["name_en_fixed"] += 1
    db.commit()


def merge_word_order_variants(db, xref: XRefEngine, stats: dict) -> None:
    def key(e: Examination) -> frozenset:
        return token_set(normalize_query(e.name_en or e.name_ar or "")) - _WORDORDER_STOP

    def n_active(eid: str) -> int:
        return len(db.execute(select(PriceRecord).where(
            PriceRecord.examination_id == eid, PriceRecord.is_active == True)).scalars().all())

    buckets: dict[frozenset, list[Examination]] = {}
    for e in db.execute(select(Examination).where(Examination.status == "active")).scalars().all():
        k = key(e)
        if not k:
            continue
        buckets.setdefault(k, []).append(e)
    for k, exs in buckets.items():
        if len(exs) < 2:
            continue
        exs.sort(key=lambda e: n_active(e.id), reverse=True)
        keep = exs[0]
        for dup in exs[1:]:
            if xref.merge_examinations(dup.id, keep.id):
                stats["merged"] += 1
    db.commit()


def dissolve_black_holes(db, xref: XRefEngine, stats: dict) -> None:
    """Re-home records piled on debris-named exams.

    A parser bug could resolve many different services onto one debris-named
    exam (a "black hole" with hundreds of unrelated records). For every
    active exam holding records whose cleaned names are mutually different,
    each record is re-resolved: a strong match to a DIFFERENT active exam
    moves it. What remains keeps the exam if it is now one single service;
    an emptied exam is retired.
    """
    now = datetime.now(timezone.utc)
    bh_created: dict[str, Examination] = {}
    exams = db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
    for exam in exams:
        recs = db.execute(select(PriceRecord).where(
            PriceRecord.examination_id == exam.id, PriceRecord.is_active == True)).scalars().all()
        if len(recs) < 2:
            continue
        names = {normalize_query(clean_service_name(r.original_name) or "") for r in recs}
        names.discard("")
        if len(names) <= 3:
            continue  # one service with a few name variants — not a black hole
        if not (_is_junky(exam.name_en or "") or _is_junky(exam.name_ar or "")
                or _is_garbled(exam.name_en or "") or _is_garbled(exam.name_ar or "")):
            # Real-named bundle of distinct services (e.g. a tariff section
            # header plus its rows). Admin decision, not an automated split.
            already = db.execute(select(ReviewItem).where(
                ReviewItem.entity_type == "examination", ReviewItem.entity_id == exam.id,
                ReviewItem.reason == "exam_split", ReviewItem.status == "pending")).scalars().first()
            if not already:
                db.add(ReviewItem(
                    entity_type="examination", entity_id=exam.id, reason="exam_split",
                    status="pending", priority=3,
                    detail={"names": sorted(names)[:10], "records": len(recs),
                            "note": "exam holds several distinct services"}),
                )
                db.flush()
            continue
        stats["black_holes_seen"] += 1
        moved = 0
        # Temporarily retire the black hole so resolve() cannot return it
        # (its own aliases would otherwise match every record back to it).
        saved_status = exam.status
        exam.status = "retired"
        db.flush()  # autoflush is off — make the change visible to queries
        xref.invalidate()
        for r in recs:
            cleaned = clean_service_name(r.original_name) or ""
            if not cleaned:
                continue
            res = xref.resolve(cleaned, use_cache=False)
            if (res.examination and res.examination.status == "active"
                    and res.examination.id != exam.id and res.confidence >= 0.88
                    and not _is_junky(res.examination.name_en or res.examination.name_ar or "")):
                r.examination_id = res.examination.id
                moved += 1
                stats["rehomed"] += 1
        exam.status = saved_status
        db.flush()
        xref.invalidate()
        # Records that matched nothing still deserve real homes: group the
        # leftovers by cleaned name and give each group its own exam.
        left = db.execute(select(PriceRecord).where(
            PriceRecord.examination_id == exam.id, PriceRecord.is_active == True)).scalars().all()
        own = {normalize_query(exam.name_en or ""), normalize_query(exam.name_ar or "")}
        groups: dict[str, list] = {}
        for r in left:
            cleaned = clean_service_name(r.original_name) or ""
            if not cleaned or _is_junky(cleaned) or _is_garbled(cleaned):
                continue  # garbled names need vision decode, not a new exam
            groups.setdefault(normalize_query(cleaned), []).append((r, cleaned))
        for k, items in groups.items():
            if k in own:
                continue
            target = _get_or_create_exam(db, xref, items[0][1], exam.category,
                                         exam.subcategory, bh_created)
            for r, cleaned in items:
                r.examination_id = target.id
                stats["regrouped"] += 1
            xref.propose_alias(items[0][1], target.id, alias_type="full_name",
                               confidence=0.9, auto_approve=True, source_page=items[0][0].source_page)
        left = db.execute(select(PriceRecord).where(
            PriceRecord.examination_id == exam.id, PriceRecord.is_active == True)).scalars().all()
        if not left:
            exam.status = "retired"
            stats["retired_bh"] += 1
            for item in db.execute(select(ReviewItem).where(
                    ReviewItem.entity_type == "examination", ReviewItem.entity_id == exam.id,
                    ReviewItem.status == "pending")).scalars():
                item.status = "resolved"
                item.resolution = "black-hole exam dissolved; records re-homed"
                item.resolved_at = now
        elif left and len({normalize_query(clean_service_name(r.original_name) or "") for r in left}) == 1:
            nm = clean_service_name(left[0].original_name) or ""
            if nm and not _is_junky(nm):
                exam.name_en = nm
                exam.name_ar = nm
                exam.embedding = pack_vector(xref.embedder.embed_one(f"{nm} {nm}"))
                exam.embedding_model = xref.embedder.name
                stats["renamed_bh"] += 1
        db.commit()


def rename_garbled_exams(db, xref: XRefEngine, stats: dict) -> None:
    """Exams whose names carry broken-CMap glyphs get renamed from their
    (already decoded) records when those agree on one clean name."""
    exams = db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
    for e in exams:
        if not (_is_garbled(e.name_en or "") or _is_garbled(e.name_ar or "")):
            continue
        recs = db.execute(select(PriceRecord).where(
            PriceRecord.examination_id == e.id, PriceRecord.is_active == True)).scalars().all()
        names = {clean_service_name(r.original_name) or "" for r in recs}
        names.discard("")
        if len(names) != 1:
            continue
        nm = next(iter(names))
        if _is_garbled(nm) or _is_junky(nm):
            continue
        e.name_en = nm
        e.name_ar = nm
        e.embedding = pack_vector(xref.embedder.embed_one(f"{nm} {nm}"))
        e.embedding_model = xref.embedder.name
        stats["garble_renamed"] += 1
    db.commit()


def fix_record_name_en(db, stats: dict) -> None:
    """Records whose name_en cell was parser debris ('( )') get the exam's
    display name so search results never show paren debris."""
    from sqlalchemy import or_
    recs = db.execute(select(PriceRecord).where(
        PriceRecord.is_active == True,
        or_(PriceRecord.name_en == "( )", PriceRecord.name_en == ") (",
            PriceRecord.name_en == ""))).scalars().all()
    for r in recs:
        e = db.get(Examination, r.examination_id)
        if e and _has_letters(e.name_en or ""):
            r.name_en = e.name_en
            if r.name_ar and not _has_letters(r.name_ar):
                r.name_ar = e.name_ar
            stats["record_name_en_fixed"] += 1
    db.commit()


def main() -> None:
    db = SessionLocal()
    xref = XRefEngine(db)
    stats = {"repointed": 0, "created": 0, "matched_existing": 0, "retired": 0,
             "name_en_fixed": 0, "merged": 0, "black_holes_seen": 0,
             "rehomed": 0, "regrouped": 0, "retired_bh": 0, "renamed_bh": 0,
             "garble_renamed": 0, "record_name_en_fixed": 0}
    split_junk_exams(db, xref, stats)
    fix_name_en(db, stats)
    fix_record_name_en(db, stats)
    rename_garbled_exams(db, xref, stats)
    dissolve_black_holes(db, xref, stats)
    merge_word_order_variants(db, xref, stats)
    from app.models import SearchCache
    db.query(SearchCache).delete()
    db.commit()
    if any(v for v in stats.values()):
        audit(db, None, None, "examination.post_ingest_cleanup", "examination", None, new_value=stats)
        db.commit()
    print(stats)
    db.close()


if __name__ == "__main__":
    main()
