"""
Multi-Agent Query System with FAISS

Agents:
0. QueryIntentAgent    - Use LLM to understand query intent (count, filters, etc.)
1. PartNumberExtractorAgent - Extract part numbers from queries
2. FAISSSearchAgent - Search FAISS for part data
3. SiliconExpertAgent - Query API with ALL manufacturer-MPN pairs
4. ResponseFormatterAgent - Format final response
5. OrchestratorAgent - Coordinate all agents
"""

import os
import re
import json
import requests
from typing import List, Dict, Optional
from .faiss_bom_store import get_faiss_store


SE_CRED = {'login': 'emerson_api', 'api_key': 'Em$809@rRt2'}

_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_CHAT_URL = f"{_OLLAMA_BASE}/api/chat"
OLLAMA_INTENT_MODEL = "llama3.2:3b"
OLLAMA_REVIEW_MODEL = "gpt-oss:latest"


# ============= AGENT 0: Query Intent Agent =============
class QueryIntentAgent:
    """
    Uses Ollama LLM to understand the user's intent from a natural language query.
    Returns structured intent: how many parts to return, any filters, etc.
    Falls back to 'no limit, no filter' if the LLM is unavailable.
    """

    SYSTEM_PROMPT = (
        "You are a query intent parser for a BOM (Bill of Materials) system. "
        "Given a user query, extract the following as JSON and return ONLY the JSON object, "
        "no explanation:\n"
        "{\n"
        '  "limit": <integer or null>,       // number of parts requested; null means all\n'
        '  "want_all": <true or false>,       // true if user explicitly asks for all parts\n'
        '  "specific_parts": [<string>, ...], // any specific part numbers mentioned\n'
        '  "filters": {                       // any attribute filters mentioned\n'
        '    "manufacturer": <string or null>,\n'
        '    "description_contains": <string or null>\n'
        "  }\n"
        "}\n"
        "Examples:\n"
        '- "get me 5 part numbers" → {"limit":5,"want_all":false,"specific_parts":[],"filters":{"manufacturer":null,"description_contains":null}}\n'
        '- "get me all part numbers" → {"limit":null,"want_all":true,"specific_parts":[],"filters":{"manufacturer":null,"description_contains":null}}\n'
        '- "show details of 563969-472" → {"limit":1,"want_all":false,"specific_parts":["563969-472"],"filters":{"manufacturer":null,"description_contains":null}}\n'
        '- "get 10 capacitors from Yageo" → {"limit":10,"want_all":false,"specific_parts":[],"filters":{"manufacturer":"Yageo","description_contains":"capacitor"}}\n'
    )

    def parse(self, query: str) -> Dict:
        """Call Ollama LLM to parse query intent. Returns intent dict."""
        default = {"limit": None, "want_all": False, "specific_parts": [], "filters": {"manufacturer": None, "description_contains": None}}
        try:
            payload = {
                "model": OLLAMA_INTENT_MODEL,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": query}
                ],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 200}
            }
            resp = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=15)
            if resp.status_code != 200:
                print(f"[IntentAgent] Ollama returned HTTP {resp.status_code}, using defaults")
                return default

            content = resp.json().get("message", {}).get("content", "").strip()
            # Extract JSON block from response (handles markdown code fences)
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if not json_match:
                print(f"[IntentAgent] No JSON in response: {content[:200]}")
                return default

            intent = json.loads(json_match.group())
            print(f"[IntentAgent] Parsed intent: {intent}")
            return {**default, **intent}

        except Exception as e:
            print(f"[IntentAgent] Error: {e} — using defaults")
            return default


# ============= AGENT 0b: Response Reviewer Agent =============
class ResponseReviewerAgent:
    """
    Uses gpt-oss (via Ollama) to review the full result set against the original
    user query and return only the meaningful / matching rows.

    Steps:
      1. Build a compact summary of all excel rows (BOM No, Part, Manufacturer,
         YEOL, EOL, RoHS, Description) — keeps the LLM prompt manageable.
      2. Ask gpt-oss to identify which BOM row numbers satisfy the query.
      3. Filter excel_data, parts_found, and regenerate formatted_response.
    """

    SYSTEM_PROMPT = (
        "You are a BOM (Bill of Materials) data analyst. "
        "You will receive a user query and a numbered table of BOM parts. "
        "Your job: identify which rows satisfy the user's request and return ONLY a JSON object.\n\n"
        "Output format (no prose, no markdown, no code fences):\n"
        '{"matching_bom_nos": [<integer>, ...], "explanation": "<one sentence>"}\n\n'
        "Column meanings in the table:\n"
        "  YEOL      = numeric Years-to-EOL (e.g. 17.1 means 17 years left; blank = unknown)\n"
        "  Lifecycle = current status: 'Active' (still produced), 'Discontinued'/'EOL' (end-of-life), "
        "'Ltb' (last-time-buy), 'Unknown'\n"
        "  RoHS      = RoHS compliance: 'Yes' / 'Yes With Exemption' = compliant; 'No' = non-compliant\n\n"
        "Rules — follow ALL of these strictly:\n"
        "1. EOL/lifecycle numeric filters (e.g. 'EOL less than 2 years'): use the YEOL column. "
        "   A LOWER YEOL means sooner end-of-life. Rows where YEOL is blank have no data "
        "   — exclude unless the user asks for unknown/missing.\n"
        "2. Lifecycle status filters (e.g. 'active parts', 'discontinued'): use the Lifecycle column.\n"
        "3. Manufacturer filters: match the Manufacturer column case-insensitively.\n"
        "4. RoHS filters: use the RoHS column ('Yes'/'Yes With Exemption' = compliant, 'No' = non-compliant).\n"
        "5. Count limits (e.g. 'get 5 parts'): return exactly that many BOM Nos, first N.\n"
        "6. 'All parts' / no specific filter: return every BOM No in the table.\n"
        "7. CRITICAL: If ALL rows match, list every single BOM No. "
        "   Never return empty matching_bom_nos unless truly zero rows satisfy the filter.\n"
        "8. If genuinely no rows match, return: "
        '{"matching_bom_nos": [], "explanation": "No rows satisfy the query."}\n'
    )

    def review(self, query: str, excel_data: List[Dict],
               parts_found: List[Dict]) -> Dict:
        """
        Review and filter the result set using gpt-oss.

        Returns a dict with filtered keys:
          {
            "excel_data": [...],
            "parts_found": [...],
            "explanation": "...",
            "reviewed": True
          }
        Falls back to the original data if the LLM is unavailable or returns garbage.
        """
        fallback = {"excel_data": excel_data, "parts_found": parts_found,
                    "explanation": "", "reviewed": False}

        if not excel_data:
            return fallback

        try:
            # Build compact table — one line per row
            # Map EOL Yes/No → descriptive Lifecycle label so gpt-oss isn't confused
            def _lifecycle_label(eol_val: str) -> str:
                v = str(eol_val).strip()
                if v == "Yes":           return "Discontinued"
                if v == "No":            return "Active"
                if v == "Not Found in SE": return "Unknown"
                return v or "Unknown"  # Ltb, Unknown, blank, etc.

            lines = ["BOM_No | Requested_Part | Manufacturer | YEOL | Lifecycle | RoHS | Description"]
            for row in excel_data:
                lines.append(
                    f"{row.get('BOM No','')} | "
                    f"{row.get('Requested Part','')} | "
                    f"{row.get('Manufacturer Name','')} | "
                    f"{row.get('YEOL','')} | "
                    f"{_lifecycle_label(row.get('EOL',''))} | "
                    f"{row.get('RoHS','')} | "
                    f"{str(row.get('Description',''))[:80]}"
                )
            table_text = "\n".join(lines)

            user_message = (
                f"User query: {query}\n\n"
                f"BOM data table:\n{table_text}"
            )

            payload = {
                "model": OLLAMA_REVIEW_MODEL,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message}
                ],
                "stream": False,
                # gpt-oss is a thinking model: it spends many tokens on chain-of-thought
                # before producing the final answer.  Give it enough headroom.
                "options": {"temperature": 0, "num_predict": 4096}
            }

            print(f"[ReviewAgent] Sending {len(excel_data)} rows to {OLLAMA_REVIEW_MODEL} for review…")
            resp = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=300)

            if resp.status_code != 200:
                print(f"[ReviewAgent] Ollama HTTP {resp.status_code} — returning unfiltered data")
                return fallback

            msg_block = resp.json().get("message", {})
            # gpt-oss (thinking model): final answer → content; chain-of-thought → thinking.
            # If content is empty (model ran out of tokens mid-think), fall back to thinking.
            content = (msg_block.get("content") or "").strip()
            if not content:
                content = (msg_block.get("thinking") or "").strip()
                if content:
                    print("[ReviewAgent] content empty — reading from thinking field")

            done_reason = resp.json().get("done_reason", "")
            if done_reason == "length":
                print(f"[ReviewAgent] WARNING: model hit token limit (done_reason=length). "
                      "Try increasing num_predict or simplifying the prompt.")

            json_match = re.search(r'\{.*?\}', content, re.DOTALL)
            if not json_match:
                print(f"[ReviewAgent] No JSON found in response (len={len(content)}): {content[:400]}")
                return fallback

            parsed = json.loads(json_match.group())
            matching_nos = set(parsed.get("matching_bom_nos", []))
            explanation  = parsed.get("explanation", "")
            print(f"[ReviewAgent] LLM matched BOM nos: {sorted(matching_nos)}")
            print(f"[ReviewAgent] Explanation: {explanation}")

            # Safety: if LLM returns 0 matches but explanation doesn't say "no rows",
            # it likely hit a formatting bug — fall back to unfiltered data.
            if not matching_nos and "no rows" not in explanation.lower() and "no match" not in explanation.lower():
                print(f"[ReviewAgent] WARNING: 0 matches but explanation suggests rows exist — returning fallback")
                return fallback

            if not matching_nos:
                # LLM genuinely found nothing
                return {"excel_data": [], "parts_found": [],
                        "explanation": explanation, "reviewed": True}

            # Filter excel_data to matching BOM rows
            filtered_excel = [r for r in excel_data if r.get("BOM No") in matching_nos]

            print(f"[ReviewAgent] Filtered: {len(filtered_excel)} excel rows kept (from {len(excel_data)})")

            # parts_found keys don't map 1:1 to excel BOM No — keep original list;
            # the frontend primarily uses excel_data for display and download.
            return {
                "excel_data":  filtered_excel,
                "parts_found": parts_found,   # keep original, excel_data is the source of truth
                "explanation": explanation,
                "reviewed":    True,
            }

        except Exception as e:
            print(f"[ReviewAgent] Error: {e} — returning unfiltered data")
            import traceback; traceback.print_exc()
            return fallback


# ============= AGENT 1: Part Number Extractor =============
class PartNumberExtractorAgent:
    """Extracts part numbers from user queries"""
    
    def extract(self, text: str) -> List[str]:
        """Extract part numbers using regex patterns"""
        
        print(f"[PartExtractor] Extracting from: {text}")
        
        patterns = [
            # ERAA/ERSA part numbers (e.g., ERAA26038, ERSA03316)
            r'\bERSA\d{5}\b',
            r'\bERAA\d{5}\b',
            
            # JT-prefix part numbers (e.g., JT106105)
            r'\bJT\d{5,}\b',
            
            # Alphanumeric with dashes — Emerson-specific
            r'\b[A-Z]{2,}-\d{3,}-\d{4,}-\d{1,}\b',      # CMP-001-2490-4
            r'\b\d{2,}[A-Z]\d{4,}-\d{4}\b',             # 42G2011-0030
            r'\b\d{2,}[A-Z]\d{4,}\b',                   # 42G2011

            # Numeric with dashes
            r'\b\d{6,}-\d{3,}\b',                       # 563969-472, 556112-224

            # Generic alphanumeric with dashes (6+ chars total, both sides)
            r'\b[A-Z0-9]{4,}-[A-Z0-9]{3,}\b',           # ABC123-456, 560325-020

            # Standard industry MPN formats
            r'\b[A-Z]{2,6}\d{3,}[A-Z0-9\-]{2,}\b',      # LM358N, SN74HC00N, TLV74333PDBVR
            r'\b[A-Z]\d{2,}[A-Z][A-Z0-9]{3,}\b',        # B72214S0351K101, C907U102
            r'\b[A-Z]{2,}\d{4,}[A-Z]{1,}\b',            # AD7689B, MAX6104E, TPS7A39

            # Pure alphanumeric (6+ chars, mixed letters and digits)
            r'\b[A-Z]{1,5}\d{5,}[A-Z0-9]*\b',           # TLV74333, MC34063AP
        ]
        
        found = set()
        text_upper = text.upper()
        
        for pattern in patterns:
            matches = re.findall(pattern, text_upper)
            for match in matches:
                if re.search(r'\d', match):
                    found.add(match)
        
        result = list(found)
        print(f"[PartExtractor] Extracted: {result}")
        return result


# ============= AGENT 2: FAISS Search Agent =============
class FAISSSearchAgent:
    """Searches FAISS index for part information"""
    
    def __init__(self):
        self.store = get_faiss_store()
    
    def search(self, query: str, part_numbers: List[str] = None) -> List[Dict]:
        """
        Search for parts in FAISS index
        
        Args:
            query: User's query
            part_numbers: Specific part numbers to search for
        
        Returns:
            List of part data dictionaries
        """
        
        print(f"[FAISSSearch] Searching for: {query}")
        
        results = []
        
        if part_numbers:
            # Exact search by part number
            for part_num in part_numbers:
                part_data = self.store.search_by_part_number(part_num)
                if part_data:
                    results.append(part_data)
                    print(f"[FAISSSearch] ✓ Found exact match: {part_num}")
                else:
                    print(f"[FAISSSearch] ✗ Not found: {part_num}")
            
            # If no exact matches, try semantic search with distance threshold
            if not results:
                print("[FAISSSearch] No exact matches, trying semantic search...")
                semantic_results = self.store.search(query, top_k=5)
                # Filter out low-relevance results — L2 distance > 200 means unrelated
                filtered = [r for r in semantic_results if r.get('distance', 9999) < 200]
                if filtered:
                    results.extend(filtered)
                    print(f"[FAISSSearch] Semantic fallback: {len(filtered)} results within distance threshold")
                else:
                    print("[FAISSSearch] Semantic fallback results too distant (>200), discarding")
        else:
            # Semantic search with distance threshold
            semantic_results = self.store.search(query, top_k=10)
            results = [r for r in semantic_results if r.get('distance', 9999) < 200]
            if len(results) < len(semantic_results):
                print(f"[FAISSSearch] Filtered {len(semantic_results) - len(results)} low-relevance results")
        
        print(f"[FAISSSearch] Found {len(results)} results")
        return results


# ============= AGENT 3: SiliconExpert API Agent =============
class SiliconExpertAgent:
    """Calls SiliconExpert API with ALL manufacturer-MPN pairs"""
    
    def __init__(self):
        self.session = None
    
    def authenticate(self) -> bool:
        """Authenticate with SiliconExpert API"""
        try:
            session = requests.Session()
            params = f'login={SE_CRED["login"]}&apiKey={SE_CRED["api_key"]}'
            auth_endpoint = f'https://api.siliconexpert.com/ProductAPI/search/authenticateUser?{params}'
            
            response = session.post(auth_endpoint, verify=False)
            
            if response.status_code == 200:
                print("[SiliconExpert] ✓ Authenticated")
                self.session = session
                return True
            else:
                print(f"[SiliconExpert] ✗ Auth failed: {response.status_code}")
                return False
        
        except Exception as e:
            print(f"[SiliconExpert] ✗ Auth error: {e}")
            return False
    
    def search_all_manufacturers(self, parts_data: List[Dict]) -> Optional[Dict]:
        """
        Search SiliconExpert with ALL manufacturer-MPN pairs for all parts
        
        Args:
            parts_data: List of part dictionaries with 'manufacturers' field
        
        Returns:
            Combined API response or None
        """
        
        if not self.session:
            if not self.authenticate():
                return None
        
        # Build list of ALL manufacturer-MPN pairs from ALL parts
        all_pairs = []
        
        for part_data in parts_data:
            manufacturers = part_data.get('manufacturers', [])
            
            print(f"\n[SiliconExpert] Part: {part_data.get('part_number')}")
            print(f"[SiliconExpert] Found {len(manufacturers)} manufacturer options:")
            
            for mfr_data in manufacturers:
                manufacturer = mfr_data.get('manufacturer')
                mpn = mfr_data.get('mpn')
                preference = mfr_data.get('preference', 1)
                
                if manufacturer and mpn:
                    all_pairs.append({
                        "partNumber": mpn,
                        "manufacturer": manufacturer,
                        "bom_part_number": part_data.get('part_number'),
                        "preference": preference
                    })
                    
                    print(f"  [{preference}] {manufacturer} : {mpn}")
        
        if not all_pairs:
            print("[SiliconExpert] No manufacturer-MPN pairs found")
            return None
        
        print(f"\n[SiliconExpert] Querying API with {len(all_pairs)} manufacturer-MPN pairs...")
        
        # Batch into chunks of 20 pairs to stay within URL/API length limits
        BATCH_SIZE = 20
        batches = [all_pairs[i:i + BATCH_SIZE] for i in range(0, len(all_pairs), BATCH_SIZE)]
        print(f"[SiliconExpert] Splitting into {len(batches)} batch(es) of up to {BATCH_SIZE} pairs")
        
        combined_results = []
        combined_metadata = []
        last_response_data = None
        
        for batch_idx, batch in enumerate(batches):
            try:
                api_input = [
                    {"partNumber": pair["partNumber"], "manufacturer": pair["manufacturer"]}
                    for pair in batch
                ]
                parts_json = json.dumps(api_input)
                endpoint = f'https://api.siliconexpert.com/ProductAPI/search/listPartSearch?partNumber={parts_json}'
                
                response = self.session.get(endpoint, verify=False)
                
                if response.status_code == 200:
                    data = response.json()
                    last_response_data = data
                    status = data.get('Status', {})
                    if status.get('Success') == 'true' or status.get('Code') != '3':
                        # SE API structure: data['Result']['PartData'] = list of PartData entries
                        result_block = data.get('Result', {})
                        if isinstance(result_block, dict):
                            part_data = result_block.get('PartData', [])
                            if isinstance(part_data, dict):
                                part_data = [part_data]
                            combined_results.extend(part_data)
                            print(f"[SiliconExpert] Batch {batch_idx+1}/{len(batches)} ✓ ({len(part_data)} PartData entries)")
                        else:
                            print(f"[SiliconExpert] Batch {batch_idx+1}/{len(batches)} ✓ (Result not a dict: {type(result_block)})")
                        combined_metadata.extend(batch)
                    else:
                        print(f"[SiliconExpert] Batch {batch_idx+1}/{len(batches)} ✗ no results: {status.get('Message')}")
                else:
                    print(f"[SiliconExpert] Batch {batch_idx+1}/{len(batches)} ✗ HTTP {response.status_code}")
            except Exception as e:
                print(f"[SiliconExpert] Batch {batch_idx+1}/{len(batches)} ✗ Error: {e}")
                import traceback
                traceback.print_exc()
        
        if not combined_metadata:
            print("[SiliconExpert] ✗ All batches failed or returned no results")
            return None
        
        # Merge all PartData entries back into the SE API structure the formatter expects:
        # { "Status": {...}, "Result": { "PartData": [...all entries...] }, "_query_metadata": [...] }
        if last_response_data is None:
            return None
        merged = dict(last_response_data)
        result_block = merged.get('Result', {})
        if isinstance(result_block, dict):
            merged['Result'] = dict(result_block)
            merged['Result']['PartData'] = combined_results
        else:
            merged['Result'] = {'PartData': combined_results}
        merged['_query_metadata'] = combined_metadata
        print(f"[SiliconExpert] ✓ Total combined PartData: {len(combined_results)} across {len(batches)} batch(es)")
        return merged


# ============= AGENT 4: Response Formatter Agent =============
class ResponseFormatterAgent:
    """Formats final response with all manufacturer options"""
    
    def format(self, parts_found: List[Dict], api_data: Optional[Dict] = None) -> str:
        """Format human-readable response for UI display"""
        
        output = []
        
        # Header
        output.append("=" * 70)
        output.append(f"📋 BOM QUERY RESULTS - Found {len(parts_found)} Part(s)")
        output.append("=" * 70)
        output.append("")
        
        for idx, part in enumerate(parts_found, 1):
            # Part header
            output.append(f"🔹 PART {idx}: {part.get('part_number')}")
            output.append("-" * 70)
            
            # Basic info
            if part.get('description'):
                desc = part.get('description', '').replace('\n', ' ').strip()
                output.append(f"   Description: {desc}")
            
            if part.get('quantity'):
                output.append(f"   Quantity: {part.get('quantity')}")
            
            if part.get('designators'):
                output.append(f"   Designators: {part.get('designators')}")
            
            output.append("")
            
            # Manufacturers
            manufacturers = part.get('manufacturers', [])
            output.append(f"   📦 Available Manufacturers: {len(manufacturers)}")
            output.append("")
            
            for mfr_data in manufacturers:
                preference = mfr_data.get('preference', 1)
                mfr = mfr_data.get('manufacturer')
                mpn = mfr_data.get('mpn')
                
                if preference == 1:
                    output.append(f"      ✓ [PRIMARY] {mfr}")
                else:
                    output.append(f"      • [ALT-{preference}] {mfr}")
                
                output.append(f"        MPN: {mpn}")
            
            output.append("")
            output.append("")
        
        # SiliconExpert summary
        if api_data and isinstance(api_data, dict):
            result = api_data.get('Result', {})
            if isinstance(result, dict):
                part_data_list = result.get('PartData', [])
                
                # SE API returns a dict for single results, list for multiple
                if isinstance(part_data_list, dict):
                    part_data_list = [part_data_list]
                
                # Ensure part_data_list is actually a list of dicts
                if isinstance(part_data_list, list) and part_data_list:
                    valid_parts = [
                        p for p in part_data_list 
                        if isinstance(p, dict) and (p.get('PartList') or {}).get('PartDto')
                    ]
                    
                    if valid_parts:
                        output.append("=" * 70)
                        output.append(f"🌐 SILICONEXPERT DATA - {len(valid_parts)} Part(s) Retrieved")
                        output.append("=" * 70)
                        output.append("")
                        
                        for api_part_entry in valid_parts:
                            dto = api_part_entry['PartList']['PartDto']
                            
                            output.append(f"   Part: {dto.get('PartNumber', 'Unknown')}")
                            output.append(f"   Manufacturer: {dto.get('Manufacturer', 'Unknown')}")
                            
                            # Key information
                            lifecycle = dto.get('Lifecycle', 'Unknown')
                            rohs = dto.get('RoHS', 'Unknown')
                            yeol = dto.get('YEOL', 'N/A')
                            
                            # EOL warning
                            eol_icon = "⚠️" if str(yeol).replace('.', '').isdigit() and float(yeol) < 10 else "✓"
                            
                            output.append(f"   Lifecycle: {lifecycle}")
                            output.append(f"   RoHS: {rohs}")
                            output.append(f"   {eol_icon} Years to EOL: {yeol}")
                            output.append("")
        
        output.append("=" * 70)
        output.append("✓ Query Complete")
        output.append("=" * 70)
        
        return "\n".join(output)
    
    def prepare_excel_data(self, parts_found: List[Dict], api_data: Optional[Dict] = None) -> List[Dict]:
        """
        Prepare structured data for Excel export
        
        Returns:
            List of dictionaries with Excel-ready data
        """
        
        excel_rows = []
        bom_index = 1
        
        # Validate API data structure
        has_valid_api_data = False
        part_data_list = []
        
        if api_data and isinstance(api_data, dict):
            result = api_data.get('Result', {})
            if isinstance(result, dict):
                part_data_list = result.get('PartData', [])
                # SE API returns a dict for single results, list for multiple
                if isinstance(part_data_list, dict):
                    part_data_list = [part_data_list]
                    result['PartData'] = part_data_list
                if isinstance(part_data_list, list) and part_data_list:
                    has_valid_api_data = True
        
        if not has_valid_api_data:
            # No API data - create basic rows from FAISS data
            for part in parts_found:
                manufacturers = part.get('manufacturers', [])
                
                for mfr_data in manufacturers:
                    excel_rows.append({
                        "BOM No": bom_index,
                        "Requested Part": part.get('part_number', ''),
                        "ComID": "",
                        "Manufacturer Part Number": mfr_data.get('mpn', ''),
                        "Manufacturer Name": mfr_data.get('manufacturer', ''),
                        "PlName": "",
                        "Description": part.get('description', '').replace('\n', ' ').strip(),
                        "Datasheet": "",
                        "EOL": "Unknown",
                        "RoHS": "Unknown",
                        "RoHS Version": "",
                        "TaxonomyPath": "",
                        "TaxonomyPathID": "",
                        "YEOL": "",
                        "Preference": mfr_data.get('preference', 1)
                    })
                    bom_index += 1
            
            return excel_rows
        
        # Process SiliconExpert API data
        # Build a lookup: requested_part+manufacturer → BOM part dict (for description fallback)
        bom_lookup = {}
        for p in parts_found:
            for mfr_data in p.get('manufacturers', []):
                key = (mfr_data.get('mpn', '').strip(), mfr_data.get('manufacturer', '').strip())
                bom_lookup[key] = (p, mfr_data)

        for api_part_entry in part_data_list:
            if not isinstance(api_part_entry, dict):
                continue

            requested_part = api_part_entry.get('RequestedPart', '')
            requested_mfr  = api_part_entry.get('RequestedManufacturer', '')

            # Get preference from metadata
            preference = 1
            metadata = api_data.get('_query_metadata', [])
            for meta in metadata:
                if (meta.get('partNumber') == requested_part and
                        meta.get('manufacturer') == requested_mfr):
                    preference = meta.get('preference', 1)
                    break

            dto = (api_part_entry.get('PartList') or {}).get('PartDto')

            if dto:
                # SE matched this MPN — use full SE data
                com_id        = str(dto.get('ComID', '')).strip()
                part_number   = str(dto.get('PartNumber', '')).strip()
                manufacturer  = str(dto.get('Manufacturer', '')).strip()
                pl_name       = str(dto.get('PlName', '')).strip()
                description   = str(dto.get('Description', '')).strip()
                datasheet     = str(dto.get('Datasheet', '')).strip()
                rohs_version  = str(dto.get('RoHSVersion', '')).strip()
                taxonomy_path    = str(dto.get('TaxonomyPath', '')).strip()
                taxonomy_path_id = str(dto.get('TaxonomyPathID', '')).strip()
                yeol = str(dto.get('YEOL', '')).strip()

                lifecycle = str(dto.get('Lifecycle', '')).lower().strip()
                if any(x in lifecycle for x in ['eol', 'obsolete', 'discontinued']):
                    eol_status = "Yes"
                elif any(x in lifecycle for x in ['active', 'production']):
                    eol_status = "No"
                else:
                    eol_status = lifecycle.title() if lifecycle else "Unknown"

                rohs_raw = str(dto.get('RoHS', '')).lower().strip()
                if rohs_raw in ['yes', 'true'] or 'compliant' in rohs_raw:
                    rohs_status = "Yes"
                elif rohs_raw in ['no', 'false'] or 'non-compliant' in rohs_raw:
                    rohs_status = "No"
                else:
                    rohs_status = rohs_raw.title() if rohs_raw else "Unknown"
            else:
                # SE had no match for this MPN — keep BOM description, leave SE fields blank
                bom_part, bom_mfr = bom_lookup.get((requested_part, requested_mfr), ({}, {}))
                com_id = part_number = pl_name = datasheet = ""
                rohs_version = taxonomy_path = taxonomy_path_id = yeol = ""
                manufacturer  = requested_mfr
                description   = str(bom_part.get('description', '')).replace('\n', ' ').strip()
                eol_status    = "Not Found in SE"
                rohs_status   = "Unknown"
                print(f"[Formatter] SE had no match for {requested_part} / {requested_mfr} — using BOM data")
            
            excel_rows.append({
                "BOM No": bom_index,
                "Requested Part": requested_part,
                "ComID": com_id,
                "Manufacturer Part Number": part_number,
                "Manufacturer Name": manufacturer,
                "PlName": pl_name,
                "Description": description,
                "Datasheet": datasheet,
                "EOL": eol_status,
                "RoHS": rohs_status,
                "RoHS Version": rohs_version,
                "TaxonomyPath": taxonomy_path,
                "TaxonomyPathID": taxonomy_path_id,
                "YEOL": yeol,
                "Preference": preference
            })
            
            bom_index += 1
        
        return excel_rows


# ============= AGENT 5: Orchestrator Agent =============
class OrchestratorAgent:
    """Coordinates all agents to process queries"""
    
    def __init__(self):
        self.extractor = PartNumberExtractorAgent()
        self.searcher = FAISSSearchAgent()
        self.api_agent = SiliconExpertAgent()
        self.formatter = ResponseFormatterAgent()
    
    def process_query(self, query: str) -> Dict:
        """
        Main query processing pipeline
        
        Returns:
            {
                "success": True/False,
                "parts_found": [...],
                "api_data": {...},
                "formatted_response": "...",  # Human-readable for UI
                "excel_data": [...],           # Structured data for Excel download
                "message": "..."
            }
        """
        
        print("\n" + "="*60)
        print("[Orchestrator] Starting multi-agent query processing")
        print("="*60)
        
        # Step 1: Extract part numbers
        part_numbers = self.extractor.extract(query)
        
        if not part_numbers:
            print("[Orchestrator] No part numbers found, trying semantic search...")
        
        # Step 2: Search FAISS
        parts_found = self.searcher.search(query, part_numbers)
        
        if not parts_found:
            return {
                "success": False,
                "parts_found": [],
                "api_data": None,
                "formatted_response": "No parts found matching your query. Please upload BOM documents first.",
                "excel_data": [],
                "message": "No parts found in database"
            }
        
        print(f"\n[Orchestrator] Found {len(parts_found)} part(s) in FAISS")
        
        # Count total manufacturer options
        total_mfr_options = sum(len(p.get('manufacturers', [])) for p in parts_found)
        print(f"[Orchestrator] Total manufacturer options: {total_mfr_options}")
        
        # Step 3: Query SiliconExpert API with ALL manufacturers
        api_data = self.api_agent.search_all_manufacturers(parts_found)
        
        # Step 4: Format response for UI display
        formatted_response = self.formatter.format(parts_found, api_data)
        
        # Step 5: Prepare Excel-ready data
        excel_data = self.formatter.prepare_excel_data(parts_found, api_data)
        
        print(f"[Orchestrator] Prepared {len(excel_data)} Excel rows")
        
        # Step 6: Return result
        return {
            "success": True,
            "parts_found": parts_found,
            "api_data": api_data,
            "formatted_response": formatted_response,
            "excel_data": excel_data,
            "message": f"Found {len(parts_found)} part(s) with {total_mfr_options} manufacturer option(s)"
        }


# Singleton instance
_orchestrator_instance = None

def get_orchestrator() -> OrchestratorAgent:
    """Get singleton orchestrator instance"""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = OrchestratorAgent()
    return _orchestrator_instance


if __name__ == "__main__":
    # Test
    orchestrator = OrchestratorAgent()
    
    result = orchestrator.process_query("What is part 563969-472?")
    
    print("\n" + "="*60)
    print("RESULT:")
    print("="*60)
    print(result.get('formatted_response'))
