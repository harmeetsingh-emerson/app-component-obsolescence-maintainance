"""Test EasyOCR integration on ERAA24476.pdf"""

from app.simple_bom_parser import parse_bom_document

pdf_path = "documents/ERAA24476.pdf"

print("=" * 80)
print("TESTING EASYOCR ON SCANNED PDF")
print("=" * 80)

parts = parse_bom_document(pdf_path)

print("\n" + "=" * 80)
print("RESULTS")
print("=" * 80)

print(f"\nTotal parts extracted: {len(parts)}")

if parts:
    print(f"\nFirst 5 parts:")
    for i, part in enumerate(parts[:5], 1):
        print(f"\n{i}. {part.get('part_number', 'N/A')}")
        print(f"   Manufacturer: {part.get('manufacturer', 'N/A')}")
        print(f"   MPN: {part.get('mpn', 'N/A')}")
        print(f"   Description: {part.get('description', 'N/A')[:60]}...")
        
        # Show all manufacturers if available
        manufacturers = part.get('manufacturers', [])
        if manufacturers:
            print(f"   All Manufacturers ({len(manufacturers)}):")
            for mfr in manufacturers:
                print(f"     - {mfr.get('manufacturer')} : {mfr.get('mpn')}")
else:
    print("\n[!] No parts extracted")
