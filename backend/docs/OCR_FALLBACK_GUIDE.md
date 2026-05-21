# OCR Fallback Feature

## Overview

The BOM parser now includes **automatic OCR fallback** for image-based/scanned PDFs using Ollama vision models.

## How It Works

```
┌─────────────────┐
│  Upload PDF     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Try Text       │
│  Extraction     │◄── Primary method (fast, accurate)
└────────┬────────┘
         │
         │ No text found?
         ▼
┌─────────────────┐
│  OCR Fallback   │◄── Uses Ollama vision model
│  (LLaVA)        │    (slower, handles scanned docs)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Parse BOM      │
│  Structure      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Return Parts   │
└─────────────────┘
```

## Setup

### 1. Install Ollama
```bash
# Windows/Mac/Linux
Download from: https://ollama.ai
```

### 2. Install Vision Model
```bash
# Install LLaVA (recommended for OCR)
ollama pull llava:latest

# OR install other vision models
ollama pull llama3.2-vision:latest
ollama pull bakllava:latest
```

### 3. Verify Installation
```bash
# Check installed models
ollama list

# Test vision capability
python test_ocr_setup.py
```

## Usage

### In Upload Endpoint

OCR fallback is **automatically enabled** for all uploads:

```python
# Automatic fallback
parts = parse_bom_document(file_path, use_ocr_fallback=True)
```

### Manual Control

```python
from app.bom_parser_v2 import parse_bom_document

# Enable OCR fallback (default)
parts = parse_bom_document("document.pdf", use_ocr_fallback=True)

# Disable OCR fallback (text-only)
parts = parse_bom_document("document.pdf", use_ocr_fallback=False)
```

### Direct OCR

```python
from app.ocr_processor import ocr_pdf_to_text, parse_ocr_bom_text

# Extract text via OCR
ocr_text = ocr_pdf_to_text("scanned_bom.pdf", model="llava:latest")

# Parse OCR text
parts = parse_ocr_bom_text(ocr_text)
```

## Supported Models

| Model | Size | Speed | Accuracy | Recommended |
|-------|------|-------|----------|-------------|
| **llava:latest** | 4.7GB | Medium | High | ✅ Best for BOM |
| llama3.2-vision | 8GB+ | Slow | Highest | Complex tables |
| bakllava | 4.5GB | Fast | Medium | Quick extraction |

## Performance

### Text-Based PDF
- **Speed**: < 1 second
- **Accuracy**: 95-99%
- **Method**: Direct text extraction (pdfplumber)

### Image-Based PDF (OCR)
- **Speed**: 10-30 seconds per page
- **Accuracy**: 70-90%
- **Method**: Vision model OCR (LLaVA)

## Limitations

1. **OCR Quality**: Depends on scan quality
   - Recommended: 150+ DPI
   - Clear text, minimal noise
   
2. **Table Structure**: Complex layouts may need manual review
   - Multi-level headers
   - Merged cells
   - Rotated text

3. **Processing Time**: OCR is slower than text extraction
   - Page 1: ~10-15 seconds
   - Each additional page: ~10-15 seconds

## Error Handling

### No Vision Model Installed
```
[BOM Parser V2] OCR fallback not available (missing dependencies)
[BOM Parser V2] Install: pip install PyMuPDF requests
```
**Solution**: Install PyMuPDF: `pip install PyMuPDF`

### Ollama Not Running
```
[OCR] Cannot connect to Ollama
Make sure Ollama is running on http://localhost:11434
```
**Solution**: Start Ollama application

### No Vision Models
```
[OCR] Model 'llava:latest' not found
```
**Solution**: `ollama pull llava:latest`

## Configuration

### Change OCR Model

Edit `app/ocr_processor.py`:
```python
# Use different model
def ocr_with_ollama(image_bytes: bytes, model: str = "llama3.2-vision:latest"):
    ...
```

### Adjust Image Quality

Edit `app/ocr_processor.py`:
```python
# Higher DPI = better quality, larger file
pix = page.get_pixmap(matrix=fitz.Matrix(300/72, 300/72))  # 300 DPI
```

### OCR Prompt Customization

Edit the prompt in `ocr_with_ollama()` to improve extraction:
```python
prompt = """Extract ALL data from this BOM table including:
- Part numbers
- Descriptions
- ALL manufacturers (Mfgr1, Mfgr2, Mfgr3, Mfgr4)
- Manufacturer part numbers
- Quantities

Format as pipe-separated table."""
```

## Testing

### Test OCR Setup
```bash
python test_ocr_setup.py
```

### Test OCR Extraction
```bash
python test_ocr_extraction.py
```

### Test Full Parser
```bash
python test_parser_with_ocr.py
```

## Debugging

### Save OCR Text
When OCR fails to parse, the raw OCR text is saved:
```
[BOM Parser V2] OCR text saved to: document_ocr_debug.txt
```

Review this file to understand what the vision model extracted.

### Enable Verbose Logging
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Best Practices

1. **Prefer text-based PDFs**: Always request digital BOMs from suppliers
2. **OCR as fallback only**: Use for legacy/scanned documents
3. **Verify OCR results**: Review extracted data for accuracy
4. **Report issues**: If OCR consistently fails, contact support with sample PDF

## Future Enhancements

- [ ] Multi-model fallback (try multiple OCR models)
- [ ] Confidence scoring for OCR results
- [ ] Post-OCR correction using context
- [ ] Batch OCR processing
- [ ] GPU acceleration for faster OCR
