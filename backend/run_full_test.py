"""
Full end-to-end test of BOM parsing + SiliconExpert API flow
for all uploaded PDFs.
"""
import requests
import json
import time
import os
import sys

BASE = "http://localhost:8000"
DOCS_DIR = os.path.join(os.path.dirname(__file__), "documents")

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})

def sep(title=""):
    print()
    print("=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)


# ─────────────────────────────────────────────
# 0. Server health
# ─────────────────────────────────────────────
sep("0. SERVER HEALTH")
try:
    r = requests.get(f"{BASE}/health", timeout=5)
    h = r.json()
    print(f"  Status : {h.get('status')}")
    print(f"  Version: {h.get('version')}")
    idx = h.get("index_status", {})
    print(f"  FAISS parts  : {idx.get('total_parts', '?')}")
    print(f"  FAISS vectors: {idx.get('total_vectors', '?')}")
except Exception as e:
    print(f"  ERROR: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────
# 1. Indexed files
# ─────────────────────────────────────────────
sep("1. INDEXED FILES")
r = requests.get(f"{BASE}/files", timeout=5)
files_data = r.json()
if isinstance(files_data, list):
    files = files_data
else:
    files = files_data.get("files", [])

for f in files:
    if isinstance(f, dict):
        print(f"  {f.get('filename', f)} — {f.get('status', '')}")
    else:
        print(f"  {f}")
indexed_filenames = [f.get("filename") if isinstance(f, dict) else f for f in files]


# ─────────────────────────────────────────────
# 2. Trigger reindex (picks up ocr_complete files)
# ─────────────────────────────────────────────
sep("2. REINDEX (load all OCR-complete docs into FAISS)")
try:
    r = requests.post(f"{BASE}/reindex", timeout=60)
    print(f"  HTTP {r.status_code}")
    data = r.json()
    print(f"  {json.dumps(data, indent=4)}")
except Exception as e:
    print(f"  Reindex failed: {e}")

time.sleep(2)

# Refresh health
r = requests.get(f"{BASE}/health", timeout=5)
idx = r.json().get("index_status", {})
print(f"\n  FAISS parts after reindex: {idx.get('total_parts', '?')}")


# ─────────────────────────────────────────────
# 3. Upload any new PDFs found in /documents
# ─────────────────────────────────────────────
sep("3. UPLOAD PDFs NOT YET INDEXED")
pdf_files = [f for f in os.listdir(DOCS_DIR) if f.lower().endswith(".pdf")]
print(f"  Found {len(pdf_files)} PDF(s) in documents/: {pdf_files}")

for pdf_name in pdf_files:
    if any(pdf_name in str(f) for f in indexed_filenames):
        print(f"  [SKIP] {pdf_name} — already indexed/OCR-complete")
        continue
    pdf_path = os.path.join(DOCS_DIR, pdf_name)
    print(f"  [UPLOAD] {pdf_name}...")
    with open(pdf_path, "rb") as fh:
        resp = requests.post(
            f"{BASE}/upload",
            files={"file": (pdf_name, fh, "application/pdf")},
            data={"ocr_dpi": "200"},
            timeout=30,
        )
    print(f"    HTTP {resp.status_code}: {resp.json()}")


# ─────────────────────────────────────────────
# 4. Part-specific queries — one per indexed file
# ─────────────────────────────────────────────
sep("4. PART-SPECIFIC QUERIES (one per indexed file)")

# Refresh indexed files list
r = requests.get(f"{BASE}/files", timeout=5)
all_files = r.json() if isinstance(r.json(), list) else r.json().get("files", [])
filenames = [f.get("filename") if isinstance(f, dict) else f for f in all_files]
print(f"  Files available: {filenames}")

# Test queries — cover each indexed file
test_queries = [
    # 561668-001-BOM-CC_4PinTop.pdf  (text-based BOM, 34 parts)
    ("get me details of part number 560325-020", "561668-001-BOM-CC_4PinTop.pdf"),
    ("what is part 556112-224",                  "561668-001-BOM-CC_4PinTop.pdf"),
    # ERAA24476.pdf  (OCR BOM)
    ("tell me about part ERAA24476",             "ERAA24476.pdf"),
    # JT105541.pdf   (OCR BOM with 42G parts)
    ("what is part 42G5000-0193",                "JT105541.pdf"),
]

for query, expected_file in test_queries:
    print()
    print(f"  QUERY : {query}")
    print(f"  EXPECT: {expected_file}")

    payload = {"query": query, "filename": expected_file}
    try:
        t0 = time.time()
        r = requests.post(f"{BASE}/query", json=payload, timeout=120)
        elapsed = time.time() - t0
        print(f"  HTTP  : {r.status_code}  ({elapsed:.1f}s)")

        if r.status_code != 200:
            print(f"  ERROR : {r.text[:300]}")
            continue

        data = r.json()

        # Parts found
        parts = data.get("parts_found", data.get("parts", data.get("results", [])))
        print(f"  PARTS FOUND: {len(parts)}")
        for p in parts[:3]:
            pn = p.get("part_number", p.get("partNumber", "?"))
            mfrs = p.get("manufacturers", [])
            mfr_summary = ", ".join(
                f"{m.get('manufacturer')}:{m.get('mpn')}" for m in mfrs[:2]
            )
            print(f"    • {pn}  →  {mfr_summary or '(no mfr)'}")

        # SiliconExpert summary
        se_data = data.get("silicon_expert_data") or data.get("api_data")
        if se_data and isinstance(se_data, dict):
            result = se_data.get("Result", {})
            part_data_list = result.get("PartData", []) if isinstance(result, dict) else []
            if isinstance(part_data_list, dict):
                part_data_list = [part_data_list]
            valid = [
                p for p in part_data_list
                if isinstance(p, dict) and (p.get("PartList") or {}).get("PartDto")
            ]
            print(f"  SE PartData entries: {len(part_data_list)}  (valid with PartDto: {len(valid)})")
            for entry in valid[:2]:
                dto = entry["PartList"]["PartDto"]
                print(f"    SE: {dto.get('PartNumber')} | {dto.get('Manufacturer')} | "
                      f"Lifecycle={dto.get('Lifecycle')} | YEOL={dto.get('YEOL')}")
        else:
            print(f"  SE data: None (no SiliconExpert response)")

        # Excel rows
        excel = data.get("excel_data", data.get("excel_rows", []))
        print(f"  Excel rows: {len(excel)}")

    except Exception as e:
        print(f"  EXCEPTION: {e}")


# ─────────────────────────────────────────────
# 5. Generic query — show all parts from each file
# ─────────────────────────────────────────────
sep("5. GENERIC QUERY — ALL PARTS PER FILE")

for fname in filenames:
    print(f"\n  File: {fname}")
    payload = {"query": "show me all parts", "filename": fname}
    try:
        r = requests.post(f"{BASE}/query", json=payload, timeout=120)
        data = r.json()
        parts = data.get("parts_found", data.get("parts", data.get("results", [])))
        excel = data.get("excel_data", data.get("excel_rows", []))
        se_data = data.get("api_data") or data.get("silicon_expert_data")
        se_count = 0
        if se_data and isinstance(se_data, dict):
            result = se_data.get("Result", {})
            pd_list = result.get("PartData", []) if isinstance(result, dict) else []
            if isinstance(pd_list, dict):
                pd_list = [pd_list]
            se_count = len(pd_list)

        print(f"    Parts returned: {len(parts)}")
        print(f"    SE PartData   : {se_count}")
        print(f"    Excel rows    : {len(excel)}")

        # Sample first 3 parts with their SE data
        for p in parts[:3]:
            pn = p.get("part_number", "?")
            mfrs = p.get("manufacturers", [])
            mfr_str = " | ".join(
                f"{m.get('manufacturer')} / {m.get('mpn')}" for m in mfrs[:2]
            )
            print(f"      • {pn}: {mfr_str or '(no mfr)'}")

    except Exception as e:
        print(f"    EXCEPTION: {e}")

sep("TEST COMPLETE")
