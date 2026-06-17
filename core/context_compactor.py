"""
EIDOS core/context_compactor.py — Context Window Manager
=========================================================
Compresión de contexto en 4 capas para sesiones autónomas largas.
Basado en patrones de:
  - tinyclaw: 4-layer compaction pipeline
  - openclaw_cli: context window guard
  - ClosedClaw: ClawDense token compression

Crítico para `eidos libre 24h+` — sin esto, el contexto crece
indefinidamente y el modelo pierde coherencia.

Capas:
  L0 — Raw: mensajes sin comprimir (recientes)
  L1 — Dedup: eliminación de duplicados por shingle hashing
  L2 — Summary: resumen por LLM de mensajes antiguos
  L3 — Archive: resúmenes comprimidos de sesiones pasadas

Uso:
    from core.context_compactor import ContextCompactor

    compactor = ContextCompactor(max_tokens=4096)
    compactor.add("user", "escanea 192.168.1.1")
    compactor.add("assistant", "Escaneando con nmap...")
    compactor.add("tool_result", "PORT  STATE  SERVICE\\n22 open ssh\\n80 open http")

    # Cuando el contexto crece demasiado:
    messages = compactor.get_context()  # Retorna mensajes compactados
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import logging
log = logging.getLogger("eidos.context")


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_MAX_TOKENS = 4096       # Ventana de contexto para Ollama 8b models
CHARS_PER_TOKEN = 4             # Estimación conservadora
L0_MAX_MESSAGES = 20            # Mensajes raw recientes a mantener
L1_DEDUP_WINDOW = 50            # Ventana de dedup en mensajes
L2_SUMMARY_TRIGGER = 30         # Cuándo generar resumen L2
L3_ARCHIVE_DIR = Path.home() / ".eidos" / "context_archive"

# Shingle size para dedup
SHINGLE_SIZE = 5  # n-gramas de palabras


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA TYPES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Message:
    role: str           # user, assistant, system, tool_result
    content: str
    timestamp: float = field(default_factory=time.time)
    token_est: int = 0  # Estimación de tokens
    compressed: bool = False
    shingle_hash: str = ""

    def __post_init__(self):
        if not self.token_est:
            self.token_est = max(1, len(self.content) // CHARS_PER_TOKEN)
        if not self.shingle_hash:
            self.shingle_hash = _shingle_hash(self.content)


def _shingle_hash(text: str, n: int = SHINGLE_SIZE) -> str:
    """Hash basado en n-gramas de palabras para dedup."""
    words = text.lower().split()
    if len(words) < n:
        return hashlib.md5(text.encode()).hexdigest()[:12]
    shingles = set()
    for i in range(len(words) - n + 1):
        shingle = " ".join(words[i:i+n])
        shingles.add(shingle)
    combined = "|".join(sorted(shingles))
    return hashlib.md5(combined.encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════════════════════
#  RULE-BASED PRE-COMPRESSION (Layer 0.5)
# ═══════════════════════════════════════════════════════════════════════════════

def _precompress(content: str) -> str:
    """
    Compresión basada en reglas antes de dedup/summary.

    - Elimina líneas vacías redundantes
    - Trunca outputs largos de herramientas
    - Comprime repeticiones
    - Elimina ANSI escape codes
    """
    import re

    # Eliminar ANSI escape codes
    content = re.sub(r'\x1b\[[0-9;]*m', '', content)

    # Colapsar múltiples líneas vacías en una
    content = re.sub(r'\n{3,}', '\n\n', content)

    # Truncar bloques de código/output muy largos (>500 chars)
    lines = content.split('\n')
    if len(lines) > 30:
        # Mantener primeras 15 y últimas 10, truncar medio
        kept = lines[:15] + [f"[... {len(lines)-25} lines truncated ...]"] + lines[-10:]
        content = '\n'.join(kept)

    # Colapsar whitespace excesivo
    content = re.sub(r'[ \t]{4,}', '  ', content)

    return content.strip()


# ═══════════════════════════════════════════════════════════════════════════════
#  CONTEXT COMPACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class ContextCompactor:
    """
    Manager de contexto con compresión en 4 capas.

    L0: mensajes raw recientes (sin tocar)
    L1: mensajes deduplicados
    L2: resúmenes generados por LLM
    L3: archivo de sesiones pasadas
    """

    def __init__(self, max_tokens: int = DEFAULT_MAX_TOKENS,
                 summarizer: Optional[callable] = None):
        """
        Args:
            max_tokens: Tokens máximos de contexto
            summarizer: Función que recibe texto y retorna resumen.
                       Si None, usa compresión sin LLM.
        """
        self.max_tokens = max_tokens
        self.summarizer = summarizer

        # L0: mensajes raw recientes
        self.l0_messages: deque[Message] = deque(maxlen=L0_MAX_MESSAGES * 2)

        # L1: hashes vistos para dedup
        self._seen_hashes: set[str] = set()

        # L2: resúmenes acumulados
        self.l2_summaries: list[str] = []

        # L3: archivo
        L3_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # Stats
        self._total_added = 0
        self._total_deduped = 0
        self._total_compressed = 0

    def add(self, role: str, content: str) -> None:
        """Añade un mensaje al contexto."""
        # Pre-compression
        compressed_content = _precompress(content)
        msg = Message(role=role, content=compressed_content)
        self._total_added += 1

        # Dedup check
        if msg.shingle_hash in self._seen_hashes and role == "tool_result":
            self._total_deduped += 1
            return  # Skip duplicados de tool results

        self._seen_hashes.add(msg.shingle_hash)
        self.l0_messages.append(msg)

        # Trigger compaction si excede límite
        total_tokens = sum(m.token_est for m in self.l0_messages)
        if total_tokens > self.max_tokens:
            self._compact()

    def _compact(self) -> None:
        """Ejecuta compactación del contexto."""
        messages = list(self.l0_messages)
        if len(messages) <= L0_MAX_MESSAGES:
            return

        # Separar: mantener últimos L0_MAX_MESSAGES, comprimir el resto
        to_compress = messages[:-L0_MAX_MESSAGES]
        to_keep = messages[-L0_MAX_MESSAGES:]

        # Generar resumen de mensajes antiguos
        summary_text = self._generate_summary(to_compress)
        if summary_text:
            self.l2_summaries.append(summary_text)
            self._total_compressed += len(to_compress)

        # Reemplazar l0 con solo los recientes
        self.l0_messages.clear()
        for m in to_keep:
            self.l0_messages.append(m)

    def _generate_summary(self, messages: list[Message]) -> str:
        """Genera resumen de mensajes, con o sin LLM."""
        if not messages:
            return ""

        # Construir texto a resumir
        text_parts = []
        for m in messages:
            text_parts.append(f"[{m.role}] {m.content[:200]}")
        full_text = "\n".join(text_parts)

        # Si hay summarizer (LLM), usarlo
        if self.summarizer:
            try:
                return self.summarizer(full_text)
            except Exception as e:
                log.warning(f"[Compactor] LLM summary failed: {e}")

        # Fallback: resumen heurístico
        return self._heuristic_summary(messages)

    def _heuristic_summary(self, messages: list[Message]) -> str:
        """Resumen sin LLM — extrae las partes más importantes."""
        parts = []
        parts.append(f"[Context summary: {len(messages)} messages compressed]")

        # Extraer solo user requests y key results
        for m in messages:
            if m.role == "user":
                parts.append(f"  User asked: {m.content[:100]}")
            elif m.role == "assistant" and len(m.content) < 200:
                parts.append(f"  EIDOS: {m.content[:100]}")
            elif m.role == "tool_result":
                # Solo primera línea de tool results
                first_line = m.content.split('\n')[0][:80]
                parts.append(f"  Tool result: {first_line}")

        return "\n".join(parts)

    def get_context(self) -> list[dict]:
        """
        Retorna el contexto compactado como lista de mensajes.

        Formato compatible con Ollama chat API.
        """
        result = []

        # Incluir resúmenes L2 como contexto del sistema
        if self.l2_summaries:
            combined_summary = "\n---\n".join(self.l2_summaries[-3:])  # Max 3 summaries
            result.append({
                "role": "system",
                "content": f"[Previous context summary]\n{combined_summary}"
            })

        # Incluir L0 messages
        for m in self.l0_messages:
            result.append({
                "role": m.role if m.role != "tool_result" else "system",
                "content": m.content,
            })

        return result

    def get_token_usage(self) -> dict:
        """Retorna estadísticas de uso de tokens."""
        l0_tokens = sum(m.token_est for m in self.l0_messages)
        l2_tokens = sum(len(s) // CHARS_PER_TOKEN for s in self.l2_summaries)
        return {
            "l0_messages": len(self.l0_messages),
            "l0_tokens": l0_tokens,
            "l2_summaries": len(self.l2_summaries),
            "l2_tokens": l2_tokens,
            "total_tokens": l0_tokens + l2_tokens,
            "max_tokens": self.max_tokens,
            "utilization": round((l0_tokens + l2_tokens) / self.max_tokens, 2),
            "total_added": self._total_added,
            "total_deduped": self._total_deduped,
            "total_compressed": self._total_compressed,
        }

    def archive_session(self, session_id: str = None) -> str:
        """Archiva la sesión actual a L3 (disco)."""
        if not session_id:
            session_id = f"session_{int(time.time())}"

        archive_path = L3_ARCHIVE_DIR / f"{session_id}.json"
        data = {
            "session_id": session_id,
            "timestamp": time.time(),
            "messages": [{"role": m.role, "content": m.content[:500]} for m in self.l0_messages],
            "summaries": self.l2_summaries,
            "stats": self.get_token_usage(),
        }

        with open(archive_path, 'w') as f:
            json.dump(data, f, indent=2)

        log.info(f"[Compactor] Session archived: {archive_path}")
        return str(archive_path)

    def load_archive(self, session_id: str) -> bool:
        """Carga resúmenes de una sesión archivada."""
        archive_path = L3_ARCHIVE_DIR / f"{session_id}.json"
        if not archive_path.exists():
            return False

        with open(archive_path) as f:
            data = json.load(f)

        # Cargar solo los resúmenes (no los mensajes raw)
        if data.get("summaries"):
            self.l2_summaries.extend(data["summaries"])
        return True

    def clear(self) -> None:
        """Limpia todo el contexto."""
        self.l0_messages.clear()
        self.l2_summaries.clear()
        self._seen_hashes.clear()


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_compactor: Optional[ContextCompactor] = None


def get_compactor(max_tokens: int = DEFAULT_MAX_TOKENS) -> ContextCompactor:
    global _compactor
    if _compactor is None:
        _compactor = ContextCompactor(max_tokens=max_tokens)
        log.info(f"📦 [Context Compactor] Inicializado (max {max_tokens} tokens)")
    return _compactor
