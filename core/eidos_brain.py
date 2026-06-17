"""
eidos_brain.py — Knowledge Graph Bridge for EIDOS Alive Orchestrator

Bridges the SemanticGraph (15,965 quality nodes, 827,238 edges) to the
orchestrator's reasoning loop. Replaces hardcoded if/elif rules with neural
spreading activation from perceptual input.

Architecture:
  PERCEPTION  →  perceive_as_graph_query()  →  GraphQuery
  GraphQuery  →  activate_brain()            →  ActivatedContext
  ActivatedContext + action descriptors      →  reason_from_activation()  →  Decision
  Decision + Outcome                         →  learn_from_outcome()      →  edges_adjusted

The key insight: instead of "if 'firefox' in active_window: browse_web",
the brain fires spreading activation from "firefox" → "browser" → "web" →
"internet" through the graph's edges, and the action whose semantic
descriptor best matches the activated neurons wins.

Learning: successful decisions strengthen edges between perceptual concepts
and action-semantic concepts (Hebbian). Failed decisions weaken them.
Over time, the brain learns which contexts call for which actions — without
anyone writing a single if/elif rule.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("eidos.brain")

# ── Action Semantic Descriptors ───────────────────────────────────────────────
# Each action's descriptor is a rich-text semantic field. When tokenized and
# matched against graph-activated neurons, it produces a relevance score.
# These are BOOTSTRAP descriptors — learning adjusts the effective weights.
# The graph handles all the heavy lifting of finding related concepts.

ACTION_DESCRIPTORS: Dict[str, str] = {
    "browse_web": (
        "web browser internet navigation http https www url webpage "
        "firefox chrome chromium brave edge safari opera browsing "
        "search engine online site link download upload html css "
        "javascript website domain dns server client"
    ),
    "explore_filesystem": (
        "file directory folder path filesystem disk storage "
        "terminal shell console command line bash zsh konsole "
        "file manager dolphin nautilus thunar explorer finder "
        "document text edit code project repository root home "
        "linux unix filesystem hierarchy mount partition"
    ),
    "offer_help": (
        "error failure crash exception problem bug issue warning "
        "alert critical emergency help assistance support fix "
        "repair troubleshoot debug diagnose failed broken missing"
    ),
    "wait_user_decision": (
        "dialog popup modal window confirm prompt question choice "
        "option select button checkbox radio form input message "
        "notification alert prompt user interaction"
    ),
    "introspect": (
        "reflection thinking analysis self knowledge learning memory "
        "statistics graph database introspection metacognition "
        "evolution growth improvement review assessment"
    ),
    "observe": (
        "desktop screen display monitor watch view idle empty "
        "wallpaper background nothing passive waiting stationary "
        "unchanged static calm quiet still ambient"
    ),
}

# ── Stop words ────────────────────────────────────────────────────────────────
_STOP_WORDS: Set[str] = {
    # English
    "the", "this", "that", "what", "which", "when", "where", "why", "how",
    "can", "will", "would", "shall", "should", "may", "might", "must",
    "has", "have", "had", "was", "were", "been", "is", "are", "am",
    "it", "its", "do", "does", "did", "to", "of", "in", "on", "at", "by",
    "for", "from", "with", "without", "about", "into", "through", "during",
    "before", "after", "above", "below", "between", "if", "and", "or", "not",
    "but", "than", "then", "also", "very", "just", "only", "some", "any",
    "all", "each", "every", "both", "few", "more", "most", "other", "such",
    "over", "under", "again", "further", "once", "here", "there", "now",
    # Spanish
    "que", "del", "las", "los", "con", "por", "para", "como", "qué", "cómo",
    "puede", "todo", "esta", "este", "más", "eso", "esa", "entre", "tiene",
    "ella", "ello", "ellos", "pero", "sino", "aunque", "porque", "cuando",
    "donde", "quien", "cual", "es", "el", "la", "un", "una", "se", "no",
    "al", "le", "su", "de", "en", "ha", "lo", "si", "ya", "son", "ser",
    "hay", "era", "fue", "han", "tan", "vez", "cada", "muy", "solo",
    "también", "entonces", "mientras", "hasta", "desde", "sobre", "bajo",
}

# ── Error-indicator patterns for visible text ─────────────────────────────────
_ERROR_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\b(error|exception|fail|crash|traceback|segfault|OOM|killed)\b',
        r'\b(problem|bug|issue|warning|alert|critical|fatal)\b',
        r'\b(cannot|could not|unable|denied|refused|timeout)\b',
        r'\b(not found|no such file|permission denied|access denied)\b',
    ]
]


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class GraphQuery:
    """A perceptual context converted into a graph-ready query.

    Extracted from AliveOrchestrator.perceive() output:
        active_window, windows_open, visible_text, elements, has_error, layout
    """
    seed_concepts: List[str] = field(default_factory=list)
    context_keywords: List[str] = field(default_factory=list)
    active_window_concept: str = ""
    active_window_tokens: List[str] = field(default_factory=list)
    visible_text: str = ""
    has_error: bool = False
    has_dialogs: bool = False
    error_matches: List[str] = field(default_factory=list)


@dataclass
class ActivatedContext:
    """Result of neural spreading activation from perceptual seeds.

    fired_neurons:  List of (concept_name, activation_level, category)
    activation_map: concept_lower → activation_level (for fast lookup)
    active_window_concept: The cleaned concept from the active window title
    """
    fired_neurons: List[Tuple[str, float, str]] = field(default_factory=list)
    activation_map: Dict[str, float] = field(default_factory=dict)
    reasoning_trace: List[str] = field(default_factory=list)
    total_neurons_fired: int = 0
    dominant_categories: List[Tuple[str, float]] = field(default_factory=list)
    graph_ready: bool = False
    elapsed_ms: float = 0.0
    active_window_concept: str = ""


@dataclass
class ActionScore:
    """Scoring breakdown for one candidate action."""
    action: str
    score: float
    direct_hits: int = 0
    category_hits: int = 0
    spread_hits: int = 0
    supporting_concepts: List[str] = field(default_factory=list)
    supporting_categories: List[str] = field(default_factory=list)


@dataclass
class Decision:
    """Final decision with full reasoning trace."""
    action: str
    reason: str
    confidence: float
    primary_action: Optional[ActionScore] = None
    alternatives: List[ActionScore] = field(default_factory=list)
    activated_context: Optional[ActivatedContext] = None


# ── EidosBrain ────────────────────────────────────────────────────────────────

class EidosBrain:
    """Bridge between the knowledge graph and the orchestrator's reasoning loop.

    Four-phase pipeline:
      1. perceive_as_graph_query() — perception → structured query
      2. activate_brain()           — query → spreading activation
      3. reason_from_activation()   — activation → scored decision
      4. learn_from_outcome()       — outcome → Hebbian weight adjustment

    Usage:
        brain = get_brain()
        query = brain.perceive_as_graph_query(ctx)
        activated = brain.activate_brain(query)
        decision = brain.reason_from_activation(activated)
        # ... execute decision.action ...
        brain.learn_from_outcome(decision, "success")
    """

    def __init__(self):
        self._reasoner = None
        self._last_fired: float = 0
        self._fired_count: int = 0
        # Learned concept→action associations — NOW PERSISTED to evolution_brain.db
        self._concept_action_weights: Dict[str, Dict[str, float]] = {}
        # Cache action descriptor tokens for fast matching
        self._descriptor_tokens: Dict[str, Set[str]] = {}
        self._init_descriptors()
        self._init_brain_weights_db()
        self._load_brain_weights()

    def _init_descriptors(self):
        """Pre-tokenize all action descriptors for fast matching."""
        for action, desc in ACTION_DESCRIPTORS.items():
            self._descriptor_tokens[action] = set(self._tokenize(desc))
            # Also add the action name itself and its components
            for part in action.split("_"):
                if len(part) >= 3:
                    self._descriptor_tokens[action].add(part)

    # ── Brain Weights Persistence ─────────────────────────────────────────

    def _init_brain_weights_db(self):
        """Create the brain_weights table in evolution_brain.db if it doesn't exist."""
        try:
            from core.db import get_conn
            from pathlib import Path as _Path
            db_path = _Path.home() / ".eidos" / "evolution_brain.db"
            conn = get_conn(db_path, timeout=10)
            conn.execute("""CREATE TABLE IF NOT EXISTS brain_weights (
                concept TEXT NOT NULL,
                action TEXT NOT NULL,
                weight REAL DEFAULT 0.0,
                last_updated TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (concept, action)
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_brain_weights_action
                ON brain_weights(action)""")
            conn.commit()
        except Exception as e:
            log.debug("_init_brain_weights_db: %s", e)

    def _load_brain_weights(self):
        """Load persisted concept→action weights from evolution_brain.db."""
        try:
            from core.db import get_conn
            from pathlib import Path as _Path
            db_path = _Path.home() / ".eidos" / "evolution_brain.db"
            conn = get_conn(db_path, timeout=10)
            rows = conn.execute(
                "SELECT concept, action, weight FROM brain_weights"
            ).fetchall()
            loaded = 0
            for row in rows:
                concept, action, weight = row[0], row[1], row[2]
                if action not in self._concept_action_weights:
                    self._concept_action_weights[action] = {}
                self._concept_action_weights[action][concept] = weight
                loaded += 1
            if loaded > 0:
                log.info("Brain weights loaded: %d associations across %d actions",
                         loaded, len(self._concept_action_weights))
        except Exception as e:
            log.debug("_load_brain_weights: %s", e)

    def _save_brain_weights(self):
        """Persist all concept→action weights to evolution_brain.db."""
        try:
            from core.db import get_conn
            from pathlib import Path as _Path
            db_path = _Path.home() / ".eidos" / "evolution_brain.db"
            conn = get_conn(db_path, timeout=10)
            count = 0
            for action, weights in self._concept_action_weights.items():
                for concept, weight in weights.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO brain_weights "
                        "(concept, action, weight, last_updated) "
                        "VALUES (?, ?, ?, datetime('now'))",
                        (concept, action, weight)
                    )
                    count += 1
            conn.commit()
            if count > 0:
                log.debug("Brain weights saved: %d associations", count)
        except Exception as e:
            log.debug("_save_brain_weights: %s", e)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def reasoner(self):
        """Lazy-load the KnowledgeReasoner singleton."""
        if self._reasoner is None:
            from core.knowledge_reasoner import get_reasoner
            self._reasoner = get_reasoner()
        return self._reasoner

    @property
    def graph(self):
        """Access the SemanticGraph (may be None if not built)."""
        r = self.reasoner
        return r.graph if r else None

    @property
    def is_ready(self) -> bool:
        """Check if the graph is built and ready for queries."""
        try:
            if not self.reasoner:
                return False
            # Force graph build if needed (lazy initialization)
            if not self.reasoner.is_ready():
                try:
                    self.reasoner.build_graph()
                except Exception:
                    pass
            return self.reasoner.is_ready()
        except Exception:
            return False

    # ── Phase 1: Perception → GraphQuery ──────────────────────────────────

    def perceive_as_graph_query(self,
                                 perceptual_context: Dict[str, Any]) -> GraphQuery:
        """Convert perceptual context into a structured graph query.

        Extracts seed concepts from:
          - Active window title (cleaned: "Mozilla Firefox" → "firefox")
          - Other open window titles
          - OCR visible text (significant keywords)
          - UI element labels
          - Error indicators in text
          - Dialog content if present

        Args:
            perceptual_context: Dict from AliveOrchestrator.perceive() with keys:
                active_window, windows_open, visible_text, elements,
                has_error, layout, active_pid

        Returns:
            GraphQuery ready for activate_brain()
        """
        query = GraphQuery()

        active = perceptual_context.get("active_window", "")
        query.visible_text = perceptual_context.get("visible_text", "")
        query.has_error = perceptual_context.get("has_error", False)
        windows = perceptual_context.get("windows_open", [])
        elements = perceptual_context.get("elements", [])
        layout = perceptual_context.get("layout") or {}

        # ── 1. Active window title ─────────────────────────────────────────
        if active:
            cleaned = self._clean_window_title(active)
            query.active_window_concept = cleaned
            query.active_window_tokens = self._tokenize(cleaned)
            if cleaned and len(cleaned) >= 3:
                query.seed_concepts.append(cleaned)
            # Add longer tokens as separate seeds
            for t in query.active_window_tokens:
                if len(t) >= 4 and t not in _STOP_WORDS:
                    query.seed_concepts.append(t)

        # ── 2. Other window titles ─────────────────────────────────────────
        for w in windows[:10]:
            if w and w != active:
                cleaned = self._clean_window_title(w)
                if cleaned and len(cleaned) >= 4:
                    query.seed_concepts.append(cleaned)

        # ── 3. OCR visible text keywords ───────────────────────────────────
        if query.visible_text:
            keywords = self._extract_significant_keywords(
                query.visible_text, max_keywords=15
            )
            query.context_keywords = keywords
            for k in keywords:
                if k not in query.seed_concepts:
                    query.seed_concepts.append(k)

        # ── 4. UI element labels ───────────────────────────────────────────
        for elem in elements[:20]:
            text = elem.get("text", "") or elem.get("name", "") or ""
            if text and len(text) >= 4:
                cleaned = self._clean_window_title(text)
                if cleaned and cleaned not in query.seed_concepts:
                    query.seed_concepts.append(cleaned)

        # ── 5. Dialog detection ────────────────────────────────────────────
        if layout.get("dialogs"):
            query.has_dialogs = True
            for dlg in layout["dialogs"]:
                content_texts = [
                    c.get("text", "") for c in dlg.get("content", [])
                ]
                buttons = [b.get("text", "") for b in dlg.get("buttons", [])]
                combined = " ".join(content_texts + buttons)
                for t in self._tokenize(combined):
                    if len(t) >= 4 and t not in _STOP_WORDS:
                        query.seed_concepts.append(t)
            query.seed_concepts.append("dialog")

        # ── 6. Error detection from visible text ───────────────────────────
        for pattern in _ERROR_PATTERNS:
            for match in pattern.findall(query.visible_text):
                if isinstance(match, tuple):
                    for m in match:
                        if m and len(m) >= 3:
                            query.error_matches.append(m.lower())
                            query.seed_concepts.append(m.lower())
                elif match and len(match) >= 3:
                    query.error_matches.append(match.lower())
                    query.seed_concepts.append(match.lower())

        if query.error_matches:
            query.has_error = True

        # ── 7. Deduplicate preserving order ────────────────────────────────
        seen: Set[str] = set()
        unique_seeds: List[str] = []
        for s in query.seed_concepts:
            sl = s.lower().strip()
            if sl not in seen and len(sl) >= 3:
                # Filter out pure numbers and UUID-like strings
                if re.match(r'^[0-9a-f]{8,}$', sl):
                    continue
                seen.add(sl)
                unique_seeds.append(sl)
        query.seed_concepts = unique_seeds[:30]  # Cap to prevent over-spread

        log.debug("GraphQuery: %d seeds from '%s': %s",
                  len(query.seed_concepts),
                  active[:50] if active else "(no active window)",
                  query.seed_concepts[:10])
        return query

    def _clean_window_title(self, title: str) -> str:
        """Extract the core application name from a window title.

        Examples:
            "Mozilla Firefox"              → "firefox"
            "user@host: ~/projects — Konsole" → "konsole"
            "settings.py — Kate"           → "kate"
            "Dolphin — /home/ser"          → "dolphin"
            "Firefox ESR"                  → "firefox"
            "~ — Konsole"                  → "konsole"
        """
        if not title:
            return ""
        title = title.strip()

        # Separators that typically split app name from context
        separators = [" — ", " - ", " | ", " – ", " :: "]

        for sep in separators:
            if sep in title:
                parts = title.split(sep)
                # Last part is often the app name
                last = parts[-1].strip()
                # If last looks like a path or prompt, use first meaningful part
                if last.startswith("/") or last.startswith("~") or "@" in last:
                    # Check each part for an app-like name
                    for part in reversed(parts[:-1]):
                        part = part.strip()
                        if part and not part.startswith("/") and len(part) < 50:
                            return self._normalize_app_name(part)
                    # Fallback: first part
                    first = parts[0].strip()
                    if first and not first.startswith("/"):
                        return self._normalize_app_name(first)
                # If last part is short and app-like, use it
                if 2 <= len(last) <= 30 and not last.startswith("/"):
                    return self._normalize_app_name(last)
                # Otherwise use the first non-path part
                for part in parts:
                    part = part.strip()
                    if part and not part.startswith("/") and not part.startswith("~"):
                        return self._normalize_app_name(part)
                break

        return self._normalize_app_name(title)

    def _normalize_app_name(self, name: str) -> str:
        """Normalize an app name: lowercase, strip versions, remove prefixes."""
        cleaned = name.lower().strip()
        # Remove version numbers
        cleaned = re.sub(r'\bv?\d+\.\d+[^\s]*\b', '', cleaned)
        # Remove known prefixes
        for prefix in ["mozilla ", "google ", "gnu ", "gnome ", "kde ",
                       "microsoft ", "apple "]:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
        # Remove ESR, Dev, Beta suffixes
        cleaned = re.sub(r'\b(esr|dev|beta|alpha|nightly)\b', '', cleaned)
        # Collapse whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    def _tokenize(self, text: str) -> List[str]:
        """Extract meaningful word tokens from text."""
        tokens = re.findall(r'[a-zA-Záéíóúñ0-9_-]{3,}', text.lower())
        return [t for t in tokens if t not in _STOP_WORDS]

    def _extract_significant_keywords(self,
                                       text: str,
                                       max_keywords: int = 15) -> List[str]:
        """Extract significant keywords using TF-IDF-like scoring.

        Prioritizes words that are:
          - Longer (more specific)
          - Appear with moderate frequency (informative, not noise)
          - Not pure numbers or short abbreviations
        """
        tokens = self._tokenize(text)
        # Count frequencies
        freq: Dict[str, int] = {}
        for t in tokens:
            if len(t) >= 4:
                freq[t] = freq.get(t, 0) + 1

        if not freq:
            return []

        max_f = max(freq.values())
        scored: List[Tuple[str, float]] = []
        for word, count in freq.items():
            # Skip pure numbers
            if word.isdigit():
                continue
            # TF normalization
            tf_norm = count / max_f
            # Length bonus: prefer 5-12 char words
            len_score = min(len(word) / 8.0, 1.5)
            # Penalize very common short words more
            rarity_bonus = 0.3 if count == 1 and len(word) >= 6 else 0.0
            score = tf_norm * 0.3 + len_score * 0.5 + rarity_bonus
            scored.append((word, score))

        scored.sort(key=lambda x: -x[1])
        return [w for w, _ in scored[:max_keywords]]

    # ── Phase 2: GraphQuery → ActivatedContext ────────────────────────────

    def activate_brain(self,
                        query: GraphQuery,
                        max_depth: int = 3) -> ActivatedContext:
        """Fire spreading activation from perceptual seed concepts.

        Three-stage process:
          A. Direct firing: find matching graph nodes for each seed concept,
             fire them (triggers spreading activation to neighbors)
          B. Active collection: collect all neurons above activation threshold
          C. BFS expansion: from top-5 activated neurons, expand one hop
             to pull in related concepts

        Args:
            query: GraphQuery from perceive_as_graph_query()
            max_depth: Max spreading depth (default 3, graph caps at 2 internally)

        Returns:
            ActivatedContext with all fired neurons and activation levels
        """
        ctx = ActivatedContext()
        ctx.active_window_concept = query.active_window_concept
        t0 = time.time()

        if not self.is_ready:
            ctx.reasoning_trace.append(
                "Graph not ready — using keyword-only activation"
            )
            ctx.graph_ready = False
            return self._fallback_activate(query, ctx)

        ctx.graph_ready = True
        g = self.graph
        if not g:
            return self._fallback_activate(query, ctx)

        try:
            # Reset all neurons to resting state (clean slate)
            g.reset_neuron_state()

            self._last_fired = time.time()
            self._fired_count += 1

            all_fired_ids: Set[str] = set()
            all_fired_nodes: List[Tuple[str, float, str]] = []

            # ── Stage A: Direct concept firing ─────────────────────────────
            for concept in query.seed_concepts:
                if not concept or len(concept) < 3:
                    continue

                matches = self._find_concept_matches(concept)
                if not matches:
                    ctx.reasoning_trace.append(
                        f"No graph match for: '{concept}'"
                    )
                    continue

                for node, score in matches:
                    if node.id in all_fired_ids:
                        continue

                    # Boost: proportional to match score, range 0.06–0.18
                    boost = 0.06 + score * 0.12
                    try:
                        fired = g.fire_neuron(node.id, boost=boost)

                        for fid in fired:
                            if fid not in all_fired_ids:
                                all_fired_ids.add(fid)
                                fn = g.nodes.get(fid)
                                if fn:
                                    all_fired_nodes.append(
                                        (fn.concept, fn.activation, fn.category)
                                    )

                        ctx.reasoning_trace.append(
                            f"Fired '{node.concept[:30]}' "
                            f"(score={score:.2f}, boost={boost:.3f}) "
                            f"→ {len(fired)} neurons"
                        )
                    except Exception as e:
                        log.debug("fire_neuron(%s) failed: %s", node.concept, e)

            # ── Stage B: Collect active neurons (above threshold) ──────────
            active_neurons = g.get_active_neurons(threshold=0.15, max_results=60)
            for nid, activation in active_neurons:
                if nid not in all_fired_ids:
                    all_fired_ids.add(nid)
                    node = g.nodes.get(nid)
                    if node:
                        all_fired_nodes.append(
                            (node.concept, activation, node.category)
                        )

            # ── Stage C: BFS expansion from top activated neurons ──────────
            all_fired_nodes.sort(key=lambda x: -x[1])
            expanded_count = 0
            for concept, activation, category in all_fired_nodes[:5]:
                node = g.get_node_by_concept(concept)
                if not node:
                    continue
                try:
                    related = g.get_related(node.id, max_depth=1, max_results=15)
                    for rel_node, rel_type, weight, depth in related:
                        if rel_node.id not in all_fired_ids:
                            spread_act = activation * weight * 0.5
                            if spread_act > 0.04:
                                all_fired_ids.add(rel_node.id)
                                all_fired_nodes.append(
                                    (rel_node.concept, spread_act, rel_node.category)
                                )
                                expanded_count += 1
                except Exception as e:
                    log.debug("get_related(%s) failed: %s", concept, e)

            if expanded_count:
                ctx.reasoning_trace.append(
                    f"BFS expanded +{expanded_count} related concepts"
                )

            # Sort final results by activation
            all_fired_nodes.sort(key=lambda x: -x[1])

            # Build activation map (deduplicate by concept)
            activation_map: Dict[str, float] = {}
            for concept, act, cat in all_fired_nodes:
                cl = concept.lower()
                if cl in activation_map:
                    activation_map[cl] = max(activation_map[cl], act)
                else:
                    activation_map[cl] = act

            # Compute dominant categories
            cat_activation: Dict[str, float] = {}
            for concept, act, cat in all_fired_nodes:
                if cat:
                    cat_activation[cat] = cat_activation.get(cat, 0) + act
            dominant_cats = sorted(
                cat_activation.items(), key=lambda x: -x[1]
            )[:5]

            ctx.fired_neurons = [
                (c, a, cat) for c, a, cat in all_fired_nodes[:40]
            ]
            ctx.activation_map = activation_map
            ctx.total_neurons_fired = len(all_fired_ids)
            ctx.dominant_categories = dominant_cats
            ctx.reasoning_trace.append(
                f"Total: {ctx.total_neurons_fired} neurons fired, "
                f"top categories: {[c for c, _ in dominant_cats[:3]]}"
            )

            log.info(
                "Brain activation: %d neurons from %d seeds, "
                "dominant: %s",
                ctx.total_neurons_fired,
                len(query.seed_concepts),
                [(c, round(a, 2)) for c, a in dominant_cats[:3]],
            )

        except Exception as e:
            log.error("activate_brain error: %s", e)
            ctx.reasoning_trace.append(f"Activation error: {e}")
            return self._fallback_activate(query, ctx)

        ctx.elapsed_ms = (time.time() - t0) * 1000
        return ctx

    def _find_concept_matches(self,
                               concept: str) -> List[Tuple[Any, float]]:
        """Find graph nodes matching a concept string.

        Strategy (in order):
          1. Exact concept lookup
          2. Fuzzy token-overlap search
          3. Decomposed keyword search (for multi-word concepts)
        """
        g = self.graph
        if not g:
            return []

        # 1. Exact match
        node = g.get_node_by_concept(concept)
        if node:
            return [(node, 0.85)]

        # 2. Fuzzy search
        matches = g.find_similar_concepts(concept, top_k=3)
        if matches:
            # Filter to meaningful scores
            filtered = [(n, s) for n, s in matches if s > 0.05]
            if filtered:
                return filtered

        # 3. Token-level search (for multi-word concepts)
        tokens = self._tokenize(concept)
        if len(tokens) > 1:
            for token in tokens:
                if len(token) >= 4:
                    token_matches = g.find_similar_concepts(token, top_k=2)
                    if token_matches and token_matches[0][1] > 0.08:
                        return [(token_matches[0][0], token_matches[0][1] * 0.7)]

        return []

    def _fallback_activate(self,
                            query: GraphQuery,
                            ctx: ActivatedContext) -> ActivatedContext:
        """Fallback when graph is not ready: use seed concepts directly."""
        for concept in query.seed_concepts[:20]:
            ctx.fired_neurons.append((concept, 0.5, "unknown"))
            ctx.activation_map[concept.lower()] = 0.5
        ctx.total_neurons_fired = len(ctx.fired_neurons)
        ctx.reasoning_trace.append(
            f"Fallback: {ctx.total_neurons_fired} seed concepts "
            f"with default activation"
        )
        return ctx

    # ── Phase 3: ActivatedContext → Decision ──────────────────────────────

    def reason_from_activation(
        self,
        activated: ActivatedContext,
        available_actions: Optional[List[str]] = None,
        urgency_overrides: Optional[Dict[str, float]] = None,
    ) -> Decision:
        """Score candidate actions based on graph activation patterns.

        For each candidate action:
          1. Tokenize its semantic descriptor
          2. Check each activated neuron: does its concept/category/definition
             overlap with the action's semantic tokens?
          3. Sum activations, weighted by match type:
             - Direct concept match:   weight 1.0
             - Category match:         weight 0.6
             - Token-in-definition:    weight 0.4

        The action with the highest total activation score wins.

        Args:
            activated: Output from activate_brain()
            available_actions: Which actions to consider (default: all)
            urgency_overrides: Action → minimum confidence (for urgent situations)

        Returns:
            Decision with ranked action scores and reasoning
        """
        actions = available_actions or list(ACTION_DESCRIPTORS.keys())
        urgency = urgency_overrides or {}

        action_scores: List[ActionScore] = []

        # Build a combined text block of all activated content for
        # efficient token matching
        activated_combined = " ".join(
            f"{c} {cat}" for c, _, cat in activated.fired_neurons
        ).lower()

        # Active window focus: neurons matching the active window concept
        # get boosted weight. The brain pays more attention to what's in focus.
        active_concept = activated.active_window_concept.lower().strip()
        active_tokens = set(self._tokenize(active_concept)) if active_concept else set()

        for action_raw in actions:
            # Accept both string actions and dict actions with 'name' key
            if isinstance(action_raw, dict):
                action = action_raw.get('name', str(action_raw))
                # Also add any custom descriptor from the dict
                custom_desc = action_raw.get('descriptor', '')
                if custom_desc:
                    self._descriptor_tokens[action] = set(self._tokenize(custom_desc))
            else:
                action = str(action_raw)
            desc_tokens = self._descriptor_tokens.get(action, set())
            if not desc_tokens:
                # No descriptor — action is always available at base score
                action_scores.append(ActionScore(
                    action=action, score=0.05, direct_hits=0
                ))
                continue

            direct_hits = 0
            direct_score = 0.0
            category_hits = 0
            category_score = 0.0
            spread_hits = 0
            spread_score = 0.0
            supporting: List[str] = []
            supporting_cats: List[str] = []

            for concept, activation, category in activated.fired_neurons:
                concept_lower = concept.lower()
                concept_tokens = set(self._tokenize(concept_lower))

                # Active-window focus boost: neurons matching the active
                # window concept get 2.5x effective activation weight.
                # This ensures the brain prioritizes what the user is
                # currently looking at over background windows.
                is_focus_match = (
                    active_concept and
                    (concept_lower == active_concept or
                     bool(concept_tokens & active_tokens))
                )
                effective_activation = activation * (2.5 if is_focus_match else 1.0)

                # Type 1: Direct token overlap between concept and descriptor
                token_overlap = concept_tokens & desc_tokens
                if token_overlap:
                    direct_hits += 1
                    direct_score += effective_activation
                    if concept not in supporting:
                        supporting.append(concept)
                    continue  # Don't double-count

                # Type 2: Concept name itself is in or contains descriptor tokens
                concept_matched = False
                for dt in desc_tokens:
                    if len(dt) >= 4 and dt in concept_lower:
                        direct_hits += 1
                        direct_score += effective_activation * 0.8
                        if concept not in supporting:
                            supporting.append(concept)
                        concept_matched = True
                        break
                if concept_matched:
                    continue

                # Type 3: Category match
                if category and category.lower() in desc_tokens:
                    category_hits += 1
                    category_score += effective_activation * 0.6
                    if category not in supporting_cats:
                        supporting_cats.append(category)
                    continue

                # Type 4: Descriptor token appears anywhere in activated content
                for dt in desc_tokens:
                    if len(dt) >= 5 and dt in activated_combined:
                        spread_hits += 1
                        spread_score += effective_activation * 0.3
                        break

            # Normalize scores — use capped sum, not average-per-hit.
            # Average-per-hit penalizes actions with many weak matches,
            # which is counterproductive for broad semantic fields.
            direct_norm = min(direct_score, 1.0) if direct_hits > 0 else 0.0
            cat_norm = min(category_score, 1.0) if category_hits > 0 else 0.0
            spread_norm = min(spread_score, 1.0) if spread_hits > 0 else 0.0

            # Weighted combination: direct > category > spread
            combined = (
                direct_norm * 0.60 +
                cat_norm * 0.25 +
                spread_norm * 0.15
            )

            # Multi-hit bonuses
            if direct_hits >= 3:
                combined *= 1.15
            if direct_hits >= 5:
                combined *= 1.10
            if direct_hits >= 1 and category_hits >= 1:
                combined *= 1.10  # Cross-validation bonus

            # Apply learned concept→action weights
            learned_boost = self._compute_learned_boost(action, activated)
            combined += learned_boost * 0.15

            # Apply urgency override (e.g., error detected → boost offer_help)
            if action in urgency:
                combined = max(combined, urgency[action])

            action_scores.append(ActionScore(
                action=action,
                score=round(min(combined, 1.0), 4),
                direct_hits=direct_hits,
                category_hits=category_hits,
                spread_hits=spread_hits,
                supporting_concepts=supporting[:10],
                supporting_categories=supporting_cats[:5],
            ))

        # Sort by score descending
        action_scores.sort(key=lambda x: -x.score)

        # Ensure "observe" is always available as ultimate fallback
        if not action_scores or action_scores[0].score < 0.03:
            observe = ActionScore(action="observe", score=0.08, direct_hits=0)
            action_scores.insert(0, observe)

        top = action_scores[0]

        # Build human-readable reason
        reason_parts: List[str] = []
        if top.direct_hits > 0:
            reason_parts.append(f"{top.direct_hits} concept matches")
        if top.category_hits > 0:
            reason_parts.append(f"{top.category_hits} category matches")
        if top.spread_hits > 0:
            reason_parts.append(f"{top.spread_hits} spread matches")
        if top.supporting_concepts:
            reason_parts.append(
                f"via: {', '.join(top.supporting_concepts[:5])}"
            )

        reason = (
            f"[brain] {top.action}: {'; '.join(reason_parts)}"
            if reason_parts
            else f"[brain] {top.action}: default (low activation)"
        )

        decision = Decision(
            action=top.action,
            reason=reason,
            confidence=top.score,
            primary_action=top,
            alternatives=action_scores[1:6],
            activated_context=activated,
        )

        log.info(
            "Brain decision: %s (conf=%.3f, direct=%d, cat=%d, spread=%d, "
            "alternatives=%s)",
            decision.action, decision.confidence,
            top.direct_hits, top.category_hits, top.spread_hits,
            [(a.action, round(a.score, 2)) for a in decision.alternatives[:3]],
        )

        return decision

    def _compute_learned_boost(self,
                                action: str,
                                activated: ActivatedContext) -> float:
        """Compute boost from learned concept→action associations."""
        if action not in self._concept_action_weights:
            return 0.0

        weights = self._concept_action_weights[action]
        if not weights:
            return 0.0

        boost = 0.0
        count = 0
        for concept, activation in activated.activation_map.items():
            if concept in weights:
                boost += activation * weights[concept]
                count += 1

        if count == 0:
            return 0.0

        return min(boost / count, 1.0)

    # ── Phase 4: Decision + Outcome → Learning ────────────────────────────

    def learn_from_outcome(self,
                            decision: Decision,
                            outcome: str) -> int:
        """Strengthen or weaken edges based on whether the decision worked.

        Hebbian principle: "neurons that fire together, wire together."
          - Success: Strengthen edges between perceptual concepts and the
            chosen action's semantic field concepts.
          - Failure: Weaken those edges.

        Also updates the internal concept→action weight memory for
        future decisions.

        Args:
            decision: The Decision that was executed
            outcome: 'success', 'partial', 'failure', or 'skipped'

        Returns:
            Number of edges adjusted (0 if learning was skipped)
        """
        if not self.is_ready:
            return 0
        if not decision.activated_context:
            return 0
        if not decision.primary_action:
            return 0

        activated = decision.activated_context
        action = decision.action

        # Learning rates by outcome
        outcome_rates = {
            "success": 0.06,    # Moderate strengthen
            "partial": 0.02,    # Slight strengthen
            "skipped": 0.0,     # No change
            "failure": -0.04,   # Weaken
        }
        rate = outcome_rates.get(outcome, 0.0)
        if rate == 0.0:
            return 0

        g = self.graph
        if not g:
            return 0

        edges_adjusted = 0

        try:
            # Get action descriptor tokens
            desc_tokens = self._descriptor_tokens.get(action, set())
            if not desc_tokens:
                return 0

            # Find graph nodes matching the action's semantic field
            action_nodes: List[Tuple[str, float]] = []
            for dt in desc_tokens:
                if len(dt) < 4:
                    continue
                matches = g.find_similar_concepts(dt, top_k=2)
                for node, score in matches:
                    if score > 0.15:
                        action_nodes.append((node.concept, score))

            if not action_nodes:
                # Fallback: use action name itself
                for part in action.split("_"):
                    if len(part) >= 3:
                        action_nodes.append((part, 0.6))
            if not action_nodes:
                return 0

            # Get the top perceptual concepts (what EIDOS saw)
            perceptual_concepts = activated.fired_neurons[:8]

            # ── Hebbian learning: adjust edges ────────────────────────────
            for p_concept, p_activation, _ in perceptual_concepts:
                p_node = g.get_node_by_concept(p_concept)
                if not p_node:
                    continue

                for a_concept, a_score in action_nodes[:5]:
                    a_node = g.get_node_by_concept(a_concept)
                    if not a_node or a_node.id == p_node.id:
                        continue

                    if rate > 0:
                        # Strengthen: Hebbian learning creates/strengthens edge
                        g.hebbian_learn(p_concept, a_concept)
                        edges_adjusted += 1
                    elif rate < 0:
                        # Weaken: find edge and reduce weight
                        abs_rate = abs(rate)
                        for i, edge in enumerate(g.edges):
                            if ((edge.source_id == p_node.id and
                                 edge.target_id == a_node.id) or
                                (edge.source_id == a_node.id and
                                 edge.target_id == p_node.id)):
                                edge.weight = max(
                                    edge.weight - abs_rate, 0.01
                                )
                                edges_adjusted += 1
                                break

                # Also update internal weight memory (fast lookup for scoring)
                if action not in self._concept_action_weights:
                    self._concept_action_weights[action] = {}
                weights = self._concept_action_weights[action]
                key = p_concept.lower()
                current = weights.get(key, 0.0)
                weights[key] = max(0.0, min(1.0, current + rate))

            if edges_adjusted > 0:
                log.info(
                    "Brain learning: outcome=%s rate=%.3f → %d edges, "
                    "action='%s', concepts_learned=%d",
                    outcome, rate, edges_adjusted, action,
                    len(self._concept_action_weights.get(action, {})),
                )
                # Persist learned weights so they survive restarts
                self._save_brain_weights()

        except Exception as e:
            log.error("learn_from_outcome error: %s", e)

        return edges_adjusted

    # ── Introspection ─────────────────────────────────────────────────────

    def get_action_profile(self, action: str) -> Dict[str, Any]:
        """Return the current learned profile for an action.

        Includes bootstrap descriptor plus any learned concept weights.
        """
        return {
            "action": action,
            "descriptor_tokens": sorted(
                self._descriptor_tokens.get(action, set())
            ),
            "learned_weights": dict(sorted(
                self._concept_action_weights.get(action, {}).items(),
                key=lambda x: -x[1],
            )[:20]),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return brain statistics."""
        return {
            "ready": self.is_ready,
            "fired_count": self._fired_count,
            "last_fired": self._last_fired,
            "actions_learned": list(self._concept_action_weights.keys()),
            "total_learned_associations": sum(
                len(w) for w in self._concept_action_weights.values()
            ),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_brain_instance: Optional[EidosBrain] = None


def get_brain() -> EidosBrain:
    """Get the singleton EidosBrain instance."""
    global _brain_instance
    if _brain_instance is None:
        _brain_instance = EidosBrain()
    return _brain_instance


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys
    from pathlib import Path as _Path
    _EIDOS_ROOT = _Path(__file__).resolve().parent.parent
    if str(_EIDOS_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_EIDOS_ROOT))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )

    print("=" * 60)
    print("EidosBrain — Smoke Test")
    print("=" * 60)

    brain = get_brain()

    # Wait for graph
    print("\nWaiting for reasoner graph...")
    import time as _time
    waited = 0
    while not brain.is_ready and waited < 90:
        _time.sleep(2)
        waited += 2
        if waited % 10 == 0:
            print(f"  ... {waited}s")

    if not brain.is_ready:
        print(f"Graph not ready after {waited}s. Tests will use fallback mode.")
    else:
        g = brain.graph
        print(f"Graph ready: {g.size[0]} nodes, {g.size[1]} edges")

    # ── Test 1: Firefox context ───────────────────────────────────────────
    # NOTE: windows_open is a list of strings (window names), matching the
    # format returned by AliveOrchestrator.perceive().
    print("\n── Test 1: Firefox window ──")
    ctx1 = {
        "active_window": "Mozilla Firefox",
        "windows_open": ["Mozilla Firefox", "~ — Konsole"],
        "visible_text": "Welcome to Firefox Browser",
        "elements": [],
        "has_error": False,
    }
    query1 = brain.perceive_as_graph_query(ctx1)
    print(f"  Seeds: {query1.seed_concepts[:10]}")
    activated1 = brain.activate_brain(query1)
    print(f"  Neurons fired: {activated1.total_neurons_fired}")
    print(f"  Top 5: {[(c, round(a, 2)) for c, a, _ in activated1.fired_neurons[:5]]}")
    decision1 = brain.reason_from_activation(activated1)
    print(f"  Decision: {decision1.action} (conf={decision1.confidence:.3f})")
    print(f"  Reason: {decision1.reason}")
    if decision1.primary_action:
        print(f"  Supporting: {decision1.primary_action.supporting_concepts[:5]}")
        print(f"  Direct hits: {decision1.primary_action.direct_hits}")
        print(f"  Alternatives: {[(a.action, round(a.score, 3)) for a in decision1.alternatives[:3]]}")

    # ── Test 2: Terminal context ──────────────────────────────────────────
    print("\n── Test 2: Terminal window ──")
    ctx2 = {
        "active_window": "user@host: ~/EIDOS — Konsole",
        "windows_open": ["user@host: ~/EIDOS — Konsole"],
        "visible_text": "ls -la\ntotal 128\ndrwxr-xr-x 2 ser ser 4096",
        "elements": [],
        "has_error": False,
    }
    query2 = brain.perceive_as_graph_query(ctx2)
    print(f"  Seeds: {query2.seed_concepts[:10]}")
    activated2 = brain.activate_brain(query2)
    print(f"  Neurons fired: {activated2.total_neurons_fired}")
    print(f"  Top 5: {[(c, round(a, 2)) for c, a, _ in activated2.fired_neurons[:5]]}")
    decision2 = brain.reason_from_activation(activated2)
    print(f"  Decision: {decision2.action} (conf={decision2.confidence:.3f})")
    if decision2.primary_action:
        print(f"  Direct hits: {decision2.primary_action.direct_hits}")
        print(f"  Supporting: {decision2.primary_action.supporting_concepts[:5]}")
    print(f"  Alternatives: {[(a.action, round(a.score, 3)) for a in decision2.alternatives[:3]]}")

    # ── Test 3: Error context ─────────────────────────────────────────────
    print("\n── Test 3: Error on screen ──")
    ctx3 = {
        "active_window": "Terminal",
        "windows_open": ["Terminal"],
        "visible_text": "Traceback (most recent call last):\n  File 'app.py', line 42\nException: Connection refused",
        "elements": [],
        "has_error": True,
    }
    query3 = brain.perceive_as_graph_query(ctx3)
    print(f"  Seeds: {query3.seed_concepts[:10]}")
    print(f"  Error matches: {query3.error_matches}")
    activated3 = brain.activate_brain(query3)
    decision3 = brain.reason_from_activation(
        activated3,
        urgency_overrides={"offer_help": 0.7} if query3.has_error else None,
    )
    print(f"  Decision: {decision3.action} (conf={decision3.confidence:.3f})")
    if decision3.primary_action:
        print(f"  Direct hits: {decision3.primary_action.direct_hits}")
        print(f"  Supporting: {decision3.primary_action.supporting_concepts[:5]}")

    # ── Test 4: Idle desktop ──────────────────────────────────────────────
    print("\n── Test 4: Idle desktop ──")
    ctx4 = {
        "active_window": "",
        "windows_open": [],
        "visible_text": "",
        "elements": [],
        "has_error": False,
    }
    query4 = brain.perceive_as_graph_query(ctx4)
    print(f"  Seeds: {query4.seed_concepts}")
    activated4 = brain.activate_brain(query4)
    decision4 = brain.reason_from_activation(activated4)
    print(f"  Decision: {decision4.action} (conf={decision4.confidence:.3f})")

    # ── Test 5: Learning ──────────────────────────────────────────────────
    print("\n── Test 5: Learning from outcome ──")
    if brain.is_ready and decision1.activated_context:
        n = brain.learn_from_outcome(decision1, "success")
        print(f"  Learned from success: {n} edges adjusted")
        n2 = brain.learn_from_outcome(decision1, "failure")
        print(f"  Learned from failure: {n2} edges adjusted")
        # Show learned profile
        profile = brain.get_action_profile("browse_web")
        if profile["learned_weights"]:
            print(f"  Learned browse_web weights: {dict(list(profile['learned_weights'].items())[:5])}")

    # ── Stats ─────────────────────────────────────────────────────────────
    print("\n── Stats ──")
    for k, v in brain.get_stats().items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 60)
    print("Smoke test complete.")
    print("=" * 60)
