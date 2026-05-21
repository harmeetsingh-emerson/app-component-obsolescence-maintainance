import requests
import time
import warnings
warnings.filterwarnings("ignore")

BASE = "http://localhost:8000"

queries = [
    {
        "label": "Specific part detail",
        "query": "get me details of part number 560325-020",
        "filename": "561668-001-BOM-CC_4PinTop.pdf",
    },
    {
        "label": "All RoHS compliant (want_all=True, should skip reviewer)",
        "query": "show me all RoHS compliant parts",
        "filename": "561668-001-BOM-CC_4PinTop.pdf",
    },
]

for q in queries:
    print("=" * 70)
    print(f"QUERY : {q['query']}")
    print(f"LABEL : {q['label']}")
    print("=" * 70)

    t0 = time.time()
    try:
        r = requests.post(f"{BASE}/query", json={"query": q["query"], "filename": q["filename"]}, timeout=600)
        elapsed = time.time() - t0
        d = r.json()
        print(f"HTTP {r.status_code}  ({elapsed:.1f}s)")
        print(f"message    : {d.get('message')}")
        excel = d.get("excel_data") or []
        print(f"excel rows : {len(excel)}")
        print()
        print("Formatted response:")
        for line in (d.get("formatted_response") or "(none)").splitlines()[:40]:
            print(f"  {line}")
        if excel:
            print()
            print("First excel row keys:")
            for k, v in list(excel[0].items()):
                print(f"  {k:30s}: {v}")
    except Exception as e:
        print(f"ERROR: {e}")
    print()
