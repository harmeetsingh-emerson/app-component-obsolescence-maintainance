"""Test multi-line manufacturer extraction"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from app.ocr_processor import parse_tesseract_bom

text = open('documents/ERAA24476_ocr_debug.txt', encoding='utf-8').read()
parts = parse_tesseract_bom(text)

# Check specific parts that had missing manufacturers
check_parts = ['ERSA03316', 'ERSA03286', 'ERSA03264', 'ERAA26016', 'ERAA26023']
print("\n" + "="*60)
print("CHECKING PREVIOUSLY FAILING PARTS")
print("="*60)
for pn in check_parts:
    found = [p for p in parts if p['part_number'] == pn]
    if found:
        p = found[0]
        mfr_count = len(p['manufacturers'])
        print(f"\n{pn}: {mfr_count} manufacturer(s)")
        for m in p['manufacturers']:
            print(f"  - {m['manufacturer']}: {m['mpn']}")
        if mfr_count == 0:
            print("  (still no manufacturers)")
    else:
        print(f"\n{pn}: NOT FOUND")

# Summary
unique = sorted(set(p['part_number'] for p in parts))
with_mfr = [p for p in parts if p['manufacturers']]
without_mfr = [p for p in parts if not p['manufacturers']]

print(f"\n{'='*60}")
print(f"SUMMARY")
print(f"{'='*60}")
print(f"Total parts: {len(parts)}")
print(f"Unique: {len(unique)}")
print(f"With manufacturers: {len(with_mfr)}")
print(f"Without manufacturers: {len(without_mfr)}")
print(f"\nParts still without manufacturers:")
for p in without_mfr:
    print(f"  {p['part_number']}")
