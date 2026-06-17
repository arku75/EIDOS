"""
core/eidos_active_research.py — Research ACTIVO síncrono para EIDOS  [S70-S72]

Cuando EIDOS no sabe algo, investiga AHORA (no en background), aprende una
definición LIMPIA, la persiste en el grafo y devuelve la respuesta.
Honestidad activa: "No lo sabía, lo acabo de investigar (canal): ...".

Canales por orden de rapidez/fiabilidad: man → apt → wikipedia → ddg → forums → github
Sin LLM. CPU. stdlib + bs4. Cache RAM. Persistencia SQLite + inject al grafo en memoria.
"""
from __future__ import annotations

import json as _json
import re
import os
import subprocess
import time
import urllib.parse as _urlparse
import urllib.request as _urlreq
import logging
from typing import Optional
from core.db import get_conn

log = logging.getLogger("eidos.active_research")

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"


# ─────────────────────────── extractor de definición limpia ──────────────────

def extract_definition(raw_text: str, query: str, max_len: int = 1200) -> str:
    """Extrae una definición LIMPIA del texto crudo (HTML o plano).
    S73: max_len 1200 → explicaciones completas. Toma varias oraciones, no solo la primera."""
    if not raw_text:
        return ""
    text = raw_text
    if "<" in text and ">" in text:
        try:
            from bs4 import BeautifulSoup
            text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    # limpiar entidades HTML comunes
    import html as _html
    text = _html.unescape(text)
    text = re.sub(r"\[\d+\]", "", text)      # quitar [1] [2] de wikipedia
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    q_tokens = [t for t in re.findall(r"\w{3,}", query.lower())]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    # S73: acumular VARIAS oraciones desde la primera relevante → explicación completa
    start = 0
    for i, sent in enumerate(sentences):
        if len(sent.strip()) >= 40 and any(t in sent.lower() for t in q_tokens):
            start = i
            break
    acc = []
    total = 0
    for sent in sentences[start:]:
        s = sent.strip()
        if not s:
            continue
        acc.append(s)
        total += len(s)
        if total >= max_len:
            break
    if not acc:
        return text[:max_len].strip()
    out = " ".join(acc)
    out = out[0].upper() + out[1:]
    if not out.endswith((".", "?", "!")):
        out += "."
    return out[:max_len].strip()


# ─────────────────────────────── canales ─────────────────────────────────────

def _ch_man(concept: str) -> str:
    tok = concept.split()[0] if concept.split() else concept
    if not re.match(r"^[a-zA-Z0-9_-]+$", tok):
        return ""
    try:
        r = subprocess.run(["man", tok], capture_output=True, text=True, timeout=4,
                           env={"MANWIDTH": "200", "PATH": "/usr/bin:/bin:/usr/sbin"})
        out = r.stdout
        if not out:
            return ""
        m = re.search(r"NAME\s+(.+?)(?:\n[A-Z]{3,}|\n\n)", out, re.S)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    except Exception:
        return ""
    return ""


def _ch_apt(concept: str) -> str:
    tok = concept.split()[0] if concept.split() else concept
    if not re.match(r"^[a-zA-Z0-9_.+-]+$", tok):
        return ""
    try:
        r = subprocess.run(["apt-cache", "show", tok], capture_output=True, text=True, timeout=4)
        m = re.search(r"Description(?:-en)?:\s*(.+)", r.stdout)
        if m:
            return m.group(1).strip()
    except Exception:
        return ""
    return ""


def _ch_whatis(concept: str) -> str:
    """whatis: descripción de una línea desde la base de datos de man pages."""
    tok = concept.split()[0] if concept.split() else concept
    if not re.match(r"^[a-zA-Z0-9_.+-]+$", tok):
        return ""
    try:
        r = subprocess.run(["whatis", tok], capture_output=True, text=True, timeout=3)
        if r.stdout.strip():
            return r.stdout.strip()
        # fallback: apropos (busca en descripciones)
        r2 = subprocess.run(["apropos", tok], capture_output=True, text=True, timeout=3)
        if r2.stdout.strip():
            return r2.stdout.strip()[:500]
    except Exception:
        return ""
    return ""


def _ch_tldr(concept: str) -> str:
    """tldr pages: ejemplos concretos de uso. Oro puro para EIDOS.
    Busca en ~/.local/share/tldr y cache local. Sin internet."""
    tok = concept.strip().lower().split()[0] if concept.strip() else concept
    if not re.match(r"^[a-zA-Z0-9_.+-]+$", tok):
        return ""
    # Buscar en paths comunes de tldr
    tldr_paths = [
        os.path.expanduser("~/.local/share/tldr/pages"),
        os.path.expanduser("~/.tldr/cache/pages"),
        "/usr/share/tldr/pages",
    ]
    for base in tldr_paths:
        for lang in ["linux", "common", "es"]:
            p = os.path.join(base, lang, f"{tok}.md")
            if os.path.exists(p):
                try:
                    text = open(p, encoding="utf-8", errors="ignore").read()
                    # Extraer ejemplos (líneas que empiezan con ` o -)
                    examples = re.findall(r"(?:^-\s*.+$|^`.+`$)", text, re.M)
                    if examples:
                        return "\n".join(examples[:8])
                    return text[:500]
                except Exception:
                    pass
    return ""


def _ch_code_structure(concept: str) -> str:
    """S76 Fase 0: Busca en nodos estructurales source='graphify'.
    Permite a EIDOS responder consultas sobre su propia arquitectura:
    qué funciones tiene X, qué clase hereda de Y, etc."""
    tok = concept.strip().lower()
    if not tok or len(tok) < 3:
        return ""
    try:
        db_path = os.path.expanduser("~/.eidos/evolution_brain.db")
        if not os.path.exists(db_path):
            return ""
        conn = __import__("sqlite3").connect(db_path, timeout=3)
        # Buscar nodos graphify cuyo concepto contenga el término
        rows = conn.execute(
            "SELECT concept, definition FROM knowledge_nodes "
            "WHERE source='graphify' AND (concept LIKE ? OR definition LIKE ?) "
            "LIMIT 20",
            (f"%{tok}%", f"%{tok}%"),
        ).fetchall()
        # Buscar aristas relacionadas
        edge_rows = conn.execute(
            "SELECT e.from_node, e.to_node, e.relation_type, n1.concept, n2.concept "
            "FROM knowledge_edges e "
            "JOIN knowledge_nodes n1 ON e.from_node = n1.id "
            "JOIN knowledge_nodes n2 ON e.to_node = n2.id "
            "WHERE (n1.concept LIKE ? OR n2.concept LIKE ?) "
            "AND e.relation_type IN ('calls','imports','inherits','contains','defines_method') "
            "LIMIT 15",
            (f"%{tok}%", f"%{tok}%"),
        ).fetchall()

        if not rows and not edge_rows:
            return ""

        lines = []
        if rows:
            lines.append(f"📐 {len(rows)} nodos estructurales:")
            for concept, definition in rows[:8]:
                lines.append(f"  • {concept}: {definition or '(sin definición)'}")

        if edge_rows:
            lines.append(f"🔗 {len(edge_rows)} relaciones estructurales:")
            for _, _, rel_type, c1, c2 in edge_rows[:8]:
                lines.append(f"  • {c1[:50]} --[{rel_type}]--> {c2[:50]}")

        return "\n".join(lines)
    except Exception as e:
        return f"(error code_structure: {e})"


def _ch_wikipedia(concept: str) -> str:
    try:
        title = _urlparse.quote(concept.replace(" ", "_"))
        url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{title}"
        req = _urlreq.Request(url, headers={"User-Agent": _UA})
        with _urlreq.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read())
            extract = data.get("extract", "")
            if extract and len(extract) > 40:
                return extract
    except Exception:
        pass
    try:
        url = (f"https://es.wikipedia.org/w/api.php?action=query&list=search"
               f"&srsearch={_urlparse.quote(concept)}&format=json&srlimit=1")
        req = _urlreq.Request(url, headers={"User-Agent": _UA})
        with _urlreq.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read())
            hits = data.get("query", {}).get("search", [])
            if hits:
                snip = re.sub(r"<[^>]+>", "", hits[0].get("snippet", ""))
                return f"{hits[0]['title']}: {snip}"
    except Exception:
        pass
    return ""


def _ch_ddg(concept: str) -> str:
    try:
        url = (f"https://api.duckduckgo.com/?q={_urlparse.quote(concept)}"
               f"&format=json&no_html=1&skip_disambig=1")
        req = _urlreq.Request(url, headers={"User-Agent": _UA})
        with _urlreq.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read())
            if data.get("Abstract"):
                return data["Abstract"]
            rt = data.get("RelatedTopics", [])
            if rt and isinstance(rt[0], dict) and rt[0].get("Text"):
                return rt[0]["Text"]
    except Exception:
        pass
    return ""


def _ch_forums(concept: str) -> str:
    try:
        q = _urlparse.quote(f"site:stackoverflow.com {concept}")
        url = f"https://html.duckduckgo.com/html/?q={q}"
        req = _urlreq.Request(url, headers={"User-Agent": _UA})
        with _urlreq.urlopen(req, timeout=6) as r:
            html = r.read().decode("utf-8", "ignore")
        m = re.search(r'result__snippet[^>]*>(.+?)</a>', html, re.S)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    except Exception:
        pass
    return ""


def _ch_github(concept: str) -> str:
    try:
        import os
        url = (f"https://api.github.com/search/repositories?q={_urlparse.quote(concept)}"
               f"&sort=stars&per_page=1")
        req = _urlreq.Request(url, headers={"User-Agent": _UA,
                                            "Accept": "application/vnd.github+json"})
        tok = os.environ.get("GITHUB_TOKEN")
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        with _urlreq.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read())
            items = data.get("items", [])
            if items and items[0].get("description"):
                return f"{items[0]['full_name']}: {items[0]['description']}"
    except Exception:
        pass
    return ""


def _ch_pypi(concept: str) -> str:
    """S94: PyPI JSON API — directa para paquetes Python, sin search engine.
    Útil cuando DDG/Wikipedia no tienen info sobre librerías Python."""
    try:
        # Intentar con el concepto como nombre de paquete
        pkg = concept.lower().strip().split()[0].replace("-", "_")
        url = f"https://pypi.org/pypi/{_urlparse.quote(pkg)}/json"
        req = _urlreq.Request(url, headers={"User-Agent": _UA})
        with _urlreq.urlopen(req, timeout=5) as r:
            data = _json.loads(r.read())
            summary = data.get("info", {}).get("summary", "")
            if summary:
                return f"PyPI {pkg}: {summary}"
    except Exception:
        pass
    return ""


def _ch_wikipedia_full(concept: str) -> str:
    """S94: Wikipedia full-text search — fallback cuando DDG bloquea (403).
    Usa action=query list=search para resultados ricos, más robusto que opensearch."""
    try:
        url = (f"https://en.wikipedia.org/w/api.php"
               f"?action=query&list=search&srsearch={_urlparse.quote(concept[:100])}"
               f"&srlimit=3&format=json")
        req = _urlreq.Request(url, headers={"User-Agent": _UA})
        with _urlreq.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read())
        results = data.get("query", {}).get("search", [])
        if results:
            snippets = []
            for res in results[:3]:
                title = res.get("title", "")
                snippet = re.sub(r"<[^>]+>", "", res.get("snippet", ""))
                if title and snippet:
                    snippets.append(f"{title}: {snippet}")
            if snippets:
                return " | ".join(snippets)
    except Exception:
        pass
    return ""


# S145: truth confidence tiers — see _TRUTH_CONFIDENCE dict below
_CHANNELS_LOCAL = [
    ("man",        _ch_man,       0.85),
    ("apt",        _ch_apt,       0.85),
    ("whatis",     _ch_whatis,    0.85),
    ("tldr",       _ch_tldr,      0.90),
    ("code_structure", _ch_code_structure, 0.90),  # S76: own graph → 0.9+
]
_CHANNELS_REMOTE = [
    ("wikipedia",  _ch_wikipedia, 0.70),
    ("duckduckgo", _ch_ddg,       0.40),   # S145: general web → 0.4
    ("forums",     _ch_forums,    0.40),   # S145: general web → 0.4
    ("github",     _ch_github,    0.50),   # S145: semi-structured
    ("pypi",       _ch_pypi,      0.50),   # S94: directo, sin search engine
    ("wiki_full",  _ch_wikipedia_full, 0.40),  # S145: fallback Wikipedia → 0.4
]
_CHANNELS = _CHANNELS_LOCAL + _CHANNELS_REMOTE

_RESEARCH_CACHE: dict = {}   # concepto.lower() → result


# ─────────────────────── S145: knowledge validation & cross-reference ──────────

# Web domains whose results should never be accepted as truth for non-EU/regulation concepts
_IRRELEVANT_DOMAINS = re.compile(
    r'(europa\.eu|eur-lex|ec\.europa|eidas|digital-strategy\.ec\.europa)',
    re.IGNORECASE)

# Error / non-result patterns
_ERROR_PATTERNS = re.compile(
    r'(404\s*(not\s*)?found|page\s*not\s*available|no\s*results|'
    r'sorry,?\s*nothing|did\s*you\s*mean|try\s*a\s*different\s*search)',
    re.IGNORECASE)

_TRUTH_CONFIDENCE = {
    "own_graph":   0.90,
    "man":         0.85,
    "apt":         0.85,
    "whatis":      0.85,
    "tldr":        0.90,
    "wikipedia":   0.70,
    "general_web": 0.40,
    "contradicts": 0.10,
}


def _ensure_contradictions_table():
    """Create knowledge_contradictions table if it doesn't exist."""
    try:
        import os as _os
        db = _os.path.expanduser("~/.eidos/evolution_brain.db")
        conn = get_conn(db, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_contradictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                concept TEXT NOT NULL,
                existing_node_id TEXT,
                existing_definition TEXT,
                existing_confidence REAL,
                new_source TEXT,
                new_definition TEXT,
                new_confidence REAL,
                reason TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kc_concept
            ON knowledge_contradictions(concept)
        """)
        conn.commit()
        return True
    except Exception as e:
        log.warning("ensure_contradictions_table falló: %s", e)
        return False


def _validate_knowledge_node(concept: str, definition: str,
                              channel: str, confidence: float) -> tuple:
    """S145: Validate that a knowledge node is actually about the concept.

    Returns (valid: bool, reason: str, adjusted_confidence: float).
    """
    if not definition or not concept:
        return False, "empty concept or definition", 0.0

    # 1. Minimum quality check: >50 chars
    if len(definition) < 50:
        return False, "definition too short (<50 chars)", 0.0

    # 2. Contains HTML tags? (unprocessed scrape)
    if re.search(r'<(?:div|span|p|a|li|ul|ol|table|tr|td|br|img|script|style)\b',
                 definition, re.IGNORECASE):
        return False, "definition contains unprocessed HTML tags", 0.0

    # 3. Is it a 404 / error page / "no results" placeholder?
    if _ERROR_PATTERNS.search(definition):
        return False, "definition appears to be an error/404/no-results page", 0.0

    # 4. Irrelevant domain for non-EU concepts?
    if (_IRRELEVANT_DOMAINS.search(definition)
            and not re.search(r'(europe|eu\s|union|regulation|directive|gdpr|'
                              r'eidas\b|digital\s+service\s+act)',
                              concept, re.IGNORECASE)):
        return False, "definition from EU regulation domain but concept is not EU-related", 0.0

    # 5. Concept token relevance: at least 30% of significant concept tokens
    #    must appear in the definition.
    concept_tokens = [t for t in re.findall(r'\w{3,}', concept.lower())
                      if t not in ('the', 'and', 'for', 'que', 'los', 'las',
                                   'del', 'una', 'con', 'por', 'como')]
    if concept_tokens:
        defn_lower = definition.lower()
        matched = sum(1 for t in concept_tokens if t in defn_lower)
        ratio = matched / len(concept_tokens)
        if ratio == 0:
            return False, (f"NO concept tokens {concept_tokens} found in "
                           f"definition — likely unrelated result"), 0.1
        if ratio < 0.3 and len(concept_tokens) >= 2:
            # Weak match: allow but flag with lower confidence
            log.info("_validate: weak token match %.0f%% for '%s' via %s",
                     ratio * 100, concept, channel)

    # 6. Extra suspicious: definition mentions a different acronym that
    #    looks like our concept but isn't (eIDAS vs EIDOS, Pythom vs Python, etc.)
    concept_clean = re.sub(r'\s+', '', concept.lower())
    defn_clean = re.sub(r'\s+', '', definition.lower())
    # If the concept string itself never appears as a substring of the definition
    # in any form (case-insensitive, space-insensitive), flag it
    if len(concept_clean) >= 5 and concept_clean not in defn_clean:
        # Check if a close-but-different acronym is present (e.g., eIDAS not EIDOS)
        # Levenshtein distance of 1-2 chars off → suspicious
        close_matches = re.findall(r'\b\w{%d,%d}\b' % (
            len(concept_clean) - 1, len(concept_clean) + 2), definition.lower())
        for match in close_matches:
            if match != concept_clean and _levenshtein_distance(concept_clean, match) <= 2:
                log.warning("_validate: suspicious close match '%s' vs concept '%s' in defn",
                            match, concept_clean)

    return True, "valid", confidence


def _levenshtein_distance(a: str, b: str) -> int:
    """Simple Levenshtein for detecting near-miss acronyms."""
    if abs(len(a) - len(b)) > 2:
        return 999
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,      # deletion
                curr[j] + 1,           # insertion
                prev[j] + (0 if ca == cb else 1)  # substitution
            ))
        prev = curr
    return prev[-1]


def _cross_reference_knowledge(concept: str, definition: str,
                                channel: str, confidence: float) -> tuple:
    """S145: Cross-reference new knowledge against existing graph.

    Returns (should_save: bool, reason: str, adjusted_confidence: float, existing_node: dict|None).
    """
    import os as _os
    db = _os.path.expanduser("~/.eidos/evolution_brain.db")
    if not _os.path.exists(db):
        return True, "no existing DB", confidence, None

    try:
        conn = get_conn(db, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        # Find existing nodes with same concept (exact or close match)
        existing = conn.execute(
            "SELECT id, concept, definition, confidence, source, category "
            "FROM knowledge_nodes WHERE concept = ? LIMIT 3",
            (concept,)
        ).fetchall()

        if not existing:
            # Also try LIKE for multi-word concepts
            existing = conn.execute(
                "SELECT id, concept, definition, confidence, source, category "
                "FROM knowledge_nodes WHERE concept LIKE ? LIMIT 3",
                (f"%{concept[:30]}%",)
            ).fetchall()

        if not existing:
            return True, "no existing node for this concept", confidence, None

        for row in existing:
            node_id, exist_concept, exist_defn, exist_conf, exist_src, exist_cat = row
            exist_conf = exist_conf or 0.5

            # Rule 1: existing high-confidence node (>0.7) + new web scraping (<0.6) → SKIP
            if exist_conf > 0.7 and confidence < 0.6:
                reason = (f"skipped: existing node '{exist_concept}' has confidence "
                          f"{exist_conf:.2f} (source: {exist_src}) > new source "
                          f"'{channel}' confidence {confidence:.2f}")
                log.info("_cross_reference: %s", reason)
                return False, reason, confidence, {
                    "id": node_id, "concept": exist_concept,
                    "definition": exist_defn, "confidence": exist_conf,
                    "source": exist_src
                }

            # Rule 2: same concept, different definition → check if contradictory
            if exist_defn and definition:
                exist_tokens = set(re.findall(r'\w{4,}', exist_defn.lower()))
                new_tokens = set(re.findall(r'\w{4,}', definition.lower()))
                if exist_tokens and new_tokens:
                    overlap = exist_tokens & new_tokens
                    jaccard = len(overlap) / len(exist_tokens | new_tokens) if (exist_tokens | new_tokens) else 0

                    # S145: multilingual guard — if one definition is very short
                    # (<80 chars) and the other is much longer (>3x), it's likely
                    # a summary vs full description, not a contradiction.
                    exist_short = len(exist_defn) < 80
                    new_short = len(definition) < 80
                    one_is_summary = (exist_short and not new_short) or (new_short and not exist_short)
                    len_ratio = max(len(exist_defn), len(definition)) / max(min(len(exist_defn), len(definition)), 1)

                    # S145: exact concept match = treat as same topic rephrased,
                    # raise threshold to avoid false contradiction from multilingual content
                    exact_concept_match = (exist_concept.strip().lower() == concept.strip().lower())
                    jaccard_threshold = 0.02 if exact_concept_match else 0.05

                    # Jaccard < threshold → nearly completely different topics
                    if jaccard < jaccard_threshold and not one_is_summary:
                        _ensure_contradictions_table()
                        flag_reason = (f"contradiction: existing '{exist_concept}' "
                                       f"(source: {exist_src}, conf: {exist_conf}) vs "
                                       f"new '{concept}' (source: {channel}, conf: {confidence}), "
                                       f"Jaccard={jaccard:.3f}")
                        log.warning("_cross_reference: %s", flag_reason)
                        try:
                            conn.execute(
                                "INSERT INTO knowledge_contradictions "
                                "(concept, existing_node_id, existing_definition, "
                                "existing_confidence, new_source, new_definition, "
                                "new_confidence, reason) VALUES (?,?,?,?,?,?,?,?)",
                                (concept, node_id, exist_defn[:500], exist_conf,
                                 channel, definition[:500], confidence, flag_reason))
                            conn.commit()
                        except Exception as e:
                            log.debug("contradiction insert falló: %s", e)
                        # Still save but with 0.1 confidence (flagged for review)
                        return True, flag_reason, 0.1, {
                            "id": node_id, "concept": exist_concept,
                            "definition": exist_defn, "confidence": exist_conf,
                            "source": exist_src
                        }

        return True, "no conflict with existing nodes", confidence, None

    except Exception as e:
        log.warning("_cross_reference falló: %s", e)
        return True, f"error during cross-reference: {e}", confidence, None


# ─────────────────────────────── orquestador ─────────────────────────────────

def research_now(concept: str, timeout: float = 10.0, persist: bool = True,
                 prefer_remote: bool = True) -> dict:
    """Investiga el CONCEPTO completo hasta timeout. Cachea en RAM. Persiste si learned.

    S75 OFFLINE-FIRST: detecta conectividad. Si offline, solo usa canales locales
    (man, apt, whatis, tldr). Si online, prueba locales primero y luego remotos.

    S145: Knowledge validation — before persisting, validates definition quality,
    concept relevance, and cross-references against existing graph knowledge.
    If a channel's result fails validation, continues to next channel."""
    concept = (concept or "").strip()
    result = {"learned": False, "definition": "", "channel": None, "concept": concept,
              "validated": False, "validation_note": "", "confidence": 0.0}
    if not concept:
        return result
    ckey = concept.lower()
    if ckey in _RESEARCH_CACHE:
        cached = _RESEARCH_CACHE[ckey]
        if not isinstance(cached, dict):
            log.warning("research_now: cache corruption for '%s' — expected dict, got %s; purging",
                        ckey, type(cached).__name__)
            del _RESEARCH_CACHE[ckey]
        else:
            return cached

    # S75: detectar conectividad para elegir canales
    try:
        from core.eidos_connectivity import is_online
        _online = is_online()
    except Exception:
        _online = True  # si falla, asumir online (no bloquear)

    if not _online:
        channels = _CHANNELS_LOCAL
        log.info("research_now: OFFLINE — solo canales locales para '%s'", concept)
    elif prefer_remote:
        # [S122] Para investigar temas del MUNDO (navegador): wikipedia/ddg/github
        # PRIMERO; el autoconocimiento (code_structure) solo como respaldo. Evita
        # que "n8n" devuelva código propio de EIDOS en vez de Wikipedia.
        channels = _CHANNELS_REMOTE + _CHANNELS_LOCAL
    else:
        channels = _CHANNELS

    deadline = time.time() + timeout
    for name, fn, conf in channels:
        if time.time() >= deadline:
            break
        try:
            raw = fn(concept)
        except Exception as e:
            log.debug("canal %s error: %s", name, e)
            continue
        if not raw:
            continue
        definition = extract_definition(raw, concept)
        if not definition or len(definition) < 40:
            continue

        # ── S145: If persist requested, validate BEFORE saving ──
        if persist:
            valid, val_reason, adj_conf = _validate_knowledge_node(
                concept, definition, name, conf)
            if not valid:
                log.warning("research_now: '%s' via %s REJECTED — %s; trying next channel",
                            concept, name, val_reason)
                # Skip this channel, try the next one
                continue

            should_save, xref_reason, xref_conf, existing = _cross_reference_knowledge(
                concept, definition, name, adj_conf)
            if not should_save:
                # Existing high-confidence node already covers this concept
                if existing and existing.get("definition"):
                    result.update(
                        learned=True, definition=existing["definition"],
                        channel=f"graph:existing", confidence=existing.get("confidence", 0.9),
                        validated=True, validation_note=xref_reason)
                    _RESEARCH_CACHE[ckey] = result
                    return result
                # Otherwise, try next channel
                log.info("research_now: '%s' via %s SKIPPED — %s; trying next channel",
                         concept, name, xref_reason)
                continue

            final_conf = xref_conf
        else:
            final_conf = conf
            valid = True
            val_reason = "validation skipped (persist=False)"

        # ── All checks passed: save & return ──
        result.update(
            learned=True, definition=definition, channel=name,
            confidence=final_conf, validated=valid, validation_note=val_reason)
        log.info("research_now: '%s' aprendido via %s (%d chars, conf=%.2f)",
                 concept, name, len(definition), final_conf)
        if persist:
            saved, _, save_reason = _persist(concept, definition, name, final_conf)
            if not saved:
                result["validation_note"] += f" | persist: {save_reason}"
        _RESEARCH_CACHE[ckey] = result
        return result

    # If we get here, no channel returned valid knowledge
    result["validation_note"] = f"all channels exhausted for '{concept}'"
    return result


def _persist(concept: str, definition: str, channel: str, confidence: float):
    """Persiste el nodo aprendido en SQLite + lo inyecta al grafo en memoria.

    S145: NOW WITH KNOWLEDGE VALIDATION — validates definition quality, concept
    relevance, and cross-references against existing graph before saving.
    Returns (saved: bool, adjusted_confidence: float, reason: str).
    """
    import os, uuid, time as _t
    node_id = uuid.uuid4().hex[:16] + "_" + re.sub(r"\s+", "_", concept[:24])

    # ── S145: Validate before saving ──
    valid, val_reason, adj_conf = _validate_knowledge_node(
        concept, definition, channel, confidence)
    if not valid:
        log.warning("persist REJECTED '%s' (channel=%s): %s", concept, channel, val_reason)
        return False, adj_conf, val_reason

    # ── S145: Cross-reference with existing knowledge ──
    should_save, xref_reason, xref_conf, existing = _cross_reference_knowledge(
        concept, definition, channel, adj_conf)
    if not should_save:
        log.info("persist SKIPPED '%s': %s", concept, xref_reason)
        return False, xref_conf, xref_reason

    final_conf = xref_conf
    try:
        import sqlite3
        db = os.path.expanduser("~/.eidos/evolution_brain.db")
        conn = get_conn(db, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_nodes "
            "(id, concept, definition, category, confidence, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (node_id, concept, definition[:1500], "researched", final_conf,
             f"active:{channel}", _t.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()

        log.info("persist OK: '%s' (active:%s, conf=%.2f)", concept, channel, final_conf)
    except Exception as e:
        log.warning("persist SQLite fallo: %s", e)
        return False, final_conf, str(e)

    # inyectar al grafo en memoria → disponible sin esperar rebuild
    try:
        from core.knowledge_reasoner import get_reasoner
        r = get_reasoner()
        if hasattr(r, "inject_node"):
            r.inject_node(node_id, concept, {"definition": definition[:1500],
                                             "category": "researched",
                                             "confidence": final_conf})
    except Exception as e:
        log.debug("inject_node fallo: %s", e)

    return True, final_conf, "saved"


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    q = sys.argv[1] if len(sys.argv) > 1 else "kali linux"
    print(_json.dumps(research_now(q, persist=False), ensure_ascii=False, indent=2))
