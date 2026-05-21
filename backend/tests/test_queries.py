"""
Manual query tests against the running server.
Run: .venv\Scripts\python.exe test_queries.py
"""
import requests
import json
import time

BASE = "http://localhost:8000"
BOM_FILE = "561668-001-BOM-CC_4PinTop.pdf"

TESTS = [
    # (label, query, filename_filter)
    ("EOL < 2 years",
     "get me part numbers which has EOL less than 2 years",
     BOM_FILE),
    ("RoHS compliant parts",
     "show me all RoHS compliant parts",
     BOM_FILE),
    ("Yageo parts",
     "get parts from manufacturer Yageo",
     BOM_FILE),
    ("5 random part numbers",
     "get me 5 part numbers",
     BOM_FILE),
    ("Capacitors",
     "show me capacitors",
     BOM_FILE),
    ("All parts",
     "get me all part numbers",
     BOM_FILE),
]


def run_query(label, query, filename=None):
    print("=" * 65)
    print(f"TEST: {label}")
    print(f"  Query   : {query}")
    print(f"  Filename: {filename or '(none)'}")
    print("-" * 65)

    payload = {"query": query}
    if filename:
        payload["filename"] = filename

    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/query", json=payload, timeout=300)
        elapsed = time.time() - t0
        print(f"  HTTP {r.status_code}  ({elapsed:.1f}s)")
        if r.status_code != 200:
            print(f"  ERROR: {r.text[:300]}")
            return

        d = r.json()
        excel = d.get("excel_data") or []
        parts = d.get("parts_found") or []
        msg   = d.get("message", "")
        fmt   = d.get("formatted_response", "") or ""

        print(f"  Message    : {msg}")
        print(f"  Excel rows : {len(excel)}")
        print(f"  Parts found: {len(parts)}")
        print()

        if excel:
            print("  Rows returned:")
            for row in excel[:15]:
                bom_no = row.get("BOM No", "?")
                part   = str(row.get("Requested Part", "?"))[:25]
                mfr    = str(row.get("Manufacturer Name", "?"))[:20]
                yeol   = row.get("YEOL", "?")
                eol    = row.get("EOL", "?")
                rohs   = row.get("RoHS", "?")
                print(f"    BOM#{bom_no:>3} | {part:<25} | {mfr:<20} | YEOL={yeol!s:6} | EOL={eol!s:12} | RoHS={rohs}")
            if len(excel) > 15:
                print(f"    ... and {len(excel) - 15} more rows")
        else:
            print("  (no excel rows)")

        print()
        print("  Formatted response (first 500 chars):")
        for line in fmt[:500].splitlines():
            print(f"    {line}")

    except Exception as e:
        print(f"  EXCEPTION: {e}")

    print()


if __name__ == "__main__":
    for label, query, fname in TESTS:
        run_query(label, query, fname)
        print()
