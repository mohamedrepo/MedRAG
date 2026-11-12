"""Export every reported member price for نقابة الاطباء 2026 to an Excel workbook.

Only the member category is exported, per the owner rule. Nothing is inferred:
rows without a member price are simply absent, and the workbook records which
categories were deliberately left out so the omission is auditable.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"E:\MedRAG\backend")

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Examination, PriceRecord, PriceValue, Provider, SourceDocument, Tariff
from app.reporting import REPORTED_CATEGORIES, SYNDICATE_2026

OUT_DIR = Path(r"E:\MedRAG\storage\exports")
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = datetime.now(timezone.utc).strftime("%Y%m%d")
OUT = OUT_DIR / f"نقابة_الاطباء_2026_اسعار_الاعضاء_{STAMP}.xlsx"

HEADER_FILL = PatternFill("solid", fgColor="1E293B")
HEADER_FONT = Font(bold=True, color="FFFFFF")

COLUMNS = [
    ("المركز", 22),
    ("المستند", 34),
    ("الصفحة", 8),
    ("الاسم في المصدر", 52),
    ("الكود الأصلي", 14),
    ("الكود المعياري", 14),
    ("الاسم المعياري EN", 40),
    ("الاسم المعياري AR", 40),
    ("التصنيف", 16),
    ("السعر (ج.م)", 12),
    ("فئة السعر", 14),
    ("حالة المراجعة", 14),
    ("ثقة الاستخراج", 12),
    ("معرف السجل", 34),
]


def style_header(ws, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, (_, width) in enumerate(COLUMNS[:ncols], start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def main() -> None:
    db = SessionLocal()
    try:
        provider = db.execute(select(Provider).where(Provider.name == SYNDICATE_2026)).scalar_one()
        allowed = sorted(REPORTED_CATEGORIES[SYNDICATE_2026])

        q = (
            select(PriceValue, PriceRecord, Tariff, SourceDocument, Examination)
            .join(PriceRecord, PriceValue.price_record_id == PriceRecord.id)
            .join(Tariff, PriceRecord.tariff_id == Tariff.id)
            .join(SourceDocument, Tariff.document_id == SourceDocument.id)
            .outerjoin(Examination, PriceRecord.examination_id == Examination.id)
            .where(
                PriceRecord.provider_id == provider.id,
                PriceValue.price_category.in_(allowed),
            )
        )
        rows = db.execute(q).all()

        # everything the rule keeps out of this report, counted for the audit sheet
        ignored = Counter()
        for pv, pr, _t, doc, _e in db.execute(
            select(PriceValue, PriceRecord, Tariff, SourceDocument, Examination)
            .join(PriceRecord, PriceValue.price_record_id == PriceRecord.id)
            .join(Tariff, PriceRecord.tariff_id == Tariff.id)
            .join(SourceDocument, Tariff.document_id == SourceDocument.id)
            .outerjoin(Examination, PriceRecord.examination_id == Examination.id)
            .where(PriceRecord.provider_id == provider.id)
        ).all():
            if pv.price_category not in allowed:
                ignored[(doc.original_filename, pv.price_category)] += 1

        data = []
        for pv, pr, _t, doc, ex in rows:
            data.append({
                "centre": provider.name,
                "doc": doc.original_filename,
                "page": pr.source_page,
                "name": pr.original_name,
                "code": pr.original_code,
                "exam_code": ex.canonical_code if ex else None,
                "exam_en": ex.name_en if ex else None,
                "exam_ar": ex.name_ar if ex else None,
                "category": pr.category,
                "price": float(pv.value) if pv.value is not None else None,
                "cat": pv.price_category,
                "review": pr.review_status,
                "conf": pr.extraction_confidence,
                "id": pr.id,
            })
        data.sort(key=lambda r: (r["doc"], r["price"] if r["price"] is not None else 1e18,
                                 r["name"] or ""))

        wb = Workbook()
        ws = wb.active
        ws.title = "اسعار الاعضاء"
        ws.sheet_view.rightToLeft = True
        ws.append([c[0] for c in COLUMNS])
        for r in data:
            ws.append([r["centre"], r["doc"], r["page"], r["name"], r["code"],
                       r["exam_code"], r["exam_en"], r["exam_ar"], r["category"],
                       r["price"], r["cat"], r["review"], r["conf"], r["id"]])
        style_header(ws, len(COLUMNS))
        for i in range(2, ws.max_row + 1):
            ws.cell(row=i, column=10).number_format = "#,##0.00"

        # ---- summary
        s = wb.create_sheet("ملخص")
        s.sheet_view.rightToLeft = True
        s.append(["المستند", "عدد أسعار الأعضاء", "أقل سعر", "أعلى سعر"])
        per_doc: dict[str, list[float]] = {}
        for r in data:
            per_doc.setdefault(r["doc"], []).append(r["price"])
        for doc, vals in sorted(per_doc.items()):
            s.append([doc, len(vals), min(vals), max(vals)])
        s.append(["الإجمالي", len(data),
                  min((r["price"] for r in data), default=None),
                  max((r["price"] for r in data), default=None)])
        for c in range(1, 5):
            cell = s.cell(row=1, column=c)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
        for col, width in zip("ABCD", (40, 18, 14, 14)):
            s.column_dimensions[col].width = width
        s.append([])
        s.append(["المركز", SYNDICATE_2026])
        s.append(["القاعدة المطبقة", "سعر الأعضاء فقط — باقي الفئات مخزّنة ولم تُبلَّغ"])
        s.append(["تاريخ التصدير", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")])

        # ---- excluded for audit
        a = wb.create_sheet("فئات مستبعدة")
        a.sheet_view.rightToLeft = True
        a.append(["المستند", "فئة السعر", "عدد القيم (غير مُبلَّغة)"])
        for (doc, cat), n in sorted(ignored.items()):
            a.append([doc, cat, n])
        for c in range(1, 4):
            cell = a.cell(row=1, column=c)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
        for col, width in zip("ABC", (40, 26, 24)):
            a.column_dimensions[col].width = width

        wb.save(OUT)
        print(f"rows exported: {len(data)}")
        print(f"documents: {len(per_doc)}")
        print(f"price range: {min(r['price'] for r in data)} .. {max(r['price'] for r in data)} EGP")
        print(f"ignored values: {sum(ignored.values())} across {len(ignored)} (doc, category) pairs")
        print(f"file: {OUT}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
