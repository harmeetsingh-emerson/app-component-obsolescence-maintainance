"""
FAISS BOM Store - Vector-based storage for BOM parts with semantic search

Features:
- Stores part data with embeddings in FAISS
- Supports semantic search for parts
- Maintains ALL manufacturers and MPNs per part
- Fast retrieval with vector similarity
"""

import json
import os
import pickle
from typing import List, Dict, Optional
import requests

import faiss
import numpy as np


class FAISSBOMStore:
    """FAISS-based vector store for BOM part data"""
    
    def __init__(self, embedding_dim: int = 768, storage_dir: str = "index-faiss-store"):
        self.embedding_dim = embedding_dim
        self.storage_dir = storage_dir
        self.index_path = os.path.join(storage_dir, "parts.index")
        self.metadata_path = os.path.join(storage_dir, "metadata.pkl")
        self.json_path = os.path.join(storage_dir, "parts_readable.json")
        self.embedding_model = "nomic-embed-text"
        _ollama_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.embedding_url = f"{_ollama_base}/api/embeddings"
        
        # Create storage directory
        os.makedirs(storage_dir, exist_ok=True)
        
        # Initialize or load FAISS index
        self.index = None
        self.metadata = []  # List of part data dicts
        self.load()
    
    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Generate embedding using Ollama API"""
        try:
            payload = {
                "model": self.embedding_model,
                "prompt": text
            }
            
            response = requests.post(self.embedding_url, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                embedding = data.get('embedding')
                
                if embedding:
                    # Ensure correct dimension
                    emb_array = np.array(embedding, dtype='float32')
                    
                    # Pad or truncate to match dimension
                    if len(emb_array) < self.embedding_dim:
                        emb_array = np.pad(emb_array, (0, self.embedding_dim - len(emb_array)))
                    elif len(emb_array) > self.embedding_dim:
                        emb_array = emb_array[:self.embedding_dim]
                    
                    return emb_array
            
            print(f"[FAISS Store] Embedding API failed: {response.status_code}")
            return None
        
        except Exception as e:
            print(f"[FAISS Store] Embedding error: {e}")
            return None
    
    def _create_searchable_text(self, part_data: Dict) -> str:
        """Create searchable text from part data for embedding"""
        
        # Raw-row / intelligent-row records carry all content in raw_row_text / description
        if part_data.get('source_type') in ('raw_row', 'intelligent_row'):
            raw = part_data.get('raw_row_text') or part_data.get('description', '')
            sheet = part_data.get('sheet', '')
            pn = part_data.get('part_number', '')
            topic = part_data.get('file_topic', '')
            prefix = f"Topic: {topic} | " if topic else ""
            return f"{prefix}Sheet: {sheet} | Part: {pn} | {raw}"

        text_parts = [
            f"Part Number: {part_data.get('part_number')}",
        ]
        
        if part_data.get('description'):
            text_parts.append(f"Description: {part_data.get('description')}")
        
        if part_data.get('quantity'):
            text_parts.append(f"Quantity: {part_data.get('quantity')}")
        
        if part_data.get('designators'):
            text_parts.append(f"Designators: {part_data.get('designators')}")
        
        # Add ALL manufacturers and MPNs
        manufacturers = part_data.get('manufacturers', [])
        for i, mfr_data in enumerate(manufacturers, 1):
            text_parts.append(f"Manufacturer {i}: {mfr_data.get('manufacturer')}")
            text_parts.append(f"MPN {i}: {mfr_data.get('mpn')}")
        
        return " | ".join(text_parts)
    
    def _get_embeddings_batch(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """
        Generate embeddings for a list of texts concurrently using a thread pool.
        Falls back to None for any text that fails.
        """
        import concurrent.futures as _cf

        EMBED_WORKERS = 8  # concurrent Ollama connections

        def _embed_one(text):
            return self._get_embedding(text)

        results = [None] * len(texts)
        with _cf.ThreadPoolExecutor(max_workers=EMBED_WORKERS) as executor:
            future_to_idx = {executor.submit(_embed_one, t): i for i, t in enumerate(texts)}
            for future in _cf.as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception:
                    results[idx] = None
        return results

    def add_parts(self, parts: List[Dict], source_file: str = None):
        """Add parts to FAISS index with embeddings"""
        
        print(f"[FAISS Store] Adding {len(parts)} parts to index...")
        
        # Prepare all part dicts and searchable texts up front
        part_dicts: List[Dict] = []
        texts: List[str] = []
        for part in parts:
            part_data = part.copy()
            if source_file:
                part_data['source_file'] = source_file
            part_dicts.append(part_data)
            texts.append(self._create_searchable_text(part_data))

        # Generate all embeddings concurrently
        print(f"[FAISS Store] Generating {len(texts)} embeddings (parallel)…")
        embeddings = self._get_embeddings_batch(texts)

        embeddings_to_add = []
        metadata_to_add   = []
        added_count = 0

        for part_data, embedding in zip(part_dicts, embeddings):
            if embedding is not None:
                embeddings_to_add.append(embedding)
                metadata_to_add.append(part_data)
                added_count += 1
            else:
                print(f"[FAISS Store] ✗ Failed to embed: {part_data.get('part_number')}")
        
        # Add to FAISS index
        if embeddings_to_add:
            embeddings_array = np.array(embeddings_to_add, dtype='float32')
            
            # Initialize index if needed
            if self.index is None:
                self.index = faiss.IndexFlatL2(self.embedding_dim)
            
            # Add embeddings
            self.index.add(embeddings_array)
            
            # Add metadata
            self.metadata.extend(metadata_to_add)
            
            print(f"[FAISS Store] Added {added_count} parts to FAISS index")
            self.save()
        else:
            print("[FAISS Store] No parts added (all embeddings failed)")
    
    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search for parts using semantic similarity"""
        
        if self.index is None or self.index.ntotal == 0:
            print("[FAISS Store] Index is empty")
            return []
        
        # Generate query embedding
        query_embedding = self._get_embedding(query)
        
        if query_embedding is None:
            print("[FAISS Store] Failed to generate query embedding")
            return []
        
        # Search FAISS
        query_embedding = query_embedding.reshape(1, -1)
        
        distances, indices = self.index.search(query_embedding, min(top_k, self.index.ntotal))
        
        # Retrieve metadata
        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.metadata):
                result = self.metadata[idx].copy()
                result['distance'] = float(distance)
                results.append(result)
        
        print(f"[FAISS Store] Found {len(results)} results for query: {query}")
        
        return results
    
    def search_by_part_number(self, part_number: str) -> Optional[Dict]:
        """Search for exact part number match"""
        
        part_number_upper = part_number.upper().strip()
        
        for part_data in self.metadata:
            if part_data.get('part_number', '').upper().strip() == part_number_upper:
                return part_data
        
        return None
    
    def get_all_parts(self) -> List[Dict]:
        """Get all parts in the index"""
        return self.metadata.copy()
    
    def clear(self):
        """Clear the FAISS index and metadata"""
        self.index = faiss.IndexFlatL2(self.embedding_dim)
        self.metadata = []
        self.save()
        print("[FAISS Store] Index cleared")
    
    def save(self):
        """Save FAISS index and metadata to disk"""
        try:
            if self.index is not None and self.index.ntotal > 0:
                faiss.write_index(self.index, self.index_path)
                print(f"[FAISS Store] Saved index with {self.index.ntotal} vectors")
            
            with open(self.metadata_path, 'wb') as f:
                pickle.dump(self.metadata, f)
            
            print(f"[FAISS Store] Saved {len(self.metadata)} parts metadata")
            
            # Save human-readable JSON file
            with open(self.json_path, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, indent=2, ensure_ascii=False)
            
            print(f"[FAISS Store] Saved human-readable JSON with {len(self.metadata)} parts")
        
        except Exception as e:
            print(f"[FAISS Store] Save error: {e}")
    
    def load(self):
        """Load FAISS index and metadata from disk"""
        try:
            if os.path.exists(self.index_path):
                self.index = faiss.read_index(self.index_path)
                print(f"[FAISS Store] Loaded index with {self.index.ntotal} vectors")
            else:
                self.index = faiss.IndexFlatL2(self.embedding_dim)
                print("[FAISS Store] Created new FAISS index")
            
            if os.path.exists(self.metadata_path):
                with open(self.metadata_path, 'rb') as f:
                    self.metadata = pickle.load(f)
                print(f"[FAISS Store] Loaded {len(self.metadata)} parts metadata")
            else:
                self.metadata = []
                print("[FAISS Store] No existing metadata found")
        
        except Exception as e:
            print(f"[FAISS Store] Load error: {e}")
            self.index = faiss.IndexFlatL2(self.embedding_dim)
            self.metadata = []
    
    def get_stats(self) -> Dict:
        """Get index statistics"""
        
        total_parts = len(self.metadata)
        total_vectors = self.index.ntotal if self.index else 0
        
        # Count parts with multiple manufacturers
        parts_with_multiple_mfrs = 0
        total_manufacturer_options = 0
        
        for part in self.metadata:
            num_mfrs = len(part.get('manufacturers', []))
            total_manufacturer_options += num_mfrs
            if num_mfrs > 1:
                parts_with_multiple_mfrs += 1
        
        # Get unique manufacturers
        manufacturers = set()
        for part in self.metadata:
            for mfr_data in part.get('manufacturers', []):
                manufacturers.add(mfr_data.get('manufacturer'))
        
        return {
            "total_parts": total_parts,
            "total_vectors": total_vectors,
            "parts_with_multiple_manufacturers": parts_with_multiple_mfrs,
            "total_manufacturer_options": total_manufacturer_options,
            "unique_manufacturers": len(manufacturers),
            "manufacturers_list": sorted(manufacturers)
        }


# Global instance
_faiss_store_instance = None

def get_faiss_store(embedding_dim: int = 768) -> FAISSBOMStore:
    """Get singleton FAISS store instance"""
    global _faiss_store_instance
    if _faiss_store_instance is None:
        storage_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "index-faiss-store"
        )
        _faiss_store_instance = FAISSBOMStore(embedding_dim, storage_dir)
    return _faiss_store_instance


if __name__ == "__main__":
    # Test
    store = FAISSBOMStore(embedding_dim=768, storage_dir="test_faiss_store")
    
    # Test data with multiple manufacturers
    test_parts = [
        {
            "part_number": "563969-472",
            "description": "Cap; Ceramic; 4700pF; 25V",
            "quantity": "10",
            "manufacturers": [
                {"manufacturer": "KEMET", "mpn": "C1210C472KARGC7800", "preference": 1},
                {"manufacturer": "Yageo", "mpn": "CC1210KKX7R8BB472", "preference": 2}
            ]
        }
    ]
    
    store.add_parts(test_parts, "test_bom.pdf")
    
    # Test search
    results = store.search("capacitor 4700pF")
    print(f"\nSearch results: {len(results)}")
    for result in results:
        print(f"  {result.get('part_number')} | Distance: {result.get('distance'):.4f}")
    
    # Stats
    print(f"\nStats: {store.get_stats()}")
