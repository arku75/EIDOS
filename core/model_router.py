"""
core/model_router.py — Router de modelo ligero/pesado para EIDOS

Arquitectura de dos nodos:
  - Nodo ligero (local CPU): Ollama local para consultas rápidas, routing, orquestación
  - Nodo pesado (remoto GPU): endpoint configurable para inferencia compleja

Configuración vía env vars:
  HEAVY_NODE_URL=http://host:port/v1/chat/completions
  HEAVY_NODE_API_KEY=sk-...
  HEAVY_NODE_MODEL=deepseek-v4-pro
  LIGHT_NODE_MODEL=lfm2.5-thinking:1.2b
  ROUTER_COMPLEXITY_THRESHOLD=0.6  # 0-1, qué tan compleja debe ser una query para ir a heavy
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.model_router")

HEAVY_NODE_URL = os.environ.get("HEAVY_NODE_URL", "")
HEAVY_NODE_API_KEY = os.environ.get("HEAVY_NODE_API_KEY", "")
HEAVY_NODE_MODEL = os.environ.get("HEAVY_NODE_MODEL", "deepseek-v4-pro")
LIGHT_NODE_MODEL = os.environ.get("LIGHT_NODE_MODEL", "lfm2.5-thinking:1.2b")
ROUTER_COMPLEXITY_THRESHOLD = float(os.environ.get("ROUTER_COMPLEXITY_THRESHOLD", "0.6"))


class ModelRouter:
    """Router que decide si una consulta va a nodo ligero (local CPU) o pesado (remoto GPU)."""

    COMPLEX_KEYWORDS = {
        "high": [
            "analiza profundamente", "compara y contrasta", "razonamiento complejo",
            "código extenso", "debug multi-archivo", "arquitectura de sistema",
            "planea", "diseña una arquitectura", "implementa un sistema",
            "refactorización completa", "deep analysis", "system design",
            "complex reasoning", "multi-step", "multistep",
        ],
        "medium": [
            "explícame en detalle", "cómo funciona", "qué diferencia hay",
            "cuál es la mejor", "por qué", "debería", "recomienda",
            "explain in detail", "how does", "what is the difference",
            "what is the best", "should i", "recommend",
        ],
    }

    def _estimate_complexity(self, message: str) -> float:
        """Estima complejidad 0-1 de un mensaje."""
        msg_lower = message.lower()
        score = 0.0

        msg_len = len(message.split())
        if msg_len > 50:
            score += 0.2
        if msg_len > 100:
            score += 0.15
        if msg_len > 200:
            score += 0.15

        for kw in self.COMPLEX_KEYWORDS["high"]:
            if kw in msg_lower:
                score += 0.35
                break

        for kw in self.COMPLEX_KEYWORDS["medium"]:
            if kw in msg_lower:
                score += 0.2
                break

        if "```" in message or "def " in message or "class " in message:
            score += 0.25

        return min(score, 1.0)

    def route(self, message: str, context: Optional[List[dict]] = None) -> Dict[str, Any]:
        """Decide a qué nodo enviar la consulta y la ejecuta."""
        complexity = self._estimate_complexity(message)
        t0 = time.time()

        if complexity >= ROUTER_COMPLEXITY_THRESHOLD and HEAVY_NODE_URL:
            return self._call_heavy(message, context, complexity)
        else:
            return self._call_light(message, context, complexity)

    def _call_light(self, message: str, context: Optional[List[dict]] = None, complexity: float = 0.0) -> Dict[str, Any]:
        """Llama a Ollama local (nodo ligero)."""
        t0 = time.time()
        try:
            messages = context or []
            if not any(m.get("role") == "user" for m in messages):
                messages.append({"role": "user", "content": message})

            payload = json.dumps({
                "model": LIGHT_NODE_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"num_predict": 512},
            }).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                content = data.get("message", {}).get("content", "")
        except Exception as e:
            content = f"[Light node error: {e}]"

        return {
            "content": content,
            "node": "light",
            "model": LIGHT_NODE_MODEL,
            "complexity": round(complexity, 3),
            "elapsed_s": round(time.time() - t0, 3),
        }

    def _call_heavy(self, message: str, context: Optional[List[dict]] = None, complexity: float = 0.0) -> Dict[str, Any]:
        """Llama a endpoint remoto (nodo pesado GPU)."""
        t0 = time.time()
        try:
            messages = context or []
            if not any(m.get("role") == "user" for m in messages):
                messages.append({"role": "user", "content": message})

            payload = json.dumps({
                "model": HEAVY_NODE_MODEL,
                "messages": messages,
                "stream": False,
                "max_tokens": 2048,
            }).encode()
            req = urllib.request.Request(
                HEAVY_NODE_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {HEAVY_NODE_API_KEY}",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not content:
                    content = data.get("response", "")
        except Exception as e:
            content = f"[Heavy node error: {e}]"

        return {
            "content": content,
            "node": "heavy",
            "model": HEAVY_NODE_MODEL,
            "complexity": round(complexity, 3),
            "elapsed_s": round(time.time() - t0, 3),
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "light_model": LIGHT_NODE_MODEL,
            "heavy_configured": bool(HEAVY_NODE_URL),
            "heavy_model": HEAVY_NODE_MODEL if HEAVY_NODE_URL else None,
            "heavy_url": HEAVY_NODE_URL if HEAVY_NODE_URL else None,
            "complexity_threshold": ROUTER_COMPLEXITY_THRESHOLD,
        }


_router: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router
