"""XRef tests against the seeded catalog in the dev database."""
from __future__ import annotations

import pytest

from app.database import SessionLocal
from app.xref import XRefEngine


@pytest.fixture(scope="module")
def db():
    s = SessionLocal()
    XRefEngine(s).seed_catalog()
    yield s
    s.close()


CBC_TERMS = [
    "CBC", "cbc", "C.B.C", "C-B-C", "C B C",
    "Complete Blood Picture", "Complete Blood Count", "صورة دم كاملة",
]


def test_cbc_acceptance(db):
    x = XRefEngine(db)
    ids = set()
    for t in CBC_TERMS:
        r = x.resolve(t)
        assert r.examination, f"unresolved: {t}"
        assert r.examination.canonical_code == "LAB-000001", f"{t} -> {r.examination.canonical_code}"
        ids.add(r.examination.id)
    assert len(ids) == 1


def test_mri_brain_vs_contrast(db):
    x = XRefEngine(db)
    a = x.resolve("MRI Brain")
    b = x.resolve("MRI Brain with Contrast")
    assert a.examination and b.examination
    assert a.examination.id != b.examination.id


def test_ct_abdomen_vs_abdomen_pelvis(db):
    x = XRefEngine(db)
    a = x.resolve("CT Abdomen")
    b = x.resolve("CT Abdomen and Pelvis")
    assert a.examination and b.examination
    assert a.examination.id != b.examination.id


def test_urine_analysis_vs_culture(db):
    x = XRefEngine(db)
    a = x.resolve("Urine Analysis")
    b = x.resolve("Urine Culture")
    assert a.examination and b.examination
    assert a.examination.id != b.examination.id


def test_arabic_mri(db):
    x = XRefEngine(db)
    r = x.resolve("رنين مغناطيسي على المخ")
    assert r.examination and r.examination.canonical_code == "RAD-010001"


def test_search_engine_structure(db):
    from app.search import SearchEngine
    eng = SearchEngine(db)
    data = eng.search("CBC")
    assert data["interpretation"]
    assert data["interpretation"]["canonical_code"] == "LAB-000001"
    assert isinstance(data["results"], list)


def test_suggest(db):
    from app.search import SearchEngine
    eng = SearchEngine(db)
    s = eng.suggest("compl")
    assert any("Complete" in (x["name_en"] or "") for x in s)
