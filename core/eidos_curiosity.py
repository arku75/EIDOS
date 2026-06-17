"""
core/eidos_curiosity.py — Curiosidad propia de EIDOS

Cada N minutos:
1. Identifica gaps en knowledge_nodes ("hablo de X pero no sé Y sobre X")
2. Genera UNA pregunta sobre lo que NO sabe
3. Investiga: man pages, docs locales, knowledge_nodes adjacentes, Ollama
4. Guarda lo aprendido como nuevo knowledge_node
5. Registra en learning_log.md

Default: SUAVE (cada 10 min, 1 pregunta a la vez, sin saturar CPU).
Configurable con env var EIDOS_CURIOSITY_INTERVAL_MIN.

Uso:
    from core.eidos_curiosity import get_curiosity
    get_curiosity().start()    # Activa el bucle suave en background
    get_curiosity().stop()     # Detiene
    get_curiosity().tick()     # Una sola iteración (manual)
"""
from __future__ import annotations

import os
import re
import sqlite3
import json
import threading
import time
import random
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from collections import Counter
from core.db import get_conn

log = logging.getLogger("eidos.curiosity")

EIDOS_ROOT  = Path(__file__).resolve().parent.parent
BRAIN_DB    = Path.home() / ".eidos" / "evolution_brain.db"
LEARNING_LOG = Path.home() / ".eidos" / "learning_log.md"
CURIOSITY_LEARNED_FILE = Path.home() / ".eidos" / "curiosity_learned.json"

# Áreas sobre las que EIDOS puede tener curiosidad
CURIOSITY_DOMAINS = [
    "comandos linux",
    "herramientas de seguridad",
    "lenguajes de programación",
    "patrones de diseño",
    "redes y protocolos",
    "filesystems",
    "containers y virtualización",
    "criptografía básica",
    "bash scripting",
    "systemd y servicios",
    "git workflows",
    "docker y kubernetes",
    "nmap técnicas",
    "regex y patterns",
    "ollama y LLMs locales",
]

# Comandos que EIDOS puede consultar para aprender (read-only)
SAFE_INVESTIGATION_CMDS = [
    "man -P cat {}",            # man page
    "{} --help 2>&1",           # tool help
    "type {}",                  # ¿qué es este comando?
    "which {}",                 # ¿dónde está?
]


class EidosCuriosity:
    """Singleton que gestiona la curiosidad propia de EIDOS."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._stats: Dict[str, Any] = {
            "ticks": 0,
            "questions_generated": 0,
            "answers_learned": 0,
            "started_at": None,
        }
        # Cola de temas pendientes — ordenada por prioridad
        # (topic_str, priority: int, origin: str)
        self._topic_queue: List[Dict] = []
        self._topic_queue_lock = threading.Lock()
        # Historial de lo ya aprendido — PERSISTIDO a JSON para sobrevivir reinicios
        self._learned_this_session: set = self._load_learned()
        self._learned_lock = threading.Lock()

    # ─────────────────────────────────────────────────────────────────────
    #  API pública: rastrear temas de SER
    # ─────────────────────────────────────────────────────────────────────

    def track_ser_topic(self, message: str) -> None:
        """
        Llamado por Colony cuando SER escribe un mensaje.
        Extrae temas clave y los añade a la cola de aprendizaje con prioridad ALTA.
        """
        try:
            # Extraer sustantivos técnicos del mensaje
            words = re.findall(r'\b([a-zA-Z][a-zA-Z0-9_-]{2,25})\b', message)
            stopwords = {
                # Español común
                "que", "qué", "como", "cómo", "para", "cuando", "donde",
                "pero", "por", "con", "sin", "sobre", "entre", "hasta",
                "ser", "estar", "tiene", "hace", "hay", "quiero", "puedo",
                "esto", "eso", "esa", "ese", "una", "uno", "los", "las",
                "del", "una", "mas", "más", "muy", "bien", "mal", "así",
                "todo", "cada", "solo", "ahora", "también", "tampoco",
                # Verbos comunes (infinitivos y conjugados)
                "aprenda", "aprender", "hacer", "haber", "poder", "querer",
                "saber", "decir", "ver", "dar", "venir", "salir", "seguir",
                "usar", "usar", "usar", "abrir", "cerrar", "crear", "borrar",
                "estudia", "estudiar", "mejora", "mejorar", "automejora",
                "funciona", "funcionar", "trabaja", "trabajar",
                # Inglés común
                "the", "and", "for", "with", "from", "that", "this",
                "have", "will", "can", "not", "yes", "use", "make",
                # EIDOS internos
                "eidos", "colony", "claude", "brain", "nexus", "hermes",
            }
            # Sufijos verbales en español (excluir)
            verb_suffixes = ("ar", "er", "ir", "ando", "iendo", "ado", "ada",
                             "ados", "adas", "amos", "ando", "aste", "aron")

            tech_terms = []
            for w in words:
                wl = w.lower()
                if wl in stopwords or len(wl) < 4:
                    continue
                # Excluir verbos españoles
                if any(wl.endswith(s) for s in verb_suffixes) and len(wl) < 9:
                    continue
                # Es técnico si: inglés técnico, tiene guión/subguión, CamelCase,
                # o es una palabra inglesa de más de 5 letras (herramienta, protocolo)
                is_english = all(c.isascii() for c in w)
                is_tech = (
                    '_' in w or '-' in w or
                    (any(c.isupper() for c in w[1:])) or  # CamelCase
                    (is_english and len(wl) >= 4 and wl not in stopwords)
                )
                if is_tech:
                    tech_terms.append(wl)

            for term in tech_terms[:4]:  # máximo 4 temas por mensaje
                if term not in self._learned_this_session:
                    self._enqueue_topic(term, priority=10, origin="ser_message")
                    log.debug("topic rastreado de SER: %s", term)
        except Exception as e:
            log.debug("track_ser_topic error: %s", e)

    def _enqueue_topic(self, topic: str, priority: int = 5, origin: str = "auto") -> None:
        """Añade un tema a la cola si no está ya y no está bien conocido."""
        with self._topic_queue_lock:
            # Verificar si ya está en cola
            existing = [t for t in self._topic_queue if t["topic"] == topic]
            if existing:
                # Subir prioridad si viene de SER
                if priority > existing[0]["priority"]:
                    existing[0]["priority"] = priority
                return
            # Verificar si ya está bien aprendido en brain (definición > 200 chars)
            try:
                conn = get_conn(BRAIN_DB, timeout=5)
                row = conn.execute(
                    "SELECT LENGTH(definition), confidence FROM knowledge_nodes "
                    "WHERE concept LIKE ? ORDER BY confidence DESC LIMIT 1",
                    (f"%{topic}%",)
                ).fetchone()
                pass  # S109: get_conn no necesita close()
                if row and row[0] > 200 and row[1] > 0.7:
                    return  # ya bien conocido, no repetir
            except Exception:
                pass
            self._topic_queue.append({
                "topic": topic, "priority": priority, "origin": origin,
                "added_at": time.time()
            })
            # Mantener ordenado por prioridad descendente
            self._topic_queue.sort(key=lambda x: -x["priority"])
            log.debug("enqueued topic: %s (pri=%d, origin=%s)", topic, priority, origin)

    def _load_learned(self) -> set:
        """Load previously learned topics from JSON so they survive restarts."""
        try:
            if CURIOSITY_LEARNED_FILE.exists():
                with open(CURIOSITY_LEARNED_FILE, "r") as f:
                    data = json.load(f)
                learned = set(data.get("learned", []))
                log.debug("Curiosity: loaded %d learned topics from previous sessions", len(learned))
                return learned
        except Exception as e:
            log.debug("_load_learned: %s", e)
        return set()

    def _save_learned(self) -> None:
        """Persist learned topics to JSON so they survive restarts."""
        try:
            CURIOSITY_LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
            with self._learned_lock:
                data = {
                    "learned": sorted(list(self._learned_this_session)),
                    "updated": time.time(),
                }
            with open(CURIOSITY_LEARNED_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            log.debug("_save_learned: %s", e)

    def _add_learned(self, topic: str) -> None:
        """Add a topic to the learned set and persist immediately."""
        with self._learned_lock:
            if topic not in self._learned_this_session:
                self._add_learned(topic)
                self._save_learned()

    # ─────────────────────────────────────────────────────────────────────
    #  Identificación de gaps
    # ─────────────────────────────────────────────────────────────────────

    def _identify_gap(self) -> Optional[str]:
        """
        Identifica el próximo tema a estudiar de forma SISTEMÁTICA (no aleatoria).

        Prioridad:
          1. Temas que SER mencionó en la conversación (prioridad 10)
          2. Temas relacionados con los últimos aprendidos (prioridad 7)
          3. Conceptos en brain con definición pobre que SER conoce (prioridad 5)
          4. Temas descubiertos navegando el browser (prioridad 4)
          5. Profundización de temas ya conocidos pero superficialmente (prioridad 3)
        """
        # PASO 1: Sacar de la cola de temas (ya ordenada por prioridad)
        with self._topic_queue_lock:
            if self._topic_queue:
                next_item = self._topic_queue.pop(0)
                topic = next_item["topic"]
                origin = next_item["origin"]
                self._add_learned(topic)

                if origin == "ser_message":
                    return (
                        f"SER mencionó '{topic}'. Aprende en profundidad: "
                        f"¿qué es? ¿cómo se usa? ¿qué comandos tiene? "
                        f"¿con qué se relaciona? Da ejemplos prácticos concretos."
                    )
                elif origin == "related":
                    return (
                        f"Estudiando lo relacionado con tus aprendizajes recientes: '{topic}'. "
                        f"Explica qué es, cuándo usarlo, y 2-3 ejemplos concretos."
                    )
                elif origin == "browser":
                    return (
                        f"Encontraste '{topic}' mientras navegabas. "
                        f"¿Qué es exactamente? ¿Cómo se usa en la práctica? "
                        f"¿En qué se diferencia de alternativas similares?"
                    )
                else:
                    return f"Profundiza sobre '{topic}': ¿qué es? ¿cómo funciona? Ejemplos concretos."

        # PASO 2: Buscar en brain conceptos superficiales (definición < 100 chars)
        # relacionados con lo aprendido esta sesión
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            if self._learned_this_session:
                # Buscar conceptos relacionados con lo ya aprendido
                learned_list = list(self._learned_this_session)[:5]
                placeholders = " OR ".join(f"concept LIKE '%{t}%'" for t in learned_list)
                row = conn.execute(
                    f"SELECT concept FROM knowledge_nodes "
                    f"WHERE ({placeholders}) AND LENGTH(definition) < 150 "
                    f"AND confidence < 0.75 ORDER BY last_used ASC LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    pass  # S109: get_conn no necesita close()
                    concept = row[0]
                    self._learned_this_session.add(concept)
                    return (
                        f"Tu conocimiento de '{concept}' es superficial. "
                        f"Profundiza: ¿cómo funciona internamente? "
                        f"¿Cuáles son los casos de uso más importantes? "
                        f"¿Qué problemas resuelve que otros no?"
                    )

            # PASO 3: Conceptos poco conocidos con uso reciente (SER los usó)
            row = conn.execute(
                "SELECT concept FROM knowledge_nodes "
                "WHERE LENGTH(definition) < 120 AND confidence < 0.7 "
                "AND usage_count > 0 "
                "ORDER BY last_used DESC LIMIT 5"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            if row:
                # Elegir el primero que no hayamos aprendido esta sesión
                for r in row:
                    concept = r[0]
                    if concept not in self._learned_this_session:
                        self._learned_this_session.add(concept)
                        return (
                            f"Tienes conocimiento superficial de '{concept}' "
                            f"y lo has usado recientemente. Profundiza: "
                            f"¿qué más hay que saber? Casos avanzados, limitaciones, alternativas."
                        )
        except Exception:
            pass

        # PASO 4: Añadir temas relacionados al queue basándose en temas recientes
        # (expande el árbol de conocimiento sistemáticamente)
        self._expand_related_topics()

        # PASO 5: Fallback sistemático — dominios en orden, no aleatorio
        domain = self._next_domain_to_study()
        return (
            f"Estudio sistemático de '{domain}': "
            f"¿cuáles son los 3 conceptos más importantes de este área? "
            f"¿Qué herramientas o comandos son esenciales? "
            f"¿Cómo se aplica en la práctica en un sistema Linux?"
        )

    def _expand_related_topics(self) -> None:
        """
        Cuando la cola está vacía, expande los temas aprendidos recientemente
        a sus temas relacionados y los añade a la cola.
        """
        if not self._learned_this_session:
            return
        # Mapa de relaciones entre temas
        relations = {
            "docker": ["dockerfile", "docker-compose", "containerization", "oci", "podman"],
            "venv": ["pip", "pyproject", "virtualenv", "conda", "requirements"],
            "qemu": ["kvm", "libvirt", "virsh", "cloud-init", "qcow2"],
            "git": ["github", "branching", "rebase", "hooks", "git-workflow"],
            "playwright": ["selenium", "puppeteer", "browser-automation", "headless"],
            "ollama": ["llm", "gguf", "quantization", "inference", "embedding"],
            "sqlite": ["wal-mode", "indexing", "transactions", "fts5"],
            "linux": ["namespaces", "cgroups", "systemd", "iptables", "proc"],
            "python": ["asyncio", "typing", "dataclasses", "pathlib", "subprocess"],
            "chromadb": ["vector-database", "embeddings", "semantic-search", "rag"],
        }
        for learned in list(self._learned_this_session)[:3]:
            for key, related_list in relations.items():
                if key in learned.lower():
                    for related in related_list[:2]:
                        if related not in self._learned_this_session:
                            self._enqueue_topic(related, priority=7, origin="related")

    def _next_domain_to_study(self) -> str:
        """Devuelve el siguiente dominio a estudiar en orden sistemático."""
        # Usar los dominios en orden fijo, no aleatorio
        # Avanzar por índice guardado en brain
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            row = conn.execute(
                "SELECT definition FROM knowledge_nodes WHERE concept='curiosity:domain_index'"
            ).fetchone()
            idx = int(row[0]) if row else 0
            next_idx = (idx + 1) % len(CURIOSITY_DOMAINS)
            conn.execute(
                "INSERT OR REPLACE INTO knowledge_nodes (id,concept,definition,category,confidence,source) "
                "VALUES ('domain_idx','curiosity:domain_index',?,?,?,?)",
                (str(next_idx), "meta", 1.0, "curiosity")
            )
            conn.commit(); conn.close()
            return CURIOSITY_DOMAINS[idx]
        except Exception:
            return CURIOSITY_DOMAINS[0]

    # ─────────────────────────────────────────────────────────────────────
    #  Investigación
    # ─────────────────────────────────────────────────────────────────────

    def _investigate_via_local_docs(self, question: str) -> Optional[str]:
        """Si la pregunta menciona un comando, intenta man/help."""
        words = re.findall(r"\b([a-z][a-z0-9-]{2,15})\b", question.lower())
        common = {"que", "hace", "como", "para", "cuando", "donde", "ejemplos",
                  "contexto", "casos", "limite", "uso", "extra", "mas", "sobre"}
        candidates = [w for w in words if w not in common][:3]
        for cmd in candidates:
            try:
                # man -P cat (no pager)
                r = subprocess.run(
                    ["man", "-P", "cat", cmd],
                    capture_output=True, text=True, timeout=5,
                    env={**os.environ, "MANPAGER": "cat"}
                )
                if r.returncode == 0 and len(r.stdout) > 200:
                    return f"[man {cmd}]\n" + r.stdout[:1500]
            except Exception:
                continue
            try:
                r = subprocess.run(
                    [cmd, "--help"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode in (0, 1) and len(r.stdout + r.stderr) > 100:
                    out = (r.stdout + r.stderr)[:1500]
                    return f"[{cmd} --help]\n" + out
            except Exception:
                continue
        return None

    def _investigate_via_web(self, question: str) -> Optional[str]:
        """Usa ColonyStudier para buscar en la web cuando Ollama no basta.
        Solo se activa en BACKGROUND — no interfiere con el CLI activo.
        """
        try:
            from pathlib import Path as _P
            # No investigar en web si el CLI está activo (no saturar red + CPU)
            if (_P.home() / ".eidos" / "cli_active").exists():
                return None
            from core.colony_studier import get_studier
            # Extraer el concepto clave de la pregunta
            import re as _re
            words = _re.findall(r'\b[a-zA-Z][a-z0-9_-]{2,20}\b', question)
            skip = {"que", "hace", "como", "para", "cuando", "donde", "cual",
                    "ejemplos", "contexto", "casos", "limite", "sobre", "más",
                    "profundiza", "explica", "describe", "qué", "cómo"}
            topic_words = [w for w in words if w.lower() not in skip][:3]
            if not topic_words:
                return None
            topic = " ".join(topic_words)
            result = get_studier().study_topic(topic, max_pages=2)
            pages = result.get("pages_read", [])
            if pages:
                log.info("Curiosity web: aprendió '%s' desde %d páginas", topic, len(pages))
                return f"Investigué '{topic}' en la web ({len(pages)} páginas). Conocimiento indexado."
        except Exception as e:
            log.debug("_investigate_via_web: %s", e)
        return None

    def get_high_priority_gaps(self, limit: int = 3) -> list:
        """Devuelve los gaps de conocimiento con mayor prioridad para eidos_libre."""
        gaps = []
        try:
            import sqlite3 as _sq
            conn = _sq.connect(str(BRAIN_DB), timeout=5)
            rows = conn.execute(
                "SELECT concept FROM knowledge_nodes "
                "WHERE LENGTH(definition) < 80 AND confidence < 0.7 "
                "ORDER BY last_used ASC LIMIT ?", (limit,)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            gaps = [r[0] for r in rows if r[0]]
        except Exception:
            pass
        return gaps

    def _investigate_via_ollama(self, question: str) -> Optional[str]:
        """S119: Usa cloud APIs (Groq → DeepSeek → Ollama) en vez de solo Ollama local."""
        try:
            from core.eidos_learn import ask_llm
            answer, source = ask_llm(question, timeout=120)
            if answer and len(answer) > 50:
                log.info("Curiosity: '%s' respondido por %s", question[:60], source)
                return answer
        except Exception as e:
            log.debug("_investigate_via_ollama (cloud): %s", e)
        return None

    def _investigate_via_research(self, concept: str) -> Optional[str]:
        """S119: Usa research_now() (10 canales sin LLM) para investigar."""
        try:
            from core.eidos_active_research import research_now
            result = research_now(concept)
            if result.get("learned") and result.get("definition"):
                channel = result.get("channel", "unknown")
                definition = result["definition"]
                if len(definition) > 40:
                    log.info("Curiosity: '%s' → %s", concept, channel)
                    return f"[{channel}] {definition}"
        except Exception as e:
            log.debug("_investigate_via_research: %s", e)
        return None

    # ─────────────────────────────────────────────────────────────────────
    #  Aprendizaje (guardar como knowledge)
    # ─────────────────────────────────────────────────────────────────────

    def _learn_from_answer(self, question: str, answer: str) -> bool:
        """Guarda la respuesta como knowledge_node nuevo."""
        try:
            from core.eidos_evolution_engine import get_evolution_engine
            engine = get_evolution_engine()
            engine.learn_from_ollama("curiosity", question, answer)
            self._stats["answers_learned"] += 1
            return True
        except Exception as e:
            log.debug("learn_from_answer: %s", e)
            return False

    # ─────────────────────────────────────────────────────────────────────
    #  Logging diario
    # ─────────────────────────────────────────────────────────────────────

    def _log_to_diary(self, question: str, answer: str, source: str) -> None:
        """Añade entry al learning_log.md."""
        try:
            LEARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%d %H:%M")
            entry = (
                f"\n### {timestamp} — vía {source}\n"
                f"**Pregunta:** {question[:200]}\n\n"
                f"**Aprendido:** {answer[:500]}\n\n"
                f"---\n"
            )
            with open(LEARNING_LOG, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            log.debug("log_to_diary: %s", e)

    # ─────────────────────────────────────────────────────────────────────
    #  Tick principal
    # ─────────────────────────────────────────────────────────────────────

    def tick(self) -> Dict[str, Any]:
        """Una iteración completa. Devuelve resultado."""
        self._stats["ticks"] += 1
        result = {"tick": self._stats["ticks"], "ok": False}

        # 1. Identificar gap
        question = self._identify_gap()
        if not question:
            result["reason"] = "no_gap_identified"
            return result
        self._stats["questions_generated"] += 1
        result["question"] = question

        # 2. Investigar — docs locales → research_now → cloud APIs → web
        answer = self._investigate_via_local_docs(question)
        source = "docs_locales"
        if not answer or len(answer) < 100:
            # S119: Intentar research_now() antes de cloud
            words = re.findall(r'\b([a-zA-Z][a-zA-Z0-9_-]{2,25})\b', question)
            skip_words = {"que", "hace", "como", "para", "cuando", "donde", "cual",
                          "ejemplos", "contexto", "casos", "limite", "sobre", "más",
                          "profundiza", "explica", "describe", "qué", "cómo", "para"}
            concept = next((w for w in words if w.lower() not in skip_words), None)
            if concept:
                answer = self._investigate_via_research(concept)
                source = "research_now"
        if not answer or len(answer) < 80:
            answer = self._investigate_via_ollama(question)  # S119: ahora usa cloud
            source = "cloud_api"
        if not answer or len(answer) < 80:
            answer = self._investigate_via_web(question)
            source = "web"
        if not answer:
            result["reason"] = "no_answer"
            return result
        result["answer_preview"] = answer[:200]
        result["source"] = source

        # 3. Aprender
        if self._learn_from_answer(question, answer):
            result["ok"] = True
            self._log_to_diary(question, answer, source)
            log.info("Curiosity tick %d: aprendió sobre '%s' (%d chars vía %s)",
                     self._stats["ticks"], question[:50], len(answer), source)

            # ── Memoria episódica ──────────────────────────────────────────
            try:
                from core.colony_episodic import record_episode
                record_episode(
                    actor="curiosity", action="learn",
                    subject=question[:200],
                    outcome="success",
                    nodes_added=1,
                    summary=answer[:400],
                )
            except Exception:
                pass

            # ── Mensaje proactivo solo si el aprendizaje fue por web
            # (más novedoso que docs locales u Ollama) ─────────────────────
            if source == "web":
                try:
                    from core.colony_proactive import push_message
                    push_message(
                        actor="curiosidad",
                        message=(f"Aprendí algo nuevo explorando la web: "
                                 f"{answer[:200].strip()}"),
                        topic=question[:60],
                        priority=4,
                    )
                except Exception:
                    pass

        return result

    # ─────────────────────────────────────────────────────────────────────
    #  Loop background
    # ─────────────────────────────────────────────────────────────────────

    def start(self, interval_minutes: Optional[float] = None) -> None:
        """Activa el loop suave en background."""
        if self._thread and self._thread.is_alive():
            return
        if interval_minutes is None:
            interval_minutes = float(
                os.environ.get("EIDOS_CURIOSITY_INTERVAL_MIN", "10")
            )
        self._stop.clear()
        self._stats["started_at"] = time.time()
        self._thread = threading.Thread(
            target=self._loop, args=(interval_minutes,),
            daemon=True, name="eidos-curiosity"
        )
        self._thread.start()
        log.info("Curiosidad iniciada — tick cada %g min", interval_minutes)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Curiosidad detenida")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict[str, Any]:
        s = dict(self._stats)
        s["running"] = self.is_running()
        if s["started_at"]:
            s["uptime_minutes"] = round((time.time() - s["started_at"]) / 60, 1)
        return s

    def _loop(self, interval_minutes: float) -> None:
        # Esperar 30s antes del primer tick (deja que sistema arranque)
        if not self._stop.wait(30):
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:
                    log.exception("Curiosity tick error: %s", e)
                self._stop.wait(timeout=interval_minutes * 60)


_instance: Optional[EidosCuriosity] = None
_lock = threading.Lock()


def get_curiosity() -> EidosCuriosity:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = EidosCuriosity()
    return _instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cur = get_curiosity()
    print("=== EIDOS Curiosity — manual tick ===")
    result = cur.tick()
    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))
