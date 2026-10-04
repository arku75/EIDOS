#!/usr/bin/env python3
"""
EIDOS Unified Memory System — ONE memory to rule them all.
===========================================================

Unifica los 5 sistemas de memoria dispares de EIDOS bajo una sola API:

  System 1: memory.py          → Ollama nomic-embed-text + cosine similarity (SQLite)
  System 2: colony_chroma.py   → sentence-transformers + ChromaDB HTTP
  System 3: memory_decay.py    → Ebbinghaus time-decay formula (SQLite)
  System 4: episodic_memory.py → FTS5 full-text episodic search
  System 5: eidos_episodic_memory.py → SQL LIKE + life-period narrative

Backends del unificado:
  - ChromaDB HTTP (vectors)  — búsqueda semántica
  - SQLite + FTS5 (keyword)  — búsqueda textual + metadata + decay
  - Episodic DB (temporal)   — timeline, recency, life periods

Arquitectura:
  store(memory) → ChromaDB (vector) + SQLite (metadata+FTS) + episodic (timeline)
  recall(query) → Router → RRF fusion from semantic + keyword + temporal
  consolidate() → merges similar, removes duplicates
  decay()       → Ebbinghaus formula based on access frequency + importance

Uso:
  from core.eidos_memory_unified import memory
  memory.remember("nmap encontró el puerto 22 abierto", category="fact", importance=0.8)
  results = memory.recall("puertos abiertos")
  memory.consolidate()
  memory.decay()
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger("eidos.unified_memory")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

EIDOS_DIR = Path(os.environ.get("EIDOS_HOME", str(Path.home() / ".eidos"))).expanduser()
UNIFIED_DB = EIDOS_DIR / "unified_memory.db"

# ChromaDB
CHROMA_HOST = os.environ.get("EIDOS_CHROMA_HOST", "127.0.0.1")
CHROMA_PORT = int(os.environ.get("EIDOS_CHROMA_PORT", "8767"))
CHROMA_COLLECTION = "eidos_unified"

# Embeddings backend priority: sentence-transformers (local) > Ollama (remote)
EMBED_MODEL_ST = "all-MiniLM-L6-v2"      # sentence-transformers, 384-dim
EMBED_MODEL_OLLAMA = "nomic-embed-text:latest"  # Ollama, 768-dim

# Decay
DEFAULT_DECAY_RATE = 0.01       # per hour (Ebbinghaus baseline)
ARCHIVE_THRESHOLD = 0.05        # effective importance below this → archived
REINFORCE_BOOST = 0.05          # how much recall boosts importance
MAX_IMPORTANCE = 1.0

# Consolidation
CONSOLIDATION_SIMILARITY = 0.85  # cosine threshold for "same memory"
MAX_MEMORIES_BEFORE_CONSOLIDATE = 500

EIDOS_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# EMBEDDING BACKEND
# ══════════════════════════════════════════════════════════════════════════════

_embed_model = None
_embed_lock = threading.Lock()


def _get_embed_model():
    """Singleton: sentence-transformers model (lazy load, thread-safe)."""
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    _embed_model = SentenceTransformer(EMBED_MODEL_ST)
                    log.info("Embed model loaded: %s", EMBED_MODEL_ST)
                except Exception as e:
                    log.warning("Cannot load sentence-transformers: %s", e)
                    return None
    return _embed_model


def embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """Generate embeddings locally with sentence-transformers. Falls back to Ollama."""
    model = _get_embed_model()
    if model is not None:
        try:
            vecs = model.encode(
                [t[:500] for t in texts],
                batch_size=min(len(texts), 32),
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            return vecs.tolist()
        except Exception as e:
            log.debug("ST embed failed: %s", e)

    # Fallback: Ollama nomic-embed-text
    return _embed_ollama(texts)


def _embed_ollama(texts: List[str]) -> Optional[List[List[float]]]:
    """Fallback: Ollama embeddings via HTTP."""
    import urllib.request
    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    embeddings = []
    for text in texts:
        try:
            payload = json.dumps({"model": EMBED_MODEL_OLLAMA, "prompt": text[:2000]}).encode()
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
                emb = data.get("embedding")
                if emb:
                    embeddings.append(emb)
                else:
                    return None
        except Exception as e:
            log.debug("Ollama embed failed: %s", e)
            return None
    return embeddings if len(embeddings) == len(texts) else None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED SQLite BACKEND (metadata + FTS5 + decay)
# ══════════════════════════════════════════════════════════════════════════════

_SCHEMA_UNIFIED = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'general',
    importance  REAL    NOT NULL DEFAULT 0.5,
    decay_rate  REAL    NOT NULL DEFAULT 0.01,
    created_at  REAL    NOT NULL,
    last_accessed REAL   NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    tags        TEXT    DEFAULT '[]',
    metadata    TEXT    DEFAULT '{}',
    chroma_id   TEXT,
    archived    INTEGER DEFAULT 0,
    source_system TEXT  DEFAULT 'unified'
);

CREATE INDEX IF NOT EXISTS idx_um_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_um_created  ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_um_importance ON memories(importance);
CREATE INDEX IF NOT EXISTS idx_um_archived ON memories(archived);
CREATE INDEX IF NOT EXISTS idx_um_chroma ON memories(chroma_id);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, category,
    content='memories', content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS um_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags, category)
    VALUES (new.id, new.content, new.tags, new.category);
END;

CREATE TRIGGER IF NOT EXISTS um_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, category)
    VALUES('delete', old.id, old.content, old.tags, old.category);
END;

CREATE TRIGGER IF NOT EXISTS um_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, category)
    VALUES('delete', old.id, old.content, old.tags, old.category);
    INSERT INTO memories_fts(rowid, content, tags, category)
    VALUES (new.id, new.content, new.tags, new.category);
END;

-- Schema version tracking
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL
);
"""


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY ROUTER — decides which backend(s) to query
# ══════════════════════════════════════════════════════════════════════════════

class MemoryRouter:
    """Routes a query to the appropriate backend(s) based on query type.

    Detection heuristics:
      - Semantic: conceptual/descriptive queries ("how does X work?", "what is Y?")
      - Keyword:  specific terms, tool names, error messages
      - Temporal:  time references ("yesterday", "last session", "recently")

    Uses all three + RRF fusion as default for robustness.
    """

    TEMPORAL_PATTERNS = re.compile(
        r'\b(yesterday|today|last\s+(night|session|week|month)|'
        r'recent(ly)?|before|earlier|just\s+now|moments?\s+ago|'
        r'\d+\s*(hour|day|week|month)s?\s+ago)\b',
        re.IGNORECASE,
    )

    SEMANTIC_PATTERNS = re.compile(
        r'\b(how|what\s+is|explain|describe|meaning|concept|'
        r'understand|why|tell\s+me\s+about|philosophy|principle|'
        r'theory|idea|overview)\b',
        re.IGNORECASE,
    )

    KEYWORD_PATTERNS = re.compile(
        r'\b(find|search|locate|grep|lookup|where\s+is|'
        r'file|error|exception|traceback|'
        r'command|tool|binary|package)\b',
        re.IGNORECASE,
    )

    def __init__(self):
        self.weights = {"semantic": 1.0, "keyword": 1.0, "temporal": 1.0}

    def classify(self, query: str) -> Dict[str, float]:
        """Return backend weights for this query."""
        temporal = bool(self.TEMPORAL_PATTERNS.search(query))
        semantic = bool(self.SEMANTIC_PATTERNS.search(query))
        keyword = bool(self.KEYWORD_PATTERNS.search(query))

        # If nothing matches strongly, use all three (default fusion)
        if not (temporal or semantic or keyword):
            return {"semantic": 0.5, "keyword": 0.5, "temporal": 0.3}

        weights = {}
        weights["semantic"] = 1.0 if semantic else (0.3 if not keyword else 0.5)
        weights["keyword"] = 1.0 if keyword else (0.3 if not semantic else 0.5)
        weights["temporal"] = 1.0 if temporal else 0.1

        return weights


# ══════════════════════════════════════════════════════════════════════════════
# RECIPROCAL RANK FUSION
# ══════════════════════════════════════════════════════════════════════════════

def reciprocal_rank_fusion(
    result_sets: List[List[Dict[str, Any]]],
    k: int = 60,
    id_key: str = "id",
    score_key: str = "score",
) -> List[Dict[str, Any]]:
    """Combine ranked result lists using Reciprocal Rank Fusion.

    Each result gets: RRF_score = sum(1 / (k + rank_i)) across all lists.
    """
    rrf_scores: Dict[Any, float] = defaultdict(float)
    item_map: Dict[Any, Dict[str, Any]] = {}

    for results in result_sets:
        for rank, item in enumerate(results):
            item_id = item.get(id_key)
            if item_id is None:
                continue
            rrf_scores[item_id] += 1.0 / (k + rank + 1)
            if item_id not in item_map:
                item_map[item_id] = dict(item)
            # Keep max individual score
            prev = item_map[item_id].get(score_key, 0)
            item_score = item.get(score_key, 0)
            item_map[item_id][score_key] = max(prev, item_score)

    # Merge RRF score into items
    merged = []
    for item_id, rrf in rrf_scores.items():
        entry = dict(item_map[item_id])
        entry["rrf_score"] = round(rrf, 4)
        merged.append(entry)

    merged.sort(key=lambda x: -x["rrf_score"])
    return merged


# ══════════════════════════════════════════════════════════════════════════════
# EPISODIC (TIMELINE) WRAPPER — reuses episodic.db from episodic_memory.py
# ══════════════════════════════════════════════════════════════════════════════

class EpisodicBridge:
    """Thin bridge to the existing episodic_memory.py FTS5 database."""

    def __init__(self):
        from core.db import get_conn
        self._db_path = str(EIDOS_DIR / "episodic.db")
        self._lock = threading.RLock()
        # Ensure schema exists
        from core.episodic_memory import _SCHEMA as EPISODIC_SCHEMA
        with self._lock:
            conn = get_conn(self._db_path, check_same_thread=False)
            conn.executescript(EPISODIC_SCHEMA)
            conn.commit()

    def record(self, content: str, memory_type: str = "general",
               importance: float = 0.5, tags: Optional[List[str]] = None) -> int:
        """Record a memory on the episodic timeline."""
        from core.db import get_conn
        try:
            with self._lock:
                conn = get_conn(self._db_path, check_same_thread=False)
                cur = conn.execute(
                    "INSERT INTO episodes (ts, type, actor, input_text, output_text, "
                    "context_json, outcome, feedback_score, tags) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (time.time(), "memory", "unified_memory",
                     content[:8000], "",
                     json.dumps({"importance": importance, "source": "unified"}),
                     "stored", importance,
                     ",".join(tags or [])[:500]),
                )
                conn.commit()
                return cur.lastrowid
        except Exception as e:
            log.debug("Episodic record failed: %s", e)
            return -1

    def recall_temporal(self, query: str, k: int = 5,
                        hours_window: int = 24 * 30) -> List[Dict[str, Any]]:
        """Recall episodes matching query, scored by BM25 + recency + importance."""
        if not query or len(query.strip()) < 3:
            return []

        from core.db import get_conn
        cutoff = time.time() - hours_window * 3600
        terms = re.findall(r"[a-zA-Z0-9]{3,}", query.lower())
        if not terms:
            return []

        fts_query = " OR ".join(terms[:6])
        try:
            with self._lock:
                conn = get_conn(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT e.*, bm25(episodes_fts) AS rank "
                    "FROM episodes_fts "
                    "JOIN episodes e ON episodes_fts.rowid = e.id "
                    "WHERE episodes_fts MATCH ? AND e.ts >= ? "
                    "ORDER BY rank LIMIT ?",
                    (fts_query, cutoff, k * 2),
                ).fetchall()

                results = []
                for r in rows:
                    d = dict(r)
                    age_hours = (time.time() - d["ts"]) / 3600
                    recency = max(0.0, 1.0 - age_hours / max(hours_window, 1))
                    bm25 = d.get("rank", 0) or 0
                    fb = d.get("feedback_score", 0) or 0
                    d["score"] = max(0, -bm25) + recency * 0.3 + fb * 0.2
                    d["source"] = "episodic"
                    results.append(d)

                results.sort(key=lambda x: -x["score"])
                return results[:k]
        except Exception as e:
            log.debug("Episodic recall failed: %s", e)
            return []

    def stats(self) -> Dict[str, Any]:
        from core.db import get_conn
        with self._lock:
            conn = get_conn(self._db_path, check_same_thread=False)
            total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        return {"total_episodes": total, "db": self._db_path}


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED MEMORY STORE
# ══════════════════════════════════════════════════════════════════════════════

class UnifiedMemoryStore:
    """Single memory system wrapping ChromaDB (vectors), SQLite+FTS5 (keyword/metadata),
    and episodic DB (timeline) behind one API.

    Usage:
        from core.eidos_memory_unified import memory
        memory.remember("something important", category="fact", importance=0.9)
        results = memory.recall("something")
        memory.consolidate()
        memory.decay()
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._router = MemoryRouter()
        self._episodic = EpisodicBridge()

        # ChromaDB client (lazy init in background)
        self._chroma_client = None
        self._chroma_collection = None
        self._chroma_ready = False
        t = threading.Thread(target=self._init_chroma, daemon=True, name="um-chroma")
        t.start()

        # SQLite init (sync, needed immediately)
        self._init_sqlite()
        self._init_fts_triggers()

        log.info("UnifiedMemoryStore initialized: sqlite=%s chroma=pending episodic=%s",
                 UNIFIED_DB, self._episodic.stats())

    # ── SQLite init ──────────────────────────────────────────────────────────

    def _init_sqlite(self):
        from core.db import get_conn
        with self._lock:
            conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
            conn.executescript(_SCHEMA_UNIFIED)
            conn.commit()

    def _init_fts_triggers(self):
        """Ensure FTS triggers exist (safe to run multiple times)."""
        from core.db import get_conn
        triggers = """
        CREATE TRIGGER IF NOT EXISTS um_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, tags, category)
            VALUES (new.id, new.content, new.tags, new.category);
        END;
        CREATE TRIGGER IF NOT EXISTS um_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, tags, category)
            VALUES('delete', old.id, old.content, old.tags, old.category);
        END;
        CREATE TRIGGER IF NOT EXISTS um_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, tags, category)
            VALUES('delete', old.id, old.content, old.tags, old.category);
            INSERT INTO memories_fts(rowid, content, tags, category)
            VALUES (new.id, new.content, new.tags, new.category);
        END;
        """
        with self._lock:
            conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
            conn.executescript(triggers)
            conn.commit()

    # ── ChromaDB init ───────────────────────────────────────────────────────

    def _init_chroma(self):
        """Initialize ChromaDB HTTP client (background thread)."""
        if os.environ.get("EIDOS_NO_CHROMA") == "1":
            log.info("ChromaDB disabled via EIDOS_NO_CHROMA=1")
            return
        try:
            import chromadb
            self._chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
            try:
                self._chroma_collection = self._chroma_client.get_collection(CHROMA_COLLECTION)
                existing = self._chroma_collection.count()
                log.info("ChromaDB unified: collection '%s' with %d vectors",
                         CHROMA_COLLECTION, existing)
            except Exception:
                self._chroma_collection = self._chroma_client.create_collection(
                    name=CHROMA_COLLECTION,
                    metadata={"hnsw:space": "cosine"},
                )
                log.info("ChromaDB unified: collection '%s' created", CHROMA_COLLECTION)
            self._chroma_ready = True
        except Exception as e:
            log.warning("ChromaDB unified init failed (will use SQLite+FTS5 fallback): %s", e)
            self._chroma_ready = False

    @property
    def chroma_ready(self) -> bool:
        return self._chroma_ready and self._chroma_collection is not None

    # ══════════════════════════════════════════════════════════════════════════
    # CORE API
    # ══════════════════════════════════════════════════════════════════════════

    def remember(self, content: str, category: str = "general",
                 importance: float = 0.5, decay_rate: Optional[float] = None,
                 tags: Optional[List[str]] = None,
                 metadata: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Store a memory in ALL backends.

        Args:
            content:    The text to remember.
            category:   "fact" | "preference" | "skill_result" | "conversation" | "error" | "general"
            importance: 0.0-1.0 base importance.
            decay_rate: Ebbinghaus decay rate per hour (None = default 0.01).
            tags:       List of tags for filtering.
            metadata:   Arbitrary extra data.

        Returns:
            Memory ID (int), or None if storage failed completely.
        """
        importance = max(0.0, min(MAX_IMPORTANCE, importance))
        decay_rate = decay_rate if decay_rate is not None else DEFAULT_DECAY_RATE
        tags = tags or []
        metadata = metadata or {}
        now = time.time()
        from core.db import get_conn

        memory_id = None

        # 1. Store in SQLite (metadata + FTS5)
        try:
            with self._lock:
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                cur = conn.execute(
                    """INSERT INTO memories
                       (content, category, importance, decay_rate, created_at,
                        last_accessed, access_count, tags, metadata, chroma_id, archived, source_system)
                       VALUES (?,?,?,?,?,?,0,?,?,?,0,'unified')""",
                    (content, category, importance, decay_rate, now, now,
                     json.dumps(tags), json.dumps(metadata), None)
                )
                conn.commit()
                memory_id = cur.lastrowid
        except Exception as e:
            log.error("SQLite store failed: %s", e)
            return None

        # 2. Store in ChromaDB (vector)
        chroma_id = None
        if self.chroma_ready and memory_id:
            try:
                emb = embed_texts([content])
                if emb and emb[0]:
                    chroma_id = f"um_{memory_id}"
                    with self._lock:
                        self._chroma_collection.upsert(
                            ids=[chroma_id],
                            embeddings=emb,
                            documents=[content[:500]],
                            metadatas=[{
                                "category": category,
                                "importance": importance,
                                "decay_rate": decay_rate,
                                "tags": ",".join(tags),
                                "sqlite_id": memory_id,
                            }],
                        )
                    # Update SQLite row with chroma_id
                    conn2 = get_conn(str(UNIFIED_DB), check_same_thread=False)
                    conn2.execute("UPDATE memories SET chroma_id=? WHERE id=?", (chroma_id, memory_id))
                    conn2.commit()
            except Exception as e:
                log.debug("ChromaDB store failed (non-fatal): %s", e)

        # 3. Store in episodic timeline
        self._episodic.record(content, category, importance, tags)

        log.debug("remember: id=%s chroma=%s category=%s importance=%.2f",
                  memory_id, chroma_id, category, importance)
        return memory_id

    def recall(self, query: str, k: int = 5, mode: str = "auto",
               min_score: float = 0.1, category: Optional[str] = None,
               include_archived: bool = False) -> List[Dict[str, Any]]:
        """Recall memories matching the query.

        Args:
            query:     Search text.
            k:         Number of results.
            mode:      "auto" (router decides), "semantic", "keyword", "temporal", "fuse" (all+RRF).
            min_score: Minimum relevance score.
            category:  Optional category filter.
            include_archived: Include archived (decayed) memories.

        Returns:
            List of memory dicts with id, content, category, score, etc.
        """
        if mode == "auto":
            weights = self._router.classify(query)
        elif mode == "fuse":
            weights = {"semantic": 1.0, "keyword": 1.0, "temporal": 1.0}
        elif mode == "semantic":
            weights = {"semantic": 1.0, "keyword": 0.0, "temporal": 0.0}
        elif mode == "keyword":
            weights = {"semantic": 0.0, "keyword": 1.0, "temporal": 0.0}
        elif mode == "temporal":
            weights = {"semantic": 0.0, "keyword": 0.0, "temporal": 1.0}
        else:
            weights = {"semantic": 1.0, "keyword": 1.0, "temporal": 1.0}

        result_sets: List[List[Dict[str, Any]]] = []

        # Semantic search (ChromaDB)
        if weights.get("semantic", 0) > 0:
            semantic_results = self._recall_semantic(query, k=k)
            for r in semantic_results:
                r["score"] = r.get("score", 0) * weights["semantic"]
            result_sets.append(semantic_results)

        # Keyword search (SQLite FTS5)
        if weights.get("keyword", 0) > 0:
            keyword_results = self._recall_keyword(query, k=k, category=category,
                                                    include_archived=include_archived)
            for r in keyword_results:
                r["score"] = r.get("score", 0) * weights["keyword"]
            result_sets.append(keyword_results)

        # Temporal search (episodic timeline)
        if weights.get("temporal", 0) > 0:
            temporal_results = self._recall_temporal(query, k=k)
            for r in temporal_results:
                r["score"] = r.get("score", 0) * weights["temporal"]
            result_sets.append(temporal_results)

        # Fuse with RRF
        if len(result_sets) >= 2:
            merged = reciprocal_rank_fusion(result_sets)
        elif len(result_sets) == 1:
            merged = sorted(result_sets[0], key=lambda x: -x.get("score", 0))
        else:
            return []

        # Filter by min_score
        filtered = [m for m in merged if m.get("score", 0) >= min_score]

        # Touch accessed memories (reinforce)
        for m in filtered[:k]:
            mem_id = m.get("id")
            if mem_id is not None:
                self._touch(mem_id)

        return filtered[:k]

    # ── Backend recall implementations ─────────────────────────────────────

    def _recall_semantic(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Semantic search via ChromaDB (fallback: SQLite cosine)."""
        if self.chroma_ready:
            try:
                emb = embed_texts([query])
                if emb and emb[0]:
                    with self._lock:
                        results = self._chroma_collection.query(
                            query_embeddings=emb,
                            n_results=min(k * 2, max(1, self._chroma_collection.count())),
                            include=["documents", "metadatas", "distances"],
                        )
                    items = []
                    docs = results.get("documents", [[]])[0]
                    metas = results.get("metadatas", [[]])[0]
                    dists = results.get("distances", [[]])[0]
                    for doc, meta, dist in zip(docs, metas, dists):
                        score = 1.0 - float(dist)  # cosine distance → similarity
                        items.append({
                            "id": meta.get("sqlite_id"),
                            "content": doc,
                            "category": meta.get("category", "general"),
                            "score": round(score, 4),
                            "source": "chromadb",
                            "importance": meta.get("importance", 0.5),
                        })
                    return items
            except Exception as e:
                log.debug("ChromaDB recall failed: %s", e)

        # Fallback: SQLite + cosine similarity using stored embedding if available
        return self._recall_keyword(query, k=k)

    def _recall_keyword(self, query: str, k: int = 5,
                        category: Optional[str] = None,
                        include_archived: bool = False) -> List[Dict[str, Any]]:
        """Keyword search via SQLite FTS5 with BM25 scoring."""
        from core.db import get_conn
        terms = re.findall(r"[a-zA-Z0-9]{2,}", query.lower())
        if not terms:
            # Fallback to LIKE search
            return self._fallback_like(query, k, category, include_archived)

        fts_query = " OR ".join(terms[:8])
        conditions = ["memories_fts MATCH ?"]
        params: List[Any] = [fts_query]
        if not include_archived:
            conditions.append("m.archived = 0")
        if category:
            conditions.append("m.category = ?")
            params.append(category)
        params.append(k * 3)

        where_clause = "WHERE " + " AND ".join(conditions)
        sql = f"""SELECT m.id, m.content, m.category, m.importance, m.tags,
                         m.created_at, m.access_count, bm25(memories_fts) AS rank
                  FROM memories_fts
                  JOIN memories m ON memories_fts.rowid = m.id
                  {where_clause}
                  ORDER BY rank LIMIT ?"""

        try:
            with self._lock:
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()

            results = []
            for r in rows:
                d = dict(r)
                rank = d.get("rank", 0) or 0
                score = max(0, -rank)  # BM25: lower rank = better match
                results.append({
                    "id": d["id"],
                    "content": d["content"],
                    "category": d["category"],
                    "importance": d.get("importance", 0.5),
                    "score": round(score, 4),
                    "source": "fts5",
                    "tags": json.loads(d.get("tags") or "[]"),
                    "access_count": d.get("access_count", 0),
                })
            results.sort(key=lambda x: -x["score"])
            return results[:k]
        except Exception as e:
            log.debug("FTS5 recall failed: %s", e)
            return self._fallback_like(query, k, category, include_archived)

    def _fallback_like(self, query: str, k: int, category: Optional[str] = None,
                       include_archived: bool = False) -> List[Dict[str, Any]]:
        """Fallback: SQL LIKE search when FTS5 fails."""
        from core.db import get_conn
        words = query.lower().split()[:5]
        conditions = []
        params: List[Any] = []
        for w in words:
            conditions.append("LOWER(content) LIKE ?")
            params.append(f"%{w}%")
        if not include_archived:
            conditions.append("archived = 0")
        if category:
            conditions.append("category = ?")
            params.append(category)
        where = " AND ".join(conditions)

        try:
            with self._lock:
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"SELECT * FROM memories WHERE {where} ORDER BY importance DESC, last_accessed DESC LIMIT ?",
                    (*params, k),
                ).fetchall()

            results = []
            for r in rows:
                d = dict(r)
                # Apply decay
                eff = self._effective_importance(
                    d.get("importance", 0.5),
                    d.get("decay_rate", DEFAULT_DECAY_RATE),
                    d.get("last_accessed", time.time()),
                )
                results.append({
                    "id": d["id"],
                    "content": d["content"],
                    "category": d["category"],
                    "importance": d.get("importance", 0.5),
                    "effective_importance": round(eff, 4),
                    "score": round(eff, 4),
                    "source": "like",
                    "tags": json.loads(d.get("tags") or "[]"),
                    "access_count": d.get("access_count", 0),
                })
            return results
        except Exception as e:
            log.error("LIKE fallback failed: %s", e)
            return []

    def _recall_temporal(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Temporal recall via episodic timeline."""
        episodes = self._episodic.recall_temporal(query, k=k)
        results = []
        for ep in episodes:
            results.append({
                "id": ep.get("id"),
                "content": ep.get("input_text", ""),
                "category": "episodic",
                "score": ep.get("score", 0),
                "source": "episodic",
                "ts": ep.get("ts"),
                "type": ep.get("type"),
            })
        return results

    # ── Touch (reinforce on access) ────────────────────────────────────────

    def _touch(self, memory_id: int):
        """Update last_accessed and access_count."""
        from core.db import get_conn
        try:
            with self._lock:
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                conn.execute(
                    "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                    (time.time(), memory_id),
                )
                conn.commit()
        except Exception:
            pass

    def reinforce(self, memory_id: int, boost: float = None) -> bool:
        """Boost importance of a memory."""
        boost = boost if boost is not None else REINFORCE_BOOST
        from core.db import get_conn
        try:
            with self._lock:
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                row = conn.execute(
                    "SELECT importance FROM memories WHERE id=?", (memory_id,)
                ).fetchone()
                if not row:
                    return False
                new_imp = min(MAX_IMPORTANCE, row[0] + boost)
                conn.execute(
                    "UPDATE memories SET importance=?, last_accessed=?, archived=0 WHERE id=?",
                    (new_imp, time.time(), memory_id),
                )
                conn.commit()
            return True
        except Exception as e:
            log.debug("Reinforce failed: %s", e)
            return False

    def forget(self, memory_id: int) -> bool:
        """Delete a memory permanently from all backends."""
        from core.db import get_conn
        try:
            with self._lock:
                # Get chroma_id before deleting
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                row = conn.execute("SELECT chroma_id FROM memories WHERE id=?", (memory_id,)).fetchone()
                chroma_id = row[0] if row else None

                # Delete from SQLite
                conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
                conn.commit()

                # Delete from ChromaDB
                if chroma_id and self.chroma_ready:
                    try:
                        self._chroma_collection.delete(ids=[chroma_id])
                    except Exception:
                        pass
            return True
        except Exception as e:
            log.error("Forget failed: %s", e)
            return False

    # ══════════════════════════════════════════════════════════════════════════
    # DECAY (Ebbinghaus Forgetting Curve)
    # ══════════════════════════════════════════════════════════════════════════

    def _effective_importance(self, importance: float, decay_rate: float,
                              last_accessed: float) -> float:
        """Ebbinghaus formula: importance * exp(-decay_rate * hours_since_access)."""
        hours_since = (time.time() - last_accessed) / 3600.0
        return importance * math.exp(-decay_rate * hours_since)

    def decay(self, archive_threshold: float = None) -> int:
        """Apply Ebbinghaus decay: archive memories below threshold. Return count."""
        threshold = archive_threshold if archive_threshold is not None else ARCHIVE_THRESHOLD
        from core.db import get_conn
        archived_count = 0
        try:
            with self._lock:
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                rows = conn.execute(
                    "SELECT id, importance, decay_rate, last_accessed FROM memories WHERE archived=0"
                ).fetchall()

                to_archive = []
                for row in rows:
                    eff = self._effective_importance(row[1], row[2], row[3])
                    if eff < threshold:
                        to_archive.append(row[0])

                if to_archive:
                    placeholders = ",".join("?" * len(to_archive))
                    conn.execute(
                        f"UPDATE memories SET archived=1 WHERE id IN ({placeholders})",
                        to_archive,
                    )
                    conn.commit()
                    archived_count = len(to_archive)

            if archived_count:
                log.info("decay: %d memories archived (threshold=%.3f)", archived_count, threshold)
            return archived_count
        except Exception as e:
            log.error("Decay failed: %s", e)
            return 0

    # ══════════════════════════════════════════════════════════════════════════
    # CONSOLIDATION — merge similar, remove duplicates
    # ══════════════════════════════════════════════════════════════════════════

    def consolidate(self, similarity_threshold: float = None) -> int:
        """Find and merge similar memories, remove near-duplicates.

        Strategy:
          1. Get all active memories from SQLite.
          2. For each pair, if same category and similar content, keep the stronger one.
          3. Use ChromaDB for semantic similarity if available, else text overlap.

        Returns:
            Number of memories consolidated (merged/removed).
        """
        threshold = similarity_threshold if similarity_threshold is not None else CONSOLIDATION_SIMILARITY
        from core.db import get_conn

        try:
            with self._lock:
                conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, content, category, importance, access_count, chroma_id FROM memories WHERE archived=0"
                ).fetchall()

            if len(rows) < 2:
                return 0

            consolidated = 0
            to_merge: List[Tuple[int, int]] = []  # (keeper, victim)

            # Phase 1: Identify similar pairs
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    a, b = rows[i], rows[j]
                    if a["category"] != b["category"]:
                        continue

                    # Compute text similarity
                    sim = self._text_similarity(a["content"], b["content"])
                    if sim >= threshold:
                        # Keep the one with higher importance + access_count
                        score_a = a["importance"] * 0.7 + min(a["access_count"] / 10, 1.0) * 0.3
                        score_b = b["importance"] * 0.7 + min(b["access_count"] / 10, 1.0) * 0.3
                        keeper, victim = (a["id"], b["id"]) if score_a >= score_b else (b["id"], a["id"])
                        to_merge.append((keeper, victim))

            # Phase 2: Execute merges
            if to_merge:
                conn2 = get_conn(str(UNIFIED_DB), check_same_thread=False)
                for keeper, victim in to_merge:
                    # Transfer access_count to keeper
                    conn2.execute(
                        "UPDATE memories SET access_count = access_count + "
                        "(SELECT access_count FROM memories WHERE id=?) WHERE id=?",
                        (victim, keeper),
                    )
                    # Boost keeper importance slightly
                    conn2.execute(
                        "UPDATE memories SET importance = MIN(1.0, importance + 0.02) WHERE id=?",
                        (keeper,),
                    )
                    # Archive victim (soft delete)
                    conn2.execute(
                        "UPDATE memories SET archived=1 WHERE id=?", (victim,)
                    )
                    consolidated += 1
                conn2.commit()

            if consolidated:
                log.info("consolidate: merged %d duplicate/similar memories", consolidated)
            return consolidated
        except Exception as e:
            log.error("Consolidate failed: %s", e)
            return 0

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """Fast text similarity: Jaccard on character trigrams + word overlap."""
        # Character trigram Jaccard
        def trigrams(s):
            s = s.lower()[:200]
            return {s[i:i + 3] for i in range(len(s) - 2)}

        ta, tb = trigrams(a), trigrams(b)
        if not ta or not tb:
            return 0.0
        tri_sim = len(ta & tb) / len(ta | tb)

        # Word overlap
        wa = set(re.findall(r'\w+', a.lower()))
        wb = set(re.findall(r'\w+', b.lower()))
        if not wa or not wb:
            word_sim = 0.0
        else:
            word_sim = len(wa & wb) / len(wa | wb)

        return 0.6 * tri_sim + 0.4 * word_sim

    # ══════════════════════════════════════════════════════════════════════════
    # STATS & HEALTH
    # ══════════════════════════════════════════════════════════════════════════

    def stats(self) -> Dict[str, Any]:
        """Comprehensive memory statistics."""
        from core.db import get_conn

        with self._lock:
            conn = get_conn(str(UNIFIED_DB), check_same_thread=False)

            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM memories WHERE archived=0").fetchone()[0]
            archived = total - active

            by_cat_rows = conn.execute(
                "SELECT category, COUNT(*) n FROM memories GROUP BY category ORDER BY n DESC"
            ).fetchall()
            by_cat = {r[0]: r[1] for r in by_cat_rows} if by_cat_rows else {}

            top = conn.execute(
                "SELECT content, importance, access_count FROM memories "
                "WHERE archived=0 ORDER BY importance DESC, access_count DESC LIMIT 5"
            ).fetchall()

            # Decay health
            rows = conn.execute(
                "SELECT importance, decay_rate, last_accessed FROM memories WHERE archived=0"
            ).fetchall()
            effs = [self._effective_importance(r[0], r[1], r[2]) for r in rows] if rows else []
            avg_eff = sum(effs) / len(effs) if effs else 0.0
            at_risk = sum(1 for e in effs if e < ARCHIVE_THRESHOLD * 2)

            # ChromaDB stats
            chroma_count = self._chroma_collection.count() if self.chroma_ready else 0

            # Episodic stats
            episodic = self._episodic.stats()

        return {
            "unified": {
                "total": total,
                "active": active,
                "archived": archived,
                "by_category": by_cat,
                "top_memories": [(r[0][:60], r[1], r[2]) for r in top],
                "avg_effective_importance": round(avg_eff, 4),
                "at_risk": at_risk,
            },
            "chromadb": {
                "ready": self.chroma_ready,
                "count": chroma_count,
                "host": f"{CHROMA_HOST}:{CHROMA_PORT}",
                "collection": CHROMA_COLLECTION,
            },
            "episodic": episodic,
            "db_path": str(UNIFIED_DB),
        }

    def memory_health(self) -> Dict[str, Any]:
        """Health score 0-100 for the memory system."""
        from core.db import get_conn

        with self._lock:
            conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
            active = conn.execute("SELECT COUNT(*) FROM memories WHERE archived=0").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            rows = conn.execute(
                "SELECT importance, decay_rate, last_accessed FROM memories WHERE archived=0"
            ).fetchall()

        if not rows:
            return {"health_score": 0, "active": 0, "total": total, "status": "empty"}

        effs = [self._effective_importance(r[0], r[1], r[2]) for r in rows]
        avg_eff = sum(effs) / len(effs)
        at_risk = sum(1 for e in effs if e < ARCHIVE_THRESHOLD * 2)

        health_score = min(100, int(
            (avg_eff * 40) +
            (min(active / max(total, 1), 1.0) * 30) +
            (max(0, 1.0 - at_risk / max(active, 1)) * 30)
        ))

        return {
            "health_score": health_score,
            "active": active,
            "archived": total - active,
            "total": total,
            "avg_effective_importance": round(avg_eff, 4),
            "at_risk": at_risk,
            "status": "healthy" if health_score >= 60 else ("degraded" if health_score >= 30 else "critical"),
        }

    def list_memories(self, category: Optional[str] = None, limit: int = 20,
                      include_archived: bool = False) -> List[Dict[str, Any]]:
        """List recent memories, optionally filtered by category."""
        from core.db import get_conn
        with self._lock:
            conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conditions = []
            params: List[Any] = []
            if not include_archived:
                conditions.append("archived = 0")
            if category:
                conditions.append("category = ?")
                params.append(category)
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            rows = conn.execute(
                f"SELECT * FROM memories {where} ORDER BY last_accessed DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["effective_importance"] = round(
                self._effective_importance(
                    d.get("importance", 0.5),
                    d.get("decay_rate", DEFAULT_DECAY_RATE),
                    d.get("last_accessed", time.time()),
                ), 4)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["metadata"] = json.loads(d.get("metadata") or "{}")
            results.append(d)
        return results


# ══════════════════════════════════════════════════════════════════════════════
# MIGRATION — pull data from all 5 legacy systems into the unified store
# ══════════════════════════════════════════════════════════════════════════════

def migrate_all(unified: Optional[UnifiedMemoryStore] = None,
                dry_run: bool = False) -> Dict[str, Any]:
    """Migrate data from all 5 legacy memory systems into the unified store.

    Returns dict with migration stats per source.
    """
    store = unified or memory  # use singleton if no instance passed
    stats: Dict[str, Dict[str, int]] = {}

    if dry_run:
        log.info("MIGRATION DRY RUN — no data will be written")

    # ── System 1: memory.py (Ollama embeddings in SQLite) ──────────────────
    stats["memory_py"] = _migrate_memory_py(store, dry_run)

    # ── System 2: colony_chroma.py (existing ChromaDB collection) ──────────
    stats["colony_chroma"] = _migrate_colony_chroma(store, dry_run)

    # ── System 3: memory_decay.py (Ebbinghaus in SQLite) ───────────────────
    stats["memory_decay"] = _migrate_memory_decay(store, dry_run)

    # ── System 4: episodic_memory.py (FTS5 episodes) ──────────────────────
    stats["episodic_memory"] = _migrate_episodic_memory(store, dry_run)

    # ── System 5: eidos_episodic_memory.py (SQL LIKE episodes) ────────────
    stats["eidos_episodic_memory"] = _migrate_eidos_episodic_memory(store, dry_run)

    total = sum(s.get("imported", 0) for s in stats.values())
    skipped = sum(s.get("skipped", 0) for s in stats.values())
    errors = sum(s.get("errors", 0) for s in stats.values())

    log.info("Migration complete: %d imported, %d skipped, %d errors (dry_run=%s)",
             total, skipped, errors, dry_run)

    return {
        "dry_run": dry_run,
        "total_imported": total,
        "total_skipped": skipped,
        "total_errors": errors,
        "by_source": stats,
    }


def _migrate_memory_py(store: UnifiedMemoryStore, dry_run: bool) -> Dict[str, int]:
    """Migrate from memory.py: ~/.eidos/memory.db (Ollama embeddings)."""
    from core.db import get_conn
    db_path = str(EIDOS_DIR / "memory.db")
    if not os.path.exists(db_path):
        return {"imported": 0, "skipped": 0, "errors": 0, "note": "no db"}

    imported = skipped = errors = 0
    try:
        conn = get_conn(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT text, category, created_at, access_count FROM memories").fetchall()

        for row in rows:
            try:
                if not dry_run:
                    mem_id = store.remember(
                        content=row["text"],
                        category=row["category"],
                        importance=max(0.3, min(1.0, 0.5 + row["access_count"] * 0.05)),
                        tags=[row["category"]],
                    )
                    if mem_id:
                        imported += 1
                    else:
                        errors += 1
                else:
                    imported += 1
            except Exception:
                errors += 1

        log.info("Migrated memory.py: %d imported, %d skipped, %d errors",
                 imported, skipped, errors)
    except Exception as e:
        log.error("memory.py migration failed: %s", e)
        return {"imported": 0, "skipped": 0, "errors": 1, "note": str(e)}

    return {"imported": imported, "skipped": skipped, "errors": errors}


def _migrate_colony_chroma(store: UnifiedMemoryStore, dry_run: bool) -> Dict[str, int]:
    """Migrate from colony_chroma.py: ChromaDB collection 'eidos_knowledge'."""
    if os.environ.get("EIDOS_NO_CHROMA") == "1":
        return {"imported": 0, "skipped": 0, "errors": 0, "note": "chroma disabled"}
    if not store.chroma_ready:
        return {"imported": 0, "skipped": 0, "errors": 0, "note": "chroma not ready"}

    imported = skipped = errors = 0
    try:
        import chromadb
        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        try:
            old_col = client.get_collection("eidos_knowledge")
        except Exception:
            return {"imported": 0, "skipped": 0, "errors": 0, "note": "no old collection"}

        count = old_col.count()
        if count == 0:
            return {"imported": 0, "skipped": 0, "errors": 0, "note": "empty collection"}

        # Fetch in batches
        batch_size = 100
        for offset in range(0, count, batch_size):
            limit = min(batch_size, count - offset)
            try:
                results = old_col.get(
                    limit=limit,
                    offset=offset,
                    include=["documents", "metadatas"],
                )
                ids = results.get("ids", [])
                docs = results.get("documents", [])
                metas = results.get("metadatas", [])

                for cid, doc, meta in zip(ids, docs, metas):
                    if not doc:
                        skipped += 1
                        continue
                    try:
                        if not dry_run:
                            mem_id = store.remember(
                                content=str(doc)[:500],
                                category="knowledge",
                                importance=float(meta.get("confidence", 0.6)) if meta else 0.5,
                                tags=[meta.get("concept", "")] if meta else [],
                                metadata={"source": meta.get("source", ""), "old_chroma_id": cid} if meta else {},
                            )
                            if mem_id:
                                imported += 1
                            else:
                                errors += 1
                        else:
                            imported += 1
                    except Exception:
                        errors += 1
            except Exception as e:
                log.warning("ChromaDB batch %d failed: %s", offset, e)
                errors += 1
                break

    except Exception as e:
        log.error("colony_chroma migration failed: %s", e)
        return {"imported": imported, "skipped": skipped, "errors": errors + 1, "note": str(e)}

    return {"imported": imported, "skipped": skipped, "errors": errors}


def _migrate_memory_decay(store: UnifiedMemoryStore, dry_run: bool) -> Dict[str, int]:
    """Migrate from memory_decay.py: ~/.eidos/decaying_memory.db."""
    from core.db import get_conn
    db_path = str(EIDOS_DIR / "decaying_memory.db")
    if not os.path.exists(db_path):
        return {"imported": 0, "skipped": 0, "errors": 0, "note": "no db"}

    imported = skipped = errors = 0
    try:
        conn = get_conn(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT content, importance, decay_rate, access_count, tags, metadata, archived FROM memories"
        ).fetchall()

        for row in rows:
            try:
                tags = json.loads(row["tags"]) if row["tags"] else []
                meta = json.loads(row["metadata"]) if row["metadata"] else {}

                if row["archived"] and not dry_run:
                    # Archived memories: store with low importance
                    mem_id = store.remember(
                        content=row["content"],
                        category="decay_archived",
                        importance=0.05,
                        decay_rate=row["decay_rate"],
                        tags=tags,
                        metadata=meta,
                    )
                elif not dry_run:
                    mem_id = store.remember(
                        content=row["content"],
                        category="general",
                        importance=row["importance"],
                        decay_rate=row["decay_rate"],
                        tags=tags,
                        metadata=meta,
                    )
                else:
                    imported += 1
                    continue

                if mem_id:
                    imported += 1
                else:
                    errors += 1
            except Exception:
                errors += 1

        log.info("Migrated memory_decay.py: %d imported, %d skipped, %d errors",
                 imported, skipped, errors)
    except Exception as e:
        log.error("memory_decay migration failed: %s", e)
        return {"imported": 0, "skipped": 0, "errors": 1, "note": str(e)}

    return {"imported": imported, "skipped": skipped, "errors": errors}


def _migrate_episodic_memory(store: UnifiedMemoryStore, dry_run: bool) -> Dict[str, int]:
    """Migrate from episodic_memory.py: ~/.eidos/episodic.db (FTS5 episodes)."""
    from core.db import get_conn
    db_path = str(EIDOS_DIR / "episodic.db")
    if not os.path.exists(db_path):
        return {"imported": 0, "skipped": 0, "errors": 0, "note": "no db"}

    imported = skipped = errors = 0
    try:
        conn = get_conn(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Only import meaningful episodes (not trivial ones)
        rows = conn.execute(
            "SELECT input_text, output_text, type, outcome, feedback_score, tags FROM episodes "
            "WHERE input_text != '' AND LENGTH(input_text) > 10 "
            "ORDER BY ts DESC LIMIT 1000"
        ).fetchall()

        for row in rows:
            try:
                content = row["input_text"]
                if row["output_text"]:
                    content = f"{content} -> {row['output_text'][:200]}"

                fb = row["feedback_score"]
                imp = 0.3 + float(fb if fb else 0) * 0.7
                tags_list = (row["tags"] or "").split(",") if row["tags"] else []
                tags_list.append(row["type"])

                if not dry_run:
                    outcome = row["outcome"] or ""
                    mem_id = store.remember(
                        content=content[:1000],
                        category="episodic",
                        importance=min(1.0, imp),
                        tags=tags_list,
                        metadata={"episode_type": row["type"], "outcome": outcome},
                    )
                    if mem_id:
                        imported += 1
                    else:
                        errors += 1
                else:
                    imported += 1
            except Exception:
                errors += 1

        log.info("Migrated episodic_memory.py: %d imported, %d skipped, %d errors",
                 imported, skipped, errors)
    except Exception as e:
        log.error("episodic_memory migration failed: %s", e)
        return {"imported": 0, "skipped": 0, "errors": 1, "note": str(e)}

    return {"imported": imported, "skipped": skipped, "errors": errors}


def _migrate_eidos_episodic_memory(store: UnifiedMemoryStore, dry_run: bool) -> Dict[str, int]:
    """Migrate from eidos_episodic_memory.py: ~/.eidos/episodic_memory.db (SQL LIKE)."""
    from core.db import get_conn
    db_path = str(EIDOS_DIR / "episodic_memory.db")
    if not os.path.exists(db_path):
        return {"imported": 0, "skipped": 0, "errors": 0, "note": "no db"}

    imported = skipped = errors = 0
    try:
        conn = get_conn(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT content, episode_type, importance, emotional_tone, tags, access_count, data FROM episodes "
            "WHERE content != '' ORDER BY timestamp DESC LIMIT 1000"
        ).fetchall()

        for row in rows:
            try:
                tags_list = json.loads(row["tags"]) if row["tags"] else []
                tags_list.append(row["episode_type"])
                data = json.loads(row["data"]) if row["data"] else {}

                if not dry_run:
                    emo_tone = row["emotional_tone"] or ""
                    acc_cnt = row["access_count"] or 0
                    mem_id = store.remember(
                        content=row["content"][:1000],
                        category=row["episode_type"],
                        importance=min(1.0, max(0.1, row["importance"] / 10.0)),
                        tags=tags_list,
                        metadata={
                            "emotional_tone": emo_tone,
                            "access_count": acc_cnt,
                            **data,
                        },
                    )
                    if mem_id:
                        imported += 1
                    else:
                        errors += 1
                else:
                    imported += 1
            except Exception:
                errors += 1

        log.info("Migrated eidos_episodic_memory.py: %d imported, %d skipped, %d errors",
                 imported, skipped, errors)
    except Exception as e:
        log.error("eidos_episodic_memory migration failed: %s", e)
        return {"imported": 0, "skipped": 0, "errors": 1, "note": str(e)}

    return {"imported": imported, "skipped": skipped, "errors": errors}


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_instance: Optional[UnifiedMemoryStore] = None
_instance_lock = threading.Lock()


def get_unified_memory() -> UnifiedMemoryStore:
    """Get the singleton UnifiedMemoryStore instance."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = UnifiedMemoryStore()
    return _instance


# ── Module-level singleton (lazy via property-like descriptor) ───────────

class _MemoryProxy:
    """Lazy-loading proxy: defers UnifiedMemoryStore init until first access."""
    def __init__(self):
        self._instance: Optional[UnifiedMemoryStore] = None

    def __getattr__(self, name: str):
        if self._instance is None:
            self._instance = get_unified_memory()
        return getattr(self._instance, name)

    def __repr__(self):
        if self._instance is None:
            return "<UnifiedMemoryStore (pending init)>"
        return repr(self._instance)


memory: _MemoryProxy = _MemoryProxy()  # type: ignore[assignment]


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    print("=" * 70)
    print("  EIDOS UNIFIED MEMORY — Self-Test")
    print("=" * 70)

    # Force init
    um = get_unified_memory()
    print(f"\n[INIT] ChromaDB ready: {um.chroma_ready}")
    print(f"[INIT] DB path: {UNIFIED_DB}")

    # Test 1: remember
    print("\n── Test 1: remember ──")
    id1 = um.remember("nmap encontró puertos 22, 80, 443 abiertos",
                       category="fact", importance=0.9, tags=["scan", "nmap"])
    id2 = um.remember("SER prefiere usar nmap con -sV para escaneos de versión",
                       category="preference", importance=0.8, tags=["preference", "nmap"])
    id3 = um.remember("La clave SSH del servidor es RSA 1024 (débil)",
                       category="fact", importance=0.7, tags=["ssh", "vuln"])
    id4 = um.remember("Log temporal de debug — olvidable",
                       category="general", importance=0.1, decay_rate=0.1, tags=["debug"])
    print(f"  Stored IDs: {id1}, {id2}, {id3}, {id4}")

    # Test 2: recall (various modes)
    print("\n── Test 2: recall ──")
    for mode in ["auto", "semantic", "keyword", "fuse"]:
        results = um.recall("puertos abiertos", k=3, mode=mode)
        print(f"  mode={mode}: {len(results)} results")
        if results:
            print(f"    top: [{results[0]['source']}] {results[0]['content'][:60]}... (score={results[0].get('score', 0):.3f})")

    # Test 3: category filter
    print("\n── Test 3: category filter ──")
    prefs = um.recall("escanear", k=3, category="preference")
    print(f"  preference results: {len(prefs)}")
    for p in prefs:
        print(f"    [{p['category']}] {p['content'][:60]}...")

    # Test 4: reinforce
    print("\n── Test 4: reinforce ──")
    if id2:
        before = um.list_memories(limit=100)
        b_imp = next((m["importance"] for m in before if m["id"] == id2), None)
        um.reinforce(id2)
        after = um.list_memories(limit=100)
        a_imp = next((m["importance"] for m in after if m["id"] == id2), None)
        print(f"  id={id2}: importance {b_imp} -> {a_imp}")

    # Test 5: decay
    print("\n── Test 5: decay ──")
    # Artificially age the debug memory
    from core.db import get_conn
    conn = get_conn(str(UNIFIED_DB), check_same_thread=False)
    conn.execute("UPDATE memories SET last_accessed=? WHERE id=?",
                 (time.time() - 3600 * 200, id4))
    conn.commit()
    archived = um.decay()
    print(f"  Decay archived: {archived}")

    # Test 6: consolidate
    print("\n── Test 6: consolidate ──")
    # Create a near-duplicate
    id5 = um.remember("nmap encontró puertos 22, 80 y 443 abiertos",
                       category="fact", importance=0.6, tags=["scan"])
    print(f"  Near-duplicate ID: {id5}")
    merged = um.consolidate()
    print(f"  Consolidated: {merged}")

    # Test 7: forget
    print("\n── Test 7: forget ──")
    if id3:
        um.forget(id3)
        print(f"  Forgot id={id3}")

    # Test 8: stats
    print("\n── Test 8: stats ──")
    s = um.stats()
    print(f"  Unified: active={s['unified']['active']} archived={s['unified']['archived']} total={s['unified']['total']}")
    print(f"  ChromaDB: ready={s['chromadb']['ready']} count={s['chromadb']['count']}")
    print(f"  Episodic: {s['episodic']}")
    print(f"  Categories: {s['unified']['by_category']}")

    # Test 9: memory_health
    print("\n── Test 9: health ──")
    h = um.memory_health()
    print(f"  Health: {h['health_score']}/100 ({h['status']})")
    print(f"  Avg importance: {h['avg_effective_importance']}")
    print(f"  At risk: {h['at_risk']}")

    # Test 10: list
    print("\n── Test 10: list ──")
    mems = um.list_memories(limit=5)
    for m in mems:
        print(f"  [{m['category']}] {m['content'][:50]}... (eff={m.get('effective_importance', 0):.3f})")

    # Test 11: migration dry-run
    print("\n── Test 11: migration dry-run ──")
    mig = migrate_all(um, dry_run=True)
    print(f"  Dry-run: {mig['total_imported']} would be imported from {len(mig['by_source'])} sources")
    for src, s in mig["by_source"].items():
        print(f"    {src}: {s}")

    print("\n" + "=" * 70)
    print("  Unified Memory self-test complete.")
    print(f"  DB: {UNIFIED_DB} ({os.path.getsize(str(UNIFIED_DB)) if os.path.exists(str(UNIFIED_DB)) else 0} bytes)")
    print("=" * 70)
