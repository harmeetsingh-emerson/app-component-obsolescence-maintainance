"""
OCR Extraction Store

Instead of embedding OCR-based PDFs into FAISS (slow, lossy), the raw
OCR text is appended to a single flat file: ocr_outputs/ocr_extraction.txt

At query time the file is scanned for keyword / part-number matches and
those matches are surfaced alongside FAISS results.
"""

import json
import os
import re
from datetime import datetime
from typing import List, Dict

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
OCR_OUTPUTS_DIR = os.path.join(BASE_DIR, "ocr_outputs")
OCR_EXTRACTION_FILE = os.path.join(OCR_OUTPUTS_DIR, "ocr_extraction.txt")
OCR_STATUS_FILE = os.path.join(OCR_OUTPUTS_DIR, "ocr_status.json")

os.makedirs(OCR_OUTPUTS_DIR, exist_ok=True)

# Delimiter written around every document section
_SECTION_DELIM = "=" * 70


# ---------------------------------------------------------------------------
# STATUS TRACKING
# ---------------------------------------------------------------------------

def _load_status() -> Dict:
    """Load the status JSON file; return empty structure if missing."""
    if not os.path.exists(OCR_STATUS_FILE):
        return {"in_progress": {}, "completed": {}}
    try:
        with open(OCR_STATUS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"in_progress": {}, "completed": {}}


def _save_status(data: Dict) -> None:
    with open(OCR_STATUS_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def mark_ocr_started(filename: str) -> None:
    """Record that OCR has started for *filename*."""
    data = _load_status()
    data["in_progress"][filename] = {
        "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "page": 0,
        "total_pages": 0,
    }
    # Remove from completed if it was there (re-upload)
    data["completed"].pop(filename, None)
    _save_status(data)
    print(f"[OCRStore] Status: started   '{filename}'")


def mark_ocr_complete(filename: str, char_count: int) -> None:
    """Record that OCR completed successfully for *filename*."""
    data = _load_status()
    started = data["in_progress"].pop(filename, {}).get("started", "unknown")
    data["completed"][filename] = {
        "started": started,
        "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chars": char_count,
    }
    _save_status(data)
    print(f"[OCRStore] Status: completed '{filename}' ({char_count} chars)")


def mark_ocr_failed(filename: str, error: str) -> None:
    """Record that OCR failed for *filename*."""
    data = _load_status()
    started = data["in_progress"].pop(filename, {}).get("started", "unknown")
    data["completed"][filename] = {
        "started": started,
        "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chars": 0,
        "error": error,
    }
    _save_status(data)
    print(f"[OCRStore] Status: failed    '{filename}' — {error}")


def update_ocr_page_progress(filename: str, page: int, total_pages: int) -> None:
    """Update the current page being processed (for progress display)."""
    data = _load_status()
    if filename in data["in_progress"]:
        data["in_progress"][filename]["page"] = page
        data["in_progress"][filename]["total_pages"] = total_pages
        _save_status(data)


def clear_stale_in_progress() -> List[str]:
    """
    On server startup, any file left in 'in_progress' was killed mid-task
    (server restart, crash).  Move them to 'completed' with an error flag
    so the user knows to re-upload.
    Returns list of filenames that were cleared.
    """
    data = _load_status()
    stale = list(data["in_progress"].keys())
    for filename in stale:
        started = data["in_progress"].pop(filename, {}).get("started", "unknown")
        data["completed"][filename] = {
            "started": started,
            "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "chars": 0,
            "error": "Server was restarted before OCR completed. Please re-upload to process.",
        }
        print(f"[OCRStore] Cleared stale in-progress entry: '{filename}'")
    if stale:
        _save_status(data)
    return stale


def get_ocr_processing_status() -> Dict:
    """Return which files are currently processing and which are done."""
    return _load_status()


def append_ocr_extraction(filename: str, ocr_text: str) -> str:
    """
    Append OCR text from one document to the common extraction store.

    Each entry is wrapped with a header so the file can be split back
    into per-document sections later.

    Returns:
        Absolute path of the extraction file.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"\n{_SECTION_DELIM}\n"
        f"SOURCE: {filename}\n"
        f"EXTRACTED: {timestamp}\n"
        f"{_SECTION_DELIM}\n"
        f"{ocr_text}\n"
    )
    with open(OCR_EXTRACTION_FILE, "a", encoding="utf-8") as fh:
        fh.write(entry)

    print(f"[OCRStore] Appended OCR text for '{filename}' → {OCR_EXTRACTION_FILE}")
    return OCR_EXTRACTION_FILE


def search_ocr_extraction(query: str, max_results: int = 10) -> List[Dict]:
    """
    Keyword-search the OCR extraction file.

    Splits the file into per-document sections, then scans every line
    for query tokens (extracted as 4+ char alphanumeric sequences).

    Args:
        query:       User query string.
        max_results: Maximum number of matching context blocks returned.

    Returns:
        List of dicts with keys: source, matched_keywords, context, line
    """
    if not os.path.exists(OCR_EXTRACTION_FILE):
        return []

    # Build keyword set from query — favour part-number-like tokens
    query_upper = query.upper()
    keywords = set(re.findall(r'[A-Z0-9]{4,}', query_upper))
    if not keywords:
        return []

    results: List[Dict] = []
    current_source = "unknown"

    with open(OCR_EXTRACTION_FILE, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    for i, raw_line in enumerate(lines):
        # Track which document we are in
        stripped = raw_line.strip()
        if stripped.startswith("SOURCE:"):
            current_source = stripped[len("SOURCE:"):].strip()
            continue
        if stripped.startswith("EXTRACTED:") or stripped == _SECTION_DELIM:
            continue

        line_upper = stripped.upper()
        matched = [kw for kw in keywords if kw in line_upper]
        if not matched:
            continue

        # Collect a small context window around the matching line
        ctx_start = max(0, i - 2)
        ctx_end = min(len(lines), i + 4)
        context = "".join(lines[ctx_start:ctx_end]).strip()

        results.append(
            {
                "source": current_source,
                "matched_keywords": matched,
                "context": context,
                "line": stripped,
            }
        )

        if len(results) >= max_results:
            break

    return results


def get_ocr_text_for_source(filename: str) -> str:
    """Return the full OCR text body for *filename*, or '' if not found."""
    if not os.path.exists(OCR_EXTRACTION_FILE):
        return ""

    text_lines = []
    in_section = False

    with open(OCR_EXTRACTION_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("SOURCE:"):
                src = stripped[len("SOURCE:"):].strip()
                in_section = (src == filename)
                continue
            if stripped.startswith("EXTRACTED:") or stripped == _SECTION_DELIM:
                continue
            if in_section:
                text_lines.append(line)

    return "".join(text_lines)


def list_ocr_sources() -> List[str]:
    """Return the filenames that have been appended to the extraction store."""
    if not os.path.exists(OCR_EXTRACTION_FILE):
        return []

    sources = []
    with open(OCR_EXTRACTION_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("SOURCE:"):
                src = stripped[len("SOURCE:"):].strip()
                if src not in sources:
                    sources.append(src)
    return sources


def clear_ocr_store() -> Dict:
    """
    Delete all OCR stored data — both the extraction text file and the
    status tracking file.  Call this before a full reindex so that OCR
    is re-run from scratch for every document.

    Returns:
        Dict with keys 'extraction_cleared' and 'status_cleared' (bool).
    """
    result = {"extraction_cleared": False, "status_cleared": False}

    if os.path.exists(OCR_EXTRACTION_FILE):
        os.remove(OCR_EXTRACTION_FILE)
        result["extraction_cleared"] = True
        print(f"[OCRStore] Cleared extraction file: {OCR_EXTRACTION_FILE}")

    if os.path.exists(OCR_STATUS_FILE):
        os.remove(OCR_STATUS_FILE)
        result["status_cleared"] = True
        print(f"[OCRStore] Cleared status file: {OCR_STATUS_FILE}")

    return result


def get_ocr_store_stats() -> Dict:
    """Return basic stats about the OCR extraction store."""
    status = get_ocr_processing_status()
    if not os.path.exists(OCR_EXTRACTION_FILE):
        return {
            "exists": False,
            "sources": [],
            "total_chars": 0,
            "in_progress": status.get("in_progress", {}),
            "completed": status.get("completed", {}),
        }

    size = os.path.getsize(OCR_EXTRACTION_FILE)
    sources = list_ocr_sources()
    return {
        "exists": True,
        "file": OCR_EXTRACTION_FILE,
        "sources": sources,
        "total_documents": len(sources),
        "total_chars": size,
        "in_progress": status.get("in_progress", {}),
        "completed": status.get("completed", {}),
    }
