"""
FAISS Multi-Agent Main Entry Point

Features:
- FAISS vector embeddings for semantic search
- Multi-agent system for query processing
- Extracts ALL manufacturers and MPNs (not just primary)
- Calls SiliconExpert API with all manufacturer options
"""

import os

# Fix Windows OpenMP conflict: PaddleOCR (libiomp5md.dll) vs numpy/scipy (libomp140.dll).
# Must be set BEFORE any paddle/numpy import.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.routes_faiss import router

app = FastAPI(
    title="FAISS Multi-Agent BOM Query System",
    description="Advanced BOM query system with FAISS embeddings, multi-agent processing, and comprehensive manufacturer data",
    version="3.0-faiss-multi-agent"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)

# ── Frontend serving ──────────────────────────────────────────────────────────
# Production: serve the React build that was baked into the image by the
#             multi-stage Dockerfile.prod (frontend/build/ directory).
# Development: fall back to the legacy ui.html when no React build exists.

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_REACT_BUILD_DIR = os.path.join(_BASE_DIR, "frontend", "build")

if os.path.isdir(_REACT_BUILD_DIR):
    # Mount React's hashed static assets (JS, CSS, media)
    _static_dir = os.path.join(_REACT_BUILD_DIR, "static")
    if os.path.isdir(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="react-static")

    @app.get("/")
    @app.get("/ui")
    def get_ui():
        return FileResponse(os.path.join(_REACT_BUILD_DIR, "index.html"))

    @app.get("/{full_path:path}")
    def serve_react_app(full_path: str):
        """Serve individual build assets (manifest.json, favicon, etc.) or
        fall back to index.html so React Router can handle the path."""
        candidate = os.path.join(_REACT_BUILD_DIR, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_REACT_BUILD_DIR, "index.html"))

else:
    # Development fallback — serve the standalone ui.html
    @app.get("/")
    @app.get("/ui")
    def get_ui():
        ui_path = os.path.join(_BASE_DIR, "frontend", "ui.html")
        return FileResponse(ui_path, media_type="text/html")


@app.on_event("startup")
async def startup_event():
    """Initialize FAISS store on startup"""
    print("\n" + "="*60)
    print("FAISS Multi-Agent BOM Query System")
    print("Version: 3.0")
    print("="*60)
    print("\nFeatures:")
    print("  [OK] FAISS vector embeddings for semantic search")
    print("  [OK] Multi-agent query processing")
    print("  [OK] Extracts ALL manufacturers (not just primary)")
    print("  [OK] API calls with all manufacturer-MPN pairs")
    print("\n" + "="*60 + "\n")

    # Clear any OCR tasks that were in-progress when the server last stopped
    from app.ocr_store import clear_stale_in_progress
    stale = clear_stale_in_progress()
    if stale:
        print(f"[Startup] Cleared {len(stale)} stale OCR task(s): {stale}")
        print("[Startup] These files need to be re-uploaded or call POST /reprocess-ocr")

    # Initialize FAISS store
    from app.faiss_bom_store import get_faiss_store
    store = get_faiss_store()
    stats = store.get_stats()
    
    print(f"FAISS Index Status:")
    print(f"  Parts: {stats.get('total_parts')}")
    print(f"  Vectors: {stats.get('total_vectors')}")
    print(f"  Manufacturer options: {stats.get('total_manufacturer_options')}")
    print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
