"""REST API routers."""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import (
    AuditLog,
    Examination,
    ExaminationAlias,
    IngestionReport,
    Organization,
    PriceRecord,
    PriceValue,
    Provider,
    ReviewItem,
    SourceDocument,
    Tariff,
    User,
)
from app.schemas import (
    AliasIn,
    DocumentPatch,
    LoginIn,
    PriceCorrectIn,
    ResolveIn,
    StatsOut,
    TokenOut,
)
from app.search import SearchEngine
from app.normalization import normalize_query
from app.security import (
    audit,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_optional_user,
    hash_password,
    require_admin,
    require_any,
    require_reviewer,
    verify_password,
)
from app.xref import XRefEngine
from app.ingestion.pipeline import IngestionPipeline

router = APIRouter()
auth_r = APIRouter(prefix="/auth", tags=["auth"])
search_r = APIRouter(prefix="/search", tags=["search"])
exams_r = APIRouter(prefix="/examinations", tags=["examinations"])
docs_r = APIRouter(prefix="/documents", tags=["documents"])
admin_r = APIRouter(prefix="/admin", tags=["admin"])
stats_r = APIRouter(prefix="/stats", tags=["stats"])


# ===========================================================================
# Auth
# ===========================================================================
@auth_r.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    user.last_login_at = func.now()
    db.commit()
    return TokenOut(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        username=user.username,
        role=user.role,
    )


@auth_r.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "role": user.role, "email": user.email}


# ===========================================================================
# Search
# ===========================================================================
@search_r.get("")
def search(
    q: str = Query(..., min_length=1, max_length=256),
    category: str | None = None,
    city: str | None = None,
    governorate: str | None = None,
    provider_id: str | None = None,
    price_category: str | None = None,
    year: int | None = None,
    include_variants: bool = False,
    effective_only: bool = True,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    group: bool = Query(False, description="Group by price category"),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    eng = SearchEngine(db)
    if group:
        data = eng.compare(
            q, price_category=price_category, year=year, category=category,
            city=city, governorate=governorate, provider_id=provider_id,
            effective_only=effective_only, include_variants=include_variants,
            limit=limit, offset=offset,
        )
    else:
        data = eng.search(
            q, category=category, city=city, governorate=governorate,
            provider_id=provider_id, price_category=price_category, year=year,
            effective_only=effective_only, include_variants=include_variants,
            limit=limit, offset=offset,
        )
    # persist the resolution cache written by xref.resolve (read-only API, but
    # the cache is the point of it — without this every search recomputes)
    db.commit()
    return data


@search_r.get("/suggest")
def suggest(q: str = Query(..., min_length=1, max_length=80), limit: int = 8, db: Session = Depends(get_db)):
    return SearchEngine(db).suggest(q, limit)


@search_r.post("/resolve")
def resolve(body: ResolveIn, db: Session = Depends(get_db)):
    xref = XRefEngine(db)
    out = []
    for name in body.names[:50]:
        r = xref.resolve(name)
        out.append({
            "query": name,
            "normalized": normalize_query(name) if name else "",
            "matched": bool(r.examination),
            "canonical_code": r.examination.canonical_code if r.examination else None,
            "canonical_en": r.examination.name_en if r.examination else None,
            "canonical_ar": r.examination.name_ar if r.examination else None,
            "confidence": r.confidence,
            "strategy": r.strategy,
            "candidates": r.candidates[:5],
        })
    return {"results": out}


@search_r.get("/price-categories")
def price_categories(db: Session = Depends(get_db)):
    rows = db.execute(
        select(PriceValue.price_category, func.count(PriceValue.id), func.min(PriceValue.value), func.max(PriceValue.value))
        .group_by(PriceValue.price_category)
        .order_by(func.count(PriceValue.id).desc())
    ).all()
    return [
        {"category": r[0], "count": r[1], "min": float(r[2]) if r[2] is not None else None, "max": float(r[3]) if r[3] is not None else None}
        for r in rows
    ]


@search_r.get("/filters")
def filter_facets(db: Session = Depends(get_db)):
    cats = db.execute(select(Examination.category, func.count(Examination.id)).group_by(Examination.category)).all()
    cities = db.execute(select(Provider.city, func.count(Provider.id)).group_by(Provider.city)).all()
    govs = db.execute(select(Provider.governorate, func.count(Provider.id)).group_by(Provider.governorate)).all()
    years = db.execute(select(Tariff.year, func.count(Tariff.id)).group_by(Tariff.year)).all()
    provs = db.execute(select(Provider.id, Provider.name, Provider.city).order_by(Provider.name)).all()
    return {
        "categories": [{"value": c[0], "count": c[1]} for c in cats if c[0]],
        "cities": [{"value": c[0], "count": c[1]} for c in cities if c[0]],
        "governorates": [{"value": g[0], "count": g[1]} for g in govs if g[0]],
        "years": [{"value": y[0], "count": y[1]} for y in years if y[0]],
        "providers": [{"id": p[0], "name": p[1], "city": p[2]} for p in provs],
    }


# ===========================================================================
# Examinations / aliases
# ===========================================================================
@exams_r.get("")
def list_examinations(
    q: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    conds = [Examination.status == "active"]
    if q:
        nq = normalize_query(q)
        conds.append(
            or_(
                Examination.name_en.ilike(f"%{q}%"),
                Examination.name_ar.ilike(f"%{q}%"),
                Examination.canonical_code.ilike(f"%{q}%"),
                Examination.id.in_(
                    select(ExaminationAlias.examination_id).where(ExaminationAlias.normalized_alias.ilike(f"%{nq}%"))
                ),
            )
        )
    if category:
        conds.append(Examination.category == category)
    total = db.execute(select(func.count(Examination.id)).where(*conds)).scalar()
    rows = db.execute(
        select(Examination).where(*conds).order_by(Examination.canonical_code).offset(offset).limit(limit)
    ).scalars().all()
    return {"total": total, "items": [
        {
            "id": e.id, "code": e.canonical_code, "name_en": e.name_en, "name_ar": e.name_ar,
            "category": e.category, "subcategory": e.subcategory, "attributes": e.attributes,
            "alias_count": len(e.aliases),
        }
        for e in rows
    ]}


@exams_r.get("/{exam_id}")
def get_examination(exam_id: str, db: Session = Depends(get_db)):
    e = db.get(Examination, exam_id)
    if not e:
        raise HTTPException(404, "Not found")
    aliases = [
        {
            "id": a.id, "alias": a.original_alias, "normalized": a.normalized_alias,
            "language": a.language, "type": a.alias_type, "status": a.approval_status,
            "confidence": a.confidence, "source_document_id": a.source_document_id,
            "source_page": a.source_page,
        }
        for a in e.aliases
    ]
    price_count = db.execute(
        select(func.count(PriceRecord.id)).where(PriceRecord.examination_id == e.id)
    ).scalar()
    return {
        "id": e.id, "code": e.canonical_code, "name_en": e.name_en, "name_ar": e.name_ar,
        "category": e.category, "subcategory": e.subcategory, "attributes": e.attributes,
        "status": e.status, "standard_code": e.standard_code, "aliases": aliases,
        "price_count": price_count,
    }


@exams_r.post("/{exam_id}/aliases")
def add_alias(exam_id: str, body: AliasIn, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    if not db.get(Examination, exam_id):
        raise HTTPException(404, "Examination not found")
    xref = XRefEngine(db)
    alias = xref.add_manual_alias(body.alias, exam_id)
    if not alias:
        raise HTTPException(400, "Alias normalizes to empty text")
    audit(db, None, user, "alias.create", "alias", alias.id, new_value={"alias": body.alias, "exam": exam_id})
    db.commit()
    return {"id": alias.id, "status": alias.approval_status}


@exams_r.post("/aliases/{alias_id}/approve")
def approve_alias(alias_id: str, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    xref = XRefEngine(db)
    a = xref.approve_alias(alias_id)
    if not a:
        raise HTTPException(404, "Alias not found")
    audit(db, None, user, "alias.approve", "alias", alias_id, new_value={"status": a.approval_status})
    db.commit()
    return {"id": a.id, "status": a.approval_status}


@exams_r.post("/aliases/{alias_id}/reject")
def reject_alias(alias_id: str, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    xref = XRefEngine(db)
    a = xref.reject_alias(alias_id)
    if not a:
        raise HTTPException(404, "Alias not found")
    audit(db, None, user, "alias.reject", "alias", alias_id)
    db.commit()
    return {"id": a.id, "status": a.approval_status}


@exams_r.patch("/{exam_id}")
def update_examination(exam_id: str, body: dict, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    e = db.get(Examination, exam_id)
    if not e:
        raise HTTPException(404, "Not found")
    old = {"name_en": e.name_en, "name_ar": e.name_ar, "category": e.category, "attributes": e.attributes}
    for k in ("name_en", "name_ar", "category", "subcategory", "attributes", "standard_code"):
        if k in body:
            setattr(e, k, body[k])
    audit(db, None, user, "examination.update", "examination", exam_id, old, body)
    db.commit()
    return {"ok": True}


@exams_r.post("/merge")
def merge_exams(body: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    src, tgt = body.get("source_id"), body.get("target_id")
    if not src or not tgt:
        raise HTTPException(400, "source_id and target_id required")
    xref = XRefEngine(db)
    ok = xref.merge_examinations(src, tgt)
    if not ok:
        raise HTTPException(400, "Merge blocked (missing exams, same id, attribute conflict, or never-merge pair)")
    audit(db, None, user, "examination.merge", "examination", src, old_value={"into": tgt}, reason=body.get("reason"))
    db.commit()
    return {"ok": True}


@exams_r.post("/split")
def split_exam(body: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    xref = XRefEngine(db)
    try:
        new = xref.split_examination(
            body["source_id"],
            body["name_en"],
            body.get("name_ar"),
            body.get("alias_ids", []),
        )
    except KeyError as e:
        raise HTTPException(400, f"missing field {e}")
    audit(db, None, user, "examination.split", "examination", body["source_id"], new_value={"new_id": new.id, "name_en": new.name_en})
    db.commit()
    return {"new_id": new.id, "code": new.canonical_code}


# ===========================================================================
# Documents
# ===========================================================================
@docs_r.get("")
def list_documents(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(require_any),
):
    conds = []
    if status:
        conds.append(SourceDocument.extraction_status == status)
    total = db.execute(select(func.count(SourceDocument.id)).where(*conds)).scalar()
    rows = db.execute(
        select(SourceDocument).where(*conds).order_by(SourceDocument.uploaded_at.desc()).offset(offset).limit(limit)
    ).scalars().all()
    return {"total": total, "items": rows}


@docs_r.get("/{doc_id}")
def get_document(doc_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    d = db.get(SourceDocument, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    reports = db.execute(
        select(IngestionReport).where(IngestionReport.document_id == doc_id).order_by(IngestionReport.created_at.desc())
    ).scalars().all()
    tariffs = db.execute(select(Tariff).where(Tariff.document_id == doc_id)).scalars().all()
    org = db.get(Organization, d.organization_id) if d.organization_id else None
    prov = db.get(Provider, d.provider_id) if d.provider_id else None
    return {
        **{c: getattr(d, c) for c in (
            "id", "original_filename", "file_format", "file_size", "title", "tariff_year",
            "effective_date", "expiration_date", "geographic_scope", "version",
            "extraction_status", "validation_status", "page_count", "uploaded_at",
            "checksum", "storage_path", "notes",
        )},
        "organization": {"id": org.id, "name": org.name} if org else None,
        "provider": {"id": prov.id, "name": prov.name} if prov else None,
        "tariffs": [{"id": t.id, "title": t.title, "year": t.year, "status": t.status} for t in tariffs],
        "reports": [
            {"id": r.id, "stats": r.stats, "issues": (r.issues or [])[:20], "warnings": (r.warnings or [])[:10], "created_at": r.created_at}
            for r in reports
        ],
    }


@docs_r.patch("/{doc_id}")
def patch_document(doc_id: str, body: DocumentPatch, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    d = db.get(SourceDocument, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(d, k, v)
    # cascade year to tariffs
    if data.get("tariff_year"):
        for t in db.execute(select(Tariff).where(Tariff.document_id == doc_id)).scalars():
            t.year = data["tariff_year"]
    audit(db, None, user, "document.update", "document", doc_id, new_value=data)
    db.commit()
    return {"ok": True}


@docs_r.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_reviewer),
):
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in settings.allowed_ext_list:
        raise HTTPException(400, f"Extension .{ext} not allowed")
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    tmp = Path(settings.UPLOAD_DIR) / f"tmp_{file.filename}"
    size = 0
    with tmp.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > max_bytes:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"File exceeds {settings.MAX_UPLOAD_MB} MB")
            out.write(chunk)
    # basic magic-byte check
    _validate_magic(tmp, ext)
    pipe = IngestionPipeline(db)
    try:
        result = pipe.ingest_file(tmp, original_filename=file.filename)
    finally:
        tmp.unlink(missing_ok=True)
    audit(db, None, user, "document.upload", "document", result.get("document_id"),
          new_value={"filename": file.filename, "result_summary": {k: result.get(k) for k in ("duplicate", "parser", "stats")}})
    db.commit()
    return result


def _validate_magic(path: Path, ext: str) -> None:
    with path.open("rb") as fh:
        head = fh.read(16)
    sigs = {
        b"%PDF": "pdf",
        b"PK\x03\x04": {"xlsx", "docx", "pptx"},
        b"\xd0\xcf\x11\xe0": {"xls", "doc"},
        b"\xff\xd8\xff": {"jpg", "jpeg"},
        b"\x89PNG": "png",
    }
    if ext == "pdf" and not head.startswith(b"%PDF"):
        raise HTTPException(400, "File is not a valid PDF")
    if ext in {"xlsx", "docx", "pptx"} and not head.startswith(b"PK\x03\x04"):
        raise HTTPException(400, f"File is not a valid {ext} (zip container missing)")
    if ext == "png" and not head.startswith(b"\x89PNG"):
        raise HTTPException(400, "File is not a valid PNG")
    if ext in {"jpg", "jpeg"} and not head.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "File is not a valid JPEG")


@docs_r.post("/ingest-folder")
def ingest_folder(body: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Ingest every supported file under a server-side folder (admin bootstrap)."""
    folder = Path(body.get("path") or str(Path.cwd()))
    if not folder.exists():
        raise HTTPException(404, "Folder not found")
    pipe = IngestionPipeline(db)
    results = pipe.ingest_directory(folder, recursive=body.get("recursive", True))
    audit(db, None, user, "document.ingest_folder", "document", None,
          new_value={"path": str(folder), "files": len(results)})
    db.commit()
    return {"count": len(results), "results": results}


@docs_r.post("/{doc_id}/associate-provider")
def associate_provider(doc_id: str, body: dict, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    d = db.get(SourceDocument, doc_id)
    if not d:
        raise HTTPException(404, "Not found")
    provider_id = body.get("provider_id")
    prov = db.get(Provider, provider_id) if provider_id else None
    if provider_id and not prov:
        raise HTTPException(404, "Provider not found")
    d.provider_id = provider_id
    for t in db.execute(select(Tariff).where(Tariff.document_id == doc_id)).scalars():
        t.provider_id = provider_id
    for r in db.execute(select(PriceRecord).join(Tariff, PriceRecord.tariff_id == Tariff.id).where(Tariff.document_id == doc_id)).scalars():
        r.provider_id = provider_id
    audit(db, None, user, "document.associate_provider", "document", doc_id, new_value={"provider_id": provider_id})
    db.commit()
    return {"ok": True}


@docs_r.post("/{doc_id}/supersede")
def supersede_document(doc_id: str, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    """Mark tariffs from this document as superseded — never delete history."""
    for t in db.execute(select(Tariff).where(Tariff.document_id == doc_id)).scalars():
        t.status = "superseded"
    audit(db, None, user, "document.supersede", "document", doc_id)
    db.commit()
    return {"ok": True}


# ===========================================================================
# Prices
# ===========================================================================
@docs_r.get("/prices/{record_id}")
def get_price_record(record_id: str, db: Session = Depends(get_db), user: User = Depends(require_any)):
    r = db.get(PriceRecord, record_id)
    if not r:
        raise HTTPException(404, "Not found")
    vals = db.execute(select(PriceValue).where(PriceValue.price_record_id == record_id)).scalars().all()
    exam = db.get(Examination, r.examination_id) if r.examination_id else None
    tariff = db.get(Tariff, r.tariff_id)
    doc = db.get(SourceDocument, tariff.document_id) if tariff else None
    prov = db.get(Provider, r.provider_id) if r.provider_id else None
    return {
        "id": r.id,
        "original_name": r.original_name,
        "original_code": r.original_code,
        "name_ar": r.name_ar,
        "name_en": r.name_en,
        "attributes": r.attributes,
        "extraction_confidence": r.extraction_confidence,
        "review_status": r.review_status,
        "source_page": r.source_page,
        "source_sheet": r.source_sheet,
        "source_cell": r.source_cell,
        "exam": {"id": exam.id, "code": exam.canonical_code, "name_en": exam.name_en, "name_ar": exam.name_ar} if exam else None,
        "provider": {"id": prov.id, "name": prov.name, "city": prov.city} if prov else None,
        "document": {"id": doc.id, "filename": doc.original_filename, "title": doc.title} if doc else None,
        "values": [
            {"id": v.id, "category": v.price_category, "value": float(v.value), "currency": v.currency, "modifier_type": v.modifier_type}
            for v in vals
        ],
    }


@docs_r.post("/prices/correct")
def correct_price(body: PriceCorrectIn, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    v = db.get(PriceValue, body.price_value_id)
    if not v:
        raise HTTPException(404, "Price value not found")
    old = float(v.value)
    v.value = body.new_value
    rec = db.get(PriceRecord, v.price_record_id)
    if rec:
        rec.review_status = "corrected"
    audit(db, None, user, "price.correct", "price_value", v.id,
          old_value={"value": old}, new_value={"value": body.new_value}, reason=body.reason)
    db.commit()
    return {"ok": True, "old": old, "new": body.new_value}


# ===========================================================================
# Admin / review
# ===========================================================================
@admin_r.get("/reviews")
def list_reviews(
    status: str = "pending",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(require_reviewer),
):
    total = db.execute(select(func.count(ReviewItem.id)).where(ReviewItem.status == status)).scalar()
    rows = db.execute(
        select(ReviewItem).where(ReviewItem.status == status)
        .order_by(ReviewItem.priority, ReviewItem.created_at.desc())
        .offset(offset).limit(limit)
    ).scalars().all()
    return {"total": total, "items": rows}


@admin_r.post("/reviews/{rid}/resolve")
def resolve_review(rid: str, body: dict, db: Session = Depends(get_db), user: User = Depends(require_reviewer)):
    item = db.get(ReviewItem, rid)
    if not item:
        raise HTTPException(404, "Not found")
    item.status = "resolved"
    item.resolution = body.get("resolution")
    item.resolved_at = func.now()
    item.resolved_by = user.id
    # optional actions
    action = body.get("action")
    if action == "approve_mapping" and item.entity_type == "price_record":
        rec = db.get(PriceRecord, item.entity_id)
        if rec:
            rec.review_status = "approved"
    elif action == "reject_record" and item.entity_type == "price_record":
        rec = db.get(PriceRecord, item.entity_id)
        if rec:
            rec.review_status = "rejected"
            rec.is_active = False
    audit(db, None, user, "review.resolve", "review", rid, new_value=body)
    db.commit()
    return {"ok": True}


@admin_r.get("/aliases")
def list_aliases(
    status: str = "proposed",
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(require_reviewer),
):
    total = db.execute(select(func.count(ExaminationAlias.id)).where(ExaminationAlias.approval_status == status)).scalar()
    rows = db.execute(
        select(ExaminationAlias).where(ExaminationAlias.approval_status == status)
        .order_by(ExaminationAlias.confidence.desc())
        .offset(offset).limit(limit)
    ).scalars().all()
    out = []
    for a in rows:
        e = db.get(Examination, a.examination_id)
        out.append({
            "id": a.id, "alias": a.original_alias, "normalized": a.normalized_alias,
            "language": a.language, "type": a.alias_type, "confidence": a.confidence,
            "exam_id": a.examination_id, "exam_en": e.name_en if e else None,
            "exam_ar": e.name_ar if e else None, "exam_code": e.canonical_code if e else None,
            "source_document_id": a.source_document_id, "source_page": a.source_page,
        })
    return {"total": total, "items": out}


@admin_r.get("/audit")
def list_audit(limit: int = 100, offset: int = 0, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    total = db.execute(select(func.count(AuditLog.id))).scalar()
    rows = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)).scalars().all()
    return {"total": total, "items": rows}


@admin_r.post("/seed-catalog")
def seed_catalog(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    xref = XRefEngine(db)
    n = xref.seed_catalog()
    audit(db, None, user, "catalog.seed", "examination", None, new_value={"created": n})
    db.commit()
    return {"created": n}


@admin_r.get("/users")
def list_users(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rows = db.execute(select(User)).scalars().all()
    return [{"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active, "email": u.email} for u in rows]


@admin_r.post("/users")
def create_user(body: dict, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    username = body.get("username")
    password = body.get("password")
    role = body.get("role", "viewer")
    if not username or not password:
        raise HTTPException(400, "username and password required")
    if role not in {"admin", "reviewer", "viewer"}:
        raise HTTPException(400, "invalid role")
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        raise HTTPException(409, "Username exists")
    u = User(username=username, email=body.get("email"), password_hash=hash_password(password), role=role)
    db.add(u)
    audit(db, None, user, "user.create", "user", u.id, new_value={"username": username, "role": role})
    db.commit()
    return {"id": u.id, "username": u.username, "role": u.role}


# ===========================================================================
# Stats
# ===========================================================================
@stats_r.get("", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)):
    def count(model, *conds):
        return db.execute(select(func.count(model.id)).where(*conds)).scalar() or 0

    return StatsOut(
        documents=count(SourceDocument),
        examinations=count(Examination, Examination.status == "active"),
        approved_aliases=count(ExaminationAlias, ExaminationAlias.approval_status == "approved"),
        proposed_aliases=count(ExaminationAlias, ExaminationAlias.approval_status == "proposed"),
        price_records=count(PriceRecord),
        price_values=count(PriceValue),
        providers=count(Provider),
        organizations=count(Organization),
        needs_review=count(PriceRecord, PriceRecord.review_status == "needs_review"),
        pending_reviews=count(ReviewItem, ReviewItem.status == "pending"),
    )


@stats_r.get("/categories")
def category_breakdown(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Examination.category, func.count(Examination.id))
        .where(Examination.status == "active")
        .group_by(Examination.category)
    ).all()
    price_rows = db.execute(
        select(PriceRecord.category, func.count(PriceRecord.id), func.count(PriceValue.id))
        .join(PriceValue, PriceValue.price_record_id == PriceRecord.id)
        .group_by(PriceRecord.category)
    ).all()
    return {
        "examinations": [{"category": r[0] or "Other", "count": r[1]} for r in rows],
        "prices": [{"category": r[0] or "Other", "records": r[1], "values": r[2]} for r in price_rows],
    }


router.include_router(auth_r)
router.include_router(search_r)
router.include_router(exams_r)
router.include_router(docs_r)
router.include_router(admin_r)
router.include_router(stats_r)
