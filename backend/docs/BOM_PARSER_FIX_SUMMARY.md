# BOM Parser Fix - Missing Manufacturers Issue

## Problem Summary
The BOM parser was missing some manufacturers when parsing PDF files. For example, part number `556150-1003` had 4 manufacturers in the source PDF but only 3 were being extracted. The missing manufacturer was **Stackpole Electronics, Inc** with MPN **RMEF0603FT100K**.

## Root Causes Identified

### 1. **Header Normalization Issue**
**File:** `app/bom_parser_v2.py` (Line 134)

**Problem:** Column headers containing newlines (e.g., "Manufacturer Part\nNumber 1") were not being properly normalized for pattern matching.

**Fix:** Modified header normalization to replace newlines with spaces:
```python
# Before:
normalized_headers = [str(cell).strip().lower() if cell else '' for cell in row]

# After:
normalized_headers = [str(cell).strip().replace('\n', ' ').replace('\r', ' ').lower() if cell else '' for cell in row]
```

### 2. **Limited Header Search Range**
**File:** `app/bom_parser_v2.py` (Line 128)

**Problem:** Only searching first 5 rows for headers, but some PDFs have metadata at the top and headers at row 6 or later.

**Fix:** Extended search range from 5 to 10 rows:
```python
# Before:
for row_idx, row in enumerate(table[:5]):

# After:
for row_idx, row in enumerate(table[:10]):
```

### 3. **Column Mapping Collision**
**File:** `app/bom_parser_v2.py` (Lines 140-165)

**Problem:** Multiple semantic types were being mapped to the same column. For example, "Manufacturer Part Number 1" would match both:
- "manufacturer" pattern → column gets mapped as manufacturer_1
- "manufacturer part number" pattern → same column gets remapped as mpn_1

This caused both manufacturer and MPN to point to the same column, leading to incorrect data extraction.

**Fix:** Implemented a priority-based matching system that:
1. Tracks which columns have already been assigned
2. Sorts patterns by length (longest first) for more specific matches
3. Ensures each column is assigned to only ONE semantic type

## Files Modified

1. **app/bom_parser_v2.py**
   - Fixed header normalization (newlines → spaces)
   - Extended header search range (5 → 10 rows)
   - Implemented priority-based column mapping to prevent collisions

2. **index-faiss-store/parts_readable.json**
   - Regenerated with all manufacturers correctly extracted
   - Increased from incomplete data to 80 total manufacturer entries

3. **index-faiss-store/parts.index** & **metadata.pkl**
   - Rebuilt FAISS index with complete manufacturer data

## Verification Results

✅ **Part 556150-1003** now correctly shows all 4 manufacturers:
1. Yageo - RC0603FR-07100KL
2. Bourns - CR0603-FX-1003ELF
3. **Stackpole Electronics, Inc - RMEF0603FT100K** ← Previously missing
4. Walsin Technology - WR06X1003FTL

✅ **Overall Statistics:**
- Total parts: 34
- Total manufacturer options: 80 (previously incomplete)
- Unique manufacturers: 25
- Parts with multiple manufacturers: 19

## Testing

Run the verification script to confirm the fix:
```bash
python final_verification.py
```

## Impact

This fix ensures that:
1. **All manufacturers** from the BOM PDF are correctly extracted
2. **Customer queries** will have access to complete manufacturer options
3. **Alternate sourcing** is more comprehensive with all available suppliers
4. **Data integrity** is maintained throughout the pipeline

## Technical Details

### Column Mapping (Before vs After)

**Before (Incorrect):**
```python
{
  'manufacturer_1': 9, 'mpn_1': 9,  # Both pointing to same column!
  'manufacturer_2': 15, 'mpn_2': 15,
  'manufacturer_3': 17, 'mpn_3': 17,
  'manufacturer_4': 19, 'mpn_4': 19
}
```

**After (Correct):**
```python
{
  'manufacturer_1': 8, 'mpn_1': 9,  # Correctly separated
  'manufacturer_2': 14, 'mpn_2': 15,
  'manufacturer_3': 16, 'mpn_3': 17,
  'manufacturer_4': 18, 'mpn_4': 19
}
```

### Pattern Matching Priority

The fix implements longest-match-first pattern matching to ensure more specific patterns take precedence:

```python
# Patterns are now sorted by length (longest first)
sorted_patterns = sorted(patterns, key=len, reverse=True)

# "manufacturer part number 1" matches before "manufacturer"
# "part number" matches before "part"
```

## Future Improvements

1. Add unit tests for BOM parser edge cases
2. Add validation warnings for parts with unexpected manufacturer counts
3. Consider supporting more BOM PDF formats with different column structures
