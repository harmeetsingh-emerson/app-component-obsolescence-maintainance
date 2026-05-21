"""
Simple Routes - Clean API endpoints using the new simplified architecture

Endpoints:
- POST /upload - Upload BOM document
- POST /query - Query for part information
- GET /stats - Get database statistics
- POST /clear - Clear all data
"""

from fastapi import APIRouter, UploadFile, File, Request
from fastapi.responses import JSONResponse
import os
import shutil

from app.simple_bom_parser import parse_bom_document
from app.simple_bom_store import get_store
from app.simple_query_engine import get_query_engine, format_response_for_user


router = APIRouter()

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    Upload a BOM document (PDF) and extract structured part data.
    
    Response:
        {
            "success": True/False,
            "filename": "bom.pdf",
            "parts_extracted": 42,
            "message": "..."
        }
    """
    
    try:
        # Save file
        file_path = os.path.join(UPLOADS_DIR, file.filename)
        
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        print(f"\n[Upload] Saved file: {file.filename}")
        
        # Parse BOM document
        try:
            parts = parse_bom_document(file_path)
            
            if not parts:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "filename": file.filename,
                        "parts_extracted": 0,
                        "message": "No parts extracted. Ensure the PDF contains a BOM table with columns: Part Number, Manufacturer, Manufacturer Part Number."
                    }
                )
            
            # Add to store
            store = get_store()
            store.add_parts(parts, source_file=file.filename)
            
            return {
                "success": True,
                "filename": file.filename,
                "parts_extracted": len(parts),
                "message": f"Successfully extracted and indexed {len(parts)} parts from {file.filename}"
            }
        
        except Exception as e:
            print(f"[Upload] Parse error: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "filename": file.filename,
                    "parts_extracted": 0,
                    "message": f"Failed to parse document: {str(e)}"
                }
            )
    
    except Exception as e:
        print(f"[Upload] Error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"Upload failed: {str(e)}"
            }
        )


@router.post("/query")
async def query_endpoint(request: Request):
    """
    Query for part information.
    
    Request:
        {
            "query": "What is part 563969-472?"
        }
    
    Response:
        {
            "success": True/False,
            "parts_found": [...],
            "api_data": {...},
            "formatted_response": "...",
            "message": "..."
        }
    """
    
    try:
        data = await request.json()
        query = data.get("query")
        
        if not query:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "No query provided"
                }
            )
        
        # Process query
        engine = get_query_engine()
        result = engine.process_query(query)
        
        # Add formatted response
        result["formatted_response"] = format_response_for_user(result)
        
        return result
    
    except Exception as e:
        print(f"[Query] Error: {e}")
        import traceback
        traceback.print_exc()
        
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"Query failed: {str(e)}"
            }
        )


@router.get("/stats")
async def get_stats():
    """
    Get database statistics.
    
    Response:
        {
            "total_parts": 42,
            "parts_with_manufacturer": 40,
            "parts_with_mpn": 38,
            "unique_manufacturers": 15,
            "manufacturers_list": ["KEMET", "Yageo", ...]
        }
    """
    
    try:
        store = get_store()
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


@router.post("/clear")
async def clear_database():
    """
    Clear all parts from the database.
    
    Response:
        {
            "success": True,
            "message": "Database cleared"
        }
    """
    
    try:
        store = get_store()
        store.clear()
        
        return {
            "success": True,
            "message": "Database cleared successfully"
        }
    
    except Exception as e:
        print(f"[Clear] Error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": f"Failed to clear database: {str(e)}"
            }
        )


@router.get("/parts")
async def get_all_parts():
    """
    Get all parts in the database (for debugging).
    
    Response:
        {
            "total": 42,
            "parts": [...]
        }
    """
    
    try:
        store = get_store()
        all_parts = store.get_all_parts()
        
        return {
            "total": len(all_parts),
            "parts": all_parts[:100]  # Limit to 100 for performance
        }
    
    except Exception as e:
        print(f"[Get Parts] Error: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Failed to get parts: {str(e)}"
            }
        )


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok", "version": "2.0-simplified"}
