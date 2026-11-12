#!/usr/bin/env python
"""Backup the MedRAG database.

SQLite: file copy while holding a read lock (WAL checkpoint).
PostgreSQL: pg_dump.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import settings


def backup_sqlite(dest: Path) -> None:
    url = settings.DATABASE_URL
    path = Path(url.replace("sqlite:///", "").replace("sqlite://", ""))
    if not path.exists():
        raise SystemExit(f"SQLite file not found: {path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(path))
    dst = sqlite3.connect(str(dest))
    src.backup(dst)
    dst.close()
    src.close()
    print(f"Backup written: {dest} ({dest.stat().st_size} bytes)")


def backup_pg(dest: Path) -> None:
    # postgresql+psycopg://user:pass@host:port/db
    url = settings.DATABASE_URL
    rest = url.split("://", 1)[1]
    userinfo, hostdb = rest.rsplit("@", 1)
    user, password = userinfo.split(":", 1)
    host, portdb = hostdb.split(":", 1)
    port, dbname = portdb.split("/", 1)
    cmd = [
        "pg_dump",
        "-h", host,
        "-p", port,
        "-U", user,
        "-F", "c",
        "-f", str(dest),
        dbname,
    ]
    env = {"PGPASSWORD": password}
    subprocess.check_call(cmd, env=env)
    print(f"Backup written: {dest}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--out", default=None)
    args = p.parse_args()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else Path(settings.DATA_DIR) / f"backup_{ts}.db"
    if settings.is_postgres:
        if out.suffix != ".dump":
            out = out.with_suffix(".dump")
        backup_pg(out)
    else:
        backup_sqlite(out)


if __name__ == "__main__":
    main()
