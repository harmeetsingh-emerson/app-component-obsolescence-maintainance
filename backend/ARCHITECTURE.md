# FAISS Multi-Agent BOM Query System — Architecture

---

## High-Level Component Map

```
┌─────────────────┐        HTTP (localhost:8000)        ┌──────────────────────────────┐
│  React Frontend │  ─────────────────────────────────▶ │  FastAPI Backend              │
│  (App.js / MUI) │  ◀─────────────────────────────────  │  app/main_faiss.py           │
└─────────────────┘                                      └──────────────────────────────┘
                                                                       │
                        ┌──────────────────────────┬──────────────────┤
                        ▼                          ▼                  ▼
               ┌──────────────────┐    ┌───────────────────┐  ┌─────────────────┐
               │  FAISS Index     │    │  OCR Store         │  │ External APIs   │
               │  (vector store)  │    │  (flat text file)  │  │  - Ollama       │
               │  index-faiss-    │    │  ocr_outputs/      │  │  - SiliconExpert│
               │  store/          │    │  ocr_extraction.txt│  └─────────────────┘
               └──────────────────┘    └───────────────────┘
```

---

## 1 — Document Ingestion Flow (`POST /upload`)

```
User drops file in UI
        │
        ▼
  ┌─────────────┐
  │ file saved  │  → uploads/<filename>
  │ to disk     │
  └─────────────┘
        │
        ├──── .txt file? ─────────────────────────────────────────────────────┐
        │                                                                      │
        │                                                                      ▼
        │                                                         parse_ocr_bom_text()
        │                                                          (ocr_processor.py)
        │                                                                      │
        │                                                                      ▼
        │                                                            Parts → FAISS store
        │                                                            HTTP 200 ✓
        │
        └──── .pdf file ──────┐
                              │
                              ▼
                  ┌────────────────────────┐
                  │  pdfplumber extraction  │   (bom_parser_v2.py)
                  │  try text-based first   │
                  └────────────────────────┘
                              │
              ┌───────────────┴──────────────┐
              │                              │
         Parts found?                   No parts
              │                              │
              ▼                              ▼
       FAISS store                  _pdf_needs_ocr()?
       HTTP 200 ✓              (check native text < 100 chars)
                                            │
                          ┌─────────────────┴───────────────────┐
                          │                                      │
                     Image-based PDF                       Text PDF,
                     (OCR needed)                       no BOM structure
                          │                                      │
                          ▼                                   HTTP 400
                  ┌──────────────────────────────────┐
                  │  BackgroundTask: _run_ocr_and_store│
                  │  HTTP 202 returned immediately     │
                  └──────────────────────────────────┘
                          │ (async, runs in thread)
                          ▼
              ┌────────────────────────────┐
              │  PyMuPDF: render page      │  200 DPI (configurable via ocr_dpi param)
              │  → numpy image array       │
              └────────────────────────────┘
                          │
                          ▼
              ┌────────────────────────────┐
              │  Native text path:         │  ≥ 50 chars? → skip OCR
              │  page.get_text("text")     │  (fast path for hybrid PDFs)
              └────────────────────────────┘
                          │ (image pages only)
                          ▼
              ┌────────────────────────────────────────────────────┐
              │  PaddleOCR 3.x engine (singleton, pre-warmed)      │
              │                                                    │
              │  Config priority (tries each until one succeeds):  │
              │  A: PP-OCRv5_mobile_det + en_PP-OCRv5_mobile_rec   │
              │  B: no explicit model names (fallback)             │
              │  C: use_angle_cls=False (older paddleocr builds)   │
              │  D: lang='en' only (absolute minimum)              │
              │                                                    │
              │  Per-page timeout: 120s at 200 DPI                 │
              │  Scales as DPI²: 300 DPI → 270s, 400 DPI → 480s   │
              └────────────────────────────────────────────────────┘
                          │
                          ▼
              ┌────────────────────────────────────────────────────┐
              │  Table-aware row grouping                           │
              │                                                    │
              │  1. Compute Y-center of each bounding box polygon   │
              │  2. Sort all blocks top → bottom by Y-center        │
              │  3. Group blocks within 10px Y-threshold (same row) │
              │  4. Sort each row left → right by X-center          │
              │  5. Join cells with \t → tab-separated line         │
              └────────────────────────────────────────────────────┘
                          │
                          ▼
              ┌────────────────────────────┐
              │  append_ocr_extraction()   │  → ocr_outputs/ocr_extraction.txt
              │  mark_ocr_complete()       │  → ocr_outputs/ocr_status.json
              └────────────────────────────┘
                          │
                          ▼
              Frontend polls GET /ocr-status every 3 seconds
              showing live progress bar: page X / Y
```

---

## 2 — OCR BOM Parser Passes (`ocr_processor.py` → `parse_tesseract_bom` / `parse_ocr_bom_text`)

```
Raw OCR text (tab-separated rows from PaddleOCR)
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PASS 1: Column-based structured parser                                  │
│                                                                          │
│  • Split lines into tab-delimited columns                                │
│  • Detect header row containing known BOM column names:                  │
│    part_number / manufacturer / mpn / description / quantity / ref_des   │
│  • Map each column semantically (handles many header spelling variants)  │
│  • Extract rows below header                                             │
│                                                                          │
│  Returns: parts[] if headers found AND ≥ 1 valid part extracted          │
└─────────────────────────────────────────────────────────────────────────┘
        │
        └─ empty → PASS 1b ──────────────────────────────────────────────────┐
                                                                              │
        ┌─────────────────────────────────────────────────────────────────────┘
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PASS 1b: _parse_emerson_drawing_format()                                │
│                                                                          │
│  Trigger condition: ≥ 2 Emerson-style BOM anchors detected in text       │
│  Anchor regex: (?<!\w)4[12][G6]\s*\d[\d\s]{2,6}\s*[-]\s*\d[\d\s]{1,5}  │
│  Examples: 42G4000-0671, 42G201 1 - 0030, 41G3189-BA1 (tolerant of OCR  │
│  spaces inserted within digit sequences)                                 │
│                                                                          │
│  Algorithm per anchor:                                                   │
│    ① Group  = anchor line + continuation lines (up to next anchor)       │
│    ② Pre-lines = up to 2 lines BEFORE anchor (only pure mfr fragments,  │
│       no digits, must match known manufacturer — catches cases like      │
│       "SAMSUNG" appearing on the line before 42G2011-0030)               │
│    ③ MPN extraction (two steps):                                         │
│       Step 1 — last tab-column of anchor line:                           │
│         collapse OCR spaces: "CL O5B1 04K05NNNC" → "CLO5B104K05NNNC"   │
│         validate: must contain digits AND letters                        │
│       Step 2 — scan continuation lines if Step 1 fails:                 │
│         SKIP description lines (Ksps 20-Pin WLCSP, LD0 Regulator…)      │
│         SKIP footprint-only lines (SOT-23, BGA-2.39X2.39…)              │
│         SKIP pure manufacturer fragment lines                            │
│         STOP on: all-caps 3+ char token or digit-only token              │
│    ④ Manufacturer extraction:                                            │
│       Search text = extra_pre_lines + continuation_lines                 │
│       (anchor line itself skipped — it contains the Emerson PN, not mfr) │
│       Tab normalization before matching                                  │
│       Compound name joining: "samsung" + "electro-mechanics" → match     │
│       Dedup: remove shorter matches that are substrings of longer ones   │
│    ⑤ Normalise extracted text → canonical title-case manufacturer name  │
│                                                                          │
│  Returns: parts[] if anchors detected; [] if < 2 anchors (falls through) │
└─────────────────────────────────────────────────────────────────────────┘
        │
        └─ empty → PASS 2 ───────────────────────────────────────────────────┐
                                                                              │
        ┌─────────────────────────────────────────────────────────────────────┘
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PASS 2: Regex-based line scanner (generic fallback)                     │
│                                                                          │
│  • Scan every line for Emerson PN patterns:                              │
│    ERAA##### / ERSA##### / JT###### / 563969-472 / 42G2011 etc.          │
│  • Extract manufacturer names via KNOWN_MANUFACTURERS_FOR_OCR set        │
│  • Apply OCR corrections dictionary (character-level substitutions)      │
│  • Multi-line continuation scanning for MPN / mfr on following lines     │
│  • combine_split_manufacturer_names() for fragmented names               │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
Parts[] with schema:
{
  "part_number":   "42G4000-0671",
  "description":   "IC-ANALOG-AD/DA",
  "quantity":      "",
  "designators":   "U1",
  "source_file":   "JT105541.pdf",
  "manufacturers": [
    { "manufacturer": "Analog Devices", "mpn": "AD7689BCBZ-RL7", "preference": 1 },
    { "manufacturer": "Alt Mfr",        "mpn": "ALT-MPN",        "preference": 2 }
  ]
}
```

---

## 3 — FAISS Vector Store (`app/faiss_bom_store.py`)

```
add_parts(parts[], source_file)
        │
        ▼
  For each part:
  ┌───────────────────────────────────────────────────────────────────┐
  │  _create_searchable_text()                                         │
  │                                                                   │
  │  "Part Number: 42G4000-0671 | Description: IC-ANALOG-AD/DA |      │
  │   Manufacturer 1: Analog Devices | MPN 1: AD7689BCBZ-RL7 | ..."   │
  │                                                                   │
  │  All manufacturers and MPNs are included so semantic search        │
  │  can match on any of them.                                        │
  └───────────────────────────────────────────────────────────────────┘
        │
        ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │  _get_embedding()                                                  │
  │                                                                   │
  │  POST http://localhost:11434/api/embeddings                        │
  │  { "model": "nomic-embed-text", "prompt": searchable_text }       │
  │  → 768-dimensional float32 vector                                 │
  │  Pad or truncate to exact 768 dims if model returns different size │
  └───────────────────────────────────────────────────────────────────┘
        │
        ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │  FAISS IndexFlatL2                                                 │
  │  • L2 (Euclidean) distance metric                                 │
  │  • All part vectors stored in memory                              │
  │  • metadata[] list in memory (parallel array to FAISS index)      │
  │                                                                   │
  │  Persisted to disk after every add_parts():                       │
  │    index-faiss-store/parts.index       ← FAISS binary index       │
  │    index-faiss-store/metadata.pkl      ← Python pickle            │
  │    index-faiss-store/parts_readable.json ← human-readable copy    │
  └───────────────────────────────────────────────────────────────────┘

Search modes:
  search_by_part_number(pn)
    → exact string match on metadata[].part_number (case-insensitive)
    → returns single dict or None

  search(query, top_k=10)
    → embed query text via Ollama
    → FAISS index.search() → top_k nearest L2 neighbors
    → filter: only keep results with L2 distance < 200
      (distance ≥ 200 = semantically unrelated, discarded)
    → returns list of part dicts with added "distance" field
```

---

## 4 — Query Flow (`POST /query`)

```
User types natural language query in UI
Optional: select a BOM filename filter from dropdown
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 0: QueryIntentAgent                                                │
│                                                                          │
│  Model: llama3.2:3b via Ollama (localhost:11434)                         │
│  Timeout: 15s — falls back to defaults if Ollama unavailable             │
│                                                                          │
│  System prompt instructs LLM to return ONLY a JSON object:               │
│  {                                                                       │
│    "limit": <int or null>,          // e.g. "get me 5 parts" → 5        │
│    "want_all": <bool>,              // "get all parts" → true            │
│    "specific_parts": ["563969-472"], // part numbers mentioned in text    │
│    "filters": {                                                          │
│      "manufacturer": "Yageo",       // "from Yageo" filter              │
│      "description_contains": "cap"  // "capacitors" keyword filter       │
│    }                                                                     │
│  }                                                                       │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 1: OrchestratorAgent.process_query(query)                          │
│                                                                          │
│  ┌─── 1a. PartNumberExtractorAgent ────────────────────────────────┐    │
│  │  regex patterns scan query text for part numbers:                │    │
│  │  ERAA#####, ERSA#####, JT######,                                 │    │
│  │  42G2011-0030, 563969-472, TLV74333PDBVR, AD7689BCBZ-RL7 etc.   │    │
│  │  → List[str] of extracted part numbers                           │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─── 1b. FAISSSearchAgent ────────────────────────────────────────┐    │
│  │  IF specific part numbers extracted:                             │    │
│  │    → search_by_part_number(pn) for each (exact metadata match)   │    │
│  │    → if no exact match: semantic search top_k=5, dist < 200      │    │
│  │  ELSE (no part numbers in query):                                │    │
│  │    → semantic embedding search top_k=10, dist < 200              │    │
│  │  → List[Dict] of matched parts                                   │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─── 1c. SiliconExpertAgent ──────────────────────────────────────┐    │
│  │  Authenticate → collect ALL manufacturer-MPN pairs from parts    │    │
│  │  Batch into groups of 20 → GET listPartSearch                    │    │
│  │  Merge PartData[] from all batches                               │    │
│  │  → api_data dict with combined SE response                       │    │
│  └──────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  ┌─── 1d. ResponseFormatterAgent ──────────────────────────────────┐    │
│  │  format() → human-readable text for chat display                 │    │
│  │  prepare_excel_data() → list of flat row dicts for Excel export   │    │
│  └──────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
        │
        │ filename_filter present AND FAISS returned no parts?
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 1b: Generic list-all fallback (filename-scoped queries)            │
│                                                                          │
│  • store.get_all_parts() → filter by source_file == filename_filter      │
│  • OR: get_ocr_text_for_source(filename) → parse_ocr_bom_text()         │
│                                                                          │
│  Apply LLM intent filters in order:                                      │
│    1. specific_parts (LLM list) + regex-extracted PNs → exact PN match   │
│    2. manufacturer substring filter (case-insensitive)                   │
│    3. description keyword filter                                          │
│    4. count limit (slice to first N)                                     │
│                                                                          │
│  If 0 parts survive filters → return HTTP 200 with "not found" message   │
│  Otherwise → SiliconExpertAgent (re-run with filtered parts only)        │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 2: OCR Extraction Search (parallel path, always runs)              │
│                                                                          │
│  search_ocr_extraction(query, max_results=10)                            │
│  → keyword / PN scan of ocr_outputs/ocr_extraction.txt (flat file)       │
│  → apply filename filter to matches                                      │
│                                                                          │
│  If OCR matches found AND FAISS didn't cover the queried tokens:         │
│    parse_ocr_bom_text(matched_ocr_text)                                  │
│    → SiliconExpertAgent call for these OCR-sourced parts                 │
│    → merge OCR SE results into main result                               │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 3: Merge FAISS + OCR results                                       │
│  De-duplicate on part_number                                             │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STEP 4: ResponseReviewerAgent (LLM post-filter)                         │
│                                                                          │
│  Model: gpt-oss:latest via Ollama (thinking model, up to 300s timeout)   │
│                                                                          │
│  Builds compact table of all matched parts:                              │
│  "BOM_No | Requested_Part | Manufacturer | YEOL | Lifecycle | RoHS | Desc"│
│                                                                          │
│  LLM identifies which rows satisfy the original query →                  │
│  returns { "matching_bom_nos": [...], "explanation": "..." }             │
│                                                                          │
│  Filter excel_data to only matching BOM rows                             │
│                                                                          │
│  Safety fallback: if LLM returns 0 but explanation doesn't say "no rows" │
│  → return unfiltered data (guards against LLM formatting bugs)           │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
JSON response:
{
  "success":            true,
  "parts_found":        [ { part_number, description, manufacturers[], ... } ],
  "api_data":           { Status, Result:{ PartData:[] }, _query_metadata:[] },
  "formatted_response": "=== BOM QUERY RESULTS ===\n🔹 PART 1: ...",
  "excel_data":         [ { BOM No, Requested Part, Manufacturer Name,
                            MPN, YEOL, EOL, RoHS, Description, ... } ],
  "message":            "Found N part(s)"
}
```

---

## 5 — SiliconExpert API Detail

```
SiliconExpertAgent.authenticate()
        │
        POST https://api.siliconexpert.com/ProductAPI/search/
             authenticateUser?login=emerson_api&apiKey=Em$809@rRt2
             (SSL verify=False — internal CA not trusted)
        │
        ▼
  Authenticated requests.Session() stored in self.session
  (session cookie reused for all subsequent calls)

─────────────────────────────────────────────────────────────────────

SiliconExpertAgent.search_all_manufacturers(parts[])
        │
        ├── Collect ALL manufacturer-MPN pairs from every part
        │   preference 1 = primary, 2/3/4 = approved alternates
        │   → all_pairs = [{ partNumber, manufacturer, bom_part_number, preference }]
        │
        ├── Split into batches of 20 (URL length limit)
        │
        └── For each batch:
              GET …/listPartSearch?partNumber=
                  [{"partNumber":"AD7689BCBZ-RL7","manufacturer":"Analog Devices"},...]
                    │
                    ▼
              Response structure:
              {
                "Status": { "Success": "true", "Code": "0", "Message": "" },
                "Result": {
                  "PartData": [
                    {
                      "RequestedPart":         "AD7689BCBZ-RL7",
                      "RequestedManufacturer": "Analog Devices",
                      "PartList": {
                        "PartDto": {
                          "PartNumber":  "AD7689BCBZ-RL7",
                          "Manufacturer":"Analog Devices",
                          "ComID":       "12345",
                          "PlName":      "ADC, SAR 16-bit",
                          "Description": "8-ch ADC SAR 16-bit",
                          "Datasheet":   "https://...",
                          "Lifecycle":   "Active",
                          "RoHS":        "Yes",
                          "YEOL":        "12.4"
                        }
                      }
                    },
                    ...
                  ]
                }
              }
                    │
                    ▼
              Merge all PartData[] from all batches into single Result
              Attach _query_metadata[] for preference lookup in formatter
                    │
                    ▼
              excel_data row per SE result:
              {
                "BOM No":                   1,
                "Requested Part":           "42G4000-0671",
                "Manufacturer Part Number": "AD7689BCBZ-RL7",
                "Manufacturer Name":        "Analog Devices",
                "YEOL":                     "12.4",
                "EOL":                      "No",        ← "Yes"=Discontinued
                "RoHS":                     "Yes",
                "Description":              "8-ch ADC SAR 16-bit",
                "Datasheet":                "https://...",
                "Preference":               1
              }
```

---

## 6 — Startup Sequence

```
$ $env:PYTHONIOENCODING="utf-8"
$ $env:KMP_DUPLICATE_LIB_OK="TRUE"       ← Windows OpenMP conflict fix
$ $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True"
$ python -W ignore -m uvicorn app.main_faiss:app --host 0.0.0.0 --port 8000
        │
        ▼
  FastAPI app created (app/main_faiss.py)
  CORS middleware: allow_origins=["*"]
  Router included: app/routes_faiss.py
  Static route: GET / and GET /ui → frontend/ui.html
        │
        ▼
  @startup_event:
    1. clear_stale_in_progress()
       → reads ocr_status.json, resets any "in_progress" entries
         left over from a previous crash (they would never complete)
       → prints list of affected filenames

    2. get_faiss_store()
       → load FAISS index from index-faiss-store/parts.index
       → load metadata from index-faiss-store/metadata.pkl
       → if files missing: initialise empty IndexFlatL2(768)
       → print stats: parts, vectors, manufacturer_options

    3. _get_paddle_ocr_engine() [singleton init]
       → try PaddleOCR configs A → B → C → D
       → pre-warm: run predict() on a 64×256 white image
         (forces lazy model download NOW at startup, not on first doc)
       → print: "Pre-warm complete - models are cached"
        │
        ▼
  Server ready — listening on 0.0.0.0:8000
```

---

## 7 — Storage Layout

```
project root/
├── uploads/                          ← saved uploaded files (PDF / TXT)
│
├── index-faiss-store/
│   ├── parts.index                   ← FAISS binary index (L2, 768-dim float32)
│   ├── metadata.pkl                  ← Python pickle: list of part dicts
│   └── parts_readable.json           ← human-readable JSON copy of metadata
│
├── ocr_outputs/
│   ├── ocr_extraction.txt            ← flat append-only store of all OCR text
│   │                                    each document section delimited by ===
│   └── ocr_status.json               ← { "in_progress": {}, "completed": {} }
│                                        tracks page-by-page OCR progress
│
├── configs/
│   ├── known_manufacturers.json      ← learned + seeded manufacturer list
│   └── settings.yaml                 ← app settings
│
├── frontend/
│   ├── ui.html                       ← legacy single-file UI
│   └── ai-assistant-fe/             ← React app (MUI components)
│       ├── src/App.js
│       └── public/index.html
│
└── app/
    ├── main_faiss.py                 ← FastAPI app entry point
    ├── routes_faiss.py               ← all HTTP endpoints
    ├── multi_agent_faiss.py          ← Agent 0-5 definitions + Orchestrator
    ├── faiss_bom_store.py            ← FAISS vector store class
    ├── bom_parser_v2.py              ← text-based PDF parser (pdfplumber)
    ├── ocr_processor.py              ← PaddleOCR engine + BOM parse passes
    └── ocr_store.py                  ← flat-file OCR store + status tracking
```

---

## 8 — Frontend Actions Map

| UI Action | HTTP Method & Endpoint | Backend Handler | Notes |
|---|---|---|---|
| Drop / select file | `POST /upload` (multipart form) | `upload_document()` | `ocr_dpi` form field controls DPI |
| Poll OCR progress | `GET /ocr-status` | `get_ocr_status()` | Polled every 3s while OCR runs |
| Submit chat query | `POST /query` (JSON) | `query_endpoint()` | Optional `filename` field for scoped search |
| Load file list | `GET /files` | `list_files()` | Populates filename dropdown |
| Reindex all docs | `POST /reindex` | `reindex_documents()` | Re-parses all files in uploads/ |
| Download Excel | Client-side only | — | Uses `xlsx.js` on `excel_data[]` from query response |
| Ingest OCR .txt | `POST /ingest-ocr` (JSON) | `ingest_ocr_file()` | `{ "file_path": "ocr_outputs/X.txt" }` |

---

## 9 — Multi-Agent System (summary)

| Agent | Class | Model / Tool | Purpose |
|---|---|---|---|
| 0 | `QueryIntentAgent` | llama3.2:3b (Ollama) | Parse query intent: limit, filters, specific PNs |
| 0b | `ResponseReviewerAgent` | gpt-oss:latest (Ollama) | Post-filter results to only rows matching query |
| 1 | `PartNumberExtractorAgent` | regex patterns | Extract PN tokens from query text |
| 2 | `FAISSSearchAgent` | FAISS + nomic-embed-text | Exact + semantic search over indexed parts |
| 3 | `SiliconExpertAgent` | SiliconExpert REST API | Lifecycle / RoHS / YEOL lookup for all MPNs |
| 4 | `ResponseFormatterAgent` | — | Format text output + build Excel row dicts |
| 5 | `OrchestratorAgent` | — | Coordinate agents 1 → 4 in sequence |

---

## 10 — External Service Dependencies

```
Ollama  (localhost:11434)
├── nomic-embed-text    → 768-dim embeddings for FAISS indexing and search
├── llama3.2:3b         → QueryIntentAgent — fast intent parsing (15s timeout)
└── gpt-oss:latest      → ResponseReviewerAgent — thinking model, LLM filtering
                          (300s timeout, num_predict=4096 for chain-of-thought)

SiliconExpert API  (https://api.siliconexpert.com)
└── /ProductAPI/search/
    ├── authenticateUser    → POST — obtain session cookie
    └── listPartSearch      → GET  — lifecycle, RoHS, YEOL, Datasheet, ComID
                              batches of ≤ 20 MPN+manufacturer pairs per request
                              SSL verification disabled (verify=False)
```
