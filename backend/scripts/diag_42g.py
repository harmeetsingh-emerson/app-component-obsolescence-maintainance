"""
Diagnostic: trace why 42G5000-0193 returns wrong manufacturer/MPN pairing.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

TARGET_PN = "42G5000-0193"
TARGET_FILE = "JT105541.pdf"

# ── 1. FAISS store ──────────────────────────────────────────────────────────
print("=" * 60)
print("1. FAISS STORE")
print("=" * 60)
from app.faiss_bom_store import get_faiss_store
store = get_faiss_store()
print(f"Total vectors: {store.index.ntotal}, Total parts: {len(store.metadata)}")

# Find parts from JT105541.pdf
jt_parts = [p for p in store.metadata if (p.get("source_file") or "").endswith("JT105541")]
print(f"\nParts from JT105541* in FAISS: {len(jt_parts)}")
for p in jt_parts[:5]:
    print(f"  pn={p.get('part_number')}  mfrs={len(p.get('manufacturers', []))}")

# Exact search
exact = store.search_by_part_number(TARGET_PN)
if exact:
    print(f"\nEXACT MATCH found for {TARGET_PN}:")
    print(json.dumps(exact, indent=2)[:1500])
else:
    print(f"\nNO exact match for {TARGET_PN} in FAISS")

# Semantic search
sem = store.search(TARGET_PN, top_k=3)
print(f"\nSemantic top-3 for '{TARGET_PN}':")
for r in sem:
    pn = r.get("part_number", "?")
    src = r.get("source_file", "?")
    dist = r.get("distance", 0)
    mfrs = r.get("manufacturers", [])
    print(f"  pn={pn}  src={src}  dist={dist:.1f}")
    for m in mfrs[:3]:
        print(f"    [{m.get('preference',1)}] mfr={m.get('manufacturer')}  mpn={m.get('mpn')}")

# ── 2. OCR extraction store ─────────────────────────────────────────────────
print()
print("=" * 60)
print("2. OCR EXTRACTION STORE")
print("=" * 60)
from app.ocr_store import (
    get_ocr_text_for_source, search_ocr_extraction,
    get_ocr_processing_status, list_ocr_sources,
)
sources = list_ocr_sources()
print(f"OCR sources: {sources}")

ocr_text = get_ocr_text_for_source(TARGET_FILE)
if ocr_text:
    print(f"\nOCR text length for {TARGET_FILE}: {len(ocr_text)} chars")
    # Find context around TARGET_PN
    idx = ocr_text.upper().find(TARGET_PN.upper())
    if idx >= 0:
        snippet = ocr_text[max(0, idx-200):idx+400]
        print(f"\nContext around '{TARGET_PN}' in OCR text:")
        print(repr(snippet))
    else:
        print(f"\n'{TARGET_PN}' NOT found in OCR text")
        # Show first 500 chars
        print("\nFirst 500 chars of OCR text:")
        print(repr(ocr_text[:500]))
else:
    print(f"\nNo OCR text for {TARGET_FILE}")

# ── 3. Parse OCR text → look for this part ──────────────────────────────────
if ocr_text:
    print()
    print("=" * 60)
    print("3. PARSED PARTS FROM OCR TEXT")
    print("=" * 60)
    from app.ocr_processor import parse_ocr_bom_text
    parsed = parse_ocr_bom_text(ocr_text)
    print(f"Total parsed parts: {len(parsed)}")
    
    matched = [p for p in parsed if TARGET_PN.upper() in p.get("part_number", "").upper()]
    if matched:
        print(f"\nParts matching '{TARGET_PN}':")
        for p in matched:
            print(json.dumps(p, indent=2)[:1000])
    else:
        print(f"\nNO parts matching '{TARGET_PN}' in parsed output")
        # Show all 42Gxxxx parts
        g42 = [p for p in parsed if p.get("part_number", "").startswith("42G")]
        print(f"\nAll 42G* parts ({len(g42)}):")
        for p in g42[:10]:
            pn = p.get("part_number")
            mfrs = p.get("manufacturers", [])
            print(f"  pn={pn}  mfrs={len(mfrs)}")
            for m in mfrs[:3]:
                print(f"    [{m.get('preference',1)}] {m.get('manufacturer')} / {m.get('mpn')}")
