"""
Test the reindex endpoint
"""
import requests
import json

print("="*70)
print("TESTING REINDEX ENDPOINT")
print("="*70)

# Make sure server is running at http://localhost:8000
url = "http://localhost:8000/reindex"

print("\nSending POST request to /reindex...")
print(f"URL: {url}\n")

try:
    response = requests.post(url, timeout=120)  # 2 minute timeout
    
    if response.status_code == 200:
        result = response.json()
        
        print("✅ Request successful!\n")
        
        # Display summary
        if result.get('success'):
            print("SUCCESS: Reindexing completed")
            print("\nSummary:")
            summary = result.get('summary', {})
            print(f"  Documents processed: {summary.get('documents_processed')}")
            print(f"  Parts indexed: {summary.get('total_parts_indexed')}")
            print(f"  Manufacturer options: {summary.get('total_manufacturer_options')}")
            print(f"  Duration: {summary.get('duration_seconds'):.2f}s")
            
            print("\nDocument Details:")
            for doc in summary.get('parsing_details', []):
                print(f"  • {doc['filename']}: {doc['parts_extracted']} parts, {doc['manufacturers_extracted']} manufacturers")
            
            # Show logs
            print("\n" + "="*70)
            print("DETAILED LOGS:")
            print("="*70)
            logs = result.get('logs', [])
            for log in logs:
                print(log)
        else:
            print("FAILED: Reindexing failed")
            print(f"Message: {result.get('message')}")
            
    else:
        print(f"❌ Request failed with status code: {response.status_code}")
        print(f"Response: {response.text}")

except requests.exceptions.ConnectionError:
    print("❌ ERROR: Could not connect to server")
    print("Make sure the server is running:")
    print("  python -m uvicorn app.main_faiss:app --reload --port 8000")
    
except Exception as e:
    print(f"❌ ERROR: {e}")
