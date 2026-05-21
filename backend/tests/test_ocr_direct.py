"""
Test OCR extraction directly on ERAA24476
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.ocr_processor import ocr_pdf_to_text, parse_ocr_bom_text


def test_ocr():
    pdf_path = Path(__file__).parent / "documents" / "ERAA24476.pdf"
    
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return
    
    print("=" * 100)
    print("TESTING OCR ON ERAA24476 (Image-based PDF)")
    print("=" * 100)
    print(f"\nFile: {pdf_path}\n")
    
    # Extract text with OCR
    print("Starting OCR extraction (this may take 2-5 minutes)...")
    print("-" * 100)
    
    ocr_text = ocr_pdf_to_text(str(pdf_path))
    
    print("\n" + "=" * 100)
    print("OCR RESULTS")
    print("=" * 100)
    
    if not ocr_text:
        print("\n❌ OCR failed to extract any text")
        print("\nPossible causes:")
        print("  1. Ollama is not running (start with: ollama serve)")
        print("  2. llava:latest model is not installed (run: ollama pull llava:latest)")
        print("  3. Model is struggling with the PDF image quality")
        return
    
    print(f"\n✅ OCR extracted {len(ocr_text)} characters")
    
    # Save OCR output for inspection
    output_file = Path(__file__).parent / "ocr_output.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(ocr_text)
    
    print(f"\nOCR output saved to: {output_file}")
    
    # Show preview
    print("\n" + "=" * 100)
    print("OCR TEXT PREVIEW (first 1000 chars)")
    print("=" * 100)
    print(ocr_text[:1000])
    
    # Try to parse BOM data
    print("\n" + "=" * 100)
    print("PARSING BOM DATA FROM OCR")
    print("=" * 100)
    
    parts = parse_ocr_bom_text(ocr_text)
    
    if not parts:
        print("\n⚠️  No BOM parts extracted from OCR text")
        print("\nThis might mean:")
        print("  1. OCR text doesn't have pipe-separated table structure")
        print("  2. Header row not detected")
        print("  3. Data format doesn't match expected BOM structure")
        print("\nCheck ocr_output.txt to see what was actually extracted")
    else:
        print(f"\n✅ Extracted {len(parts)} parts from OCR!")
        
        # Show first 5 parts
        for i, part in enumerate(parts[:5], 1):
            print(f"\n{i}. {part.get('part_number', 'N/A')}")
            print(f"   Description: {part.get('description', 'N/A')[:80]}")
            for mfr in part.get('manufacturers', []):
                print(f"   {mfr.get('manufacturer', 'N/A')} → {mfr.get('mpn', 'N/A')}")


if __name__ == "__main__":
    test_ocr()
