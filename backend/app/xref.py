"""Medical examination cross-reference engine.

Resolves any Arabic/English name, abbreviation, separator variant or
transliteration to a single canonical examination — without merging
genuinely distinct procedures (contrast, views, laterality, qualitative
vs quantitative, different regions).

Evidence rules
--------------
* Exact approved alias (normalized or collapsed) → confidence 1.0
* Seed alias / manual alias → 0.98
* Same canonical name after normalization → 0.95
* Strong lexical (Dice/Jaccard) + compatible attributes → ≤ 0.85, proposed
* Vector similarity is supplementary evidence only; it never approves a merge
* NEVER_MERGE pairs and attribute conflicts block a merge even at high similarity
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.embeddings import cosine, get_embedder, pack_vector, unpack_vector
from app.models import Examination, ExaminationAlias, SearchCache
from app.normalization import (
    GENERIC_FAMILY_TOKENS,
    acronym_of,
    attributes_compatible,
    collapse_separators,
    detect_language,
    extract_attributes,
    fix_rtl_visual_order,
    has_arabic,
    normalize_query,
    similar,
)
from app.seed_catalog import NEVER_MERGE_PAIRS, SEED_EXAMS


@dataclass
class Resolution:
    examination: Examination | None
    confidence: float
    strategy: str
    matched_alias: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    needs_review: bool = False


class XRefEngine:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.embedder = get_embedder()
        self._alias_cache: dict[str, Examination] | None = None
        self._collapsed_cache: dict[str, Examination] | None = None
        self._exam_list: list[Examination] | None = None
        self._name_index: list[tuple[Examination, list[str]]] | None = None

    # ------------------------------------------------------------------
    # Caches
    # ------------------------------------------------------------------
    def warm(self) -> None:
        if self._alias_cache is None:
            alias_cache: dict[str, Examination] = {}
            collapsed_cache: dict[str, Examination] = {}
            rows = self.db.execute(
                select(ExaminationAlias).where(ExaminationAlias.approval_status == "approved")
            ).scalars().all()
            exams = {e.id: e for e in self.db.execute(select(Examination)).scalars().all()}
            for a in rows:
                e = exams.get(a.examination_id)
                if e is None:
                    continue
                # follow merges; never resolve to retired/superseded exams
                hops = 0
                while e.status == "merged" and e.merged_into_id and hops < 5:
                    e = exams.get(e.merged_into_id)
                    hops += 1
                if e is None or e.status != "active":
                    continue
                alias_cache.setdefault(a.normalized_alias, e)
                collapsed_cache.setdefault(collapse_separators(a.original_alias), e)
            self._alias_cache = alias_cache
            self._collapsed_cache = collapsed_cache

    def _exams(self) -> list[Examination]:
        if self._exam_list is None:
            self._exam_list = list(
                self.db.execute(select(Examination).where(Examination.status == "active")).scalars().all()
            )
        return self._exam_list

    def _exam_name_index(self) -> list[tuple[Examination, list[str]]]:
        """Precomputed normalized names/aliases for lexical scan."""
        if self._name_index is None:
            self._name_index = []
            for e in self._exams():
                names = [e.name_en or "", e.name_ar or ""]
                names += [a.original_alias for a in e.aliases if a.approval_status == "approved"]
                norms = [normalize_query(n) for n in names if n]
                self._name_index.append((e, norms))
        return self._name_index

    def invalidate(self) -> None:
        # Cheap flag only; caches rebuild on next warm() when actually needed.
        self._alias_cache = None
        self._collapsed_cache = None
        self._exam_list = None
        self._name_index = None
        # Stale persisted resolutions must go too: an approved alias, merge or
        # split changes what old queries should resolve to.
        try:
            self.db.query(SearchCache).delete()
        except Exception:
            self.db.rollback()

    def _maybe_warm(self) -> None:
        if self._alias_cache is None:
            self.warm()

    # ------------------------------------------------------------------
    # Resolution pipeline
    # ------------------------------------------------------------------
    def resolve(self, raw: str, use_cache: bool = True) -> Resolution:
        raw = (raw or "").strip()
        if not raw:
            return Resolution(None, 0.0, "empty")

        nq = normalize_query(raw)
        cache_key = nq[:256]
        if use_cache:
            cached = self.db.get(SearchCache, cache_key)
            if cached and cached.examination_id:
                exam = self.db.get(Examination, cached.examination_id)
                if exam and exam.status == "active":
                    return Resolution(exam, cached.confidence, cached.strategy or "cache", raw)

        self._maybe_warm()
        assert self._alias_cache is not None

        # RTL visual-order PDFs store source names reversed; logically-ordered
        # sources store them straight. Try both orientations so a name matches
        # the catalog regardless of which orientation the alias was stored in.
        alt_raw = ""
        if has_arabic(raw):
            cand = fix_rtl_visual_order(raw)
            if cand != raw:
                alt_raw = cand
        alt_nq = normalize_query(alt_raw) if alt_raw else ""
        alt_coll = collapse_separators(alt_raw) if alt_raw else ""

        # Stage 1 — exact normalized approved alias
        exam = self._alias_cache.get(nq) or (self._alias_cache.get(alt_nq) if alt_nq else None)
        if exam and exam.status != "active":
            exam = None  # defensive: retired exam leaked into the alias cache
        if exam:
            res = Resolution(exam, 1.0, "exact_alias", raw)
            self._store_cache(cache_key, res)
            return res

        # Stage 2 — collapsed separators (C.B.C / C-B-C / C B C → cbc)
        coll = collapse_separators(raw)
        assert self._collapsed_cache is not None
        exam = self._collapsed_cache.get(coll) or (self._collapsed_cache.get(alt_coll) if alt_coll else None)
        if exam:
            res = Resolution(exam, 0.99, "collapsed_alias", raw)
            self._store_cache(cache_key, res)
            return res

        # Stage 3 — canonical names
        for e in self._exams():
            n_en = normalize_query(e.name_en or "")
            n_ar = normalize_query(e.name_ar) if e.name_ar else ""
            if nq in (n_en, n_ar) or (alt_nq and alt_nq in (n_en, n_ar)):
                res = Resolution(e, 0.96, "canonical_name", raw)
                self._store_cache(cache_key, res)
                return res

        # Stage 4 — acronym (mri, cbc, ct, esr…)
        ac = collapse_separators(raw).lower()
        if 2 <= len(ac) <= 8 and ac.isalpha():
            for e in self._exams():
                e_ac = acronym_of(e.name_en or "")
                if e_ac and collapse_separators(e_ac) == ac:
                    # Only trust initials-matches that are corroborated by a
                    # registered alias; a bare initials collision (any word
                    # "cat" vs Computerized Axial Tomography) must not
                    # auto-approve at 0.94 — keep it above the lexical floor
                    # but below the 0.92 auto-approve bar and flag it.
                    registered = self._collapsed_cache.get(ac)
                    if registered is e:
                        res = Resolution(e, 0.94, "acronym", raw)
                        self._store_cache(cache_key, res)
                        return res
                    res = Resolution(e, 0.88, "acronym_unverified", raw, needs_review=True)
                    self._store_cache(cache_key, res)
                    return res

        # Stage 5 — lexical candidates + attribute gate
        cands = self._lexical_candidates(raw, nq, limit=12)
        if alt_raw:
            # merge orientation-variant candidates, keeping the best per exam
            by_id: dict[str, dict[str, Any]] = {c["id"]: c for c in cands}
            for c in self._lexical_candidates(alt_raw, alt_nq, limit=6):
                prev = by_id.get(c["id"])
                if prev is None or c["score"] > prev["score"]:
                    by_id[c["id"]] = c
            cands = sorted(by_id.values(), key=lambda x: (-x["score"], x["code"]))[:12]
        if cands:
            best = cands[0]
            exam = self.db.get(Examination, best["id"])
            if exam and best["score"] >= 0.82 and best.get("attrs_ok"):
                conf = min(0.90, best["score"])
                res = Resolution(exam, conf, "lexical", raw, candidates=cands, needs_review=conf < 0.85)
                self._store_cache(cache_key, res)
                return res
            # below threshold → suggestions only
            return Resolution(None, best["score"] if cands else 0.0, "suggest", raw, candidates=cands, needs_review=True)

        # Stage 6 — semantic (supplementary)
        sem = self._semantic_candidates(raw, limit=5)
        if sem and sem[0]["score"] >= 0.88:
            exam = self.db.get(Examination, sem[0]["id"])
            if exam and sem[0].get("attrs_ok"):
                res = Resolution(exam, 0.72, "semantic", raw, candidates=sem, needs_review=True)
                return res
            return Resolution(None, sem[0]["score"] if sem else 0.0, "suggest", raw, candidates=sem, needs_review=True)

        return Resolution(None, 0.0, "unmatched", raw, candidates=cands + sem)

    def _exam_attrs(self, e: Examination) -> dict[str, Any]:
        """Stored attributes, with modality derived from the name when silent.

        Exams created before modality extraction existed have empty attribute
        dicts; without this derivation an X-ray record would still merge into
        a 'CT …' exam because the exam side looks 'silent'.
        """
        attrs = dict(e.attributes or {})
        if "modality" not in attrs:
            m = extract_attributes(e.name_en or "").get("modality") or extract_attributes(
                e.name_ar or ""
            ).get("modality")
            if m:
                attrs["modality"] = m
        return attrs

    def _lexical_candidates(self, raw: str, nq: str, limit: int = 10) -> list[dict[str, Any]]:
        attrs_q = extract_attributes(raw)
        nq_tokens = set(nq.split()) if nq else set()
        nq_compact = collapse_separators(raw).lower()
        out: list[dict[str, Any]] = []
        for e, norms in self._exam_name_index():
            best = 0.0
            for nn in norms:
                if abs(len(nn) - len(nq)) > 18:
                    continue
                nt = set(nn.split())
                compact = collapse_separators(nn)
                if not (nq_tokens & nt) and nq_compact[:4] != compact[:4] and nq_compact not in compact and compact not in nq_compact:
                    continue
                # Family-token trap: Vitamin A vs Vitamin D must not merge
                # on the shared word "vitamin" alone.
                shared = nq_tokens & nt
                distinctive = {t for t in shared if t not in GENERIC_FAMILY_TOKENS}
                if shared and not distinctive and len(nq_tokens) >= 2:
                    continue
                s = similar(nq, nn)
                if compact == nq_compact and s < 0.99:
                    s = max(s, 0.93)
                if s > best:
                    best = s
            if best <= 0:
                continue
            attrs_ok = attributes_compatible(attrs_q, self._exam_attrs(e))
            if not attrs_ok:
                best *= 0.35
            out.append({
                "id": e.id,
                "code": e.canonical_code,
                "name_en": e.name_en,
                "name_ar": e.name_ar,
                "category": e.category,
                "score": round(best, 4),
                "attrs_ok": attrs_ok,
                "attributes": e.attributes,
            })
        out.sort(key=lambda x: (-x["score"], x["code"]))
        return out[:limit]

    def _semantic_candidates(self, raw: str, limit: int = 5) -> list[dict[str, Any]]:
        qvec = self.embedder.embed_one(raw)
        attrs_q = extract_attributes(raw)
        out: list[dict[str, Any]] = []
        for e in self._exams():
            vec = unpack_vector(e.embedding)
            if vec is None or len(vec) != len(qvec):
                continue
            s = cosine(qvec, vec)
            if s <= 0.35:
                continue
            attrs_ok = attributes_compatible(attrs_q, self._exam_attrs(e))
            out.append({
                "id": e.id,
                "code": e.canonical_code,
                "name_en": e.name_en,
                "name_ar": e.name_ar,
                "category": e.category,
                "score": round(s, 4),
                "attrs_ok": attrs_ok,
            })
        out.sort(key=lambda x: -x["score"])
        return out[:limit]

    def _store_cache(self, key: str, res: Resolution) -> None:
        if not res.examination:
            return
        # autoflush=False hides this session's own pending cache rows from a
        # plain SELECT, so two resolves of the same query in one transaction
        # would both INSERT and collide on the primary key at commit time.
        self.db.flush()
        row = self.db.execute(
            select(SearchCache).where(SearchCache.normalized_query == key)
        ).scalar_one_or_none()
        if row:
            row.examination_id = res.examination.id
            row.confidence = res.confidence
            row.strategy = res.strategy
            row.hits += 1
        else:
            self.db.add(SearchCache(
                normalized_query=key,
                examination_id=res.examination.id,
                confidence=res.confidence,
                strategy=res.strategy,
            ))
        # No per-row commit — the caller's transaction flushes this in bulk.

    # ------------------------------------------------------------------
    # Alias management
    # ------------------------------------------------------------------
    def propose_alias(
        self,
        raw_alias: str,
        examination_id: str,
        source_document_id: str | None = None,
        source_page: int | None = None,
        alias_type: str = "original",
        confidence: float = 0.8,
        auto_approve: bool = False,
    ) -> ExaminationAlias:
        nq = normalize_query(raw_alias)
        if not nq:
            return None
        # repeated ingests can accumulate duplicate alias rows; the lookups
        # must tolerate multiples instead of crashing the ingestion
        existing = self.db.execute(
            select(ExaminationAlias).where(
                ExaminationAlias.normalized_alias == nq,
                ExaminationAlias.examination_id == examination_id,
            ).order_by(ExaminationAlias.confidence.desc()).limit(1)
        ).scalars().first()
        if existing:
            if auto_approve and existing.approval_status == "proposed":
                existing.approval_status = "approved"
                existing.confidence = max(existing.confidence, confidence)
            return existing

        dup_orig = self.db.execute(
            select(ExaminationAlias).where(
                ExaminationAlias.original_alias == raw_alias,
                ExaminationAlias.examination_id == examination_id,
            ).limit(1)
        ).scalars().first()
        if dup_orig:
            return dup_orig

        # Same normalized alias already approved for another exam → do not steal
        other = self.db.execute(
            select(ExaminationAlias).where(
                ExaminationAlias.normalized_alias == nq,
                ExaminationAlias.approval_status == "approved",
                ExaminationAlias.examination_id != examination_id,
            ).limit(1)
        ).scalars().first()
        status = "proposed"
        if auto_approve and not other:
            status = "approved"
        if other and auto_approve:
            # conflict → keep proposed for human review
            status = "proposed"
            confidence = min(confidence, 0.5)

        alias = ExaminationAlias(
            examination_id=examination_id,
            original_alias=raw_alias[:512],
            normalized_alias=nq[:512],
            language=detect_language(raw_alias),
            alias_type=alias_type,
            source_document_id=source_document_id,
            source_page=source_page,
            confidence=confidence,
            approval_status=status,
        )
        self.db.add(alias)
        self.db.flush()  # visible to the next existence check in this session
        return alias

    def approve_alias(self, alias_id: str) -> ExaminationAlias | None:
        alias = self.db.get(ExaminationAlias, alias_id)
        if not alias:
            return None
        # If another exam already owns this normalized alias as approved, reject steal
        other = self.db.execute(
            select(ExaminationAlias).where(
                ExaminationAlias.normalized_alias == alias.normalized_alias,
                ExaminationAlias.approval_status == "approved",
                ExaminationAlias.examination_id != alias.examination_id,
            )
        ).scalar_one_or_none()
        if other:
            alias.approval_status = "rejected"
            alias.confidence = 0.1
        else:
            alias.approval_status = "approved"
            alias.confidence = max(alias.confidence, 0.95)
        self.invalidate()
        return alias

    def reject_alias(self, alias_id: str) -> ExaminationAlias | None:
        alias = self.db.get(ExaminationAlias, alias_id)
        if not alias:
            return None
        alias.approval_status = "rejected"
        self.invalidate()
        return alias

    def add_manual_alias(self, raw: str, examination_id: str) -> ExaminationAlias | None:
        return self.propose_alias(
            raw, examination_id, alias_type="manual", confidence=1.0, auto_approve=True
        )

    def merge_examinations(self, source_id: str, target_id: str) -> bool:
        """Move aliases and price records from source into target; retire source."""
        if source_id == target_id:
            return False
        if (source_id, target_id) in NEVER_MERGE_PAIRS or (target_id, source_id) in NEVER_MERGE_PAIRS:
            return False
        src = self.db.get(Examination, source_id)
        tgt = self.db.get(Examination, target_id)
        if not src or not tgt:
            return False
        if not attributes_compatible(self._exam_attrs(src), self._exam_attrs(tgt)):
            return False
        for a in src.aliases:
            a.examination_id = target_id
        from app.models import PriceRecord
        for p in self.db.execute(select(PriceRecord).where(PriceRecord.examination_id == source_id)).scalars():
            p.examination_id = target_id
        src.status = "merged"
        src.merged_into_id = target_id
        self.invalidate()
        return True

    def split_examination(self, exam_id: str, new_name_en: str, new_name_ar: str | None, alias_ids: list[str]) -> Examination:
        """Pull selected aliases out into a new canonical examination."""
        src = self.db.get(Examination, exam_id)
        if not src:
            raise ValueError("examination not found")
        from app.services.catalog import next_canonical_code
        new = Examination(
            canonical_code=next_canonical_code(self.db, src.category or "Other"),
            name_en=new_name_en,
            name_ar=new_name_ar,
            category=src.category,
            subcategory=src.subcategory,
            attributes=src.attributes,
        )
        self.db.add(new)
        self.db.flush()
        for aid in alias_ids:
            a = self.db.get(ExaminationAlias, aid)
            if a and a.examination_id == exam_id:
                a.examination_id = new.id
                a.approval_status = "approved"
        self.invalidate()
        return new

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------
    def seed_catalog(self) -> int:
        """Insert the curated catalog if missing. Idempotent."""
        from app.services.catalog import next_canonical_code
        created = 0
        existing_codes = {
            e.canonical_code: e
            for e in self.db.execute(select(Examination)).scalars().all()
        }
        for seed in SEED_EXAMS:
            exam = existing_codes.get(seed.code)
            if not exam:
                exam = Examination(
                    canonical_code=seed.code,
                    name_en=seed.name_en,
                    name_ar=seed.name_ar,
                    category=seed.category,
                    subcategory=seed.subcategory,
                    attributes=seed.attributes,
                    standard_code=seed.standard_code,
                )
                self.db.add(exam)
                self.db.flush()
                created += 1
            # embeddings
            if not exam.embedding:
                text_for_vec = " | ".join(filter(None, [seed.name_en, seed.name_ar, *(seed.aliases_en or []), *(seed.aliases_ar or [])]))
                exam.embedding = pack_vector(self.embedder.embed_one(text_for_vec))
                exam.embedding_model = self.embedder.name
            # aliases
            for al in seed.aliases_en + seed.aliases_ar:
                self.propose_alias(al, exam.id, alias_type="abbreviation" if len(al) <= 8 else "full_name",
                                   confidence=1.0, auto_approve=True)
            self.propose_alias(seed.name_en, exam.id, alias_type="full_name", confidence=1.0, auto_approve=True)
            if seed.name_ar:
                self.propose_alias(seed.name_ar, exam.id, alias_type="full_name", confidence=1.0, auto_approve=True)
        self.db.commit()
        self.invalidate()
        return created
