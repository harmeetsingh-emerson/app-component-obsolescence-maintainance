"""
Test the improved OCR parser on ERAA24476 OCR debug output
"""

from app.ocr_processor import parse_tesseract_bom

# Load the OCR debug text
with open('documents/ERAA24476_ocr_debug.txt', 'r', encoding='utf-8') as f:
    ocr_text = f.read()

print("=" * 80)
print("Testing Tesseract BOM Parser on ERAA24476_ocr_debug.txt")
print("=" * 80)

parts = parse_tesseract_bom(ocr_text)

print(f"\n{'=' * 80}")
print(f"RESULTS: Found {len(parts)} parts")
print(f"{'=' * 80}\n")

# Print each part
for i, part in enumerate(parts, 1):
    print(f"{i}. {part['part_number']}")
    print(f"   Description: {part.get('description', 'N/A')[:60]}")
    print(f"   Manufacturers: {len(part.get('manufacturers', []))}")
    for mfr in part.get('manufacturers', []):
        print(f"      - {mfr['manufacturer']}: {mfr['mpn']}")
    print()

# Check for expected parts
expected_parts = [
    'ERAA26008', 'ERAA26011', 'ERAA26018', 'ERAA26022', 'ERAA26029',
    'ERAA26030', 'ERAA26032', 'ERAA26035', 'ERAA26036', 'ERAA26037',
    'ERAA26038', 'ERAA26042', 'ERAA26050', 'ERAA26056'
]

found_parts = [p['part_number'] for p in parts]
missing_parts = [p for p in expected_parts if p not in found_parts]
extra_parts = [p for p in found_parts if p not in expected_parts]

print(f"\n{'=' * 80}")
print(f"Expected: {len(expected_parts)} | Found: {len(found_parts)}")
print(f"{'=' * 80}\n")

if missing_parts:
    print(f"STILL MISSING ({len(missing_parts)}):")
    for mp in missing_parts:
        print(f"  - {mp}")
    print()

if extra_parts:
    print(f"EXTRA PARTS ({len(extra_parts)}):")
    for ep in extra_parts:
        print(f"  + {ep}")
