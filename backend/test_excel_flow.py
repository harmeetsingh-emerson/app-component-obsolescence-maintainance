"""
Quick smoke test for the unified BOM parsing pipeline.
Tests Excel-style tables with real-world headers.
Run from backend/ directory:
    python test_excel_flow.py
"""
import sys
sys.path.insert(0, ".")

from app.file_converters import _parse_table_to_parts

SEPARATOR = "-" * 60

def run(label, table, expected_min=1):
    print(f"\n{SEPARATOR}")
    print(f"TEST: {label}")
    print(SEPARATOR)
    parts, detection = _parse_table_to_parts(table, label, page_num=1)
    print(f"\n>>> {len(parts)} part(s) found")
    for p in parts:
        mfrs = [(m["manufacturer"], m["mpn"]) for m in p.get("manufacturers", [])]
        print(f"  PN={p['part_number']!r:30}  desc={p['description']!r:30}  mfrs={mfrs}")
    ok = len(parts) >= expected_min
    print(f"{'[PASS]' if ok else '[FAIL]'} expected >= {expected_min} parts")
    return ok


results = []

# ── Test 1: typical Excel BOM (Level / Subclass columns) ──────────────────
results.append(run(
    "Excel BOM with Level+Subclass",
    [
        ["Level", "Part Number", "Subclass",      "Description",           "Qty", "Mfr Part / Cell / Xref", "Mfr. Name"],
        ["1",     "ABC-12345",   "CAPACITOR_CAP", "100nF 0402 X7R 10V",   "4",   "GRM155R71A104KA01D",     "Murata"],
        ["1",     "DEF-67890",   "RESISTOR_RES",  "10K 0402 1%",           "8",   "RC0402FR-0710KL",        "Yageo"],
        ["1",     "GHI-11111",   "IC",            "Op-Amp Dual 5V SOIC-8","2",   "LM358DR",                "Texas Instruments"],
    ],
    expected_min=2,
))

# ── Test 2: plain minimal BOM ─────────────────────────────────────────────
results.append(run(
    "Minimal BOM (Part No + MPN + Mfr)",
    [
        ["Part Number", "Description",    "Qty", "Manufacturer",  "MPN"],
        ["PN-001",      "Resistor 10K",   "10",  "Vishay",        "CRCW04021K00FKED"],
        ["PN-002",      "Cap 100uF 16V",  "5",   "Panasonic",     "EEU-FR1C101"],
        ["PN-003",      "MCU STM32F4",    "1",   "STMicroelectronics", "STM32F401CCU6"],
    ],
    expected_min=2,
))

# ── Test 3: cross-sheet mapping persistence ────────────────────────────────
print(f"\n{SEPARATOR}")
print("TEST: Cross-sheet last_mapping persistence")
print(SEPARATOR)
sheet1 = [
    ["Part Number", "Description", "Qty", "Manufacturer",  "MPN"],
    ["PN-A01",      "Resistor",    "10",  "Vishay",        "CRCW04021K00FKED"],
]
sheet2 = [
    # continuation sheet — no header, same columns
    ["PN-A02",  "Capacitor", "5", "Panasonic", "EEU-FR1C101"],
    ["PN-A03",  "Diode",     "2", "ON Semi",   "1N4148W-7-F"],
]
parts1, mapping = _parse_table_to_parts(sheet1, "Sheet1", page_num=1)
parts2, _       = _parse_table_to_parts(sheet2, "Sheet2 (no header)", page_num=2, last_mapping=mapping)
total = len(parts1) + len(parts2)
print(f"\n>>> Sheet1: {len(parts1)} part(s),  Sheet2 (reused mapping): {len(parts2)} part(s)  => Total: {total}")
ok = total >= 2
print(f"{'[PASS]' if ok else '[FAIL]'} expected >= 2 total parts")
results.append(ok)

# ── Test 4: JT BOM style ──────────────────────────────────────────────────
results.append(run(
    "JT BOM style (Manufacturers.Mfr Part / Cell / Xref)",
    [
        ["Item", "Part Number",  "Description",      "Manufacturers.Mfr Part / Cell / Xref", "Manufacturers.Mfr. Name"],
        ["1",    "JT-00100",     "Ferrite Bead",     "BLM15AG601SN1D",                       "Murata"],
        ["2",    "JT-00200",     "TVS Diode 5V",     "PESD5V0L1BA,115",                      "Nexperia"],
    ],
    expected_min=1,
))

# ── Summary ───────────────────────────────────────────────────────────────
print(f"\n{SEPARATOR}")
passed = sum(results)
print(f"SUMMARY: {passed}/{len(results)} tests passed")
print(SEPARATOR)
sys.exit(0 if passed == len(results) else 1)
