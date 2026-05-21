"""
Test BOM parsing with manufacturer learning
"""

from app.bom_parser_v2 import parse_bom_document, get_manufacturer_stats, MANUFACTURERS_DB_PATH

def test_bom_with_learning():
    """Test BOM parsing with automatic manufacturer learning"""
    
    print("=" * 80)
    print("BOM PARSING WITH MANUFACTURER LEARNING TEST")
    print("=" * 80)
    print()
    
    # Show initial stats
    print("[STATS] Before parsing:")
    stats = get_manufacturer_stats()
    print(f"  Total manufacturers: {stats['total']}")
    print(f"  Learned manufacturers: {stats['learned']}")
    print()
    
    # Parse a BOM document
    test_file = "documents/ERAA24476.pdf"
    print(f"[FILE] Parsing: {test_file}")
    print()
    
    parts = parse_bom_document(test_file, use_ocr_fallback=False)
    
    print()
    print("=" * 80)
    print("PARSING RESULTS")
    print("=" * 80)
    print()
    
    if parts:
        print(f"✓ Extracted {len(parts)} parts")
        print()
        
        # Show first 3 parts
        print("Sample parts:")
        for i, part in enumerate(parts[:3], 1):
            print(f"\n{i}. {part['part_number']}")
            print(f"   Description: {part.get('description', 'N/A')[:60]}...")
            for j, mfr in enumerate(part.get('manufacturers', []), 1):
                print(f"   {j}. {mfr['manufacturer']} -> {mfr['mpn']} (conf: {mfr.get('confidence', 0):.2f})")
        
        # Collect all unique manufacturers from parsed parts
        print()
        print("[STATS] Manufacturers found in BOM:")
        unique_mfrs = set()
        for part in parts:
            for mfr_entry in part.get('manufacturers', []):
                mfr_name = mfr_entry.get('manufacturer', '')
                if mfr_name and mfr_name != 'UNKNOWN':
                    unique_mfrs.add(mfr_name)
        
        print(f"  Unique manufacturers: {len(unique_mfrs)}")
        for mfr in sorted(list(unique_mfrs)[:10]):
            print(f"    • {mfr}")
        if len(unique_mfrs) > 10:
            print(f"    ... and {len(unique_mfrs) - 10} more")
    else:
        print("✗ No parts extracted")
    
    print()
    print("[STATS] After parsing:")
    stats_after = get_manufacturer_stats()
    print(f"  Total manufacturers: {stats_after['total']}")
    print(f"  Learned manufacturers: {stats_after['learned']}")
    print(f"  New manufacturers learned: {stats_after['learned'] - stats['learned']}")
    print(f"  Database: {MANUFACTURERS_DB_PATH}")
    print()
    print("=" * 80)


if __name__ == "__main__":
    test_bom_with_learning()
