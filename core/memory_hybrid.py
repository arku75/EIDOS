import os
import json
from datetime import datetime
from typing import Any, List, Dict

try:
    import chromadb
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

try:
    import duckdb
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

class EidosMemoryHybrid:
    """Sistema de memoria híbrida: ChromaDB (Semántica) + DuckDB (Analítica)."""
    
    def __init__(self, data_dir: str = "~/EIDOS/OpenClaw_Data"):
        self.data_dir = os.path.expanduser(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)

        # 1. Setup ChromaDB (Semántica)
        self.chroma_client = None
        self.collection = None
        if HAS_CHROMADB:
            self.chroma_path = os.path.join(self.data_dir, "chroma_db")
            self.chroma_client = chromadb.PersistentClient(path=self.chroma_path)
            self.collection = self.chroma_client.get_or_create_collection(
                name="eidos_stories",
                metadata={"hnsw:space": "cosine"}
            )

        # 2. Setup DuckDB (Analítica)
        self.duck_conn = None
        if HAS_DUCKDB:
            self.duck_path = os.path.join(self.data_dir, "eidos_analytics.duckdb")
            self.duck_conn = duckdb.connect(self.duck_path)
            self._setup_duck_schema()

    def _setup_duck_schema(self):
        """Crea las tablas necesarias para analytics si no existen."""
        self.duck_conn.execute("""
        CREATE TABLE IF NOT EXISTS video_success (
            video_id VARCHAR PRIMARY KEY,
            timestamp TIMESTAMP,
            title TEXT,
            prompt TEXT,
            views INTEGER,
            likes INTEGER,
            platform TEXT,
            engagement_rate FLOAT
        )
        """)

    def record_story(self, story_id: str, content: str, metadata: Dict[str, Any]):
        """Guarda una historia en la memoria semántica."""
        self.collection.add(
            documents=[content],
            metadatas=[metadata],
            ids=[story_id]
        )

    def search_similar_stories(self, query: str, n_results: int = 3) -> List[Dict]:
        """Busca historias similares por significado."""
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results
        )
        return results

    def update_analytics(self, video_id: str, views: int, likes: int, platform: str):
        """Actualiza los datos de éxito de un vídeo."""
        engagement = (likes / views * 100) if views > 0 else 0
        self.duck_conn.execute("""
        INSERT OR REPLACE INTO video_success 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (video_id, datetime.now(), "N/A", "N/A", views, likes, platform, engagement))

    def get_best_patterns(self) -> List[Any]:
        """Analiza qué prompts o temas están teniendo más éxito."""
        return self.duck_conn.execute("""
        SELECT platform, AVG(engagement_rate) as avg_eng
        FROM video_success
        GROUP BY platform
        ORDER BY avg_eng DESC
        """).fetchall()

if __name__ == "__main__":
    # Test rápido
    mem = EidosMemoryHybrid()
    print("✅ Sistema de memoria híbrida inicializado con éxito.")
