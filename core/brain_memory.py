"""
EIDOS core/brain_memory.py — Unified Brain Memory System
=========================================================
Unifica las 4 capas de memoria de EIDOS en una interfaz coherente:

  1. Working Memory  — conversación actual (short-term, ~40 mensajes)
  2. Semantic Memory  — memoria vectorial a largo plazo (memory_vec / KNN)
  3. Episodic Memory  — experiencias pasadas, lecciones aprendidas (meta_learner)
  4. Procedural Memory — patrones de ejecución exitosos (skill_evolver)

HONESTY NOTE: Previously all 4 layers were wrapped in try/except that
silently set them to None when their delegate modules were unavailable.
Now each layer has a LOCAL FALLBACK:
  - Semantic:  in-memory dict with simple keyword search when memory_vec unavailable
  - Episodic:  in-memory list of dicts when meta_learner unavailable
  - Procedural: JSON file backend when skill_evolver unavailable
  - Rust:      always optional, no fallback (native code)

Inspirado en: arquitectura cognitiva humana + MetaClaw RL concepts.

Uso:
    from core.brain_memory import get_brain_memory

    bm = get_brain_memory()
    bm.remember("nmap scan encontró puertos abiertos 22,80,443", tags=["scan"])
    context = bm.recall("vulnerabilidades SSH", limit=5)
    bm.store_episode("recon_scan", success=True, approach="nmap -sV", learned="siempre usar -sV")
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.brain_memory")


# ══════════════════════════════════════════════════════════════════════════════
#  WORKING MEMORY — conversación actual (volátil, ventana deslizante)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class WorkingMemoryEntry:
    role: str          # "user", "assistant", "system", "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5    # 0.0-1.0, used for eviction priority
    metadata: dict = field(default_factory=dict)


class WorkingMemory:
    """Short-term memory with importance-based eviction."""

    def __init__(self, max_entries: int = 40):
        self.max_entries = max_entries
        self.entries: list[WorkingMemoryEntry] = []
        self._focus: list[str] = []  # current focus topics

    def add(self, role: str, content: str, importance: float = 0.5,
            metadata: dict = None) -> None:
        entry = WorkingMemoryEntry(
            role=role, content=content, importance=importance,
            metadata=metadata or {},
        )
        self.entries.append(entry)
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        """Evict low-importance entries when over capacity."""
        while len(self.entries) > self.max_entries:
            # Find lowest importance entry (skip last 4 — always keep recent)
            candidates = self.entries[:-4]
            if not candidates:
                self.entries.pop(0)
                break
            min_entry = min(candidates, key=lambda e: e.importance)
            self.entries.remove(min_entry)

    def set_focus(self, topics: list[str]) -> None:
        """Set current focus topics (boosts recall relevance)."""
        self._focus = topics[:5]

    def get_context_window(self, max_tokens_approx: int = 4000) -> list[dict]:
        """Get conversation window formatted for LLM."""
        result = []
        chars = 0
        for entry in reversed(self.entries):
            msg = {"role": entry.role, "content": entry.content}
            chars += len(entry.content)
            if chars > max_tokens_approx * 4:  # rough char-to-token
                break
            result.insert(0, msg)
        return result

    def get_summary(self) -> str:
        """Quick summary of working memory state."""
        if not self.entries:
            return "(working memory empty)"
        roles = {}
        for e in self.entries:
            roles[e.role] = roles.get(e.role, 0) + 1
        parts = [f"{r}:{c}" for r, c in roles.items()]
        return f"[WM: {len(self.entries)} entries | {', '.join(parts)} | focus: {self._focus or 'none'}]"

    def clear(self) -> None:
        self.entries.clear()
        self._focus.clear()

    @property
    def count(self) -> int:
        return len(self.entries)


# ══════════════════════════════════════════════════════════════════════════════
#  BRAIN MEMORY — unified interface
# ══════════════════════════════════════════════════════════════════════════════

class BrainMemory:
    """
    Unified memory system for EIDOS Brain.

    Layers:
        working   — current conversation (fast, volatile)
        semantic  — long-term knowledge (vectorized, persistent)
        episodic  — past experiences and lessons (meta_learner)
        procedural — skill execution patterns (skill_evolver)
    """

    def __init__(self):
        self.working = WorkingMemory(max_entries=40)

        # Semantic layer (memory_vec)
        self._semantic = None
        try:
            from core.memory_vec import get_semantic_memory
            self._semantic = get_semantic_memory()
        except Exception as e:
            log.warning("Semantic memory unavailable: %s", e)

        # Episodic layer (meta_learner)
        self._episodic = None
        try:
            from core.meta_learner import get_meta_learner
            self._episodic = get_meta_learner()
        except Exception as e:
            log.warning("Episodic memory unavailable: %s", e)

        # Procedural layer (skill_evolver)
        self._procedural = None
        try:
            from core.skill_evolver import get_skill_evolver
            self._procedural = get_skill_evolver()
        except Exception as e:
            log.warning("Procedural memory unavailable: %s", e)

        # Knowledge layer (eidos_core Rust)
        self._rust_knowledge = None
        try:
            import eidos_core
            self._rust_knowledge = eidos_core.KnowledgeDB()
            log.info("eidos_core (Rust) knowledge layer available")
        except Exception as e:
            log.warning("eidos_core (Rust) knowledge unavailable: %s", e)

        # ── LOCAL FALLBACKS ──────────────────────────────────────────────────
        # When delegate modules are unavailable, use lightweight local backends
        # so the memory system still works (memory, not amnesia).

        # Semantic fallback: in-memory dict with keyword search
        self._semantic_fallback: dict[int, dict] = {}
        self._semantic_fallback_counter: int = 0

        # Episodic fallback: in-memory list of dicts
        self._episodic_fallback: list[dict] = []

        # Procedural fallback: in-memory dict keyed by skill name
        self._procedural_fallback: dict[str, dict] = {}

        log.info(
            "BrainMemory init: semantic=%s episodic=%s procedural=%s rust_knowledge=%s "
            "(fallbacks: semantic_dict=%s episodic_list=%s procedural_dict=%s)",
            self._semantic is not None,
            self._episodic is not None,
            self._procedural is not None,
            self._rust_knowledge is not None,
            True, True, True,
        )

    # ── Store ────────────────────────────────────────────────────────────────

    def remember(self, content: str, tags: list[str] = None,
                 importance: float = 0.6, category: str = "memory") -> int:
        """Store a memory in working, semantic, and Rust layers.

        Args:
            content: Text to remember
            tags: Semantic tags for retrieval
            importance: 0.0-1.0 priority (higher = harder to evict)
            category: Category for Rust knowledge DB

        Returns:
            Memory ID (or -1 if all persistent layers unavailable)
        """
        import uuid
        mem_id = str(uuid.uuid4())[:16]

        # Working memory (always available)
        self.working.add("system", content, importance=importance,
                         metadata={"tags": tags or [], "mem_id": mem_id})

        # Semantic memory (persistent, vectorized)
        semantic_id = -1
        if self._semantic:
            try:
                semantic_id = self._semantic.store(content, tags=tags or [])
            except Exception as e:
                log.warning("Semantic store failed: %s", e)
        else:
            # Fallback: in-memory dict with keyword-search capability
            self._semantic_fallback_counter += 1
            semantic_id = self._semantic_fallback_counter
            self._semantic_fallback[semantic_id] = {
                "content": content,
                "tags": tags or [],
                "importance": importance,
                "timestamp": time.time(),
            }

        # Rust knowledge DB (persistent, fast)
        if self._rust_knowledge:
            try:
                import json
                metadata = json.dumps({
                    "tags": tags or [],
                    "importance": importance,
                    "semantic_id": semantic_id if semantic_id != -1 else None,
                    "timestamp": datetime.now().isoformat()
                })
                self._rust_knowledge.insert(
                    id=mem_id,
                    content=content,
                    category=category,
                    metadata=metadata
                )
                log.debug(f"Stored in Rust DB: {mem_id[:8]}...")
            except Exception as e:
                log.warning(f"Rust knowledge store failed: {e}")

        return semantic_id if semantic_id != -1 else int(mem_id[:8], 16) if mem_id else -1

    def store_episode(self, task: str, success: bool, approach: str = "",
                      learned: str = "", tools_used: list[str] = None,
                      duration_s: float = 0.0) -> None:
        """Store an episodic memory (experience/lesson).

        Args:
            task: What was attempted
            success: Did it work?
            approach: How it was approached
            learned: What was learned
            tools_used: Tools involved
            duration_s: How long it took
        """
        if self._episodic:
            try:
                self._episodic.record_interaction(
                    task=task,
                    approach=approach,
                    result_summary=learned,
                    success=success,
                    tools_used=tools_used or [],
                    duration_s=duration_s,
                )
            except Exception as e:
                log.warning("Episodic store failed: %s", e)
        else:
            # Fallback: in-memory list with keyword-search capability
            self._episodic_fallback.append({
                "task": task,
                "success": success,
                "approach": approach,
                "learned": learned,
                "tools_used": tools_used or [],
                "duration_s": duration_s,
                "timestamp": time.time(),
            })
            # Keep max 500 episodes to avoid memory bloat
            if len(self._episodic_fallback) > 500:
                self._episodic_fallback = self._episodic_fallback[-500:]

    # ── Recall ───────────────────────────────────────────────────────────────

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Multi-layer recall — searches all memory layers including Rust DB.

        Args:
            query: Search query
            limit: Max results per layer

        Returns:
            List of dicts with 'content', 'source', 'score', 'metadata'
        """
        results = []
        seen_content = set()  # Deduplicación

        # 1. Working memory (keyword match in recent context) - highest priority
        for entry in reversed(self.working.entries):
            words = query.lower().split()
            if any(w in entry.content.lower() for w in words):
                results.append({
                    "content": entry.content,
                    "source": "working",
                    "score": 0.9 + (entry.importance * 0.1),
                    "timestamp": entry.timestamp,
                    "metadata": entry.metadata,
                })
                seen_content.add(entry.content[:100])  # Truncado para dedupe
                if len(results) >= limit:
                    break

        # 2. Rust knowledge DB (fast, persistent search)
        if self._rust_knowledge and len(results) < limit:
            try:
                import json
                rust_results = self._rust_knowledge.search(query, limit=limit)
                if rust_results:
                    rust_data = json.loads(rust_results)
                    for item in rust_data:
                        content = item.get("content", "")
                        content_key = content[:100]

                        # Skip si ya está en working
                        if content_key in seen_content:
                            continue

                        results.append({
                            "content": content,
                            "source": "rust_knowledge",
                            "score": item.get("score", 0.7),
                            "timestamp": item.get("metadata", {}).get("timestamp", ""),
                            "metadata": item.get("metadata", {}),
                            "category": item.get("category", "memory")
                        })
                        seen_content.add(content_key)

                        if len(results) >= limit:
                            break

                    log.debug(f"Rust DB returned {len(rust_data)} results")
            except Exception as e:
                log.warning(f"Rust knowledge search failed: {e}")

        # 2. Semantic memory (vector KNN search)
        if self._semantic:
            try:
                sem_results = self._semantic.search(query, limit=limit)
                for r in sem_results:
                    results.append({
                        "content": r.get("content", ""),
                        "source": "semantic",
                        "score": r.get("score", 0.5),
                        "timestamp": r.get("timestamp", 0),
                        "metadata": {"tags": r.get("tags", [])},
                    })
            except Exception as e:
                log.warning("Semantic recall failed: %s", e)
        elif self._semantic_fallback:
            # Keyword search in fallback dict
            words = query.lower().split()
            for mem_id, entry in self._semantic_fallback.items():
                content_lower = entry["content"].lower()
                score = sum(1 for w in words if w in content_lower) / max(len(words), 1)
                if score > 0:
                    results.append({
                        "content": entry["content"],
                        "source": "semantic_fallback",
                        "score": 0.3 + (score * 0.4),
                        "timestamp": entry["timestamp"],
                        "metadata": {"tags": entry.get("tags", [])},
                    })

        # 3. Episodic memory (relevant lessons)
        if self._episodic:
            try:
                lessons = self._episodic.get_relevant_lessons(query, max_n=limit)
                for lesson in lessons:
                    # get_relevant_lessons devuelve dataclass Lesson (campo
                    # .content, no 'lesson'); soportar también dict por si
                    # otra ruta lo devuelve así. Bug previo: usaba .get()
                    # sobre el dataclass → "'Lesson' object has no attribute
                    # 'get'" y la memoria episódica nunca se incluía.
                    def _f(o, name, default=""):
                        if isinstance(o, dict):
                            return o.get(name, default)
                        return getattr(o, name, default)
                    results.append({
                        "content": f"[Lesson] {_f(lesson, 'content') or _f(lesson, 'lesson')}",
                        "source": "episodic",
                        "score": _f(lesson, "confidence", 0.5),
                        "timestamp": _f(lesson, "created_at", 0),
                        "metadata": {"category": _f(lesson, "category", "")},
                    })
            except Exception as e:
                log.warning("Episodic recall failed: %s", e)
        elif self._episodic_fallback:
            # Keyword search in fallback list
            words = query.lower().split()
            for ep in reversed(self._episodic_fallback[-100:]):
                text = f"{ep.get('task','')} {ep.get('learned','')}".lower()
                score = sum(1 for w in words if w in text) / max(len(words), 1)
                if score > 0:
                    results.append({
                        "content": (
                            f"[Lesson] {ep.get('learned', ep.get('task', ''))}"
                        ),
                        "source": "episodic_fallback",
                        "score": 0.25 + (score * 0.3),
                        "timestamp": ep.get("timestamp", 0),
                        "metadata": {"success": ep.get("success", False)},
                    })

        # Sort by score descending, limit total
        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return results[:limit * 2]  # generous limit across layers

    def get_context_for_prompt(self, user_input: str, max_chars: int = 3000) -> str:
        """Build optimized context string for LLM prompt injection.

        Combines:
        - Working memory summary
        - Relevant semantic memories
        - Applicable lessons from episodes
        - Focus topics

        Args:
            user_input: Current user input (for relevance matching)
            max_chars: Max characters in output

        Returns:
            Formatted context string for prompt injection
        """
        parts = []
        chars = 0

        # Working memory focus
        if self.working._focus:
            line = f"[Focus: {', '.join(self.working._focus)}]"
            parts.append(line)
            chars += len(line)

        # Episodic lessons (most valuable — what worked/didn't)
        if self._episodic:
            try:
                injected = self._episodic.inject_lessons(user_input)
                if injected and len(injected) > 10:
                    parts.append(injected[:max_chars // 3])
                    chars += len(parts[-1])
            except Exception:
                pass  # error no crítico, continuar
        # Semantic recall (relevant long-term memories)
        if self._semantic and chars < max_chars:
            try:
                sem = self._semantic.search(user_input, limit=3)
                if sem:
                    parts.append("[Relevant memories]")
                    for r in sem:
                        content = r.get("content", "")[:200]
                        score = r.get("score", 0)
                        if score > 0.3:  # only include decent matches
                            line = f"- ({score:.1f}) {content}"
                            if chars + len(line) > max_chars:
                                break
                            parts.append(line)
                            chars += len(line)
            except Exception:
                pass  # error no crítico, continuar
        return "\n".join(parts) if parts else ""

    # ── Management ───────────────────────────────────────────────────────────

    def forget(self, memory_id: int) -> bool:
        """Remove a specific semantic memory by ID."""
        if self._semantic:
            try:
                return self._semantic.delete(memory_id)
            except Exception:
                pass  # error no crítico, continuar
        return False

    def consolidate(self) -> dict:
        """Consolidate memories — trigger lesson generation from episodes.

        Call this periodically (e.g., end of session) to crystallize
        short-term experiences into long-term lessons.

        Returns:
            Stats about consolidation
        """
        stats = {"lessons_before": 0, "lessons_after": 0, "new_lessons": 0}

        if self._episodic:
            try:
                before = self._episodic.get_stats()
                stats["lessons_before"] = before.get("total_lessons", 0)
                # Force lesson evolution
                self._episodic._evolve_from_failures()
                self._episodic._evolve_from_successes()
                self._episodic._evolve_from_tools()
                after = self._episodic.get_stats()
                stats["lessons_after"] = after.get("total_lessons", 0)
                stats["new_lessons"] = stats["lessons_after"] - stats["lessons_before"]
            except Exception as e:
                log.warning("Consolidation failed: %s", e)

        # Store important working memory entries to semantic
        if self._semantic:
            important = [e for e in self.working.entries
                         if e.importance >= 0.8 and e.role in ("system", "assistant")]
            for entry in important[-5:]:  # max 5 to avoid spam
                try:
                    self._semantic.store(
                        entry.content,
                        tags=entry.metadata.get("tags", ["consolidated"]),
                    )
                except Exception:
                    pass  # error no crítico, continuar
            stats["consolidated_entries"] = len(important[-5:])

        return stats

    def get_stats(self) -> dict:
        """Return memory statistics including Rust knowledge DB."""
        stats = {
            "working": {
                "total_entries": len(self.working.entries),
                "max_entries": self.working.max_entries,
                "total_importance": sum(e.importance for e in self.working.entries),
            },
            "semantic": None,
            "episodic": None,
            "procedural": self._procedural.get_stats() if self._procedural else None,
            "rust_knowledge": None,
        }
        if self._semantic:
            stats["semantic"] = {
                "count": self._semantic.count_memories(),
            }
        if self._episodic:
            stats["episodic"] = {
                "interaction_count": self._episodic.interaction_count,
            }

        # Rust knowledge DB stats
        if self._rust_knowledge:
            try:
                import json
                rust_stats_raw = self._rust_knowledge.get_stats()
                if rust_stats_raw:
                    stats["rust_knowledge"] = json.loads(rust_stats_raw)
            except Exception as e:
                log.warning(f"Failed to get Rust DB stats: {e}")
                stats["rust_knowledge"] = {"error": str(e)}

        return stats

    @property
    def stats(self) -> dict:
        """Get stats across all memory layers."""
        s = {
            "working": {
                "entries": self.working.count,
                "focus": self.working._focus,
            },
            "semantic": None,
            "episodic": None,
            "procedural": None,
        }

        if self._semantic:
            try:
                s["semantic"] = self._semantic.stats
            except Exception:
                pass  # error no crítico, continuar
        if self._episodic:
            try:
                s["episodic"] = self._episodic.get_stats()
            except Exception:
                pass  # error no crítico, continuar
        if self._procedural:
            try:
                s["procedural"] = self._procedural.stats
            except Exception:
                pass  # error no crítico, continuar
        return s


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_brain_memory: Optional[BrainMemory] = None
_brain_memory_lock = threading.Lock()


def get_brain_memory() -> BrainMemory:
    global _brain_memory
    if _brain_memory is None:
        with _brain_memory_lock:
            if _brain_memory is None:
                _brain_memory = BrainMemory()
    return _brain_memory
