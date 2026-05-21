"""
PaddleOCR 3.0 - Full PDF Extraction to Readable File

Usage:
    python extract_ocr.py <path_to_pdf> [output_file]

Examples:
    python extract_ocr.py uploads/ERAA24476.pdf
    python extract_ocr.py uploads/ERAA24476.pdf outputs/ERAA24476_ocr.txt
"""

import sys
import os
import re

# Disable mkldnn-by-default in PaddleX so the runner uses plain 'paddle' mode
# instead of 'mkldnn', which triggers a PIR/oneDNN crash on Windows:
#   ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"

from pathlib import Path

import numpy as np
import fitz  # PyMuPDF


def render_page(page, dpi: int = 300) -> np.ndarray:
    """Render a PDF page to an RGB numpy array at the given DPI."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def group_into_rows(detections, row_gap_px: int = 15):
    """
    Group individual text detections into logical rows based on their
    vertical (Y) position on the page.

    detections: list of [[x0,y0],[x1,y1],[x2,y2],[x3,y3]], [text, conf]]
    Returns: list of rows, each row is a list of (x_center, text) pairs.
    """
    if not detections:
        return []

    # Build list of (y_center, x_center, text, confidence)
    items = []
    for det in detections:
        bbox, (text, conf) = det
        y_top = min(pt[1] for pt in bbox)
        y_bot = max(pt[1] for pt in bbox)
        x_left = min(pt[0] for pt in bbox)
        x_right = max(pt[0] for pt in bbox)
        y_center = (y_top + y_bot) / 2
        x_center = (x_left + x_right) / 2
        items.append((y_center, x_center, text, conf))

    # Sort top-to-bottom
    items.sort(key=lambda t: t[0])

    # Cluster into rows: items within row_gap_px of each other share a row
    rows = []
    current_row = [items[0]]
    for item in items[1:]:
        if abs(item[0] - current_row[-1][0]) <= row_gap_px:
            current_row.append(item)
        else:
            rows.append(current_row)
            current_row = [item]
    rows.append(current_row)

    # Within each row sort left-to-right
    for row in rows:
        row.sort(key=lambda t: t[1])

    return rows


def rows_to_text(rows, tab_width: int = 4) -> str:
    """
    Convert grouped rows to human-readable text.
    Cells within a row are joined with a tab separator.
    """
    lines = []
    for row in rows:
        cells = [item[2].strip() for item in row if item[2].strip()]
        if cells:
            lines.append("\t".join(cells))
    return "\n".join(lines)


def extract_pdf_with_paddleocr(pdf_path: str, output_path: str, dpi: int = 150,
                                min_confidence: float = 0.5, row_gap_px: int = 15):
    """
    Run PaddleOCR on every page of a PDF and write a clean, readable output file.

    Output format per page:
        === PAGE N (W x H px @ DPI dpi) ===
        <tab-separated text rows>

    Args:
        pdf_path:       Path to the input PDF.
        output_path:    Path to write the extracted text.
        dpi:            Render resolution (higher = better OCR accuracy).
        min_confidence: Minimum OCR confidence to include a detection (0-1).
        row_gap_px:     Maximum vertical gap (px) between detections in the same row.
    """
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        print("[ERROR] PaddleOCR is not installed. Run: pip install paddleocr paddlepaddle")
        sys.exit(1)

    print("[OCR] Initializing PaddleOCR 3.0 (English, fast mode)...")
    # Disable orientation classifiers and document-unwarping — not needed for
    # flat BOM sheets and cuts startup time by ~60%.
    ocr_engine = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        lang="en",
    )

    doc = fitz.open(pdf_path)
    num_pages = len(doc)
    print(f"[OCR] PDF: {pdf_path}  ({num_pages} page(s))")

    output_lines = [
        f"PDF OCR EXTRACTION — PaddleOCR 3.0",
        f"Source  : {Path(pdf_path).resolve()}",
        f"Pages   : {num_pages}",
        f"DPI     : {dpi}",
        f"Min conf: {min_confidence}",
        "=" * 80,
        "",
    ]

    total_words = 0

    for page_idx in range(num_pages):
        page = doc[page_idx]
        img = render_page(page, dpi=dpi)
        h, w = img.shape[:2]
        page_num = page_idx + 1

        print(f"[OCR]   Page {page_num}/{num_pages}  ({w}x{h} px)...")

        # PaddleOCR 3.0 API: use predict() instead of ocr()
        raw = list(ocr_engine.predict(img))

        output_lines.append(f"=== PAGE {page_num}  ({w}x{h} px @ {dpi} dpi) ===")
        output_lines.append("")

        if not raw:
            output_lines.append("  [No text detected on this page]")
            output_lines.append("")
            continue

        # PaddleOCR 3.0 result: raw[0] may be a dict or an object with a .res dict.
        page_res = raw[0]
        if hasattr(page_res, "res"):
            page_res = page_res.res  # unwrap result object if needed

        if page_num == 1:  # debug first page only
            print(f"[OCR DEBUG] result type={type(raw[0])}, keys={list(page_res.keys()) if hasattr(page_res, 'keys') else 'N/A'}")

        dt_polys   = page_res.get("dt_polys",   [])
        rec_texts  = page_res.get("rec_texts",  [])
        rec_scores = page_res.get("rec_scores", [])

        if not rec_texts:
            output_lines.append("  [No text detected on this page]")
            output_lines.append("")
            continue

        # Build detections in the format group_into_rows expects:
        #   [[x0,y0],[x1,y1],[x2,y2],[x3,y3]], [text, conf]
        detections = []
        all_count = len(rec_texts)
        for bbox, text, score in zip(dt_polys, rec_texts, rec_scores):
            if score >= min_confidence and text.strip():
                detections.append((bbox, (text, score)))

        rejected = all_count - len(detections)
        print(f"[OCR]     {len(detections)} detections kept  ({rejected} below conf {min_confidence})")

        # Group spatially into rows, then convert to text
        rows = group_into_rows(detections, row_gap_px=row_gap_px)
        page_text = rows_to_text(rows)

        output_lines.append(page_text)
        output_lines.append("")

        # Stats
        word_count = sum(len(item[2].split()) for row in rows for item in row)
        total_words += word_count
        print(f"[OCR]     {len(rows)} rows, ~{word_count} words")

    doc.close()

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"\n[OCR] Done. Total words extracted: {total_words}")
    print(f"[OCR] Output written to: {out_path.resolve()}")
    return str(out_path.resolve())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        # Default: run on both sample PDFs in uploads/
        pdfs = list(Path("uploads").glob("*.pdf"))
        if not pdfs:
            print("Usage: python extract_ocr.py <pdf_path> [output_file]")
            sys.exit(1)
    else:
        pdfs = [Path(sys.argv[1])]

    for pdf_path in pdfs:
        if not pdf_path.exists():
            print(f"[ERROR] File not found: {pdf_path}")
            continue

        if len(sys.argv) >= 3 and len(pdfs) == 1:
            output_path = sys.argv[2]
        else:
            stem = pdf_path.stem
            output_path = f"ocr_outputs/{stem}_ocr.txt"

        extract_pdf_with_paddleocr(str(pdf_path), output_path)


if __name__ == "__main__":
    main()
