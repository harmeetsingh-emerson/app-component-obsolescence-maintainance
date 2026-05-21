"""
Test regex pattern on specific OCR lines
"""

import re

# Test lines
lines = [
    "ZN |ERAA26056 | Varistor, 350VAC Disc Imi [Yes [EPCOS B72214S0351K101_— [Bourns —————Ss=* MOV 1K DS @IKTR",
    "AN|ERAA26050_ [Power Supply, Sv, 25W GAPTEC Sacretw.05s3t ff"
]

# Regex patterns from OCR processor (updated with case-insensitive MPN)
generic_pattern = r'([A-Z][A-Za-z]{2,}(?:[\s\-][A-Z][A-Za-z]+)*)[\s\=\|\[\]]+([A-Za-z0-9][A-Za-z0-9\-_\.]{4,})(?:\s|$|[^\w])'
space_mpn_pattern = r'([A-Z][A-Za-z]{2,}(?:[\s\-][A-Z][A-Za-z]+)*)[\s\=\|\[\]]+([A-Za-z0-9][A-Za-z0-9\-_\.]{2,}\s[A-Za-z0-9][A-Za-z0-9\-_\.]{2,})(?:\s|$|[^\w])'

print("=" * 80)
print("Testing Regex Patterns")
print("=" * 80)

for i, line in enumerate(lines, 1):
    print(f"\nLine {i}: {line[:80]}...")
    print("-" * 80)
    
    # Test generic pattern
    matches = list(re.finditer(generic_pattern, line))
    print(f"Generic pattern matches: {len(matches)}")
    for match in matches:
        mfr = match.group(1)
        mpn = match.group(2)
        print(f"  → {mfr} | {mpn}")
    
    # Test space MPN pattern
    space_matches = list(re.finditer(space_mpn_pattern, line))
    print(f"Space MPN pattern matches: {len(space_matches)}")
    for match in space_matches:
        mfr = match.group(1)
        mpn = match.group(2)
        print(f"  → {mfr} | {mpn}")
