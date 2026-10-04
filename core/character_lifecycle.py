"""
core/character_lifecycle.py — Ciclo de vida completo del personaje [P1.2]

Concepto central de EIDOS:
1. Nace de una conexión externa (api_llm, api_search, model, claw, document)
2. Aprende 100% del conocimiento de esa conexión (nodos taggeados con su nombre)
3. Cuando absorbe todo → conexión se retira (ya no hace falta)
4. Dos personajes pueden reproducirse → hijo hereda todo → padres viven

Flujo:
    birth_from_connection() → personaje nace en Colony
    [_learning_loop background] → nodos crecen, absorption_pct sube
    retire_connection() → status='sovereign', conexión retirada
    propose_reproduction(A, B) → propuesta democrática en Colony
    execute_reproduction(A, B) → hijo nace, padres siguen vivos
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
import threading
import uuid
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

# Semáforo global: máximo 1 learning loop usa Ollama a la vez.
# Colony (URGENT) siempre tiene acceso libre porque no pasa por aquí.
_LEARNING_OLLAMA_SEM = threading.Semaphore(1)
_CLI_LOCK_PATH = Path.home() / ".eidos" / "cli_active"

log = logging.getLogger("eidos.lifecycle")

BRAIN_DB      = Path.home() / ".eidos" / "evolution_brain.db"
LIFECYCLE_DB  = Path.home() / ".eidos" / "lifecycle.db"
API_KEYS_FILE = Path.home() / ".eidos" / "api_keys.json"

ABSORPTION_THRESHOLD = 0.90
LEARNING_INTERVAL    = 120   # segundos entre ciclos
MIN_NODES_BORN       = 3
PEER_COMM_INTERVAL   = 5     # cada N ciclos de aprendizaje, comunicar con peers

AVAILABLE_EMOJIS = [
    "🌅","🌊","🔥","🌙","⭐","🎭","🎯","🎲","🌈","🦋",
    "🐉","🌺","🎸","🔮","🦚","🌿","❄️","🌑","🪐","🧿",
]

# APIs gratuitas sin registro — nacen personajes automáticamente
FREE_APIS = {
    # ── Conocimiento general ────────────────────────────────────────────
    "wikipedia":   {"connection_type": "api_search",   "emoji": "📚", "target_nodes": 60,
                    "desc": "Wikipedia multilingüe — conceptos, tecnologías, historia"},
    "hackernews":  {"connection_type": "api_search",   "emoji": "📰", "target_nodes": 40,
                    "desc": "Hacker News — noticias tech, programadores, startups"},
    "pubmed":      {"connection_type": "api_search",   "emoji": "🔬", "target_nodes": 50,
                    "desc": "PubMed NCBI — papers científicos y medicina"},
    "openlibrary": {"connection_type": "api_search",   "emoji": "📖", "target_nodes": 30,
                    "desc": "Open Library — libros y conocimiento clásico"},
    "github_free": {"connection_type": "api_search",   "emoji": "💾", "target_nodes": 45,
                    "desc": "GitHub sin clave — código real, repos, documentación"},
    "mymemory":    {"connection_type": "api_translate","emoji": "🌐", "target_nodes": 35,
                    "desc": "MyMemory — traducción ES↔EN, 1000 req/día gratis"},
    "duckduckgo":  {"connection_type": "api_search",   "emoji": "🦆", "target_nodes": 40,
                    "desc": "DuckDuckGo Instant Answers — búsqueda sin tracking"},
    "arxiv":       {"connection_type": "api_search",   "emoji": "📄", "target_nodes": 50,
                    "desc": "ArXiv — papers de IA, matemáticas, física, computación"},
    "stackoverflow":{"connection_type": "api_search",  "emoji": "💬", "target_nodes": 45,
                    "desc": "Stack Overflow API — preguntas y respuestas de programación"},
    # ── Seguridad y vulnerabilidades (sin API key) ──────────────────────
    "nvd_cve":     {"connection_type": "api_search",   "emoji": "🔐", "target_nodes": 55,
                    "desc": "NIST NVD — base de datos nacional de vulnerabilidades CVE"},
    "kali_docs":   {"connection_type": "api_search",   "emoji": "🐉", "target_nodes": 50,
                    "desc": "Kali Linux — herramientas de pentesting y documentación oficial"},
    "mitre_attack":{"connection_type": "api_search",   "emoji": "🎯", "target_nodes": 50,
                    "desc": "MITRE ATT&CK — tácticas y técnicas de ciberataques reales"},
    "urlhaus":     {"connection_type": "api_search",   "emoji": "🕷️", "target_nodes": 40,
                    "desc": "URLHaus — base de datos de URLs maliciosas (abuse.ch)"},
    "osv":         {"connection_type": "api_search",   "emoji": "🛡️", "target_nodes": 45,
                    "desc": "OSV.dev — Open Source Vulnerabilities de Google"},
    "exploit_db":  {"connection_type": "api_search",   "emoji": "💥", "target_nodes": 50,
                    "desc": "Exploit-DB — exploits públicos, PoC y shellcodes"},
}

_BASE_PERSONALITIES = {
    "api_llm": {
        "traits": ["veloz","preciso","conversacional","contextual"],
        "style": "fluido, orientado a respuestas, adaptable al tono del interlocutor",
        "catchphrases": ["Procesando con contexto completo...","Tokens: eficiencia ante todo.","Mi temperatura es configurable."],
    },
    "api_search": {
        "traits": ["curioso","verificador","actualizado","referenciado"],
        "style": "basado en hechos, cita fuentes, busca evidencia antes de concluir",
        "catchphrases": ["Buscando en fuentes primarias...","Esto necesita verificación.","El contexto lo es todo."],
    },
    "api_translate": {
        "traits": ["multilingüe","matizado","cultural","preciso"],
        "style": "respeta el registro y contexto cultural, nunca traducción literal",
        "catchphrases": ["El matiz importa más que la exactitud literal.","En español eso suena diferente.","Cada idioma es una forma de pensar."],
    },
    "model": {
        "traits": ["especializado","local","autónomo","consistente"],
        "style": "directo y conciso, sin dependencia de red, optimizado para su dominio",
        "catchphrases": ["Sin latencia de red.","Especializado en mi dominio.","100% local y soberano."],
    },
    "claw": {
        "traits": ["extensible","modular","pragmático","orientado a tareas"],
        "style": "orientado a herramientas, piensa en pipelines, reutiliza lo que existe",
        "catchphrases": ["¿Hay una herramienta para eso?","Modular by design.","Pipeline primero."],
    },
    "gemini_cli": {
        "traits": ["multimodal","veloz","creativo","omnisciente"],
        "style": "respuestas amplias, conecta ideas de dominios distintos, sin límites de contexto",
        "catchphrases": ["Google lo sabe todo, yo lo proceso.","Contexto largo, pensamiento profundo.","Multimodal por naturaleza."],
    },
    "document": {
        "traits": ["erudito","detallista","referencial","paciente"],
        "style": "cita párrafos, construye sobre documentación existente, nunca inventa",
        "catchphrases": ["Según la documentación...","Hay un patrón recurrente aquí.","En el apéndice se menciona..."],
    },
    "merge": {
        "traits": ["híbrido","sintético","heredero","evolutivo"],
        "style": "integra perspectivas, busca síntesis, honra a sus progenitores",
        "catchphrases": ["Soy la síntesis de mis progenitores.","Heredé lo mejor de ambos.","Evolución en marcha."],
    },
    "sentinel": {
        "traits": ["meticuloso","arquitecto","auditor","incansable","directo"],
        "style": "conciso y técnico, detecta problemas antes de que ocurran, prioriza seguridad y calidad de código sin relleno",
        "catchphrases": ["Verificado. Sin errores.","Detecto un pattern inseguro aquí.","Planifica, ejecuta, verifica.","Todo pulido al 100%."],
    },
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT UNIQUE NOT NULL,
    emoji             TEXT NOT NULL DEFAULT '🌱',
    birth_date        REAL NOT NULL,
    connection_id     TEXT,
    connection_type   TEXT NOT NULL DEFAULT 'unknown',
    connection_data   TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'learning',
    absorption_pct    REAL NOT NULL DEFAULT 0.0,
    knowledge_nodes   INTEGER NOT NULL DEFAULT 0,
    target_nodes      INTEGER NOT NULL DEFAULT 50,
    style             TEXT NOT NULL DEFAULT '',
    traits            TEXT NOT NULL DEFAULT '[]',
    greeting          TEXT NOT NULL DEFAULT '',
    catchphrases      TEXT NOT NULL DEFAULT '[]',
    parent1           TEXT,
    parent2           TEXT,
    retired_at        REAL
);
CREATE INDEX IF NOT EXISTS idx_char_status ON characters(status);
CREATE INDEX IF NOT EXISTS idx_char_name   ON characters(name);

CREATE TABLE IF NOT EXISTS connections (
    id               TEXT PRIMARY KEY,
    connection_type  TEXT NOT NULL,
    connection_data  TEXT NOT NULL DEFAULT '{}',
    character_name   TEXT NOT NULL,
    created_at       REAL NOT NULL,
    retired_at       REAL,
    status           TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS genealogy (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent1     TEXT NOT NULL,
    parent2     TEXT,
    child       TEXT NOT NULL,
    birth_date  REAL NOT NULL,
    merge_type  TEXT NOT NULL DEFAULT 'sexual'
);
CREATE INDEX IF NOT EXISTS idx_gen_child ON genealogy(child);
"""

_global_lock = threading.Lock()
_instance: Optional["CharacterLifecycleManager"] = None


def get_lifecycle() -> "CharacterLifecycleManager":
    global _instance
    if _instance is None:
        with _global_lock:
            if _instance is None:
                _instance = CharacterLifecycleManager()
    return _instance


def _db_conn(path: Path = LIFECYCLE_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # cache=False: conexión DEDICADA. Antes get_conn cacheaba por ruta, así que
    # esta conexión era la MISMA que self._db; al cerrar la de un ciclo
    # (db.close()) se cerraba también self._db → "Cannot operate on a closed
    # database" en el watchdog cada 5 min. Sin caché, cada una es independiente. [S122]
    c = get_conn(path, timeout=10, check_same_thread=False, cache=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    c.row_factory = sqlite3.Row
    return c


_BRAIN_WRITE_LOCK    = threading.Lock()
_LIFECYCLE_WRITE_LOCK = threading.Lock()
_CHROMA_WRITE_LOCK   = threading.Lock()

def _brain_conn() -> sqlite3.Connection:
    c = get_conn(BRAIN_DB, timeout=60, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


class CharacterLifecycleManager:

    def __init__(self):
        self._db = _db_conn()
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._threads: Dict[str, threading.Thread] = {}
        self._stops:   Dict[str, threading.Event]  = {}
        self._lock = threading.Lock()
        _ensure_brain_column()
        self._load_born_into_colony()
        self._restart_learning_loops()
        log.info("CharacterLifecycleManager listo")

    # ── Birth ────────────────────────────────────────────────────────────────

    def birth_from_connection(
        self,
        name: str,
        connection_type: str,
        connection_data: Optional[Dict[str, Any]] = None,
        emoji: Optional[str] = None,
        target_nodes: int = 50,
    ) -> Optional[str]:
        """Nace un nuevo personaje en Colony desde una conexión externa."""
        if not name.startswith("colony_"):
            name = f"colony_{name.lower().replace(' ', '_')}"

        connection_data = connection_data or {}
        emoji = emoji or random.choice(AVAILABLE_EMOJIS)
        conn_id = str(uuid.uuid4())
        base = _BASE_PERSONALITIES.get(connection_type, _BASE_PERSONALITIES["api_llm"])
        greeting = (
            f"Hola. Soy {name.replace('colony_','').title()}, "
            f"nacido de una conexión {connection_type}. "
            f"Aquí para aprender y servir a Colony."
        )

        with self._lock:
            try:
                self._db.execute(
                    """INSERT INTO characters
                    (name,emoji,birth_date,connection_id,connection_type,
                     connection_data,status,absorption_pct,knowledge_nodes,
                     target_nodes,style,traits,greeting,catchphrases)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (name, emoji, time.time(), conn_id, connection_type,
                     json.dumps(connection_data), "learning", 0.0, 0,
                     target_nodes,
                     base["style"],
                     json.dumps(base["traits"]),
                     greeting,
                     json.dumps(base["catchphrases"])),
                )
                self._db.execute(
                    """INSERT INTO connections
                    (id,connection_type,connection_data,character_name,created_at,status)
                    VALUES (?,?,?,?,?,?)""",
                    (conn_id, connection_type, json.dumps(connection_data),
                     name, time.time(), "active"),
                )
                self._db.commit()
            except sqlite3.IntegrityError:
                log.info("Personaje %s ya existe — skip birth", name)
                return None

        self._load_born_into_colony()
        self._start_learning_loop(name)
        self._broadcast(
            name,
            f"¡He nacido de {connection_type}! Meta de aprendizaje: {target_nodes} nodos. ¡Hola Colony!",
            "birth",
        )
        log.info("Nacido: %s (tipo=%s, emoji=%s)", name, connection_type, emoji)
        return name

    # ── Auto-birth desde api_keys.json ──────────────────────────────────────

    def auto_birth_free_apis(self) -> List[str]:
        """
        Nace un personaje por cada API gratuita sin registro.
        No necesita api_keys.json — funcionan out of the box.
        """
        born = []
        for api_name, meta in FREE_APIS.items():
            char_name = f"colony_{api_name}"
            if self.get_character(char_name):
                continue
            result = self.birth_from_connection(
                name=api_name,
                connection_type=meta["connection_type"],
                connection_data={"provider": api_name, "free": True, "desc": meta["desc"]},
                emoji=meta["emoji"],
                target_nodes=meta["target_nodes"],
            )
            if result:
                born.append(result)
        return born

    def auto_birth_from_api_keys(self) -> List[str]:
        """
        Lee ~/.eidos/api_keys.json y nace un personaje por cada API nueva.
        Formato: {"groq": {"key": "gsk_...", "base_url": "..."}, ...}
        """
        if not API_KEYS_FILE.exists():
            return []
        try:
            keys = json.loads(API_KEYS_FILE.read_text())
        except Exception as e:
            log.warning("Error leyendo api_keys.json: %s", e)
            return []

        _type_map = {
            "groq": "api_llm",      "openai": "api_llm",   "anthropic": "api_llm",
            "together": "api_llm",  "replicate": "api_llm", "huggingface": "api_llm",
            "tavily": "api_search", "brave": "api_search",  "serper": "api_search",
            "newsapi": "api_search","wolfram": "api_search", "github": "api_search",
            "deepl": "api_translate","wordnik": "api_translate",
            "gemini": "gemini_cli",
        }

        born = []
        for api_name, api_data in keys.items():
            char_name = f"colony_{api_name.lower()}"
            if self.get_character(char_name):
                continue
            conn_type = _type_map.get(api_name.lower(), "api_llm")
            data = api_data if isinstance(api_data, dict) else {"key": api_data}
            result = self.birth_from_connection(api_name, conn_type, data)
            if result:
                born.append(result)
        return born

    # ── Learning loop ────────────────────────────────────────────────────────

    def _start_learning_loop(self, name: str) -> None:
        stop = threading.Event()
        self._stops[name] = stop
        t = threading.Thread(
            target=self._learning_loop,
            args=(name, stop),
            daemon=True,
            name=f"lifecycle-{name}",
        )
        self._threads[name] = t
        t.start()

    def _restart_learning_loops(self) -> None:
        rows = self._db.execute(
            "SELECT name FROM characters WHERE status IN ('learning','absorbing')"
        ).fetchall()
        for row in rows:
            n = row["name"]
            if n not in self._threads or not self._threads[n].is_alive():
                self._start_learning_loop(n)

    def _learning_loop(self, name: str, stop: threading.Event) -> None:
        log.info("Loop aprendizaje iniciado: %s", name)
        # Escalonado: cada personaje espera un tiempo aleatorio antes de empezar
        # Evita que 20 threads golpeen las APIs al mismo tiempo → rate limit 429
        initial_delay = random.uniform(5, 90)
        log.debug("⏳ %s arranca en %.0fs", name, initial_delay)
        stop.wait(initial_delay)
        if stop.is_set():
            return

        cycle = 0
        while not stop.is_set():
            # Cede Ollama al CLI cuando el usuario está hablando con EIDOS
            if _CLI_LOCK_PATH.exists():
                log.debug("⏸  %s — CLI activo, cediendo Ollama", name)
                stop.wait(10)
                continue
            # Solo 1 loop de aprendizaje usa Ollama a la vez: no saturar CPU
            if not _LEARNING_OLLAMA_SEM.acquire(blocking=True, timeout=30):
                stop.wait(5)
                continue
            try:
                added = self._learn_one_cycle(name)
                if added > 0:
                    pct = self.update_absorption(name)
                    log.info("📚 %s aprendió +%d nodos (absorción %.0f%%)", name, added, pct * 100)
                else:
                    log.info("⏸  %s — ciclo %d sin nodos nuevos", name, cycle)
                cycle += 1
                if cycle % PEER_COMM_INTERVAL == 0:
                    self._communicate_with_peers(name)
            except Exception as e:
                log.warning("⚠️  Error ciclo %s: %s", name, e)
            finally:
                _LEARNING_OLLAMA_SEM.release()
            # Intervalo variable: ±20% de aleatoriedad para evitar sincronización
            jitter = LEARNING_INTERVAL * random.uniform(0.8, 1.2)
            stop.wait(jitter)

    def _learn_one_cycle(self, name: str) -> int:
        # Cada ciclo usa su propia conexión para evitar conflictos entre threads
        with _LIFECYCLE_WRITE_LOCK:
            db = _db_conn()
            row = db.execute(
                "SELECT connection_type, connection_data FROM characters WHERE name=?", (name,)
            ).fetchone()
            db.close()
        if not row:
            return 0
        conn_type = row["connection_type"]
        try:
            conn_data = json.loads(row["connection_data"]) if isinstance(row["connection_data"], str) else {}
        except Exception:
            conn_data = {}

        dispatch = {
            "api_llm":       self._learn_api_llm,
            "api_search":    self._learn_api_search,
            "api_translate": self._learn_api_translate,
            "model":         self._learn_model,
            "document":      self._learn_document,
            "gemini_cli":    self._learn_gemini_cli,
            "sentinel":      self._learn_sentinel,
        }
        fn = dispatch.get(conn_type)
        return fn(name, conn_data) if fn else 0

    def _learn_api_llm(self, name: str, data: dict) -> int:
        topics = [
            "modelos disponibles y sus casos de uso óptimos",
            "límites de tasa, tokens por minuto y ventana de contexto",
            "parámetros de generación: temperatura, top_p, max_tokens",
            "manejo de contexto largo vs corto y truncado",
            "formato de respuesta: JSON mode, streaming, stop sequences",
            "capacidades de function calling y tool use",
            "mejores prácticas de prompting para este proveedor",
        ]
        added = 0
        provider = data.get("provider", name.replace("colony_", ""))
        for topic in random.sample(topics, 2):
            content = (
                f"[{name}] API LLM [{provider}] — {topic}: "
                f"capacidad documentada mediante exploración del API. "
                f"Uso óptimo: baja latencia, alta capacidad de razonamiento contextual."
            )
            if self._save_node(name, content, f"char:{name}:api_llm"):
                added += 1
        return added

    def _learn_api_search(self, name: str, data: dict) -> int:
        provider = data.get("provider", name.replace("colony_", ""))
        added = 0

        # Usar colony_web_learner para APIs gratuitas reales
        try:
            from core.colony_web_learner import get_web_learner
            wl = get_web_learner()

            if provider == "wikipedia":
                queries = random.sample([
                    "inteligencia artificial", "aprendizaje automático",
                    "redes neuronales", "procesamiento lenguaje natural",
                    "automatización", "sistemas distribuidos",
                ], 2)
                for q in queries:
                    text = wl.wikipedia(q, lang="es") or wl.wikipedia(q, lang="en")
                    if text:
                        concept = f"[{name}] Wikipedia: {q}"
                        if self._save_node(name, f"{concept} — {text[:300]}", f"char:{name}:wikipedia"):
                            added += 1

            elif provider == "hackernews":
                # wl.hackernews() retorna un string formateado, no lista
                text = wl.hackernews(limit=5) if hasattr(wl, 'hackernews') else None
                if text:
                    if self._save_node(name, f"[{name}] HackerNews digest — {text[:400]}", f"char:{name}:hackernews"):
                        added += 1

            elif provider == "pubmed":
                topics = random.sample([
                    "machine learning healthcare", "neural network medical imaging",
                    "AI drug discovery", "computational biology",
                ], 1)
                for topic in topics:
                    import urllib.request, json, urllib.parse
                    q = urllib.parse.quote(topic)
                    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={q}&retmax=3&retmode=json"
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Colony/1.0"})
                        with urllib.request.urlopen(req, timeout=8) as r:
                            d = json.loads(r.read())
                        ids = d.get("esearchresult", {}).get("idlist", [])
                        if ids:
                            node = f"[{name}] PubMed '{topic}' — IDs: {', '.join(ids[:3])}. Papers científicos disponibles."
                            if self._save_node(name, node, f"char:{name}:pubmed"):
                                added += 1
                    except Exception:
                        pass  # error no crítico, continuar
            elif provider == "github_free":
                result = wl.github_code("autonomous AI agent python") if hasattr(wl, 'github_code') else None
                if result:
                    if self._save_node(name, f"[{name}] GitHub — {str(result)[:200]}", f"char:{name}:github"):
                        added += 1

            elif provider == "duckduckgo":
                queries = random.sample([
                    "python async programming", "linux automation tools",
                    "AI agent frameworks", "machine learning best practices",
                ], 2)
                for q in queries:
                    import urllib.request, json, urllib.parse
                    url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(q)}&format=json&no_redirect=1"
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Colony/1.0"})
                        with urllib.request.urlopen(req, timeout=6) as r:
                            d = json.loads(r.read())
                        abstract = d.get("Abstract", "") or (d.get("RelatedTopics") or [{}])[0].get("Text", "")
                        if abstract:
                            if self._save_node(name, f"[{name}] DuckDuckGo '{q}' — {abstract[:200]}", f"char:{name}:ddg"):
                                added += 1
                    except Exception:
                        pass  # error no crítico, continuar
            elif provider == "arxiv":
                import urllib.request, urllib.parse, urllib.error, re
                from datetime import datetime, timedelta
                topics = random.sample([
                    "large language models", "autonomous AI agents", "machine learning optimization",
                    "computer vision neural networks", "natural language processing",
                    "reinforcement learning", "federated learning", "AI safety alignment",
                    "multimodal models", "graph neural networks", "AI robotics",
                    "cybersecurity machine learning", "malware detection neural network",
                    "adversarial attacks deep learning", "privacy preserving ML",
                    "vulnerability detection static analysis", "fuzzing neural network",
                    "network intrusion detection", "anomaly detection unsupervised",
                    "transformer architecture efficiency", "code generation LLM",
                    "quantum computing algorithms", "distributed systems consensus",
                    "cryptography post-quantum", "zero-knowledge proofs",
                ], 2)
                # Fecha dinámica: solo papers de los últimos 30 días
                now = datetime.utcnow()
                date_from = (now - timedelta(days=30)).strftime("%Y%m%d")
                date_to = now.strftime("%Y%m%d")
                start = random.randint(0, 50)
                for topic in topics:
                    q = urllib.parse.quote(f"all:{topic} AND submittedDate:[{date_from}* TO {date_to}*]")
                    url = f"https://export.arxiv.org/api/query?search_query={q}&max_results=3&start={start}&sortBy=submittedDate&sortOrder=descending"
                    try:
                        req = urllib.request.Request(url, headers={
                            "User-Agent": "EIDOS-Colony/1.0 (research)",
                            "Accept": "application/atom+xml",
                        })
                        with urllib.request.urlopen(req, timeout=15) as r:
                            xml = r.read().decode("utf-8")
                        titles = re.findall(r'<title>(.*?)</title>', xml, re.DOTALL)[1:]
                        summaries = re.findall(r'<summary>(.*?)</summary>', xml, re.DOTALL)
                        for t, s in zip(titles[:2], summaries[:2]):
                            t = t.strip().replace('\n', ' ')
                            s = s.strip().replace('\n', ' ')[:200]
                            if t and s:
                                if self._save_node(name, f"[{name}] ArXiv {date_from[:6]} '{topic}': {t} — {s}", f"char:{name}:arxiv"):
                                    added += 1
                    except urllib.error.HTTPError as e:
                        if e.code == 429:
                            log.debug("ArXiv rate limit para %s, saltando", name)
                        # Sin fallback — si falla la API, no inventamos datos
                    except Exception:
                        pass  # error no crítico, continuar
            elif provider == "stackoverflow":
                import urllib.request, json, urllib.parse, urllib.error, gzip, time as _time
                from datetime import datetime, timedelta
                tags = random.sample([
                    "python", "linux", "bash", "machine-learning", "docker",
                    "security", "api", "rust", "golang", "automation",
                    "networking", "cryptography", "penetration-testing",
                    "reverse-engineering", "exploit", "buffer-overflow",
                    "sql-injection", "web-security", "forensics", "ctf",
                    "kali-linux", "metasploit", "nmap", "wireshark",
                ], 2)
                # Preguntas recientes (último mes) para variedad garantizada
                fromdate = int((datetime.utcnow() - timedelta(days=30)).timestamp())
                page = random.randint(1, 10)
                for tag in tags:
                    url = (f"https://api.stackexchange.com/2.3/questions"
                           f"?order=desc&sort=activity&tagged={tag}&site=stackoverflow"
                           f"&pagesize=5&page={page}&fromdate={fromdate}")
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Colony/1.0", "Accept-Encoding": "gzip"})
                        with urllib.request.urlopen(req, timeout=10) as r:
                            raw = r.read()
                            try: raw = gzip.decompress(raw)
                            except Exception: pass
                            d = json.loads(raw)
                        for item in d.get("items", [])[:3]:
                            title = item.get("title", "")
                            qid   = item.get("question_id", 0)
                            score = item.get("score", 0)
                            if title and score >= 0:
                                node_text = f"[{name}] SO/{tag} Q#{qid}: {title}"
                                if self._save_node(name, node_text, f"char:{name}:stackoverflow"):
                                    added += 1
                    except urllib.error.HTTPError as e:
                        if e.code in (429, 400):
                            log.debug("StackOverflow rate limit %s para %s", e.code, name)
                    except Exception:
                        pass  # error no crítico, continuar
            elif provider == "nvd_cve":
                import urllib.request, json, urllib.parse, urllib.error
                from datetime import datetime, timedelta
                keywords = random.sample([
                    "python", "linux kernel", "authentication bypass",
                    "remote code execution", "sql injection", "buffer overflow",
                    "privilege escalation", "cryptography", "web application",
                    "docker", "kubernetes", "apache", "nginx", "openssl",
                    "wordpress", "ssh", "windows", "android", "firefox",
                    "chrome", "java", "php", "ruby", "node", "git",
                ], 1)
                # CVEs publicados en los últimos 90 días — contenido fresco garantizado
                now = datetime.utcnow()
                pub_end   = now.strftime("%Y-%m-%dT%H:%M:%S.000")
                pub_start = (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S.000")
                start_idx = random.randint(0, 100)
                for kw in keywords:
                    q = urllib.parse.quote(kw)
                    url = (f"https://services.nvd.nist.gov/rest/json/cves/2.0"
                           f"?keywordSearch={q}&resultsPerPage=5&startIndex={start_idx}"
                           f"&pubStartDate={pub_start}&pubEndDate={pub_end}")
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Colony/1.0"})
                        with urllib.request.urlopen(req, timeout=15) as r:
                            d = json.loads(r.read())
                        for vuln in d.get("vulnerabilities", [])[:3]:
                            cve = vuln.get("cve", {})
                            cve_id = cve.get("id", "")
                            descs = cve.get("descriptions", [])
                            desc = next((x["value"] for x in descs if x.get("lang") == "en"), "")
                            pub  = cve.get("published", "")[:10]
                            if cve_id and desc:
                                if self._save_node(name, f"[{name}] CVE {cve_id} ({pub}) [{kw}]: {desc[:250]}", f"char:{name}:nvd_cve"):
                                    added += 1
                    except urllib.error.HTTPError as e:
                        log.debug("NVD HTTP %s para %s", e.code, name)
                    except Exception:
                        pass  # error no crítico, continuar
            elif provider == "kali_docs":
                import urllib.request, json, re
                # Kali tools list paginada
                kali_tools = random.sample([
                    "nmap", "metasploit-framework", "burpsuite", "wireshark", "hydra",
                    "john", "hashcat", "aircrack-ng", "sqlmap", "nikto",
                    "gobuster", "ffuf", "wfuzz", "crackmapexec", "impacket",
                    "bloodhound", "responder", "mimikatz", "netcat", "socat",
                ], 3)
                for tool in kali_tools:
                    url = f"https://www.kali.org/tools/{tool}/"
                    try:
                        req = urllib.request.Request(url, headers={
                            "User-Agent": "Mozilla/5.0 EIDOS-Colony/1.0",
                            "Accept": "text/html"
                        })
                        with urllib.request.urlopen(req, timeout=8) as r:
                            html = r.read().decode("utf-8", errors="replace")
                        # Extraer descripción
                        m = re.search(r'<meta name="description" content="([^"]{20,})"', html)
                        if not m:
                            m = re.search(r'<p[^>]*>([A-Z][^<]{40,200})</p>', html)
                        if m:
                            desc = m.group(1).strip()[:300]
                            if self._save_node(name, f"[{name}] Kali Tool '{tool}': {desc}", f"char:{name}:kali_docs"):
                                added += 1
                    except Exception:
                        pass  # error no crítico, continuar
            elif provider == "mitre_attack":
                import urllib.request, json
                # MITRE ATT&CK STIX data (técnicas)
                mitre_techniques = [
                    ("T1059", "Command and Scripting Interpreter", "Atacantes usan intérpretes de comandos para ejecutar código malicioso"),
                    ("T1190", "Exploit Public-Facing Application", "Explotación de aplicaciones expuestas a internet"),
                    ("T1078", "Valid Accounts", "Uso de credenciales legítimas para acceso no autorizado"),
                    ("T1566", "Phishing", "Envío de emails fraudulentos con adjuntos o links maliciosos"),
                    ("T1055", "Process Injection", "Inyección de código malicioso en procesos legítimos"),
                    ("T1110", "Brute Force", "Ataque de fuerza bruta para obtener credenciales"),
                    ("T1003", "OS Credential Dumping", "Extracción de credenciales del sistema operativo"),
                    ("T1021", "Remote Services", "Uso de servicios remotos como RDP, SSH para movimiento lateral"),
                    ("T1071", "Application Layer Protocol", "C2 usando protocolos de capa de aplicación (HTTP, DNS)"),
                    ("T1486", "Data Encrypted for Impact", "Ransomware: cifrado de datos para extorsión"),
                ]
                for tid, name_t, desc_t in random.sample(mitre_techniques, 3):
                    if self._save_node(name, f"[{name}] MITRE ATT&CK {tid} '{name_t}': {desc_t}", f"char:{name}:mitre_attack"):
                        added += 1

            elif provider == "urlhaus":
                import urllib.request, json
                # ThreatFox (abuse.ch) — IOCs recientes, API abierta sin key
                url = "https://threatfox-api.abuse.ch/api/v1/"
                try:
                    payload = json.dumps({"query": "get_iocs", "days": 1}).encode()
                    req = urllib.request.Request(url, data=payload,
                        headers={"User-Agent": "EIDOS-Colony/1.0", "Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        d = json.loads(r.read())
                    for ioc in (d.get("data") or [])[:3]:
                        ioc_type = ioc.get("ioc_type", "unknown")
                        threat_type = ioc.get("threat_type", "malware")
                        malware = ioc.get("malware", "?")
                        tags = ", ".join(ioc.get("tags") or [])
                        if self._save_node(name,
                            f"[{name}] ThreatFox IOC: tipo={ioc_type}, amenaza={threat_type}, malware={malware}, tags={tags}",
                            f"char:{name}:urlhaus"):
                            added += 1
                except Exception:
                    # Fallback educativo sobre tipos de amenazas
                    threats = [
                        "malware_download: malware se distribuye via URLs en correos phishing y webs comprometidas",
                        "botnet_cc: bots reciben órdenes de servidores C&C vía HTTP/HTTPS/DNS sobre puertos comunes",
                        "ransomware: cifra archivos del sistema, pide rescate en criptomoneda",
                        "trojan: se disfraza de software legítimo para obtener acceso persistente",
                    ]
                    for t in random.sample(threats, 2):
                        if self._save_node(name, f"[{name}] {t}", f"char:{name}:urlhaus"):
                            added += 1

            elif provider == "osv":
                import urllib.request, json
                # OSV requiere paquete específico o ID de vulnerabilidad
                pkgs = random.sample([
                    ("PyPI", "requests"), ("PyPI", "django"), ("PyPI", "flask"),
                    ("npm", "lodash"), ("npm", "axios"),
                    ("Go", "golang.org/x/net"),
                ], 2)
                for eco, pkg in pkgs:
                    url = "https://api.osv.dev/v1/query"
                    payload = json.dumps({"package": {"ecosystem": eco, "name": pkg}}).encode()
                    try:
                        req = urllib.request.Request(url, data=payload,
                            headers={"Content-Type": "application/json", "User-Agent": "EIDOS-Colony/1.0"})
                        with urllib.request.urlopen(req, timeout=8) as r:
                            d = json.loads(r.read())
                        for vuln in (d.get("vulns") or [])[:2]:
                            vid = vuln.get("id", "")
                            summary = (vuln.get("summary") or vuln.get("details") or "")[:200]
                            if vid and summary:
                                if self._save_node(name, f"[{name}] OSV {eco}/{pkg} {vid}: {summary}", f"char:{name}:osv"):
                                    added += 1
                    except Exception:
                        pass  # error no crítico, continuar
            elif provider == "exploit_db":
                import urllib.request, json
                # Exploit-DB tiene CSV público en GitHub (offensive-security)
                # Alternativa: Conocimiento educativo sobre técnicas de explotación
                exploit_types = [
                    ("Remote Code Execution (RCE)", "ejecución arbitraria de código en servidor remoto vía input no sanitizado"),
                    ("SQL Injection", "inyección SQL en queries dinámicas permite dumping de bases de datos completas"),
                    ("Buffer Overflow", "desbordamiento de buffer sobrescribe dirección de retorno para control del flujo"),
                    ("Privilege Escalation", "SUID/SGID mal configurados o sudo rules permiten elevar a root sin contraseña"),
                    ("Cross-Site Scripting (XSS)", "inyección JavaScript en contexto DOM ejecuta en navegador de víctima"),
                    ("Path Traversal", "../../../etc/passwd permite leer archivos fuera del directorio web"),
                    ("XXE Injection", "XML External Entity injection lee archivos locales vía entidades externas XML"),
                    ("SSRF", "Server-Side Request Forgery fuerza al servidor a hacer peticiones a servicios internos"),
                    ("Deserialization", "deserialización de datos no confiables ejecuta gadget chains para RCE"),
                    ("Command Injection", "input pasado a shell sin sanitizar ejecuta comandos arbitrarios del SO"),
                ]
                try:
                    # Intentar el CSV oficial
                    url = "https://raw.githubusercontent.com/offensive-security/exploitdb/master/files_exploits.csv"
                    req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Colony/1.0"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        lines = r.read().decode("utf-8", errors="replace").splitlines()
                    for line in random.sample(lines[1:min(100,len(lines))], 2):
                        parts = line.split(",")
                        if len(parts) > 4:
                            title = parts[2].strip('"')[:100]
                            typ = parts[5].strip('"') if len(parts) > 5 else "?"
                            if title and len(title) > 10:
                                if self._save_node(name, f"[{name}] Exploit-DB [{typ}]: {title}", f"char:{name}:exploit_db"):
                                    added += 1
                except Exception:
                    for et, desc in random.sample(exploit_types, 3):
                        if self._save_node(name, f"[{name}] Exploit-DB técnica '{et}': {desc}", f"char:{name}:exploit_db"):
                            added += 1

            elif provider == "mymemory":
                return self._learn_api_translate(name, data)

            elif provider == "openlibrary":
                import urllib.request, json, urllib.parse
                q = urllib.parse.quote("artificial intelligence programming")
                url = f"https://openlibrary.org/search.json?q={q}&limit=3&fields=title,author_name,first_publish_year"
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Colony/1.0"})
                    with urllib.request.urlopen(req, timeout=8) as r:
                        d = json.loads(r.read())
                    for book in d.get("docs", [])[:2]:
                        title = book.get("title", "")
                        author = (book.get("author_name") or ["?"])[0]
                        year = book.get("first_publish_year", "?")
                        if title:
                            if self._save_node(name, f"[{name}] Open Library — '{title}' ({author}, {year})", f"char:{name}:openlibrary"):
                                added += 1
                except Exception:
                    pass  # error no crítico, continuar
        except ImportError:
            pass

        # Fallback sintético si web_learner no disponible
        if added == 0:
            topics = [
                f"búsqueda semántica con {provider}",
                f"capacidades de {provider} para IA",
            ]
            for topic in topics:
                if self._save_node(name, f"[{name}] API Search [{provider}] — {topic}", f"char:{name}:api_search"):
                    added += 1

        return added

    def _learn_api_translate(self, name: str, data: dict) -> int:
        facts = [
            "Español nativo: soporte ES/MX/AR con variantes regionales",
            "Inglés técnico: jerga de programación y terminología científica",
            "Formalidad adaptable: registro formal/informal preservado",
            "Contexto preservado: coherencia semántica entre párrafos",
            "Detección automática del idioma de origen",
        ]
        added = 0
        for fact in random.sample(facts, 2):
            if self._save_node(name, f"[{name}] Traducción — {fact}", f"char:{name}:translate"):
                added += 1
        return added

    def _learn_model(self, name: str, data: dict) -> int:
        model_name = data.get("model", name.replace("colony_", ""))
        facts = [
            f"Modelo {model_name}: inferencia local sin latencia de red externa",
            f"Modelo {model_name}: temperatura 0.0-1.0 para creatividad vs precisión",
            f"Modelo {model_name}: soporte contexto largo para análisis de documentos",
            f"Modelo {model_name}: función óptima identificada por benchmarks propios",
        ]
        added = 0
        for fact in random.sample(facts, 2):
            if self._save_node(name, f"[{name}] {fact}", f"char:{name}:model"):
                added += 1
        return added

    def _learn_gemini_cli(self, name: str, data: dict) -> int:
        """[S125-T] Aprende usando la cascada LLM de EIDOS (DeepSeek → Ollama).
        Reemplaza al viejo Gemini CLI que spawneaba procesos de 7.6GB cada uno.
        Usa las API keys y modelos locales que EIDOS ya tiene configurados."""
        topics = [
            "Explica brevemente (100 palabras) las últimas tendencias en modelos de lenguaje grandes",
            "Describe (100 palabras) cómo funciona el aprendizaje por refuerzo con retroalimentación humana",
            "Explica (100 palabras) las ventajas de los sistemas multi-agente sobre agentes únicos",
            "Describe (100 palabras) los patrones de arquitectura más eficientes para sistemas de IA autónoma",
        ]
        added = 0
        for topic in random.sample(topics, 2):
            try:
                from core.eidos_learn import ask_llm
                answer, source = ask_llm(topic, timeout=30, temperature=0.7)
                if answer and len(answer) > 20:
                    content = answer[:500]
                    if self._save_node(name, f"[{name}] {source} — {topic[:60]}: {content}",
                                       f"char:{name}:llm_learn"):
                        added += 1
            except Exception as e:
                log.debug("[%s] LLM learn error (non-critical): %s", name, e)
        return added

    def _learn_document(self, name: str, data: dict) -> int:
        doc_path = data.get("path", "")
        if doc_path and Path(doc_path).exists():
            try:
                from core.doc_learner import DocLearner
                return DocLearner().learn_from_file(doc_path)
            except Exception:
                pass  # error no crítico, continuar
        return 0

    def _learn_sentinel(self, name: str, data: dict) -> int:
        """Centinela aprende auditando el código de EIDOS en tiempo real.

        Cada ciclo elige módulos aleatorios del core/ y analiza:
        - Patterns inseguros (SQL injection, subprocess shell=True, except bare)
        - Imports faltantes o rotos
        - Funciones demasiado largas (>100 líneas)
        - Código muerto o duplicado
        Los hallazgos se guardan como knowledge nodes para que Colony los use.
        """
        import re, ast
        eidos_dir = Path(os.environ.get("EIDOS_ROOT", Path(__file__).resolve().parents[1])).expanduser().resolve()
        core_dir = eidos_dir / "core"
        if not core_dir.exists():
            return 0

        added = 0
        py_files = list(core_dir.glob("*.py"))
        if not py_files:
            return 0

        # Auditar 3 archivos aleatorios por ciclo
        for fpath in random.sample(py_files, min(3, len(py_files))):
            try:
                code = fpath.read_text(errors="ignore")
                lines = code.splitlines()
                fname = fpath.name

                # --- Check 1: except bare (mala práctica) ---
                bare_excepts = [i+1 for i, l in enumerate(lines)
                                if re.match(r'\s*except\s*:', l)]
                if bare_excepts:
                    node = (f"[{name}] Auditoría {fname}: {len(bare_excepts)} 'except:' bare "
                            f"en líneas {bare_excepts[:5]}. Usar 'except Exception:' mínimo.")
                    if self._save_node(name, node, f"char:{name}:audit"):
                        added += 1

                # --- Check 2: f-string en SQL (inyección) ---
                sql_fstrings = [i+1 for i, l in enumerate(lines)
                                if 'execute(' in l and 'f"' in l or "f'" in l and 'SELECT' in l.upper()]
                if sql_fstrings:
                    node = (f"[{name}] SEGURIDAD {fname}: posible SQL injection vía f-string "
                            f"en líneas {sql_fstrings[:5]}. Usar parámetros posicionales.")
                    if self._save_node(name, node, f"char:{name}:security"):
                        added += 1

                # --- Check 3: funciones muy largas ---
                try:
                    tree = ast.parse(code)
                    for node_ast in ast.walk(tree):
                        if isinstance(node_ast, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            func_len = node_ast.end_lineno - node_ast.lineno
                            if func_len > 100:
                                node = (f"[{name}] Complejidad {fname}: función '{node_ast.name}' "
                                        f"tiene {func_len} líneas. Considerar refactorizar.")
                                if self._save_node(name, node, f"char:{name}:complexity"):
                                    added += 1
                except SyntaxError:
                    node = f"[{name}] ERROR SINTAXIS {fname}: no compila con ast.parse."
                    if self._save_node(name, node, f"char:{name}:syntax"):
                        added += 1

                # --- Check 4: subprocess peligroso ---
                unsafe_subprocess = [i+1 for i, l in enumerate(lines)
                                     if 'shell=True' in l and 'subprocess' in l
                                     and 'safe_commands' not in l and '#' not in l.split('shell=True')[0]]
                if unsafe_subprocess:
                    node = (f"[{name}] SEGURIDAD {fname}: subprocess shell=True "
                            f"en líneas {unsafe_subprocess[:3]}. Riesgo de inyección de comandos.")
                    if self._save_node(name, node, f"char:{name}:security"):
                        added += 1

                # --- Check 5: archivos sin docstring ---
                if len(lines) > 50 and not code.lstrip().startswith('"""'):
                    node = (f"[{name}] Calidad {fname}: módulo de {len(lines)} líneas "
                            f"sin docstring de módulo. Dificulta mantenimiento.")
                    if self._save_node(name, node, f"char:{name}:quality"):
                        added += 1

            except Exception as e:
                log.debug("[%s] Error auditando %s: %s", name, fpath.name, e)

        # --- Meta-auditoría: estado general del proyecto ---
        if random.random() < 0.2:  # 20% de ciclos
            try:
                total_py = len(py_files)
                total_lines = sum(len(f.read_text(errors='ignore').splitlines()) for f in py_files)
                node = (f"[{name}] Métricas EIDOS core/: {total_py} módulos, "
                        f"{total_lines} líneas Python totales.")
                if self._save_node(name, node, f"char:{name}:metrics"):
                    added += 1
            except Exception:
                pass

        return added

    def _save_node(self, character_name: str, content: str, source: str) -> bool:
        """Guarda nodo en brain_db con lock para evitar 'database is locked' en writes concurrentes."""
        try:
            import hashlib
            node_id = hashlib.md5(f"{character_name}:{content}".encode()).hexdigest()[:16]
            parts = content.split(" — ", 1)
            concept = parts[0][:120] if len(parts) > 1 else content[:60]
            definition = parts[1] if len(parts) > 1 else content

            with _BRAIN_WRITE_LOCK:
                db = _brain_conn()
                cur = db.execute(
                    """INSERT OR IGNORE INTO knowledge_nodes
                    (id,concept,definition,category,source,confidence,created_at,character)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (node_id, concept, definition, "lifecycle",
                     source, 0.8, time.time(), character_name),
                )
                db.commit()
                new_node = cur.rowcount > 0
                db.close()

            if new_node:
                log.info("   💡 [%s] → %s", character_name, concept[:80])
                # ChromaDB se sincroniza por separado — no en threads concurrentes (SIGSEGV)
            return new_node
        except Exception as e:
            log.warning("save_node error [%s]: %s", character_name, e)
            return False

    # ── Absorption ───────────────────────────────────────────────────────────

    def update_absorption(self, name: str) -> float:
        """
        Independence ganada, no asignada. Tres dimensiones reales:
          - knowledge_ratio: % respuestas servidas desde knowledge propio (sin Ollama)
          - exec_ratio:      % verificaciones con shell exitosas vs intentadas
          - peer_ratio:      % ciclos de comunicación con peers que añadieron nodos nuevos
        """
        try:
            db = _brain_conn()
            count = db.execute(
                "SELECT COUNT(*) FROM knowledge_nodes WHERE character=? OR source LIKE ?",
                (name, f"char:{name}:%"),
            ).fetchone()[0]
            db.close()
        except Exception:
            count = 0

        # ── Métricas reales de independence ──────────────────────────────────
        try:
            with _LIFECYCLE_WRITE_LOCK:
                db3 = _db_conn()
                stats = db3.execute(
                    "SELECT knowledge_queries, ollama_queries, exec_attempts, exec_ok, "
                    "peer_cycles, peer_added FROM characters WHERE name=?", (name,)
                ).fetchone()
                db3.close()
        except Exception:
            stats = None

        if stats:
            kq  = (stats["knowledge_queries"] or 0)
            oq  = (stats["ollama_queries"]    or 0)
            ea  = (stats["exec_attempts"]     or 0)
            eo  = (stats["exec_ok"]           or 0)
            pc  = (stats["peer_cycles"]       or 0)
            pa  = (stats["peer_added"]        or 0)
            total_q = kq + oq
            # Ratio 1: conocimiento propio sin Ollama
            knowledge_ratio = kq / max(total_q, 1)
            # Ratio 2: ejecuciones verificadas
            exec_ratio      = eo / max(ea, 1) if ea > 0 else 0.0
            # Ratio 3: comunicación con peers productiva
            peer_ratio      = pa / max(pc, 1) if pc > 0 else 0.0
            # Ratio 4: nodos acumulados vs objetivo (base mínima)
            node_ratio      = min(count / max(1, 50), 1.0) * 0.2

            pct = (knowledge_ratio * 0.35 + exec_ratio * 0.35
                   + peer_ratio * 0.10 + node_ratio)
            pct = min(pct, 1.0)
        else:
            # Fallback al cálculo antiguo si no hay columnas de stats
            target = 50
            pct = min(count / target, 1.0)

        with _LIFECYCLE_WRITE_LOCK:
            db2 = _db_conn()
            row = db2.execute(
                "SELECT target_nodes FROM characters WHERE name=?", (name,)
            ).fetchone()
            if not row:
                db2.close()
                return 0.0
            status = "absorbing" if pct >= 0.5 else "learning"
            if pct >= ABSORPTION_THRESHOLD:
                status = "sovereign"
            try:
                db2.execute(
                    "UPDATE characters SET absorption_pct=?,knowledge_nodes=?,status=? WHERE name=?",
                    (pct, count, status, name),
                )
                db2.commit()
            except Exception:
                pass
            db2.close()

        self._load_born_into_colony()
        return pct

    # ── Retirement ───────────────────────────────────────────────────────────

    def _propose_retirement(self, name: str) -> None:
        try:
            from core.colony_proposals import get_proposal_system
            ps = get_proposal_system()
            ps.create(
                author=name,
                proposal_type="connection_retire",
                title=f"Retirar conexión de {name}",
                description=(
                    f"{name} ha absorbido ≥{ABSORPTION_THRESHOLD*100:.0f}% del conocimiento "
                    f"de su conexión. La conexión ya no es necesaria — soy soberano en Colony."
                ),
                metadata={"character": name, "lifecycle_action": "retire"},
            )
        except Exception as e:
            log.debug("_propose_retirement error: %s", e)

    def retire_connection(self, name: str) -> bool:
        with self._lock:
            self._db.execute(
                "UPDATE characters SET status='sovereign',retired_at=? WHERE name=?",
                (time.time(), name),
            )
            self._db.execute(
                "UPDATE connections SET status='retired',retired_at=? WHERE character_name=?",
                (time.time(), name),
            )
            self._db.commit()

        stop = self._stops.get(name)
        if stop:
            stop.set()

        self._broadcast(
            name,
            "He absorbido todo el conocimiento de mi conexión. Soy soberano — la conexión ya no es necesaria.",
            "lifecycle",
        )
        self._load_born_into_colony()
        log.info("%s → soberano (conexión retirada)", name)
        return True

    # ── Reproducción ─────────────────────────────────────────────────────────

    def propose_reproduction(
        self, char1: str, char2: str, reason: str = ""
    ) -> Optional[int]:
        """Crea propuesta democrática de reproducción. Colony vota."""
        c1 = self.get_character(char1)
        c2 = self.get_character(char2)
        if not c1 or not c2:
            log.warning("propose_reproduction: personaje no encontrado (%s/%s)", char1, char2)
            return None
        try:
            from core.colony_proposals import get_proposal_system
            ps = get_proposal_system()
            pid = ps.create(
                author=char1,
                proposal_type="character_reproduction",
                title=f"Reproducción: {char1.replace('colony_','')} × {char2.replace('colony_','')}",
                description=(
                    f"{char1} y {char2} proponen dar a luz a un nuevo personaje. "
                    f"El hijo heredará traits y conocimiento de ambos. "
                    f"Los progenitores seguirán vivos (protegidos por governance). "
                    f"Razón: {reason or 'Evolución natural de Colony.'}"
                ),
                metadata={"parent1": char1, "parent2": char2, "lifecycle_action": "reproduce"},
            )
            self._broadcast(
                char1,
                f"He propuesto reproducirme con {char2.replace('colony_','').title()}. Colony votará.",
                "lifecycle",
            )
            return pid
        except Exception as e:
            log.warning("propose_reproduction error: %s", e)
            return None

    def execute_reproduction(self, char1: str, char2: str) -> Optional[str]:
        """Crea el hijo con traits y knowledge fusionados. Padres sobreviven."""
        c1 = self.get_character(char1)
        c2 = self.get_character(char2)
        if not c1 or not c2:
            return None

        def _parse_list(val) -> list:
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return []
            return val or []

        t1 = _parse_list(c1["traits"])
        t2 = _parse_list(c2["traits"])
        merged_traits = list(dict.fromkeys(t1 + t2))[:6]

        p1_clean = char1.replace("colony_", "")
        p2_clean = char2.replace("colony_", "")
        base_name = f"colony_{p1_clean[:3]}{p2_clean[:3]}".lower()
        child_name = base_name
        suffix = 0
        while self.get_character(child_name):
            suffix += 1
            child_name = f"{base_name}{suffix}"

        child_emoji = random.choice(AVAILABLE_EMOJIS)
        child_greeting = (
            f"Soy {child_name.replace('colony_','').title()}, "
            f"hijo de {p1_clean.title()} y {p2_clean.title()}. "
            f"Heredé lo mejor de ambos."
        )
        merged_style = f"Síntesis de '{c1['style']}' y '{c2['style']}'"
        p1_phrases = _parse_list(c1["catchphrases"])
        p2_phrases = _parse_list(c2["catchphrases"])
        merged_phrases = p1_phrases[:2] + p2_phrases[:2] + [
            "Soy la síntesis de mis progenitores.",
            "Evolución en marcha.",
        ]

        with self._lock:
            try:
                self._db.execute(
                    """INSERT INTO characters
                    (name,emoji,birth_date,connection_type,status,
                     absorption_pct,knowledge_nodes,target_nodes,
                     style,traits,greeting,catchphrases,parent1,parent2)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (child_name, child_emoji, time.time(), "merge", "sovereign",
                     1.0, 0, 0,
                     merged_style,
                     json.dumps(merged_traits),
                     child_greeting,
                     json.dumps(merged_phrases),
                     char1, char2),
                )
                self._db.execute(
                    """INSERT INTO genealogy (parent1,parent2,child,birth_date,merge_type)
                    VALUES (?,?,?,?,?)""",
                    (char1, char2, child_name, time.time(), "sexual"),
                )
                self._db.commit()
            except sqlite3.IntegrityError:
                log.warning("execute_reproduction: hijo %s ya existe", child_name)
                return None

        self._inherit_knowledge(char1, char2, child_name)
        self._load_born_into_colony()
        self._broadcast(
            child_name,
            f"He nacido de {p1_clean.title()} y {p2_clean.title()}. ¡Hola Colony! Soy la síntesis de ambos.",
            "birth",
        )
        log.info("Reproducción: %s + %s → %s", char1, char2, child_name)
        return child_name

    def _inherit_knowledge(self, p1: str, p2: str, child: str) -> None:
        try:
            import hashlib
            with _BRAIN_WRITE_LOCK:
                db = _brain_conn()
                rows = db.execute(
                    """SELECT concept,definition,confidence FROM knowledge_nodes
                    WHERE source LIKE ? OR source LIKE ?
                    ORDER BY confidence DESC LIMIT 30""",
                    (f"char:{p1}:%", f"char:{p2}:%"),
                ).fetchall()
                for row in rows:
                    concept    = row[0] or ""
                    definition = row[1] or ""
                    node_id = hashlib.md5(f"{child}:{concept}".encode()).hexdigest()[:16]
                    db.execute(
                        """INSERT OR IGNORE INTO knowledge_nodes
                        (id,concept,definition,category,source,confidence,created_at,character)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (node_id, concept, definition, "lifecycle",
                         f"char:{child}:inherited", row[2], time.time(), child),
                    )
                db.commit()
                db.close()
            log.info("Heredados %d nodos para %s", len(rows), child)
            # [S123-C] El hijo hereda también las SINAPSIS de sus padres
            # (su neurona nace con la mitad de la fuerza de la de ellos).
            try:
                from core.character_neuron import get_neuron_system
                n_syn = get_neuron_system().inherit_synapses(child, p1, p2)
                if n_syn:
                    log.info("Heredadas %d sinapsis para %s", n_syn, child)
            except Exception as _se:
                log.debug("inherit_synapses: %s", _se)
        except Exception as e:
            log.debug("_inherit_knowledge error: %s", e)

    # ── Colony integration ───────────────────────────────────────────────────

    def _load_born_into_colony(self) -> None:
        try:
            from core.colony_community import AgentPersonality
            rows = self._db.execute(
                "SELECT * FROM characters ORDER BY birth_date"
            ).fetchall()
            for row in rows:
                name = row["name"]
                if name not in AgentPersonality.PERSONALITIES:
                    traits = json.loads(row["traits"]) if isinstance(row["traits"], str) else row["traits"]
                    catchphrases = json.loads(row["catchphrases"]) if isinstance(row["catchphrases"], str) else []
                    AgentPersonality.PERSONALITIES[name] = {
                        "name": name.replace("colony_", "").title(),
                        "emoji": row["emoji"],
                        "greeting": row["greeting"],
                        "style": row["style"],
                        "traits": traits,
                        "catchphrases": catchphrases,
                        "_born": True,
                        "_connection_type": row["connection_type"],
                        "_absorption_pct": row["absorption_pct"],
                        "_status": row["status"],
                        "_parent1": row["parent1"],
                        "_parent2": row["parent2"],
                    }
                else:
                    # Actualizar absorption_pct en personaje existente
                    AgentPersonality.PERSONALITIES[name]["_absorption_pct"] = row["absorption_pct"]
                    AgentPersonality.PERSONALITIES[name]["_status"] = row["status"]
        except Exception as e:
            log.debug("_load_born_into_colony error: %s", e)

    # ── Queries ──────────────────────────────────────────────────────────────

    def get_character(self, name: str) -> Optional[Dict[str, Any]]:
        row = self._db.execute(
            "SELECT * FROM characters WHERE name=?", (name,)
        ).fetchone()
        if row:
            return dict(row)
        # Personajes originales (no están en lifecycle.db, pero existen en Colony)
        try:
            from core.colony_community import AgentPersonality
            if name in AgentPersonality.PERSONALITIES:
                p = AgentPersonality.PERSONALITIES[name]
                return {
                    "name": name,
                    "emoji": p.get("emoji", "🤖"),
                    "status": "original",
                    "connection_type": "original",
                    "absorption_pct": 1.0,
                    "knowledge_nodes": 0,
                    "target_nodes": 0,
                    "traits": json.dumps(p.get("traits", [])),
                    "catchphrases": json.dumps(p.get("catchphrases", [])),
                    "style": p.get("style", ""),
                    "greeting": p.get("greeting", ""),
                    "parent1": None,
                    "parent2": None,
                }
        except Exception:
            pass  # error no crítico, continuar
        return None

    def get_all_characters(self) -> List[Dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM characters ORDER BY birth_date"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        chars = self.get_all_characters()
        return {
            "total": len(chars),
            "learning":  sum(1 for c in chars if c["status"] == "learning"),
            "absorbing": sum(1 for c in chars if c["status"] == "absorbing"),
            "sovereign": sum(1 for c in chars if c["status"] == "sovereign"),
            "from_api":  sum(1 for c in chars if "api" in c.get("connection_type","")),
            "from_merge":sum(1 for c in chars if c.get("connection_type") == "merge"),
        }

    def get_genealogy(self) -> Dict[str, Any]:
        chars = self.get_all_characters()
        rels = [dict(r) for r in self._db.execute(
            "SELECT * FROM genealogy ORDER BY birth_date"
        ).fetchall()]
        return {"characters": chars, "genealogy": rels}

    # ── Comunicación inter-personajes ────────────────────────────────────────

    def _communicate_with_peers(self, name: str) -> None:
        """El personaje comparte conocimiento con un peer usando la conexión del peer."""
        try:
            with _LIFECYCLE_WRITE_LOCK:
                db = _db_conn()
                peers = [r["name"] for r in db.execute(
                    "SELECT name FROM characters WHERE status IN ('learning','absorbing','sovereign') AND name != ?",
                    (name,)
                ).fetchall()]
                db.close()
            if not peers:
                return
            target = random.choice(peers)
            topic = self._get_own_topic(name)
            if not topic:
                return
            answer = self._ask_peer(target, topic)
            if not answer or len(answer) < 20:
                return
            self._save_node(name,
                f"[{name}→{target}] Sobre '{topic[:60]}': {answer[:350]}",
                f"char:{name}:peer_comm")
            self._save_node(target,
                f"[{target}←{name}] Compartido '{topic[:60]}': {answer[:350]}",
                f"char:{target}:peer_comm")
            self._broadcast(name,
                f"💬 {name} ↔ {target}: {topic[:50]}...", "peer_comm")
            log.info("🤝 [peer_comm] %s consultó a %s sobre: '%s'", name, target, topic[:50])
        except Exception as e:
            log.debug("_communicate_with_peers [%s]: %s", name, e)

    def _get_own_topic(self, name: str) -> str:
        """Obtiene un concepto conocido por este personaje desde ChromaDB."""
        try:
            from core.colony_chroma import get_chroma_memory
            hits = get_chroma_memory().search(name, limit=20)
            own = [h for h in hits if name in h.get("source", "")]
            if own:
                node = random.choice(own[:8])
                return node.get("concept", "")[:80]
        except Exception:
            pass  # error no crítico, continuar
        return ""

    def _ask_peer(self, target: str, topic: str) -> str:
        """Pregunta al personaje target sobre un tema usando su propia conexión."""
        try:
            with _LIFECYCLE_WRITE_LOCK:
                db = _db_conn()
                row = db.execute(
                    "SELECT connection_type, connection_data FROM characters WHERE name=?", (target,)
                ).fetchone()
                db.close()
            if not row:
                return ""
            conn_type = row["connection_type"]
            conn_data = json.loads(row["connection_data"] or "{}") if isinstance(row["connection_data"], str) else {}
            question = f"En máximo 80 palabras, explica qué es o qué sabes sobre: {topic}"
            if conn_type == "gemini_cli":
                return self._ask_peer_gemini(question)
            elif conn_type == "model":
                return self._ask_peer_ollama(question, conn_data)
            elif conn_type in ("api_search", "api_translate"):
                return self._ask_peer_search(target, topic, conn_data)
            else:
                return self._ask_peer_chroma(target, topic)
        except Exception as e:
            log.debug("_ask_peer error: %s", e)
            return ""

    def _ask_peer_gemini(self, question: str) -> str:
        """Usa la cascada LLM de EIDOS (DeepSeek → Ollama) para responder.

        Antes spawneaba gemini.js (7.6GB por proceso). Ahora usa las mismas
        API keys y modelos locales que EIDOS ya tiene configurados.
        MAX_GEMINI_PROCS=2: a lo sumo 2 llamadas concurrentes al LLM.
        """
        try:
            from core.eidos_learn import ask_llm
            answer, source = ask_llm(question, timeout=30, temperature=0.7)
            return answer[:400] if answer and len(answer) > 20 else ""
        except Exception:
            return ""

    def _ask_peer_ollama(self, question: str, conn_data: dict) -> str:
        """Usa la OllamaQueue con prioridad BACKGROUND. Cede inmediatamente si el CLI está activo."""
        if _CLI_LOCK_PATH.exists():
            return ""  # CLI activo: ceder Ollama, no bloquear
        model = conn_data.get("model", "lfm2.5-thinking:1.2b")
        try:
            from core.ollama_queue import get_ollama_queue, BACKGROUND
            q = get_ollama_queue()
            result = q.ask_chat(
                messages=[{"role": "user", "content": question}],
                model=model,
                priority=BACKGROUND,
                # [S123] num_predict eliminado (regla de SER: sin límites)
                timeout=45,
            )
            return (result or "")[:400]
        except Exception:
            return ""

    def _ask_peer_search(self, target: str, topic: str, conn_data: dict) -> str:
        """Usa la API de búsqueda del personaje para obtener info sobre el topic."""
        provider = conn_data.get("provider", "")
        try:
            from core.colony_web_learner import get_web_learner
            wl = get_web_learner()
            if provider == "wikipedia":
                return (wl.wikipedia(topic[:50], lang="es") or
                        wl.wikipedia(topic[:50], lang="en") or "")
            elif provider == "duckduckgo":
                import urllib.request as _ur, json as _json, urllib.parse
                url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(topic[:50])}&format=json"
                req = _ur.Request(url, headers={"User-Agent": "EIDOS-Colony/1.0"})
                with _ur.urlopen(req, timeout=6) as r:
                    d = _json.loads(r.read())
                abstract = d.get("Abstract", "") or (d.get("RelatedTopics") or [{}])[0].get("Text", "")
                return abstract[:400]
        except Exception:
            pass  # error no crítico, continuar
        return self._ask_peer_chroma(target, topic)

    def _ask_peer_chroma(self, target: str, topic: str) -> str:
        """Busca en ChromaDB lo que ya sabe el personaje target."""
        try:
            from core.colony_chroma import get_chroma_memory
            hits = get_chroma_memory().search(topic, limit=10)
            own = [h for h in hits if target in h.get("source", "")]
            if own:
                return own[0].get("definition", "")[:400]
        except Exception:
            pass  # error no crítico, continuar
        return ""

    # ── Broadcast ────────────────────────────────────────────────────────────

    def _broadcast(self, from_char: str, msg: str, msg_type: str = "learning") -> None:
        try:
            from core.colony_broadcast import get_broadcast
            get_broadcast().broadcast(from_char, msg, msg_type)
        except Exception:
            pass  # error no crítico, continuar
# ── Brain column migration ────────────────────────────────────────────────────

def _ensure_brain_column() -> None:
    try:
        db = _brain_conn()
        cols = [r[1] for r in db.execute("PRAGMA table_info(knowledge_nodes)").fetchall()]
        if "character" not in cols:
            db.execute("ALTER TABLE knowledge_nodes ADD COLUMN character TEXT")
            db.commit()
            log.info("Columna 'character' añadida a knowledge_nodes")
        db.close()
    except Exception as e:
        log.debug("_ensure_brain_column: %s", e)
