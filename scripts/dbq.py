import sqlite3
c = sqlite3.connect(r"E:\MedRAG\data\medrag.db", timeout=10)
print(c.execute("select original_filename, extraction_status from source_documents order by uploaded_at").fetchall())
print("price_records", c.execute("select count(*) from price_records").fetchone())
print("exams", c.execute("select count(*) from examinations").fetchone())
c.close()
