"""
FAISS Multi-Agent Routes - API endpoints using FAISS and multi-agent system

Features:
- Upload BOM documents → extract ALL manufacturers → store in FAISS
- Query using multi-agent system with semantic search
- Call SiliconExpert API with ALL manufacturer-MPN pairs
"""

from fastapi import APIRouter, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse
import os
import glob
from datetime import datetime

from app.bom_parser_v2 import parse_bom_document
from app.faiss_bom_store import get_faiss_store
from app.multi_agent_faiss import get_orchestrator
from app.ocr_processor import parse_ocr_bom_text
from app.file_converters import convert_and_parse, get_file_type, SUPPORTED_EXTENSIONS, IMAGE_EXTENSIONS
from app.ocr_store import (
    append_ocr_extraction,
    search_ocr_extraction,
    get_ocr_store_stats,
    get_ocr_processing_status,
    get_ocr_text_for_source,
    mark_ocr_started,
    mark_ocr_complete,
    mark_ocr_failed,
    update_ocr_page_progress,
    clear_stale_in_progress,
    list_ocr_sources,
    clear_ocr_store,
)


router = APIRouter()

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)


def _pdf_needs_ocr(file_path: str) -> bool:
    """Return True when the PDF has little/no native text (image-based)."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        total_text = sum(len(p.get_text("text").strip()) for p in doc)
        doc.close()
        return total_text < 100
    except Exception:
        return False


def _run_ocr_and_store(file_path: str, filename: str, dpi: int = 200) -> None:
    """
    Background task: run PaddleOCR on *file_path* page-by-page and
    append the extracted text to the common OCR extraction store.
    Runs in a thread after the HTTP response is returned.
    dpi controls render resolution: higher = more accurate but slower.
    """
    print(f"[BgOCR] Starting OCR for '{filename}'…")
    mark_ocr_started(filename)
    try:
        import fitz
        import numpy as np
        from app.ocr_processor import _get_paddle_ocr_engine

        # Count pages so we can report progress
        doc = fitz.open(file_path)
        num_pages = len(doc)
        doc.close()
        print(f"[BgOCR] '{filename}': {num_pages} page(s) to process")
        update_ocr_page_progress(filename, 0, num_pages)

        # Pre-warm engine before processing starts
        print(f"[BgOCR] Initialising PaddleOCR engine…")
        engine = _get_paddle_ocr_engine()
        if engine is None:
            mark_ocr_failed(filename, "PaddleOCR engine failed to initialise")
            return

        # Process page by page, writing progress after each page
        doc = fitz.open(file_path)
        all_text = []
        print(f"[BgOCR] '{filename}': rendering at {dpi} DPI")

        for page_num in range(num_pages):
            update_ocr_page_progress(filename, page_num + 1, num_pages)
            print(f"[BgOCR] '{filename}': page {page_num + 1}/{num_pages}…")

            page = doc[page_num]

            # Try native text first (fast path)
            native_text = page.get_text("text").strip()
            if len(native_text) >= 50:
                all_text.append(f"=== PAGE {page_num + 1} ===\n{native_text}")
                print(f"[BgOCR]   [NATIVE] {len(native_text)} chars")
                continue

            # PaddleOCR fallback
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3
            )

            # Run OCR with a per-page timeout that scales with DPI.
            # Pixel count grows as DPI^2, so OCR work scales the same way.
            # Base: 120s at 200 DPI  ->  300 DPI = 270s, 400 DPI = 480s.
            # Clamped to 60-600s so it never hangs indefinitely.
            try:
                import concurrent.futures as _cf
                _PAGE_TIMEOUT = int(max(60, min(600, 120 * (dpi / 200) ** 2)))
                print(f"[BgOCR]   [TIMEOUT-BUDGET] {_PAGE_TIMEOUT}s at {dpi} DPI")
                with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                    _future = _pool.submit(lambda img=img_array: list(engine.predict(img)))
                    try:
                        raw = _future.result(timeout=_PAGE_TIMEOUT)
                    except _cf.TimeoutError:
                        print(f"[BgOCR]   [TIMEOUT] Page {page_num + 1} exceeded {_PAGE_TIMEOUT}s - skipping")
                        all_text.append(f"=== PAGE {page_num + 1} ===\n[OCR TIMEOUT after {_PAGE_TIMEOUT}s]")
                        continue
            except Exception as page_exc:
                print(f"[BgOCR]   [X] Page {page_num + 1} OCR error: {page_exc}")
                all_text.append(f"=== PAGE {page_num + 1} ===\n[OCR ERROR: {page_exc}]")
                continue

            if raw and raw[0].get("rec_texts"):
                page_res = raw[0]
                rec_texts  = page_res.get("rec_texts",  [])
                rec_scores = page_res.get("rec_scores", [])
                dt_polys   = page_res.get("dt_polys",   [])

                # Filter by confidence first
                valid = [
                    (poly, text.strip(), score)
                    for poly, text, score in zip(dt_polys, rec_texts, rec_scores)
                    if score >= 0.5 and text.strip()
                ]

                # --- Table-aware row grouping ---
                # Each dt_poly is a 4-point polygon [[x,y], ...].
                # Compute the vertical center of each block, then group blocks
                # whose centers are within ROW_THRESHOLD pixels of each other
                # (same row). Within each row sort left→right by X center.
                # This reconstructs table rows as single tab-separated lines.
                ROW_THRESHOLD = 10  # pixels at render DPI (150)

                def _y_center(poly):
                    ys = [pt[1] for pt in poly]
                    return (min(ys) + max(ys)) / 2

                def _x_center(poly):
                    xs = [pt[0] for pt in poly]
                    return (min(xs) + max(xs)) / 2

                # Sort all blocks top→bottom by y-center
                valid.sort(key=lambda t: _y_center(t[0]))

                rows = []          # list of lists of (x_center, text)
                for poly, text, _score in valid:
                    yc = _y_center(poly)
                    xc = _x_center(poly)
                    # Try to append to the last open row
                    if rows and abs(yc - rows[-1][0]) <= ROW_THRESHOLD:
                        rows[-1][1].append((xc, text))
                    else:
                        rows.append([yc, [(xc, text)]])  # [row_y, [(xc, text), ...]]

                # Build output: each row → cells sorted left→right, joined by \t
                page_lines = []
                for _row_y, cells in rows:
                    cells.sort(key=lambda c: c[0])          # sort by x
                    row_text = "\t".join(c[1] for c in cells)
                    page_lines.append(row_text)

                page_text = "\n".join(page_lines)
                all_text.append(f"=== PAGE {page_num + 1} ===\n{page_text}")
                print(f"[BgOCR]   [PADDLE] {len(valid)} blocks → {len(page_lines)} rows, {len(page_text)} chars")
            else:
                print(f"[BgOCR]   [X] No text on page {page_num + 1}")

        doc.close()
        ocr_text = "\n\n".join(all_text)
        print(f"[BgOCR] '{filename}': all pages done — {len(ocr_text)} total chars")

        if ocr_text.strip():
            append_ocr_extraction(filename, ocr_text)
            mark_ocr_complete(filename, len(ocr_text))
        else:
            mark_ocr_failed(filename, "OCR produced no text from any page")
            print(f"[BgOCR] OCR returned no text for '{filename}'")

    except Exception as exc:
        import traceback
        msg = str(exc)
        print(f"[BgOCR] Error processing '{filename}': {msg}")
        traceback.print_exc()
        mark_ocr_failed(filename, msg)


@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    ocr_dpi: int = Form(200),
):
    """
    Upload a BOM document.

    Routing logic:
    - .pdf (text-based)  → pdfplumber parser → FAISS
    - .pdf (image-based) → PaddleOCR background task → ocr_extraction.txt
    - .txt / .text       → pre-extracted OCR text → FAISS
    - .xlsx / .xls       → openpyxl table parser → FAISS
    - .csv               → csv parser → FAISS
    - .docx / .doc       → python-docx table parser → FAISS
    - .pptx / .ppt       → python-pptx table parser → FAISS
    - .png/.jpg/.jpeg/.bmp/.tiff/.gif/.webp → PaddleOCR on image → FAISS
    """
    try:
        # ── Save uploaded file ────────────────────────────────────────────
        file_path = os.path.join(UPLOADS_DIR, file.filename)
        with open(file_path, "wb") as fh:
            content = await file.read()
            fh.write(content)
        print(f"\n[Upload] Saved file: {file.filename}")

        fname_lower = file.filename.lower()
        is_txt = fname_lower.endswith(".txt") or fname_lower.endswith(".text")
        is_pdf = fname_lower.endswith(".pdf")

        # ── .txt: parse as pre-extracted OCR text → FAISS ─────────────────
        if is_txt:
            print("[Upload] Detected OCR text file, using OCR parser…")
            try:
                with open(file_path, "r", encoding="utf-8") as fh:
                    ocr_text = fh.read()
                parts = parse_ocr_bom_text(ocr_text)

                if not parts:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "success": False,
                            "filename": file.filename,
                            "parts_extracted": 0,
                            "message": (
                                "No parts extracted from OCR text. Ensure the file was produced "
                                "by extract_ocr.py and contains recognisable part numbers."
                            ),
                        },
                    )

                total_mfr = sum(len(p.get("manufacturers", [])) for p in parts)
                store = get_faiss_store()
                store.add_parts(parts, source_file=file.filename)
                return {
                    "success": True,
                    "filename": file.filename,
                    "parts_extracted": len(parts),
                    "total_manufacturer_options": total_mfr,
                    "storage": "faiss",
                    "message": (
                        f"Successfully extracted {len(parts)} parts with {total_mfr} "
                        f"manufacturer options from {file.filename}"
                    ),
                }
            except Exception as exc:
                import traceback
                traceback.print_exc()
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "filename": file.filename, "message": str(exc)},
                )

        # ── .pdf: try text-based extraction first (no OCR) ─────────────────
        if is_pdf:
            try:
                parts = parse_bom_document(file_path, use_ocr_fallback=False)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                parts = []

            if parts:
                total_mfr = sum(len(p.get("manufacturers", [])) for p in parts)
                store = get_faiss_store()
                store.add_parts(parts, source_file=file.filename)
                return {
                    "success": True,
                    "filename": file.filename,
                    "parts_extracted": len(parts),
                    "total_manufacturer_options": total_mfr,
                    "storage": "faiss",
                    "message": (
                        f"Successfully extracted {len(parts)} parts with {total_mfr} "
                        f"manufacturer options from {file.filename}"
                    ),
                }

            # No parts from text extraction — check if image-based
            if _pdf_needs_ocr(file_path):
                background_tasks.add_task(_run_ocr_and_store, file_path, file.filename, ocr_dpi)
                return JSONResponse(
                    status_code=202,
                    content={
                        "success": True,
                        "filename": file.filename,
                        "storage": "ocr_extraction",
                        "message": (
                            f"'{file.filename}' is an image-based PDF. "
                            "OCR is running in the background — this usually takes "
                            "1-3 minutes per page. Once complete, the extracted text "
                            "will be searchable via the /query endpoint. "
                            "Check /ocr-status for progress."
                        ),
                    },
                )

            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "filename": file.filename,
                    "parts_extracted": 0,
                    "message": (
                        "No BOM parts found in this PDF. Possible reasons:\n"
                        "1. Non-standard table format (headers not recognised)\n"
                        "2. Complex merged cells or unusual layout\n"
                        "3. Missing required columns (Part Number, Manufacturer, MPN)"
                    ),
                },
            )

        # ── Excel / CSV / Word / PowerPoint / Image ────────────────────────
        file_type = get_file_type(file.filename)
        if file_type is not None:
            try:
                parts = convert_and_parse(file_path, file.filename)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "filename": file.filename,
                        "message": f"Failed to process file: {str(exc)}",
                    },
                )

            # ── Raw-row fallback for Excel/CSV when BOM structure not detected ──
            raw_fallback = False
            if not parts and file_type in ("excel", "csv"):
                print(f"[Upload] Structured BOM parsing returned 0 parts — trying raw-row fallback")
                try:
                    if file_type == "excel":
                        from app.file_converters import parse_excel_raw_rows
                        parts = parse_excel_raw_rows(file_path)
                    elif file_type == "csv":
                        from app.file_converters import _table_to_raw_row_parts
                        import csv as _csv
                        table: list = []
                        with open(file_path, newline="", encoding="utf-8-sig") as fh:
                            sample = fh.read(4096); fh.seek(0)
                            try:
                                import csv as _csv2
                                dialect = _csv2.Sniffer().sniff(sample)
                            except Exception:
                                dialect = _csv.excel
                            for row in _csv.reader(fh, dialect):
                                table.append([c.strip() for c in row])
                        parts = _table_to_raw_row_parts(table, file.filename, 1)
                    raw_fallback = bool(parts)
                except Exception as exc2:
                    import traceback; traceback.print_exc()
                    print(f"[Upload] Raw-row fallback failed: {exc2}")

            if parts:
                total_mfr = sum(len(p.get("manufacturers", [])) for p in parts)
                store = get_faiss_store()
                store.add_parts(parts, source_file=file.filename)
                msg = (
                    f"Indexed {len(parts)} rows from '{file.filename}' as raw text "
                    f"(BOM structure not detected — stored for semantic search)."
                    if raw_fallback else
                    f"Successfully extracted {len(parts)} parts with {total_mfr} "
                    f"manufacturer options from {file.filename}"
                )
                return {
                    "success": True,
                    "filename": file.filename,
                    "parts_extracted": len(parts),
                    "total_manufacturer_options": total_mfr,
                    "storage": "faiss",
                    "raw_row_fallback": raw_fallback,
                    "message": msg,
                }

            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "filename": file.filename,
                    "parts_extracted": 0,
                    "message": (
                        f"No content could be extracted from '{file.filename}'. "
                        "Ensure the file contains a table or data rows."
                    ),
                },
            )

        # ── Unsupported file type ──────────────────────────────────────────
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "filename": file.filename,
                "message": (
                    "Unsupported file type. Supported formats: "
                    ".pdf, .txt, .xlsx, .xls, .csv, .docx, .pptx, "
                    ".png, .jpg, .jpeg, .bmp, .tiff, .gif, .webp"
                ),
            },
        )

    except Exception as exc:
        print(f"[Upload] Unexpected error: {exc}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Upload failed: {str(exc)}"},
        )


@router.post("/ingest-ocr")
async def ingest_ocr_file(request: Request):
    """
    Ingest a pre-extracted OCR text file from disk into FAISS.

    Accepts a JSON body with:
      { "file_path": "ocr_outputs/ERAA24476_ocr.txt" }

    The file must be a .txt file produced by extract_ocr.py.
    Parts are parsed with parse_ocr_bom_text() and stored in FAISS
    in the same format as regular uploads.
    """

    try:
        data = await request.json()
        file_path = data.get("file_path", "").strip()

        if not file_path:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "No file_path provided"}
            )

        # Resolve relative paths against the project root
        if not os.path.isabs(file_path):
            file_path = os.path.join(BASE_DIR, file_path)

        if not os.path.exists(file_path):
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": f"File not found: {file_path}"}
            )

        print(f"\n[IngestOCR] Reading: {file_path}")
        with open(file_path, 'r', encoding='utf-8') as f:
            ocr_text = f.read()

        parts = parse_ocr_bom_text(ocr_text)

        if not parts:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "file_path": file_path,
                    "parts_extracted": 0,
                    "message": (
                        "No parts found in OCR text. "
                        "Ensure the file contains BOM data with recognisable part numbers."
                    )
                }
            )

        # Tag each part with the source file name
        source_name = os.path.basename(file_path)
        total_mfr_options = sum(len(p.get('manufacturers', [])) for p in parts)

        store = get_faiss_store()
        store.add_parts(parts, source_file=source_name)

        print(f"[IngestOCR] Stored {len(parts)} parts from {source_name}")

        return {
            "success": True,
            "file_path": file_path,
            "source_file": source_name,
            "parts_extracted": len(parts),
            "total_manufacturer_options": total_mfr_options,
            "message": (
                f"Successfully ingested {len(parts)} parts with "
                f"{total_mfr_options} manufacturer options from {source_name}"
            )
        }

    except Exception as e:
        print(f"[IngestOCR] Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Ingest failed: {str(e)}"}
        )


@router.post("/query")
async def query_endpoint(request: Request):
    """
    Query using multi-agent system.

    Searches BOTH:
    1. FAISS index (structured, text-based BOM PDFs)
    2. OCR extraction file (image-based PDFs processed in background)

    Results from both sources are merged into one response.
    """
    try:
        data = await request.json()
        query = data.get("query")
        filename_filter = (data.get("filename") or "").strip() or None  # optional BOM file filter

        if not query:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "No query provided"},
            )

        # ── 0. Parse query intent via LLM ────────────────────────────────
        from app.multi_agent_faiss import QueryIntentAgent
        intent = QueryIntentAgent().parse(query)
        count_limit      = intent.get("limit")           # int or None
        specific_parts   = intent.get("specific_parts", [])  # e.g. ["563969-472"]
        mfr_filter       = (intent.get("filters") or {}).get("manufacturer")
        desc_filter      = (intent.get("filters") or {}).get("description_contains")

        # ── 1. FAISS multi-agent search ───────────────────────────────────
        orchestrator = get_orchestrator()
        result = orchestrator.process_query(query)

        # ── 1b. Generic "list all" fallback when filename is provided ─────
        # If a specific file was requested but the query has no extractable part-number
        # tokens (e.g. "get me details of part numbers"), return ALL parts from that file.
        _fallback_handled = False  # prevents double SE call in the filter block below
        if filename_filter and not result.get("parts_found"):
            from app.multi_agent_faiss import SiliconExpertAgent, ResponseFormatterAgent
            store_fb = get_faiss_store()
            all_file_parts = [
                p for p in store_fb.get_all_parts()
                if p.get("source_file") == filename_filter
            ]
            # Also check OCR store for this file
            if not all_file_parts:
                ocr_text_fb = get_ocr_text_for_source(filename_filter)
                if ocr_text_fb:
                    all_file_parts = parse_ocr_bom_text(ocr_text_fb)
                    for p in all_file_parts:
                        p.setdefault("source_document", filename_filter)

            if all_file_parts:
                # ── Apply intent filters from LLM ───────────────────────
                # 1. Specific part numbers requested (e.g. "show 563969-472")
                #    Combine LLM-detected specific_parts with regex-extracted part numbers.
                #    The LLM sometimes misses non-standard part number formats (e.g. 42G5000-0193).
                from app.multi_agent_faiss import PartNumberExtractorAgent as _PNExtractor
                _regex_parts = _PNExtractor().extract(query)
                _all_specific = list({p.upper() for p in (specific_parts + _regex_parts)})
                if _all_specific:
                    matched = [p for p in all_file_parts if p.get("part_number", "").upper() in _all_specific]
                    if matched:
                        all_file_parts = matched
                        print(f"[Query] Intent+regex specific parts filter {_all_specific} → {len(all_file_parts)} part(s)")
                    else:
                        # Part not found in indexed data — clear list so we don't call SE
                        # with unrelated garbage parts and return a wrong manufacturer/MPN.
                        print(f"[Query] Specific part(s) {_all_specific} not found in '{filename_filter}' — clearing results")
                        all_file_parts = []

                # 2. Manufacturer filter (e.g. "from Yageo")
                if all_file_parts and mfr_filter:
                    mfr_lower = mfr_filter.lower()
                    matched = [
                        p for p in all_file_parts
                        if any(mfr_lower in (m.get("manufacturer") or "").lower()
                               for m in p.get("manufacturers", []))
                    ]
                    if matched:
                        all_file_parts = matched
                        print(f"[Query] Intent: manufacturer filter '{mfr_filter}' → {len(all_file_parts)} part(s)")

                # 3. Description keyword filter (e.g. "capacitors")
                if all_file_parts and desc_filter:
                    desc_lower = desc_filter.lower()
                    matched = [p for p in all_file_parts if desc_lower in (p.get("description") or "").lower()]
                    if matched:
                        all_file_parts = matched
                        print(f"[Query] Intent: description filter '{desc_filter}' → {len(all_file_parts)} part(s)")

                # 4. Count limit (e.g. "get me 5 parts")
                if all_file_parts and count_limit and len(all_file_parts) > count_limit:
                    print(f"[Query] Intent: limiting to {count_limit} of {len(all_file_parts)} part(s)")
                    all_file_parts = all_file_parts[:count_limit]

                if not all_file_parts:
                    # Filters eliminated all parts (e.g. specific part not found in OCR data)
                    _part_label = ", ".join(specific_parts) if specific_parts else "requested part(s)"
                    result = {
                        "success": False,
                        "parts_found": [],
                        "api_data": None,
                        "formatted_response": (
                            f"Part '{_part_label}' was not found in the indexed data for "
                            f"'{filename_filter}'.\n\n"
                            f"This usually means the BOM table in this document was not "
                            f"fully extracted by OCR. Try re-uploading the file at higher "
                            f"resolution (use the 'ocr_dpi' parameter set to 300)."
                        ),
                        "excel_data": [],
                        "message": f"Part '{_part_label}' not found in '{filename_filter}'",
                    }
                    _fallback_handled = True
                else:
                    print(f"[Query] Generic query + filename filter → returning {len(all_file_parts)} parts from '{filename_filter}'")
                    _se_fb = SiliconExpertAgent()
                    _api_fb = _se_fb.search_all_manufacturers(all_file_parts)
                    _fmt_fb = ResponseFormatterAgent()
                    result = {
                        "success": True,
                        "parts_found": all_file_parts,
                        "api_data": _api_fb,
                        "formatted_response": _fmt_fb.format(all_file_parts, _api_fb),
                        "excel_data": _fmt_fb.prepare_excel_data(all_file_parts, _api_fb),
                        "message": f"Found {len(all_file_parts)} part(s) in '{filename_filter}'",
                    }
                    _fallback_handled = True

        # Apply source-file filter to FAISS results if requested.
        # Skip when the fallback above already ran SE for this file — avoids a double call.
        if filename_filter and result.get("parts_found") and not _fallback_handled:
            filtered_parts = [
                p for p in result["parts_found"]
                if p.get("source_file") == filename_filter
            ]
            if filtered_parts:
                result["parts_found"] = filtered_parts
                # Re-run SE + formatter with the filtered set
                import json as _json_filter
                from app.multi_agent_faiss import SiliconExpertAgent, ResponseFormatterAgent
                _se = SiliconExpertAgent()
                _api = _se.search_all_manufacturers(filtered_parts)
                _fmt = ResponseFormatterAgent()
                result["api_data"] = _api
                result["formatted_response"] = _fmt.format(filtered_parts, _api)
                result["excel_data"] = _fmt.prepare_excel_data(filtered_parts, _api)
                print(f"[Query] Filtered to {len(filtered_parts)} part(s) from '{filename_filter}'")
            else:
                # No FAISS parts for this file — clear FAISS result, let OCR path handle it
                result["parts_found"] = []
                result["api_data"] = None
                result["excel_data"] = []
                result["formatted_response"] = ""
                result["success"] = False

        # ── 2. OCR extraction file search ─────────────────────────────────
        ocr_matches = search_ocr_extraction(query, max_results=10)
        # Apply filename filter to OCR matches
        if filename_filter:
            ocr_matches = [m for m in ocr_matches if m.get("source") == filename_filter]
        ocr_status = get_ocr_processing_status()
        in_progress = ocr_status.get("in_progress", {})
        completed = ocr_status.get("completed", {})

        if ocr_matches:
            print(f"[Query] Found {len(ocr_matches)} OCR match(es) for query")

            # ── 2a. Try to extract structured parts from OCR for SiliconExpert ──
            # Use OCR results when FAISS either had no API results OR didn't find the
            # actual queried part (e.g. fell back to semantic search with irrelevant results).
            import re as _re
            import json as _json
            from app.multi_agent_faiss import SiliconExpertAgent, ResponseFormatterAgent

            query_upper = query.upper()
            query_tokens = set(_re.findall(r"[A-Z0-9]{4,}", query_upper))

            # Check if FAISS results actually contain any of the queried tokens
            faiss_parts = result.get("parts_found", [])
            faiss_has_match = bool(faiss_parts) and bool(query_tokens) and any(
                any(tok in _json.dumps(p).upper() for tok in query_tokens)
                for p in faiss_parts
            )

            if not result.get("api_data") or not faiss_has_match:
                if not faiss_has_match and faiss_parts:
                    print("[Query] FAISS returned unrelated parts (no query token match); using OCR results instead")

                # Parse OCR text for each matched source into structured parts
                matched_sources = list({m["source"] for m in ocr_matches})
                # If a filename filter is active, restrict to that source only
                if filename_filter:
                    matched_sources = [s for s in matched_sources if s == filename_filter]
                ocr_parsed_parts = []

                for source in matched_sources:
                    ocr_full_text = get_ocr_text_for_source(source)
                    if ocr_full_text:
                        parsed = parse_ocr_bom_text(ocr_full_text)
                        print(f"[Query] OCR parsed {len(parsed)} parts from '{source}'")
                        for part in parsed:
                            # Keep parts whose BOM data contains any query token
                            part_str = _json.dumps(part).upper()
                            if not query_tokens or any(tok in part_str for tok in query_tokens):
                                # Tag with source so response formatter can show it
                                part.setdefault("source_document", source)
                                ocr_parsed_parts.append(part)

                # Deduplicate by part_number — keep the entry with the most manufacturers
                seen_pn: dict = {}
                for part in ocr_parsed_parts:
                    pn = part.get("part_number", "")
                    if pn not in seen_pn:
                        seen_pn[pn] = part
                    else:
                        # Prefer the entry with more manufacturer data
                        existing = seen_pn[pn]
                        if len(part.get("manufacturers", [])) > len(existing.get("manufacturers", [])):
                            seen_pn[pn] = part
                ocr_parsed_parts = list(seen_pn.values())
                print(f"[Query] After dedup: {len(ocr_parsed_parts)} unique OCR part(s)")

                if ocr_parsed_parts:
                    print(f"[Query] Calling SiliconExpert with {len(ocr_parsed_parts)} OCR-parsed part(s)")
                    se_agent = SiliconExpertAgent()
                    se_data = se_agent.search_all_manufacturers(ocr_parsed_parts)
                    if se_data:
                        result["api_data"] = se_data
                        result["parts_found"] = ocr_parsed_parts
                        # Regenerate formatted response with SE results
                        formatter = ResponseFormatterAgent()
                        result["formatted_response"] = formatter.format(ocr_parsed_parts, se_data)
                        result["excel_data"] = formatter.prepare_excel_data(ocr_parsed_parts, se_data)
                        print(f"[Query] SiliconExpert returned results for OCR parts")
                    else:
                        print(f"[Query] SiliconExpert returned no results for OCR parts")

            # Build a readable OCR section to append to the formatted response
            ocr_lines = [
                "",
                "=" * 70,
                "📄 OCR EXTRACTED DATA — Matching Lines",
                "=" * 70,
                "",
            ]
            seen_contexts: set = set()
            for match in ocr_matches:
                ctx_key = match["context"][:80]  # deduplicate near-identical hits
                if ctx_key in seen_contexts:
                    continue
                seen_contexts.add(ctx_key)

                ocr_lines.append(f"  Source : {match['source']}")
                ocr_lines.append(f"  Matched: {', '.join(match['matched_keywords'])}")
                ocr_lines.append("  Context:")
                for ctx_line in match["context"].splitlines():
                    ocr_lines.append(f"    {ctx_line}")
                ocr_lines.append("  " + "-" * 50)

            ocr_section = "\n".join(ocr_lines)

            # Attach OCR data to result
            result["ocr_matches"] = ocr_matches
            result["ocr_match_count"] = len(ocr_matches)

            if result.get("formatted_response"):
                result["formatted_response"] += ocr_section
            else:
                result["formatted_response"] = ocr_section

            # If FAISS had no results, mark the overall query as successful
            if not result.get("success"):
                result["success"] = True
                result["message"] = (
                    f"No FAISS results, but found {len(ocr_matches)} match(es) "
                    "in OCR extraction store."
                )
        else:
            result["ocr_matches"] = []
            result["ocr_match_count"] = 0

            # Give the user helpful context about why OCR data wasn't found
            status_notes = []
            if in_progress:
                files = ", ".join(in_progress.keys())
                status_notes.append(
                    f"⏳ OCR still processing: {files}. "
                    "Please wait and query again once it completes."
                )
            if completed:
                done_files = [f for f, v in completed.items() if not v.get("error")]
                failed_files = [f for f, v in completed.items() if v.get("error")]
                if done_files:
                    status_notes.append(
                        f"✓ OCR completed for: {', '.join(done_files)} "
                        "— no keyword match found for your query. "
                        "Try querying with a specific part number (e.g. ERAA24476)."
                    )
                if failed_files:
                    status_notes.append(
                        f"✗ OCR failed for: {', '.join(failed_files)}. "
                        "Check /ocr-status for details."
                    )

            if status_notes:
                note_block = "\n\n" + "\n".join(status_notes)
                if result.get("formatted_response"):
                    result["formatted_response"] += note_block
                else:
                    result["formatted_response"] = note_block
                # If FAISS had nothing either, still surface the note
                if not result.get("success"):
                    result["ocr_status_note"] = status_notes

        # ── Final step: LLM response review via gpt-oss ──────────────────
        # Skip when:
        #  - no data or failed query
        #  - want_all with no real filter (no-op)
        #  - specific_parts query: upstream already filtered to exactly those parts;
        #    reviewer can't see the internal BOM number → would wrongly return 0 rows
        #  - single-row result: trivially correct, no filtering needed
        want_all = intent.get("want_all", False)
        has_real_filter = bool(
            mfr_filter or desc_filter or count_limit
            or any(w in query.lower() for w in [
                "eol", "yeol", "lifecycle", "rohs", "end-of-life",
                "discontinued", "active", "last time buy", "ltb"
            ])
        )
        excel_rows = result.get("excel_data") or []
        skip_review = (
            not excel_rows
            or not result.get("success")
            or (want_all and not has_real_filter)
            or bool(specific_parts)          # internal BOM# ≠ MPN in table, would mismatch
            or len(excel_rows) == 1          # trivially correct, no filtering needed
            or len(excel_rows) > 200         # too large for LLM review — would take minutes
        )
        if not skip_review:
            from app.multi_agent_faiss import ResponseReviewerAgent
            reviewed = ResponseReviewerAgent().review(
                query=query,
                excel_data=excel_rows,
                parts_found=result.get("parts_found", []),
            )
            if reviewed.get("reviewed"):
                filtered_excel = reviewed["excel_data"]
                result["excel_data"]  = filtered_excel
                result["parts_found"] = reviewed["parts_found"]

                if filtered_excel:
                    # Build formatted_response directly from filtered excel rows
                    lines = [f"Found {len(filtered_excel)} matching row(s) for your query:"]
                    for _row in filtered_excel:
                        _bom    = _row.get('BOM No', '?')
                        _parent = _row.get("Parent Part Number", "")
                        _libref = _row.get("LibRef", "")
                        _part   = _row.get("Requested Part", "")
                        _mpn    = _row.get("Manufacturer Part Number", "") or _part
                        _mfr    = _row.get("Manufacturer Name", "")
                        _desc   = _row.get("Description", "")
                        _yeol   = _row.get("YEOL", "")
                        _eol    = _row.get("EOL", "")
                        _rohs   = _row.get("RoHS", "")
                        _ds     = _row.get("Datasheet", "")
                        _plnm   = _row.get("PlName", "")
                        lines.append(f"\n  ─── BOM# {_bom} ───────────────────────────────")
                        if _parent and _parent != _mpn:
                            lines.append(f"  Parent Part    : {_parent}")
                        if _libref:
                            lines.append(f"  LibRef         : {_libref}")
                        lines.append(f"  Requested Part : {_part}")
                        lines.append(f"  MPN            : {_mpn}")
                        lines.append(f"  Manufacturer   : {_mfr}")
                        if _plnm:
                            lines.append(f"  Category       : {_plnm}")
                        if _desc:
                            lines.append(f"  Description    : {_desc}")
                        lines.append(f"  Lifecycle      : {_eol}")
                        lines.append(f"  RoHS           : {_rohs}")
                        lines.append(f"  Years to EOL   : {_yeol if _yeol else 'Unknown'}")
                        if _ds:
                            lines.append(f"  Datasheet      : {_ds}")
                    if reviewed.get("explanation"):
                        lines.append(f"\n\U0001f4dd {reviewed['explanation']}")
                    result["formatted_response"] = "\n".join(lines)
                else:
                    result["formatted_response"] = (
                        "No parts matched your query.\n\n"
                        f"\U0001f4dd {reviewed.get('explanation', 'No rows satisfy the filter.')}"
                    )

                result["message"] = (
                    f"LLM review: {len(filtered_excel)} relevant row(s) "
                    f"from {len(excel_rows)} total"
                )
                print(f"[Query] Review complete: {len(filtered_excel)} / {len(excel_rows)} rows kept")

        # ── Build rich formatted_response when reviewer was skipped ──────
        # (specific-part queries, single-row results, want_all with no filter)
        elif skip_review and excel_rows and result.get("success"):
            lines = [f"Found {len(excel_rows)} matching row(s) for your query:"]
            for _row in excel_rows:
                _bom    = _row.get("BOM No", "?")
                _parent = _row.get("Parent Part Number", "")
                _libref = _row.get("LibRef", "")
                _part   = _row.get("Requested Part", "")
                _mpn    = _row.get("Manufacturer Part Number", "") or _part
                _mfr    = _row.get("Manufacturer Name", "")
                _desc   = _row.get("Description", "")
                _yeol   = _row.get("YEOL", "")
                _eol    = _row.get("EOL", "")
                _rohs   = _row.get("RoHS", "")
                _ds     = _row.get("Datasheet", "")
                _plnm   = _row.get("PlName", "")
                lines.append(f"\n  ─── BOM# {_bom} ───────────────────────────────")
                if _parent and _parent != _mpn:
                    lines.append(f"  Parent Part    : {_parent}")
                if _libref:
                    lines.append(f"  LibRef         : {_libref}")
                lines.append(f"  Requested Part : {_part}")
                if _mpn != _part:
                    lines.append(f"  MPN            : {_mpn}")
                lines.append(f"  Manufacturer   : {_mfr}")
                if _plnm:
                    lines.append(f"  Category       : {_plnm}")
                if _desc:
                    lines.append(f"  Description    : {_desc}")
                lines.append(f"  Lifecycle      : {_eol}")
                lines.append(f"  RoHS           : {_rohs}")
                lines.append(f"  Years to EOL   : {_yeol if _yeol else 'Unknown'}")
                if _ds:
                    lines.append(f"  Datasheet      : {_ds}")
            result["formatted_response"] = "\n".join(lines)

        return result

    except Exception as exc:
        print(f"[Query] Error: {exc}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"Query failed: {str(exc)}"},
        )


@router.get("/stats")
async def get_stats():
    """Get FAISS index statistics"""
    
    try:
        store = get_faiss_store()
        stats = store.get_stats()
        return stats
    
    except Exception as e:
        print(f"[Stats] Error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Failed to get stats: {str(e)}"
            }
        )


@router.get("/ocr-status")
async def ocr_status():
    """
    Return the current state of the OCR extraction store.

    Shows which image-based PDFs have been processed and how much
    text has been accumulated so far.
    """
    try:
        stats = get_ocr_store_stats()
        return stats
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to read OCR store: {str(exc)}"},
        )


@router.get("/ocr-status/{filename}")
async def ocr_status_for_file(filename: str):
    """
    Return OCR status for a specific file.
    Returns 200 with status=in_progress|complete|failed, or 404 if not tracked.
    """
    try:
        from app.ocr_store import get_ocr_processing_status
        data = get_ocr_processing_status()
        in_progress = data.get("in_progress", {})
        completed   = data.get("completed",   {})

        if filename in in_progress:
            entry = in_progress[filename]
            return {
                "filename": filename,
                "status": "in_progress",
                "started": entry.get("started"),
                "page": entry.get("page"),
                "total_pages": entry.get("total_pages"),
            }
        elif filename in completed:
            entry = completed[filename]
            if entry.get("error"):
                return {
                    "filename": filename,
                    "status": "failed",
                    "reason": entry.get("error"),
                    "started": entry.get("started"),
                }
            else:
                return {
                    "filename": filename,
                    "status": "complete",
                    "started": entry.get("started"),
                    "completed": entry.get("finished"),
                    "char_count": entry.get("chars", 0),
                }
        else:
            return JSONResponse(status_code=404, content={"filename": filename, "status": "not_found"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/files")
async def list_uploaded_files():
    """
    Return a list of all uploaded BOM files available to query.
    Combines:
    - Files in the uploads/ directory
    - Sources recorded in the OCR extraction store
    """
    try:
        # Sources in the OCR store
        ocr_sources = set(list_ocr_sources())

        # Sources in FAISS metadata
        store = get_faiss_store()
        faiss_sources = set(
            p.get("source_file", "")
            for p in store.get_all_parts()
            if p.get("source_file")
        )

        # Only show files that are actually indexed/searchable.
        # Raw disk files that haven't been indexed are excluded — they can't be queried.
        all_files = sorted(ocr_sources | faiss_sources)

        # Enrich with status info
        ocr_status_data = get_ocr_processing_status()
        in_progress = ocr_status_data.get("in_progress", {})
        completed = ocr_status_data.get("completed", {})

        file_list = []
        for fname in all_files:
            if fname in in_progress:
                status = "ocr_processing"
            elif fname in completed:
                status = "ocr_complete" if not completed[fname].get("error") else "ocr_failed"
            elif fname in faiss_sources:
                status = "indexed"
            else:
                status = "uploaded"
            file_list.append({"filename": fname, "status": status})

        return {"files": file_list, "total": len(file_list)}

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to list files: {str(exc)}"},
        )


@router.post("/reprocess-ocr")
async def reprocess_ocr(background_tasks: BackgroundTasks, request: Request):
    """
    Re-trigger OCR for a file that is stuck, failed, or was interrupted
    by a server restart.

    Body: { "filename": "ERAA24476.pdf" }

    The file must already exist in the uploads directory.
    """
    try:
        data = await request.json()
        filename = data.get("filename", "").strip()
        if not filename:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "No filename provided"},
            )

        file_path = os.path.join(UPLOADS_DIR, filename)
        if not os.path.exists(file_path):
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": f"'{filename}' not found in uploads directory. Please re-upload the file.",
                },
            )

        background_tasks.add_task(_run_ocr_and_store, file_path, filename)
        return {
            "success": True,
            "filename": filename,
            "message": (
                f"OCR restarted for '{filename}'. "
                "Check /ocr-status for progress."
            ),
        }

    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(exc)},
        )


@router.post("/clear")
async def clear_index():
    """Clear FAISS index"""
    
    try:
        store = get_faiss_store()
        store.clear()
        
        return {
            "success": True,
            "message": "FAISS index cleared successfully"
        }
    
    except Exception as e:
        print(f"[Clear] Error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"Failed to clear index: {str(e)}"
            }
        )


@router.get("/parts")
async def get_all_parts(filename: str = None):
    """
    Get parts from FAISS index and/or OCR store.

    Optional query param:
      ?filename=ERAA24476.pdf  → return only parts from that file
    """
    try:
        store = get_faiss_store()
        all_parts = store.get_all_parts()

        if filename:
            # ── FAISS parts for this file ─────────────────────────────────
            faiss_parts = [
                {"part_number": p.get("part_number", ""), "source": "faiss"}
                for p in all_parts
                if p.get("source_file") == filename and p.get("part_number")
            ]

            # ── OCR-parsed parts for this file ────────────────────────────
            ocr_parts = []
            ocr_text = get_ocr_text_for_source(filename)
            if ocr_text:
                parsed = parse_ocr_bom_text(ocr_text)
                ocr_parts = [
                    {"part_number": p.get("part_number", ""), "source": "ocr"}
                    for p in parsed
                    if p.get("part_number")
                ]

            # Merge, deduplicate — FAISS wins on duplicates
            seen = set()
            combined = []
            for p in faiss_parts + ocr_parts:
                pn = p["part_number"].upper()
                if pn not in seen:
                    seen.add(pn)
                    combined.append(p)

            combined.sort(key=lambda x: x["part_number"])
            return {"filename": filename, "total": len(combined), "parts": combined}

        # No filter — return all (capped for performance)
        summary = [
            {"part_number": p.get("part_number", ""), "source_file": p.get("source_file", ""), "source": "faiss"}
            for p in all_parts
            if p.get("part_number")
        ]
        summary.sort(key=lambda x: (x["source_file"], x["part_number"]))
        return {"total": len(summary), "parts": summary[:200]}

    except Exception as e:
        print(f"[Get Parts] Error: {e}")
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": f"Failed to get parts: {str(e)}"})


@router.post("/search")
async def semantic_search(request: Request):
    """Direct FAISS semantic search (for testing)"""
    
    try:
        data = await request.json()
        query = data.get("query")
        top_k = data.get("top_k", 5)
        
        if not query:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "No query provided"
                }
            )
        
        store = get_faiss_store()
        results = store.search(query, top_k=top_k)
        
        return {
            "query": query,
            "results": results
        }
    
    except Exception as e:
        print(f"[Search] Error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Search failed: {str(e)}"
            }
        )


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    
    try:
        store = get_faiss_store()
        stats = store.get_stats()
        
        return {
            "status": "ok",
            "version": "3.0-faiss-multi-agent",
            "index_status": {
                "total_parts": stats.get("total_parts"),
                "total_vectors": stats.get("total_vectors")
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/reindex")
async def reindex_all_documents(background_tasks: BackgroundTasks):
    """
    Reindex all uploaded documents - Rebuild FAISS index from scratch

    Process:
    1. Find all PDF files in uploads directory
    2. Clear ALL stored data: OCR extraction, OCR status, and FAISS index
    3. Re-parse text-based documents and add to FAISS
    4. Trigger background OCR for image-based PDFs (processed fresh)
    5. Return detailed logs of what was indexed
    """
    
    try:
        start_time = datetime.now()
        logs = []
        
        # Step 1: Find all uploaded documents
        logs.append("=" * 70)
        logs.append("REINDEXING PROCESS STARTED")
        logs.append(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logs.append("=" * 70)
        
        pdf_files = glob.glob(os.path.join(UPLOADS_DIR, "*.pdf"))
        txt_files = glob.glob(os.path.join(UPLOADS_DIR, "*.txt"))
        all_files = pdf_files + txt_files

        if not all_files:
            logs.append("\n⚠️  No documents found in uploads directory")
            return {
                "success": False,
                "message": "No documents to reindex",
                "logs": logs
            }

        logs.append(f"\n📄 Found {len(pdf_files)} PDF + {len(txt_files)} OCR text file(s) to reindex:")
        for f in all_files:
            logs.append(f"   • {os.path.basename(f)}")
        
        # Step 2: Clear ALL stored data (OCR + FAISS)
        logs.append("\n🗑️  CLEARING ALL STORED DATA")
        logs.append("-" * 70)

        # 2a: Clear OCR store (extraction text + status)
        ocr_clear = clear_ocr_store()
        if ocr_clear["extraction_cleared"]:
            logs.append("   ✓ Cleared OCR extraction file (ocr_outputs/ocr_extraction.txt)")
        else:
            logs.append("   ℹ️  OCR extraction file was already empty")
        if ocr_clear["status_cleared"]:
            logs.append("   ✓ Cleared OCR status file (ocr_outputs/ocr_status.json)")
        else:
            logs.append("   ℹ️  OCR status file was already empty")

        # 2b: Clear FAISS index
        store = get_faiss_store()
        old_stats = store.get_stats()

        logs.append(f"   Old FAISS index contained:")
        logs.append(f"   • {old_stats.get('total_parts', 0)} parts")
        logs.append(f"   • {old_stats.get('total_vectors', 0)} vectors")
        logs.append(f"   • {old_stats.get('total_manufacturer_options', 0)} manufacturer options")

        index_path = os.path.join(BASE_DIR, "index-faiss-store", "parts.index")
        metadata_path = os.path.join(BASE_DIR, "index-faiss-store", "metadata.pkl")
        json_path = os.path.join(BASE_DIR, "index-faiss-store", "parts_readable.json")

        deleted_files = []
        for fpath, fname in [(index_path, "parts.index"), (metadata_path, "metadata.pkl"), (json_path, "parts_readable.json")]:
            if os.path.exists(fpath):
                os.remove(fpath)
                deleted_files.append(fname)

        if deleted_files:
            logs.append(f"   ✓ Deleted FAISS files: {', '.join(deleted_files)}")

        store.clear()
        logs.append("   ✓ FAISS index cleared successfully")
        
        # Step 3: Re-parse all documents
        logs.append("\n📋 PARSING DOCUMENTS")
        logs.append("-" * 70)
        
        all_parts = []
        parsing_summary = []
        
        for file_path in all_files:
            filename = os.path.basename(file_path)
            logs.append(f"\n   Processing: {filename}")
            
            try:
                # Choose parser based on extension
                if filename.lower().endswith('.txt'):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        ocr_text = f.read()
                    parts = parse_ocr_bom_text(ocr_text)
                else:
                    # Try text-based extraction first (same as upload flow)
                    parts = parse_bom_document(file_path, use_ocr_fallback=False)

                    if not parts:
                        # OCR store was wiped — re-trigger OCR for image-based PDFs
                        if _pdf_needs_ocr(file_path):
                            logs.append(f"   [OCR] Image-based PDF detected — OCR queued as background task")
                            background_tasks.add_task(_run_ocr_and_store, file_path, filename)
                            parsing_summary.append({
                                "filename": filename,
                                "parts_extracted": 0,
                                "manufacturers_extracted": 0,
                                "status": "ocr_queued"
                            })
                            continue
                
                if parts:
                    part_count = len(parts)
                    mfr_count = sum(len(p.get('manufacturers', [])) for p in parts)
                    
                    logs.append(f"   ✓ Extracted {part_count} part(s) with {mfr_count} manufacturer option(s)")
                    
                    # Log sample parts
                    sample_size = min(3, len(parts))
                    logs.append(f"   Sample parts:")
                    for i, part in enumerate(parts[:sample_size], 1):
                        part_num = part.get('part_number', 'Unknown')
                        num_mfrs = len(part.get('manufacturers', []))
                        logs.append(f"      {i}. {part_num} ({num_mfrs} manufacturer(s))")
                        
                        # Show manufacturers for first part
                        if i == 1:
                            for j, mfr in enumerate(part.get('manufacturers', [])[:4], 1):
                                logs.append(f"         {j}. {mfr.get('manufacturer')} - {mfr.get('mpn')}")
                    
                    # Add to collection
                    for part in parts:
                        part['source_file'] = filename
                    all_parts.extend(parts)
                    
                    parsing_summary.append({
                        "filename": filename,
                        "parts_extracted": part_count,
                        "manufacturers_extracted": mfr_count,
                        "status": "success"
                    })
                else:
                    logs.append(f"   ⚠️  No parts extracted from {filename}")
                    parsing_summary.append({
                        "filename": filename,
                        "parts_extracted": 0,
                        "manufacturers_extracted": 0,
                        "status": "no_parts"
                    })
            
            except Exception as parse_error:
                logs.append(f"   ✗ Error parsing {filename}: {str(parse_error)}")
                parsing_summary.append({
                    "filename": filename,
                    "parts_extracted": 0,
                    "manufacturers_extracted": 0,
                    "status": "error",
                    "error": str(parse_error)
                })
        
        # Step 4: Rebuild FAISS index
        if all_parts:
            logs.append(f"\n🔨 REBUILDING FAISS INDEX")
            logs.append("-" * 70)
            logs.append(f"   Total parts to index: {len(all_parts)}")
            logs.append(f"   Total manufacturer options: {sum(len(p.get('manufacturers', [])) for p in all_parts)}")
            logs.append("")
            logs.append("   Creating embeddings and indexing...")
            
            # Add all parts to FAISS (this will create embeddings)
            store.add_parts(all_parts)
            
            logs.append("   ✓ FAISS index rebuilt successfully")
        else:
            logs.append("\n⚠️  No parts to index")
        
        # Step 5: Final statistics
        logs.append("\n📊 FINAL STATISTICS")
        logs.append("-" * 70)
        
        new_stats = store.get_stats()
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        logs.append(f"   Total parts indexed: {new_stats.get('total_parts', 0)}")
        logs.append(f"   Total vectors: {new_stats.get('total_vectors', 0)}")
        logs.append(f"   Total manufacturer options: {new_stats.get('total_manufacturer_options', 0)}")
        logs.append(f"   Unique manufacturers: {new_stats.get('unique_manufacturers', 0)}")
        logs.append(f"   Parts with multiple manufacturers: {new_stats.get('parts_with_multiple_manufacturers', 0)}")
        logs.append("")
        logs.append(f"   Duration: {duration:.2f} seconds")
        logs.append(f"   End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        logs.append("\n" + "=" * 70)
        logs.append("✅ REINDEXING COMPLETED SUCCESSFULLY")
        logs.append("=" * 70)
        
        # Return detailed response
        return {
            "success": True,
            "message": f"Successfully reindexed {len(pdf_files)} document(s)",
            "summary": {
                "documents_processed": len(pdf_files),
                "total_parts_indexed": new_stats.get('total_parts', 0),
                "total_manufacturer_options": new_stats.get('total_manufacturer_options', 0),
                "duration_seconds": duration,
                "parsing_details": parsing_summary
            },
            "statistics": new_stats,
            "logs": logs
        }
    
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        
        logs.append("\n" + "=" * 70)
        logs.append("❌ REINDEXING FAILED")
        logs.append("=" * 70)
        logs.append(f"Error: {str(e)}")
        logs.append(f"\nTraceback:\n{error_trace}")
        
        print(f"[Reindex] Error: {e}")
        print(error_trace)
        
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"Reindexing failed: {str(e)}",
                "logs": logs
            }
        )
