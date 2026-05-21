"""
Test FAISS Multi-Agent System

Tests:
1. FAISS store initialization
2. Embedding generation
3. Multi-agent query processing
4. All manufacturers extraction
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.faiss_bom_store import get_faiss_store
from app.multi_agent_faiss import get_orchestrator


def test_faiss_store():
    """Test FAISS store operations"""
    
    print("\n" + "="*60)
    print("TEST 1: FAISS Store Initialization")
    print("="*60)
    
    try:
        store = get_faiss_store()
        print("✓ FAISS store initialized")
        
        # Get stats
        stats = store.get_stats()
        print(f"✓ Stats retrieved:")
        print(f"  - Total parts: {stats.get('total_parts')}")
        print(f"  - Total vectors: {stats.get('total_vectors')}")
        print(f"  - Manufacturer options: {stats.get('total_manufacturer_options')}")
        
        return True
    
    except Exception as e:
        print(f"✗ FAISS store error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_embedding():
    """Test embedding generation"""
    
    print("\n" + "="*60)
    print("TEST 2: Embedding Generation")
    print("="*60)
    
    try:
        store = get_faiss_store()
        
        # Test embedding
        text = "Test capacitor 4700pF 25V ceramic"
        print(f"Generating embedding for: '{text}'")
        
        embedding = store._get_embedding(text)
        
        if embedding is not None and len(embedding) == 768:
            print(f"✓ Embedding generated: {len(embedding)} dimensions")
            print(f"  Sample values: {embedding[:5]}")
            return True
        else:
            print(f"✗ Invalid embedding: {embedding}")
            return False
    
    except Exception as e:
        print(f"✗ Embedding error: {e}")
        print("\nMake sure Ollama is running:")
        print("  1. Start Ollama: ollama serve")
        print("  2. Pull model: ollama pull nomic-embed-text")
        import traceback
        traceback.print_exc()
        return False


def test_add_part():
    """Test adding parts with multiple manufacturers"""
    
    print("\n" + "="*60)
    print("TEST 3: Add Part with Multiple Manufacturers")
    print("="*60)
    
    try:
        store = get_faiss_store()
        
        # Create test part with 3 manufacturers
        test_part = {
            "part_number": "TEST-12345",
            "description": "Test ceramic capacitor 4700pF 25V",
            "quantity": "10",
            "designators": "C1, C2, C3",
            "manufacturers": [
                {"manufacturer": "KEMET", "mpn": "C1210C472KARGC7800", "preference": 1},
                {"manufacturer": "Yageo", "mpn": "CC1210KKX7R8BB472", "preference": 2},
                {"manufacturer": "Murata", "mpn": "GRM31CR71H472KA01", "preference": 3}
            ]
        }
        
        print(f"Adding part: {test_part['part_number']}")
        print(f"Manufacturers: {len(test_part['manufacturers'])}")
        
        # Add to store
        store.add_parts([test_part], source_file="test.pdf")
        
        print("✓ Part added successfully")
        
        # Search for it
        result = store.search_by_part_number("TEST-12345")
        
        if result:
            print("✓ Part retrieved successfully")
            print(f"  Part Number: {result.get('part_number')}")
            print(f"  Manufacturers: {len(result.get('manufacturers', []))}")
            
            for i, mfr in enumerate(result.get('manufacturers', []), 1):
                print(f"    [{i}] {mfr.get('manufacturer')} : {mfr.get('mpn')}")
            
            return True
        else:
            print("✗ Part not found after adding")
            return False
    
    except Exception as e:
        print(f"✗ Add part error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_semantic_search():
    """Test semantic search"""
    
    print("\n" + "="*60)
    print("TEST 4: Semantic Search")
    print("="*60)
    
    try:
        store = get_faiss_store()
        
        query = "ceramic capacitor"
        print(f"Searching for: '{query}'")
        
        results = store.search(query, top_k=3)
        
        print(f"✓ Found {len(results)} results")
        
        for i, result in enumerate(results, 1):
            print(f"\n  Result {i}:")
            print(f"    Part Number: {result.get('part_number')}")
            print(f"    Description: {result.get('description', 'N/A')[:50]}...")
            print(f"    Manufacturers: {len(result.get('manufacturers', []))}")
        
        return len(results) > 0
    
    except Exception as e:
        print(f"✗ Search error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_orchestrator():
    """Test multi-agent orchestrator"""
    
    print("\n" + "="*60)
    print("TEST 5: Multi-Agent Orchestrator")
    print("="*60)
    
    try:
        orchestrator = get_orchestrator()
        
        query = "What is part TEST-12345?"
        print(f"Processing query: '{query}'")
        
        result = orchestrator.process_query(query)
        
        if result.get('success'):
            print("✓ Query processed successfully")
            print(f"  Parts found: {len(result.get('parts_found', []))}")
            print(f"  Message: {result.get('message')}")
            
            # Print formatted response
            print("\n" + "-"*60)
            print("Formatted Response:")
            print("-"*60)
            print(result.get('formatted_response', ''))
            
            return True
        else:
            print(f"✗ Query failed: {result.get('message')}")
            return False
    
    except Exception as e:
        print(f"✗ Orchestrator error: {e}")
        import traceback
        traceback.print_exc()
        return False


def cleanup():
    """Clean up test data"""
    
    print("\n" + "="*60)
    print("Cleanup: Removing Test Part")
    print("="*60)
    
    try:
        store = get_faiss_store()
        
        # Remove test part by clearing and reloading without it
        # (FAISS doesn't support single item deletion)
        print("Note: To remove test part, clear index or restart")
        
        return True
    
    except Exception as e:
        print(f"✗ Cleanup error: {e}")
        return False


def main():
    """Run all tests"""
    
    print("\n" + "="*70)
    print(" "*20 + "FAISS MULTI-AGENT SYSTEM TESTS")
    print("="*70)
    
    tests = [
        ("FAISS Store Init", test_faiss_store),
        ("Embedding Generation", test_embedding),
        ("Add Part with Multiple Mfrs", test_add_part),
        ("Semantic Search", test_semantic_search),
        ("Multi-Agent Orchestrator", test_orchestrator),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"\n✗ Test '{name}' crashed: {e}")
            results.append((name, False))
    
    # Print summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status:8} - {name}")
    
    print("\n" + "="*70)
    print(f"Result: {passed}/{total} tests passed")
    print("="*70)
    
    if passed == total:
        print("\n🎉 All tests passed! FAISS Multi-Agent system is working!")
    else:
        print("\n⚠️  Some tests failed. Check errors above.")
        print("\nCommon issues:")
        print("  1. Ollama not running → Start with: ollama serve")
        print("  2. Model not installed → Install with: ollama pull nomic-embed-text")
        print("  3. FAISS index corrupted → Clear with: curl -X POST http://localhost:8000/clear")
    
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
