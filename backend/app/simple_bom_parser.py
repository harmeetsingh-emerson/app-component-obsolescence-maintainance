"""
Simple BOM Parser - Extract structured data from BOM documents

Focus: Precision over complexity
- Parse tables from PDFs
- Extract: Part Number, Manufacturer, MPN, Description, Qty, Designators
- Store in clean structured format
- OCR support for scanned/image-based PDFs
"""

import re
from typing import List, Dict, Optional
import pdfplumber
import camelot

# OCR imports (optional - only used for scanned PDFs)
try:
    import pytesseract
    from pdf2image import convert_from_path
    import pandas as pd
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("[BOM Parser] OCR libraries not available. Scanned PDFs won't be processed.")

# ML-based table detection (advanced OCR)
try:
    from img2table.document import Image as Img2TableImage
    from img2table.ocr import TesseractOCR
    import cv2
    import numpy as np
    from PIL import Image as PILImage, ImageEnhance, ImageFilter
    ML_TABLE_DETECTION_AVAILABLE = True
except ImportError:
    ML_TABLE_DETECTION_AVAILABLE = False
    # Import PIL anyway for type hints
    try:
        from PIL import Image as PILImage
    except:
        PILImage = None
    print("[BOM Parser] ML table detection not available. Install: pip install img2table opencv-python-headless")

# EasyOCR (best local OCR - better than Tesseract, easier than PaddleOCR)
try:
    import easyocr
    EASYOCR_AVAILABLE = True
    print("[BOM Parser] EasyOCR loaded successfully")
except Exception as e:
    EASYOCR_AVAILABLE = False
    print(f"[BOM Parser] EasyOCR not available: {str(e)[:100]}")
    print("[BOM Parser] Install: pip install easyocr")


def parse_bom_document(file_path: str) -> List[Dict[str, str]]:
    """
    Parse BOM document and extract structured part data.
    
    Returns:
        List of parts with structure:
        {
            "part_number": "563969-472",        # Internal BOM part number
            "manufacturer": "KEMET",             # Primary manufacturer name
            "mpn": "C1210C472KARGC7800",        # Manufacturer Part Number
            "description": "Cap; Ceramic...",    # Full description
            "quantity": "10",                    # Quantity
            "designators": "C1, C2, C3"          # Component designators
        }
    """
    
    if file_path.lower().endswith('.pdf'):
        return _parse_pdf_bom(file_path)
    else:
        raise ValueError(f"Unsupported file format: {file_path}")


def _parse_pdf_bom(file_path: str) -> List[Dict[str, str]]:
    """Parse PDF BOM using table extraction"""
    
    parts_data = []
    
    # Try Method 1: Camelot (best for structured tables)
    try:
        print(f"[BOM Parser] Extracting tables from {file_path} using Camelot...")
        tables = camelot.read_pdf(file_path, pages='all', flavor='lattice')
        
        if not tables:
            tables = camelot.read_pdf(file_path, pages='all', flavor='stream')
        
        for table_idx, table in enumerate(tables):
            df = table.df
            print(f"[BOM Parser] Processing table {table_idx + 1} ({len(df)} rows)")
            
            # Parse the dataframe
            parsed_parts = _parse_table_dataframe(df)
            parts_data.extend(parsed_parts)
    
    except Exception as e:
        print(f"[BOM Parser] Camelot failed: {e}")
    
    # Try Method 2: pdfplumber (fallback)
    if not parts_data:
        try:
            print(f"[BOM Parser] Trying pdfplumber...")
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    
                    for table in tables:
                        parsed_parts = _parse_table_rows(table)
                        parts_data.extend(parsed_parts)
        
        except Exception as e:
            print(f"[BOM Parser] pdfplumber failed: {e}")
    
    # Try Method 3: EasyOCR (best local OCR for scanned PDFs)
    if not parts_data and EASYOCR_AVAILABLE:
        try:
            print(f"[BOM Parser] No text-based tables found. Trying EasyOCR...")
            easy_parts = _parse_pdf_with_easyocr(file_path)
            parts_data.extend(easy_parts)
        except Exception as e:
            print(f"[BOM Parser] EasyOCR failed: {e}")
            import traceback
            traceback.print_exc()
    
    # Try Method 4: ML-based table detection (for scanned/image-based PDFs)
    if not parts_data and ML_TABLE_DETECTION_AVAILABLE:
        try:
            print(f"[BOM Parser] Trying ML-based table detection...")
            ml_parts = _parse_pdf_with_ml_table_detection(file_path)
            parts_data.extend(ml_parts)
        except Exception as e:
            print(f"[BOM Parser] ML table detection failed: {e}")
            import traceback
            traceback.print_exc()
    
    # Try Method 5: Basic OCR (fallback - always try if still no parts found)
    if not parts_data and OCR_AVAILABLE:
        try:
            print(f"[BOM Parser] Trying basic OCR fallback...")
            ocr_parts = _parse_pdf_with_ocr(file_path)
            parts_data.extend(ocr_parts)
        except Exception as e:
            print(f"[BOM Parser] OCR failed: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"[BOM Parser] Extracted {len(parts_data)} parts total")
    return parts_data


def _preprocess_image_for_ocr(image, upscale_factor: float = 2.0):
    """
    Preprocess image to improve OCR accuracy with upscaling
    
    Steps:
    1. Upscale/zoom image for better text recognition
    2. Convert to grayscale
    3. Enhance contrast more aggressively
    4. Enhance sharpness
    5. Reduce noise with median filter
    6. Apply adaptive thresholding (binarization)
    
    Args:
        image: PIL Image object
        upscale_factor: Zoom factor for image (2.0 = 2x zoom, improves OCR on poor scans)
    
    Returns:
        PIL Image object (preprocessed and upscaled)
    """
    
    # Upscale/zoom the image for better text recognition
    if upscale_factor > 1.0:
        new_width = int(image.width * upscale_factor)
        new_height = int(image.height * upscale_factor)
        # Use LANCZOS for high-quality upscaling
        img = image.resize((new_width, new_height), PILImage.Resampling.LANCZOS)
        print(f"[BOM Preprocessing] Upscaled from {image.width}x{image.height} to {new_width}x{new_height} ({upscale_factor}x)")
    else:
        img = image
    
    # Convert to grayscale
    img = img.convert('L')
    
    # Enhance contrast more aggressively (2.5x instead of 2.0x)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.5)
    
    # Enhance sharpness (new step)
    sharpness_enhancer = ImageEnhance.Sharpness(img)
    img = sharpness_enhancer.enhance(2.0)
    
    # Denoise using median filter
    img = img.filter(ImageFilter.MedianFilter(size=3))
    
    # Additional sharpening
    img = img.filter(ImageFilter.SHARPEN)
    
    # Convert to numpy array for OpenCV processing
    img_np = np.array(img)
    
    # Apply adaptive thresholding with larger block size for upscaled images
    block_size = max(11, int(11 * upscale_factor)) | 1  # Ensure odd number
    binary = cv2.adaptiveThreshold(
        img_np, 255, 
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY, 
        block_size, 2
    )
    
    # Convert back to PIL Image
    return PILImage.fromarray(binary)


def _parse_pdf_with_easyocr(file_path: str) -> List[Dict[str, str]]:
    """
    Parse scanned/image-based PDF using EasyOCR - Best local OCR solution
    
    EasyOCR advantages:
    - Better accuracy than Tesseract (deep learning based)
    - Easy installation (no complex dependencies)
    - Supports 80+ languages
    - Completely offline/local
    - No cloud API costs
    - Works reliably on Windows
    """
    
    if not EASYOCR_AVAILABLE:
        print("[BOM Parser EasyOCR] Library not available")
        return []
    
    parts_data = []
    
    try:
        print(f"[BOM Parser EasyOCR] Initializing reader...")
        
        # Initialize EasyOCR reader (downloads models on first run)
        # gpu=False for CPU-only (works on most machines)
        # lang_list=['en'] for English
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        
        print(f"[BOM Parser EasyOCR] Converting PDF to images...")
        # Convert PDF to images at 300 DPI
        from pdf2image import convert_from_path
        images = convert_from_path(file_path, dpi=300)
        
        print(f"[BOM Parser EasyOCR] Processing {len(images)} page(s)...")
        
        all_page_text = []
        
        for page_num, pil_image in enumerate(images, 1):
            print(f"[BOM Parser EasyOCR] Page {page_num}: Detecting text...")
            
            # Convert PIL image to numpy array
            import numpy as np
            img_array = np.array(pil_image)
            
            # Run OCR
            result = reader.readtext(img_array)
            
            print(f"[BOM Parser EasyOCR] Page {page_num}: Found {len(result)} text elements")
            
            # Extract text and bounding boxes
            # Result format: [(bbox, text, confidence), ...]
            page_text_lines = []
            for (bbox, text, confidence) in result:
                # bbox is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                # Get top-left y-coordinate for sorting by vertical position
                y_pos = bbox[0][1]
                page_text_lines.append((y_pos, text, confidence))
            
            # Sort by vertical position to reconstruct text order
            page_text_lines.sort(key=lambda x: x[0])
            
            # Collect text for this page
            page_text = '\n'.join([text for (_, text, conf) in page_text_lines if conf > 0.3])
            all_page_text.append(page_text)
            
            print(f"[BOM Parser EasyOCR] Page {page_num}: Extracted {len(page_text)} characters")
        
        # Combine all pages
        full_text = '\n\n'.join(all_page_text)
        
        print(f"[BOM Parser EasyOCR] Total text extracted: {len(full_text)} characters")
        
        if full_text:
            # Convert text to rows (split by newlines)
            rows = [line.strip() for line in full_text.split('\n') if line.strip()]
            
            # Detect table structure
            table_rows = _detect_table_structure(rows)
            
            if table_rows:
                print(f"[BOM Parser EasyOCR] Detected table with {len(table_rows)} rows")
                
                # Parse as table
                parsed_parts = _parse_table_rows(table_rows)
                
                if parsed_parts:
                    print(f"[BOM Parser EasyOCR] Found {len(parsed_parts)} parts")
                    parts_data.extend(parsed_parts)
            else:
                print(f"[BOM Parser EasyOCR] No table structure detected, trying pattern-based extraction...")
                # Try pattern-based extraction
                from app.simple_bom_parser import _extract_bom_by_pattern
                parsed_parts = _extract_bom_by_pattern([rows])
                if parsed_parts:
                    parts_data.extend(parsed_parts)
        
        print(f"[BOM Parser EasyOCR] Total parts extracted: {len(parts_data)}")
        
    except Exception as e:
        print(f"[BOM Parser EasyOCR] Error: {e}")
        import traceback
        traceback.print_exc()
    
    return parts_data


def _parse_pdf_with_ml_table_detection(file_path: str) -> List[Dict[str, str]]:
    """
    Parse scanned/image-based PDF using ML-based table detection
    
    Uses img2table library which employs:
    - Computer vision algorithms for table detection
    - Morphological operations for border detection
    - OCR (Tesseract) for text extraction within detected tables
    """
    
    if not ML_TABLE_DETECTION_AVAILABLE:
        print("[BOM Parser ML] Libraries not available")
        return []
    
    parts_data = []
    
    try:
        print(f"[BOM Parser ML] Converting PDF to images...")
        # Convert PDF pages to images at high DPI for better detection
        # 600 DPI provides 2x improvement over standard 300 DPI
        images = convert_from_path(file_path, dpi=600)
        
        # Initialize Tesseract OCR
        ocr = TesseractOCR(lang="eng")
        
        print(f"[BOM Parser ML] Processing {len(images)} page(s) with ML table detection...")
        
        for page_num, pil_image in enumerate(images, 1):
            print(f"[BOM Parser ML] Page {page_num}: Preprocessing image...")
            
            # Preprocess image with 2x upscaling for better table detection
            # Upscaling helps OCR recognize small/blurry text
            preprocessed = _preprocess_image_for_ocr(pil_image, upscale_factor=2.0)
            
            # Save preprocessed image temporarily for img2table
            import tempfile
            import os
            
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                tmp_path = tmp_file.name
                preprocessed.save(tmp_path, 'PNG')
            
            try:
                print(f"[BOM Parser ML] Page {page_num}: Detecting tables...")
                
                # Create img2table Image object
                img_doc = Img2TableImage(tmp_path, detect_rotation=True)
                
                # Extract tables using ML-based detection
                # implicit_rows=True helps with tables that don't have clear row borders
                # borderless_tables=True detects tables without visible borders
                extracted_tables = img_doc.extract_tables(
                    ocr=ocr,
                    implicit_rows=True,
                    borderless_tables=True,
                    min_confidence=50
                )
                
                if extracted_tables:
                    print(f"[BOM Parser ML] Page {page_num}: Found {len(extracted_tables)} table(s)")
                    
                    for table_idx, table_obj in enumerate(extracted_tables):
                        # Convert to DataFrame
                        df = table_obj.df
                        
                        if df is not None and not df.empty:
                            print(f"[BOM Parser ML] Table {table_idx + 1}: {len(df)} rows, {len(df.columns)} columns")
                            
                            # Try parsing as DataFrame first
                            parsed_parts = _parse_table_dataframe(df)
                            
                            if not parsed_parts:
                                # Convert DataFrame to list of lists for row-based parsing
                                table_rows = [df.columns.tolist()] + df.values.tolist()
                                parsed_parts = _parse_table_rows(table_rows)
                            
                            parts_data.extend(parsed_parts)
                            print(f"[BOM Parser ML] Table {table_idx + 1}: Extracted {len(parsed_parts)} parts")
                else:
                    print(f"[BOM Parser ML] Page {page_num}: No tables detected")
            
            except cv2.error as cv_err:
                print(f"[BOM Parser ML] Page {page_num}: OpenCV error - {cv_err}")
                print(f"[BOM Parser ML] Skipping this page, will try basic OCR fallback")
                
            except Exception as page_err:
                print(f"[BOM Parser ML] Page {page_num}: Error - {page_err}")
                import traceback
                traceback.print_exc()
            
            finally:
                # Clean up temporary file
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
    
    except Exception as e:
        print(f"[BOM Parser ML] Error: {e}")
        import traceback
        traceback.print_exc()
    
    return parts_data


def _parse_pdf_with_ocr(file_path: str) -> List[Dict[str, str]]:
    """Parse scanned/image-based PDF using OCR"""
    
    if not OCR_AVAILABLE:
        print("[BOM Parser OCR] Libraries not available")
        return []
    
    parts_data = []
    
    try:
        print(f"[BOM Parser OCR] Converting PDF to images...")
        # Convert PDF pages to images at high DPI for better OCR accuracy
        # 600 DPI provides significantly better text recognition on poor quality scans
        images = convert_from_path(file_path, dpi=600)
        
        print(f"[BOM Parser OCR] Processing {len(images)} page(s) with Tesseract...")
        
        for page_num, image in enumerate(images, 1):
            print(f"[BOM Parser OCR] Processing page {page_num}...")
            
            # Preprocess with upscaling for better OCR
            print(f"[BOM Parser OCR] Preprocessing and upscaling image...")
            preprocessed_image = _preprocess_image_for_ocr(image, upscale_factor=2.0)
            
            # Run OCR on the preprocessed image
            # Use TSV output for structured data extraction
            ocr_data = pytesseract.image_to_data(preprocessed_image, output_type=pytesseract.Output.DICT)
            
            # Convert OCR output to table-like structure
            table_data = _extract_table_from_ocr(ocr_data)
            
            if table_data:
                # OCR tables often have variable column counts - use raw rows approach
                parsed_parts = _parse_table_rows(table_data)
                
                parts_data.extend(parsed_parts)
                print(f"[BOM Parser OCR] Page {page_num}: Found {len(parsed_parts)} parts")
    
    except Exception as e:
        print(f"[BOM Parser OCR] Error: {e}")
        import traceback
        traceback.print_exc()
    
    return parts_data


def _extract_table_from_ocr(ocr_data: Dict) -> List[List[str]]:
    """
    Extract table structure from Tesseract OCR output
    
    OCR data contains: text, left, top, width, height, conf per word
    Group words into rows based on vertical position (top coordinate)
    """
    
    if not ocr_data or 'text' not in ocr_data:
        return []
    
    # Filter out empty text and low confidence detections
    words_data = []
    for i in range(len(ocr_data['text'])):
        text = str(ocr_data['text'][i]).strip()
        conf = int(ocr_data['conf'][i]) if ocr_data['conf'][i] != -1 else 0
        
        if text and conf > 30:  # Only use words with >30% confidence
            words_data.append({
                'text': text,
                'left': ocr_data['left'][i],
                'top': ocr_data['top'][i],
                'width': ocr_data['width'][i],
                'height': ocr_data['height'][i],
                'conf': conf
            })
    
    if not words_data:
        return []
    
    # Group words into rows (tolerance: ±15 pixels vertically)
    rows = []
    current_row = []
    current_top = None
    row_tolerance = 15
    
    # Sort by vertical position first, then horizontal
    words_data.sort(key=lambda w: (w['top'], w['left']))
    
    for word in words_data:
        if current_top is None:
            current_top = word['top']
            current_row = [word]
        elif abs(word['top'] - current_top) <= row_tolerance:
            # Same row
            current_row.append(word)
        else:
            # New row - save current row
            if current_row:
                # Sort words in row by horizontal position
                current_row.sort(key=lambda w: w['left'])
                row_text = [w['text'] for w in current_row]
                rows.append(row_text)
            
            # Start new row
            current_row = [word]
            current_top = word['top']
    
    # Add last row
    if current_row:
        current_row.sort(key=lambda w: w['left'])
        row_text = [w['text'] for w in current_row]
        rows.append(row_text)
    
    # IMPROVED: More flexible table detection
    # Look for rows with BOM keywords OR part number patterns
    table_start_idx = None
    
    for idx, row in enumerate(rows):
        row_text = ' '.join(row).upper()
        
        # Check for header keywords (more flexible)
        has_bom_keywords = (
            'DESCRIPTION' in row_text or 
            'MANUFACTURER' in row_text or
            'MFG' in row_text or
            'PART' in row_text or
            'QTY' in row_text or
            'DESIGNATOR' in row_text or
            'REF' in row_text
        )
        
        # Check for part number pattern (alphanumeric with hyphens/underscores)
        has_part_pattern = any(
            re.match(r'^[A-Z0-9]{3,}[-_]?[A-Z0-9]*$', cell.upper()) 
            for cell in row if len(cell) > 3
        )
        
        if has_bom_keywords:
            # Found header row
            table_start_idx = idx
            print(f"[BOM Parser OCR] Found header at row {idx}: {' | '.join(row[:5])}")
            break
        elif has_part_pattern and idx > 3:  # Skip first few rows (likely title block)
            # Found data row without clear header - start here
            table_start_idx = max(0, idx - 1)  # Include previous row as potential header
            print(f"[BOM Parser OCR] Found data pattern at row {idx}, starting from row {table_start_idx}")
            break
    
    if table_start_idx is not None:
        # Extract table from header onwards
        table_rows = rows[table_start_idx:]
        print(f"[BOM Parser OCR] Detected table with {len(table_rows)} rows")
        return table_rows
    
    # If no clear table, return all rows (skip first 3 - usually title block)
    print(f"[BOM Parser OCR] No clear table found, returning rows from index 3")
    return rows[3:] if len(rows) > 3 else rows


def _parse_table_dataframe(df) -> List[Dict[str, str]]:
    """Parse pandas DataFrame from Camelot - Extract ALL manufacturers and MPNs"""
    
    parts = []
    
    # Find header row (contains "Part Number", "Manufacturer", etc.)
    header_row_idx = None
    columns_map = {}
    
    for idx, row in df.iterrows():
        row_text = ' '.join(str(cell).upper() for cell in row)
        
        if 'PART' in row_text and 'NUMBER' in row_text:
            header_row_idx = idx
            
            # Map column names to indices - SUPPORT MULTIPLE MANUFACTURERS
            for col_idx, cell in enumerate(row):
                cell_upper = str(cell).upper().strip()
                
                if 'PART' in cell_upper and 'NUMBER' in cell_upper and 'MANUFACTURER' not in cell_upper:
                    columns_map['part_number'] = col_idx
                elif 'DESCRIPTION' in cell_upper:
                    columns_map['description'] = col_idx
                elif 'QTY' in cell_upper or 'QUANTITY' in cell_upper:
                    columns_map['quantity'] = col_idx
                elif 'DESIGNATOR' in cell_upper or 'REF' in cell_upper:
                    columns_map['designators'] = col_idx
                
                # Detect Manufacturer 1, 2, 3, 4...
                elif 'MANUFACTURER' in cell_upper and 'PART' not in cell_upper:
                    # Extract manufacturer number (e.g., "MANUFACTURER 1" -> 1)
                    mfr_match = re.search(r'MANUFACTURER[\s\n]*(\d+)', cell_upper)
                    if mfr_match:
                        mfr_num = int(mfr_match.group(1))
                        columns_map[f'manufacturer_{mfr_num}'] = col_idx
                    elif 'manufacturer_1' not in columns_map:
                        # Default to manufacturer 1 if no number
                        columns_map['manufacturer_1'] = col_idx
                
                # Detect Manufacturer Part Number 1, 2, 3, 4... (handle newlines in headers)
                elif 'MANUFACTURER' in cell_upper and 'PART' in cell_upper:
                    # Use [\s\S] instead of . to match newlines
                    mpn_match = re.search(r'MANUFACTURER[\s\n]+PART[\s\S]*?(\d+)', cell_upper)
                    if mpn_match:
                        mpn_num = int(mpn_match.group(1))
                        columns_map[f'mpn_{mpn_num}'] = col_idx
                    elif 'mpn_1' not in columns_map:
                        columns_map['mpn_1'] = col_idx
            
            break
    
    if header_row_idx is None:
        print("[BOM Parser] Could not find header row")
        return parts
    
    print(f"[BOM Parser] Found columns: {columns_map}")
    
    # Extract data rows
    for idx, row in df.iterrows():
        if idx <= header_row_idx:
            continue
        
        # Extract part number
        part_number = None
        if 'part_number' in columns_map:
            part_number = str(row[columns_map['part_number']]).strip()
        
        # Skip if no valid part number
        if not part_number or part_number == 'nan' or len(part_number) < 3:
            continue
        
        # Extract basic fields
        part_data = {"part_number": part_number}
        
        if 'description' in columns_map:
            desc = str(row[columns_map['description']]).strip()
            if desc and desc != 'nan':
                part_data['description'] = desc
        
        if 'quantity' in columns_map:
            qty = str(row[columns_map['quantity']]).strip()
            if qty and qty != 'nan':
                part_data['quantity'] = qty
        
        if 'designators' in columns_map:
            des = str(row[columns_map['designators']]).strip()
            if des and des != 'nan':
                part_data['designators'] = des
        
        # Extract ALL manufacturers and MPNs
        manufacturers = []
        for i in range(1, 5):  # Support up to 4 manufacturers
            mfr_key = f'manufacturer_{i}'
            mpn_key = f'mpn_{i}'
            
            mfr = None
            mpn = None
            
            if mfr_key in columns_map:
                mfr = str(row[columns_map[mfr_key]]).strip()
                if mfr and mfr != 'nan' and len(mfr) > 2:
                    mfr = mfr
                else:
                    mfr = None
            
            if mpn_key in columns_map:
                mpn = str(row[columns_map[mpn_key]]).strip()
                if mpn and mpn != 'nan' and len(mpn) > 2:
                    mpn = mpn
                else:
                    mpn = None
            
            # Only add if we have both manufacturer and MPN
            if mfr and mpn:
                manufacturers.append({
                    "manufacturer": mfr,
                    "mpn": mpn,
                    "preference": i  # 1 = primary, 2+ = alternatives
                })
        
        # Only add part if we have at least one manufacturer-MPN pair
        if manufacturers:
            part_data['manufacturers'] = manufacturers
            parts.append(part_data)
            
            print(f"[BOM Parser] + {part_number} | {len(manufacturers)} manufacturer(s):")
            for mfr_data in manufacturers:
                print(f"    [{mfr_data['preference']}] {mfr_data['manufacturer']} | {mfr_data['mpn']}")
    
    return parts


def _extract_bom_by_pattern(table: List[List[str]]) -> List[Dict[str, str]]:
    """
    Pattern-based BOM extraction for OCR/scanned documents.
    Instead of matching headers, analyzes data patterns to identify BOM structure.
    
    Strategy:
    1. Find rows with part number patterns (alphanumeric codes)
    2. Identify manufacturer columns by checking for manufacturer names
    3. Extract data based on position and pattern, not header names
    """
    
    if not table or len(table) < 3:
        return []
    
    parts_data = []
    
    # Common manufacturer names/patterns (case-insensitive)
    KNOWN_MANUFACTURERS = {
        'kemet', 'murata', 'yageo', 'tdk', 'samsung', 'panasonic', 'vishay', 
        'avx', 'walsin', 'bourns', 'nichicon', 'rubycon', 'wurth', 'coilcraft',
        'nxp', 'ti', 'texas instruments', 'analog devices', 'adi', 'maxim',
        'infineon', 'on semi', 'stmicro', 'microchip', 'atmel', 'renesas',
        'linear tech', 'ltc', 'fairchild', 'onsemi', 'rohm', 'toshiba',
        'samsung electro', 'johanson', 'abracon', 'epson', 'kyocera'
    }
    
    def looks_like_part_number(text: str) -> bool:
        """Check if text looks like a part number"""
        if not text or len(text) < 3:
            return False
        text = str(text).strip()
        # Part numbers typically have mix of letters and numbers
        has_letter = any(c.isalpha() for c in text)
        has_digit = any(c.isdigit() for c in text)
        # Must have both letters and numbers, length 3-50
        return has_letter and has_digit and 3 <= len(text) <= 50
    
    def looks_like_manufacturer(text: str) -> bool:
        """Check if text looks like a manufacturer name"""
        if not text or len(text) < 2:
            return False
        text_lower = str(text).lower().strip()
        # Check against known manufacturers
        for mfr in KNOWN_MANUFACTURERS:
            if mfr in text_lower:
                return True
        # Generic patterns: 2-30 chars, mostly letters, could be a company name
        if 2 <= len(text_lower) <= 30 and sum(c.isalpha() for c in text_lower) > len(text_lower) * 0.5:
            # Not a part number pattern
            if not looks_like_part_number(text):
                return True
        return False
    
    def looks_like_description(text: str) -> bool:
        """Check if text looks like a description"""
        if not text or len(text) < 5:
            return False
        text = str(text).strip()
        # Descriptions are typically longer and have spaces
        return len(text) > 10 and ' ' in text
    
    print(f"[BOM Pattern] Analyzing {len(table)} rows for BOM patterns...")
    
    # Skip first few rows (often headers/titles in scanned docs)
    start_row = 0
    for i, row in enumerate(table[:10]):  # Check first 10 rows
        row_text = ' '.join(str(cell).lower() for cell in row if cell)
        # Look for BOM header keywords
        if any(kw in row_text for kw in ['item', 'part', 'mfr', 'manufacturer', 'qty', 'description']):
            start_row = i + 1
            print(f"[BOM Pattern] Found header at row {i}, starting data extraction from row {start_row}")
            break
    
    # Analyze column structure from actual data
    bom_rows = []
    for row_idx in range(start_row, len(table)):
        row = table[row_idx]
        if not row or len(row) < 3:
            continue
        
        # Clean row data
        cleaned_row = [str(cell).strip() if cell else '' for cell in row]
        
        # Check if this row contains BOM data
        part_number_found = False
        manufacturer_found = False
        
        for cell in cleaned_row:
            if looks_like_part_number(cell):
                part_number_found = True
            if looks_like_manufacturer(cell):
                manufacturer_found = True
        
        # If row has both part number and manufacturer patterns, likely a BOM row
        if part_number_found:
            bom_rows.append({
                'row_idx': row_idx,
                'data': cleaned_row
            })
    
    print(f"[BOM Pattern] Found {len(bom_rows)} potential BOM rows")
    
    if not bom_rows:
        return []
    
    # Analyze columns across all BOM rows
    max_cols = max(len(r['data']) for r in bom_rows)
    
    col_analysis = []
    for col_idx in range(max_cols):
        col_values = [r['data'][col_idx] if col_idx < len(r['data']) else '' 
                     for r in bom_rows]
        
        # Analyze this column
        part_count = sum(1 for v in col_values if looks_like_part_number(v))
        mfr_count = sum(1 for v in col_values if looks_like_manufacturer(v))
        desc_count = sum(1 for v in col_values if looks_like_description(v))
        non_empty = sum(1 for v in col_values if v)
        
        col_type = 'unknown'
        confidence = 0
        
        if non_empty > 0:
            part_ratio = part_count / non_empty
            mfr_ratio = mfr_count / non_empty
            desc_ratio = desc_count / non_empty
            
            if part_ratio > 0.5:
                col_type = 'part_number' if part_ratio > mfr_ratio else 'mpn'
                confidence = part_ratio
            elif mfr_ratio > 0.3:
                col_type = 'manufacturer'
                confidence = mfr_ratio
            elif desc_ratio > 0.5:
                col_type = 'description'
                confidence = desc_ratio
        
        col_analysis.append({
            'idx': col_idx,
            'type': col_type,
            'confidence': confidence,
            'sample': col_values[0] if col_values else ''
        })
    
    # Find primary part number column (highest confidence)
    part_cols = [c for c in col_analysis if c['type'] == 'part_number']
    if not part_cols:
        print("[BOM Pattern] No part number column detected")
        return []
    
    primary_part_col = max(part_cols, key=lambda x: x['confidence'])['idx']
    print(f"[BOM Pattern] Primary part number column: {primary_part_col}")
    
    # Find manufacturer and MPN column pairs
    mfr_cols = [c['idx'] for c in col_analysis if c['type'] == 'manufacturer']
    mpn_cols = [c['idx'] for c in col_analysis if c['type'] == 'mpn']
    
    print(f"[BOM Pattern] Manufacturer columns: {mfr_cols}")
    print(f"[BOM Pattern] MPN columns: {mpn_cols}")
    
    # Extract parts
    for bom_row in bom_rows:
        row_data = bom_row['data']
        
        # Get primary part number
        if primary_part_col >= len(row_data):
            continue
            
        part_number = row_data[primary_part_col].strip()
        if not part_number or not looks_like_part_number(part_number):
            continue
        
        # Extract manufacturers and MPNs
        manufacturers = []
        
        # Strategy 1: Pair manufacturer and MPN columns
        for mfr_col in mfr_cols:
            if mfr_col >= len(row_data):
                continue
            
            manufacturer = row_data[mfr_col].strip()
            if not manufacturer:
                continue
            
            # Find nearest MPN column to the right
            mpn = ''
            for mpn_col in sorted(mpn_cols):
                if mpn_col > mfr_col and mpn_col < len(row_data):
                    mpn = row_data[mpn_col].strip()
                    if mpn and looks_like_part_number(mpn):
                        break
            
            if manufacturer and mpn:
                manufacturers.append({
                    'manufacturer': manufacturer,
                    'mpn': mpn,
                    'preference': len(manufacturers) + 1
                })
        
        # Strategy 2: If we have unpaired MPNs, try to find manufacturers nearby
        if not manufacturers:
            for mpn_col in mpn_cols:
                if mpn_col >= len(row_data):
                    continue
                
                mpn = row_data[mpn_col].strip()
                if not mpn or not looks_like_part_number(mpn):
                    continue
                
                # Look for manufacturer in adjacent columns (left or right)
                manufacturer = ''
                for offset in [-1, -2, 1, 2]:
                    check_col = mpn_col + offset
                    if 0 <= check_col < len(row_data):
                        candidate = row_data[check_col].strip()
                        if looks_like_manufacturer(candidate):
                            manufacturer = candidate
                            break
                
                if manufacturer and mpn:
                    manufacturers.append({
                        'manufacturer': manufacturer,
                        'mpn': mpn,
                        'preference': len(manufacturers) + 1
                    })
        
        # Get description if available
        desc_cols = [c['idx'] for c in col_analysis if c['type'] == 'description']
        description = ''
        if desc_cols and desc_cols[0] < len(row_data):
            description = row_data[desc_cols[0]].strip()
        
        part_entry = {
            'part_number': part_number,
            'description': description,
            'manufacturers': manufacturers
        }
        
        parts_data.append(part_entry)
    
    print(f"[BOM Pattern] Extracted {len(parts_data)} parts with pattern-based approach")
    
    return parts_data


def _parse_table_rows(table: List[List[str]]) -> List[Dict[str, str]]:
    """Parse table as list of rows (from pdfplumber or OCR) - Extract ALL manufacturers"""
    
    if not table or len(table) < 2:
        return []
    
    parts = []
    
    # Find header row (more flexible for OCR)
    header_row_idx = None
    columns_map = {}
    
    # For OCR data, skip first 5 rows (typically title block/confidential notices)
    start_row = 5 if len(table) > 10 else 0
    
    # Check rows for header (OCR might have header split across rows)
    for idx, row in enumerate(table[start_row:20], start=start_row):
        if not row or len(row) < 3:  # Header must have at least 3 columns
            continue
        
        row_text = ' '.join(str(cell or '').upper() for cell in row)
        
        # Skip confidential notices and title blocks (common false positives)
        if 'CONFIDENTIAL' in row_text or 'EMERSON' in row_text or 'RECIPIENT' in row_text:
            continue
        
        # Count how many BOM-related keywords appear
        keyword_count = sum([
            'DESCRIPTION' in row_text,
            'MANUFACTURER' in row_text,
            'MFG' in row_text and len(row_text) < 100,  # Short text only
            bool(re.search(r'\bPART\b', row_text)),  # Whole word match
            bool(re.search(r'\bPIN\b', row_text)),
            'QTY' in row_text,
            'DESIGNATOR' in row_text,
            'REF' in row_text and len(row_text) < 50
        ])
        
        # Require at least 2 BOM keywords to consider it a header
        if keyword_count >= 2:
            header_row_idx = idx
            
            # Map columns - SUPPORT MULTIPLE MANUFACTURERS
            for col_idx, cell in enumerate(row):
                if not cell:
                    continue
                
                cell_upper = str(cell).upper().strip()
                
                # More flexible matching
                if ('PART' in cell_upper or 'PIN' in cell_upper) and 'NUMBER' in cell_upper and 'MANUFACTURER' not in cell_upper:
                    columns_map['part_number'] = col_idx
                elif 'PART' in cell_upper and not columns_map.get('part_number') and 'MANUFACTURER' not in cell_upper:
                    # Just "PART" or "PIN" alone
                    columns_map['part_number'] = col_idx
                elif 'DESCRIPTION' in cell_upper or 'DESC' in cell_upper:
                    columns_map['description'] = col_idx
                elif 'QTY' in cell_upper or 'QUANTITY' in cell_upper:
                    columns_map['quantity'] = col_idx
                elif 'DESIGNATOR' in cell_upper or 'REF' in cell_upper:
                    columns_map['designators'] = col_idx
                
                # Detect Manufacturer 1, 2, 3, 4...
                elif ('MANUFACTURER' in cell_upper or 'MFGR' in cell_upper or 'MFG' in cell_upper) and 'PART' not in cell_upper and 'PIN' not in cell_upper:
                    mfr_match = re.search(r'(\d+)', cell_upper)
                    if mfr_match:
                        mfr_num = int(mfr_match.group(1))
                        columns_map[f'manufacturer_{mfr_num}'] = col_idx
                    elif 'manufacturer_1' not in columns_map:
                        columns_map['manufacturer_1'] = col_idx
                
                # Detect Manufacturer Part Number 1, 2, 3, 4...
                elif ('MANUFACTURER' in cell_upper or 'MFGR' in cell_upper or 'MFG' in cell_upper) and ('PART' in cell_upper or 'PIN' in cell_upper):
                    mpn_match = re.search(r'(\d+)', cell_upper)
                    if mpn_match:
                        mpn_num = int(mpn_match.group(1))
                        columns_map[f'mpn_{mpn_num}'] = col_idx
                    elif 'mpn_1' not in columns_map:
                        columns_map['mpn_1'] = col_idx
            
            # If we found at least a part number column, use this row
            if 'part_number' in columns_map or 'description' in columns_map:
                break
    
    if header_row_idx is None:
        # No header found - try to infer from data patterns
        # Look for first row with alphanumeric part-like data
        for idx, row in enumerate(table[:15]):
            if not row or len(row) < 3:
                continue
            
            # Check if row has part number pattern
            for col_idx, cell in enumerate(row):
                if cell and re.match(r'^[A-Z0-9]{3,}[-_]?[A-Z0-9]*$', str(cell).upper().strip()):
                    # Found potential part number column
                    header_row_idx = max(0, idx - 1)  # Use previous row as header
                    columns_map['part_number'] = col_idx
                    print(f"[BOM Parser] Inferred part number column {col_idx} from data pattern")
                    break
            
            if 'part_number' in columns_map:
                break
    
    if not columns_map:
        print("[BOM Parser] Could not find or infer column structure")
        print("[BOM Parser] Trying pattern-based extraction...")
        # Use pattern-based extraction as fallback
        return _extract_bom_by_pattern(table)
    
    print(f"[BOM Parser] Found columns: {columns_map}")
    
    # Extract data rows
    for idx in range(header_row_idx + 1, len(table)):
        row = table[idx]
        
        if not row:
            continue
        
        # Extract part number (required field)
        part_number = None
        if 'part_number' in columns_map and columns_map['part_number'] < len(row):
            part_number = str(row[columns_map['part_number']] or '').strip()
        
        # Skip if no valid part number
        if not part_number or len(part_number) < 3:
            continue
        
        part_data = {"part_number": part_number}
        
        # Extract basic fields
        if 'description' in columns_map and columns_map['description'] < len(row):
            desc = str(row[columns_map['description']] or '').strip()
            if desc:
                part_data['description'] = desc
        
        if 'quantity' in columns_map and columns_map['quantity'] < len(row):
            qty = str(row[columns_map['quantity']] or '').strip()
            if qty:
                part_data['quantity'] = qty
        
        if 'designators' in columns_map and columns_map['designators'] < len(row):
            des = str(row[columns_map['designators']] or '').strip()
            if des:
                part_data['designators'] = des
        
        # Extract ALL manufacturers and MPNs
        manufacturers = []
        for i in range(1, 5):  # Support up to 4 manufacturers
            mfr_key = f'manufacturer_{i}'
            mpn_key = f'mpn_{i}'
            
            mfr = None
            mpn = None
            
            if mfr_key in columns_map and columns_map[mfr_key] < len(row):
                mfr = str(row[columns_map[mfr_key]] or '').strip()
                if mfr and len(mfr) > 2:
                    mfr = mfr
                else:
                    mfr = None
            
            if mpn_key in columns_map and columns_map[mpn_key] < len(row):
                mpn = str(row[columns_map[mpn_key]] or '').strip()
                if mpn and len(mpn) > 2:
                    mpn = mpn
                else:
                    mpn = None
            
            # Only add if we have both manufacturer and MPN
            if mfr and mpn:
                manufacturers.append({
                    "manufacturer": mfr,
                    "mpn": mpn,
                    "preference": i
                })
        
        # Only add if we have at least one manufacturer-MPN pair
        if manufacturers:
            part_data['manufacturers'] = manufacturers
            parts.append(part_data)
    
    return parts


if __name__ == "__main__":
    # Test
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python simple_bom_parser.py <pdf_file>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    parts = parse_bom_document(file_path)
    
    print(f"\n=== Extracted {len(parts)} parts ===")
    for part in parts[:5]:  # Show first 5
        print(f"\nPart Number: {part.get('part_number')}")
        print(f"Manufacturer: {part.get('manufacturer')}")
        print(f"MPN: {part.get('mpn')}")
        print(f"Description: {part.get('description', 'N/A')[:50]}...")
