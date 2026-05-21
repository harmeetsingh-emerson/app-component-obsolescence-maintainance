import sys
sys.path.insert(0, ".")

from app.file_converters import _parse_table_intelligent

bom_table = [
    ["Line #", "PART NUMBER", "Description", "Quantity", "Designator",
     "Manufacturer 1", "Manufacturer Part Number 1", "RoHS",
     "MANUFACTURER 2", "MANUFACTURER PART NUMBER 2",
     "MANUFACTURER 3", "MANUFACTURER PART NUMBER 3"],
    ["1", "563969-472", "Cap; Ceramic; 4.7nF; 250V", "6", "C1,C4",
     "KEMET Corporation", "CAS18C472KARGC", "Yes",
     "Yageo", "CC1812KKX7RBBB472",
     "Murata Manufacturing", "GA343DR7GD472KW01L"],
]

parts = _parse_table_intelligent(bom_table, "BOM", 1)
p = parts[0]

print("--- manufacturers ---")
for m in p["manufacturers"]:
    pref = m["preference"]
    mfr  = m["manufacturer"]
    mpn  = m["mpn"]
    print(f"  [{pref}] {mfr} : {mpn}")

print()
print("--- extra_fields ---")
for k, v in p["extra_fields"].items():
    print(f"  {k!r}: {v!r}")

# No duplicates: original-case key should NOT have a matching lowercase sibling
keys = list(p["extra_fields"].keys())
dups = [k for k in keys if k != k.lower() and k.lower() in keys]
print()
if dups:
    print(f"FAIL — duplicate lowercase keys found: {dups}")
else:
    print("PASS — no duplicate lowercase keys")

assert len(p["manufacturers"]) == 3, f"Expected 3 manufacturers, got {len(p['manufacturers'])}"
print("PASS — all 3 manufacturers promoted correctly")
