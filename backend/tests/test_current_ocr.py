"""Test current OCR system (Tesseract + ML Table Detection) on ERAA24476.pdf"""

from app.simple_bom_parser import parse_bom_document

pdf_path = "documents/ERAA24476.pdf"

print("=" * 80)
print("TESTING CURRENT OCR SYSTEM ON SCANNED PDF")
print("=" * 80)
print("\nUsing: Tesseract OCR + ML Table Detection")
print("DPI: 600, Upscaling: 2x, Preprocessing: Enhanced\n")

parts = parse_bom_document(pdf_path)

print("\n" + "=" * 80)
print("RESULTS")
print("=" * 80)

print(f"\nTotal parts extracted: {len(parts)}")

if parts:
    print(f"\nFirst 10 parts:")
    print("-" * 80)
    for i, part in enumerate(parts[:10], 1):
        print(f"\n{i}. Part Number: {part.get('part_number', 'N/A')}")
        
        # Show all manufacturers if available
        manufacturers = part.get('manufacturers', [])
        if manufacturers:
            print(f"   Manufacturers ({len(manufacturers)}):")
            for mfr in manufacturers:
                print(f"     - {mfr.get('manufacturer', 'N/A')} : {mfr.get('mpn', 'N/A')}")
        else:
            print(f"   Manufacturer: {part.get('manufacturer', 'N/A')}")
            print(f"   MPN: {part.get('mpn', 'N/A')}")
        
        desc = part.get('description', 'N/A')
        if len(desc) > 60:
            desc = desc[:60] + "..."
        print(f"   Description: {desc}")
        
        if part.get('quantity'):
            print(f"   Quantity: {part.get('quantity')}")
        if part.get('designators'):
            print(f"   Designators: {part.get('designators')}")
    
    if len(parts) > 10:
        print(f"\n... and {len(parts) - 10} more parts")
    
    # Show summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    total_manufacturers = sum(len(p.get('manufacturers', [])) for p in parts)
    if total_manufacturers == 0:
        total_manufacturers = sum(1 for p in parts if p.get('manufacturer'))
    
    print(f"Total Parts: {len(parts)}")
    print(f"Total Manufacturer Entries: {total_manufacturers}")
    print(f"Avg Manufacturers per Part: {total_manufacturers/len(parts):.2f}")
    
else:
    print("\n[!] No parts extracted")
    print("\nPossible reasons:")
    print("- PDF scan quality too poor for OCR")
    print("- Table structure not detected")
    print("- Text not recognizable by Tesseract")
    print("\nRecommendations:")
    print("1. Install Visual C++ Redistributable for better OCR (EasyOCR)")
    print("2. Request text-based PDF from supplier")
    print("3. Use Azure Document Intelligence (cloud service)")
