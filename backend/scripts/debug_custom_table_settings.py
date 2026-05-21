"""
Debug table extraction with custom settings
"""

import pdfplumber
from pathlib import Path


def debug_with_custom_settings():
    pdf_path = Path(__file__).parent / "documents" / "ERAA24476.pdf"
    
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        return
    
    print("=" * 100)
    print("TESTING CUSTOM TABLE EXTRACTION SETTINGS")
    print("=" * 100)
    
    # Try different extraction strategies
    strategies = [
        {
            "name": "Default",
            "settings": {}
        },
        {
            "name": "Text-based",
            "settings": {
                "vertical_strategy": "text",
                "horizontal_strategy": "text"
            }
        },
        {
            "name": "Lines-based",
            "settings": {
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines"
            }
        },
        {
            "name": "Explicit",
            "settings": {
                "vertical_strategy": "explicit",
                "horizontal_strategy": "explicit",
                "explicit_vertical_lines": [],
                "explicit_horizontal_lines": []
            }
        }
    ]
    
    with pdfplumber.open(pdf_path) as pdf:
        # Just test page 2 (the main BOM page)
        page = pdf.pages[1]  # Page 2
        
        for strategy in strategies:
            print(f"\n{'='*100}")
            print(f"STRATEGY: {strategy['name']}")
            print(f"Settings: {strategy['settings']}")
            print("=" * 100)
            
            try:
                tables = page.extract_tables(table_settings=strategy['settings'])
                
                if not tables:
                    print("No tables found")
                    continue
                
                for table_idx, table in enumerate(tables, 1):
                    print(f"\nTable {table_idx}: {len(table)} rows")
                    
                    # Show first 3 rows
                    for row_idx, row in enumerate(table[:3]):
                        # Show non-empty cells only
                        non_empty = [str(cell)[:50] for cell in row if cell]
                        if non_empty:
                            print(f"  Row {row_idx}: {non_empty}")
            
            except Exception as e:
                print(f"Error: {e}")


if __name__ == "__main__":
    debug_with_custom_settings()
