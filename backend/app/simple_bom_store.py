"""
Simple BOM Store - Fast lookup for part data

Stores structured BOM data in memory for instant queries.
No complex vector embeddings - just direct lookup by part number.
"""

import json
import os
from typing import List, Dict, Optional


class SimpleBOMStore:
    """In-memory store for BOM part data with fast lookup"""
    
    def __init__(self, storage_file: str = "bom_data.json"):
        self.storage_file = storage_file
        self.parts_db: Dict[str, Dict] = {}  # part_number -> part_data
        self.load()
    
    def add_parts(self, parts: List[Dict[str, str]], source_file: str = None):
        """Add parts to the store"""
        
        added_count = 0
        for part in parts:
            part_number = part.get('part_number')
            
            if not part_number:
                continue
            
            # Normalize part number (uppercase for consistent lookup)
            part_number_key = part_number.upper().strip()
            
            # Add source file info
            part_data = part.copy()
            if source_file:
                part_data['source_file'] = source_file
            
            # Store
            self.parts_db[part_number_key] = part_data
            added_count += 1
        
        print(f"[BOM Store] Added {added_count} parts to database")
        self.save()
    
    def get_part(self, part_number: str) -> Optional[Dict[str, str]]:
        """Get part data by part number (case-insensitive)"""
        
        part_number_key = part_number.upper().strip()
        return self.parts_db.get(part_number_key)
    
    def search_parts(self, query: str) -> List[Dict[str, str]]:
        """Search for parts matching query (fuzzy search)"""
        
        query_upper = query.upper().strip()
        results = []
        
        for part_number, part_data in self.parts_db.items():
            # Check if query matches part number, manufacturer, MPN, or description
            if (query_upper in part_number or
                query_upper in part_data.get('manufacturer', '').upper() or
                query_upper in part_data.get('mpn', '').upper() or
                query_upper in part_data.get('description', '').upper()):
                
                results.append(part_data)
        
        return results
    
    def get_all_parts(self) -> List[Dict[str, str]]:
        """Get all parts in the database"""
        return list(self.parts_db.values())
    
    def clear(self):
        """Clear all parts from the database"""
        self.parts_db = {}
        self.save()
        print("[BOM Store] Database cleared")
    
    def save(self):
        """Save database to file"""
        try:
            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump(self.parts_db, f, indent=2, ensure_ascii=False)
            print(f"[BOM Store] Saved {len(self.parts_db)} parts to {self.storage_file}")
        except Exception as e:
            print(f"[BOM Store] Failed to save: {e}")
    
    def load(self):
        """Load database from file"""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    self.parts_db = json.load(f)
                print(f"[BOM Store] Loaded {len(self.parts_db)} parts from {self.storage_file}")
            except Exception as e:
                print(f"[BOM Store] Failed to load: {e}")
                self.parts_db = {}
        else:
            print(f"[BOM Store] No existing database found")
            self.parts_db = {}
    
    def get_stats(self) -> Dict:
        """Get database statistics"""
        
        total_parts = len(self.parts_db)
        
        # Count parts with manufacturer and MPN
        parts_with_mfr = sum(1 for p in self.parts_db.values() if p.get('manufacturer'))
        parts_with_mpn = sum(1 for p in self.parts_db.values() if p.get('mpn'))
        
        # Get unique manufacturers
        manufacturers = set()
        for part in self.parts_db.values():
            mfr = part.get('manufacturer')
            if mfr:
                manufacturers.add(mfr)
        
        return {
            "total_parts": total_parts,
            "parts_with_manufacturer": parts_with_mfr,
            "parts_with_mpn": parts_with_mpn,
            "unique_manufacturers": len(manufacturers),
            "manufacturers_list": sorted(manufacturers)
        }


# Global instance
_store_instance = None

def get_store() -> SimpleBOMStore:
    """Get singleton store instance"""
    global _store_instance
    if _store_instance is None:
        storage_path = os.path.join(
            os.path.dirname(__file__), 
            "..", 
            "bom_data.json"
        )
        _store_instance = SimpleBOMStore(storage_path)
    return _store_instance


if __name__ == "__main__":
    # Test
    store = SimpleBOMStore("test_bom_data.json")
    
    # Add test data
    test_parts = [
        {
            "part_number": "563969-472",
            "manufacturer": "KEMET",
            "mpn": "C1210C472KARGC7800",
            "description": "Cap; Ceramic; 4700pF; 25V",
            "quantity": "10"
        },
        {
            "part_number": "556112-224",
            "manufacturer": "Yageo",
            "mpn": "CC0805KRX7R9BB224",
            "description": "Cap; Ceramic; 220nF; 50V",
            "quantity": "5"
        }
    ]
    
    store.add_parts(test_parts, "test_bom.pdf")
    
    # Test lookup
    part = store.get_part("563969-472")
    print(f"\nLookup result: {part}")
    
    # Test search
    results = store.search_parts("KEMET")
    print(f"\nSearch results for 'KEMET': {len(results)} parts found")
    
    # Stats
    print(f"\nStats: {store.get_stats()}")
