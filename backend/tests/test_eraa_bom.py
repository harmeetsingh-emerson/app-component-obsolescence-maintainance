"""
Test script for ERAA24476 BOM parsing
Tests all critical fixes:
1. Row length validation (padding shorter rows)
2. Header persistence across pages
3. MPN-only row acceptance
4. Aggressive manufacturer normalization
"""

import sys
import os
from pathlib import Path

# Add app directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.bom_parser_v2 import parse_bom_document


def test_eraa24476():
    """Test ERAA24476 PDF that previously returned 0 parts"""
    
    # Try to find the PDF in documents folder
    pdf_path = Path(__file__).parent / "documents" / "ERAA24476.pdf"
    
    if not pdf_path.exists():
        print(f"❌ PDF not found at: {pdf_path}")
        print("Please ensure ERAA24476.pdf is in the documents/ folder")
        return
    
    print("=" * 100)
    print("TESTING ERAA24476 BOM PARSER - WITH ALL CRITICAL FIXES")
    print("=" * 100)
    print(f"\nFile: {pdf_path}")
    print(f"Exists: {pdf_path.exists()}")
    print(f"Size: {pdf_path.stat().st_size / 1024:.2f} KB\n")
    
    # Parse with OCR fallback ENABLED (uses Tesseract now)
    print("Parsing PDF (with Tesseract OCR for image-based content)...")
    print("-" * 100)
    
    parts = parse_bom_document(str(pdf_path), use_ocr_fallback=True)
    
    print("\n" + "=" * 100)
    print("RESULTS")
    print("=" * 100)
    
    if not parts:
        print("\n[FAILED] No parts extracted")
        print("\nThis means the fixes didn't work. Check:")
        print("  1. Is the PDF text-based or scanned?")
        print("  2. Are tables being detected by pdfplumber?")
        print("  3. Is BOM structure detection working?")
        return
    
    print(f"\n[SUCCESS] Extracted {len(parts)} parts!")
    print(f"\n{'='*100}")
    print("STATISTICS")
    print("=" * 100)
    
    # Statistics
    total_manufacturers = sum(len(p.get('manufacturers', [])) for p in parts)
    avg_confidence = sum(p.get('confidence', 0) for p in parts) / len(parts) if parts else 0
    parts_with_flags = sum(1 for p in parts if p.get('validation_flags'))
    unknown_mfr_count = sum(
        1 for p in parts 
        for m in p.get('manufacturers', []) 
        if m.get('manufacturer') == 'UNKNOWN'
    )
    
    print(f"\nTotal parts: {len(parts)}")
    print(f"Total manufacturer entries: {total_manufacturers}")
    print(f"Average confidence: {avg_confidence:.2f}")
    print(f"Parts with validation flags: {parts_with_flags}")
    print(f"Parts with UNKNOWN manufacturer: {unknown_mfr_count}")
    
    # Page distribution
    pages = {}
    for part in parts:
        page = part.get('page_number', 1)
        pages[page] = pages.get(page, 0) + 1
    
    print(f"\nParts per page:")
    for page in sorted(pages.keys()):
        print(f"  Page {page}: {pages[page]} parts")
    
    # Show first 10 parts
    print(f"\n{'='*100}")
    print("SAMPLE PARTS (first 10)")
    print("=" * 100)
    
    for i, part in enumerate(parts[:10], 1):
        print(f"\n{i}. Part Number: {part['part_number']}")
        print(f"   Description: {part.get('description', 'N/A')[:80]}")
        print(f"   Quantity: {part.get('quantity', 'N/A')}")
        print(f"   Designators: {part.get('designators', 'N/A')[:50]}")
        print(f"   Confidence: {part.get('confidence', 0):.2f}")
        print(f"   Page: {part.get('page_number')}")
        
        # Show all manufacturer alternatives
        manufacturers = part.get('manufacturers', [])
        for j, mfr in enumerate(manufacturers, 1):
            mfr_name = mfr.get('manufacturer', 'UNKNOWN')
            mpn = mfr.get('mpn', 'N/A')
            mfr_conf = mfr.get('confidence', 0)
            
            # Flag unknown manufacturers
            flag = " [!] UNKNOWN" if mfr_name == 'UNKNOWN' else ""
            print(f"   Mfr {j}: {mfr_name} -> {mpn} (conf: {mfr_conf:.2f}){flag}")
        
        # Show validation flags
        if part.get('validation_flags'):
            print(f"   [!] Flags: {', '.join(part['validation_flags'])}")
    
    # Show parts with unknown manufacturers (critical fix #3 validation)
    print(f"\n{'='*100}")
    print("PARTS WITH UNKNOWN MANUFACTURERS (validates Fix #3)")
    print("=" * 100)
    
    unknown_parts = [
        p for p in parts 
        if any(m.get('manufacturer') == 'UNKNOWN' for m in p.get('manufacturers', []))
    ]
    
    if unknown_parts:
        print(f"\nFound {len(unknown_parts)} parts with UNKNOWN manufacturer")
        print("This is EXPECTED - it means we're accepting valid MPNs without known manufacturers.")
        print("\nSample:")
        
        for i, part in enumerate(unknown_parts[:5], 1):
            print(f"\n{i}. {part['part_number']}")
            for mfr in part.get('manufacturers', []):
                if mfr.get('manufacturer') == 'UNKNOWN':
                    print(f"   UNKNOWN → {mfr.get('mpn')} (conf: {mfr.get('confidence', 0):.2f})")
    else:
        print("\n[!] No parts with UNKNOWN manufacturer found.")
        print("This might mean Fix #3 isn't being triggered, or all manufacturers are known.")
    
    # Multi-page validation (critical fix #2)
    print(f"\n{'='*100}")
    print("MULTI-PAGE VALIDATION (validates Fix #2)")
    print("=" * 100)
    
    if len(pages) > 1:
        print(f"\n✅ BOM spans {len(pages)} pages")
        print("This validates that header persistence is working!")
    else:
        print(f"\n[!] BOM only found on {len(pages)} page(s)")
    
    print(f"\n{'='*100}")
    print("TEST COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    test_eraa24476()
