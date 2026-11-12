import sys
sys.path.insert(0, r"E:\MedRAG\backend")
from app.database import SessionLocal
from app.models import Examination
from sqlalchemy import select
from app.xref import XRefEngine

db = SessionLocal()
x = XRefEngine(db)

def exam_by_en(name):
    return db.execute(select(Examination).where(Examination.name_en == name)).scalar_one_or_none()

# Aliases that must not fall through to Vitamin D
mapping = {
    "Vitamin A": ["VIT A", "Vit A", "Retinol", "فيتامين أ", "Retinol serum"],
    "Vitamin E": ["VIT E", "Vit E", "Tocopherol", "فيتامين هـ"],
    "Vitamin K1": ["VIT K1", "Phylloquinone", "فيتامين ك 1"],
    "Vitamin K2": ["VIT K2", "Menaquinone", "فيتامين ك 2"],
    "Vitamin D 25-OH": ["VIT D", "Vit D", "25 OH Vitamin D", "25-Hydroxy Vitamin D",
                        "vit D 25 Hydroxy Cholecalciferol", "Vitamin D (25-Hydroxy-Cholecalcifelol)"],
}
for en, aliases in mapping.items():
    e = exam_by_en(en)
    if not e:
        print("MISSING exam", en)
        continue
    for a in aliases:
        al = x.propose_alias(a, e.id, alias_type="abbreviation", confidence=1.0, auto_approve=True)
        print("alias", a, "->", en, al.approval_status if al else None)

# Merge the misspelled 25-OH variant into the canonical Vitamin D
src = db.execute(select(Examination).where(Examination.name_en == "Vitamin D (25-Hydroxy-Cholecalcifelol)")).scalar_one_or_none()
tgt = exam_by_en("Vitamin D 25-OH")
if src and tgt and src.id != tgt.id:
    ok = x.merge_examinations(src.id, tgt.id)
    print("merge 25-OH variant:", ok, src.canonical_code, "->", tgt.canonical_code)

db.commit()
x.invalidate()
db.close()
print("done")
