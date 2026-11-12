import sys
sys.path.insert(0, r"E:\MedRAG\backend")
from app.database import SessionLocal
from app.models import Examination, PriceRecord, Provider
from sqlalchemy import select

db = SessionLocal()

borg = db.execute(select(Provider).where(Provider.name == "معمل البرج")).scalar_one()
print("borg", borg.id)

print("--- borg recs containing Ferritin / Vitamin / VIT ---")
rows = db.execute(
    select(PriceRecord).where(
        PriceRecord.provider_id == borg.id,
        PriceRecord.original_name.ilike("%ferritin%") | PriceRecord.original_name.ilike("%vitamin%") | PriceRecord.original_name.ilike("%VIT %"),
    ).limit(20)
).scalars().all()
for r in rows:
    exam = db.get(Examination, r.examination_id) if r.examination_id else None
    print(repr(r.original_name)[:70], "->", exam.name_en if exam else None, [float(v.value) for v in r.values])

print("--- borg rec count ---")
print(db.execute(select(__import__("sqlalchemy").func.count(PriceRecord.id)).where(PriceRecord.provider_id == borg.id)).scalar())

print("--- Vitamin exams ---")
for e in db.execute(select(Examination).where(Examination.name_en.ilike("%vitamin%") | Examination.name_en.ilike("%VIT %")).limit(20)).scalars():
    print(e.canonical_code, e.name_en)

db.close()
