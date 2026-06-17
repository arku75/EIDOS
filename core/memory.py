"""
EIDOS core/memory.py — Semantic Memory + Embeddings Store (Fase 3 extra)
=========================================================================
De la auditoría de open-source (TinyClaw AGI + MimiClaw):
  ✅ Embeddings store con nomic-embed-text (768d) → SQLite vector
  ✅ add_memory(text, category): guarda con su embedding
  ✅ search_memory(query, top_k): recupera los K más similares por coseno
  ✅ Categorías: "fact", "skill_result", "conversation", "preference", "error"
  ✅ Compatible con el Context Compactor (compactor.py usa esto para memoria semántica)

ClosedClaw pattern: rate limiter integrado (max N embeddings por minuto)
MimiClaw pattern: memoria persistente local, nunca nube
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import urllib.request
from datetime import datetime
from typing import Any
from core.db import get_conn, get_conn_ctx

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text:latest"
MEMORY_DB   = os.path.expanduser("~/.eidos/memory.db")
EMBED_DIM   = 768   # Dimensión de nomic-embed-text

os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)


def _init_memory_db() -> None:
    with get_conn_ctx(MEMORY_DB) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                text       TEXT NOT NULL,
                category   TEXT NOT NULL DEFAULT 'fact',
                embedding  TEXT NOT NULL,
                created_at TEXT NOT NULL,
                access_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_category ON memories(category);
            CREATE INDEX IF NOT EXISTS idx_created  ON memories(created_at);
        """)

_init_memory_db()


# ── Rate Limiter (de ClosedClaw) ─────────────────────────────────────────────

class RateLimiter:
    """
    Limita las llamadas a la API de embeddings.
    ClosedClaw: rateLimiter.acquirePermit() antes de cada llamada.
    """
    def __init__(self, max_calls: int = 20, window_s: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window_s  = window_s
        self._calls: list[float] = []
    
    def acquire(self) -> bool:
        """Espera si es necesario y da permiso."""
        now = time.time()
        # Limpiar llamadas fuera de la ventana
        self._calls = [t for t in self._calls if now - t < self.window_s]
        
        if len(self._calls) >= self.max_calls:
            # Esperar hasta que haya slot disponible
            oldest = self._calls[0]  # pyre-ignore[arg-type]
            wait_s = self.window_s - (now - oldest) + 0.1
            if wait_s > 0:
                print(f"\033[93m[RATE LIMIT]\033[0m Esperando {wait_s:.1f}s (límite: {self.max_calls}/min)")
                time.sleep(wait_s)
        
        self._calls.append(time.time())
        return True

_rate_limiter = RateLimiter(max_calls=20, window_s=60.0)


# ── Embedding ────────────────────────────────────────────────────────────────

def _embed(text: str) -> list[float] | None:
    """Genera embedding con nomic-embed-text. Returns None si falla."""
    _rate_limiter.acquire()
    
    payload = json.dumps({
        "model": EMBED_MODEL,
        "prompt": text[:2000],   # Truncar si muy largo  # pyre-ignore[arg-type]
    }).encode()
    
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
            return data.get("embedding")
    except Exception as e:
        print(f"\033[91m[MEMORY] Error generando embedding: {e}\033[0m")
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Similitud coseno entre dos vectores."""
    if len(a) != len(b):
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── API Pública ──────────────────────────────────────────────────────────────

def add_memory(text: str, category: str = "fact") -> bool:
    """
    Guarda un recuerdo con su embedding semántico.
    
    Args:
        text: El contenido a recordar.
        category: "fact" | "skill_result" | "conversation" | "preference" | "error"
    
    Returns:
        True si se guardó correctamente.
    
    Ejemplo:
        add_memory("SER prefiere usar nmap con -sV para escaneos", "preference")
        add_memory("El password de sudo es correcto", "fact")
    """
    embedding = _embed(text)
    if embedding is None:
        return False
    
    with get_conn_ctx(MEMORY_DB) as conn:
        conn.execute(
            "INSERT INTO memories (text, category, embedding, created_at) VALUES (?,?,?,?)",
            (text, category, json.dumps(embedding), datetime.now().isoformat())
        )
    
    print(f"\033[94m[MEMORY]\033[0m Guardado ({category}): {text[:60]}")  # pyre-ignore[arg-type]
    return True


def search_memory(query: str, top_k: int = 5,
                  category: str | None = None) -> list[dict]:
    """
    Busca los K recuerdos más similares semánticamente a la query.
    
    Args:
        query: Texto de búsqueda.
        top_k: Número de resultados a retornar.
        category: Filtrar por categoría (None = todas).
    
    Returns:
        Lista de dicts con text, category, similarity, created_at.
    
    Ejemplo:
        results = search_memory("escanear red")
        for r in results:
            print(r['text'], r['similarity'])
    """
    query_embedding = _embed(query)
    if query_embedding is None:
        return []
    
    with get_conn_ctx(MEMORY_DB) as conn:
        conn.row_factory = sqlite3.Row
        if category:
            rows = conn.execute(
                "SELECT id, text, category, embedding, created_at FROM memories WHERE category=?",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, text, category, embedding, created_at FROM memories"
            ).fetchall()
    
    # Calcular similitudes
    scored = []
    for row in rows:
        try:
            mem_embedding = json.loads(row["embedding"])
            sim = _cosine_similarity(query_embedding, mem_embedding)
            scored.append({
                "id":         row["id"],
                "text":       row["text"],
                "category":   row["category"],
                "similarity": round(sim, 4),
                "created_at": row["created_at"],
            })
        except Exception:
            continue
    
    # Ordenar por similitud descendente y actualizar access_count
    scored.sort(key=lambda x: -x["similarity"])
    results = scored[:top_k]
    
    if results:
        # Actualizar access_count para los resultados devueltos
        ids = [r["id"] for r in results]
        with get_conn_ctx(MEMORY_DB) as conn:
            conn.execute(
                f"UPDATE memories SET access_count = access_count + 1 WHERE id IN ({','.join('?'*len(ids))})",
                ids
            )
    
    return results


def get_memory_context(query: str, top_k: int = 3,
                       min_similarity: float = 0.7) -> str:
    """
    Construye una sección de contexto de memoria para inyectar en el system prompt.
    Solo incluye memorias con similitud > min_similarity.
    
    OpenClaw pattern: inyectar contexto relevante dinámicamente.
    """
    results = search_memory(query, top_k=top_k)
    relevant = [r for r in results if r["similarity"] >= min_similarity]
    
    if not relevant:
        return ""
    
    lines = ["## Recuerdos relevantes para esta tarea:"]
    for r in relevant:
        lines.append(f"- [{r['category']}] {r['text']} (sim={r['similarity']})")
    
    return "\n".join(lines)


def list_memories(category: str | None = None, limit: int = 20) -> list[dict]:
    """Lista las memorias más recientes."""
    with get_conn_ctx(MEMORY_DB) as conn:
        conn.row_factory = sqlite3.Row
        if category:
            rows = conn.execute(
                "SELECT id, text, category, created_at, access_count FROM memories "
                "WHERE category=? ORDER BY created_at DESC LIMIT ?",
                (category, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, text, category, created_at, access_count FROM memories "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def forget(memory_id: int) -> bool:
    """Elimina un recuerdo por ID."""
    with get_conn_ctx(MEMORY_DB) as conn:
        n = conn.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount
    return n > 0


def memory_stats() -> dict:
    """Estadísticas de la base de memoria."""
    with get_conn_ctx(MEMORY_DB) as conn:
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]  # pyre-ignore[arg-type]
        by_cat = conn.execute(
            "SELECT category, COUNT(*) n FROM memories GROUP BY category"
        ).fetchall()
        most_accessed = conn.execute(
            "SELECT text, access_count FROM memories ORDER BY access_count DESC LIMIT 3"
        ).fetchall()
    
    return {
        "total": total,
        "by_category": {r[0]: r[1] for r in by_cat},  # pyre-ignore[arg-type]
        "most_accessed": [(r[0][:50], r[1]) for r in most_accessed],  # pyre-ignore[arg-type]
    }


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Test Semantic Memory ===")
    print("Nota: Requiere ollama con nomic-embed-text corriendo")
    
    stats = memory_stats()
    print(f"Estado actual: {stats}")
