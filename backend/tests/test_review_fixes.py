"""Regression tests for the 2025-11 review bug fixes."""
from __future__ import annotations

import pytest

from app.ingestion.base import map_price_category, parse_number
from app.ingestion.pdf_parsers import _looks_like_serial, _parse_generic, _parse_syndicate_table
from app.ingestion.office_parsers import _parse_excel_simple
from app.normalization import attributes_compatible, extract_attributes


# --- map_price_category: compound categories must not collapse -------------
def test_compound_price_categories_survive():
    assert map_price_category("member_share_50") == "member_share_50"
    assert map_price_category("member_project_50") == "member_project_50"
    assert map_price_category("member_parents_50") == "member_parents_50"
    assert map_price_category("parents_project_40") == "parents_project_40"
    assert map_price_category("parents_project_60") == "parents_project_60"
    # plain keys unchanged
    assert map_price_category("العضو") == "member"
    assert map_price_category("50%") == "project_50"
    assert map_price_category("Cash") == "cash"
    assert map_price_category("جناح VIP") == "vip"


def test_arabic_digit_percent_headers_map():
    assert map_price_category("٥٠٪") == "project_50"
    assert map_price_category("٤٠٪") == "project_40"


# --- parse_number: European thousands dot ----------------------------------
def test_parse_number_thousands_dot():
    assert parse_number("1.250") == 1250
    assert parse_number("12.500") == 12500
    # two-decimal fractions are still real decimals
    assert parse_number("460.00") == 460.0
    assert parse_number("460.5") == 460.5


# --- extract_attributes: no substring false positives ----------------------
def test_laterality_word_boundaries():
    assert extract_attributes("Upright MRI Brain").get("laterality") is None
    assert extract_attributes("Cleft Palate Repair").get("laterality") is None
    assert extract_attributes("X-ray Right Knee")["laterality"] == "right"
    assert extract_attributes("X-ray Left Knee")["laterality"] == "left"


def test_qualitative_does_not_imply_differential():
    a = extract_attributes("صورة دم كاملة نوعية")
    assert a.get("quantification") == "qualitative"
    assert "differential" not in a
    b = extract_attributes("CBC with Differential")
    assert b.get("differential") is True


# --- generic parser: integer price columns must survive ---------------------
def test_generic_parser_keeps_integer_prices_with_serial_column():
    table = [
        [None, None, None],
        ["1", "Complete Blood Picture", "460"],
        ["2", "Urine Analysis", "120"],
        ["3", "Ferritin", "350"],
        ["4", "Hemoglobin", "90"],
    ]
    rows: list = []
    _parse_generic(table, 1, rows)
    assert len(rows) == 4
    prices = [r.prices[0].value for r in rows]
    assert prices == [460.0, 120.0, 350.0, 90.0]
    codes = [r.code for r in rows]
    assert codes == ["1", "2", "3", "4"]


def test_generic_parser_single_integer_column_is_price():
    table = [
        [None, None],
        ["Complete Blood Picture", "460"],
        ["Urine Analysis", "120"],
        ["Ferritin", "350"],
    ]
    rows: list = []
    _parse_generic(table, 1, rows)
    assert len(rows) == 3
    assert [r.prices[0].value for r in rows] == [460.0, 120.0, 350.0]


def test_generic_parser_alphanumeric_code_column():
    table = [
        [None, None, None],
        ["LAB-001", "Complete Blood Picture", "460"],
        ["LAB-002", "Urine Analysis", "120"],
    ]
    rows: list = []
    _parse_generic(table, 1, rows)
    assert len(rows) == 2
    assert [r.code for r in rows] == ["LAB-001", "LAB-002"]
    assert [r.prices[0].value for r in rows] == [460.0, 120.0]


def test_looks_like_serial_helper():
    assert _looks_like_serial(["1", "2", "3", "4"])
    assert not _looks_like_serial(["460", "120", "350", "90"])
    assert not _looks_like_serial(["1", "2"])


# --- syndicate parser: Arabic-Indic digits / ٪ in headers -------------------
def _syndicate_table(header_row):
    return [
        header_row,
        ["101", "اجراء اشعة مقطعية على المخ", "100", "120", "150", "160", "300"],
        ["102", "تحليل صورة دم كاملة", "50", "60", "75", "80", "150"],
    ]


def _assert_syndicate_rows(rows):
    assert len(rows) == 2
    cats = [p.category for p in rows[0].prices]
    assert "parents_project_40" in cats
    assert "parents_project_60" in cats
    assert "member" in cats
    # the two 50% columns stay distinct
    assert "member_parents_50" in cats and "member_project_50" in cats
    values = {p.category: p.value for p in rows[0].prices}
    assert values["member"] == 300.0
    assert values["parents_project_40"] == 100.0
    # the code column must never leak in as a price
    assert 101.0 not in values.values()


def test_syndicate_header_visual_order_rtl():
    # what pdfplumber actually emits for these PDFs: visual (reversed) order
    rows: list = []
    _parse_syndicate_table(_syndicate_table(
        ["دوكلا", "ةمدخلا عون", "٪04", "٪06", "٪05", "٪05", "وضعلا"]), 1, rows)
    _assert_syndicate_rows(rows)


def test_syndicate_header_logical_order_rtl():
    # logically-ordered headers must work just as well
    rows: list = []
    _parse_syndicate_table(_syndicate_table(
        ["الكود", "نوع الخدمة", "٤٠٪", "٦٠٪", "٥٠٪", "٥٠٪", "العضو"]), 1, rows)
    _assert_syndicate_rows(rows)


# --- xref: unverified acronyms must not auto-approve ------------------------
@pytest.fixture(scope="module")
def db():
    from app.database import SessionLocal
    from app.xref import XRefEngine

    s = SessionLocal()
    XRefEngine(s).seed_catalog()
    yield s
    s.close()


def test_unverified_acronym_needs_review(db):
    from app.xref import XRefEngine

    x = XRefEngine(db)
    # "cbp" is the initials-acronym of Complete Blood Picture but is NOT a
    # registered alias — it must resolve below the auto-approve bar.
    r = x.resolve("cbp")
    assert r.examination and r.examination.canonical_code == "LAB-000001"
    assert r.confidence < 0.92
    assert r.needs_review


def test_registered_acronym_still_high_confidence(db):
    from app.xref import XRefEngine

    x = XRefEngine(db)
    r = x.resolve("CBC")
    assert r.examination and r.examination.canonical_code == "LAB-000001"
    assert r.confidence >= 0.99
    assert not r.needs_review


def test_invalidate_clears_persisted_search_cache(db):
    from app.models import SearchCache
    from app.xref import XRefEngine

    x = XRefEngine(db)
    x.resolve("CBC")
    db.commit()
    assert db.query(SearchCache).count() > 0
    x.invalidate()
    db.commit()
    assert db.query(SearchCache).count() == 0
def test_excel_simple_skips_serial_price_column():
    grid = [
        ["م", "البيان", "السعر"],
        ["1", "Complete Blood Picture", "460"],
        ["2", "Urine Analysis", "120"],
        ["3", "Ferritin", "350"],
    ]
    rows: list = []
    _parse_excel_simple(grid, "sheet", rows, None)
    assert len(rows) == 3
    assert [r.prices[0].value for r in rows] == [460.0, 120.0, 350.0]


def test_excel_simple_handles_empty_rows():
    grid = [
        ["البيان", "السعر"],
        ["Complete Blood Picture", "460"],
        [],
        ["Urine Analysis", "120"],
    ]
    rows: list = []
    _parse_excel_simple(grid, "sheet", rows, None)
    assert len(rows) == 2


def test_excel_simple_code_not_float_string():
    grid = [
        ["كود", "البيان", "السعر"],
        [101, "Complete Blood Picture", "460"],
        [102, "Urine Analysis", "120"],
    ]
    rows: list = []
    _parse_excel_simple(grid, "sheet", rows, None)
    assert rows[0].code == "101"


# ---------------------------------------------------------------------------
# Modality guards (X-ray vs CT vs MRI must never merge)
# ---------------------------------------------------------------------------

def test_modality_extracted_from_names():
    assert extract_attributes("C.T. Brain")["modality"] == "ct"
    assert extract_attributes("أشعة مقطعية على المخ")["modality"] == "ct"
    assert extract_attributes("MRI Brain")["modality"] == "mri"
    assert extract_attributes("رنين مغناطيسي على المخ")["modality"] == "mri"
    assert extract_attributes("X-Ray Chest")["modality"] == "xray"
    assert extract_attributes("أشعة على الصدر")["modality"] == "xray"
    assert extract_attributes("Abdominal Ultrasound")["modality"] == "ultrasound"
    assert extract_attributes("سونار على البطن")["modality"] == "ultrasound"
    # 'أشعة مقطعية' contains the generic 'أشعة' — CT must win
    assert extract_attributes("أشعة مقطعية على الجيوب الأنفية")["modality"] == "ct"
    # no modality word → no attribute
    assert "modality" not in extract_attributes("Complete Blood Picture")


def test_modality_blocks_attribute_merge():
    assert not attributes_compatible({"modality": "ct"}, {"modality": "xray"})
    assert not attributes_compatible({"modality": "mri"}, {"modality": "ct"})
    # silent vs explicit still allowed (silent name may be shorthand)
    assert attributes_compatible({"modality": "ct"}, {})
    assert attributes_compatible(None, {"modality": "xray"})


def test_lexical_prefilter_blocks_modality_only_overlap(db):
    """'أشعة على الجيوب' vs 'أشعة مقطعية على الجيوب' share only modality
    tokens + generic words — no merge without a distinctive shared token."""
    from app.models import Examination
    from app.xref import XRefEngine

    e1 = Examination(canonical_code="RAD-T001", name_en="CT Paranasal Sinuses",
                     name_ar="أشعة مقطعية على الجيوب الأنفية", category="Radiology")
    e2 = Examination(canonical_code="RAD-T002", name_en="X-Ray Paranasal Sinuses",
                     name_ar="أشعة على الجيوب الأنفية", category="Radiology")
    db.add_all([e1, e2])
    db.flush()
    xref = XRefEngine(db)
    r = xref.resolve("أشعة على الجيوب الأنفية", use_cache=False)
    assert r.examination is None or r.examination.id == e2.id
    # and the X-ray name must not resolve into the CT exam
    r2 = xref.resolve("X-Ray Paranasal Sinuses", use_cache=False)
    assert r2.examination is None or r2.examination.id != e1.id


def test_split_modalities_moves_foreign_records(db):
    """An exam holding CT + X-ray records gets the X-ray records split out."""
    from app.models import Examination, PriceRecord, SourceDocument, Tariff

    exam = Examination(canonical_code="RAD-T003", name_en="C.T. Brain",
                       name_ar="أشعة مقطعية على المخ", category="Radiology")
    doc = SourceDocument(original_filename="test_split.pdf", file_format="pdf",
                         checksum="test-split-checksum", storage_path="test_split.pdf")
    db.add_all([exam, doc])
    db.flush()
    tariff = Tariff(document_id=doc.id)
    db.add(tariff)
    db.flush()
    rec_ct = PriceRecord(
        tariff_id=tariff.id, original_name="C.T. Brain",
        name_en="C.T. Brain", examination_id=exam.id, category="Radiology",
    )
    rec_xr = PriceRecord(
        tariff_id=tariff.id, original_name="X-Ray Skull",
        name_en="X-Ray Skull", examination_id=exam.id, category="Radiology",
    )
    db.add_all([rec_ct, rec_xr])
    db.flush()

    from scripts.split_modalities import modality_of  # noqa: F401
    assert modality_of("X-Ray Skull", None) == "xray"
    assert modality_of("C.T. Brain", None) == "ct"
    # the exam's own modality keeps its matching record
    assert modality_of(exam.name_en, exam.attributes) == "ct"


# ---------------------------------------------------------------------------
# Serial-run detection with restarted runs (package tables)
# ---------------------------------------------------------------------------

def test_serial_detection_restarted_runs():
    from app.ingestion.pdf_parsers import _looks_like_serial
    # multi-block package table: 1..7 then restart 1..3
    assert _looks_like_serial(["1", "2", "3", "4", "5", "6", "7", "1", "2", "3"])
    assert _looks_like_serial(["8", "9", "10", "11", "12", "13", "14", "4", "5", "6"])
    assert _looks_like_serial(["1", "2", "3", "4", "5"])
    assert _looks_like_serial(["3", "1", "2"])  # scrambled single run
    # real price columns must not be flagged
    assert not _looks_like_serial(["460", "230", "115", "115", "230"])
    assert not _looks_like_serial(["30", "61"])
    assert not _looks_like_serial(["195", "290", "240", "245", "485"])


def test_syndicate_member_column_is_last_numeric():
    """The rightmost numeric column is the full member price; the 'العضو'
    label on a 50% share column must not create a second member value."""
    from app.ingestion.pdf_parsers import _parse_syndicate_table

    table = [
        ["الوالدين", None, "العضو وأسرته", None, "القاهرة", "نوع الخدمة", "CODE كود"],
        ["المشروع", "الوالدين", "المشروع", "العضو", "2026", None, None],
        ["40% 60% 50% 50%", None, None, None, None, "C.T. الأشعة المقطعية", None],
        ["185", "275", "230", "230", "460", "C.T. Brain أشعة مقطعية", "3117"],
        ["0", "440", "0", "440", "440", "استئصال زوائد", "4024"],
    ]
    rows: list = []
    _parse_syndicate_table(table, 1, rows)
    brain = next(r for r in rows if "Brain" in (r.name or ""))
    cats = {p.category: p.value for p in brain.prices}
    assert cats["member"] == 460.0
    assert cats["parents_project_40"] == 185.0
    assert cats["parents_project_60"] == 275.0
    assert "member_parents_50" in cats and "member_project_50" in cats
    # exactly one member value
    assert sum(1 for p in brain.prices if p.category == "member") == 1
    # zeros ('scheme does not cover') are not stored as prices
    skin = next(r for r in rows if "4024" in (r.code or ""))
    assert all(p.value > 0 for p in skin.prices)
    assert not any(p.value == 0 for p in skin.prices)


def test_clean_service_name_fixes_visual_parens():
    from app.ingestion.base import clean_service_name

    # visual-order ')أ( ب' → logical 'ب (أ)'
    assert clean_service_name(')وأى صبغة زيادة( ملى 10 استكمال') == \
        'ملى 10 استكمال (وأى صبغة زيادة)'
    # fee-note parentheticals are dropped (they duplicate the stored price)
    out = clean_service_name('تردد حرارى الورام ) جنية 5000 شامل اجر الطبيب ( الكبد')
    assert 'جنية 5000' not in out and 'شامل' not in out
    assert 'تردد حرارى' in out
    # ordinary logical-order name passes through untouched
    assert clean_service_name('Complete Blood Picture') == 'Complete Blood Picture'


def test_clean_service_name_keeps_short_abbreviations():
    from app.ingestion.base import clean_service_name

    assert clean_service_name('TSH') == 'TSH'
    assert clean_service_name('IgG') == 'IgG'
