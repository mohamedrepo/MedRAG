# MedRAG Architecture

## 1. Goals

One centralized, expandable medical price intelligence database. A user searches
any examination by abbreviation, full name, Arabic or English, separator
variant, or code. The system resolves one canonical identity, then returns an
ascending, provenance-complete price comparison from structured rows.

Non-goals: LLM-generated prices, vector-store-as-source-of-truth, silent merges
of look-alike procedures.

## 2. Runtime topology

```
                    ┌────────────────────────┐
   Browser  ──HTTPS──▶  Nginx / Vite dev     │
   (AR RTL / EN LTR)  │  static React bundle  │
                    └──────────┬─────────────┘
                               │ /api/v1
                    ┌──────────▼─────────────┐
                    │  FastAPI (Uvicorn)     │
                    │  JWT · RBAC · Audit    │
                    │  Search · XRef · Ingest│
                    └──────────┬─────────────┘
                               │ SQLAlchemy
              ┌────────────────┴────────────────┐
              │                                 │
     SQLite (dev, this host)        PostgreSQL 16 + pgvector
     data/medrag.db                 pg_trgm · FTS · vector
```

Background ingestion runs in-process (FastAPI BackgroundTasks / CLI). A
separate worker container is unnecessary at current volume; the pipeline is
already isolated behind `IngestionPipeline` and can be lifted into Celery/RQ
without touching the API layer.

## 3. Data model (authoritative store)

| Table | Role |
|---|---|
| `organizations` | Issuing body (syndicate, university hospital authority, lab chain). **Not** the performing center. |
| `providers` | Actual medical center / branch. `provider_id` is nullable by design. |
| `source_documents` | Immutable original + checksum (SHA-256) + extraction status. Duplicate upload = same checksum. |
| `examinations` | Canonical identity. `canonical_code` (LAB-000001…), EN/AR names, `attributes` JSON (contrast, views, laterality, quantification). |
| `examination_aliases` | Original alias + normalized key + language + type + confidence + approval. Approved alias uniquely owns a normalized string. |
| `tariffs` | One price list: provider?, org?, document, year, validity, currency, status (`active`/`superseded`). |
| `price_records` | One source row. Keeps `original_name`, `original_code`, page/sheet/cell, confidence, review status. |
| `price_values` | **One monetary value.** Never collapse categories. `member` ≠ `parents_project_40`. |
| `pricing_modifiers` | Contrast, extra views, doctor fees, percentages, conditionals. |
| `review_items` | Low-confidence maps, provider-unspecified, conflicting prices. |
| `users` / `audit_logs` | RBAC + every alias/price/document mutation. |
| `search_cache` | normalized query → exam id (repeat lookups in ms). |
| `ingestion_reports` | Per-document stats, issues, warnings. |

### Why a child price-value table

Radiology 2026 Cairo has five commercial columns per row
(member, member×project 50%, member×parents 50%, parents×project 60%,
parents×project 40%). Storing one number would destroy the comparison.
`price_values` keeps each column as its own category; the UI groups by category
and sorts ascending inside each group.

## 4. Identity resolution (`app/xref.py`)

```
raw query
  → normalize (lower, strip punct, Alef/Ya/ta-marbuta, tashkeel, tatweel,
               Arabic-Indic digits, NFKC)
  → Stage 1  exact approved alias            conf 1.00
  → Stage 2  collapsed separators            conf 0.99   C.B.C → cbc
  → Stage 3  canonical EN or AR name         conf 0.96
  → Stage 4  acronym of full name            conf 0.94   MRI, CBC, ESR
  → Stage 5  lexical (Dice/Lev/Jaccard)      conf ≤ 0.90  + attribute gate
  → Stage 6  vector cosine                   conf 0.72, review only
```

Attribute gate — a merge is **refused** when any of these conflict:

* `contrast` with / without / الصبغة / بدون صبغة
* `views` 1 / 2 / 3 (AP vs AP+Lateral)
* `laterality` right / left / bilateral
* `quantification` qualitative / quantitative

`NEVER_MERGE_PAIRS` is a hard blocklist on top of the gate.

Vector similarity never approves an alias. It only proposes.

## 5. Normalization (`app/normalization.py`)

* English: lowercase, strip punctuation, collapse spaces.
* Arabic: strip diacritics + tatweel, unify أإآٱ→ا, ى→ي, ة→ه, ؤ→و, ئ→ي,
  Arabic-Indic digits→ASCII.
* Mixed: both.
* RTL visual-order repair for pdfplumber output (reverse whole Arabic phrases,
  not single words).
* Original names are never overwritten.

## 6. Embeddings (`app/embeddings.py`)

Default `local`: signed character n-gram hashing (2/3/4-grams + word hits),
L2-normalized, dim 256. Deterministic, offline, multilingual enough for
discovery.

Configurable `EMBEDDING_PROVIDER=openai` for LM Studio / Ollama / any
`/v1/embeddings` endpoint. Vectors are stored as base64 float32 on the
examination row. They are **not** the price store.

## 7. Ingestion (`app/ingestion/`)

```
register (checksum dedup)
  → parse (layout detect: syndicate | lab_list | simple_4 | generic | xlsx_package)
  → metadata (org / provider hints from title — provider may stay NULL)
  → per row: resolve identity → alias → PriceRecord + PriceValues
  → post-validate (conflicting prices same exam)
  → review queue for low confidence
  → ingestion report
```

Layout families in the current corpus:

| Parser | Documents |
|---|---|
| `syndicate` | أشعة 2026, تحاليل 2026, عيادات خارجية 2026 (CODE + 5 price columns) |
| `lab_list` | المختبر و البرج (Test ID / Category / Name / Price) |
| `simple_4` / `generic` | قصر العينى 2026 (serial / code / name / EGP) |
| `xlsx_package` | مستشفى الرحمة (room-class columns + component modifiers) |

Accuracy controls implemented:

* Headers read, not assumed; percentages in the header win over position.
* `parse_number` returns `None` for `-`, empty, N/A, letters. Never 0.
* Codes (`3117`, `5301`) are not prices.
* Missing provider is not a skip — the row is stored and the UI shows
  “Provider not specified in source”.
* Unresolved names still become a price record + proposed exam so the admin
  can merge; they are not invented into an existing canonical.

## 8. Search & comparison (`app/search.py`)

`SearchEngine.search` = xref + SQL join of
`price_values → price_records → examinations → tariffs → providers → documents`,
filters (category, city, governorate, provider, price category, year,
effective-only), `ORDER BY value ASC`, paginated.

`compare()` additionally groups by `price_category` and reports the cheapest
inside comparable groups. Prices are never sorted in Python from text.

## 9. Security

* JWT access + refresh, PBKDF2-SHA256 (120k iterations) password hashes.
* Roles: `admin` (merge/split/users/folder ingest), `reviewer` (upload, aliases,
  corrections), `viewer` (search + read).
* Upload: extension allow-list, size cap, magic-byte sniff (PDF/ZIP/PNG/JPEG).
* Originals stored read-only under `storage/documents/<checksum>_<name>`.
* Instructions inside uploaded documents are never executed.
* `audit_logs` records old value, new value, operator, timestamp, reason.

## 10. Performance

* Alias + collapsed caches in the XRef engine (rebuilt lazily).
* `search_cache` table for repeat queries.
* No embedding regeneration for unchanged examinations.
* Indexes: alias normalized, exam category, tariff year+status, price
  category+value, record exam+tariff.
* SQLite WAL + busy timeout for the dev store.
* Pagination on all list endpoints.

## 11. Extension points

* New format: add a parser returning `ParsedDoc` and register it in
  `IngestionPipeline.parse`.
* New price category: it is already a free string on `price_values`;
  add a display label in the UI.
* Real multilingual embedder: set `EMBEDDING_PROVIDER=openai`.
* Qdrant: not used. pgvector is enough at this scale; a separate vector DB
  would duplicate identity.
