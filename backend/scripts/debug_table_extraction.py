"""
Debug script to see what pdfplumber is extracting from ERAA24476
"""

import pdfplumber
from pathlib import Path


def debug_table_extraction():
    pdf_path = Path(__file__).parent / "documents" / "ERAA24476.pdf"
    
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return
    
    print("=" * 100)
    print("DEBUGGING TABLE EXTRACTION")
    print("=" * 100)
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            print(f"\n{'='*100}")
            print(f"PAGE {page_num}")
            print("=" * 100)
            
            tables = page.extract_tables()
            
            if not tables:
                print("No tables found")
                continue
            
            for table_idx, table in enumerate(tables, 1):
                print(f"\n--- Table {table_idx} ({len(table)} rows) ---")
                
                # Show first 5 rows
                for row_idx, row in enumerate(table[:5]):
                    print(f"Row {row_idx}: {row}")
                
                if len(table) > 5:
                    print(f"... and {len(table) - 5} more rows")


if __name__ == "__main__":
    debug_table_extraction()
