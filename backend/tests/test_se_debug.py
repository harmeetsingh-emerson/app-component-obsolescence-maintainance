"""Debug Silicon Expert API calls"""
import requests
import json
import urllib3
urllib3.disable_warnings()

SE_CRED = {'login': 'emerson_api', 'api_key': 'Em$809@rRt2'}

# Step 1: Authenticate
session = requests.Session()
login = SE_CRED["login"]
api_key = SE_CRED["api_key"]
auth_url = f'https://api.siliconexpert.com/ProductAPI/search/authenticateUser?login={login}&apiKey={api_key}'

print("Authenticating...")
r = session.post(auth_url, verify=False)
print(f"Auth status: {r.status_code}")
if r.status_code != 200:
    print(f"Auth failed: {r.text}")
    exit(1)
print("Authenticated OK\n")

# Test with single MPN (same way the pipeline sends it)
tests = [
    [{"partNumber": "LTST-C190KRKT", "manufacturer": "Lite-ON"}],
    [{"partNumber": "CRCW0402787RFKED", "manufacturer": "Vishay Dale"}],
    [{"partNumber": "CRCW0402787RFKED"}],
    [{"partNumber": "RK73H1ETTP1002F", "manufacturer": "KOA"}],
]

for pairs in tests:
    parts_json = json.dumps(pairs)
    url = f'https://api.siliconexpert.com/ProductAPI/search/listPartSearch?partNumber={parts_json}'
    r = session.get(url, verify=False)
    data = r.json()
    status = data.get('Status', {})
    result = data.get('Result', {})
    
    mpn = pairs[0]["partNumber"]
    mfr = pairs[0].get("manufacturer", "")
    print(f"\n=== {mpn} (mfr={mfr}) ===")
    print(f"Status Code: {status.get('Code')}, Message: {status.get('Message')}")
    
    if isinstance(result, dict):
        part_data = result.get('PartData', [])
        print(f"PartData type: {type(part_data).__name__}")
        
        if isinstance(part_data, dict):
            print(f"PartData keys: {list(part_data.keys())[:10]}")
            for k, v in list(part_data.items())[:3]:
                val_str = str(v)[:200] if not isinstance(v, dict) else json.dumps(v)[:200]
                print(f"  [{k}]: {val_str}")
        elif isinstance(part_data, list):
            print(f"PartData len: {len(part_data)}")
            for i in range(min(3, len(part_data))):
                entry = part_data[i]
                if isinstance(entry, str):
                    print(f"  [{i}] str: {entry[:200]}")
                elif isinstance(entry, dict):
                    print(f"  [{i}] dict keys: {list(entry.keys())}")
    
    # Also dump raw JSON (truncated)
    raw = json.dumps(data, indent=2)
    if len(raw) > 800:
        print(f"  Raw (truncated): {raw[:800]}...")
    else:
        print(f"  Raw: {raw}")
