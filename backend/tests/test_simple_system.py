"""
Test Script for Simple BOM Query System

Run this to test the new simplified architecture:
1. Upload a BOM document
2. Query for part information
3. Check stats
"""

import requests
import json
import sys
import os


BASE_URL = "http://localhost:8000"


def test_health():
    """Test if server is running"""
    print("=" * 60)
    print("Testing server health...")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/health")
        data = response.json()
        print(f"✓ Server is running")
        print(f"  Version: {data.get('version')}")
        print(f"  Status: {data.get('status')}")
        return True
    except Exception as e:
        print(f"✗ Server not reachable: {e}")
        print("\nPlease start the server first:")
        print("  uvicorn app.main_simple:app --reload --port 8000")
        return False


def test_upload(file_path):
    """Test document upload"""
    print("\n" + "=" * 60)
    print(f"Testing document upload: {file_path}")
    print("=" * 60)
    
    if not os.path.exists(file_path):
        print(f"✗ File not found: {file_path}")
        return False
    
    try:
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f, 'application/pdf')}
            response = requests.post(f"{BASE_URL}/upload", files=files)
        
        data = response.json()
        
        if data.get('success'):
            print(f"✓ Upload successful")
            print(f"  File: {data.get('filename')}")
            print(f"  Parts extracted: {data.get('parts_extracted')}")
            print(f"  Message: {data.get('message')}")
            return True
        else:
            print(f"✗ Upload failed")
            print(f"  Message: {data.get('message')}")
            return False
    
    except Exception as e:
        print(f"✗ Upload error: {e}")
        return False


def test_query(query_text):
    """Test query"""
    print("\n" + "=" * 60)
    print(f"Testing query: {query_text}")
    print("=" * 60)
    
    try:
        response = requests.post(
            f"{BASE_URL}/query",
            json={"query": query_text}
        )
        
        data = response.json()
        
        if data.get('success'):
            print(f"✓ Query successful")
            
            parts_found = data.get('parts_found', [])
            print(f"\nParts found: {len(parts_found)}")
            
            for part in parts_found:
                print(f"\n  Part Number: {part.get('part_number')}")
                print(f"  Manufacturer: {part.get('manufacturer')}")
                print(f"  MPN: {part.get('mpn')}")
                print(f"  Description: {part.get('description', 'N/A')[:60]}...")
                print(f"  Quantity: {part.get('quantity', 'N/A')}")
            
            # Show API data if available
            api_data = data.get('api_data')
            if api_data:
                print(f"\n  SiliconExpert API called: ✓")
                status = api_data.get('Status', {})
                print(f"  API Status: {status.get('Message', 'N/A')}")
            else:
                print(f"\n  SiliconExpert API called: ✗")
            
            print(f"\n{'-' * 60}")
            print("Formatted Response:")
            print(f"{'-' * 60}")
            print(data.get('formatted_response', 'N/A'))
            
            return True
        else:
            print(f"✗ Query failed")
            print(f"  Message: {data.get('message')}")
            return False
    
    except Exception as e:
        print(f"✗ Query error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stats():
    """Test stats endpoint"""
    print("\n" + "=" * 60)
    print("Testing database stats")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/stats")
        data = response.json()
        
        print(f"Database Statistics:")
        print(f"  Total parts: {data.get('total_parts')}")
        print(f"  Parts with manufacturer: {data.get('parts_with_manufacturer')}")
        print(f"  Parts with MPN: {data.get('parts_with_mpn')}")
        print(f"  Unique manufacturers: {data.get('unique_manufacturers')}")
        
        manufacturers = data.get('manufacturers_list', [])
        if manufacturers:
            print(f"\n  Manufacturers:")
            for mfr in manufacturers[:10]:  # Show first 10
                print(f"    - {mfr}")
            if len(manufacturers) > 10:
                print(f"    ... and {len(manufacturers) - 10} more")
        
        return True
    
    except Exception as e:
        print(f"✗ Stats error: {e}")
        return False


def test_get_parts():
    """Test getting all parts"""
    print("\n" + "=" * 60)
    print("Testing get all parts")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/parts")
        data = response.json()
        
        total = data.get('total')
        parts = data.get('parts', [])
        
        print(f"Total parts in database: {total}")
        print(f"Showing first {min(5, len(parts))} parts:\n")
        
        for idx, part in enumerate(parts[:5], 1):
            print(f"{idx}. {part.get('part_number')} | {part.get('manufacturer')} | {part.get('mpn')}")
        
        return True
    
    except Exception as e:
        print(f"✗ Get parts error: {e}")
        return False


def main():
    """Run all tests"""
    
    print("\n" + "=" * 60)
    print("Simple BOM Query System - Test Suite")
    print("=" * 60)
    
    # Test 1: Health check
    if not test_health():
        return
    
    # Test 2: Stats
    test_stats()
    
    # Test 3: Get parts (if any exist)
    test_get_parts()
    
    # Test 4: Upload (if file path provided)
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if test_upload(file_path):
            # After upload, check stats again
            test_stats()
    else:
        print("\n" + "=" * 60)
        print("ℹ️  To test upload, run:")
        print("  python test_simple_system.py path/to/bom.pdf")
        print("=" * 60)
    
    # Test 5: Query (if query provided or use default)
    if len(sys.argv) > 2:
        query = sys.argv[2]
    else:
        # Try to query a part from the database
        try:
            response = requests.get(f"{BASE_URL}/parts")
            parts = response.json().get('parts', [])
            if parts:
                query = f"What is part {parts[0].get('part_number')}?"
                test_query(query)
            else:
                print("\n" + "=" * 60)
                print("ℹ️  To test query, run:")
                print('  python test_simple_system.py "" "What is part 563969-472?"')
                print("=" * 60)
        except:
            print("\n" + "=" * 60)
            print("ℹ️  No parts in database yet. Upload a document first.")
            print("=" * 60)
    
    print("\n" + "=" * 60)
    print("Test suite completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
