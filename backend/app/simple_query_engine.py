"""
Simple Query Engine - Handle user queries and call SiliconExpert API

Clean workflow:
1. Extract part numbers from user query
2. Lookup in BOM store
3. Get MPN + Manufacturer
4. Call SiliconExpert API
5. Format response
"""

import re
import json
import requests
from typing import List, Dict, Optional
from .simple_bom_store import get_store


SE_CRED = {'login': 'emerson_api', 'api_key': 'Em$809@rRt2'}


class SimpleQueryEngine:
    """Simple and precise query engine"""
    
    def __init__(self):
        self.store = get_store()
        self.se_session = None
    
    def process_query(self, query: str) -> Dict:
        """
        Process user query and return structured response.
        
        Returns:
            {
                "success": True/False,
                "parts_found": [...],
                "api_data": {...},
                "message": "..."
            }
        """
        
        print(f"\n[Query Engine] Processing: {query}")
        
        # Step 1: Extract part numbers from query
        part_numbers = self._extract_part_numbers(query)
        
        if not part_numbers:
            return {
                "success": False,
                "message": "No part numbers found in query. Please include a part number."
            }
        
        print(f"[Query Engine] Extracted part numbers: {part_numbers}")
        
        # Step 2: Lookup parts in BOM store
        parts_data = []
        for part_num in part_numbers:
            part = self.store.get_part(part_num)
            if part:
                parts_data.append(part)
                print(f"[Query Engine] ✓ Found {part_num} in database")
            else:
                print(f"[Query Engine] ✗ {part_num} not found in database")
        
        if not parts_data:
            return {
                "success": False,
                "parts_found": [],
                "message": f"Part(s) {', '.join(part_numbers)} not found in database. Please upload the BOM document first."
            }
        
        # Step 3: Prepare MPN + Manufacturer pairs for API
        api_input = []
        for part in parts_data:
            mpn = part.get('mpn')
            manufacturer = part.get('manufacturer')
            
            if mpn and manufacturer:
                api_input.append({
                    "partNumber": mpn,
                    "manufacturer": manufacturer
                })
        
        if not api_input:
            return {
                "success": False,
                "parts_found": parts_data,
                "message": "Parts found but missing manufacturer/MPN data. Cannot query SiliconExpert API."
            }
        
        print(f"[Query Engine] Calling SiliconExpert API with {len(api_input)} parts")
        
        # Step 4: Call SiliconExpert API
        api_response = self._call_siliconexpert_api(api_input)
        
        # Step 5: Format response
        if api_response:
            return {
                "success": True,
                "parts_found": parts_data,
                "api_data": api_response,
                "message": f"Found {len(parts_data)} part(s) with detailed information from SiliconExpert."
            }
        else:
            return {
                "success": True,  # Still success because we found parts in DB
                "parts_found": parts_data,
                "api_data": None,
                "message": f"Found {len(parts_data)} part(s) in database, but SiliconExpert API query failed."
            }
    
    def _extract_part_numbers(self, text: str) -> List[str]:
        """Extract part numbers from text using regex patterns"""
        
        patterns = [
            # Alphanumeric with dashes
            r'\b[A-Z]{2,}-\d{3,}-\d{4,}-\d{1,}\b',      # CMP-001-2490-4
            r'\b\d{2,}[A-Z]\d{4,}-\d{4}\b',             # 42G2011-0030
            r'\b\d{2,}[A-Z]\d{4,}\b',                   # 42G2011
            
            # Numeric with dashes
            r'\b\d{6,}-\d{3,}\b',                       # 563969-472, 556112-224
            
            # Generic alphanumeric
            r'\b[A-Z0-9]{6,}-[A-Z0-9]{3,}\b',           # ABC123-456
        ]
        
        found = set()
        text_upper = text.upper()
        
        for pattern in patterns:
            matches = re.findall(pattern, text_upper)
            for match in matches:
                # Validate it has digits
                if re.search(r'\d', match):
                    found.add(match)
        
        return list(found)
    
    def _call_siliconexpert_api(self, parts: List[Dict[str, str]]) -> Optional[Dict]:
        """
        Call SiliconExpert API with list of {"partNumber": MPN, "manufacturer": NAME}
        
        Args:
            parts: [{"partNumber": "C1210C472KARGC7800", "manufacturer": "KEMET"}, ...]
        
        Returns:
            API response dict or None
        """
        
        # Authenticate if needed
        if not self.se_session:
            if not self._authenticate():
                return None
        
        try:
            parts_json = json.dumps(parts)
            endpoint = f'https://api.siliconexpert.com/ProductAPI/search/listPartSearch?partNumber={parts_json}'
            
            print(f"[SiliconExpert API] Calling with {len(parts)} parts")
            
            response = self.se_session.get(endpoint, verify=False)
            
            if response.status_code == 200:
                data = response.json()
                
                # Check if successful
                status = data.get('Status', {})
                if status.get('Success') == 'true' or status.get('Code') != '3':
                    print(f"[SiliconExpert API] ✓ Success")
                    return data
                else:
                    print(f"[SiliconExpert API] ✗ No results: {status.get('Message')}")
                    return None
            else:
                print(f"[SiliconExpert API] ✗ HTTP {response.status_code}")
                return None
        
        except Exception as e:
            print(f"[SiliconExpert API] ✗ Error: {e}")
            return None
    
    def _authenticate(self) -> bool:
        """Authenticate with SiliconExpert API"""
        
        try:
            session = requests.Session()
            params = f'login={SE_CRED["login"]}&apiKey={SE_CRED["api_key"]}'
            auth_endpoint = f'https://api.siliconexpert.com/ProductAPI/search/authenticateUser?{params}'
            
            response = session.post(auth_endpoint, verify=False)
            
            if response.status_code == 200:
                print("[SiliconExpert API] ✓ Authenticated")
                self.se_session = session
                return True
            else:
                print(f"[SiliconExpert API] ✗ Auth failed: {response.status_code}")
                return False
        
        except Exception as e:
            print(f"[SiliconExpert API] ✗ Auth error: {e}")
            return False


def format_response_for_user(result: Dict) -> str:
    """Format the query result into readable text for the user"""
    
    if not result.get("success"):
        return result.get("message", "Query failed")
    
    parts_found = result.get("parts_found", [])
    api_data = result.get("api_data")
    
    output = []
    
    # Show parts from database
    output.append(f"**Found {len(parts_found)} part(s) in database:**\n")
    
    for part in parts_found:
        output.append(f"**Part Number:** {part.get('part_number')}")
        output.append(f"- **Manufacturer:** {part.get('manufacturer', 'N/A')}")
        output.append(f"- **MPN:** {part.get('mpn', 'N/A')}")
        output.append(f"- **Description:** {part.get('description', 'N/A')}")
        output.append(f"- **Quantity:** {part.get('quantity', 'N/A')}")
        
        if part.get('designators'):
            output.append(f"- **Designators:** {part.get('designators')}")
        
        output.append("")
    
    # Show API data if available
    if api_data:
        output.append("\n**SiliconExpert Data:**")
        
        # Extract relevant fields from API response
        parts_list = api_data.get('PartList', [])
        
        for api_part in parts_list:
            output.append(f"\n**{api_part.get('PartNumber', 'Unknown')}:**")
            
            # Lifecycle status
            lifecycle = api_part.get('LifecycleStatus', 'Unknown')
            output.append(f"- **Lifecycle:** {lifecycle}")
            
            # Lead time
            lead_time = api_part.get('LeadTime', 'N/A')
            if lead_time != 'N/A':
                output.append(f"- **Lead Time:** {lead_time} weeks")
            
            # Compliance
            rohs = api_part.get('RoHS', 'Unknown')
            output.append(f"- **RoHS:** {rohs}")
            
            # Availability
            stock = api_part.get('StockLevel', 'N/A')
            if stock != 'N/A':
                output.append(f"- **Stock Level:** {stock}")
    
    return "\n".join(output)


# Singleton instance
_engine_instance = None

def get_query_engine() -> SimpleQueryEngine:
    """Get singleton query engine instance"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = SimpleQueryEngine()
    return _engine_instance


if __name__ == "__main__":
    # Test
    engine = SimpleQueryEngine()
    
    # Test query
    result = engine.process_query("What is part number 563969-472?")
    
    print("\n=== Result ===")
    print(json.dumps(result, indent=2))
    
    print("\n=== Formatted ===")
    print(format_response_for_user(result))
