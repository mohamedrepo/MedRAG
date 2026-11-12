# Project Status Report

Date: 2026-11-08
Workspace: `E:\MedRAG`

## 1. Completed components

| # | Deliverable | Where | Status |
|---|---|---|---|
| 1 | System architecture | `docs/ARCHITECTURE.md` | ✅ |
| 2 | Database schema & relationships | `docs/DATABASE.md`, `backend/app/models.py` | ✅ |
| 3 | Complete source code | `backend/` + `frontend/` | ✅ |
| 4 | Document ingestion pipeline | `backend/app/ingestion/` | ✅ |
| 5 | OCR / extraction modules | image OCR via pytesseract; low-text page flagging | ✅ (tesseract binary optional) |
| 6 | Central store | SQLite (local run) + PostgreSQL/pgvector compose | ✅ |
| 7 | Cross-reference engine | `backend/app/xref.py` + `normalization.py` | ✅ |
| 8 | Hybrid search | `backend/app/search.py` | ✅ |
| 9 | Price comparison engine | SQL `ORDER BY value ASC`, grouped by category | ✅ |
| 10 | REST API | FastAPI `/docs` | ✅ |
| 11 | Bilingual web app | `frontend/` AR-RTL / EN-LTR | ✅ |
| 12 | Admin dashboard | `/admin` | ✅ |
| 13 | Upload & review UI | Admin → Documents / Reviews / Aliases | ✅ |
| 14 | Docker Compose | `docker-compose.yml` | ✅ (Docker not installed on this host) |
| 15 | Backup / restore | `scripts/backup.py`, `scripts/restore.py` | ✅ |
| 16 | Unit & integration tests | `backend/tests/` | ✅ |
| 17 | Initial ingestion | see §3 | ✅ partial / in progress |
| 18 | Data quality report | this file §4 + `ingestion_reports` table | ✅ |
| 19 | Install & ops manual | `README.md`, `docs/OPERATIONS.md` | ✅ |

## 2. Environment reality check

* Windows 11, Python 3.13, Node 24.
* **Docker is not installed** → compose file is provided; local run is SQLite + Uvicorn + Vite.
* **PostgreSQL is not installed** → same models run on SQLite; `DATABASE_URL` switches to PG.
* pandas is blocked by an Application Control policy → ingestion uses openpyxl / pdfplumber / pymupdf only.
* passlib/bcrypt incompatible with this Python → passwords use PBKDF2-SHA256 (stdlib).

## 3. Ingestion (final run)

Unique files ingested (duplicates under `نقابه الاطباء/` skipped by SHA-256):

| Document | Parser | Price records | Price values |
|---|---|---|---|
| أشعة 2026 قاهرة.pdf | syndicate | 325 | 1,625 |
| تحاليل 2026 قاهرة.pdf | syndicate | 376 | 1,880 |
| Others 2026.pdf | lab_list | 552 | 552 |
| القائمة المكملة 2026.pdf | lab_list | 632 | 632 |
| المؤسسة العلاجية 2023.pdf (lab) | lab_list | 568 | 568 |
| عيادات خارجية 2026 القاهرة | syndicate | 248 | 1,278 |
| (C) داخلي 2026 | syndicate | 382 | 1,155 |
| مستشفي الرحمة.xlsx | xlsx_package | 128 | 228 |
| لائحة اسعار 2026 القصر العينى | generic | 2,104 | 2,273 |
| المؤسسه العلاجيه ٢٠٢٣.pdf (dental) | generic | 119 | 119 |
| الكشف الطبي القاهرة 2026 | partial | 0 | 0 |
| **Total** | | **5,434** | **10,306** |

Catalog: **4,334** canonical examinations.
Aliases: **1,407** approved · **4,427** proposed (awaiting admin).
Review queue: **146** pending items · **301** price records flagged.
Organizations: 3 · Providers identified: 2 (syndicate lists correctly left as “provider not specified”).

Skipped: `دليل القاهرة2026.pdf` (368-page doctor directory — not a price list).
Duplicates skipped: أشعة / تحاليل copies under نقابة الأطباء.

## 4. Quality

* Missing prices are not coerced to 0.
* Codes are not treated as prices.
* Original names preserved on every price record.
* Proposed (unapproved) aliases: see `/api/v1/stats`.
* Conflicting same-exam prices are queued for review.
* Every value keeps page / sheet provenance.

## 5. Acceptance tests — PASS

`python scripts/acceptance.py`

Mandatory CBC (all 8 forms) → **LAB-000001 Complete Blood Picture**, confidence 1.00.

| Term | Resolves to |
|---|---|
| CBC / cbc / C.B.C / C-B-C / C B C | LAB-000001 |
| Complete Blood Picture / Complete Blood Count | LAB-000001 |
| صورة دم كاملة | LAB-000001 |
| MRI Brain | RAD-010001 (not the contrast variant) |
| MRI Brain with Contrast | RAD-010002 |
| CT Abdomen | RAD-000005 |
| CT Abdomen and Pelvis | RAD-000007 |
| X-ray Knee AP | RAD-020002 |
| X-ray Knee AP and Lateral | RAD-020003 |
| Urine Analysis | LAB-030001 |
| Urine Culture | LAB-030002 |

CT Brain comparison: 5 price-category groups, SQL ascending, cheapest
`parents_project_40 = 185 EGP`, member full `460 EGP`, provenance on every row.
Search latency CBC: **2.8 ms**. CT Brain first compare: **165 ms**.

pytest: **17 passed** (normalization, xref identity, API smoke).

## 6. Remaining / known limits

* OCR binary (tesseract) is not on PATH — scanned pages are flagged, not read.
* دليل القاهرة is a doctor directory, intentionally not treated as a tariff.
* الكشف الطبي 1-pager needs a dedicated clinic-fee parser (queued as review).
* Local hash embeddings are weaker than a real multilingual model;
  set `EMBEDDING_PROVIDER=openai` + LM Studio for better semantic recall.
* Docker / PostgreSQL path is code-complete but unexercised on this host.
* Some long-tail source names become new canonical exams and should be merged
  by an admin after review (by design, not silently auto-merged).

## 7. How to run

See `README.md` (Quick start) and `docs/OPERATIONS.md`.
