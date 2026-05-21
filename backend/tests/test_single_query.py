"""Single query test for EOL < 2 years with ReviewerAgent."""
import warnings
warnings.filterwarnings("ignore")

import requests
import json
import time

BASE = "http://localhost:8000"

print("=" * 70)
print("QUERY: get me part numbers which has EOL less than 2 years")
print("FILE : 561668-001-BOM-CC_4PinTop.pdf")
print("=" * 70)

t0 = time.time()
r = requests.post(f"{BASE}/query", json={
    "query": "get me part numbers which has EOL less than 2 years",
    "filename": "561668-001-BOM-CC_4PinTop.pdf"
}, timeout=600)
elapsed = time.time() - t0
print(f"\nHTTP {r.status_code}  ({elapsed:.1f}s)\n")

d = r.json()
print("message    :", d.get("message", ""))
excel = d.get("excel_data") or []
parts = d.get("parts_found") or []
print(f"excel rows : {len(excel)}")
print(f"parts found: {len(parts)}")
print()

if excel:
    print("Matching rows:")
    for row in excel:
        bno = row.get("BOM No", "?")
        part = str(row.get("Requested Part", "?"))
        mfr  = str(row.get("Manufacturer Name", "?"))[:25]
        yeol = row.get("YEOL", "?")
        eol  = row.get("EOL", "?")
        rohs = row.get("RoHS", "?")
        print(f"  BOM#{bno:>3} | {part:<20} | {mfr:<25} | YEOL={yeol!s:8} | EOL={eol!s:15} | RoHS={rohs}")
else:
    print("(no matching rows)")

print()
fmt = d.get("formatted_response") or ""
print("Formatted response:")
for line in fmt[:1000].splitlines():
    print(" ", line)
