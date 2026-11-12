# MedRAG Operations Manual

## 1. Install (Windows 11, no Docker)

```powershell
cd E:\MedRAG
python -m pip install -r backend\requirements.txt
copy .env.example .env   # edit SECRET_KEY / ADMIN_PASSWORD

# Initialize
$env:PYTHONPATH = "E:\MedRAG\backend"
python -c "import sys; sys.path.insert(0,'backend'); from app.cli import main; sys.argv=['medrag','init']; main()"
```

## 2. Run

```powershell
# API
python -c "import sys; sys.path.insert(0,'backend'); from app.cli import main; sys.argv=['medrag','serve','--host','0.0.0.0','--port','8000']; main()"

# UI (dev)
cd frontend; npm install; npm run dev     # http://localhost:5173

# UI (prod build served by FastAPI if frontend/dist exists)
npm run build
```

Health: `GET http://localhost:8000/api/health`
OpenAPI: `http://localhost:8000/docs`

## 3. Ingest

```powershell
python scripts\ingest_corpus.py          # the source PDFs / Excel in this workspace
# or
python -c "... app.cli ... ingest E:\path\to\file.pdf"
```

Re-ingesting the same file is a no-op (SHA-256 checksum).

## 4. Users & roles

| User | Password (default) | Role |
|---|---|---|
| admin | medrag-admin | full |
| reviewer | medrag-reviewer | upload, aliases, price corrections |
| viewer | medrag-viewer | search only |

Change passwords by updating the `users` table or `ADMIN_PASSWORD` before first
init. Create users: `POST /api/v1/admin/users`.

## 5. Daily operations

### Associate a document with the real provider

`PATCH /api/v1/documents/{id}` with `provider_id`
or `POST /api/v1/documents/{id}/associate-provider`.

### Approve / reject proposed aliases

Admin → Aliases, or:
`POST /api/v1/examinations/aliases/{id}/approve`
`POST /api/v1/examinations/aliases/{id}/reject`

### Merge / split canonical exams

```http
POST /api/v1/examinations/merge
{ "source_id": "...", "target_id": "...", "reason": "same CBC panel" }

POST /api/v1/examinations/split
{ "source_id": "...", "name_en": "CBC with Differential",
  "name_ar": "صورة دم كاملة مع فرق", "alias_ids": ["..."] }
```

Merges that hit `NEVER_MERGE` or attribute conflicts are refused (HTTP 400).

### Correct a price

```http
POST /api/v1/documents/prices/correct
{ "price_value_id": "...", "new_value": 460, "reason": "misread 460 as 46" }
```

Old value, new value, operator, timestamp and reason are written to `audit_logs`.
Historical records are never deleted.

### Supersede a tariff (new version uploaded)

`POST /api/v1/documents/{id}/supersede`

## 6. Backup & restore

```powershell
python scripts\backup.py
python scripts\restore.py data\backup_YYYYMMDD_HHMMSS.db
```

PostgreSQL:

```bash
pg_dump -Fc -U medrag medrag > backup.dump
pg_restore -U medrag -d medrag backup.dump
```

Schedule: daily SQLite copy or `pg_dump` cron. Keep originals in
`storage/documents` (they are content-addressed by checksum).

## 7. Environment

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | sqlite:///data/medrag.db | switch to `postgresql+psycopg://…` for prod |
| `SECRET_KEY` | change me | JWT signing |
| `EMBEDDING_PROVIDER` | local | `openai` for LM Studio / Ollama |
| `EMBEDDING_API_BASE` | http://localhost:1234/v1 | OpenAI-compatible |
| `OCR_ENABLED` | true | used when a PDF page has < 80 chars |
| `MAX_UPLOAD_MB` | 200 | |

## 8. Logging

`storage/logs/medrag.log` — rotating, 5 MB × 3. HTTP access + unhandled errors.

## 9. Troubleshooting

| Symptom | Action |
|---|---|
| `sqlite3.OperationalError: database is locked` | Only one ingest at a time; WAL is on. |
| Search returns 0 for a known exam | Check alias approval status; run resolve endpoint. |
| Arabic looks reversed in a newly added PDF | The parser repairs visual order; if a new layout still fails, capture a sample table and extend `_header_score`. |
| Docker not found | Expected on this host. Use local Uvicorn, or install Docker Desktop then `docker compose up`. |
| pandas DLL blocked | Not used. Ingestion uses openpyxl / pdfplumber / pymupdf only. |

## 10. Never do

* Delete historical tariff rows.
* Treat a vector score as a price.
* Merge CT with / without contrast because names look close.
* Put LLM output into `price_values.value`.
* Execute macros or embedded instructions from uploaded files.
