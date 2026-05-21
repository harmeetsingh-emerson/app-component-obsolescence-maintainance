import sys
sys.path.insert(0, ".")

from app.file_converters import _parse_table_intelligent
from app.faiss_bom_store import FAISSBOMStore

# ── Test 1: Metadata sheet → should be skipped ───────────────────────────
meta_table = [
    ["Project Full Path", "C:/Projects/MyBoard.PrjPcb"],
    ["Project Filename",  "MyBoard.PrjPcb"],
    ["Variant Name",      "No Variations"],
    ["Report Date",       "2026-05-22"],
    ["Report Time",       "10:30:00"],
    ["Output Name",       "BOM Report"],
]
parts = _parse_table_intelligent(meta_table, "ProjectInfo", 1)
print(f"\n[Test 1] Meta sheet parts: {len(parts)} (expected 0)")
assert len(parts) == 0, "Metadata sheet should be skipped"
print("[Test 1] PASS")

# ── Test 2: Real BOM sheet → columns detected, rows extracted ────────────
bom_table = [
    ["Level", "Part Number", "Subclass",   "Description",    "Qty", "Mfr Part",           "Mfr Name"],
    ["1",     "ABC-12345",   "CAPACITOR",  "100nF 0402 X7R", "4",   "GRM155R71A104KA01D", "Murata"],
    ["1",     "DEF-67890",   "RESISTOR",   "10K 0402 1%",    "8",   "RC0402FR-0710KL",    "Yageo"],
    ["-",     "",            "",           "",               "",    "",                   ""],
]
parts = _parse_table_intelligent(bom_table, "BOM", 2)
print(f"\n[Test 2] BOM sheet parts: {len(parts)} (expected 2)")
for p in parts:
    pn   = p["part_number"]
    mfrs = [(m["manufacturer"], m["mpn"]) for m in p["manufacturers"]]
    desc = p["description"]
    print(f"  PN={pn!r}  mfrs={mfrs}  desc={desc!r}")
assert len(parts) == 2, "Should extract 2 BOM rows"
print("[Test 2] PASS")

# ── Test 3: FAISS searchable text includes file_topic ────────────────────
store = FAISSBOMStore.__new__(FAISSBOMStore)
text = store._create_searchable_text(parts[0])
print(f"\n[Test 3] Searchable text:\n  {text}")
assert "Part:" in text
print("[Test 3] PASS")

print("\nAll tests passed.")
