"""
Simple Main Entry Point - FastAPI app with simplified architecture

Clean and focused on:
- Upload BOM documents
- Query part information
- Call SiliconExpert API with precise MPN + Manufacturer pairs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.routes_simple import router
import os

app = FastAPI(
    title="Simple BOM Query System",
    description="Upload BOM documents and query part information with SiliconExpert API integration",
    version="2.0-simplified"
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

# Serve UI
@app.get("/")
@app.get("/ui")
def get_ui():
    ui_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "ui.html")
    return FileResponse(ui_path, media_type="text/html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
