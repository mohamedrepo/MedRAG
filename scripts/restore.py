#!/usr/bin/env python
"""Restore a SQLite backup produced by backup.py."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from app.config import settings


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("backup", type=Path)
    args = p.parse_args()
    if not args.backup.exists():
        raise SystemExit(f"Backup not found: {args.backup}")
    url = settings.DATABASE_URL
    dest = Path(url.replace("sqlite:///", "").replace("sqlite://", ""))
    shutil.copy2(args.backup, dest)
    print(f"Restored {args.backup} -> {dest}")


if __name__ == "__main__":
    main()
