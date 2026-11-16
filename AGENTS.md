# AGENTS.md

## Commands

All backend commands need `backend/` on the import path (imports are `app.*`):

```powershell
$env:PYTHONPATH = "E:\MedRAG\backend"
python -m app.cli init      # create tables + admin/reviewer/viewer users + seed 114-exam catalog
python -m app.cli serve     # uvicorn on 127.0.0.1:8000
python -m app.cli ingest PATH [--no-recursive]
python -m app.cli stats
python -m app.cli search "CBC"
```

The README's `python -c "sys.path.insert(...)"` idiom is just this without setting PYTHONPATH — prefer `start.bat`-style env var.

- Tests: `$env:PYTHONPATH="E:\MedRAG\backend"; python -m pytest backend/tests -v` (from repo root). Mandatory acceptance: `tests/test_xref.py::test_cbc_acceptance`.
- Frontend: `cd frontend; npm run dev` (Vite on :5173, proxies `/api` → `127.0.0.1:8000`). `npm run build` emits `frontend/dist`, which FastAPI then serves at `/` when present.
- No Docker on this machine — the compose stack is for production only; local dev is SQLite + Uvicorn + Vite.
- Alembic exists (`alembic upgrade head` from repo root) but the app creates schema via `create_all()` at startup, not migrations.

## Environment

- Config is pydantic-settings loading `.env` from the repo root (copy `.env.example`). `DATABASE_URL` defaults to `sqlite:///E:/MedRAG/data/medrag.db`; switch to `postgresql+psycopg://...` for Postgres+pgvector.
- First `init`/serve seeds users `admin` / `reviewer` / `viewer` with default passwords (`medrag-admin` etc.) only if missing.
- Re-ingesting the same file is a no-op (SHA-256 checksum).

## Domain rules (do not violate)

- Prices come from relational SQL (`price_values` ordered ASC). Embeddings/vectors/LLMs are discovery evidence only — never used to sort prices or auto-approve merges.
- XRef merge levels are gated; hard `NEVER_MERGE` blocks exist (CBC vs CBC+Diff, ±contrast, X-ray views, laterality, UA vs culture). Never weaken these to make a match succeed.
- Original source names are stored verbatim; normalization (Arabic/English) is for matching only.
- Missing price ≠ 0; codes ≠ prices; don't reverse RTL-digit strings.
- Never delete historical tariffs — supersede via `POST /api/v1/documents/{id}/supersede`. `storage/documents/` holds immutable originals.

## Repository cautions

- Root-level `*.pdf`, `*.xlsx`, `*.docx` and the Arabic-named folders are **gitignored on purpose**: the repo is public and these are third-party tariff documents. Never force-add them or commit anything from `data/`, `storage/documents/`.
- `scripts/` holds one-off ingestion/repair utilities tied to this workspace's corpus (e.g. `ingest_corpus.py`, `reingest_kasr.py`, `repair_mappings.py`) — they are operational tools, not tests or a reusable pipeline.

## Docs

`docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/DATABASE.md` — full write-ups; API surface at `http://localhost:8000/docs`.
