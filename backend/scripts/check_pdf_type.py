"""
Check if PDF is truly text-based or image-based
"""

import fitz  # PyMuPDF
from pathlib import Path


def check_pdf_type():
    pdf_path = Path(__file__).parent / "documents" / "ERAA24476.pdf"
    
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return
    
    print("=" * 100)
    print("PDF TYPE ANALYSIS")
    print("=" * 100)
    
    doc = fitz.open(pdf_path)
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Extract text
        text = page.get_text()
        
        # Get images
        images = page.get_images()
        
        print(f"\nPage {page_num + 1}:")
        print(f"  Text characters: {len(text)}")
        print(f"  Images: {len(images)}")
        
        if len(text) > 100:
            print(f"  First 200 chars: {text[:200]}")
    
    doc.close()


if __name__ == "__main__":
    check_pdf_type()
