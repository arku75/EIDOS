"""
EIDOS core/ollama_fallback.py — Resiliencia cuando Ollama no está disponible
============================================================================
Cuando Ollama cae, Colony no muere — responde desde su propio conocimiento.

Estrategia de fallback (por orden):
1. Buscar en knowledge_nodes respuestas semánticamente similares
2. Buscar en thoughts recientes relevantes
3. Respuesta de emergencia desde catchphrases del agente

Colony detecta Ollama down automáticamente y activa este fallback.
"""
from __future__ import annotations

import sqlite3
import os
import time
import threading
import logging
from typing import Optional, Dict, Any, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.ollama_fallback")

BRAIN_DB   = os.path.expanduser("~/.eidos/evolution_brain.db")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Cache de disponibilidad para no hacer ping en cada request
_available_cache: Tuple[bool, float] = (True, 0.0)
_cache_ttl = 15.0  # segundos
_cache_lock = threading.Lock()


def is_ollama_available(url: str = OLLAMA_URL) -> bool:
    """Verifica si Ollama está disponible. Cachea el resultado 15 segundos."""
    global _available_cache
    with _cache_lock:
        ok, ts = _available_cache
        if time.time() - ts < _cache_ttl:
            return ok
    try:
        import urllib.request
        urllib.request.urlopen(f"{url}/api/tags", timeout=3)
        result = True
    except Exception:
        result = False
    with _cache_lock:
        _available_cache = (result, time.time())
    if not result:
        log.warning("Ollama no disponible en %s — modo fallback activo", url)
    return result


def invalidate_cache() -> None:
    """Fuerza re-check en la próxima llamada."""
    global _available_cache
    with _cache_lock:
        _available_cache = (True, 0.0)


def get_fallback_response(prompt: str, agent_id: str = "colony") -> str:
    """
    Genera respuesta local sin Ollama usando conocimiento almacenado.
    Orden: ChromaDB semántico → knowledge_nodes LIKE → thoughts recientes.
    """
    # 1. Intentar ChromaDB primero (semántico, mejor calidad)
    try:
        from core.colony_chroma import get_chroma_memory
        chroma = get_chroma_memory()
        if chroma.is_ready():
            results = chroma.search(prompt[:300], limit=4, min_score=0.35)
            if len(results) >= 2:
                parts = [f"• {r['concept']}: {r['definition'][:150]}" for r in results[:3]]
                return "[Conocimiento semántico — Ollama no disponible]\n" + "\n".join(parts)
    except Exception:
        pass  # error no crítico, continuar
    prompt_lower = prompt.lower()
    keywords = [w for w in prompt_lower.split() if len(w) > 3][:6]

    try:
        conn = get_conn(BRAIN_DB, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")

        results = []

        # 1. Búsqueda exacta por concepto
        for kw in keywords[:3]:
            rows = conn.execute(
                "SELECT concept, definition, confidence FROM knowledge_nodes "
                "WHERE concept LIKE ? ORDER BY confidence DESC, usage_count DESC LIMIT 3",
                (f"%{kw}%",)
            ).fetchall()
            results.extend(rows)

        # 2. Búsqueda en definiciones si no hay suficiente
        if len(results) < 2:
            for kw in keywords[:2]:
                rows = conn.execute(
                    "SELECT concept, definition, confidence FROM knowledge_nodes "
                    "WHERE definition LIKE ? ORDER BY confidence DESC LIMIT 2",
                    (f"%{kw}%",)
                ).fetchall()
                results.extend(rows)

        # 3. Thoughts recientes relevantes
        recent_thoughts = []
        if keywords:
            for kw in keywords[:2]:
                rows = conn.execute(
                    "SELECT content FROM thoughts WHERE content LIKE ? "
                    "ORDER BY timestamp DESC LIMIT 2",
                    (f"%{kw}%",)
                ).fetchall()
                recent_thoughts.extend([r[0] for r in rows])

        pass  # S109: get_conn no necesita close()
        # Construir respuesta
        if results:
            seen = set()
            unique = []
            for concept, definition, conf in results:
                if concept not in seen:
                    seen.add(concept)
                    unique.append(f"• {concept}: {definition[:150]}")
                if len(unique) >= 4:
                    break

            response = f"[Conocimiento propio — Ollama no disponible]\n" + "\n".join(unique)
            if recent_thoughts:
                response += f"\n\nPensamiento reciente: {recent_thoughts[0][:200]}"
            return response

        if recent_thoughts:
            return f"[Modo offline] {recent_thoughts[0][:300]}"

        return ("[Modo offline] Ollama no está disponible en este momento. "
                "Mi base de conocimiento aún no tiene suficiente información sobre este tema. "
                "Puedo continuar cuando Ollama vuelva.")

    except Exception as e:
        log.debug("fallback DB query failed: %s", e)
        return f"[Modo offline] No puedo acceder a mi conocimiento ahora. Error: {e}"


class OllamaFallback:
    """
    Wrapper thread-safe que detecta Ollama down y activa fallback automáticamente.
    Úsalo en lugar de llamar a Ollama directamente para mayor resiliencia.
    """

    def __init__(self, url: str = OLLAMA_URL):
        self.ollama_url = url

    def is_available(self) -> bool:
        return is_ollama_available(self.ollama_url)

    def generate(self, prompt: str, model: str = "lfm2.5-thinking:1.2b",
                 agent_id: str = "colony") -> Dict[str, Any]:
        if self.is_available():
            return {"response": None, "fallback": False, "use_ollama": True}
        return {
            "response": get_fallback_response(prompt, agent_id),
            "fallback": True,
            "use_ollama": False,
            "offline_reason": "Ollama no disponible",
        }

    def get_status(self) -> Dict[str, Any]:
        ok = self.is_available()
        return {
            "ollama_available": ok,
            "mode": "normal" if ok else "fallback",
            "url": self.ollama_url,
        }


_instance: Optional[OllamaFallback] = None
_lock = threading.Lock()


def get_fallback() -> OllamaFallback:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = OllamaFallback()
    return _instance
