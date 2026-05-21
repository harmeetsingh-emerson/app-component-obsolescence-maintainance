"""
Advanced BOM Parser - Structured Approach for Text-Based PDFs

Pipeline:
1. PDF Input (text-based, not scanned)
2. Text & Table Extraction (pdfplumber)
3. BOM Structure Detection (detect BOM intent first)
4. Column-Aware Parsing (semantic mapping)
5. Entity Detection & Normalization
6. Validated Manufacturer ↔ Part Number Output

This approach prioritizes accuracy over coverage by:
- Detecting BOM structure BEFORE extracting data
- Using column-aware parsing (not raw regex)
- Validating against known manufacturers
- Adding confidence scoring
"""

import pdfplumber
import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


# Default manufacturers for validation (initial seed list)
DEFAULT_MANUFACTURERS = {
    'kemet', 'murata', 'yageo', 'tdk', 'samsung', 'panasonic', 'vishay',
    'avx', 'walsin', 'bourns', 'nichicon', 'rubycon', 'wurth', 'coilcraft',
    'nxp', 'ti', 'texas instruments', 'analog devices', 'adi', 'maxim',
    'infineon', 'on semi', 'stmicro', 'st micro', 'microchip', 'atmel', 'renesas',
    'linear tech', 'ltc', 'fairchild', 'onsemi', 'rohm', 'toshiba',
    'samsung electro', 'johanson', 'abracon', 'epson', 'kyocera',
    'molex', 'te connectivity', 'jst', 'amphenol', 'harwin', 'sullins',
    'analog', 'cypress', 'freescale', 'idt', 'intersil', 'lattice',
    'micron', 'spansion', 'xilinx', 'altera', 'broadcom', 'marvell',
    'qualcomm', 'skyworks', 'qorvo', 'triquint', 'rfmd',
    'stackpole', 'stackpole electronics', 'stackpole electronics inc',
    'littelfuse', 'bourns inc', 'walsin technology', 'walsin tech',
    'united chemi-con', 'chemi-con', 'cornell dubilier', 'cde',
    'eaton', 'schurter', 'bel fuse', 'bel', 'pulse electronics', 'pulse',
    '3m', 'three m', 'henkel', 'loctite', 'dow corning', 'dow',
    'diodes inc', 'diodes', 'lite-on', 'liteon', 'kingbright', 'cree', 'osram'
}

# Dynamic manufacturer list (loaded from file + learned during parsing)
KNOWN_MANUFACTURERS = set()

# Path to persistent manufacturer database
MANUFACTURERS_DB_PATH = Path(__file__).parent.parent / 'configs' / 'known_manufacturers.json'

# Column header mappings (semantic understanding)
COLUMN_MAPPINGS = {
    'manufacturer': [
        'mfgr', 'manufacturer', 'vendor', 'mfr', 'brand', 'supplier',
        'manufacturer 1', 'manufacturer 2', 'manufacturer 3', 'manufacturer 4',
        'mfgr 1', 'mfgr 2', 'mfgr 3', 'mfgr 4',
        'mfr 1', 'mfr 2', 'mfr 3', 'mfr 4',
        'mfgr1', 'mfgr2', 'mfgr3', 'mfgr4',  # No space variations
        'mfr1', 'mfr2', 'mfr3', 'mfr4',
        # Common variants in real-world BOMs
        'approved vendor', 'approved mfr', 'approved manufacturer',
        'component manufacturer', 'component mfr', 'comp mfr',
        'alt mfr', 'alt vendor', 'alt manufacturer',
        'alternate mfr', 'alternate manufacturer', 'alternate vendor',
        '2nd source', 'second source', '2nd mfr',
        'primary mfr', 'preferred mfr', 'preferred vendor',
        'source', 'make',
        # JT BOM / Agile PLM style
        'mfr. name', 'mfr name', 'manufacturers.mfr. name', 'manufacturers.mfr name',
    ],
    'mpn': [
        'mfgr p/n', 'manufacturer part number', 'mpn', 'mfg p/n', 'mfr p/n',
        'manufacturer pn', 'mfr pn', 'vendor p/n', 'vendor pn',
        'manufacturer part number 1', 'manufacturer part number 2',
        'manufacturer part number 3', 'manufacturer part number 4',
        'mfgr p/n 1', 'mfgr p/n 2', 'mfgr p/n 3', 'mfgr p/n 4',
        'mpn 1', 'mpn 2', 'mpn 3', 'mpn 4',
        'mfgr1 p/n', 'mfgr2 p/n', 'mfgr3 p/n', 'mfgr4 p/n',  # No space in mfgr
        'mfgr p/n1', 'mfgr p/n2', 'mfgr p/n3', 'mfgr p/n4',  # No space in number
        'mfgr1p/n', 'mfgr2p/n', 'mfgr3p/n', 'mfgr4p/n',  # Compact format
        'p/n 1', 'p/n 2', 'p/n 3', 'p/n 4',  # Just P/N with number
        'p/n1', 'p/n2', 'p/n3', 'p/n4',  # Compact P/N
        # Additional variants
        'approved p/n', 'approved pn', 'approved part no',
        'component pn', 'component p/n', 'component part no',
        'alt mpn', 'alt p/n', 'alt pn',
        'alternate mpn', 'alternate p/n',
        'mfg p/n', 'mfg pn', 'mfg part no', 'mfg no',
        'manufacturer p/n', 'manufacturer pn', 'manufacturer no',
        'mfr part no', 'mfr part no.', 'mfr no',
        'vendor part', 'vendor part no', 'vendor part number',
        'catalog number', 'cat no', 'cat number', 'order code',
        # JT BOM / Agile PLM style
        'mfr part / cell / xref', 'mfr part / cell', 'cell / xref',
        'mfr part', 'xref',
        'manufacturers.mfr part / cell / xref', 'manufacturers.mfr part',
    ],
    'part_number': [
        'part number', 'p/n', 'pn', 'part no', 'part #', 'internal pn',
        'tescom p/n', 'eraa pn', 'item', 'item no', 'item number',
        # Additional variants
        'component no', 'component no.', 'component number',
        'material no', 'material no.', 'material number', 'material',
        'internal p/n', 'internal part no',
        'drawing no', 'drawing number',
        'bom no', 'bom no.', 'bom number',
        'ref no', 'ref no.',
        'comp no', 'comp no.',
        'part id', 'part code',
        'find no', 'find number',
    ],
    'description': [
        'description', 'desc', 'part description', 'component description',
        'specification', 'component', 'details',
        'item description', 'component name', 'spec',
        'subclass', 'class', 'category', 'type', 'sub class',
    ],
    'quantity': [
        'qty', 'quantity', 'qnty', 'amount', 'count',
        'total qty', 'total quantity', 'req qty', 'required qty',
    ],
    'designators': [
        'reference', 'ref', 'ref des', 'designator', 'designators',
        'reference designator', 'location',
        'ref designator', 'reference des', 'refdes',
        'placement', 'position',
    ]
}

# Part number patterns (relaxed for common variations)
PART_NUMBER_PATTERNS = [
    r'ERAA\d{5}(-[A-Z0-9]+)?',  # ERAA12345 or ERAA12345-A1
    r'ERSA\d{5}',                # ERSA12345
    r'[A-Z]{2,}[0-9][A-Z0-9/+.\-]{3,}',  # SMBJ7.0A-E3/52, MAX6104EUR+T
    r'[A-Z0-9][A-Z0-9/+.\-]{4,}',  # Generic with special chars (/, +, ., -)
    r'[0-9]{3,6}[-_][0-9]{2,4}',  # 560330-222
    r'[A-Z0-9]{3,}-[A-Z0-9]{3,}',  # ABC-DEF123
]


@dataclass
class BOMTableDetection:
    """Result of BOM table detection"""
    is_bom: bool
    confidence: float
    column_map: Dict[str, int]
    header_row_idx: int
    bom_signals: List[str]


@dataclass
class ExtractedPart:
    """Extracted BOM part with validation"""
    part_number: str
    description: str
    manufacturers: List[Dict[str, str]]
    quantity: str = ""
    designators: str = ""
    confidence: float = 0.0
    page_number: int = 1
    validation_flags: List[str] = None
    
    def __post_init__(self):
        if self.validation_flags is None:
            self.validation_flags = []


# ============================================================================
# MANUFACTURER DATABASE MANAGEMENT
# ============================================================================

def load_manufacturers() -> set:
    """
    Load known manufacturers from persistent JSON file.
    Falls back to default list if file doesn't exist.
    """
    global KNOWN_MANUFACTURERS
    
    try:
        if MANUFACTURERS_DB_PATH.exists():
            with open(MANUFACTURERS_DB_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                manufacturers = set(data.get('manufacturers', []))
                print(f"[Manufacturer DB] Loaded {len(manufacturers)} manufacturers from {MANUFACTURERS_DB_PATH}")
                return manufacturers
        else:
            print(f"[Manufacturer DB] No database found, using {len(DEFAULT_MANUFACTURERS)} default manufacturers")
            return set(DEFAULT_MANUFACTURERS)
    except Exception as e:
        print(f"[Manufacturer DB] Error loading: {e}, using defaults")
        return set(DEFAULT_MANUFACTURERS)


def save_manufacturers() -> bool:
    """
    Save current manufacturer list to persistent JSON file.
    """
    try:
        # Ensure config directory exists
        MANUFACTURERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        # Save as sorted list for readability
        data = {
            'manufacturers': sorted(list(KNOWN_MANUFACTURERS)),
            'count': len(KNOWN_MANUFACTURERS),
            'last_updated': 'auto-learned from BOM parsing'
        }
        
        with open(MANUFACTURERS_DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        print(f"[Manufacturer DB] Error saving: {e}")
        return False


def learn_manufacturer(manufacturer: str, mpn: str = "", auto_approve: bool = True) -> bool:
    """
    Learn a new manufacturer and add to known list.
    
    Args:
        manufacturer: Manufacturer name to add
        mpn: Associated MPN (for validation context)
        auto_approve: If True, automatically add without validation
    
    Returns:
        True if manufacturer was added (new), False if already known
    """
    if not manufacturer or len(manufacturer) < 2:
        return False
    
    # Normalize for storage (lowercase, stripped)
    normalized = manufacturer.lower().strip()
    
    # Remove common suffixes for cleaner storage
    clean_name = re.sub(r'\s*(inc\.?|corp\.?|ltd\.?|llc|corporation|incorporated)$', '', normalized, flags=re.IGNORECASE)
    clean_name = clean_name.strip()
    
    # Check if already known
    if normalized in KNOWN_MANUFACTURERS or clean_name in KNOWN_MANUFACTURERS:
        return False
    
    # Auto-approve if flag set (default behavior during BOM parsing)
    if auto_approve:
        # Basic validation: must have mostly letters
        if sum(c.isalpha() for c in normalized) >= len(normalized) * 0.5:
            KNOWN_MANUFACTURERS.add(normalized)
            if clean_name != normalized:
                KNOWN_MANUFACTURERS.add(clean_name)
            print(f"[Manufacturer DB] 🆕 Learned new manufacturer: '{manufacturer}' (from MPN: {mpn})")
            return True
    
    return False


def get_manufacturer_stats() -> Dict[str, int]:
    """
    Get statistics about manufacturer database.
    """
    return {
        'total': len(KNOWN_MANUFACTURERS),
        'default': len(DEFAULT_MANUFACTURERS),
        'learned': len(KNOWN_MANUFACTURERS - DEFAULT_MANUFACTURERS)
    }


# Initialize manufacturer database on module load
KNOWN_MANUFACTURERS = load_manufacturers()


# ============================================================================
# BOM DETECTION & PARSING
# ============================================================================

# Token-based scoring weights per column semantic type
_COLUMN_TYPE_TOKENS: Dict[str, Dict[str, set]] = {
    'manufacturer': {
        'strong':   {'name', 'vendor', 'brand', 'supplier', 'source', 'make'},
        'medium':   {'manufacturer', 'manufacturers', 'mfgr', 'mfr'},
        'penalize': {'part', 'xref', 'cell', 'pn', 'mpn', 'number', 'catalog', 'code', 'order'},
    },
    'mpn': {
        'strong':   {'xref', 'cell', 'pn', 'mpn', 'catalog', 'code', 'order'},
        'medium':   {'part', 'number', 'no'},
        'penalize': {'name', 'vendor', 'brand', 'supplier'},
    },
    'part_number': {
        'strong':   {'bom', 'internal', 'drawing', 'find', 'item', 'level'},
        'medium':   {'part', 'number', 'pn', 'component', 'material', 'ref'},
        'penalize': {'mfr', 'mfgr', 'manufacturer', 'manufacturers', 'vendor', 'name'},
    },
    'description': {
        'strong':   {'description', 'desc', 'specification', 'spec', 'details',
                     'subclass', 'class', 'category', 'type'},
        'medium':   {'component', 'item'},
        'penalize': set(),
    },
    'quantity': {
        'strong':   {'qty', 'quantity', 'qnty', 'amount', 'count'},
        'medium':   {'total', 'req', 'required'},
        'penalize': set(),
    },
    'designators': {
        'strong':   {'designator', 'designators', 'refdes', 'placement'},
        'medium':   {'reference', 'ref', 'location', 'position'},
        'penalize': set(),
    },
}


def _score_header_for_type(header: str, semantic_type: str) -> float:
    """Return a score (0-2+) indicating how well a column header matches a semantic type."""
    import re as _re
    tokens = set(_re.split(r'[\s/\.\-_,]+', header.lower()))
    tokens.discard('')
    weights = _COLUMN_TYPE_TOKENS.get(semantic_type, {})
    score = 0.0
    for t in tokens:
        if t in weights.get('strong', set()):
            score += 1.0
        elif t in weights.get('medium', set()):
            score += 0.5
        if t in weights.get('penalize', set()):
            score -= 0.5
    return max(0.0, score)


def _infer_columns_from_content(
    table: List[List[str]],
    header_row_idx: int,
    column_map: Dict[str, int],
    n_cols: int,
) -> Dict[str, int]:
    """
    For columns not yet mapped, sample data rows and guess the semantic type
    from cell contents (e.g. alphanumeric+dash → likely MPN).
    Updates and returns a copy of column_map.
    """
    import re as _re
    col_map = dict(column_map)
    mapped_cols = set(col_map.values())

    data_rows = table[header_row_idx + 1: header_row_idx + 7]  # sample up to 6 rows
    if not data_rows:
        return col_map

    # Score each unmapped column
    mpn_pattern    = _re.compile(r'^[A-Z0-9][A-Z0-9/+.\-]{3,}$', _re.IGNORECASE)
    mfr_pattern    = _re.compile(r'^[A-Za-z][A-Za-z\s&,\.]{5,}$')

    col_mpn_hits   = {}
    col_mfr_hits   = {}

    for row in data_rows:
        for col_idx in range(n_cols):
            if col_idx in mapped_cols:
                continue
            cell = str(row[col_idx]).strip() if col_idx < len(row) else ''
            if not cell or cell.lower() in ('n/a', '-', 'none', ''):
                continue
            if mpn_pattern.match(cell) and ' ' not in cell:
                col_mpn_hits[col_idx] = col_mpn_hits.get(col_idx, 0) + 1
            if mfr_pattern.match(cell) and ' ' in cell:
                col_mfr_hits[col_idx] = col_mfr_hits.get(col_idx, 0) + 1

    # Assign best unambiguous hits (need ≥2 matching rows)
    if 'mpn' not in col_map:
        best_col = max(col_mpn_hits, key=col_mpn_hits.get, default=None)
        if best_col is not None and col_mpn_hits[best_col] >= 2 and best_col not in mapped_cols:
            col_map['mpn'] = best_col
            mapped_cols.add(best_col)
            print(f"[ContentInfer] Inferred mpn at col {best_col} "
                  f"({col_mpn_hits[best_col]} hits)")

    if 'manufacturer' not in col_map:
        best_col = max(col_mfr_hits, key=col_mfr_hits.get, default=None)
        if best_col is not None and col_mfr_hits[best_col] >= 2 and best_col not in mapped_cols:
            col_map['manufacturer'] = best_col
            mapped_cols.add(best_col)
            print(f"[ContentInfer] Inferred manufacturer at col {best_col} "
                  f"({col_mfr_hits[best_col]} hits)")

    return col_map


def detect_bom_structure(table: List[List[str]], page_num: int = 1) -> BOMTableDetection:
    """
    Step 3: Detect BOM intent BEFORE extracting data
    
    Returns BOMTableDetection with:
    - is_bom: True if this looks like a BOM table
    - confidence: 0-1 score
    - column_map: Semantic column mapping
    - header_row_idx: Index of header row
    - bom_signals: What indicators were found
    """
    
    if not table or len(table) < 2:
        return BOMTableDetection(False, 0.0, {}, -1, [])
    
    bom_signals = []
    column_map = {}
    header_row_idx = -1
    
    # Search first 10 rows for BOM headers (some PDFs have metadata at top)
    for row_idx, row in enumerate(table[:10]):
        if not row:
            continue
        
        # Normalize row headers (replace newlines with spaces for matching)
        normalized_headers = [str(cell).strip().replace('\n', ' ').replace('\r', ' ').lower() if cell else '' for cell in row]
        
        # Check for BOM indicator keywords
        row_text = ' '.join(normalized_headers)
        
        # Count BOM column matches
        matches = 0
        temp_column_map = {}
        assigned_columns = set()  # Track which columns have been assigned
        
        for col_idx, header in enumerate(normalized_headers):
            if col_idx in assigned_columns:
                continue  # Skip columns that are already mapped
            
            best_match = None
            best_pattern = ""
            best_key = None
            
            # Try to match against our semantic mappings (find longest/best match)
            for semantic_type, patterns in COLUMN_MAPPINGS.items():
                # Sort patterns by length (longest first) for more specific matches
                sorted_patterns = sorted(patterns, key=len, reverse=True)
                for pattern in sorted_patterns:
                    if pattern in header and len(pattern) > len(best_pattern):
                        # Handle numbered columns (Manufacturer 1, 2, 3, 4)
                        num_match = re.search(r'(\d+)', header)
                        if num_match and semantic_type in ['manufacturer', 'mpn']:
                            key = f"{semantic_type}_{num_match.group(1)}"
                        else:
                            key = semantic_type
                        
                        best_match = semantic_type
                        best_pattern = pattern
                        best_key = key
            
            # Assign the best match for this column
            if best_match:
                temp_column_map[best_key] = col_idx
                assigned_columns.add(col_idx)
                matches += 1
                bom_signals.append(f"Found '{best_pattern}' in column {col_idx}")
        
        # If we found strong BOM indicators, this is likely our header row
        # Require at least 2 BOM columns (e.g. just "Part No" + "Manufacturer")
        # With 3+ matches confidence becomes 0.6+ which passes the threshold.
        # With 2 matches + extra signals (data rows, etc.) it can still pass.
        if matches >= 2:
            column_map = temp_column_map
            header_row_idx = row_idx
            bom_signals.append(f"Header row detected at index {row_idx} with {matches} BOM columns")
            break
    
    # Additional BOM signals
    if header_row_idx >= 0:
        # Check for repeating row structures
        data_rows = table[header_row_idx + 1:]
        if len(data_rows) >= 5:
            bom_signals.append(f"Has {len(data_rows)} data rows")
        elif len(data_rows) >= 3:
            bom_signals.append(f"Has {len(data_rows)} data rows")
        
        # Check for aligned columns
        if len(column_map) >= 3:
            bom_signals.append(f"Has {len(column_map)} semantic columns")
        elif len(column_map) >= 2:
            bom_signals.append(f"Has {len(column_map)} semantic columns")
    
    # Calculate confidence
    confidence = 0.0
    if header_row_idx >= 0:
        confidence = min(1.0, len(bom_signals) * 0.2)
    
    is_bom = confidence >= 0.4  # Accept BOMs with at least 2 column matches + data rows
    
    print(f"[BOM Detection] Page {page_num}, Table: is_bom={is_bom}, confidence={confidence:.2f}")
    if bom_signals:
        for signal in bom_signals:
            print(f"  • {signal}")
    
    return BOMTableDetection(is_bom, confidence, column_map, header_row_idx, bom_signals)


def validate_manufacturer(manufacturer: str) -> Tuple[bool, float]:
    """
    Validate manufacturer against known list
    Returns (is_valid, confidence)
    """
    if not manufacturer or len(manufacturer) < 2:
        return False, 0.0
    
    mfr_lower = manufacturer.lower().strip()
    
    # Aggressive normalization to handle variations
    # Remove special characters, parentheses, common suffixes
    normalized = re.sub(r'[^a-z0-9 ]', '', mfr_lower)
    normalized = normalized.replace('electronics', '').replace('semiconductor', '')
    normalized = normalized.replace('technology', '').replace('corporation', '')
    normalized = normalized.replace('inc', '').replace('corp', '').replace('ltd', '')
    normalized = ' '.join(normalized.split())  # Collapse spaces
    
    # Exact match on original
    if mfr_lower in KNOWN_MANUFACTURERS:
        return True, 1.0
    
    # Exact match on normalized
    if normalized in KNOWN_MANUFACTURERS:
        return True, 1.0
    
    # Partial match on original
    for known_mfr in KNOWN_MANUFACTURERS:
        if known_mfr in mfr_lower or mfr_lower in known_mfr:
            return True, 0.9
    
    # Partial match on normalized
    for known_mfr in KNOWN_MANUFACTURERS:
        if known_mfr in normalized or normalized in known_mfr:
            return True, 0.85
    
    # Generic validation (looks like a company name)
    if len(mfr_lower) >= 3 and sum(c.isalpha() for c in mfr_lower) > len(mfr_lower) * 0.5:
        return True, 0.5  # Unknown but plausible
    
    return False, 0.0


def validate_mpn(mpn: str) -> Tuple[bool, float, List[str]]:
    """
    Validate manufacturer part number
    Returns (is_valid, confidence, flags)
    """
    flags = []
    
    if not mpn or len(mpn) < 3:
        return False, 0.0, ['too_short']
    
    mpn = mpn.strip()
    
    # Length check
    if len(mpn) < 5:
        flags.append('short_mpn')
    
    # Whitespace check (spaces inside MPN are not valid)
    if ' ' in mpn:
        flags.append('has_whitespace')
        return False, 0.0, flags
    
    # Must have at least one digit OR at least one letter (either is fine)
    has_digits = any(c.isdigit() for c in mpn)
    has_letters = any(c.isalpha() for c in mpn)
    
    if not has_digits and not has_letters:
        return False, 0.0, ['no_alphanumeric']
    
    if not has_digits:
        flags.append('no_digits')
    if not has_letters:
        flags.append('no_letters')
    
    # Pattern matching
    confidence = 0.7  # Base confidence
    
    for pattern in PART_NUMBER_PATTERNS:
        if re.match(pattern, mpn, re.IGNORECASE):
            confidence = 0.95
            flags.append('pattern_match')
            break
    
    is_valid = confidence >= 0.5
    
    return is_valid, confidence, flags


def parse_bom_row(row: List[str], column_map: Dict[str, int], page_num: int) -> Optional[ExtractedPart]:
    """
    Step 4 & 5: Column-aware parsing + validation
    
    Parse a single BOM row using semantic column mapping
    Only accepts candidates that appear in BOM context
    """
    
    if not row or not column_map:
        return None
    
    # Helper to normalize cell content (join multi-line text)
    def normalize_cell(cell) -> str:
        if not cell:
            return ""
        text = str(cell).strip()
        # Replace newlines/carriage returns with spaces for wrapped text
        text = text.replace('\n', ' ').replace('\r', ' ')
        # Collapse multiple spaces
        text = ' '.join(text.split())
        return text
    
    # Extract part number (required)
    part_number = None
    if 'part_number' in column_map:
        col_idx = column_map['part_number']
        if col_idx < len(row):
            part_number = normalize_cell(row[col_idx]) if row[col_idx] else None
    
    if not part_number or len(part_number) < 3:
        return None
    
    # Extract description
    description = ""
    if 'description' in column_map:
        col_idx = column_map['description']
        if col_idx < len(row):
            description = normalize_cell(row[col_idx]) if row[col_idx] else ""
    
    # Extract quantity
    quantity = ""
    if 'quantity' in column_map:
        col_idx = column_map['quantity']
        if col_idx < len(row):
            quantity = normalize_cell(row[col_idx]) if row[col_idx] else ""
    
    # Extract designators
    designators = ""
    if 'designators' in column_map:
        col_idx = column_map['designators']
        if col_idx < len(row):
            designators = normalize_cell(row[col_idx]) if row[col_idx] else ""
    
    # Extract manufacturers and MPNs (support 1-4)
    manufacturers = []
    validation_flags = []
    overall_confidence = 0.0
    
    # Determine max column index needed from column_map
    max_col_needed = max(column_map.values()) if column_map else 0
    
    # CRITICAL FIX #1: Check if row is too short for required columns
    # Allow shorter rows, but skip if critical columns are missing
    if len(row) < max_col_needed + 1:
        # Row is too short to contain all mapped columns
        return None
    
    for i in range(1, 5):
        mfr_key = f'manufacturer_{i}' if f'manufacturer_{i}' in column_map else 'manufacturer'
        mpn_key = f'mpn_{i}' if f'mpn_{i}' in column_map else 'mpn'
        
        if mfr_key not in column_map or mpn_key not in column_map:
            if i == 1:  # First manufacturer/MPN required
                continue
            else:
                break

        # If we're on iteration 2+ but there are no numbered columns (manufacturer_2,
        # mpn_2, etc.), we already processed the single manufacturer/mpn on i==1 — stop.
        if i > 1 and mfr_key == 'manufacturer' and mpn_key == 'mpn':
            break
        
        mfr_col = column_map[mfr_key]
        mpn_col = column_map[mpn_key]
        
        # CRITICAL FIX #1 (continued): Pad shorter rows instead of skipping
        # Safely extract values even if row is shorter than expected
        manufacturer = ""
        mpn = ""
        
        if mfr_col < len(row):
            manufacturer = normalize_cell(row[mfr_col]) if row[mfr_col] else ""
        if mpn_col < len(row):
            mpn = normalize_cell(row[mpn_col]) if row[mpn_col] else ""
        
        # Skip if both empty
        if not manufacturer and not mpn:
            continue
        
        # Validate manufacturer
        mfr_valid, mfr_conf = validate_manufacturer(manufacturer)
        
        # Validate MPN
        mpn_valid, mpn_conf, mpn_flags = validate_mpn(mpn)
        
        # AUTO-LEARN: If we have a valid MPN with an unknown manufacturer in BOM context, learn it
        if mpn_valid and manufacturer and not mfr_valid:
            # This is a new manufacturer appearing in a valid BOM row - learn it
            if learn_manufacturer(manufacturer, mpn, auto_approve=True):
                # Re-validate after learning
                mfr_valid, mfr_conf = validate_manufacturer(manufacturer)
        
        # CRITICAL FIX #3: Allow MPN-only rows within BOM context
        # Don't require both manufacturer AND MPN to be valid
        if mpn_valid:
            # Accept valid MPN even without manufacturer
            manufacturers.append({
                'manufacturer': manufacturer if manufacturer else 'UNKNOWN',
                'mpn': mpn,
                'preference': len(manufacturers) + 1,
                'confidence': (mfr_conf + mpn_conf) / 2 if manufacturer else mpn_conf * 0.8
            })
            overall_confidence = max(overall_confidence, (mfr_conf + mpn_conf) / 2 if manufacturer else mpn_conf * 0.8)
            
            # Flag if manufacturer is unknown
            if not manufacturer or not mfr_valid:
                validation_flags.append(f'unknown_manufacturer_{i}')
        elif manufacturer and mfr_valid:
            # Have manufacturer but no valid MPN - flag but don't include
            validation_flags.append(f'missing_or_invalid_mpn_{i}')
        else:
            # Neither valid - flag for debugging
            if manufacturer and not mfr_valid:
                validation_flags.append(f'unknown_manufacturer_{i}')
            if mpn and not mpn_valid:
                validation_flags.extend([f'{flag}_{i}' for flag in mpn_flags])
    
    # Only return if we have at least one manufacturer
    if not manufacturers:
        return None
    
    return ExtractedPart(
        part_number=part_number,
        description=description,
        manufacturers=manufacturers,
        quantity=quantity,
        designators=designators,
        confidence=overall_confidence,
        page_number=page_num,
        validation_flags=validation_flags
    )


def parse_bom_document(file_path: str, use_ocr_fallback: bool = True) -> List[Dict[str, any]]:
    """
    Main entry point: Parse BOM from PDF (text-based or image-based with OCR)
    
    Pipeline:
    1. Try text extraction first (pdfplumber)
    2. If no parts found, fall back to OCR (Ollama GLM-OCR)
    3. Detect BOM structure (column-aware)
    4. Parse rows with validation
    5. Return validated parts
    
    Args:
        file_path: Path to PDF file
        use_ocr_fallback: Enable OCR fallback for image-based PDFs (default: True)
    """
    
    parts = []
    # CRITICAL FIX #2: Persist last valid BOM mapping across pages
    last_bom_mapping = None
    
    try:
        print(f"[BOM Parser V2] Opening PDF: {file_path}")
        
        with pdfplumber.open(file_path) as pdf:
            print(f"[BOM Parser V2] Found {len(pdf.pages)} pages")
            
            for page_num, page in enumerate(pdf.pages, 1):
                print(f"\n[BOM Parser V2] Processing page {page_num}...")
                
                # Extract tables from page
                tables = page.extract_tables()
                
                if not tables:
                    print(f"  No tables found on page {page_num}")
                    continue
                
                print(f"  Found {len(tables)} table(s)")
                
                # Process each table
                for table_idx, table in enumerate(tables, 1):
                    print(f"\n  Table {table_idx}: {len(table)} rows")
                    
                    # Step 3: Detect BOM structure
                    detection = detect_bom_structure(table, page_num)
                    
                    # CRITICAL FIX #2: Use last valid mapping if current detection fails
                    if not detection.is_bom and last_bom_mapping:
                        print(f"    [!] Not a BOM table (confidence: {detection.confidence:.2f})")
                        print(f"    [!] Attempting to use previous BOM mapping from earlier pages...")
                        detection = last_bom_mapping
                        # Treat entire table as data rows (no header on continuation pages)
                        detection.header_row_idx = -1
                    elif not detection.is_bom:
                        print(f"    [X] Not a BOM table (confidence: {detection.confidence:.2f})")
                        continue
                    else:
                        # Valid BOM detected - save for future pages
                        last_bom_mapping = detection
                        print(f"    [OK] BOM table detected! (confidence: {detection.confidence:.2f})")
                        print(f"    Column mapping: {detection.column_map}")
                    
                    # Grab header cells for extra_fields capture
                    _header_row = table[detection.header_row_idx] if detection.header_row_idx >= 0 else []
                    _used_cols  = set(detection.column_map.values())

                    # Parse data rows (skip header if present)
                    data_rows = table[detection.header_row_idx + 1:] if detection.header_row_idx >= 0 else table
                    
                    for row_idx, row in enumerate(data_rows):
                        # Step 4 & 5: Parse with validation
                        part = parse_bom_row(row, detection.column_map, page_num)
                        
                        if part:
                            # Capture columns not consumed by the structured parser into extra_fields
                            extra_fields: dict = {}
                            for _ci, _hdr_cell in enumerate(_header_row):
                                if _ci in _used_cols:
                                    continue
                                _hdr = str(_hdr_cell).strip() if _hdr_cell else ""
                                _val = str(row[_ci]).strip() if _ci < len(row) and row[_ci] else ""
                                if _hdr and _val:
                                    extra_fields[_hdr] = _val

                            # Convert to dict format
                            part_dict = {
                                'part_number': part.part_number,
                                'description': part.description,
                                'manufacturers': part.manufacturers,
                                'quantity': part.quantity,
                                'designators': part.designators,
                                'confidence': part.confidence,
                                'page_number': part.page_number
                            }

                            if extra_fields:
                                part_dict['extra_fields'] = extra_fields
                            
                            if part.validation_flags:
                                part_dict['validation_flags'] = part.validation_flags
                            
                            parts.append(part_dict)
                    
                    print(f"    Extracted {len([p for p in parts if p['page_number'] == page_num])} parts from this table")
        
        print(f"\n[BOM Parser V2] [OK] Total parts extracted: {len(parts)}")
        
        # If no parts found and OCR fallback enabled, try OCR
        if len(parts) == 0 and use_ocr_fallback:
            print(f"\n[BOM Parser V2] No parts found via text extraction")
            print(f"[BOM Parser V2] Attempting OCR fallback with Ollama GLM-OCR...")
            
            try:
                from app.ocr_processor import ocr_pdf_to_text, parse_ocr_bom_text
                
                # Extract text using OCR
                ocr_text = ocr_pdf_to_text(file_path)
                
                if ocr_text:
                    print(f"[BOM Parser V2] OCR extracted {len(ocr_text)} characters")
                    
                    # Try to parse OCR text as BOM data
                    # Note: This requires the OCR text to be formatted as tables
                    # You may need to enhance parse_ocr_bom_text based on actual OCR output
                    ocr_parts = parse_ocr_bom_text(ocr_text)
                    
                    if ocr_parts:
                        parts.extend(ocr_parts)
                        print(f"[BOM Parser V2] OCR extracted {len(ocr_parts)} additional parts")
                    else:
                        print(f"[BOM Parser V2] OCR text did not contain parseable BOM data")
                        # Save OCR text for debugging
                        ocr_debug_file = file_path.replace('.pdf', '_ocr_debug.txt')
                        with open(ocr_debug_file, 'w', encoding='utf-8') as f:
                            f.write(ocr_text)
                        print(f"[BOM Parser V2] OCR text saved to: {ocr_debug_file}")
                else:
                    print(f"[BOM Parser V2] OCR failed to extract text")
                    
            except ImportError:
                print(f"[BOM Parser V2] OCR fallback not available (missing dependencies)")
                print(f"[BOM Parser V2] Install: pip install PyMuPDF requests")
            except Exception as ocr_error:
                print(f"[BOM Parser V2] OCR fallback error: {ocr_error}")
                import traceback
                traceback.print_exc()
        
        # Summary statistics
        if parts:
            total_mfr = sum(len(p.get('manufacturers', [])) for p in parts)
            avg_confidence = sum(p.get('confidence', 0) for p in parts) / len(parts)
            
            print(f"[BOM Parser V2] Total manufacturer entries: {total_mfr}")
            print(f"[BOM Parser V2] Average confidence: {avg_confidence:.2f}")
            print(f"[BOM Parser V2] Parts with validation flags: {sum(1 for p in parts if p.get('validation_flags'))}")
        
        # Save updated manufacturer database
        stats = get_manufacturer_stats()
        if stats['learned'] > 0:
            print(f"\n[Manufacturer DB] 💾 Saving database...")
            print(f"[Manufacturer DB] Total: {stats['total']} ({stats['default']} default + {stats['learned']} learned)")
            if save_manufacturers():
                print(f"[Manufacturer DB] ✓ Saved to: {MANUFACTURERS_DB_PATH}")
            else:
                print(f"[Manufacturer DB] ✗ Failed to save")
        
        return parts
    
    except Exception as e:
        print(f"[BOM Parser V2] Error: {e}")
        import traceback
        traceback.print_exc()
        return []


if __name__ == "__main__":
    # Test on your BOM PDF
    test_file = "documents/ERAA24476.pdf"
    
    print("=" * 80)
    print("BOM PARSER V2 - STRUCTURED APPROACH TEST")
    print("=" * 80)
    print(f"\nFile: {test_file}\n")
    
    parts = parse_bom_document(test_file)
    
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    
    if parts:
        print(f"\n[OK] Successfully extracted {len(parts)} parts\n")
        
        # Show first 5 parts
        for i, part in enumerate(parts[:5], 1):
            print(f"{i}. {part['part_number']}")
            print(f"   Description: {part.get('description', 'N/A')}")
            print(f"   Confidence: {part.get('confidence', 0):.2f}")
            print(f"   Page: {part.get('page_number')}")
            
            for j, mfr in enumerate(part.get('manufacturers', []), 1):
                print(f"   {j}. {mfr['manufacturer']} -> {mfr['mpn']} (conf: {mfr.get('confidence', 0):.2f})")
            
            if part.get('validation_flags'):
                print(f"   [!] Flags: {', '.join(part['validation_flags'])}")
            print()
    else:
        print("\n[X] No parts extracted")
