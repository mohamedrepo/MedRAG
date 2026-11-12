"""Acceptance tests for the CBC / MRI / CT cross-reference contract."""
from __future__ import annotations

import pytest

from app.normalization import normalize_query, similar, collapse_separators, extract_attributes
from app.ingestion.base import parse_number, map_price_category, looks_like_code


def test_cbc_separator_variants_normalize_together():
    variants = ["CBC", "cbc", "C.B.C", "C-B-C", "C B C"]
    norms = {collapse_separators(v).lower() for v in variants}
    assert norms == {"cbc"}


def test_complete_blood_picture_aliases():
    assert normalize_query("Complete Blood Picture") == "complete blood picture"
    assert normalize_query("Complete Blood Count") == "complete blood count"
    assert normalize_query("صورة دم كاملة")


def test_arabic_normalization_alef_ya():
    a = normalize_query("صورة دم كاملة")
    b = normalize_query("صوره دم كامله")
    c = normalize_query("صُورة دمٍ كامِلة")
    assert a == b == c


def test_contrast_variants_do_not_share_attributes():
    with_c = extract_attributes("MRI Brain with Contrast")
    wo_c = extract_attributes("MRI Brain without Contrast")
    assert with_c.get("contrast") == "with"
    assert wo_c.get("contrast") == "without"


def test_views_distinct():
    assert extract_attributes("X-ray Knee AP")["views"] == 1
    assert extract_attributes("X-ray Knee AP and Lateral")["views"] == 2


def test_parse_number_does_not_invent_zero():
    assert parse_number("") is None
    assert parse_number("-") is None
    assert parse_number(None) is None
    assert parse_number("1,234.56") == 1234.56
    assert parse_number("٤٦٠") == 460
    assert parse_number("460") == 460
    assert parse_number("460.00") == 460.0
    assert parse_number("3117") == 3117  # could be a price; caller filters codes
    assert parse_number("CODE") is None
    assert parse_number("N/A") is None


def test_looks_like_code():
    assert looks_like_code("3117")
    assert looks_like_code("5301")
    assert not looks_like_code("CBC")
    assert not looks_like_code("460.00")


def test_price_category_mapping():
    assert map_price_category("العضو") == "member"
    assert map_price_category("50%") == "project_50"
    assert map_price_category("Cash") == "cash"
    assert map_price_category("جناح VIP") == "vip"


def test_similar_cbc_variants():
    assert similar("cbc", "c b c") > 0.8
    assert similar("complete blood picture", "complete blood count") >= 0.7
    assert similar("mri brain", "m r i brain") > 0.8


def test_dice_does_not_crash_on_dicts():
    from app.normalization import dice
    # regression: used to raise TypeError on dict & dict
    assert 0 <= dice("abc", "abd") <= 1
