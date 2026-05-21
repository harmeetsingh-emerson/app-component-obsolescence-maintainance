import os, sys, time
os.environ['PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT'] = '0'
sys.path.insert(0, '.')

from app.ocr_processor import ocr_pdf_to_text, parse_ocr_bom_text

pdf = 'uploads/ERAA24476.pdf'
t0 = time.time()
text = ocr_pdf_to_text(pdf, dpi=150)
t1 = time.time()
print(f'OCR done in {t1-t0:.1f}s, {len(text)} chars')

parts = parse_ocr_bom_text(text)
t2 = time.time()
print(f'Parsed in {t2-t1:.1f}s: {len(parts)} parts')

for p in parts[:5]:
    mfrs = ', '.join(m['manufacturer'] for m in p.get('manufacturers', []))
    print(f"  {p['part_number']} -> {mfrs or '(no mfr)'}")
