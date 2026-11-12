"""Management CLI.

  python -m app.cli init          # create tables, admin users, seed catalog
  python -m app.cli ingest PATH   # ingest file or folder
  python -m app.cli stats
  python -m app.cli search TERM
  python -m app.cli serve
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal, create_all
from app.main import _seed_admin
from app.models import Examination, ExaminationAlias, PriceRecord, PriceValue, SourceDocument
from app.search import SearchEngine
from app.xref import XRefEngine


def cmd_init(_: argparse.Namespace) -> None:
    create_all()
    _seed_admin()
    db = SessionLocal()
    try:
        xref = XRefEngine(db)
        n = xref.seed_catalog()
        print(f"Seeded {n} examinations. Admin user: {settings.ADMIN_USERNAME}")
    finally:
        db.close()


def cmd_ingest(args: argparse.Namespace) -> None:
    create_all()
    _seed_admin()
    db = SessionLocal()
    try:
        # ensure catalog exists before matching
        if db.query(Examination).count() == 0:
            XRefEngine(db).seed_catalog()
        pipe = __import__("app.ingestion.pipeline", fromlist=["IngestionPipeline"]).IngestionPipeline(db)
        p = Path(args.path)
        if not p.exists():
            print(f"Path not found: {p}")
            sys.exit(2)
        if p.is_dir():
            results = pipe.ingest_directory(p, recursive=not args.no_recursive)
        else:
            results = [pipe.ingest_file(p)]
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


def cmd_stats(_: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        def c(model):
            return db.query(model).count()

        print(json.dumps({
            "documents": c(SourceDocument),
            "examinations": c(Examination),
            "aliases": c(ExaminationAlias),
            "approved_aliases": db.query(ExaminationAlias).filter(ExaminationAlias.approval_status == "approved").count(),
            "price_records": c(PriceRecord),
            "price_values": c(PriceValue),
        }, indent=2))
    finally:
        db.close()


def cmd_search(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        eng = SearchEngine(db)
        data = eng.compare(args.term, limit=args.limit)
        interp = data.get("interpretation") or {}
        print("Query:", args.term)
        print("Canonical:", interp.get("canonical_en"), "/", interp.get("canonical_ar"),
              "conf=", interp.get("confidence"), "strategy=", interp.get("strategy"))
        for g in data.get("groups", []):
            print(f"\n--- {g['category']} ({g['count']}) ---")
            for r in g["rows"][: args.limit]:
                print(f"  {r['rank']:3d}  {r['price']!s:>10}  {r['provider_name'] or 'Provider not specified'}  "
                      f"{r['original_name'][:60]}  p{r['source_page']}")
    finally:
        db.close()


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


def main() -> None:
    p = argparse.ArgumentParser("medrag")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    ig = sub.add_parser("ingest")
    ig.add_argument("path")
    ig.add_argument("--no-recursive", action="store_true")
    sub.add_parser("stats")
    sg = sub.add_parser("search")
    sg.add_argument("term")
    sg.add_argument("--limit", type=int, default=15)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    args = p.parse_args()
    {"init": cmd_init, "ingest": cmd_ingest, "stats": cmd_stats, "search": cmd_search, "serve": cmd_serve}[args.cmd](args)


if __name__ == "__main__":
    main()
