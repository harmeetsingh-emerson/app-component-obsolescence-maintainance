"""
Spatial Table Extractor
=======================
Position-aware BOM table extraction pipeline:

  1. Image preprocessing  — grayscale, denoise, deskew, adaptive threshold
  2. Spatial OCR          — PaddleOCR with bounding-box positions
  3. Row grouping         — cluster by Y-centre with adaptive threshold
  4. Column clustering    — cluster by X-centre into fixed ranges
  5. Cell merging         — multi-line cells joined, multi-value arrays built
  6. Header detection     — first header-like row normalised to schema
  7. LLM refinement       — Ollama call to fix OCR noise, merge broken cells
  8. BOM normalisation    — map to standard part dict used by the rest of app

Used as a fallback when the existing line-regex parsers fail to extract
structured parts from an image-based PDF / standalone image file.
"""

import os
import re
import json
import math
import requests
from typing import List, Dict, Optional, Tuple, Any

import numpy as np

# ---------------------------------------------------------------------------
# OCR engine (shared singleton from ocr_processor)
# ---------------------------------------------------------------------------
from app.ocr_processor import _get_paddle_ocr_engine, clean_ocr_mpn

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OLLAMA_URL  = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

_MFR_KEYWORDS = {
    "manufacturer", "mfr", "mfgr", "vendor", "make", "supplier",
    "manufacturer name", "mfr name",
}
_MPN_KEYWORDS = {
    "mpn", "manufacturer part", "mfr part", "mfr part number",
    "manufacturer part number", "mfr pn", "mfr p/n", "mfg p/n",
    "vendor p/n", "order code", "catalog number", "cat no",
}
_PN_KEYWORDS = {
    "part", "part number", "part no", "p/n", "pn", "item",
    "item number", "item no", "bom item", "internal p/n",
    "drawing no", "material no", "component no",
}
_DESC_KEYWORDS  = {"description", "desc", "component", "specification", "spec", "name"}
_QTY_KEYWORDS   = {"qty", "quantity", "count"}
_DESIG_KEYWORDS = {"designator", "ref des", "ref designator", "reference designator", "refdes"}
_LEVEL_KEYWORDS = {"level", "lvl", "indent"}

# OCR character-confusion corrections applied to every MPN
_OCR_CHAR_MAP = [
    (re.compile(r'^CLO(\d)'),  r'CL0\1'),   # Samsung CL series
    (re.compile(r'^GMCO(\d)'), r'GMC0\1'),  # GMC capacitors
    (re.compile(r'^GRMO(\d)'), r'GRM0\1'),  # GRM capacitors
    (re.compile(r'^WRO(\d)'),  r'WR0\1'),
    (re.compile(r'^RKT(\d)'),  r'RK7\1'),
]

# ---------------------------------------------------------------------------
# Step 1 — Image preprocessing
# ---------------------------------------------------------------------------

def preprocess_image(img_array: np.ndarray) -> np.ndarray:
    """
    Grayscale → denoise → deskew → adaptive threshold.
    Returns a cleaned uint8 numpy array (still RGB, but visually binary).
    """
    try:
        import cv2
    except ImportError:
        return img_array  # cv2 not installed — use raw image

    # Grayscale
    if img_array.ndim == 3 and img_array.shape[2] == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array.copy()

    # Denoise
    denoised = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    # Deskew via Hough transform on edges
    edges = cv2.Canny(denoised, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, math.pi / 180, threshold=100,
                            minLineLength=img_array.shape[1] // 4, maxLineGap=20)
    angle = 0.0
    if lines is not None:
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if abs(x2 - x1) > 10:
                a = math.degrees(math.atan2(y2 - y1, x2 - x1))
                if abs(a) < 10:
                    angles.append(a)
        if angles:
            angle = float(np.median(angles))

    if abs(angle) > 0.3:
        h, w = denoised.shape
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        denoised = cv2.warpAffine(denoised, M, (w, h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)

    # Adaptive threshold — makes text crisp for OCR
    binary = cv2.adaptiveThreshold(
        denoised, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 21, 10
    )

    # Back to RGB so PaddleOCR accepts it
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)


# ---------------------------------------------------------------------------
# Step 2 — Spatial OCR
# ---------------------------------------------------------------------------

def run_spatial_ocr(img_array: np.ndarray) -> List[Dict]:
    """
    Run PaddleOCR and return a list of detected blocks with positions:
      { "text": str, "x": float, "y": float, "w": float, "h": float,
        "x_center": float, "y_center": float, "confidence": float }
    Sorted top-to-bottom then left-to-right.
    """
    engine = _get_paddle_ocr_engine()
    if engine is None:
        return []

    raw = list(engine.predict(img_array))
    if not raw or not raw[0].get("rec_texts"):
        return []

    page_res   = raw[0]
    rec_texts  = page_res.get("rec_texts",  [])
    rec_scores = page_res.get("rec_scores", [])
    dt_polys   = page_res.get("dt_polys",   [])

    blocks = []
    for poly, text, score in zip(dt_polys, rec_texts, rec_scores):
        if score < 0.40 or not text.strip():
            continue
        xs = [pt[0] for pt in poly]
        ys = [pt[1] for pt in poly]
        x, y = min(xs), min(ys)
        w, h = max(xs) - x, max(ys) - y
        blocks.append({
            "text":     text.strip(),
            "x":        x,
            "y":        y,
            "w":        w,
            "h":        h,
            "x_center": x + w / 2,
            "y_center": y + h / 2,
            "confidence": score,
        })

    blocks.sort(key=lambda b: (b["y_center"], b["x_center"]))
    return blocks


# ---------------------------------------------------------------------------
# Step 3 — Row grouping by Y-centre
# ---------------------------------------------------------------------------

def group_into_rows(blocks: List[Dict], row_threshold: int = 12) -> List[List[Dict]]:
    """
    Cluster blocks into rows by similar Y-centre.
    Adaptive threshold = max(row_threshold, median_height * 0.6).
    """
    if not blocks:
        return []

    heights = [b["h"] for b in blocks if b["h"] > 0]
    if heights:
        adaptive = max(row_threshold, np.median(heights) * 0.6)
    else:
        adaptive = row_threshold

    rows: List[List[Dict]] = []
    for block in blocks:
        yc = block["y_center"]
        placed = False
        for row in reversed(rows):
            row_yc = np.mean([b["y_center"] for b in row])
            if abs(yc - row_yc) <= adaptive:
                row.append(block)
                placed = True
                break
        if not placed:
            rows.append([block])

    # Sort each row left→right
    for row in rows:
        row.sort(key=lambda b: b["x_center"])

    return rows


# ---------------------------------------------------------------------------
# Step 4 — Column clustering by X-centre
# ---------------------------------------------------------------------------

def cluster_columns(rows: List[List[Dict]]) -> List[float]:
    """
    Collect all X-centres, cluster into column boundaries using
    a simple gap-based algorithm.  Returns sorted list of column X-centres.
    """
    all_x = sorted(set(round(b["x_center"]) for row in rows for b in row))
    if not all_x:
        return []

    # Find natural gaps between clusters
    gap_threshold = max(20, (all_x[-1] - all_x[0]) / max(len(all_x), 1) * 0.6)
    columns = []
    cluster_start = all_x[0]
    prev = all_x[0]
    for x in all_x[1:]:
        if x - prev > gap_threshold:
            columns.append((cluster_start + prev) / 2)
            cluster_start = x
        prev = x
    columns.append((cluster_start + prev) / 2)
    return columns


def assign_column(x_center: float, col_centers: List[float]) -> int:
    """Return the index of the nearest column centre."""
    if not col_centers:
        return 0
    return min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - x_center))


# ---------------------------------------------------------------------------
# Step 5 — Build cell grid
# ---------------------------------------------------------------------------

def build_cell_grid(rows: List[List[Dict]], col_centers: List[float]) -> List[List[str]]:
    """
    Map each block to (row, col) and merge multi-line blocks in the same cell.
    Returns a 2D list of strings [row][col].
    """
    n_cols = len(col_centers)
    grid: List[List[str]] = []

    for row_blocks in rows:
        cells = [""] * n_cols
        for block in row_blocks:
            ci = assign_column(block["x_center"], col_centers)
            sep = " " if cells[ci] else ""
            cells[ci] = cells[ci] + sep + block["text"]
        grid.append(cells)

    return grid


# ---------------------------------------------------------------------------
# Step 6 — Header detection
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r'\s+', ' ', s.strip().lower())


def detect_header_row(grid: List[List[str]]) -> Tuple[int, List[str]]:
    """
    Scan the first 10 rows looking for the one with the most header-like cells.
    Returns (row_index, list_of_header_strings).
    Heuristics: a header cell is short (<60 chars), no digits alone, matches
    known BOM keyword sets.
    """
    all_keywords = (
        _PN_KEYWORDS | _MFR_KEYWORDS | _MPN_KEYWORDS |
        _DESC_KEYWORDS | _QTY_KEYWORDS | _DESIG_KEYWORDS | _LEVEL_KEYWORDS
    )

    best_idx, best_score = 0, -1
    for ri, row in enumerate(grid[:10]):
        score = 0
        for cell in row:
            cn = _norm(cell)
            if not cn:
                continue
            if len(cn) > 60:
                continue
            if cn in all_keywords:
                score += 3
                continue
            for kw in all_keywords:
                if kw in cn or cn in kw:
                    score += 1
                    break
        if score > best_score:
            best_score, best_idx = score, ri

    return best_idx, grid[best_idx] if grid else []


# ---------------------------------------------------------------------------
# Step 7 — Map columns to schema
# ---------------------------------------------------------------------------

def map_headers_to_schema(raw_headers: List[str]) -> Dict[str, int]:
    """
    Returns { schema_field: col_index } for every column we care about.
    Multiple manufacturer/MPN columns are returned as mfr_0, mfr_1, mpn_0, …
    """
    mapping: Dict[str, int] = {}
    mfr_n = mpn_n = 0
    seen_pn = False

    for ci, hdr in enumerate(raw_headers):
        hn = _norm(hdr)
        if not hn:
            continue

        if any(kw in hn for kw in _LEVEL_KEYWORDS) and "level" not in mapping:
            mapping["level"] = ci
        elif any(kw in hn for kw in _DESIG_KEYWORDS) and "designator" not in mapping:
            mapping["designator"] = ci
        elif any(kw in hn for kw in _QTY_KEYWORDS) and "quantity" not in mapping:
            mapping["quantity"] = ci
        elif any(kw in hn for kw in _DESC_KEYWORDS) and "description" not in mapping:
            mapping["description"] = ci
        elif any(kw in hn for kw in _MPN_KEYWORDS):
            mapping[f"mpn_{mpn_n}"] = ci
            mpn_n += 1
        elif any(kw in hn for kw in _MFR_KEYWORDS):
            mapping[f"mfr_{mfr_n}"] = ci
            mfr_n += 1
        elif any(kw in hn for kw in _PN_KEYWORDS):
            if not seen_pn:
                mapping["part_number"] = ci
                seen_pn = True
            else:
                # Second part-number-like column is usually an MPN
                mapping[f"mpn_{mpn_n}"] = ci
                mpn_n += 1

    return mapping


# ---------------------------------------------------------------------------
# Step 8 — Extract structured rows
# ---------------------------------------------------------------------------

def extract_structured_rows(
    grid: List[List[str]],
    header_idx: int,
    col_mapping: Dict[str, int],
    raw_headers: List[str],
) -> List[Dict[str, Any]]:
    """
    Convert each grid row (after the header) into a structured dict.
    Multi-value manufacturer/MPN columns are collected into arrays.
    Non-mapped columns go into extra_fields.
    """
    mapped_cols = set(col_mapping.values())
    mfr_keys  = sorted(k for k in col_mapping if k.startswith("mfr_"))
    mpn_keys  = sorted(k for k in col_mapping if k.startswith("mpn_"))
    n_cols    = len(raw_headers)

    structured = []
    for row in grid[header_idx + 1:]:
        # Skip separator or totally empty rows
        non_empty = [c for c in row if c.strip()]
        if not non_empty or all(re.fullmatch(r'[-=|_ ]+', c) for c in non_empty):
            continue

        def _get(key: str, default: str = "") -> str:
            idx = col_mapping.get(key)
            if idx is None or idx >= len(row):
                return default
            return row[idx].strip()

        # Build manufacturers list (paired mfr/mpn)
        manufacturers = []
        max_pairs = max(len(mfr_keys), len(mpn_keys), 1)
        for i in range(max_pairs):
            mfr_key = f"mfr_{i}"
            mpn_key = f"mpn_{i}"
            mfr = _get(mfr_key)
            mpn = clean_ocr_mpn(_get(mpn_key)) if _get(mpn_key) else ""
            # Apply targeted OCR corrections
            for pat, repl in _OCR_CHAR_MAP:
                mpn = pat.sub(repl, mpn)
            if mfr or mpn:
                manufacturers.append({
                    "manufacturer": mfr,
                    "mpn":          mpn or "N/A",
                    "preference":   i + 1,
                    "confidence":   0.80,
                })

        part_number = _get("part_number")
        if not part_number and not manufacturers:
            continue  # nothing useful in this row

        # Extra columns not in schema
        extra_fields: Dict[str, str] = {}
        for ci, hdr in enumerate(raw_headers):
            if ci in mapped_cols or ci >= len(row):
                continue
            key = hdr.strip()
            val = row[ci].strip() if ci < len(row) else ""
            if key and val:
                extra_fields[key] = val

        result: Dict[str, Any] = {
            "part_number":  part_number,
            "description":  _get("description"),
            "manufacturers": manufacturers,
            "quantity":     _get("quantity"),
            "designators":  _get("designator"),
            "confidence":   0.80,
            "page_number":  1,
            "source":       "spatial_ocr",
        }
        if _get("level"):
            result["level"] = _get("level")
        if extra_fields:
            result["extra_fields"] = extra_fields

        structured.append(result)

    return structured


# ---------------------------------------------------------------------------
# Step 9 — LLM refinement (Ollama)
# ---------------------------------------------------------------------------

_LLM_SYSTEM = (
    "You are a BOM table expert. You receive a JSON object representing OCR-extracted rows "
    "from an engineering Bill of Materials. Your job is to fix OCR errors, merge broken cells, "
    "correct manufacturer names, and ensure every row has the right fields. "
    "Return ONLY the corrected JSON with the same schema. No explanation."
)

_LLM_ROW_LIMIT = 30   # only send first N rows to LLM (token budget)
_LLM_TIMEOUT   = 45   # seconds


def _call_ollama(prompt: str) -> Optional[str]:
    """POST to Ollama /api/generate; return the response text or None."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=_LLM_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
    except Exception as exc:
        print(f"[SpatialOCR] LLM call failed: {exc}")
    return None


def llm_refine_rows(rows: List[Dict], raw_headers: List[str]) -> List[Dict]:
    """
    Send the extracted rows to Ollama for cleaning.
    Falls back to unrefined rows if the LLM is unavailable or returns bad JSON.
    Only processes first _LLM_ROW_LIMIT rows to stay within token budget.
    """
    if not rows:
        return rows

    sample = rows[:_LLM_ROW_LIMIT]
    prompt = (
        f"Below are OCR-extracted BOM rows in JSON format.\n"
        f"Original column headers: {raw_headers}\n\n"
        f"Rows:\n{json.dumps(sample, indent=2)}\n\n"
        "Tasks:\n"
        "1. Fix OCR character errors (O vs 0, I vs 1, etc.) in part numbers and MPNs.\n"
        "2. Merge any description text that was split across lines.\n"
        "3. Correct obvious manufacturer name misspellings.\n"
        "4. If a row has multiple manufacturer names or MPNs, keep them all in the "
        "   'manufacturers' array.\n"
        "5. Remove rows that are clearly separator lines or contain no part data.\n"
        "6. Return ONLY the corrected JSON array of rows. No explanation, no markdown."
    )

    response = _call_ollama(prompt)
    if not response:
        print("[SpatialOCR] LLM unavailable — skipping refinement")
        return rows

    # Try to parse JSON from response
    try:
        # Strip any accidental markdown fences
        clean = re.sub(r"```(?:json)?", "", response).strip().strip("`").strip()
        # Find first [ ... ]
        m = re.search(r'\[.*\]', clean, re.DOTALL)
        if m:
            refined_sample = json.loads(m.group())
            if isinstance(refined_sample, list):
                # Merge refined sample back with any rows beyond the limit
                return refined_sample + rows[_LLM_ROW_LIMIT:]
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[SpatialOCR] LLM returned invalid JSON ({exc}) — keeping original")

    return rows


# ---------------------------------------------------------------------------
# Step 10 — Main entry point
# ---------------------------------------------------------------------------

def extract_table_from_image(
    img_array: np.ndarray,
    use_llm: bool = True,
    row_threshold: int = 12,
) -> List[Dict]:
    """
    Full spatial table extraction pipeline for a single image/page.

    Args:
        img_array:      RGB numpy array (H×W×3).
        use_llm:        Whether to call Ollama for LLM refinement.
        row_threshold:  Base pixel tolerance for row grouping.

    Returns:
        List of standardised part dicts compatible with the FAISS store.
    """
    print("[SpatialOCR] Step 1: preprocessing image")
    processed = preprocess_image(img_array)

    print("[SpatialOCR] Step 2: running spatial OCR")
    blocks = run_spatial_ocr(processed)
    if not blocks:
        print("[SpatialOCR] No text detected")
        return []
    print(f"[SpatialOCR]   {len(blocks)} blocks detected")

    print("[SpatialOCR] Step 3: grouping into rows")
    rows = group_into_rows(blocks, row_threshold=row_threshold)
    print(f"[SpatialOCR]   {len(rows)} rows")

    print("[SpatialOCR] Step 4: clustering columns")
    col_centers = cluster_columns(rows)
    print(f"[SpatialOCR]   {len(col_centers)} columns detected")
    if not col_centers:
        return []

    print("[SpatialOCR] Step 5: building cell grid")
    grid = build_cell_grid(rows, col_centers)

    print("[SpatialOCR] Step 6: detecting header row")
    header_idx, raw_headers = detect_header_row(grid)
    print(f"[SpatialOCR]   Header at row {header_idx}: {raw_headers}")

    print("[SpatialOCR] Step 7: mapping columns to schema")
    col_mapping = map_headers_to_schema(raw_headers)
    print(f"[SpatialOCR]   Column mapping: {col_mapping}")

    print("[SpatialOCR] Step 8: extracting structured rows")
    structured = extract_structured_rows(grid, header_idx, col_mapping, raw_headers)
    print(f"[SpatialOCR]   {len(structured)} candidate rows")

    if use_llm and structured:
        print("[SpatialOCR] Step 9: LLM refinement")
        structured = llm_refine_rows(structured, raw_headers)
        print(f"[SpatialOCR]   {len(structured)} rows after LLM refinement")

    # Final filter: must have a part_number or at least one manufacturer with real MPN
    final = []
    for p in structured:
        has_pn  = bool((p.get("part_number") or "").strip())
        has_mpn = any(
            m.get("mpn", "N/A") not in ("N/A", "", None)
            for m in p.get("manufacturers", [])
        )
        if has_pn or has_mpn:
            final.append(p)

    print(f"[SpatialOCR] Done: {len(final)} valid parts extracted")
    return final


# ---------------------------------------------------------------------------
# Universal grid-based entry point (structured files: Excel, CSV, Word, PPTX,
# and text-based PDFs extracted via pdfplumber)
# ---------------------------------------------------------------------------

def extract_table_from_grid(
    grid: List[List[str]],
    source_name: str = "",
    use_llm: bool = True,
) -> List[Dict]:
    """
    Run the spatial pipeline on a pre-built 2D list of strings (steps 6-10).
    No image preprocessing or OCR needed — the caller supplies the raw grid.

    This is the universal path for:
      - Excel / CSV  (openpyxl / csv.reader rows)
      - Word tables  (python-docx)
      - PPTX tables  (python-pptx)
      - Text-based PDF tables (pdfplumber)

    Args:
        grid:        List of rows; each row is a list of cell strings.
        source_name: Label used in log messages.
        use_llm:     Whether to call Ollama for LLM refinement.

    Returns:
        List of standardised part dicts compatible with the FAISS store.
    """
    label = source_name or "grid"

    # Normalise: all cells are stripped strings
    grid = [[str(c).strip() for c in row] for row in grid if any(str(c).strip() for c in row)]
    if len(grid) < 2:
        print(f"[SpatialOCR:{label}] Grid too small ({len(grid)} rows) — skipping")
        return []

    print(f"[SpatialOCR:{label}] Step 6: detecting header row")
    header_idx, raw_headers = detect_header_row(grid)
    print(f"[SpatialOCR:{label}]   Header at row {header_idx}: {raw_headers}")

    print(f"[SpatialOCR:{label}] Step 7: mapping columns to schema")
    col_mapping = map_headers_to_schema(raw_headers)
    print(f"[SpatialOCR:{label}]   Column mapping: {col_mapping}")

    if not col_mapping:
        print(f"[SpatialOCR:{label}] No BOM columns recognised — skipping")
        return []

    print(f"[SpatialOCR:{label}] Step 8: extracting structured rows")
    structured = extract_structured_rows(grid, header_idx, col_mapping, raw_headers)
    print(f"[SpatialOCR:{label}]   {len(structured)} candidate rows")

    if use_llm and structured:
        print(f"[SpatialOCR:{label}] Step 9: LLM refinement")
        structured = llm_refine_rows(structured, raw_headers)
        print(f"[SpatialOCR:{label}]   {len(structured)} rows after LLM refinement")

    final = [
        p for p in structured
        if (p.get("part_number") or "").strip()
        or any(
            m.get("mpn", "N/A") not in ("N/A", "", None)
            for m in p.get("manufacturers", [])
        )
    ]

    print(f"[SpatialOCR:{label}] Done: {len(final)} valid parts from grid")
    return final


def extract_table_from_pdf_text(
    pdf_path: str,
    use_llm: bool = True,
) -> List[Dict]:
    """
    Extract BOM parts from a text-based PDF using pdfplumber table detection,
    then run each extracted table through the spatial grid pipeline (steps 6-10).

    Use this when bom_parser_v2's structured detection returns 0 parts.
    """
    try:
        import pdfplumber
    except ImportError:
        print("[SpatialOCR] pdfplumber not installed — skipping PDF text grid extraction")
        return []

    print(f"[SpatialOCR] Reading pdfplumber tables from: {pdf_path}")
    all_parts: List[Dict] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables() or []
                if not tables:
                    # Fallback: treat text lines as a single-column table
                    words = page.extract_words()
                    if words:
                        lines: List[List[str]] = []
                        # Group words by similar y-top into rows
                        prev_top = None
                        row: List[str] = []
                        for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
                            if prev_top is None or abs(w["top"] - prev_top) < 5:
                                row.append(w["text"])
                            else:
                                if row:
                                    lines.append(row)
                                row = [w["text"]]
                            prev_top = w["top"]
                        if row:
                            lines.append(row)
                        tables = [lines] if lines else []

                for tbl_idx, raw_table in enumerate(tables):
                    if not raw_table:
                        continue
                    # pdfplumber cells may be None
                    grid = [
                        [str(c) if c is not None else "" for c in row]
                        for row in raw_table
                    ]
                    parts = extract_table_from_grid(
                        grid,
                        source_name=f"PDF p{page_num} tbl{tbl_idx + 1}",
                        use_llm=use_llm,
                    )
                    for p in parts:
                        p["page_number"] = page_num
                    all_parts.extend(parts)

    except Exception as exc:
        print(f"[SpatialOCR] pdfplumber error: {exc}")

    print(f"[SpatialOCR] PDF text grid total: {len(all_parts)} parts")
    return all_parts


# ---------------------------------------------------------------------------
# Convenience: run over all pages of a PDF (image-based via PaddleOCR)
# ---------------------------------------------------------------------------

def extract_table_from_pdf(
    pdf_path: str,
    dpi: int = 200,
    use_llm: bool = True,
) -> List[Dict]:
    """
    Run the spatial pipeline on every page of a PDF.
    Returns combined list of parts across all pages.
    """
    import fitz

    print(f"[SpatialOCR] Processing PDF: {pdf_path}")
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        print(f"[SpatialOCR] Cannot open PDF: {exc}")
        return []

    all_parts: List[Dict] = []
    for page_num in range(len(doc)):
        print(f"[SpatialOCR] Page {page_num + 1}/{len(doc)}")
        page = doc[page_num]
        mat  = fitz.Matrix(dpi / 72, dpi / 72)
        pix  = page.get_pixmap(matrix=mat)
        img  = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)

        page_parts = extract_table_from_image(img, use_llm=use_llm)
        for p in page_parts:
            p["page_number"] = page_num + 1
        all_parts.extend(page_parts)

    doc.close()
    print(f"[SpatialOCR] Total across all pages: {len(all_parts)} parts")
    return all_parts
