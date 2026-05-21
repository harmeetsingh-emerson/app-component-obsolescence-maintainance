"""
Test the improved manufacturer-MPN pairing fix
"""

from app.ocr_processor import extract_sequential_mfr_mpn_pairs, clean_ocr_mpn

def test_sequential_pairing():
    """Test the new sequential pairing function"""
    
    print("=" * 80)
    print("MANUFACTURER-MPN PAIRING FIX TEST")
    print("=" * 80)
    print()
    
    # Test case from actual OCR output
    test_cases = [
        # ERAA26024 - the problematic case
        (
            "FRAA26024  [Choke, 2.2 UH L301 Yes [Panasonic JELG-TEA2R2NA Samsung. —S=« CIG21C2R2MINE",
            [
                ("Panasonic", "ELG-TEA2R2NA"),
                ("Samsung", "CIG21C2R2MNE"),  # MINE -> MNE (OCR fix)
            ]
        ),
        # Another example with multiple manufacturers
        (
            "ERAA26008 Cap Yes AVX 04023C104K TDK CGA2B3X7R1E104K050BB",
            [
                ("Avx", "04023C104K"),
                ("Tdk", "CGA2B3X7R1E104K050BB"),
            ]
        ),
        # Vishay Dale with two-word name
        (
            "ERAA26030 Res Vishay Dale CRCW040210M0FKED Yageo RC0402FR-07",
            [
                ("Vishay Dale", "CRCW040210M0FKED"),
                ("Yageo", "RC0402FR-07"),
            ]
        ),
        # United Chemi-Con with hyphen
        (
            "FRAA26014 Cap, Al, 10uF United Chemi-Con UVR2G100MHD",
            [
                ("United Chemi-Con", "UVR2G100MHD"),
            ]
        ),
    ]
    
    print("Testing MPN cleaning:")
    print("-" * 40)
    mpn_tests = [
        ("JELG-TEA2R2NA", "ELG-TEA2R2NA"),  # Remove leading J noise
        ("CIG21C2R2MINE", "CIG21C2R2MNE"),  # Fix MINE -> MNE
        ("[CRCW0402", "CRCW0402"),  # Remove leading bracket
        ("=RC0603FR", "RC0603FR"),  # Remove leading =
    ]
    
    for raw, expected in mpn_tests:
        result = clean_ocr_mpn(raw)
        status = "[OK]" if result == expected else "[FAIL]"
        print(f"  {status} '{raw}' -> '{result}' (expected: '{expected}')")
    
    print()
    print("Testing sequential pairing:")
    print("-" * 40)
    
    for test_line, expected_pairs in test_cases:
        print(f"\nInput: {test_line[:70]}...")
        print(f"Expected pairs: {expected_pairs}")
        
        result = extract_sequential_mfr_mpn_pairs(test_line)
        
        print(f"Actual pairs:")
        for pair in result:
            print(f"  - {pair['manufacturer']} -> {pair['mpn']}")
        
        # Check results
        if len(result) == len(expected_pairs):
            all_match = True
            for i, (exp_mfr, exp_mpn) in enumerate(expected_pairs):
                if i < len(result):
                    actual_mfr = result[i]['manufacturer']
                    actual_mpn = result[i]['mpn']
                    # Case-insensitive comparison for manufacturer
                    mfr_match = actual_mfr.lower() == exp_mfr.lower()
                    # MPN should match exactly
                    mpn_match = actual_mpn == exp_mpn
                    if not (mfr_match and mpn_match):
                        all_match = False
                        print(f"  [MISMATCH] Expected: {exp_mfr} -> {exp_mpn}")
            
            if all_match:
                print("  [OK] All pairs match!")
            else:
                print("  [FAIL] Some pairs don't match")
        else:
            print(f"  [FAIL] Expected {len(expected_pairs)} pairs, got {len(result)}")
    
    print()
    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    test_sequential_pairing()
