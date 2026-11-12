import sys
sys.path.insert(0, r"E:\MedRAG\backend")
from pathlib import Path
from app.database import SessionLocal
from app.ingestion.pipeline import IngestionPipeline

FILES = [
    Path(r"E:\MedRAG\المختبر و البرج\القائمة المكملة 2026.pdf"),
    Path(r"E:\MedRAG\المختبر و البرج\المؤسسة العلاجية 2023.pdf"),
    Path(r"E:\MedRAG\نقابه الاطباء\عيادات خارجية  2026 القاهرة.pdf"),
    Path(r"E:\MedRAG\نقابه الاطباء\الكشف الطبي القاهرة 2026.pdf"),
    Path(r"E:\MedRAG\نقابه الاطباء\(C) داخلي 2026.pdf"),
    Path(r"E:\MedRAG\مستشفي الرحمة.xlsx"),
    Path(r"E:\MedRAG\لائحة اسعار 2026 القصر العينى الجديد -.pdf"),
    Path(r"E:\MedRAG\المؤسسه العلاجيه ٢٠٢٣.pdf"),
]

def main():
    db = SessionLocal()
    try:
        pipe = IngestionPipeline(db)
        for f in FILES:
            print("INGEST", f.name, flush=True)
            r = pipe.ingest_file(f)
            s = r.get("stats") or {}
            print("  parser=%s rows=%s recs=%s vals=%s review=%s new=%s err=%s" % (
                r.get("parser"), s.get("raw_rows"), s.get("price_records"),
                s.get("price_values"), s.get("needs_review"),
                s.get("new_examinations"), r.get("error"),
            ), flush=True)
        print("DONE")
    finally:
        db.close()

if __name__ == "__main__":
    main()
