# MedRAG — Medical Services Price Intelligence & Comparison

Bilingual (Arabic / English) platform that ingests multi-format medical tariff
documents, resolves examination identities across naming variants, and produces
auditable ascending price comparisons from structured records.

Search `CBC`, `C.B.C`, `C B C`, `Complete Blood Picture`, or `صورة دم كاملة`
and get the same canonical examination, then compare prices **from SQL**, not
from unstructured text.

---

## What actually runs on this machine

| Component | Status | Notes |
|---|---|---|
| FastAPI backend | ✅ | `backend/app` |
| SQLAlchemy models (11 entities) | ✅ | Organizations, Providers, Documents, Examinations, Aliases, Tariffs, PriceRecords, PriceValues, Modifiers, Reviews, Audit |
| SQLite local store | ✅ | `data/medrag.db` — zero-dependency dev |
| PostgreSQL + pgvector compose | ✅ | `docker-compose.yml` (Docker not installed locally) |
| Multi-format ingestion | ✅ | PDF tables, XLSX, CSV, DOCX, TXT, HTML, images |
| Arabic/English normalizer | ✅ | Alef/Ya/ta marbuta, tashkeel, tatweel, RTL visual order, separators, acronyms |
| Cross-reference engine | ✅ | Exact → collapsed → canonical → acronym → lexical → semantic; never merges contrast / views / laterality |
| Hybrid search + price compare | ✅ | SQL sort ascending; grouped by price category |
| Bilingual React UI | ✅ | RTL/LTR toggle, search, comparison tables, admin |
| Admin dashboard | ✅ | Documents, reviews, aliases, catalog, upload |
| Auth + RBAC + audit | ✅ | JWT, admin/reviewer/viewer |
| Seed catalog | ✅ | 114 canonical exams incl. mandatory CBC / MRI / CT pairs |

Docker is **not installed** on this Windows host. The compose stack is provided
for production; local run uses SQLite + Uvicorn + Vite.

---

## Quick start (Windows, no Docker)

```powershell
# 1. Backend deps (already installed in this workspace)
pip install -r backend/requirements.txt

# 2. Init DB + admin + seed catalog
$env:PYTHONPATH = "E:\MedRAG\backend"
python -c "import sys; sys.path.insert(0,'backend'); from app.cli import main; sys.argv=['medrag','init']; main()"

# 3. Ingest the source documents
python scripts/ingest_corpus.py

# 4. Serve API on :8000
python -c "import sys; sys.path.insert(0,'backend'); from app.cli import main; sys.argv=['medrag','serve']; main()"

# 5. Frontend dev server on :5173 (proxies /api to :8000)
cd frontend; npm install; npm run dev
```

Default admin: `admin / medrag-admin` — change via `.env`.

## Production (Docker Compose)

```bash
cp .env.example .env   # set SECRET_KEY, POSTGRES_PASSWORD, ADMIN_PASSWORD
docker compose up --build
# API  http://localhost:8000/docs
# UI   http://localhost:5173
```

`db` is `pgvector/pgvector:pg16` with `vector`, `pg_trgm`, `unaccent`.
`DATABASE_URL` switches the same SQLAlchemy models to PostgreSQL.

---

## Architecture

```
Browser (React, AR-RTL / EN-LTR)
  ↓ /api/v1
FastAPI  ── Auth (JWT) ── RBAC ── Audit
  ├── SearchEngine        (xref + SQL prices)
  ├── XRefEngine          (identity resolution)
  ├── IngestionPipeline   (parse → match → persist → review)
  └── SQLAlchemy
        ├── SQLite (dev)
        └── PostgreSQL + pgvector (prod)

Vector embeddings = discovery only.
Prices, identities, validity = relational tables. Never the reverse.
```

### Cross-reference rules

1. Exact approved alias (normalized).
2. Collapsed separators (`C.B.C` / `C-B-C` / `C B C` → `cbc`).
3. Canonical EN/AR name.
4. Acronym of the full name.
5. Lexical (Dice / Levenshtein / Jaccard) **gated by attributes**.
6. Vector similarity as supplementary evidence only — never auto-approve.

Hard blocks (`NEVER_MERGE`): CBC vs CBC+Diff, CT/MRI ± contrast,
X-ray 1 view vs 2 views, UA vs urine culture, CT abdomen vs abdomen+pelvis.

### Ingestion accuracy

* Column positions are detected per table, not assumed.
* Header percentages map to explicit price categories
  (`member`, `member_project_50`, `member_parents_50`,
   `parents_project_40`, `parents_project_60`, `vip`, `first_class_premium`, …).
* Missing price ≠ 0. Codes ≠ prices. RTL numbers are not reversed.
* Every value keeps page / sheet / cell provenance.
* Original source names are stored; normalization is for matching only.
* Issuing org ≠ provider. Unidentified centers show
  “Provider not specified in source”.

### Price comparison

```sql
SELECT ... FROM price_values
JOIN price_records ... JOIN examinations ... JOIN tariffs ...
ORDER BY price_values.value ASC
```

Multiple commercial columns become **separate comparison groups**.

---

## API (OpenAPI at `/docs`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/auth/login` | JWT |
| GET | `/api/v1/search?q=CBC&group=true` | Interpretation + grouped ascending prices |
| GET | `/api/v1/search/suggest?q=comp` | Autocomplete |
| POST | `/api/v1/search/resolve` | Batch identity resolution |
| GET | `/api/v1/search/filters` | Facets |
| GET | `/api/v1/examinations` | Catalog |
| POST | `/api/v1/examinations/{id}/aliases` | Add alias (reviewer+) |
| POST | `/api/v1/examinations/merge` | Merge after verification (admin) |
| POST | `/api/v1/examinations/split` | Split wrong merge (admin) |
| POST | `/api/v1/documents/upload` | Ingest one file |
| POST | `/api/v1/documents/ingest-folder` | Bootstrap folder ingest (admin) |
| POST | `/api/v1/documents/prices/correct` | Correct a price (audit-logged) |
| GET | `/api/v1/admin/reviews` | Review queue |
| GET | `/api/v1/stats` | Counts |

---

## Backup / restore

```powershell
python scripts/backup.py            # SQLite file backup → data/backup_*.db
python scripts/restore.py data/backup_YYYYMMDD_HHMMSS.db
```

PostgreSQL: `pg_dump -Fc` / `pg_restore` (see `scripts/backup.py`).

Never delete historical tariffs. Use `POST /documents/{id}/supersede`.

---

## Tests

```powershell
$env:PYTHONPATH = "E:\MedRAG\backend"
python -m pytest backend/tests -v
```

Mandatory CBC acceptance lives in `tests/test_xref.py::test_cbc_acceptance`.

---

## Layout

```
backend/app          FastAPI, models, xref, search, ingestion
backend/tests        pytest
frontend/src         React UI (search + admin)
scripts/             ingest, backup, restore, initdb
docker/              Dockerfiles, nginx
storage/documents    immutable originals
data/                SQLite + backups
```

See `docs/ARCHITECTURE.md` and `docs/OPERATIONS.md` for the full write-up.
