"""Command-line acceptance checks matching the project brief.

Run after ingestion:
  python scripts/acceptance.py
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"E:\MedRAG\backend")

from app.database import SessionLocal
from app.search import SearchEngine
from app.xref import XRefEngine

CBC = ["CBC", "cbc", "C.B.C", "C-B-C", "C B C", "Complete Blood Picture", "Complete Blood Count", "صورة دم كاملة"]


def main() -> None:
    db = SessionLocal()
    x = XRefEngine(db)
    eng = SearchEngine(db)
    ok = True

    print("=== Mandatory CBC acceptance ===")
    ids = set()
    for t in CBC:
        r = x.resolve(t)
        code = r.examination.canonical_code if r.examination else None
        print(f"  {t!r:40} -> {code} conf={r.confidence:.2f} {r.strategy}")
        if code != "LAB-000001":
            ok = False
        ids.add(code)
    if ids != {"LAB-000001"}:
        ok = False
        print("  FAIL: CBC variants did not collapse to one exam")
    else:
        print("  PASS: all CBC variants → LAB-000001")

    print("\n=== Distinct variants ===")
    pairs = [
        ("MRI Brain", "RAD-010001"),
        ("MRI Brain with Contrast", "RAD-010002"),
        ("CT Abdomen", "RAD-000005"),
        ("CT Abdomen and Pelvis", "RAD-000007"),
        ("X-ray Knee AP", "RAD-020002"),
        ("X-ray Knee AP and Lateral", "RAD-020003"),
        ("Urine Analysis", "LAB-030001"),
        ("Urine Culture", "LAB-030002"),
    ]
    for term, expected in pairs:
        r = x.resolve(term)
        got = r.examination.canonical_code if r.examination else None
        flag = "PASS" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  {flag} {term!r} -> {got} (want {expected})")

    print("\n=== Price comparison (CT Brain) ===")
    t0 = time.perf_counter()
    data = eng.compare("CT Brain")
    dt = (time.perf_counter() - t0) * 1000
    print(f"  canonical={data['interpretation']['canonical_code'] if data['interpretation'] else None}"
          f" total={data['total']} groups={len(data.get('groups') or [])} {dt:.1f} ms")
    for g in (data.get("groups") or []):
        first = g["rows"][0] if g["rows"] else None
        if first:
            print(f"    {g['category']:24} n={g['count']:3} cheapest={first['price']} {first['provider_name'] or 'Provider not specified'}")

    print("\n=== Search latency (CBC) ===")
    t0 = time.perf_counter()
    d = eng.search("CBC")
    dt = (time.perf_counter() - t0) * 1000
    print(f"  {dt:.1f} ms  n={d['total']}")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    db.close()


if __name__ == "__main__":
    main()
