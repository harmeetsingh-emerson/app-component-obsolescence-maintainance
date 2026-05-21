"""
Debug script to see raw text extraction from ERAA24476
"""

import pdfplumber
from pathlib import Path


def debug_text_extraction():
    pdf_path = Path(__file__).parent / "documents" / "ERAA24476.pdf"
    
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return
    
    print("=" * 100)
    print("DEBUGGING RAW TEXT EXTRACTION")
    print("=" * 100)
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            print(f"\n{'='*100}")
            print(f"PAGE {page_num}")
            print("=" * 100)
            
            # Extract raw text
            text = page.extract_text()
            
            if text:
                lines = text.split('\n')
                print(f"Total lines: {len(lines)}")
                print(f"\nFirst 30 lines:")
                for i, line in enumerate(lines[:30], 1):
                    print(f"{i:3d}: {line}")
            else:
                print("No text extracted")


if __name__ == "__main__":
    debug_text_extraction()
