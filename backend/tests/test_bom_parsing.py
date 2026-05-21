"""
Test BOM parsing to debug why certain parts aren't being stored
"""

from app.bom_parser_v2 import parse_bom_document

# Test the actual ERAA24476.pdf file
pdf_path = "documents/ERAA24476.pdf"

print("=" * 80)
print("Testing BOM Parser on ERAA24476.pdf")
print("=" * 80)

parts = parse_bom_document(pdf_path, use_ocr_fallback=False)

print(f"\n{'=' * 80}")
print(f"RESULTS: Found {len(parts)} parts")
print(f"{'=' * 80}\n")

# Print each part
for i, part in enumerate(parts, 1):
    print(f"{i}. {part['part_number']}")
    print(f"   Description: {part.get('description', 'N/A')}")
    print(f"   Manufacturers: {len(part.get('manufacturers', []))}")
    for mfr in part.get('manufacturers', []):
        print(f"      - {mfr['manufacturer']}: {mfr['mpn']} (conf: {mfr['confidence']:.2f})")
    if part.get('validation_flags'):
        print(f"   Flags: {', '.join(part['validation_flags'])}")
    print()

# Check for missing parts
expected_parts = [
    'ERAA26008', 'ERAA26011', 'ERAA26018', 'ERAA26022', 'ERAA26029',
    'ERAA26030', 'ERAA26032', 'ERAA26035', 'ERAA26036', 'ERAA26037',
    'ERAA26038', 'ERAA26042', 'ERAA26050', 'ERAA26056'
]

found_parts = [p['part_number'] for p in parts]
missing_parts = [p for p in expected_parts if p not in found_parts]

print(f"\n{'=' * 80}")
print(f"MISSING PARTS: {len(missing_parts)}")
print(f"{'=' * 80}\n")
for mp in missing_parts:
    print(f"  - {mp}")
