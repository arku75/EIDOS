"""
EIDOS Hybrid Memory RRF — Memoria con Reciprocal Rank Fusion
Inspirado en IronClaw: combina full-text, vector y keyword search.

RRF fusiona rankings de múltiples estrategias para resultados superiores.
"""

import re
import json
import math
import sqlite3
import time
import hashlib
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from collections import Counter
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from core.db import get_conn

# ─── Constantes ──────────────────────────────────────────────────────────────

EIDOS_DIR = Path.home() / ".eidos"
DB_PATH = EIDOS_DIR / "memory_rrf.db"
RRF_K = 60  # Constante RRF estándar

# ─── Dataclass ───────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    id: str
    content: str
    metadata: Dict = field(default_factory=dict)
    score: float = 0.0
    sources: List[str] = field(default_factory=list)


class HybridMemoryRRF:
    """
    Memoria híbrida que combina 3 estrategias de búsqueda:
    1. Full-text search (SQLite FTS5)
    2. Vector search (sqlite-vec con Ollama embeddings)
    3. Keyword search (TF-IDF simplificado)

    Los resultados se fusionan con Reciprocal Rank Fusion.
    """

    def __init__(self, embedding_model: str = "nomic-embed-text"):
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        self.embedding_model = embedding_model
        self.db = get_conn(str(DB_PATH), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self._has_vec = self._check_vec()
        self._has_ollama = self._check_ollama()
        self._idf_cache = {}

    def _init_db(self):
        """Inicializa tablas incluyendo FTS5"""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                keywords TEXT DEFAULT '',
                created_at REAL,
                accessed_at REAL,
                access_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                strategy TEXT,
                results_count INTEGER,
                time_ms REAL,
                timestamp REAL
            );
        """)

        # FTS5 virtual table
        try:
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(id, content, keywords, content='memories', content_rowid='rowid')
            """)
        except Exception:
            # FTS5 puede no estar disponible
            pass

        # Vector table (si sqlite-vec disponible)
        try:
            self.db.execute("SELECT vec_version()")
            self.db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec
                USING vec0(id TEXT PRIMARY KEY, embedding float[768])
            """)
        except Exception:
            pass  # error no crítico, continuar
        self.db.commit()

    def _check_vec(self) -> bool:
        """Verifica si sqlite-vec está disponible"""
        try:
            self.db.execute("SELECT vec_version()")
            return True
        except Exception:
            return False

    def _check_ollama(self) -> bool:
        """Verifica si Ollama está disponible"""
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:11434/api/tags")
            urllib.request.urlopen(req, timeout=2)
            return True
        except Exception:
            return False

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Obtiene embedding de Ollama"""
        if not self._has_ollama:
            return None
        try:
            import urllib.request
            data = json.dumps({"model": self.embedding_model, "prompt": text}).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/embeddings",
                data=data,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get("embedding", [])
        except Exception:
            return None

    def _extract_keywords(self, text: str) -> str:
        """Extrae keywords del texto"""
        # Tokenizar
        words = re.findall(r'\b[a-záéíóúñ]{3,}\b', text.lower())
        # Stopwords básicas
        stops = {"the", "and", "for", "are", "but", "not", "you", "all",
                 "can", "had", "her", "was", "one", "our", "out", "que",
                 "del", "los", "las", "una", "con", "para", "por", "esta",
                 "como", "más", "pero", "sus", "este", "entre", "cuando",
                 "muy", "sin", "sobre", "ser", "también", "desde", "todo"}
        keywords = [w for w in words if w not in stops]
        # Top keywords por frecuencia
        counter = Counter(keywords)
        top = [w for w, _ in counter.most_common(20)]
        return " ".join(top)

    def _compute_tfidf(self, query_words: List[str], doc_words: List[str]) -> float:
        """TF-IDF simplificado"""
        if not doc_words:
            return 0.0

        total_docs = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if total_docs == 0:
            return 0.0

        score = 0.0
        doc_counter = Counter(doc_words)
        doc_len = len(doc_words)

        for word in query_words:
            tf = doc_counter.get(word, 0) / max(doc_len, 1)

            if word not in self._idf_cache:
                doc_freq = self.db.execute(
                    "SELECT COUNT(*) FROM memories WHERE keywords LIKE ?",
                    (f"%{word}%",)
                ).fetchone()[0]
                self._idf_cache[word] = math.log(
                    (total_docs + 1) / (doc_freq + 1)
                ) + 1
            idf = self._idf_cache[word]
            score += tf * idf

        return score

    # ─── API Principal ───────────────────────────────────────────────────

    def store(self, content: str, metadata: Dict = None) -> str:
        """
        Almacena un recuerdo.

        Returns:
            memory_id
        """
        mem_id = hashlib.md5(f"{content}_{time.time()}".encode()).hexdigest()[:12]
        keywords = self._extract_keywords(content)
        now = time.time()

        self.db.execute("""
            INSERT OR REPLACE INTO memories (id, content, metadata, keywords, created_at, accessed_at, access_count)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (mem_id, content, json.dumps(metadata or {}), keywords, now, now))

        # FTS5 sync
        try:
            self.db.execute(
                "INSERT INTO memories_fts (id, content, keywords) VALUES (?, ?, ?)",
                (mem_id, content, keywords)
            )
        except Exception:
            pass  # error no crítico, continuar
        # Vector sync
        if self._has_vec and self._has_ollama:
            embedding = self._get_embedding(content)
            if embedding:
                try:
                    self.db.execute(
                        "INSERT INTO memories_vec (id, embedding) VALUES (?, ?)",
                        (mem_id, json.dumps(embedding))
                    )
                except Exception:
                    pass  # error no crítico, continuar
        self.db.commit()
        return mem_id

    def _search_fulltext(self, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
        """Búsqueda full-text con FTS5"""
        start = time.time()
        results = []
        try:
            rows = self.db.execute("""
                SELECT id, rank FROM memories_fts
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, top_k)).fetchall()
            results = [(r["id"], -r["rank"]) for r in rows]  # rank es negativo en FTS5
        except Exception:
            # Fallback a LIKE si FTS5 no funciona
            words = query.lower().split()
            for word in words:
                rows = self.db.execute(
                    "SELECT id FROM memories WHERE content LIKE ? LIMIT ?",
                    (f"%{word}%", top_k)
                ).fetchall()
                for r in rows:
                    results.append((r["id"], 1.0))

        elapsed = (time.time() - start) * 1000
        self._log_search(query, "fulltext", len(results), elapsed)
        return results

    def _search_vector(self, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
        """Búsqueda vectorial con sqlite-vec"""
        start = time.time()
        results = []

        if self._has_vec and self._has_ollama:
            embedding = self._get_embedding(query)
            if embedding:
                try:
                    rows = self.db.execute("""
                        SELECT id, distance FROM memories_vec
                        WHERE embedding MATCH ?
                        AND k = ?
                    """, (json.dumps(embedding), top_k)).fetchall()
                    # Convertir distancia a score (menor distancia = mejor)
                    results = [(r["id"], 1.0 / (1.0 + r["distance"])) for r in rows]
                except Exception:
                    pass  # error no crítico, continuar
        elapsed = (time.time() - start) * 1000
        self._log_search(query, "vector", len(results), elapsed)
        return results

    def _search_keyword(self, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
        """Búsqueda por keywords con TF-IDF"""
        start = time.time()
        query_words = re.findall(r'\b[a-záéíóúñ]{3,}\b', query.lower())

        # Obtener candidatos
        candidates = []
        for word in query_words:
            rows = self.db.execute(
                "SELECT id, keywords FROM memories WHERE keywords LIKE ? LIMIT 50",
                (f"%{word}%",)
            ).fetchall()
            for r in rows:
                candidates.append(r)

        # Score con TF-IDF
        scored = {}
        for r in candidates:
            doc_words = r["keywords"].split()
            score = self._compute_tfidf(query_words, doc_words)
            if r["id"] in scored:
                scored[r["id"]] = max(scored[r["id"]], score)
            else:
                scored[r["id"]] = score

        results = sorted(scored.items(), key=lambda x: x[1], reverse=True)[:top_k]

        elapsed = (time.time() - start) * 1000
        self._log_search(query, "keyword", len(results), elapsed)
        return results

    def search(self, query: str, top_k: int = 10) -> List[MemoryEntry]:
        """
        Búsqueda híbrida con RRF.

        Ejecuta las 3 estrategias y fusiona resultados.
        """
        # Ejecutar búsquedas en paralelo
        with ThreadPoolExecutor(max_workers=3) as pool:
            ft_future = pool.submit(self._search_fulltext, query, top_k * 2)
            vec_future = pool.submit(self._search_vector, query, top_k * 2)
            kw_future = pool.submit(self._search_keyword, query, top_k * 2)

            ft_results = ft_future.result()
            vec_results = vec_future.result()
            kw_results = kw_future.result()

        # RRF fusion
        rrf_scores = {}
        sources = {}

        for rank, (doc_id, _) in enumerate(ft_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(doc_id, []).append("fulltext")

        for rank, (doc_id, _) in enumerate(vec_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(doc_id, []).append("vector")

        for rank, (doc_id, _) in enumerate(kw_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (RRF_K + rank + 1)
            sources.setdefault(doc_id, []).append("keyword")

        # Ordenar por RRF score
        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        # Construir resultados
        results = []
        for doc_id, score in sorted_ids:
            row = self.db.execute(
                "SELECT * FROM memories WHERE id = ?", (doc_id,)
            ).fetchone()
            if row:
                # Actualizar acceso
                self.db.execute(
                    "UPDATE memories SET accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                    (time.time(), doc_id)
                )
                results.append(MemoryEntry(
                    id=doc_id,
                    content=row["content"],
                    metadata=json.loads(row["metadata"] or "{}"),
                    score=score,
                    sources=sources.get(doc_id, [])
                ))

        self.db.commit()
        return results

    def hybrid_search(self, query: str, top_k: int = 10,
                      weights: Dict[str, float] = None) -> List[MemoryEntry]:
        """
        Búsqueda ponderada — permite dar más peso a ciertas estrategias.

        Args:
            weights: {"fulltext": 1.0, "vector": 1.0, "keyword": 1.0}
        """
        if not weights:
            weights = {"fulltext": 1.0, "vector": 1.0, "keyword": 1.0}

        ft_results = self._search_fulltext(query, top_k * 2)
        vec_results = self._search_vector(query, top_k * 2)
        kw_results = self._search_keyword(query, top_k * 2)

        rrf_scores = {}
        sources = {}

        for rank, (doc_id, _) in enumerate(ft_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + weights.get("fulltext", 1.0) / (RRF_K + rank + 1)
            sources.setdefault(doc_id, []).append("fulltext")

        for rank, (doc_id, _) in enumerate(vec_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + weights.get("vector", 1.0) / (RRF_K + rank + 1)
            sources.setdefault(doc_id, []).append("vector")

        for rank, (doc_id, _) in enumerate(kw_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + weights.get("keyword", 1.0) / (RRF_K + rank + 1)
            sources.setdefault(doc_id, []).append("keyword")

        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        results = []
        for doc_id, score in sorted_ids:
            row = self.db.execute("SELECT * FROM memories WHERE id = ?", (doc_id,)).fetchone()
            if row:
                results.append(MemoryEntry(
                    id=doc_id, content=row["content"],
                    metadata=json.loads(row["metadata"] or "{}"),
                    score=score, sources=sources.get(doc_id, [])
                ))
        return results

    def _log_search(self, query: str, strategy: str, count: int, time_ms: float):
        try:
            self.db.execute(
                "INSERT INTO search_log (query, strategy, results_count, time_ms, timestamp) VALUES (?, ?, ?, ?, ?)",
                (query[:200], strategy, count, time_ms, time.time())
            )
        except Exception:
            pass  # error no crítico, continuar
    def reindex(self):
        """Reconstruye todos los índices"""
        print("🔄 [MemoryRRF] Reindexando...")

        # Rebuild FTS5
        try:
            self.db.execute("DELETE FROM memories_fts")
            rows = self.db.execute("SELECT id, content, keywords FROM memories").fetchall()
            for r in rows:
                self.db.execute(
                    "INSERT INTO memories_fts (id, content, keywords) VALUES (?, ?, ?)",
                    (r["id"], r["content"], r["keywords"])
                )
            print(f"  ✅ FTS5: {len(rows)} documentos indexados")
        except Exception as e:
            print(f"  ⚠️ FTS5: {e}")

        # Rebuild vector index
        if self._has_vec and self._has_ollama:
            try:
                self.db.execute("DELETE FROM memories_vec")
                rows = self.db.execute("SELECT id, content FROM memories").fetchall()
                count = 0
                for r in rows:
                    emb = self._get_embedding(r["content"])
                    if emb:
                        self.db.execute(
                            "INSERT INTO memories_vec (id, embedding) VALUES (?, ?)",
                            (r["id"], json.dumps(emb))
                        )
                        count += 1
                print(f"  ✅ Vector: {count} embeddings generados")
            except Exception as e:
                print(f"  ⚠️ Vector: {e}")

        # Refresh IDF cache
        self._idf_cache.clear()

        self.db.commit()
        print("✅ [MemoryRRF] Reindexación completa")

    def search_stats(self) -> Dict:
        """Estadísticas de búsqueda"""
        total_memories = self.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        total_searches = self.db.execute("SELECT COUNT(*) FROM search_log").fetchone()[0]

        by_strategy = {}
        for row in self.db.execute(
            "SELECT strategy, COUNT(*) as c, AVG(time_ms) as avg_ms FROM search_log GROUP BY strategy"
        ).fetchall():
            by_strategy[row["strategy"]] = {
                "count": row["c"],
                "avg_time_ms": round(row["avg_ms"], 2)
            }

        return {
            "total_memories": total_memories,
            "total_searches": total_searches,
            "by_strategy": by_strategy,
            "has_vec": self._has_vec,
            "has_ollama": self._has_ollama,
        }

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass  # error no crítico, continuar
# ─── Test ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Test Hybrid Memory RRF ===\n")

    mem = HybridMemoryRRF()

    # Almacenar memorias
    print("Test 1: Almacenar memorias")
    docs = [
        ("EIDOS es un sistema de IA autónomo que aprende y se auto-mejora", {"type": "definition"}),
        ("El kernel determinista usa un loop PLAN EXECUTE REFLECT VERIFY", {"type": "architecture"}),
        ("Phoenix Guardian resucita a EIDOS si no hay heartbeat en 3 horas", {"type": "guardian"}),
        ("Shield tiene 9 capas de seguridad incluyendo leak detector y prompt shield", {"type": "security"}),
        ("Ollama ejecuta modelos como hermes3 y lfm2.5-1.2b-instruct:q4_0 localmente", {"type": "infrastructure"}),
        ("La autonomía real fue probada durante 20 minutos con 6 acciones", {"type": "milestone"}),
        ("Library Autodidact aprendió 36 librerías con 3485 APIs", {"type": "learning"}),
        ("El sistema de gobernanza permite a los agentes votar propuestas", {"type": "governance"}),
        ("Token economy da a cada agente un balance de tokens como HP", {"type": "economy"}),
        ("Ganglia permite heredar capacidades entre agentes del sistema", {"type": "ganglia"}),
    ]
    ids = []
    for content, meta in docs:
        mid = mem.store(content, meta)
        ids.append(mid)
        print(f"  📝 {mid}: {content[:50]}...")
    print(f"  ✅ {len(ids)} memorias almacenadas\n")

    # Búsqueda
    print("Test 2: Búsqueda híbrida RRF")
    results = mem.search("seguridad shield protección")
    for r in results[:3]:
        print(f"  🔍 [{r.score:.4f}] ({','.join(r.sources)}) {r.content[:60]}...")
    assert len(results) > 0
    print(f"  ✅ {len(results)} resultados\n")

    print("Test 3: Búsqueda de agentes y autonomía")
    results = mem.search("agentes autonomos aprendizaje")
    for r in results[:3]:
        print(f"  🔍 [{r.score:.4f}] ({','.join(r.sources)}) {r.content[:60]}...")
    print(f"  ✅ {len(results)} resultados\n")

    # Stats
    print("Test 4: Stats")
    stats = mem.search_stats()
    print(f"  Memorias: {stats['total_memories']}")
    print(f"  Búsquedas: {stats['total_searches']}")
    print(f"  Estrategias: {stats['by_strategy']}")
    print(f"  Vec: {stats['has_vec']}, Ollama: {stats['has_ollama']}")

    print("\n✅ Hybrid Memory RRF funcional")
