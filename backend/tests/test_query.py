"""Test querying with full Silicon Expert response"""
import requests
import json

queries = ['ERAA26038', 'ERSA03316', 'ERAA26016']

for query in queries:
    print(f'\n{"="*70}')
    print(f'  QUERYING: {query}')
    print(f'{"="*70}')
    
    r = requests.post('http://localhost:8000/query', json={'query': query}, timeout=120)
    data = r.json()
    
    print(f'Success: {data.get("success")}')
    print(f'Message: {data.get("message")}')
    
    # Parts found from FAISS
    parts = data.get('parts_found', [])
    print(f'\n--- FAISS Results: {len(parts)} part(s) ---')
    for p in parts:
        pn = p["part_number"]
        src = p.get("source_file", "")
        mfrs = p.get("manufacturers", [])
        print(f'  Part: {pn} (from {src}) - {len(mfrs)} manufacturer(s)')
        for m in mfrs:
            print(f'    [{m.get("preference",1)}] {m["manufacturer"]}: {m["mpn"]}')
    
    # Silicon Expert API data
    api_data = data.get('api_data')
    if api_data and isinstance(api_data, dict):
        result = api_data.get('Result', {})
        status = api_data.get('Status', {})
        print(f'\n--- SiliconExpert API ---')
        print(f'  Status: {status.get("Message", "N/A")} (Code: {status.get("Code", "N/A")})')
        
        part_data_list = result.get('PartData', []) if isinstance(result, dict) else []
        valid = [p for p in part_data_list if isinstance(p, dict) and (p.get('PartList') or {}).get('PartDto')]
        print(f'  Parts with data: {len(valid)} / {len(part_data_list)}')
        
        for entry in part_data_list:
            if not isinstance(entry, dict):
                continue
            req_part = entry.get('RequestedPart', '?')
            dto = (entry.get('PartList') or {}).get('PartDto')
            if dto:
                print(f'\n  >> {req_part}')
                print(f'     MPN:          {dto.get("PartNumber", "?")}')
                print(f'     Manufacturer: {dto.get("Manufacturer", "?")}')
                print(f'     Lifecycle:    {dto.get("Lifecycle", "?")}')
                print(f'     RoHS:         {dto.get("RoHS", "?")}')
                print(f'     YEOL:         {dto.get("YEOL", "N/A")}')
                print(f'     Description:  {dto.get("Description", "?")[:80]}')
            else:
                print(f'\n  >> {req_part}: NO DATA')
    else:
        print(f'\n--- SiliconExpert API: No response ---')
    
    # Excel data
    excel = data.get('excel_data', [])
    print(f'\n--- Excel Rows: {len(excel)} ---')
    for row in excel:
        print(f'  [{row.get("BOM No")}] {row.get("Manufacturer Part Number")} | {row.get("Manufacturer Name")} | EOL={row.get("EOL")} | RoHS={row.get("RoHS")}')
    
    # Formatted response (first 15 lines)
    fmt = data.get('formatted_response', '')
    if fmt:
        lines = fmt.split('\n')
        print(f'\n--- Formatted Response (first 15 lines) ---')
        for line in lines[:15]:
            print(f'  {line}')
        if len(lines) > 15:
            print(f'  ... ({len(lines) - 15} more lines)')

print(f'\n{"="*70}')
print('ALL TESTS COMPLETE')
print(f'{"="*70}')
