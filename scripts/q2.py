import sys
sys.path.insert(0, r"E:\MedRAG\backend")
from app.database import SessionLocal
from app.search import SearchEngine
from app.xref import XRefEngine
from app.models import Examination, PriceRecord, Tariff
from sqlalchemy import select

db = SessionLocal()
x = XRefEngine(db)
r = x.resolve("Ferritin")
print("resolve", r.examination.canonical_code if r.examination else None, r.confidence, r.strategy)

exam = r.examination
print("exam id", exam.id)
recs = db.execute(select(PriceRecord).where(PriceRecord.examination_id == exam.id)).scalars().all()
print("price records", len(recs))
for rec in recs[:12]:
    t = db.get(Tariff, rec.tariff_id)
    print(" ", rec.original_name[:40], "prov", rec.provider_id, "active", rec.is_active, "year", t.year, "status", t.status, [float(v.value) for v in rec.values])

eng = SearchEngine(db)
d = eng.search("Ferritin")
print("search total", d["total"], "interp", d["interpretation"]["canonical_code"] if d["interpretation"] else None)
print("rows", len(d["results"]))
for row in d["results"][:10]:
    print(" ", row["price"], row["provider_name"], row["price_category"])

# try without effective_only
d2 = eng.search("Ferritin", effective_only=False)
print("no-effective total", d2["total"])
db.close()
