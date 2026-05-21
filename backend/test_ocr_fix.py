import re, sys
sys.path.insert(0, '.')
from app.ocr_processor import _correct_part_number, _PART_NUMBER_RE, parse_ocr_bom_text

# Test 1: Direct correction
tests = ['42G5000- 0193', '42G5000-0193', '42G2011- 0030', '41G3189- BA1']
print('--- _correct_part_number tests ---')
for t in tests:
    result = _correct_part_number(t)
    print(f'  {repr(t)} -> {repr(result)}')

# Test 2: Regex match on OCR line
print()
print('--- _PART_NUMBER_RE match tests ---')
lines = [
    '42G5000- 0193\tIC-VOLTAGE-REGULATOR-LINEAR\tU2\tSOT23-5-F-HSV',
    '42G5000-0193\tIC-VOLTAGE-REGULATOR-LINEAR',
]
for line in lines:
    m = _PART_NUMBER_RE.search(line)
    matched = m.group(0) if m else None
    print(f'  {repr(line[:45])} -> match={repr(matched)}')

# Test 3: Full OCR text parse with the artifact
print()
print('--- parse_ocr_bom_text test ---')
ocr_sample = """=== PAGE 2 ===
Item\tPart Number\tDescription\tRef Des\tPackage\tSource
1\tAD7689BCBZ-RL7\tIC-ADC\tU1\tSOT23
2\t42G5000- 0193\tIC-VOLTAGE-REGULATOR-LINEAR\tU2\tSOT23-5-F-HSV\tCAD-CELL
\tTLV74333PDBVR\tTEXAS INSTRUMENTS
3\t560325-020\tCONNECTOR\tJ1\tTHR
\tZW-20-10-G-D-400-093\tSAMTEC
"""
parts = parse_ocr_bom_text(ocr_sample)
print(f'Parts found: {len(parts)}')
for p in parts:
    mpns = [m.get('mpn') for m in p.get('manufacturers', [])]
    print(f'  PN={repr(p["part_number"])}  mpns={mpns}')
