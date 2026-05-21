"""
OCR Processor using PaddleOCR 3.0
Fallback for image-based PDFs when text extraction fails

PIPELINE:
1. Render each PDF page to image via PyMuPDF at 200 DPI
2. Run PaddleOCR 3.0 on each page image
3. Reassemble detected text blocks into lines
4. Parse lines for BOM part number / manufacturer data
"""

import os
import re

# Fix Windows OpenMP conflict between Intel (libiomp5md.dll) and LLVM (libomp140.dll).
# Must be set before paddle/numpy are imported.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Disable mkldnn-by-default in PaddleX so the runner uses plain 'paddle' mode
# instead of 'mkldnn', which triggers a PIR/oneDNN crash on Windows:
#   ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")

import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import fitz  # PyMuPDF for image extraction

# ============================================================================
# PADDLEOCR SINGLETON — initialise once per process, reuse on every call
# ============================================================================
_paddle_ocr_engine = None


def _get_paddle_ocr_engine():
    """Return the module-level PaddleOCR instance, creating it on first call.

    IMPORTANT — PaddleOCR 3.x downloads model weights *lazily* (on the first
    predict() call, NOT during __init__).  Without pre-warming, the first real
    document can hang for 20-40 minutes while the ~150 MB server models are
    downloaded.

    Fix: after __init__ we run predict() once on a tiny blank image so that
    model weights are cached before any document arrives.  This moves the
    one-time download cost to server start-up (visible in logs) instead of
    silently freezing a background task.
    """
    global _paddle_ocr_engine
    if _paddle_ocr_engine is not None:
        return _paddle_ocr_engine
    try:
        import numpy as np
        from paddleocr import PaddleOCR
        print("[OCR] Initialising PaddleOCR engine (one-time setup)...")

        # Try configs from most-optimised to most-compatible.
        # text_detection_model_name  → PP-OCRv5_mobile_det   (fast, ~5MB)
        # text_recognition_model_name → en_PP-OCRv5_mobile_rec (fast, ~5MB)
        # Without BOTH, PaddleOCR auto-picks PP-OCRv5_server_rec (85MB, very slow).
        _configs = [
            # Config A – v3 API, both mobile models, all heavy sub-models disabled
            dict(text_detection_model_name='PP-OCRv5_mobile_det',
                 text_recognition_model_name='en_PP-OCRv5_mobile_rec',
                 use_doc_orientation_classify=False,
                 use_doc_unwarping=False,
                 use_textline_orientation=False,
                 lang='en'),
            # Config B – v3 API without explicit model names (fallback)
            dict(use_doc_orientation_classify=False,
                 use_doc_unwarping=False,
                 use_textline_orientation=False,
                 lang='en'),
            # Config C – v2-style minimal (works on older paddleocr builds)
            dict(use_angle_cls=False, lang='en', show_log=False),
            # Config D – absolute minimum (any version)
            dict(lang='en'),
        ]

        engine = None
        for cfg in _configs:
            try:
                engine = PaddleOCR(**cfg)
                print(f"[OCR] PaddleOCR __init__ OK with config: {list(cfg.keys())}")
                break
            except TypeError as te:
                print(f"[OCR] Config {list(cfg.keys())} rejected ({te}), trying next...")

        if engine is None:
            print("[OCR] All PaddleOCR configs failed.")
            return None

        # --- PRE-WARM: run predict on a tiny blank image -----------------
        # This triggers the lazy model download NOW (at startup) so that
        # subsequent document OCR calls return quickly.
        print("[OCR] Pre-warming PaddleOCR (downloading/caching models)...")
        print("[OCR]   This may take several minutes on first run - please wait.")
        try:
            tiny = np.ones((64, 256, 3), dtype=np.uint8) * 255  # white 64×256 px
            list(engine.predict(tiny))
            print("[OCR] Pre-warm complete - models are cached.")
        except Exception as warm_exc:
            # Pre-warm failure is non-fatal; engine may still work on real docs
            print(f"[OCR] Pre-warm warning (non-fatal): {warm_exc}")

        _paddle_ocr_engine = engine
        print("[OCR] PaddleOCR engine ready.")
    except ImportError:
        print("[OCR] PaddleOCR is not installed. Install with: pip install paddleocr")
    except Exception as exc:
        print(f"[OCR] Failed to initialise PaddleOCR: {exc}")
    return _paddle_ocr_engine


# ============================================================================
# KNOWN MANUFACTURER LIST FOR IMPROVED PAIRING
# ============================================================================
# This list helps identify manufacturer names in OCR text for accurate pairing
# The list is used for DETECTION only - unknown manufacturers are still accepted
# Includes common OCR variations and partial matches
KNOWN_MANUFACTURERS_FOR_OCR = {
    # Passives
    'kemet', 'murata', 'yageo', 'tdk', 'samsung', 'panasonic', 'vishay',
    'avx', 'walsin', 'bourns', 'nichicon', 'rubycon', 'wurth', 'coilcraft',
    'stackpole', 'stackpole electronics', 'littelfuse', 'bourns inc',
    'walsin technology', 'walsin tech', 'united chemi-con', 'chemi-con',
    'cornell dubilier', 'cde', 'eaton', 'schurter', 'bel fuse', 'bel',
    'pulse electronics', 'pulse', 'epcos', 'koa', 'vishay dale',
    'samsung electro-mechanics', 'cal-chip electronics', 'cal-chip',
    # Semiconductors
    'nxp', 'ti', 'texas instruments', 'analog devices', 'adi', 'maxim',
    'infineon', 'on semi', 'stmicro', 'st micro', 'microchip', 'atmel', 'renesas',
    'linear tech', 'ltc', 'fairchild', 'onsemi', 'rohm', 'toshiba',
    'analog', 'cypress', 'freescale', 'idt', 'intersil', 'lattice',
    'micron', 'spansion', 'xilinx', 'altera', 'broadcom', 'marvell',
    'qualcomm', 'skyworks', 'qorvo', 'triquint', 'rfmd',
    'st microelectronics', 'microelectronics', 'diodes inc', 'diodes',
    # Connectors
    'molex', 'te connectivity', 'jst', 'amphenol', 'harwin', 'sullins',
    'samtec', 'amphenol fci', 'adam tech', 'adams tech',
    # Optoelectronics
    'lite-on', 'liteon', 'kingbright', 'cree', 'osram', 'lumileds',
    # Crystals/Oscillators
    'samsung electro', 'johanson', 'abracon', 'epson', 'kyocera',
    # Adhesives/Epoxies
    'henkel', 'loctite', 'dow corning', 'dow', 'momentive', 'dymax',
    '3m', 'three m', 'master bond', 'permabond',
    # PCB/Assembly
    'suntak', 'suntech', 'jabil', 'flex', 'foxconn',
    # Common OCR partial matches (manufacturer names cut off)
    'instruments', 'electronics', 'technology', 'semiconductor', 
    'electro-mechanics', 'ctro-mechanics', 'mechanics',  # OCR errors
    'nstruments', 'lectronics',  # OCR drops first letter
}

# Common OCR errors in manufacturer names
OCR_MANUFACTURER_CORRECTIONS = {
    'flectro-mechanics': 'electro-mechanics',
    'lectro-mechanics': 'electro-mechanics',
    'ctro-mechanics': 'electro-mechanics',
    'samsung flectro-mechanics': 'samsung electro-mechanics',
    'samsung lectro-mechanics': 'samsung electro-mechanics',
    'samsung flectro': 'samsung electro',
    'nstruments': 'instruments',
    'lectronics': 'electronics',
    'amsung': 'samsung',
    'anasonic': 'panasonic',
    'nfineon': 'infineon',
    'urata': 'murata',
    'ishay': 'vishay',
    'ourns': 'bourns',
    # Additional common OCR errors
    'cal-chip': 'cal-chip',  # Cal-Chip Electronics
    'suntak': 'suntak',  # SUNTAK PCB manufacturer
    'cad-cell': 'cad-cell',  # CAD-CELL footprint provider
    'analog devlces': 'analog devices',
    'analog devces': 'analog devices',
    'texas lnstruments': 'texas instruments',
    'texas lnstr': 'texas instruments',
}

# Common English/technical words that appear in BOM descriptions/columns but are NEVER MPNs.
# These prevent words like "SERIAL", "ANALOG", "DIGITAL" from being mistaken for MPNs.
INVALID_MPN_WORDS = {
    'serial', 'parallel', 'digital', 'analog', 'linear', 'output', 'input',
    'interface', 'module', 'board', 'circuit', 'device', 'signal', 'power',
    'ground', 'voltage', 'current', 'filter', 'bridge', 'driver', 'buffer',
    'switch', 'relay', 'sensor', 'crystal', 'clock', 'reset', 'enable',
    'latch', 'logic', 'memory', 'flash', 'sdram', 'eeprom', 'eprom',
    'controller', 'processor', 'amplifier', 'converter', 'regulator',
    'transistor', 'diode', 'resistor', 'capacitor', 'inductor', 'coil',
    'connector', 'header', 'socket', 'terminal', 'jumper', 'fuse',
    'single', 'double', 'triple', 'quad', 'octal', 'channel', 'stage',
    'package', 'surface', 'mount', 'through', 'hole', 'solder', 'reflow',
    'standard', 'optional', 'required', 'alternate', 'preferred',
    'series', 'model', 'grade', 'class', 'type', 'version', 'revision',
    'primary', 'secondary', 'backup', 'spare', 'active', 'passive',
    'general', 'special', 'custom', 'commercial', 'industrial', 'military',
}

# Known MPN suffixes that should NOT be treated as part numbers
# These are common manufacturer part number suffixes/patterns
MPN_SUFFIX_PATTERNS = [
    r'\d*BCBZ-RL\d*',  # e.g., 9BCBZ-RL7 from AD7689BCBZ-RL7
    r'PDBVR?$',  # Texas Instruments suffix
    r'K\d+N+C$',  # Samsung capacitor suffix like K05NNNC
    r'\d*FKED$',  # Vishay Dale suffix
    r'-T-?D$',  # Samtec suffix
    r'-RL$',  # Generic reel suffix
    r'-TR$',  # Tape and reel suffix
]


def combine_split_manufacturer_names(text: str) -> str:
    """
    Combine manufacturer names that may be split across lines/cells.
    
    In BOM tables, manufacturers like "SAMSUNG ELECTRO-MECHANICS" may have
    "SAMSUNG" on one line and "ELECTRO-MECHANICS" on the next. This function
    detects and combines such patterns.
    
    Args:
        text: OCR text that may have split manufacturer names
        
    Returns:
        Text with combined manufacturer names
    """
    # Pattern: word ending in manufacturer prefix + newline/space + manufacturer suffix
    combine_patterns = [
        # "SAMSUNG\nELECTRO-MECHANICS" -> "SAMSUNG ELECTRO-MECHANICS"
        (r'\b(SAMSUNG)\s*[\n\r]+\s*(ELECTRO-?MECHANICS)', r'\1 \2'),
        (r'\b(SAMSUNG)\s*[\n\r]+\s*(FLECTRO-?MECHANICS)', r'\1 ELECTRO-MECHANICS'),
        (r'\b(SAMSUNG)\s*[\n\r]+\s*(LECTRO-?MECHANICS)', r'\1 ELECTRO-MECHANICS'),
        # "TEXAS\nINSTRUMENTS" -> "TEXAS INSTRUMENTS"
        (r'\b(TEXAS)\s*[\n\r]+\s*(INSTRUMENTS?)', r'\1 INSTRUMENTS'),
        # "ANALOG\nDEVICES" -> "ANALOG DEVICES"
        (r'\b(ANALOG)\s*[\n\r]+\s*(DEVICES?)', r'\1 DEVICES'),
        # "CAL-CHIP\nELECTRONICS" -> "CAL-CHIP ELECTRONICS"
        (r'\b(CAL-?CHIP)\s*[\n\r]+\s*(ELECTRONICS?)', r'\1 ELECTRONICS'),
    ]
    
    result = text
    for pattern, replacement in combine_patterns:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    
    return result


def clean_ocr_mpn(mpn: str) -> str:
    """
    Clean up OCR artifacts from MPN strings
    
    Args:
        mpn: Raw MPN from OCR
        
    Returns:
        Cleaned MPN
    """
    if not mpn:
        return ""
    
    mpn = mpn.strip()
    
    # Remove leading OCR noise characters (common artifacts: J, [, =, |)
    mpn = re.sub(r'^[\[\]\|\=\s]+', '', mpn)
    # Remove leading single letters that are OCR noise (J, F, etc.)
    mpn = re.sub(r'^[JF](?=[A-Z]{2,})', '', mpn)
    
    # Remove trailing OCR noise
    mpn = re.sub(r'[\[\]\|\=\.\s]+$', '', mpn)
    
    # Remove spaces within MPN (likely OCR errors)
    mpn = mpn.replace(' ', '')
    
    # Fix common OCR character substitutions
    mpn = re.sub(r'^CRCWOUO', 'CRCW040', mpn)
    mpn = re.sub(r'^CRCWOU0', 'CRCW040', mpn)
    mpn = re.sub(r'^CRCWO', 'CRCW0', mpn)
    mpn = re.sub(r'^WRO(\d)', r'WR0\1', mpn)
    mpn = re.sub(r'^RKT(\d)', r'RK7\1', mpn)
    mpn = re.sub(r'HIETTP', 'H1ETTP', mpn)
    mpn = re.sub(r'REKED$', 'RFKED', mpn)
    mpn = re.sub(r'MOFKED$', 'M0FKED', mpn)
    
    # Fix common suffix OCR errors
    # CIG21C2R2MINE -> CIG21C2R2MNE (I→nothing, common in Samsung inductors)
    mpn = re.sub(r'MINE$', 'MNE', mpn)
    
    return mpn


def extract_sequential_mfr_mpn_pairs(text: str) -> List[Dict]:
    """
    Extract manufacturer-MPN pairs SEQUENTIALLY from text.
    
    This approach fixes cross-pairing issues by:
    1. Finding all manufacturer names and their positions
    2. For each manufacturer, extracting the MPN that follows (before next manufacturer)
    3. Handling OCR noise between manufacturer and MPN
    
    Args:
        text: OCR text line containing manufacturer-MPN data
        
    Returns:
        List of manufacturer-MPN pairs with confidence scores
    """
    pairs = []
    
    if not text:
        return pairs
    
    # Step 0a: Combine split manufacturer names (e.g., "SAMSUNG\\nELECTRO-MECHANICS")
    text = combine_split_manufacturer_names(text)
    
    # Step 0b: Apply OCR corrections before searching for manufacturers
    corrected_text = text
    corrected_lower = text.lower()
    for ocr_error, correction in OCR_MANUFACTURER_CORRECTIONS.items():
        pattern = r'\b' + re.escape(ocr_error) + r'\b'
        if re.search(pattern, corrected_lower):
            corrected_text = re.sub(pattern, correction, corrected_text, flags=re.IGNORECASE)
            corrected_lower = corrected_text.lower()
    
    # Step 1: Find all manufacturer positions (case-insensitive matching)
    mfr_positions = []
    text_lower = corrected_lower
    
    # Sort known manufacturers by length (longer first) to avoid partial matches
    sorted_mfrs = sorted(KNOWN_MANUFACTURERS_FOR_OCR, key=len, reverse=True)
    
    for mfr in sorted_mfrs:
        pos = 0
        while True:
            idx = text_lower.find(mfr, pos)
            if idx == -1:
                break
            
            # Check if this is a word boundary (not part of another word)
            before_ok = idx == 0 or not text_lower[idx-1].isalnum()
            after_ok = idx + len(mfr) >= len(text_lower) or not text_lower[idx + len(mfr)].isalnum()
            
            if before_ok and after_ok:
                # Check if this position overlaps with an already-found manufacturer
                overlap = False
                for existing_mfr, existing_pos, existing_len in mfr_positions:
                    if not (idx + len(mfr) <= existing_pos or idx >= existing_pos + existing_len):
                        overlap = True
                        break
                
                if not overlap:
                    # Get the actual case-preserved name from corrected text
                    actual_name = corrected_text[idx:idx + len(mfr)]
                    mfr_positions.append((actual_name, idx, len(mfr)))
            
            pos = idx + 1
    
    # Sort by position
    mfr_positions.sort(key=lambda x: x[1])
    
    # Step 2: For each manufacturer, extract the MPN that follows
    for i, (mfr_name, mfr_pos, mfr_len) in enumerate(mfr_positions):
        # Get text after this manufacturer until the next manufacturer (or end)
        start_pos = mfr_pos + mfr_len
        if i + 1 < len(mfr_positions):
            end_pos = mfr_positions[i + 1][1]
        else:
            end_pos = len(corrected_text)
        
        between_text = corrected_text[start_pos:end_pos]
        
        # Extract MPN from the text between this manufacturer and the next
        # MPN pattern: alphanumeric with dashes, underscores, dots (5+ chars)
        mpn_match = re.search(r'[\s\[\]\|\=\.\-]*([A-Za-z0-9][A-Za-z0-9\-_\.]{4,})', between_text)
        
        # Helper to emit a manufacturer+MPN pair (de-duplicated by mfr)
        def _emit(mfr_raw, mpn_val, conf=0.85):
            clean_mfr = mfr_raw.strip()
            # Fix OCR doubled-first-letter artifact (VVISHAY → VISHAY)
            if len(clean_mfr) >= 2 and clean_mfr[0].upper() == clean_mfr[1].upper() and clean_mfr[0].isalpha():
                clean_mfr = clean_mfr[1:]
            if clean_mfr.lower() in ['tdk', 'avx', 'nxp', 'ti', 'adi', 'ltc', 'koa', 'jst', 'cde', 'bel']:
                clean_mfr = clean_mfr.upper()
            else:
                clean_mfr = clean_mfr.title()
            pairs.append({'manufacturer': clean_mfr, 'mpn': mpn_val,
                          'preference': len(pairs) + 1, 'confidence': conf})

        if mpn_match:
            raw_mpn = mpn_match.group(1)
            mpn = clean_ocr_mpn(raw_mpn)
            
            # Validate MPN
            if len(mpn) >= 5:
                # Check that MPN has both letters and numbers
                has_letters = bool(re.search(r'[A-Za-z]', mpn))
                has_digits = bool(re.search(r'\d', mpn))
                
                # Skip if it's another part number (ERAA, ERSA pattern)
                is_part_number = bool(re.match(r'^(ERAA|ERSA|FRAA|FRSA)\d+', mpn, re.IGNORECASE))
                
                if has_letters and has_digits and not is_part_number:
                    _emit(mfr_name, mpn)
        else:
            # Short-numeric MPN fallback: known manufacturers sometimes have
            # 2-4 digit product codes (e.g. Henkel 383, Dow Corning 340).
            mfr_key = mfr_name.strip().lower()
            if mfr_key in KNOWN_MANUFACTURERS_FOR_OCR:
                short_match = re.search(r'(?:^|[\s\t\|])([1-9]\d{1,3})(?:\s|\t|\||$)', between_text)
                if short_match:
                    _emit(mfr_name, short_match.group(1).strip(), conf=0.75)
    
    return pairs


def ocr_pdf_to_text(pdf_path: str, dpi: int = 150) -> str:
    """
    Extract text from entire PDF.

    Pipeline (per page):
    1. Try PyMuPDF native text extraction — fast, zero OCR cost.
    2. If native text is insufficient (<50 chars) fall back to PaddleOCR:
       a. Render page to a numpy RGB image at *dpi* (default 150 — good
          accuracy for typical BOM tables while ~44% fewer pixels than 200).
       b. Run the shared PaddleOCR engine (singleton — initialised once).
       c. Sort detected blocks top-to-bottom and reassemble into lines.

    Args:
        pdf_path: Path to the PDF file.
        dpi:      Render resolution for OCR fallback (default 200 DPI).

    Returns:
        Combined text from all pages, or "" on failure.
    """
    print(f"[OCR] Extracting text from: {pdf_path}")

    try:
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
        all_text = []
        ocr_engine = None  # Lazy — only loaded if a page needs it

        for page_num in range(num_pages):
            page = doc[page_num]
            print(f"[OCR] Processing page {page_num + 1}/{num_pages}...")

            # ------------------------------------------------------------------
            # Step 1 — Native text extraction (instant, no OCR)
            # ------------------------------------------------------------------
            native_text = page.get_text("text").strip()
            if len(native_text) >= 50:
                all_text.append(f"=== PAGE {page_num + 1} ===\n{native_text}")
                print(f"[OCR]   [NATIVE] {len(native_text)} chars")
                continue

            # ------------------------------------------------------------------
            # Step 2 — PaddleOCR fallback (image-based / scanned page)
            # ------------------------------------------------------------------
            if ocr_engine is None:
                ocr_engine = _get_paddle_ocr_engine()
                if ocr_engine is None:
                    print("[OCR]   [X] PaddleOCR unavailable - skipping OCR pages")
                    continue

            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            )

            raw = list(ocr_engine.predict(img_array))

            if raw and raw[0].get("rec_texts"):
                page_res = raw[0]
                rec_texts  = page_res.get("rec_texts",  [])
                rec_scores = page_res.get("rec_scores", [])
                dt_polys   = page_res.get("dt_polys",   [])

                items = sorted(
                    zip(dt_polys, rec_texts, rec_scores),
                    key=lambda t: min(pt[1] for pt in t[0])
                )

                page_lines = [
                    text.strip()
                    for _bbox, text, confidence in items
                    if confidence >= 0.5 and text.strip()
                ]

                page_text = '\n'.join(page_lines)
                all_text.append(f"=== PAGE {page_num + 1} ===\n{page_text}")
                print(f"[OCR]   [PADDLE] {len(page_lines)} blocks, {len(page_text)} chars")
            else:
                print(f"[OCR]   [X] No text detected on page {page_num + 1}")

        doc.close()
        combined = "\n\n".join(all_text)
        print(f"\n[OCR] Total: {len(combined)} chars from {num_pages} pages")
        return combined

    except Exception as exc:
        print(f"[OCR] Error: {exc}")
        import traceback
        traceback.print_exc()
        return ""


# ============================================================================
# COLUMN-BASED BOM TABLE PARSER
# Detects the header row and extracts manufacturer+MPN directly by column
# position.  No manufacturer whitelist required — any value in a "Manufacturer"
# column is accepted as-is.
# ============================================================================

# Words (lowercase, stripped) that identify each column type in a BOM header.
_PART_HEADER_WORDS: set = {
    'part', 'part number', 'part no', 'part no.', 'part#', 'pn', 'p/n', 'p.n.',
    'item', 'item number', 'item no', 'item no.', 'item#',
    'find no', 'find number', 'bom item',
    'component no', 'component no.', 'component number',
    'material no', 'material no.', 'material number', 'material',
    'internal p/n', 'internal pn', 'internal part no',
    'drawing no', 'drawing number',
    'bom no', 'bom no.', 'bom number',
    'sch ref', 'ref no', 'ref no.',
    'comp no', 'comp no.', 'comp #',
}
_DESC_HEADER_WORDS: set = {
    'description', 'desc', 'component', 'part description',
    'component description', 'name', 'component name',
    'item description', 'specification', 'spec',
}
_MFR_HEADER_WORDS: set = {
    'manufacturer', 'mfr', 'mfgr', 'vendor', 'supplier', 'make',
    'manufacturer name', 'mfr name', 'mfr.', 'mfr.name',
    'approved vendor', 'approved mfr', 'approved manufacturer',
    'component manufacturer', 'comp mfr', 'component mfr',
    '2nd source', 'second source', 'alt mfr', 'alt vendor',
    'alternate mfr', 'alternate manufacturer', 'alternate vendor',
    'primary mfr', 'preferred mfr', 'preferred vendor',
    'source', 'sources',
}
_MPN_HEADER_WORDS: set = {
    'mpn', 'mfr part', 'mfr part number', 'mfr part no',
    'manufacturer part', 'manufacturer part number', 'manufacturer part no',
    'mfr pn', 'mfr p/n', 'mfr#', 'mfr #',
    'order code', 'catalog number', 'cat number', 'cat no',
    'part number',  # second occurrence after mfr column → MPN
    'vendor p/n', 'vendor pn', 'vendor part', 'vendor part no',
    'approved p/n', 'approved pn', 'approved part no',
    'comp mpn', 'component pn', 'component part no', 'component p/n',
    'alt mpn', 'alt p/n', 'alt pn', 'alternate mpn', 'alternate p/n',
    'mfg p/n', 'mfg pn', 'mfg part no', 'mfg no',
    'manufacturer p/n', 'manufacturer pn',
    'mfr part no.', 'manufacturer part no.',
}
_QTY_HEADER_WORDS: set = {'qty', 'quantity', 'count', 'total qty', 'total'}
_ROHS_HEADER_WORDS: set = {'rohs', 'rohs compliant', 'pb free', 'pb-free'}
_DESIG_HEADER_WORDS: set = {
    'designator', 'ref des', 'reference designator', 'reference',
    'ref', 'refdes', 'placement',
}


def _cells_from_line(line: str) -> List[str]:
    """Split a line on tabs, strip each cell."""
    return [c.strip() for c in line.split('\t')]


def _is_header_row(cells: List[str]) -> bool:
    """
    Return True if this row looks like a BOM column header.
    Requires at least two of {part-column, manufacturer-column, mpn-column}.
    Handles Tescom-style headers: 'Tescom PIN', 'Mfgr1', 'Mfgr1 PIN', 'ROHS Mfgr1', etc.
    """
    has_part = any(_classify_header_cell(c) == 'part' for c in cells if c.strip())
    has_mfr  = any(_classify_header_cell(c) == 'mfr'  for c in cells if c.strip())
    has_mpn  = any(_classify_header_cell(c) == 'mpn'  for c in cells if c.strip())
    return sum([has_part, has_mfr, has_mpn]) >= 2


def _classify_header_cell(cell: str) -> str:
    """
    Return the semantic column type of a BOM header cell.
    Handles exact-match word sets AND common Tescom-style variants:
      'Tescom PIN' / 'Tescom P/N' → 'part'
      'Mfgr1' / 'ROHS Mfgr1'     → 'mfr'
      'Mfgr1 PIN' / 'Mfgr2 P/N'  → 'mpn'
    Returns 'part', 'mfr', 'mpn', 'qty', 'desig', 'desc', or ''.
    """
    c = cell.strip().lower()
    if not c:
        return ''
    # Exact-match word sets first
    if c in _PART_HEADER_WORDS:
        return 'part'
    if c in _MPN_HEADER_WORDS:
        return 'mpn'
    if c in _MFR_HEADER_WORDS:
        return 'mfr'
    if c in _QTY_HEADER_WORDS:
        return 'qty'
    if c in _DESIG_HEADER_WORDS:
        return 'desig'
    if c in _DESC_HEADER_WORDS:
        return 'desc'
    # Pattern-based — mfgr-specific checks MUST come before generic p/n check
    # 'Mfgr1 PIN', 'Mfgr2 P/N', 'Mfr1 P/N'
    if (c.startswith('mfgr') or c.startswith('mfr')) and (
            c.endswith(' pin') or c.endswith(' p/n') or c.endswith(' pn')):
        return 'mpn'
    # Generic part-number column: 'Tescom P/N', 'Tescom PIN', 'BOM P/N' …
    if c.endswith(' p/n') or c.endswith(' pn'):
        return 'part'
    if c.endswith(' pin') and not (c.startswith('mfgr') or c.startswith('mfr')):
        # e.g. 'Tescom PIN' — 'PIN' here means P/N (part number), not a manufacturer pin
        return 'part'
    # 'Mfgr1', 'Mfgr2', 'Mfgr' bare
    if re.match(r'^mfgr\d*$', c) or re.match(r'^mfr\d*$', c):
        return 'mfr'
    # 'ROHS Mfgr1', 'Qty ROHS Mfgr1' — combined cells containing mfgr keyword
    if re.search(r'\bmfgr\d*\b', c) and not (c.endswith(' pin') or c.endswith(' p/n')):
        return 'mfr'
    # 'Approved Vendor 1', 'Approved Mfr 2', 'Alt Mfr', '2nd Source'
    if re.search(r'\b(approved|preferred|primary|alternate|alt|2nd|second)\b', c) and \
            re.search(r'\b(mfr|mfgr|vendor|manufacturer|source|supplier)\b', c):
        if re.search(r'\b(p/n|pn|part|mpn)\b', c):
            return 'mpn'
        return 'mfr'
    # 'Component Manufacturer', 'Component Mfr'
    if re.search(r'\bcomponent\b', c) and re.search(r'\b(mfr|mfgr|manufacturer)\b', c):
        return 'mfr'
    # 'Vendor P/N 1', 'Approved P/N'
    if re.search(r'\b(vendor|approved|component|comp|alt|alternate)\b', c) and \
            re.search(r'\b(p/n|pn|mpn|part no|part number)\b', c):
        return 'mpn'
    # 'Material No', 'Component No', 'Drawing No'
    if re.search(r'\b(material|component|drawing|bom|document|comp)\b', c) and \
            re.search(r'\b(no|no\.|number|#)\b', c):
        return 'part'
    return ''


def _detect_bom_columns(lines: List[str]) -> Optional[dict]:
    """
    Scan *lines* for a BOM header row and return a column-map dict:

      header_line_idx  : int
      part_col         : int | None
      desc_cols        : list[int]
      qty_cols         : list[int]
      desig_cols       : list[int]
      mfr_mpn_pairs    : list of (mfr_col: int, mpn_col: int | None)

    Returns None when no header is found (caller falls back to regex parser).
    """
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('==='):
            continue
        cells = _cells_from_line(line)
        if len(cells) < 2:
            continue
        if not _is_header_row(cells):
            continue

        # Map column types using the flexible classifier
        types = [_classify_header_cell(c) for c in cells]
        part_cols  = [i for i, t in enumerate(types) if t == 'part']
        desc_cols  = [i for i, t in enumerate(types) if t == 'desc']
        mfr_cols   = [i for i, t in enumerate(types) if t == 'mfr']
        # MPN cols: type 'mpn' but NOT already a part col
        part_col_set = set(part_cols)
        mpn_cols   = [i for i, t in enumerate(types)
                      if t == 'mpn' and i not in part_col_set]
        qty_cols   = [i for i, t in enumerate(types) if t == 'qty']
        desig_cols = [i for i, t in enumerate(types) if t == 'desig']

        # Pair each manufacturer column with the nearest MPN column that follows it
        used_mpn = set()
        mfr_mpn_pairs = []
        for mc in sorted(mfr_cols):
            candidates = [pc for pc in mpn_cols if pc > mc and pc not in used_mpn]
            if candidates:
                pc = min(candidates)
                used_mpn.add(pc)
                mfr_mpn_pairs.append((mc, pc))
            else:
                mfr_mpn_pairs.append((mc, None))

        print(f"[ColParser] Header detected at line {idx}: "
              f"part={part_cols}, mfr_mpn={mfr_mpn_pairs}, qty={qty_cols}")
        return {
            'header_line_idx': idx,
            'part_col':  part_cols[0] if part_cols else None,
            'desc_cols': desc_cols,
            'qty_cols':  qty_cols,
            'desig_cols': desig_cols,
            'mfr_mpn_pairs': mfr_mpn_pairs,
        }

    return None  # No header found


# Valid part-number formats (shared by both parsers)
_PART_NUMBER_RE = re.compile(
    r'(?i)(?:ERAA|ERSA|FRAA|FRSA|ERAAZ|eRaa)\w{3,}'
    r'|\b4[12][G6]\d{4,}-\s*[A-Z0-9\-]+\b'
    r'|\b\d{6,}(?:-[A-Z0-9]{2,})?\b'
    r'|\b[A-Z]{2,}\d+[A-Z0-9\-]{3,}\b'
    r'|\b[A-Z]\d{2,}[A-Z][A-Z0-9]{3,}\b'  # single-letter prefix: B72214S0351K101, C907U102...
    r'|\b[A-Z0-9]{2,}-[A-Z0-9]{3,}\b'
)

_MFR_LIKE_PATTERNS = [
    'ELECTRO-MECHANICS', 'FLECTRO-MECHANICS', 'LECTRO-MECHANICS', 'CTRO-MECHANICS',
]

_MPN_FRAGMENT_RE = [
    # Only reject clearly incomplete OCR fragments — NOT full manufacturer part numbers.
    # Removed: r'^[A-Z]{2,5}\d{5,}[A-Z]{2,}$'   — matched valid MPNs like TLV74333PDBVR
    # Removed: r'^[A-Z]{2}\d{4}[A-Z]{2,}[CZ]?-RL\d$' — matched valid ADI tape-reel MPNs
    r'^9BCBZ',                              # OCR fragment: partial AD7689BCBZ suffix
    r'^BGA-[\d\.]+X[\d\.]+-\d+-.+$',       # PCB footprint identifier (not an MPN)
    r'^[A-Z]{2,4}\d{2,3}-\d{1,2}-[A-Z]-[A-Z]{2,}$',  # Internal allocation codes
]

_SKIP_PART_WORDS = {'EMERSON', 'TESCOM', 'ROHS', 'UNLESS', 'MATERIAL', 'SURFACE', 'CORNER'}

_OCR_KNOWN_CORRECTIONS = {'1106105': 'JT106105'}


def _correct_part_number(raw: str) -> Optional[str]:
    """
    Apply all OCR corrections to a raw candidate part-number string.
    Returns the corrected string, or None if it should be discarded.
    """
    pn = raw.upper().strip()

    # Normalize spaces around hyphens — OCR sometimes inserts a space after
    # the hyphen in part numbers (e.g. "42G5000- 0193" → "42G5000-0193").
    pn = re.sub(r'\s*-\s*', '-', pn)

    # --- manufacturer-like strings ---
    if any(pat in pn for pat in _MFR_LIKE_PATTERNS):
        return None

    # --- ERAA/ERSA prefix corrections ---
    if pn.startswith('FRAA'):
        pn = 'ERAA' + pn[4:]
    elif pn.startswith('FRSA'):
        pn = 'ERSA' + pn[4:]
    if pn.startswith('ERAAZO'):
        pn = 'ERAA26' + pn[6:]
    elif pn.startswith('ERAAZ'):
        pn = 'ERAA2' + pn[5:]
    prefix = ''
    for pfx in ('ERAA', 'ERSA'):
        if pn.startswith(pfx):
            prefix = pfx
            break
    if prefix:
        suffix = pn[len(prefix):]
        suffix = suffix.translate(str.maketrans('OISZlorgeBGRE', '0152101962916'))
        suffix = re.sub(r'[^0-9]', '', suffix)
        pn = prefix + suffix

    # --- 42G / 41G corrections ---
    m = re.match(r'^(4[12])G6(\d{4})(-.*)?$', pn)
    if m:
        pn = f"{m.group(1)}G{m.group(2)}{m.group(3) or ''}"
    m = re.match(r'^(4[12])6(\d{4,})(-.*)?$', pn)
    if m:
        pn = f"{m.group(1)}G{m.group(2)}{m.group(3) or ''}"
    m = re.match(r'^(4[12])(\d)(\d{4,})(-[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)?$', pn)
    if m and m.group(2) == '6':
        pn = f"{m.group(1)}G{m.group(3)}{m.group(4) or ''}"

    # --- suffix O→0 ---
    if '-FBO' in pn:
        pn = pn.replace('-FBO', '-FB0')
    if pn.endswith('-BO'):
        pn = pn[:-3] + '-B0'

    # --- MPN fragment check ---
    if any(re.match(pat, pn, re.IGNORECASE) for pat in _MPN_FRAGMENT_RE):
        return None

    # --- Known corrections ---
    if pn in _OCR_KNOWN_CORRECTIONS:
        pn = _OCR_KNOWN_CORRECTIONS[pn]

    # --- Skip-words ---
    if pn in _SKIP_PART_WORDS:
        return None

    # --- Basic validation ---
    if len(pn) < 4 or not re.search(r'\d', pn):
        return None
    if re.match(r'^\d+$', pn):
        if not (pn.startswith('41') or pn.startswith('42')) and len(pn) < 8:
            return None

    # --- Format validation ---
    valid_formats = [
        r'^(ERAA|ERSA)\d{5}$',
        r'^4[12]G\d{4}-[A-Z0-9\-]+$',
        r'^4[12]G\d{4}$',
        r'^[A-Z]{1,6}\d{3,}[A-Z0-9\-]*$',      # LM358, SN74HC00, TLV74333PDBVR, AD7689BCPZ-RL7
        r'^[A-Z0-9]{3,}-[A-Z0-9\-]{2,}$',        # ABC-DEF, 123-456
        r'^[A-Z0-9]{5,}$',                         # Generic 5+ alphanumeric
        r'^[A-Z0-9]{2,}[A-Z0-9\-\.]{2,}[A-Z0-9]+$',  # Catch-all: mixed letters/digits/hyphens
    ]
    if not any(re.match(fmt, pn) for fmt in valid_formats):
        return None

    return pn


def _clean_mfr_name(raw: str) -> str:
    """Title-case a manufacturer name and fix doubled-first-letter OCR artifact."""
    name = raw.strip()
    if not name:
        return name
    # Fix doubled first letter: VVISHAY → VISHAY, HHENKEL → HENKEL
    if len(name) >= 2 and name[0].upper() == name[1].upper() and name[0].isalpha():
        name = name[1:]
    # Apply known OCR corrections
    name_lower = name.lower()
    for ocr_err, correction in OCR_MANUFACTURER_CORRECTIONS.items():
        pattern = r'\b' + re.escape(ocr_err) + r'\b'
        if re.search(pattern, name_lower):
            name = re.sub(pattern, correction, name, flags=re.IGNORECASE)
            break
    return name.title()


def _detect_floating_mfr_block(cells: List[str]) -> List[tuple]:
    """
    Detect a 'floating' manufacturer row: a line whose cells appear to contain
    paired (manufacturer, MPN, manufacturer, MPN, ...) data but whose indices
    do NOT align with the detected header columns.  This happens when PaddleOCR
    splits a wide BOM table row into two OCR rows — the right-side manufacturer
    columns end up at a slightly different Y position from the left-side part-
    number columns and are grouped into a separate line by the row-threshold.

    Returns a list of (mfr_str, mpn_str) tuples, or [] if not a floating block.
    """
    n = len(cells)
    if n < 2 or n > 8 or n % 2 != 0:
        return []

    pairs = []
    for i in range(0, n, 2):
        mfr = cells[i].strip()
        mpn = cells[i + 1].strip() if i + 1 < n else ''
        if not mfr or not mpn:
            return []  # strict: every pair must be filled

        # Reject common non-manufacturer words (RoHS flags, doc keywords …)
        _FLOAT_NON_MFR = {
            'yes', 'no', 'y', 'n', 'x', 'tbd', 'n/a', '-',
            'changes', 'tolerance', 'surface', 'finish', 'emerson',
            'tescom', 'unless', 'otherwise', 'specified', 'rohs',
            'material', 'corner', 'break', 'sharp', 'corners',
        }
        if mfr.lower() in _FLOAT_NON_MFR:
            return []

        # mfr must NOT itself be a valid part number
        if _correct_part_number(mfr.split()[0]):
            return []

        # mfr must not be a bare integer or short designator (R7, C14, L2 …)
        if re.match(r'^\d+$', mfr) or re.match(r'^[A-Z]{1,2}\d{1,3}$', mfr):
            return []

        # mfr must not be description text: no commas allowed, max 4 words
        if ',' in mfr or mfr.count(' ') > 3:
            return []

        # mpn must look like a real part number: ≥3 chars and at least one digit
        if len(mpn) < 3 or not re.search(r'\d', mpn):
            return []

        # mpn must not be a designator list (e.g. 'C2, C4, C5,')
        if ',' in mpn:
            return []

        # mpn should not look like a sentence (description text with many words)
        if mpn.count(' ') > 5:
            return []

        pairs.append((mfr, mpn))

    return pairs


def _extract_parts_by_columns(lines: List[str], col_map: dict) -> List[Dict]:
    """
    Extract BOM parts using header-derived column positions.
    Manufacturer and MPN values are read directly from their columns —
    no whitelist check.  Any non-empty string in a Manufacturer column is
    accepted as the manufacturer name.
    """
    parts = []
    last_part = None   # track previous part for continuation rows
    # pending_mfr_rows: floating mfr blocks (cells in the wrong column
    # positions because the PDF row was split by Y-threshold) waiting to be
    # attached to the next valid part number as pre-continuation data.
    pending_mfr_rows: List[List[Dict]] = []

    hdr = col_map['header_line_idx']
    part_col      = col_map['part_col']
    desc_cols     = col_map['desc_cols']
    qty_cols      = col_map['qty_cols']
    desig_cols    = col_map['desig_cols']
    mfr_mpn_pairs = col_map['mfr_mpn_pairs']

    def _flush_pending_to(target_part: Dict) -> None:
        """Attach all pending floating mfr blocks to target_part."""
        for pending_list in pending_mfr_rows:
            for m in pending_list:
                m['preference'] = len(target_part['manufacturers']) + 1
                target_part['manufacturers'].append(m)
        pending_mfr_rows.clear()

    for line in lines[hdr + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith('==='):
            continue

        cells = _cells_from_line(line)

        # --- Manufacturer values from this row (column-position read) ---
        row_manufacturers = []
        for mfr_col, mpn_col in mfr_mpn_pairs:
            mfr_val = cells[mfr_col].strip() if mfr_col < len(cells) else ''
            mpn_val = cells[mpn_col].strip() if mpn_col is not None and mpn_col < len(cells) else ''
            if not mfr_val or mfr_val.lower() in {'n/a', '-', 'tbd', 'none', '', 'yes', 'no', 'y', 'n', 'x'}:
                continue
            # Reject cells that look like MPNs (not manufacturer names):
            #   • starts with a digit AND isn't a short brand like "3M"
            #   • matches a known part-number pattern (alphanumeric MPN)
            #   • long (≥8), no spaces, mixes letters and digits (e.g. B72214S0351K101)
            _is_short_brand = (re.match(r'^\d', mfr_val)
                               and len(mfr_val) <= 4
                               and re.search(r'[A-Za-z]', mfr_val))
            if not _is_short_brand and (re.match(r'^\d', mfr_val) or _PART_NUMBER_RE.search(mfr_val)):
                continue
            if (' ' not in mfr_val and len(mfr_val) >= 8
                    and re.search(r'[A-Za-z]', mfr_val)
                    and re.search(r'\d', mfr_val)):
                continue
            mfr_clean = _clean_mfr_name(mfr_val)
            mpn_clean = clean_ocr_mpn(mpn_val) if mpn_val else 'N/A'
            row_manufacturers.append({
                'manufacturer': mfr_clean,
                'mpn': mpn_clean,
                'preference': 0,
                'confidence': 0.92,
            })

        # --- Part number ---
        pn_raw = cells[part_col].strip() if part_col is not None and part_col < len(cells) else ''

        # ── Case A: post-continuation via proper column positions ──────────
        # Empty part-number cell AND manufacturer data in the correct columns.
        if not pn_raw and row_manufacturers and last_part is not None:
            for m in row_manufacturers:
                m['preference'] = len(last_part['manufacturers']) + 1
                last_part['manufacturers'].append(m)
            mfr_names = ', '.join(m['manufacturer'] for m in row_manufacturers)
            print(f"[ColParser]   [CONT] appended {len(row_manufacturers)} mfr(s) to "
                  f"{last_part['part_number']} — {mfr_names}")
            continue

        # ── Case B: floating manufacturer block ────────────────────────────
        # PaddleOCR splits the wide BOM row into two OCR lines at different
        # Y positions.  The right-side manufacturer/MPN columns land in a
        # separate line where cells[0..3] hold the mfr/mpn pairs — their
        # column indices (4..7) are out of bounds, so row_manufacturers=[].
        # We detect these lines and queue them as pending.
        # IMPORTANT: only trigger when pn_raw is also empty — if the row has
        # a non-empty part-number cell the row belongs to the part-extraction
        # path, not the floating-block path.
        if not pn_raw and not row_manufacturers:
            floating = _detect_floating_mfr_block(cells)
            if floating:
                floating_dicts = [
                    {
                        'manufacturer': _clean_mfr_name(mfr),
                        'mpn': clean_ocr_mpn(mpn),
                        'preference': 0,
                        'confidence': 0.85,
                    }
                    for mfr, mpn in floating
                ]
                if pending_mfr_rows:
                    # There is already a pending block.  That means the
                    # current floating block is NOT immediately before a part
                    # row — so the pending block is post-continuation of the
                    # previous part.
                    if last_part is not None:
                        mfr_names = ', '.join(
                            m['manufacturer'] for p in pending_mfr_rows for m in p
                        )
                        _flush_pending_to(last_part)
                        print(f"[ColParser]   [FLOAT-POST] flushed pending to "
                              f"{last_part['part_number']} — {mfr_names}")
                    else:
                        pending_mfr_rows.clear()
                pending_mfr_rows.append(floating_dicts)
                continue

        # ── Case C: attempt to read a part-number from the cell ───────────
        if not pn_raw:
            if not row_manufacturers:
                continue
            # No dedicated part column; scan all cells for a part-number token
            for c in cells:
                if _PART_NUMBER_RE.search(c):
                    pn_raw = c
                    break
        if not pn_raw:
            continue

        # Extract the first matching token
        m_re = _PART_NUMBER_RE.search(pn_raw)
        pn_token = m_re.group() if m_re else pn_raw

        part_number = _correct_part_number(pn_token)
        if not part_number:
            # Invalid part-number string.  If pending floating mfrs exist they
            # belong to the previous part as post-continuation data.
            if pending_mfr_rows and last_part is not None:
                mfr_names = ', '.join(
                    m['manufacturer'] for p in pending_mfr_rows for m in p
                )
                _flush_pending_to(last_part)
                print(f"[ColParser]   [FLOAT-POST] flushed pending to "
                      f"{last_part['part_number']} — {mfr_names}")
            continue

        # ── Valid part found ───────────────────────────────────────────────
        # Prepend any pending floating mfr blocks as pre-continuation data
        # (they appeared above this part row in the OCR output).
        all_mfrs: List[Dict] = []
        for pending_list in pending_mfr_rows:
            all_mfrs.extend(pending_list)
        pending_mfr_rows.clear()

        # Add manufacturers from current row (proper column positions)
        all_mfrs.extend(row_manufacturers)

        # ── Inline-row fallback ────────────────────────────────────────────
        # When the row has fewer cells than the header expects (e.g. the
        # description wrapped to a preceding line, shifting all column
        # positions left by 1), the mfr columns are out of bounds or contain
        # MPN values that were rejected above.  Scan the rightmost cells for
        # paired (mfr, mpn) blocks as a fallback.
        if not all_mfrs and mfr_mpn_pairs:
            n_pairs = len(mfr_mpn_pairs)
            # Exclude the part-number cell itself, then take the last 2*n cells
            other_cells = [cells[i] for i in range(len(cells)) if i != part_col]
            for width in (n_pairs * 2, n_pairs * 2 - 2, 2):
                if width < 2 or width > len(other_cells):
                    continue
                candidate = other_cells[-width:]
                floating = _detect_floating_mfr_block(candidate)
                if floating:
                    all_mfrs = [
                        {
                            'manufacturer': _clean_mfr_name(mfr),
                            'mpn': clean_ocr_mpn(mpn),
                            'preference': i + 1,
                            'confidence': 0.80,
                        }
                        for i, (mfr, mpn) in enumerate(floating)
                    ]
                    print(f"[ColParser]   [INROW] {part_number}: "
                          f"found {len(all_mfrs)} mfr(s) via inline scan")
                    break

        # Renumber preferences
        for idx, mfr in enumerate(all_mfrs):
            mfr['preference'] = idx + 1

        # --- Description ---
        description = ' '.join(
            cells[i] for i in desc_cols if i < len(cells) and cells[i]
        ).strip()

        # --- Quantity ---
        quantity = next(
            (cells[i] for i in qty_cols if i < len(cells) and cells[i]), ''
        )

        # --- Designators ---
        designators = next(
            (cells[i] for i in desig_cols if i < len(cells) and cells[i]), ''
        )

        part = {
            'part_number': part_number,
            'description': description,
            'manufacturers': all_mfrs,
            'quantity': quantity,
            'designators': designators,
            'confidence': 0.92 if all_mfrs else 0.55,
            'page_number': 1,
            'source': 'paddle_ocr_columnar',
        }
        parts.append(part)
        last_part = part

        if all_mfrs:
            mfr_names = ', '.join(m['manufacturer'] for m in all_mfrs)
            print(f"[ColParser]   [OK] {part_number}: {len(all_mfrs)} mfr(s) — {mfr_names}")
        else:
            print(f"[ColParser]   [~] {part_number}: no manufacturers found in mfr columns")

    # Flush any trailing pending mfr blocks to the last part
    if pending_mfr_rows and last_part is not None:
        mfr_names = ', '.join(
            m['manufacturer'] for p in pending_mfr_rows for m in p
        )
        _flush_pending_to(last_part)
        print(f"[ColParser]   [FLOAT-POST] flushed trailing pending to "
              f"{last_part['part_number']} — {mfr_names}")

    return parts


def _parse_emerson_drawing_format(lines: List[str]) -> List[Dict]:
    """
    Parse engineering drawing BOMs where MPN/manufacturer appear on continuation
    lines separated from the BOM anchor line by description text.

    Example structure:
        42G4000- 0671   IC-ANALOG-AD/DA   U1   BGA-2.39X2.39-20-A-BLR   CAD-CELL
        Ksps 20-Pin WLCSP                          ← description continuation
        AD7689BCBZ -RL7   ANALOG DEVICES           ← actual MPN + manufacturer

    Strategy: detect 42G/41G anchor lines; greedily group ALL lines between
    consecutive anchors; extract MPNs and manufacturers from each group.

    Returns [] if fewer than 2 Emerson-style anchors are found (not this format).
    """
    # Tolerant anchor pattern — handles OCR spaces within the number
    # Matches: 42G4000-0671, 42G201 1 - 0030, 41G3189-BA10-000-B0, etc.
    ANCHOR_RE = re.compile(
        r'(?<!\w)4[12][G6]\s*\d[\d\s]{2,6}\s*[-]\s*\d[\d\s]{1,5}',
        re.IGNORECASE
    )

    # Footprint / CAD-cell tokens to strip from candidate MPNs
    FOOTPRINT_RE = re.compile(
        r'^(?:BGA-[\d.]+X[\d.]+-\d+[-A-Z]*'
        r'|SOT[-\s]?\d{2,3}(?:-\d+-[A-Z]-[A-Z]+)?'
        r'|CAD-CELL|CAD\s*CELL'
        r'|[A-Z]{2,4}\d{2,3}-\d+-[A-Z]-[A-Z]{2,}'  # SOT23-5-F-HSV
        r'|WLCSP|QFN|LQFP|TSSOP|SSOP|SOP\d*|DIP\d*|TO-\d+'
        r')$',
        re.IGNORECASE
    )

    # Industry MPN pattern — at least 3 letters/digits, contains both letters and digits
    INDUSTRY_MPN_RE = re.compile(
        r'^[A-Z]{1,6}\d[\dA-Z\-]{3,}$|^[A-Z0-9]{2,}-[A-Z0-9]{2,}(?:-[A-Z0-9]+)*$',
        re.IGNORECASE
    )

    # Words that are definitely NOT MPNs
    NOT_MPN_WORDS = {
        'CAD-CELL', 'CAD', 'CELL', 'CAP', 'CER', 'RES', 'IC', 'DIODE', 'LED',
        'SMD', 'SMT', 'ROHS', 'WLCSP', 'UNLESS', 'OTHERWISE', 'SPECIFIED',
        'MATERIAL', 'FINISH', 'ANGLES', 'UNITS', 'INCHES', 'THREADS', 'CLASS',
        'REMOVE', 'BURRS', 'SCALE', 'SHEET', 'DRAWING', 'PROJECT', 'SURFACE',
        'ROUGHNESS', 'CORNER', 'FILLET', 'RADII', 'ASME', 'ECRN', 'CHANGES',
    }

    # Find anchor indices
    anchor_indices = []
    for i, line in enumerate(lines):
        if ANCHOR_RE.search(line):
            anchor_indices.append(i)

    if len(anchor_indices) < 2:
        return []

    print(f"[DrawingParser] Found {len(anchor_indices)} Emerson anchors — using drawing format parser")

    parts = []

    for anchor_pos, anchor_idx in enumerate(anchor_indices):
        # Primary group: anchor line + continuation lines up to (not including) next anchor
        group_end = anchor_indices[anchor_pos + 1] if anchor_pos + 1 < len(anchor_indices) else len(lines)
        group_lines = lines[anchor_idx:group_end]

        # Grab lines immediately before the anchor that look like orphan manufacturer
        # name fragments (e.g. "SAMSUNG" appearing before "42G2011-0030").
        # We only include a pre-line if it is a PURE manufacturer fragment (no digits,
        # all-caps word matching known manufacturer list) — this prevents bleeding
        # MPN/manufacturer data from the previous anchor's continuation lines.
        extra_pre = []
        prev_anchor_boundary = (anchor_indices[anchor_pos - 1] + 1) if anchor_pos > 0 else 0
        for k in range(1, 3):
            pre_idx = anchor_idx - k
            if pre_idx < prev_anchor_boundary:
                break
            if pre_idx < 0:
                break
            if pre_idx in anchor_indices:
                break
            pre_line = lines[pre_idx].strip()
            pre_lower = pre_line.lower()
            # Only include if the line is purely a manufacturer name fragment
            # (no digits, and matches a known manufacturer key)
            if re.search(r'\d', pre_line):
                break  # has digits → likely MPN or description, stop
            if any(pre_lower == mfr or pre_lower in mfr or mfr in pre_lower
                   for mfr in KNOWN_MANUFACTURERS_FOR_OCR if len(mfr) >= 4):
                extra_pre.insert(0, pre_line)
            else:
                break

        group_text = ' '.join(extra_pre + group_lines)

        # ── Extract Emerson BOM part number from anchor line ──────────────
        anchor_line = lines[anchor_idx]
        anchor_match = ANCHOR_RE.search(anchor_line)
        raw_pn = re.sub(r'\s+', '', anchor_match.group()) if anchor_match else ''
        # Normalize hyphens (remove spaces around hyphen)
        raw_pn = re.sub(r'\s*-\s*', '-', raw_pn)
        part_number = _correct_part_number(raw_pn)
        if not part_number:
            # Try correcting common OCR: 42G201 1 - 0030 → 42G2011-0030
            raw_pn2 = re.sub(r'\s', '', raw_pn)
            part_number = _correct_part_number(raw_pn2)
        if not part_number:
            continue

        # ── Extract MPN from continuation lines ───────────────────────────
        # In this format the MPN appears alone (or with a manufacturer name)
        # on a dedicated continuation line, NOT mixed into the BOM anchor line.
        # Strategy:
        #   1. Check the anchor line's last tab-column — for the case where
        #      MPN is the last column (e.g., "CL O5B1 04K05NNNC").
        #   2. Then scan continuation lines; skip description lines; the first
        #      line whose first token passes MPN validation is the MPN line.
        primary_mpn = ""
        continuation_lines = lines[anchor_idx + 1:group_end]

        # Regex patterns that indicate a description line (NOT an MPN line)
        DESCRIPTION_LINE_RE = re.compile(
            r'\d+\s*-?\s*Pin\b'      # "20-Pin", "5-Pin"
            r'|\d+\s*V\b'            # "3.3V", "16V"
            r'|\d+\s*A\b'            # "0.3A"
            r'|\b(?:SAR|ADC|DAC|LDO|Regulator|Serial|Ksps|kSps|MHz|kHz|uF|nF|pF|Ohm|mA)\b',
            re.IGNORECASE
        )

        # Step 1: MPN embedded in anchor line as last tab-column
        anchor_cols = anchor_line.split('\t')
        if len(anchor_cols) >= 2:
            last_col = anchor_cols[-1].strip()
            if last_col and not FOOTPRINT_RE.match(last_col):
                # Extract only the MPN portion — stop at first known-mfr word or all-caps fragment
                last_col_tokens = last_col.split()
                mpn_tokens = []
                for tok in last_col_tokens:
                    if tok.lower() in KNOWN_MANUFACTURERS_FOR_OCR:
                        break
                    if mpn_tokens and re.match(r'^[A-Z]{3,}$', tok):
                        break
                    if mpn_tokens and re.match(r'^\d+$', tok):
                        break
                    mpn_tokens.append(tok)
                # Try collapsed (removes only spaces, preserves hyphens)
                collapsed = re.sub(r'\s+', '', ''.join(mpn_tokens))
                for candidate in (collapsed, ' '.join(mpn_tokens)):
                    if not candidate:
                        continue
                    if not re.search(r'\d', candidate) or not re.search(r'[A-Z]', candidate, re.IGNORECASE):
                        continue
                    corrected = _correct_part_number(candidate)
                    if corrected and not re.match(r'^4[12]G\d', corrected):
                        primary_mpn = corrected
                        break

        # Step 2: Scan continuation lines for MPN line
        if not primary_mpn:
            for cont_line in continuation_lines:
                cont_stripped = cont_line.strip()
                if not cont_stripped:
                    continue
                # Skip pure-letter description lines (no digits at all)
                if not re.search(r'\d', cont_stripped):
                    continue
                # Skip lines that look like component descriptions ("Ksps 20-Pin WLCSP")
                if DESCRIPTION_LINE_RE.search(cont_stripped):
                    continue
                # Skip footprint-only lines ("SOT-23", "BGA-...")
                cont_stripped_clean = cont_stripped.split('\t')[0].strip()
                if FOOTPRINT_RE.match(cont_stripped_clean):
                    continue
                # Skip lines that are purely manufacturer name fragments
                cont_lower = cont_stripped.lower()
                if any(cont_lower.strip() == mfr for mfr in KNOWN_MANUFACTURERS_FOR_OCR if len(mfr) > 4):
                    continue
                # Extract only the MPN portion — tokens before first known-mfr word
                # Also stop at pure-uppercase words that are likely manufacturer names
                # (e.g., TEXAS, ANALOG, SAMSUNG, ONT, INC) or digit-only separator tokens
                parts_of_line = cont_stripped.replace('\t', ' ').split()
                mpn_tokens = []
                for tok in parts_of_line:
                    if tok.lower() in KNOWN_MANUFACTURERS_FOR_OCR:
                        break
                    # Stop at all-caps words that look like manufacturer fragments
                    # (all uppercase, 3+ chars, no digits) after we have at least one MPN token
                    if mpn_tokens and re.match(r'^[A-Z]{3,}$', tok):
                        break
                    # Stop at digit-only tokens after first MPN token (e.g. "1" in "GMCO4X7R105K 1 ONT")
                    if mpn_tokens and re.match(r'^\d+$', tok):
                        break
                    mpn_tokens.append(tok)
                # First try space-collapsed only (preserves hyphens), then first token
                candidates = []
                if mpn_tokens:
                    collapsed = re.sub(r'\s+', '', ''.join(mpn_tokens))  # remove only spaces
                    candidates.append(collapsed)
                    candidates.append(mpn_tokens[0].replace(' ', ''))
                for cand in candidates:
                    if not cand or not re.search(r'\d', cand) or not re.search(r'[A-Z]', cand, re.IGNORECASE):
                        continue
                    if FOOTPRINT_RE.match(cand):
                        continue
                    corrected = _correct_part_number(cand)
                    if corrected and not re.match(r'^4[12]G\d', corrected):
                        primary_mpn = corrected
                        break
                if primary_mpn:
                    break

        # ── Extract manufacturers from group text ─────────────────────────
        sorted_mfrs = sorted(KNOWN_MANUFACTURERS_FOR_OCR, key=len, reverse=True)
        # Use anchor line + continuation lines + limited pre-lines for mfr search.
        # Pre-lines are limited to catch "SAMSUNG" fragment appearing before anchor
        # but we exclude ALL lines from the previous anchor's continuation to avoid
        # bleeding manufacturer names across anchors.
        # Build two text blobs:
        # 1. extra_pre + continuation_lines (skipping anchor line) so that
        #    "SAMSUNG" (pre-line) and "ELECTRO-MECHANICS" (continuation) appear
        #    adjacent for combined-name matching.
        # 2. The anchor line itself (for any manufacturers listed there).
        mfr_search_lines = extra_pre + continuation_lines
        mfr_search_text = ' '.join(mfr_search_lines)
        # Normalize tabs to spaces before further processing
        mfr_search_text = mfr_search_text.replace('\t', ' ')
        # Combine split manufacturer names across lines (e.g. SAMSUNG\nELECTRO-MECHANICS)
        mfr_search_text = combine_split_manufacturer_names(mfr_search_text)
        mfr_lower = mfr_search_text.lower()
        # Note: do NOT apply OCR_MANUFACTURER_CORRECTIONS here — those substring
        # replacements corrupt correctly-spelled words (e.g. "nstruments" inside
        # "instruments" → "iinstruments"). The drawing format has clean manufacturer
        # names so no correction is needed.
        group_lower = mfr_lower  # keep alias for consistency

        found_mfrs = []
        for mfr_key in sorted_mfrs:
            if mfr_key in group_lower:
                display = mfr_key.title()
                if display not in found_mfrs:
                    found_mfrs.append(display)

        # Remove shorter manufacturer matches that are substrings of longer ones
        # e.g. remove "Electro-Mechanics" if "Samsung Electro-Mechanics" is also present
        dedupe_mfrs = []
        for m in found_mfrs:
            if not any(m.lower() in other.lower() and m != other for other in found_mfrs):
                dedupe_mfrs.append(m)
        found_mfrs = dedupe_mfrs

        # Clean up found manufacturers: remove generic partial matches if
        # a more specific match covers them
        primary_mfr = ""
        alt_mfr = ""
        if found_mfrs:
            # Prefer the most specific (longest) match
            found_mfrs_sorted = sorted(found_mfrs, key=len, reverse=True)
            primary_mfr = found_mfrs_sorted[0].title()
            if len(found_mfrs_sorted) > 1:
                alt_mfr = found_mfrs_sorted[1].title()

        part = {
            'part_number': part_number,
            'description': '',
            'manufacturer': primary_mfr,
            'mpn': primary_mpn,
            'alternate_mpn': '',
            'alternate_manufacturer': alt_mfr,
        }
        parts.append(part)
        print(f"[DrawingParser]   {part_number} → MPN: {primary_mpn!r}, Mfr: {primary_mfr!r}")

    return parts


def parse_tesseract_bom(ocr_text: str) -> List[Dict]:
    """
    Parse BOM data from OCR text (PaddleOCR natural line format).

    PaddleOCR outputs detected text as individual lines sorted spatially.
    This parser uses regex to extract BOM part numbers, manufacturers, and
    MPNs from the resulting unstructured text.

    Args:
        ocr_text: Raw text from PaddleOCR (one detection per line)

    Returns:
        List of parts extracted from OCR text
    """
    parts = []
    
    if not ocr_text:
        print("[Tesseract Parser] No text to parse")
        return parts
    
    print(f"[Tesseract Parser] Parsing {len(ocr_text)} characters")
    
    # Pre-process: combine split manufacturer names across lines
    ocr_text = combine_split_manufacturer_names(ocr_text)
    
    # Split into lines
    lines = ocr_text.split('\n')

    # ── PASS 1: Column-based extraction (header-driven) ───────────────────────
    # When the OCR text contains a header row, extract manufacturer and MPN
    # values directly from their column positions — no whitelist required.
    col_map = _detect_bom_columns(lines)
    if col_map and col_map['mfr_mpn_pairs']:
        col_parts = _extract_parts_by_columns(lines, col_map)
        if col_parts:
            print(f"[Tesseract Parser] Column-based extraction: {len(col_parts)} parts")
            return col_parts
        print("[Tesseract Parser] Column-based extraction yielded 0 parts — falling back to regex")
    else:
        print("[Tesseract Parser] No header row detected — using regex extraction")

    # ── PASS 1b: Emerson Engineering Drawing format ───────────────────────────
    # Handles documents where MPN/manufacturer appear on continuation lines
    # separated from the BOM anchor line by description text.
    emerson_parts = _parse_emerson_drawing_format(lines)
    if emerson_parts:
        print(f"[Tesseract Parser] Emerson drawing parser: {len(emerson_parts)} parts")
        return emerson_parts

    # ── PASS 2: Regex-based extraction (original approach) ───────────────────
    # - ERAA/ERSA: Internal part numbers (e.g., ERAA12345)
    # - 42G format: e.g., 42G4000-0671, 41G3189-BA10-000-B0 (often OCR'd as 426xxxx)
    # - Numeric: Pure digit part numbers (e.g., 4264000-067, 7488875)
    # - Alphanumeric: Standard MPNs (e.g., AD768QBCBZ-RL7, LV74333PDBVR)
    # - Mixed: Various manufacturer formats with letters, digits, hyphens
    part_pattern = re.compile(
        r'(?i)(?:ERAA|ERSA|FRAA|FRSA|ERAAZ|eRaa)\w{3,}'  # Internal part numbers
        r'|\b4[12][G6]\d{4,}-\s*[A-Z0-9\-]+\b'  # 41G/42G format (space after hyphen allowed)
        r'|\b\d{6,}(?:-[A-Z0-9]{2,})?\b'  # Numeric with optional suffix (4264000-067)
        r'|\b[A-Z]{2,}\d+[A-Z0-9\-]{3,}\b'  # Alphanumeric MPN (AD768QBCBZ-RL7)
        r'|\b[A-Z0-9]{2,}-[A-Z0-9]{3,}\b'  # Hyphenated format (SOT23-5-F-HSV)
    )
    
    # Patterns that look like part numbers but are actually manufacturer names or MPN fragments
    manufacturer_like_patterns = [
        'ELECTRO-MECHANICS', 'FLECTRO-MECHANICS', 'LECTRO-MECHANICS', 'CTRO-MECHANICS',
    ]
    
    # MPN fragment patterns - should NOT be treated as part numbers
    # These are fragments of actual MPNs that might be OCR'd on their own line
    mpn_fragment_patterns = [
        r'^\d*BCBZ-RL\d*$',  # Fragment of AD7689BCBZ-RL7
        r'^[A-Z]*PDBVR?$',  # TI suffix fragment
        r'^K\d+N+C$',  # Samsung suffix
        r'^\d+FKED$',  # Vishay suffix
        r'^[A-Z]{1,2}\d{2,3}-\d+-[A-Z]-[A-Z]+$',  # Footprint patterns like SOT23-5-F-HSV
        r'^BGA-[\d\.]+X[\d\.]+-.+$',  # BGA footprint: BGA-2.39X2.39-20-A-BLR
        r'^[A-Z]{2,4}\d{2,3}-\d{1,2}-[A-Z]-[A-Z]{2,}$',  # SOT23-5-F-HSV style footprints
        r'^[A-Z]{2,5}\d{5,}[A-Z]{2,}$',  # Long TI/Analog MPNs like TLV74333PDBVR
        r'^[A-Z]{2}\d{4}[A-Z]{2,}[CZ]?-RL\d$',  # AD7689BCBZ-RL7 style
    ]
    
    def is_real_part_number(line_text):
        """Check if line has a real part number (not a manufacturer-like pattern or MPN-only continuation)"""
        match = part_pattern.search(line_text)
        if not match:
            return False
        matched_text = match.group().upper()
        # Skip if matched text is a manufacturer-like pattern
        for mfr_pattern in manufacturer_like_patterns:
            if mfr_pattern in matched_text:
                return False
        # Skip if the ENTIRE token looks like an MPN/footprint fragment (continuation row)
        stripped = line_text.strip()
        # If the line is ONLY an alphanumeric token (possibly + mfr name after it),
        # check whether that token matches a known MPN-only pattern
        first_token = stripped.split()[0] if stripped.split() else ''
        for frag_pat in mpn_fragment_patterns:
            if re.match(frag_pat, first_token, re.IGNORECASE):
                return False
        return True
    
    # Helper function to check if a line has manufacturer data
    def has_manufacturer_data(line_text):
        if not line_text:
            return False
        line_lower = line_text.lower()
        has_uppercase = bool(re.search(r'[A-Z][a-z]+\s+[A-Z0-9]|[A-Z]{3,}', line_text))
        has_known_mfr = any(mfr in line_lower for mfr in KNOWN_MANUFACTURERS_FOR_OCR)
        has_ocr_mfr = any(ocr_mfr in line_lower for ocr_mfr in OCR_MANUFACTURER_CORRECTIONS)
        has_mpn_pattern = bool(re.search(r'[A-Z][A-Z0-9\-]{4,}', line_text))
        return has_uppercase or has_known_mfr or has_ocr_mfr or has_mpn_pattern
    
    # Build part lines, merging continuation lines (both before and after)
    # that contain manufacturer/MPN data
    part_lines = []
    used_lines = set()  # Track which lines have been merged
    
    for i, line in enumerate(lines):
        if i in used_lines:
            continue
        if is_real_part_number(line):
            merged_line = line
            
            # Look BACKWARD for manufacturer data on previous lines
            # (handles cases where manufacturer appears before part number)
            prev_idx = i - 1
            backward_lines = []
            while prev_idx >= 0 and prev_idx not in used_lines:
                prev_line = lines[prev_idx].strip()
                if not prev_line:
                    break
                if is_real_part_number(prev_line):
                    break  # Hit another real part number, stop
                if has_manufacturer_data(prev_line):
                    backward_lines.insert(0, prev_line)
                    used_lines.add(prev_idx)
                    prev_idx -= 1
                else:
                    break
            
            # Prepend backward lines
            if backward_lines:
                merged_line = ' '.join(backward_lines) + ' ' + merged_line
            
            # Look FORWARD for manufacturer data on next lines
            next_idx = i + 1
            while next_idx < len(lines) and next_idx not in used_lines:
                next_line = lines[next_idx].strip()
                if not next_line:
                    break
                if is_real_part_number(next_line):
                    break  # Hit another real part number, stop
                if has_manufacturer_data(next_line):
                    merged_line += ' ' + next_line
                    used_lines.add(next_idx)
                    next_idx += 1
                else:
                    break
            
            part_lines.append(merged_line)
            used_lines.add(i)
    
    print(f"[Tesseract Parser] Found {len(part_lines)} lines with part numbers")
    
    for line in part_lines:
        # Extract part number - expanded patterns for various BOM formats
        # Patterns (in order of priority):
        # 1. Internal: ERAA/ERSA/FRAA/FRSA prefixed numbers
        # 2. Numeric: 6+ digits, optionally with hyphen suffix
        # 3. Alphanumeric: Standard MPNs (letters+digits with hyphens allowed)
        part_match = re.search(
            r'((?:ERAA|ERSA|FRAA|FRSA|ERAAZ)\d{5,}'  # Internal part numbers
            r'|(?:ERAAZ|ERAAZO|eRaa)[A-Za-z0-9]{3,}'  # OCR misreads
            r'|4[12][G6]\d{4,}-\s*[A-Z0-9\-]+'  # 42G/41G (space after hyphen allowed)
            r'|\d{6,}(?:-[A-Z0-9]{2,})?'  # Numeric: 4264000-067, 7488875
            r'|[A-Z]{2,}\d+[A-Z0-9\-]{3,}'  # Alphanumeric: AD768QBCBZ-RL7
            r'|[A-Z0-9]{3,}-\s*[A-Z0-9\-]{3,})'  # Hyphenated (space after hyphen allowed)
            , line, re.IGNORECASE
        )
        if not part_match:
            continue
        
        # Apply all OCR corrections via shared helper
        part_number = _correct_part_number(part_match.group(1))
        if not part_number:
            continue

        # Log any correction that was made
        raw_upper = part_match.group(1).upper()
        if part_number != raw_upper:
            print(f"[Tesseract Parser]   [FIX] {raw_upper} -> {part_number}")

        # Extract description (usually follows part number, ends before manufacturer names)
        # Pattern: Cap, Cer, xxx | Diode, xxx | Resistor, xxx | LED, xxx
        desc_match = re.search(r'(?:Cap|Resistor|Res|Diode|LED|Choke|Fuse|IC|MOSFET|TERM|Varistor)[^|]*?(?=\s+(?:Yes|No|[A-Z][a-z]+\s+[A-Z0-9]|\d+\s*$))', line)
        description = desc_match.group(0).strip() if desc_match else ""
        
        # Clean up description
        description = re.sub(r'\s+', ' ', description)
        description = description.replace('â€"', '-').replace('â€˜', "'")
        
        # ============================================================================
        # IMPROVED MANUFACTURER-MPN DETECTION (Multi-Strategy)
        # ============================================================================
        # Strategy 1: Sequential extraction for "Manufacturer MPN" format
        # Strategy 2: Generic pattern matching
        # Strategy 3: Detect manufacturer names anywhere in line (without MPN)
        # ============================================================================
        
        # Primary: Use sequential extraction for known manufacturers
        manufacturers = extract_sequential_mfr_mpn_pairs(line)
        
        # Fallback 1: If no known manufacturers found, try generic pattern matching
        if not manufacturers:
            # Generic pattern for unknown manufacturers
            generic_pattern = r'([A-Z][A-Za-z]{2,}(?:[\s\-][A-Z][A-Za-z]+)*)[\s\=\|\[\]]+([A-Za-z0-9][A-Za-z0-9\-_\.]{4,})(?:\s|$|[^\w])'
            matches = list(re.finditer(generic_pattern, line))
            
            # Skip words that are not manufacturers
            skip_words = {
                'Cap', 'Cer', 'Res', 'Diode', 'LED', 'Choke', 'Varistor', 'MOSFET', 
                'RESISTOR', 'TERM', 'BLOCK', 'HDR', 'ADR', 'POS', 'IC', 'TRIAC',
                'Yes', 'No', 'ROHS', 'PIN', 'Mfgr', 'Tescom', 'ERAA', 'ERSA',
                'Fuse', 'PTC', 'TVS', 'SMD', 'SMT', 'Technology', 'Electronics',
                'FIRMWARE', 'GERBER', 'FILE', 'PACKET', 'BARE', 'BOARD',
            }
            
            for match in matches:
                mfr_name = match.group(1).strip()
                mpn = match.group(2).strip()
                
                # Skip non-manufacturer words
                if mfr_name.lower() in [w.lower() for w in skip_words]:
                    continue
                
                # Clean MPN
                mpn = clean_ocr_mpn(mpn)
                
                # Validate
                if len(mfr_name) >= 3:
                    is_known_mfr = mfr_name.lower() in KNOWN_MANUFACTURERS_FOR_OCR
                    is_part_number = bool(re.match(r'^(ERAA|ERSA|FRAA|FRSA)\d+', mpn, re.IGNORECASE))
                    has_letters = bool(re.search(r'[A-Za-z]', mpn))
                    has_digits = bool(re.search(r'\d', mpn))
                    # Standard alphanumeric MPN (≥5 chars) OR short numeric for known mfr
                    is_valid_mpn = (
                        (len(mpn) >= 5 and has_letters and has_digits)
                        or (is_known_mfr and len(mpn) >= 2 and re.match(r'^\d+$', mpn))
                    )
                    if is_valid_mpn and not is_part_number:
                        manufacturers.append({
                            'manufacturer': mfr_name,
                            'mpn': mpn,
                            'preference': len(manufacturers) + 1,
                            'confidence': 0.85
                        })
        
        # Fallback 2: Detect manufacturer names anywhere in line (even without MPN)
        # This handles BOMs where manufacturer name appears but MPN is elsewhere
        if not manufacturers:
            # Apply OCR corrections to the line before searching for manufacturers
            # Only apply corrections at word boundaries to avoid corrupting valid text
            corrected_line = line
            corrected_line_lower = line.lower()
            for ocr_error, correction in OCR_MANUFACTURER_CORRECTIONS.items():
                # Only correct if it appears as a standalone word (not part of correct word)
                pattern = r'\b' + re.escape(ocr_error) + r'\b'
                if re.search(pattern, corrected_line_lower):
                    corrected_line = re.sub(pattern, correction, corrected_line, flags=re.IGNORECASE)
                    corrected_line_lower = corrected_line.lower()
            
            line_lower = corrected_line_lower
            search_line = corrected_line  # Use corrected line for name extraction
            found_mfrs = []
            matched_positions = set()  # Track matched positions to avoid duplicates
            
            # Sort manufacturers by length (longer first) to prioritize full names
            # This prevents "instruments" from matching when "texas instruments" should
            sorted_mfrs = sorted(KNOWN_MANUFACTURERS_FOR_OCR, key=len, reverse=True)
            
            # Skip partial/generic words that shouldn't be standalone manufacturers
            skip_standalone = {'instruments', 'electronics', 'technology', 'semiconductor', 
                              'mechanics', 'microelectronics', 'nstruments', 'lectronics'}
            
            for mfr in sorted_mfrs:
                if mfr in line_lower:
                    idx = line_lower.find(mfr)
                    
                    # Skip if this position was already matched by a longer name
                    if any(idx >= start and idx < end for start, end in matched_positions):
                        continue
                    
                    # Verify it's a word boundary (not part of another word)
                    before_ok = idx == 0 or not line_lower[idx-1].isalnum()
                    after_ok = idx + len(mfr) >= len(line_lower) or not line_lower[idx + len(mfr)].isalnum()
                    
                    if before_ok and after_ok:
                        # Skip standalone generic words
                        if mfr in skip_standalone:
                            continue
                        
                        # Track this position as matched
                        matched_positions.add((idx, idx + len(mfr)))
                        
                        # Get original case version
                        actual_mfr = line[idx:idx + len(mfr)]
                        # Fix OCR doubled-first-letter artifact (PaddleOCR bold text)
                        if len(actual_mfr) >= 2 and actual_mfr[0].upper() == actual_mfr[1].upper() and actual_mfr[0].isalpha():
                            actual_mfr = actual_mfr[1:]
                        # Proper case formatting
                        if actual_mfr.lower() in ['tdk', 'avx', 'nxp', 'ti', 'adi', 'ltc', 'koa', 'jst', 'cde', 'bel']:
                            actual_mfr = actual_mfr.upper()
                        else:
                            actual_mfr = actual_mfr.title()
                        found_mfrs.append((actual_mfr, idx))
            
            # Add manufacturers found (without specific MPN)
            for mfr, mfr_idx in found_mfrs:
                # Try to find an MPN near the manufacturer name (search AFTER the mfr name)
                search_start = mfr_idx + len(mfr)
                nearby_text = line[search_start:min(len(line), search_start + 60)]
                
                # Look for alphanumeric MPN pattern after manufacturer
                # Must have at least one letter AND at least one digit, 5+ chars
                mpn_match = re.search(r'([A-Z][A-Z0-9\-]{4,}[A-Z0-9])', nearby_text, re.IGNORECASE)
                if mpn_match:
                    mpn = mpn_match.group(1).upper()
                    # Reject if it's the part number, another manufacturer, a pure-letter word,
                    # or a known non-MPN English word
                    has_digit = bool(re.search(r'\d', mpn))
                    is_invalid_word = mpn.lower() in INVALID_MPN_WORDS
                    if (mpn != part_number
                            and mpn.lower() not in KNOWN_MANUFACTURERS_FOR_OCR
                            and has_digit
                            and not is_invalid_word):
                        manufacturers.append({
                            'manufacturer': mfr,
                            'mpn': mpn,
                            'preference': len(manufacturers) + 1,
                            'confidence': 0.70
                        })
                        continue
                
                # Store manufacturer without MPN (can be enriched later via API)
                manufacturers.append({
                    'manufacturer': mfr,
                    'mpn': 'N/A',
                    'preference': len(manufacturers) + 1,
                    'confidence': 0.60
                })
        
        # ====================================================================
        # POST-PROCESS: Correct manufacturer names using OCR corrections
        # ====================================================================
        for mfr_data in manufacturers:
            mfr_name = mfr_data.get('manufacturer', '')

            # Collapse multiple spaces (e.g., "Adams     Tech" → "Adams Tech")
            mfr_name = re.sub(r'\s+', ' ', mfr_name).strip()

            # Fix PaddleOCR doubled-first-letter artifact (e.g., VVishay → Vishay)
            # This happens when OCR reads bold text as a doubled first character
            if len(mfr_name) >= 2 and mfr_name[0].upper() == mfr_name[1].upper() and mfr_name[0].isalpha():
                mfr_name = mfr_name[1:]

            mfr_data['manufacturer'] = mfr_name
            mfr_lower = mfr_name.lower()

            # Apply OCR corrections to manufacturer name.
            # Use \b word-boundary so partial corrections like 'anasonic' don't
            # accidentally match inside an already-correct 'Panasonic'.
            for ocr_error, correction in OCR_MANUFACTURER_CORRECTIONS.items():
                pattern = r'\b' + re.escape(ocr_error) + r'\b'
                if re.search(pattern, mfr_lower):
                    corrected_name = re.sub(
                        pattern,
                        correction,
                        mfr_name,
                        flags=re.IGNORECASE
                    )
                    mfr_data['manufacturer'] = corrected_name.title()
                    break
            
            # Special case: "Flectro-Mechanics" alone → "Samsung Electro-Mechanics"
            # (OCR may drop "Samsung" entirely)
            if 'flectro' in mfr_data['manufacturer'].lower():
                mfr_data['manufacturer'] = 'Samsung Electro-Mechanics'
        
        # Extract quantity if present
        qty_match = re.search(r'\|\s*(\d+)\s*\|', line)
        quantity = qty_match.group(1) if qty_match else ""
        
        # Extract designators (e.g., C2, C4, C5)
        designator_match = re.search(r'([A-Z]\d+(?:,\s*[A-Z]\d+)*)', line)
        designators = designator_match.group(1) if designator_match else ""
        
        # Store ALL valid parts - even without manufacturers
        # Parts without manufacturer info can still be queried and enriched via SiliconExpert API
        confidence = 0.85 if manufacturers else 0.5
        parts.append({
            'part_number': part_number,
            'description': description,
            'manufacturers': manufacturers,
            'quantity': quantity,
            'designators': designators,
            'confidence': confidence,
            'page_number': 1,
            'source': 'paddle_ocr'
        })
        if manufacturers:
            mfr_names = ', '.join([m['manufacturer'] for m in manufacturers])
            print(f"[Tesseract Parser]   [OK] {part_number}: {len(manufacturers)} manufacturer(s) - {mfr_names}")
        else:
            print(f"[Tesseract Parser]   [~] {part_number}: No manufacturers (part still stored)")
    
    print(f"[Tesseract Parser] Total: {len(parts)} parts")
    return parts


def parse_ocr_bom_text(ocr_text: str) -> List[Dict]:
    """
    Parse BOM data from OCR text - tries multiple parsers.

    Strategy:
    1. Natural line parser (regex-based) — handles PaddleOCR spatial output
    2. Pipe-separated table parser — fallback for structured/columnar output

    Args:
        ocr_text: Raw text from PaddleOCR (or any OCR engine)

    Returns:
        List of parts extracted from OCR text
    """
    parts = []
    
    if not ocr_text or 'NO TABLE FOUND' in ocr_text.upper():
        print("[OCR Parser] No table data to parse")
        return parts
    
    # Try natural line parser first (for PaddleOCR spatial output)
    parts = parse_tesseract_bom(ocr_text)

    if parts:
        print(f"[OCR Parser] Natural line parser successful: {len(parts)} parts")
        return parts

    # Fallback to pipe-separated parser (for structured/columnar output)
    print("[OCR Parser] Trying pipe-separated parser...")
    parts = parse_pipe_separated_bom(ocr_text)
    
    return parts


def parse_pipe_separated_bom(ocr_text: str) -> List[Dict]:
    """
    Parse BOM data from pipe-separated table format.
    Only processes actual table data, ignores descriptive text.
    
    Args:
        ocr_text: Raw text from OCR (pipe-separated format)
        
    Returns:
        List of parts extracted from OCR table
    """
    parts = []
    
    lines = ocr_text.split('\n')
    print(f"[OCR Parser] Processing {len(lines)} lines of OCR text")
    
    # Filter: Only keep lines that look like table data (contain |)
    table_lines = [line.strip() for line in lines if '|' in line and line.strip()]
    
    if not table_lines:
        print("[OCR Parser] No pipe-separated table structure detected")
        return parts
    
    # Remove lines that are descriptive text (too many words, no data pattern)
    data_lines = []
    for line in table_lines:
        cells = [c.strip() for c in line.split('|')]
        # Valid table row: 3+ columns, not too much text per cell
        if len(cells) >= 3:
            # Skip if line looks like instructions/descriptions
            if any(phrase in line.lower() for phrase in ['extract', 'format', 'example', 'task', 'instruction', 'detection']):
                continue
            data_lines.append(line)
    
    print(f"[OCR Parser] Found {len(data_lines)} valid table data lines")
    
    if len(data_lines) < 2:
        print("[OCR Parser] Insufficient table rows (need header + at least 1 data row)")
        return parts
    
    # Find header row
    header_row = None
    header_idx = -1
    
    for idx, line in enumerate(data_lines):
        line_lower = line.lower()
        # Check for BOM-related headers
        if any(kw in line_lower for kw in ['part number', 'p/n', 'manufacturer', 'mfgr', 'mpn', 'description', 'qty']):
            header_row = [col.strip() for col in line.split('|')]
            header_idx = idx
            print(f"[OCR Parser] Found header row at line {idx}: {len(header_row)} columns")
            break
    
    if not header_row or header_idx == -1:
        print("[OCR Parser] No BOM header row found in table")
        return parts
    
    # Create column mapping
    column_map = {}
    for col_idx, header in enumerate(header_row):
        header_lower = header.lower()
        
        # Map to semantic types
        if any(kw in header_lower for kw in ['part number', 'p/n', 'pn', 'item']):
            column_map['part_number'] = col_idx
        elif 'description' in header_lower or 'desc' in header_lower:
            column_map['description'] = col_idx
        elif 'qty' in header_lower or 'quantity' in header_lower:
            column_map['quantity'] = col_idx
        elif 'mfgr' in header_lower or 'manufacturer' in header_lower:
            # Detect numbered manufacturers
            import re
            num_match = re.search(r'(\d+)', header)
            if num_match:
                num = num_match.group(1)
                if 'p/n' in header_lower or 'part' in header_lower:
                    column_map[f'mpn_{num}'] = col_idx
                else:
                    column_map[f'manufacturer_{num}'] = col_idx
    
    print(f"[OCR Parser] Column mapping: {column_map}")
    
    # Parse data rows (skip header and any separator lines)
    data_rows = data_lines[header_idx + 1:]
    
    # Filter out separator lines (like "---|---|---")
    data_rows = [row for row in data_rows if not all(c in '-|: ' for c in row)]
    
    print(f"[OCR Parser] Processing {len(data_rows)} data rows")
    
    for row in data_rows:
        cells = [cell.strip() for cell in row.split('|')]
        
        if len(cells) < 3:  # Skip invalid rows
            continue
        
        # CRITICAL FIX #1: Safely extract values even from shorter rows
        # Check bounds before accessing cells by index
        part_number = None
        description = ""
        quantity = ""
        
        if 'part_number' in column_map and column_map['part_number'] < len(cells):
            part_number = cells[column_map['part_number']]
        if 'description' in column_map and column_map['description'] < len(cells):
            description = cells[column_map['description']]
        if 'quantity' in column_map and column_map['quantity'] < len(cells):
            quantity = cells[column_map['quantity']]
        
        if not part_number or len(part_number) < 3:
            continue
        
        # Extract manufacturers
        manufacturers = []
        for i in range(1, 5):
            mfr_key = f'manufacturer_{i}'
            mpn_key = f'mpn_{i}'
            
            if mfr_key in column_map and mpn_key in column_map:
                mfr = cells[column_map[mfr_key]] if column_map[mfr_key] < len(cells) else ""
                mpn = cells[column_map[mpn_key]] if column_map[mpn_key] < len(cells) else ""
                
                if mfr and mpn:
                    manufacturers.append({
                        'manufacturer': mfr,
                        'mpn': mpn,
                        'preference': i,
                        'confidence': 0.7  # OCR confidence
                    })
        
        if manufacturers:
            parts.append({
                'part_number': part_number,
                'description': description,
                'manufacturers': manufacturers,
                'quantity': quantity,
                'designators': "",
                'confidence': 0.7,
                'page_number': 1,
                'source': 'ocr'
            })
    
    print(f"[OCR Parser] Extracted {len(parts)} parts from OCR text")
    
    return parts
