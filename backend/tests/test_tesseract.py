"""
Tesseract OCR implementation for ERAA24476 BOM extraction
This replaces llava with fast, accurate OCR for technical documents
"""

import fitz  # PyMuPDF
from pathlib import Path
from typing import Optional


def ocr_pdf_with_tesseract(pdf_path: str) -> str:
    """
    Extract text from PDF using Tesseract OCR
    Much faster and more accurate than llava for technical documents
    
    Args:
        pdf_path: Path to PDF file
        
    Returns:
        Extracted text from all pages
    """
    try:
        import pytesseract
        from PIL import Image
        import io
    except ImportError:
        print("[Tesseract] ERROR: Required packages not installed")
        print("[Tesseract] Install with: pip install pytesseract pillow")
        print("[Tesseract] Also install Tesseract binary from:")
        print("[Tesseract]   https://github.com/UB-Mannheim/tesseract/wiki")
        return ""
    
    print(f"[Tesseract] Starting OCR on: {pdf_path}")
    
    # Open PDF
    doc = fitz.open(pdf_path)
    num_pages = len(doc)
    all_text = []
    
    for page_num in range(num_pages):
        page = doc[page_num]
        
        print(f"[Tesseract] Processing page {page_num + 1}/{num_pages}...")
        
        # Render at 300 DPI for best OCR accuracy
        mat = fitz.Matrix(300/72, 300/72)
        pix = page.get_pixmap(matrix=mat)
        
        # Convert to PIL Image
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # OCR with Tesseract
        # PSM 6 = Assume uniform block of text
        # PSM 4 = Assume single column of variable size
        text = pytesseract.image_to_string(
            img, 
            config='--psm 6 --oem 3'  # PSM 6 = uniform block, OEM 3 = default+LSTM
        )
        
        if text.strip():
            all_text.append(f"=== PAGE {page_num + 1} ===\n{text}")
            print(f"[Tesseract]   Extracted {len(text)} characters")
        else:
            print(f"[Tesseract]   No text found")
    
    doc.close()
    
    combined = "\n\n".join(all_text)
    print(f"[Tesseract] Total: {len(combined)} characters from {num_pages} pages")
    
    return combined


def test_tesseract_ocr():
    """Test Tesseract OCR on ERAA24476"""
    
    pdf_path = Path(__file__).parent / "documents" / "ERAA24476.pdf"
    
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return
    
    print("=" * 100)
    print("TESTING TESSERACT OCR ON ERAA24476")
    print("=" * 100)
    print(f"File: {pdf_path}\n")
    
    # Extract text
    text = ocr_pdf_with_tesseract(str(pdf_path))
    
    if not text:
        print("\n❌ Tesseract OCR failed")
        print("\nMake sure:")
        print("  1. pytesseract is installed: pip install pytesseract pillow")
        print("  2. Tesseract binary is installed")
        print("     Windows: https://github.com/UB-Mannheim/tesseract/wiki")
        print("     Or: choco install tesseract")
        print("  3. Tesseract is in your PATH")
        return
    
    print(f"\n{'='*100}")
    print("OCR RESULTS")
    print("=" * 100)
    print(f"\n✅ Extracted {len(text)} characters")
    
    # Save output
    output_file = Path(__file__).parent / "tesseract_output.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(text)
    
    print(f"Output saved to: {output_file}")
    
    # Preview
    print(f"\n{'='*100}")
    print("PREVIEW (first 2000 chars)")
    print("=" * 100)
    print(text[:2000])
    
    # Now parse with BOM parser
    print(f"\n{'='*100}")
    print("PARSING BOM DATA")
    print("=" * 100)
    
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    
    from app.bom_parser_v2 import parse_bom_document
    
    # Note: parse_bom_document expects PDF, but we could refactor to accept text
    # For now, let's just show the raw text extraction is working
    
    # Check for BOM keywords
    keywords = ['ERAA', 'manufacturer', 'part number', 'description', 'quantity']
    found = [kw for kw in keywords if kw.lower() in text.lower()]
    
    print(f"\nBOM indicators found: {', '.join(found) if found else 'None'}")
    
    if len(found) >= 3:
        print("✅ Text contains BOM data!")
    else:
        print("⚠️  Text might not contain BOM data")


if __name__ == "__main__":
    test_tesseract_ocr()
