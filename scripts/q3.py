import sys
sys.path.insert(0, r"E:\MedRAG\backend")
from app.database import SessionLocal
from app.search import SearchEngine

db = SessionLocal()
eng = SearchEngine(db)
for q in ["VIT A", "VIT E", "VIT D", "Vitamin D", "Vitamin K1", "Ferritin", "CBC"]:
    d = eng.search(q)
    i = d.get("interpretation") or {}
    provs = sorted({(r["provider_name"] or "unspecified") for r in d["results"]})
    print(q.ljust(12), "->", (i.get("canonical_en") or "?").ljust(40), "n=%s" % d["total"], provs)
db.close()
