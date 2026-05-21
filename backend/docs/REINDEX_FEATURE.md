# Reindexing Feature Implementation

## Overview
The reindexing feature has been implemented to allow users to rebuild the FAISS index from all uploaded documents with detailed logging and progress tracking.

## Implementation Details

### Backend Changes

#### 1. Updated Parser Import
**File:** `app/routes_faiss.py`

Changed from `simple_bom_parser` to the fixed `bom_parser_v2`:
```python
from app.bom_parser_v2 import parse_bom_document
```

This ensures all manufacturers are correctly extracted (including previously missing ones like Stackpole Electronics, Inc).

#### 2. New `/reindex` Endpoint
**File:** `app/routes_faiss.py`

**Method:** POST  
**Route:** `/reindex`

**Process Flow:**
1. **Find Documents** - Scans uploads directory for all PDF files
2. **Clear Old Index** - Deletes old FAISS index files (parts.index, metadata.pkl)
3. **Parse Documents** - Re-parses all PDFs with the fixed parser
4. **Rebuild Index** - Creates new FAISS index with embeddings
5. **Return Results** - Provides detailed logs and statistics

**Response Structure:**
```json
{
  "success": true,
  "message": "Successfully reindexed N document(s)",
  "summary": {
    "documents_processed": 1,
    "total_parts_indexed": 34,
    "total_manufacturer_options": 80,
    "duration_seconds": 45.23,
    "parsing_details": [
      {
        "filename": "561668-001-BOM-CC_4PinTop.pdf",
        "parts_extracted": 34,
        "manufacturers_extracted": 80,
        "status": "success"
      }
    ]
  },
  "statistics": {
    "total_parts": 34,
    "total_vectors": 34,
    "total_manufacturer_options": 80,
    "unique_manufacturers": 25,
    "parts_with_multiple_manufacturers": 19
  },
  "logs": ["Detailed log lines..."]
}
```

### Frontend Changes

#### Updated Reindex Button Handler
**File:** `frontend/ui.html`

**Features:**
- ✅ Displays progress bar during reindexing
- ✅ Shows summary statistics (documents, parts, manufacturers)
- ✅ Displays per-document parsing details
- ✅ Expandable detailed logs section
- ✅ Error handling with detailed error messages
- ✅ Button disabled during reindexing to prevent duplicate requests

**UI Elements:**
- Summary card with key metrics
- Document details with status indicators
- Collapsible detailed logs viewer
- Progress bar with smooth transitions
- Success/error status indicators

## Detailed Logging

The reindex process provides comprehensive logging including:

### 1. Process Start
```
======================================================================
REINDEXING PROCESS STARTED
Start Time: 2026-04-30 14:30:00
======================================================================

📄 Found 1 PDF file(s) to reindex:
   • 561668-001-BOM-CC_4PinTop.pdf
```

### 2. Index Clearing
```
🗑️  CLEARING OLD INDEX
----------------------------------------------------------------------
   Old index contained:
   • 34 parts
   • 34 vectors
   • 80 manufacturer options
   ✓ Deleted old files: parts.index, metadata.pkl
   ✓ Index cleared successfully
```

### 3. Document Parsing
```
📋 PARSING DOCUMENTS
----------------------------------------------------------------------

   Processing: 561668-001-BOM-CC_4PinTop.pdf
   ✓ Extracted 34 part(s) with 80 manufacturer option(s)
   Sample parts:
      1. 563969-472 (3 manufacturer(s))
         1. KEMET Corporation - CAS18C472KARGC
         2. Yageo - CC1812KKX7RBBB472
         3. Murata Manufacturing - GA343DR7GD472KW01L
      2. 556112-224 (4 manufacturer(s))
      3. 560330-222 (3 manufacturer(s))
```

### 4. Index Rebuilding
```
🔨 REBUILDING FAISS INDEX
----------------------------------------------------------------------
   Total parts to index: 34
   Total manufacturer options: 80

   Creating embeddings and indexing...
   ✓ FAISS index rebuilt successfully
```

### 5. Final Statistics
```
📊 FINAL STATISTICS
----------------------------------------------------------------------
   Total parts indexed: 34
   Total vectors: 34
   Total manufacturer options: 80
   Unique manufacturers: 25
   Parts with multiple manufacturers: 19

   Duration: 45.23 seconds
   End Time: 2026-04-30 14:30:45

======================================================================
✅ REINDEXING COMPLETED SUCCESSFULLY
======================================================================
```

## Usage

### Via API
```bash
curl -X POST http://localhost:8000/reindex
```

### Via Web UI
1. Open the web interface at `http://localhost:8000`
2. Click the "Re-index Documents" button
3. Wait for the process to complete
4. View the detailed results and logs

### Testing
Run the test script:
```bash
python test_reindex_endpoint.py
```

## Benefits

1. **Data Accuracy** - Uses the fixed parser that captures all manufacturers
2. **Transparency** - Detailed logs show exactly what's being indexed
3. **Debugging** - Easy to identify parsing issues with per-document status
4. **Statistics** - Comprehensive metrics about the indexed data
5. **User Control** - Allows manual reindexing when needed

## When to Use Reindex

- After fixing bugs in the BOM parser
- After uploading new documents
- When you suspect the index is corrupted or outdated
- To rebuild the index with updated embeddings
- After changing manufacturer validation rules

## Technical Notes

### File Cleanup
The reindex process deletes these files:
- `index-faiss-store/parts.index` - FAISS vector index
- `index-faiss-store/metadata.pkl` - Part metadata pickle file

The `parts_readable.json` is regenerated automatically by the FAISS store save function.

### Performance
- Small BOMs (< 50 parts): ~10-20 seconds
- Medium BOMs (50-200 parts): ~30-60 seconds  
- Large BOMs (200+ parts): ~1-3 minutes

Most time is spent on:
1. Generating embeddings (Ollama API calls)
2. PDF parsing and table extraction

### Error Handling
The endpoint handles:
- Missing uploads directory
- No PDF files to reindex
- Parsing errors (per-document)
- FAISS index errors
- Embedding generation failures

Each error is logged with details for debugging.
