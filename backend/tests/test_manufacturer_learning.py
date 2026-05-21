"""
Test script for dynamic manufacturer learning feature
"""

from app.bom_parser_v2 import (
    KNOWN_MANUFACTURERS, 
    DEFAULT_MANUFACTURERS,
    load_manufacturers,
    save_manufacturers,
    learn_manufacturer,
    get_manufacturer_stats,
    validate_manufacturer,
    MANUFACTURERS_DB_PATH
)


def test_manufacturer_learning():
    """Test the manufacturer learning system"""
    
    print("=" * 80)
    print("MANUFACTURER LEARNING SYSTEM TEST")
    print("=" * 80)
    print()
    
    # Show initial state
    print("[STATS] Initial Database State:")
    stats = get_manufacturer_stats()
    print(f"  Total manufacturers: {stats['total']}")
    print(f"  Default manufacturers: {stats['default']}")
    print(f"  Learned manufacturers: {stats['learned']}")
    print(f"  Database file: {MANUFACTURERS_DB_PATH}")
    print()
    
    # Test 1: Check existing manufacturer
    print("Test 1: Validate known manufacturer")
    test_mfr = "Murata"
    is_valid, confidence = validate_manufacturer(test_mfr)
    print(f"  '{test_mfr}' -> Valid: {is_valid}, Confidence: {confidence:.2f}")
    print()
    
    # Test 2: Try to learn a new manufacturer
    print("Test 2: Learn new manufacturer")
    new_mfr = "Acme Electronics"
    new_mpn = "ACM-12345-X"
    was_learned = learn_manufacturer(new_mfr, new_mpn, auto_approve=True)
    print(f"  Learning '{new_mfr}' with MPN '{new_mpn}'")
    print(f"  Result: {'[OK] Learned (new)' if was_learned else '[SKIP] Already known'}")
    
    # Validate after learning
    is_valid, confidence = validate_manufacturer(new_mfr)
    print(f"  Validation after learning -> Valid: {is_valid}, Confidence: {confidence:.2f}")
    print()
    
    # Test 3: Try to learn the same manufacturer again
    print("Test 3: Try to learn same manufacturer again")
    was_learned_again = learn_manufacturer(new_mfr, new_mpn, auto_approve=True)
    print(f"  Learning '{new_mfr}' again")
    print(f"  Result: {'[OK] Learned (new)' if was_learned_again else '[SKIP] Already known'}")
    print()
    
    # Test 4: Learn another manufacturer
    print("Test 4: Learn another manufacturer")
    another_mfr = "GlobalTech Inc."
    another_mpn = "GT-9999-Z"
    was_learned = learn_manufacturer(another_mfr, another_mpn, auto_approve=True)
    print(f"  Learning '{another_mfr}' with MPN '{another_mpn}'")
    print(f"  Result: {'[OK] Learned (new)' if was_learned else '[SKIP] Already known'}")
    print()
    
    # Show updated stats
    print("[STATS] Updated Database State:")
    stats = get_manufacturer_stats()
    print(f"  Total manufacturers: {stats['total']}")
    print(f"  Default manufacturers: {stats['default']}")
    print(f"  Learned manufacturers: {stats['learned']}")
    print()
    
    # Test 5: Save to file
    print("Test 5: Save manufacturer database")
    success = save_manufacturers()
    print(f"  Save result: {'[OK] Success' if success else '[FAIL] Failed'}")
    if success:
        print(f"  Saved to: {MANUFACTURERS_DB_PATH}")
    print()
    
    # Test 6: Show some learned manufacturers
    if stats['learned'] > 0:
        print("[LIST] Learned Manufacturers:")
        learned = KNOWN_MANUFACTURERS - DEFAULT_MANUFACTURERS
        for mfr in sorted(list(learned)[:10]):  # Show first 10
            print(f"  - {mfr}")
        if len(learned) > 10:
            print(f"  ... and {len(learned) - 10} more")
    
    print()
    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    test_manufacturer_learning()
