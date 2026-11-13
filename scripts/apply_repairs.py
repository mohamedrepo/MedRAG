"""Post-ingest repair pass for the garbled المؤسسه العلاجيه ٢٠٢٣.pdf tariff.

That PDF's embedded font has a broken ToUnicode CMap: BOTH pdfminer and
PyMuPDF extract wrong Arabic letters (U+063B–U+063F and shifted glyphs) and
even wrong digits on some pages (rendered "100" extracts as "111"). The text
is unreadable programmatically but perfectly readable on screen, so the pages
were rendered to images and transcribed with a vision model. The transcripts
live in scripts/repairs/moassa_2023_transcripts/ (one file per page, lines of
"<numbers> | <service name in readable Arabic>").

This script:
1. matches each garbled price record to a transcript row (price-first,
   row-order monotonic alignment),
2. for records whose digits were corrupted (0 -> 1), matches by name
   similarity and CORRECTS the stored price value,
3. restores the seven pacemaker rows of (C) داخلي 2026.pdf page 19 whose
   name column was extracted as a bare number,
4. deactivates aggregate/average rows that are not real service prices
   (Excel column averages, a header-percentages row),
5. consolidates the renamed records into clean examinations, re-embeds
   touched exams, retires emptied shells and clears the search cache.

Idempotent: on a repaired database nothing matches, nothing changes.
"""
from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, r"E:\MedRAG\backend")

from sqlalchemy import select

from app.database import SessionLocal
from app.embeddings import pack_vector
from app.models import Examination, PriceRecord, SourceDocument, Tariff
from app.normalization import normalize_query
from app.security import audit
from app.services.catalog import next_canonical_code
from app.xref import XRefEngine

REPAIRS_DIR = Path(r"E:\MedRAG\scripts\repairs")
TRANSCRIPT_DIR = REPAIRS_DIR / "moassa_2023_transcripts"
GARBLE = re.compile(r"[\u063b-\u063f]")

# (C) داخلي 2026.pdf page 19 — pacemaker rows whose name column was captured
# as a bare number; totals verified against the doctor+hospital components.
PACEMAKERS = {
    "8,500": ("تركيب منظم لضربات قلب مؤقت (شامل المستلزمات)", 8500.0),
    "19,000": ("تركيب منظم دائم لضربات القلب - الجهاز الاحادي (VVI)", 19000.0),
    "23,000": ("تركيب منظم دائم لضربات القلب - الجهاز الثنائي (DDD)", 23000.0),
    "30,000": ("تركيب منظم دائم لضربات القلب - الجهاز الثلاثي (CRT P)", 30000.0),
    "29,000": ("تركيب منظم دائم لضربات القلب مزود بصدمات - الجهاز الاحادي (ICD-Single)", 29000.0),
    "34,000": ("تركيب منظم دائم لضربات القلب مزود بصدمات - الجهاز الثنائي (ICD-Dual)", 34000.0),
    "49,000": ("تركيب منظم دائم لضربات القلب مزود بصدمات - الجهاز الثلاثي (CRT D)", 49000.0),
}

# Aggregate/average rows that are statistics, not service prices.
DEACTIVATE = {
    # مستشفي الرحمة.xlsx sheet 'فتق وتركيب شبكه' — column averages
    ("مستشفي الرحمة.xlsx", "8333333333333"),
    ("مستشفي الرحمة.xlsx", "4285714285716"),
    ("مستشفي الرحمة.xlsx", "12139.4"),
    ("مستشفي الرحمة.xlsx", "19682.9"),
    # عيادات خارجية 2026 القاهرة.pdf — header-percentages row
    ("عيادات خارجية  2026 القاهرة.pdf", "100% 70% 60% 50% 30%"),
}

# Garbled names the similarity pass cannot safely match (too mangled or
# digit-reversed prices); decoded by hand from the page images.
MANUAL_DECODE = {
    "جهاز مزخة محالضل أو سرنجة محالضل يؾميا":
        ("جهاز مضخة محاليل أو سرنجة محاليل يومياً", 160.0),
    "المبؾمضؽ": ("الألبومين", 80.0),
    "ماغشدضؾـ": ("ماغنسيوم", 110.0),
}


def parse_transcript(path: Path) -> list[tuple[list[float], str]]:
    rows: list[tuple[list[float], str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        nums_part, _, name = line.partition("|")
        name = name.strip().strip('"').strip("*").strip()
        cands: set[float] = set()
        for tok in re.findall(r"\d[\d,\.]*", nums_part):
            parts = re.split(r"[, ]", tok.strip(",. "))
            vals = []
            ok = True
            for pt in parts:
                try:
                    vals.append(round(float(pt), 2))
                except ValueError:
                    ok = False
                    break
            if ok and vals:
                cands.update(vals)
        if name and cands:
            rows.append((sorted(cands), name))
    return rows


def corrupt(x: float) -> float | None:
    """Value as it extracts when every rendered '0' becomes '1'."""
    s = str(int(x))
    return float(s.replace("0", "1")) if "0" in s else None


def main() -> None:
    db = SessionLocal()
    xref = XRefEngine(db)
    stats = {
        "decoded": 0, "value_corrections": 0, "moved": 0, "renamed": 0,
        "created": 0, "retired": 0, "pacemakers": 0, "deactivated": 0,
        "already_ok": 0,
    }

    # ------------------------------------------------------------------
    # 1) المؤسسه العلاجيه ٢٠٢٣ — vision-decoded names
    # ------------------------------------------------------------------
    doc = db.execute(
        select(SourceDocument).where(SourceDocument.original_filename.like("%المؤسسه%"))
    ).scalars().first()
    if doc is not None:
        tariff_ids = [t.id for t in db.execute(
            select(Tariff).where(Tariff.document_id == doc.id)).scalars().all()]
        garbled_exams = {
            e.id for e in db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
            if GARBLE.search((e.name_en or "") + (e.name_ar or ""))
        }
        recs = [r for r in db.execute(select(PriceRecord).where(
            PriceRecord.tariff_id.in_(tariff_ids), PriceRecord.is_active == True)).scalars().all()
            if r.examination_id in garbled_exams]
        if recs:
            by_page: dict[int, list[PriceRecord]] = {}
            for r in recs:
                by_page.setdefault(r.source_page, []).append(r)

            decoded: dict[str, tuple[str, float | None]] = {}
            for page, page_recs in sorted(by_page.items()):
                tpath = TRANSCRIPT_DIR / f"transcript_p{page:02d}.txt"
                if not tpath.exists():
                    continue
                trows = parse_transcript(tpath)
                page_recs.sort(key=lambda r: (r.created_at is None, r.created_at, r.id))
                ptr = 0
                for r in page_recs:
                    rvals = {round(float(v.value), 2) for v in r.values}
                    hit = None
                    for i in range(ptr, len(trows)):
                        if rvals & set(trows[i][0]):
                            hit = i
                            break
                    if hit is not None:
                        ptr = hit + 1
                        decoded[r.id] = (trows[hit][1], None)
                        continue
                    # digit corruption (0 -> 1) or name-only match
                    nq = normalize_query(r.original_name or "")
                    best = None
                    for nums, name in trows:
                        true_price = None
                        for t in nums:
                            if t in rvals or corrupt(t) in rvals:
                                true_price = t
                                break
                        if true_price is None:
                            continue
                        sim = SequenceMatcher(None, nq, normalize_query(name)).ratio()
                        if best is None or sim > best[0]:
                            best = (sim, name, true_price)
                    if best and best[0] >= 0.5:
                        decoded[r.id] = (best[1], best[2])
                    if r.id not in decoded:
                        manual = MANUAL_DECODE.get((r.original_name or "").strip())
                        if manual:
                            decoded[r.id] = manual
            stats["decoded"] = len(decoded)

            # rename records + correct corrupted values
            for r in recs:
                if r.id not in decoded:
                    continue
                name, tp = decoded[r.id]
                r.original_name = name
                if tp is not None and r.values:
                    v = r.values[0]
                    if float(v.value) != float(tp):
                        v.value = tp
                        stats["value_corrections"] += 1
            db.flush()

            # consolidate into clean exams
            claimed: dict[str, str] = {}
            for r in recs:
                if r.id not in decoded:
                    continue
                name, _ = decoded[r.id]
                res = xref.resolve(name, use_cache=False)
                target = None
                if (res.examination and res.examination.status == "active"
                        and not GARBLE.search((res.examination.name_en or "") + (res.examination.name_ar or ""))
                        and res.confidence >= 0.88):
                    target = res.examination
                if target is not None and target.id != r.examination_id:
                    r.examination_id = target.id
                    stats["moved"] += 1
                elif target is None:
                    cur = db.get(Examination, r.examination_id)
                    if claimed.get(cur.id) not in (None, name):
                        target = Examination(
                            canonical_code=next_canonical_code(db, cur.category or "Other"),
                            name_en=name, name_ar=name, category=cur.category or "Other",
                            subcategory=cur.subcategory)
                        db.add(target)
                        db.flush()
                        r.examination_id = target.id
                        stats["created"] += 1
                    else:
                        claimed[cur.id] = name
                        cur.name_en = name
                        cur.name_ar = name
                        stats["renamed"] += 1
                final = db.get(Examination, r.examination_id)
                if GARBLE.search((final.name_en or "") + (final.name_ar or "")):
                    pass  # should not happen
                else:
                    final.embedding = pack_vector(xref.embedder.embed_one(f"{final.name_en} {final.name_ar or ''}"))
                    final.embedding_model = xref.embedder.name
                xref.propose_alias(name, final.id, source_document_id=doc.id,
                                   source_page=r.source_page, alias_type="original",
                                   confidence=0.9, auto_approve=True)
            db.commit()

            # retire emptied garbled shells
            for eid in list(garbled_exams):
                e = db.get(Examination, eid)
                if e.status != "active":
                    continue
                left = db.execute(select(PriceRecord).where(
                    PriceRecord.examination_id == e.id, PriceRecord.is_active == True)).scalars().all()
                if not left:
                    e.status = "retired"
                    stats["retired"] += 1
            db.commit()

    # ------------------------------------------------------------------
    # 2) (C) داخلي page 19 pacemaker rows
    # ------------------------------------------------------------------
    ddoc = db.execute(
        select(SourceDocument).where(SourceDocument.original_filename.like("%داخلي%"))
    ).scalars().first()
    if ddoc is not None:
        for t in db.execute(select(Tariff).where(Tariff.document_id == ddoc.id)).scalars().all():
            for r in db.execute(select(PriceRecord).where(
                    PriceRecord.tariff_id == t.id, PriceRecord.is_active == True)).scalars().all():
                hit = PACEMAKERS.get((r.original_name or "").strip())
                if not hit or r.values:
                    continue
                name, total = hit
                res = xref.resolve(name, use_cache=False)
                exam = res.examination
                if exam is None or res.confidence < 0.88 or exam.status != "active":
                    exam = Examination(
                        canonical_code=next_canonical_code(db, "Procedures"),
                        name_en=name, name_ar=name, category="Procedures")
                    db.add(exam)
                    db.flush()
                    exam.embedding = pack_vector(xref.embedder.embed_one(f"{name} {name}"))
                    exam.embedding_model = xref.embedder.name
                r.original_name = name
                r.examination_id = exam.id
                from app.models import PriceValue
                db.add(PriceValue(price_record_id=r.id, price_category="cash",
                                  value=total, currency="EGP"))
                xref.propose_alias(name, exam.id, source_document_id=ddoc.id,
                                   source_page=r.source_page, alias_type="original",
                                   confidence=0.9, auto_approve=True)
                stats["pacemakers"] += 1
        db.commit()

    # ------------------------------------------------------------------
    # 3) deactivate aggregate rows
    # ------------------------------------------------------------------
    from datetime import datetime, timezone
    for d in db.execute(select(SourceDocument)).scalars().all():
        for t in db.execute(select(Tariff).where(Tariff.document_id == d.id)).scalars().all():
            for r in db.execute(select(PriceRecord).where(
                    PriceRecord.tariff_id == t.id, PriceRecord.is_active == True)).scalars().all():
                if ((d.original_filename, (r.original_name or "").strip()) in DEACTIVATE
                        and not GARBLE.search(r.original_name or "")):
                    r.is_active = False
                    stats["deactivated"] += 1
    db.commit()

    # ------------------------------------------------------------------
    # 4) clear search cache
    # ------------------------------------------------------------------
    from app.models import SearchCache
    n = db.query(SearchCache).delete()
    db.commit()

    if any(v for k, v in stats.items() if k != "already_ok"):
        audit(db, None, None, "ingestion.apply_repairs", "price_record", None, new_value=stats)
        db.commit()
    print(json.dumps(stats, ensure_ascii=False))
    db.close()


if __name__ == "__main__":
    main()
