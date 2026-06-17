#!/usr/bin/env python3
"""
core/autonomous_research_loop.py — Orquestador de autonomía real (S119).

EL conductor que faltaba. Conecta research → lógica → cloud APIs → navegador
en un ciclo autónomo que corre en background sin intervención de SER.

Ciclo (cada N minutos):
  1. IDENTIFICA: ¿qué no sé? (gaps en grafo, conceptos huérfanos, curiosidad)
  2. INVESTIGA: research_now() → Groq/DeepSeek → navegador (en cascada)
  3. EXTRAE: hechos del texto investigado → motor lógico
  4. RAZONA: aplica reglas a nuevos hechos → conclusiones
  5. PERSISTE: guarda en DB + learning_log
  6. NOTIFICA: si es relevante, avisa a SER

Arquitectura SIN LLM LOCAL:
  Capa 1 (90%): research_now() — 10 canales, $0, <5s
  Capa 2 (9%):  Groq cloud — 70B params, $0, <3s
  Capa 3 (1%):  DeepSeek cloud — 67B params, ~$0.0003, <8s

Uso:
  from core.autonomous_research_loop import AutonomousResearchLoop
  loop = AutonomousResearchLoop(interval=300)
  loop.start()   # inicia ciclo en background
  loop.stop()    # detiene
  loop.cycle()   # una iteración manual
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from core.db import get_conn  # noqa: E402 — capa DB unificada S109
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.autoresearch")

# ── Configuración ─────────────────────────────────────────────────────────────
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
LEARNING_LOG = Path.home() / ".eidos" / "autonomous_learning_log.md"
STATS_FILE = Path.home() / ".eidos" / "autoresearch_stats.json"

# Intervalo por defecto: 5 minutos (300s)
DEFAULT_INTERVAL = int(os.environ.get("EIDOS_AUTONOMOUS_INTERVAL", "300"))

# Máximo de investigaciones por día
MAX_RESEARCH_PER_DAY = int(os.environ.get("EIDOS_AUTONOMOUS_MAX_DAY", "30"))

# Dominios de conocimiento que EIDOS puede explorar
RESEARCH_DOMAINS = [
    "comandos linux",
    "herramientas de seguridad",
    "protocolos de red",
    "lenguajes de programación",
    "sistemas de archivos",
    "contenedores y virtualización",
    "criptografía",
    "bases de datos",
    "servidores web",
    "monitorización y logging",
    "automatización y CI/CD",
    "forense digital",
    "redes inalámbricas",
    "firewalls y defensa",
    "ingeniería inversa",
]

# ── Browser (sin límites, con credenciales de SER) ───────────────────────────
# EIDOS usa el perfil real de Firefox de SER para navegar a CUALQUIER sitio,
# con sus contraseñas, sesiones, y cuentas ya logueadas.
# Sin whitelist — SER confía en EIDOS plenamente.
#
# La navegación real consume ~1-2GB RAM (Playwright + Firefox).
# Se usa como último recurso en la cascada (capa 4), después de research_now y cloud APIs.


# ── Topic sources ─────────────────────────────────────────────────────────────

def _is_valid_topic(topic: str) -> bool:
    """Filtra basura del grafo: IDs internos, código, metadatos."""
    if not topic or len(topic) < 3 or len(topic) > 60:
        return False
    topic_lower = topic.lower().strip()
    # Rechazar: prefijos internos conocidos
    garbage_prefixes = [
        "libre:", "colony_", "eidos_", "curiosity:", "domain_idx",
        "gfy:", "todo:", "fixme:", "test:", "debug:",
        "pc_explorer:", "self:", "sistema:", "investigué", "investigado:",
        "outputs", "por lo que", "batería de", "conocimiento libre",
    ]
    for prefix in garbage_prefixes:
        if topic_lower.startswith(prefix):
            return False
    # Rechazar: URLs, paths
    if "://" in topic or "/" in topic or "\\" in topic:
        return False
    # Rechazar: código (funciones, clases, variables)
    if "(" in topic or ")" in topic:
        return False
    if topic.startswith("_"):  # _private_function
        return False
    if ":" in topic and not topic[0].isalpha():  # :internal:tag
        return False
    # Rechazar: conceptos con muchos guiones bajos (snake_case código)
    underscore_count = topic.count("_")
    if underscore_count >= 2:
        return False
    # Rechazar: si tiene formato de log/commit
    if re.match(r"^[a-f0-9]{7,}", topic_lower):  # git hashes
        return False
    # Rechazar: demasiado cortos o solo números
    if len(topic) < 3:
        return False
    if topic.replace(".", "").replace("-", "").isdigit():
        return False
    # Debe tener al menos 60% letras
    alpha_ratio = sum(1 for c in topic if c.isalpha() or c.isspace()) / len(topic)
    if alpha_ratio < 0.6:
        return False
    return True


def _get_graph_gaps(limit: int = 10) -> List[str]:
    """Conceptos en el grafo con definición pobre o nula."""
    try:
        if not BRAIN_DB.exists():
            return []
        conn = get_conn(BRAIN_DB, timeout=30)
        rows = conn.execute(
            "SELECT concept FROM knowledge_nodes "
            "WHERE (definition IS NULL OR LENGTH(definition) < 80) "
            "AND confidence < 0.7 "
            "ORDER BY last_used DESC LIMIT ?",
            (limit * 3,)  # pedir más para compensar filtrado
        ).fetchall()
        topics = []
        for (concept,) in rows:
            if _is_valid_topic(concept):
                topics.append(concept)
                if len(topics) >= limit:
                    break
        return topics
    except Exception as e:
        log.debug("get_graph_gaps: %s", e)
        return []


def _get_orphan_concepts(limit: int = 10) -> List[str]:
    """Conceptos en el grafo sin aristas entrantes (huérfanos)."""
    try:
        if not BRAIN_DB.exists():
            return []
        conn = get_conn(BRAIN_DB, timeout=30)
        rows = conn.execute(
            "SELECT n.concept FROM knowledge_nodes n "
            "LEFT JOIN knowledge_edges e ON n.id = e.to_node "
            "WHERE e.to_node IS NULL "
            "AND LENGTH(n.concept) > 3 "
            "ORDER BY n.last_used DESC LIMIT ?",
            (limit * 3,)
        ).fetchall()
        topics = []
        for (concept,) in rows:
            if _is_valid_topic(concept) and concept.lower() not in topics:
                topics.append(concept.lower())
                if len(topics) >= limit:
                    break
        return topics
    except Exception as e:
        log.debug("get_orphan_concepts: %s", e)
        return []


def _get_learned_topics_for_expansion(limit: int = 5) -> List[str]:
    """Temas recién aprendidos — generar preguntas derivadas."""
    try:
        if not BRAIN_DB.exists():
            return []
        conn = get_conn(BRAIN_DB, timeout=30)
        rows = conn.execute(
            "SELECT fact_key FROM logic_learned_facts "
            "ORDER BY rowid DESC LIMIT ?",
            (limit * 3,)
        ).fetchall()
        # Extraer sujetos únicos de los fact_keys
        # Filtrar palabras genéricas españolas que no son temas técnicos
        generic_words = {
            "sistema", "plataforma", "herramienta", "protocolo", "servicio",
            "datos", "archivo", "código", "lenguaje", "motor", "interfaz",
            "proceso", "usuario", "red", "acceso", "control", "gestión",
            "módulo", "función", "método", "clase", "objeto", "tipo",
            "forma", "manera", "nivel", "caso", "través",
        }
        seen = set()
        topics = []
        for (key,) in rows:
            if not key:
                continue
            # fact_key es tipo "ssh es_protocolo" → extraer sujeto
            parts = key.split()
            if parts:
                subject = parts[0].strip().lower()
                if (len(subject) > 3 and subject not in seen
                        and subject not in generic_words
                        and not subject.startswith("_")):
                    seen.add(subject)
                    topics.append(subject)
                    if len(topics) >= limit:
                        break
        return topics
    except Exception as e:
        log.debug("get_learned_topics: %s", e)
        return []


# ── Research cascade ──────────────────────────────────────────────────────────

def _research_local(concept: str) -> Optional[str]:
    """Investiga usando canales locales SIN internet (man, apt, tldr, whatis)."""
    try:
        from core.eidos_active_research import research_now
        result = research_now(concept)
        if result.get("learned") and result.get("definition"):
            channel = result.get("channel", "unknown")
            definition = result["definition"]
            if len(definition) > 40:
                log.info("autoresearch: '%s' → %s (%s)", concept, channel,
                         definition[:80])
                return f"[{channel}] {definition}"
    except Exception as e:
        log.debug("research_local error: %s", e)
    return None


def _research_cloud(question: str) -> Optional[Tuple[str, str]]:
    """Investiga usando APIs cloud (Groq → DeepSeek)."""
    try:
        from core.eidos_learn import ask_llm
        answer, source = ask_llm(question, timeout=60)
        if answer and len(answer) > 30:
            log.info("autoresearch: '%s' → %s", question[:60], source)
            return answer, source
    except Exception as e:
        log.debug("research_cloud error: %s", e)
    return None


def _research_browser(topic: str) -> Optional[str]:
    """Investiga con navegador REAL (Playwright + Firefox con perfil de SER).

    Último recurso en la cascada. Lento (~10-30s), consume ~1-2GB RAM.
    Pero puede acceder a CUALQUIER sitio con las credenciales de SER.
    """
    # Intento 1: search_and_learn() - busca en DuckDuckGo, visita resultados
    try:
        from core.browser_manager import search_and_learn
        result = search_and_learn(topic, max_results=2)
        if result.get("ok") and result.get("results"):
            texts = []
            for r in result["results"]:
                content = r.get("content", "")
                if content and len(content) > 80:
                    texts.append(f"[{r.get('url', '?')[:60]}]\n{content[:800]}")
            if texts:
                combined = "\n\n".join(texts)
                log.info("autoresearch: browser search '%s' → %d resultados (%d chars)",
                         topic, len(texts), len(combined))
                return f"[browser_search] {combined}"
    except Exception as e:
        log.debug("research_browser search: %s", e)

    # Intento 2: Navegar directamente a Wikipedia con el perfil de SER
    try:
        from core.browser_manager import navigate
        import urllib.parse
        wiki_url = f"https://es.wikipedia.org/wiki/{urllib.parse.quote(topic.replace(' ', '_'))}"
        page = navigate(wiki_url, extract_text=True)
        if page.get("ok") and page.get("text"):
            text = page["text"][:2000]
            if len(text) > 100:
                log.info("autoresearch: browser wikipedia '%s' → %d chars",
                         topic, len(text))
                return f"[browser_wikipedia] {text}"
    except Exception as e:
        log.debug("research_browser wikipedia: %s", e)

    # Intento 3: Navegar con sesión de SER (Firefox real, credenciales)
    try:
        from core.browser_manager import navigate_with_session
        import urllib.parse
        search_url = f"https://duckduckgo.com/?q={urllib.parse.quote(topic)}&ia=web"
        page = navigate_with_session(search_url, extract_text=True)
        if page.get("ok") and page.get("text"):
            text = page["text"][:2000]
            if len(text) > 100:
                log.info("autoresearch: browser session '%s' → %d chars",
                         topic, len(text))
                return f"[browser_session] {text}"
    except Exception as e:
        log.debug("research_browser session: %s", e)

    return None


# ── Fact extraction ───────────────────────────────────────────────────────────

def _extract_and_learn(text: str, topic: str) -> int:
    """Extrae hechos del texto investigado y los pasa al motor lógico.

    S119 #242: Integración con ContinuousLearner para extracción enriquecida.
    Dos capas: lógica (hechos atómicos) + continuous_learner (conceptos, tecnologías).
    """
    learned = 0
    try:
        from core.eidos_logic import get_logic_reasoner
        logic = get_logic_reasoner()
        # Asegurar que está cargado
        if len(logic._concept_index) < 100:
            logic.load_from_graph(max_nodes=5000)
            logic.load_edges(max_edges=5000)
            logic.load_seed_facts()
            logic._load_learned_facts()
        learned = logic.learn_from_text(text, subject_hint=topic, confidence=0.65)
        if learned:
            log.info("autoresearch: %d hechos extraídos de texto (%d chars)",
                     learned, len(text))

        # ── S119 #242: Capa enriquecida vía ContinuousLearner ──────────
        # Extrae conceptos, tecnologías, comandos y código del texto.
        # Esta info enriquece el grafo más allá de los hechos atómicos.
        try:
            from core.continuous_learner import get_continuous_learner
            cl = get_continuous_learner()
            enriched = cl._extract_knowledge_from_text(
                text, source=f"autoresearch:{topic}",
                metadata={"topic": topic, "chars": len(text)}
            )
            # Inyectar conceptos extraídos como nodos en el grafo
            concepts_added = 0
            for concept in enriched.get("concepts", [])[:5]:
                c_name = concept if isinstance(concept, str) else concept.get("name", str(concept))
                if len(c_name) > 3:
                    try:
                        from core.eidos_logic import TruthValue
                        fact_key = f"{topic.lower()} relacionado_con {c_name.lower()}"
                        if fact_key not in logic.facts:
                            logic.facts[fact_key] = TruthValue(0.55, 0.75)
                            concepts_added += 1
                    except Exception:
                        pass
            # Inyectar tecnologías
            for tech in enriched.get("technologies", [])[:5]:
                t_name = tech if isinstance(tech, str) else tech.get("name", str(tech))
                if len(t_name) > 2:
                    try:
                        from core.eidos_logic import TruthValue
                        fact_key = f"{topic.lower()} usa_tecnologia {t_name.lower()}"
                        if fact_key not in logic.facts:
                            logic.facts[fact_key] = TruthValue(0.55, 0.75)
                            concepts_added += 1
                    except Exception:
                        pass
            if concepts_added:
                log.info("autoresearch #242: %d conceptos extraídos vía ContinuousLearner",
                         concepts_added)
                learned += concepts_added
        except ImportError:
            log.debug("ContinuousLearner no disponible — solo extracción lógica")
        except Exception as e:
            log.debug("ContinuousLearner enrichment error: %s", e)

    except Exception as e:
        log.debug("extract_and_learn error: %s", e)
    return learned


# ── Main loop ─────────────────────────────────────────────────────────────────

class AutonomousResearchLoop:
    """Orquestador de investigación autónoma.

    Conecta research_now() + cloud APIs + lógica en un ciclo continuo.
    """

    def __init__(self, interval: int = DEFAULT_INTERVAL):
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._stats: Dict[str, Any] = self._load_stats()
        self._researched_today: set = self._load_todays_topics()
        self._current_topic: str = ""

    # ── Stats ────────────────────────────────────────────────────────────

    def _load_stats(self) -> Dict[str, Any]:
        if STATS_FILE.exists():
            try:
                return json.loads(STATS_FILE.read_text())
            except Exception:
                pass
        return {
            "total_cycles": 0,
            "total_researched": 0,
            "total_facts_learned": 0,
            "by_source": {"local": 0, "groq": 0, "deepseek": 0, "ollama": 0, "web": 0},
            "started_at": None,
            "last_cycle_at": None,
        }

    def _save_stats(self):
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(self._stats, indent=2))

    def _load_todays_topics(self) -> set:
        """Carga los topics ya investigados hoy para no repetir."""
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            if LEARNING_LOG.exists():
                text = LEARNING_LOG.read_text(encoding="utf-8")
                topics = set()
                for line in text.splitlines():
                    if line.startswith(f"### {today}") or today in line:
                        m = re.search(r"Tema:\s*(.+)$", line)
                        if m:
                            topics.add(m.group(1).strip().lower())
                return topics
        except Exception:
            pass
        return set()

    # ── Topic selection ──────────────────────────────────────────────────

    def _choose_topic(self) -> Optional[str]:
        """Elige el próximo tema a investigar por prioridad.

        Prioridades:
          1. Temas derivados de aprendizajes recientes (expansión natural)
          2. Dominio de investigación (rotación sistemática)
          3. Gaps en el grafo (conceptos con definición pobre)
          4. Conceptos huérfanos (sin conexiones)
        """
        # 1. Temas derivados de aprendizajes recientes
        derived = _get_learned_topics_for_expansion(limit=5)
        for topic in derived:
            if topic.lower() not in self._researched_today:
                self._current_topic = topic
                log.debug("choose_topic: derived → %s", topic)
                return topic

        # 2. Dominio de investigación sistemática
        idx = self._stats["total_cycles"] % len(RESEARCH_DOMAINS)
        domain = RESEARCH_DOMAINS[idx]
        if domain.lower() not in self._researched_today:
            self._current_topic = domain
            log.debug("choose_topic: domain → %s", domain)
            return domain

        # 3. Gaps en el grafo
        gaps = _get_graph_gaps(limit=10)
        for gap in gaps:
            if gap.lower() not in self._researched_today:
                self._current_topic = gap
                log.debug("choose_topic: gap → %s", gap)
                return gap

        # 4. Conceptos huérfanos
        orphans = _get_orphan_concepts(limit=10)
        for orphan in orphans:
            if orphan.lower() not in self._researched_today:
                self._current_topic = orphan
                log.debug("choose_topic: orphan → %s", orphan)
                return orphan

        # 5. Fallback: siguiente dominio aunque ya se haya investigado
        self._current_topic = domain
        return domain

    # ── Research cascade ─────────────────────────────────────────────────

    def _research_cascade(self, topic: str) -> Dict[str, Any]:
        """Investiga un tema usando la estrategia en cascada más barata/eficaz.

        Returns:
            Dict con: topic, definition, source, facts_learned, elapsed_s
        """
        t0 = time.time()
        result = {
            "topic": topic,
            "definition": "",
            "source": "none",
            "facts_learned": 0,
            "elapsed_s": 0,
        }

        # ── Capa 1: research_now() — canales locales + web gratuitos ─────
        definition = _research_local(topic)
        if definition and len(definition) > 60:
            result["definition"] = definition
            result["source"] = "local"
            result["elapsed_s"] = round(time.time() - t0, 2)
            self._stats["by_source"]["local"] += 1
            return result

        # ── Capa 2-3: Cloud APIs ─────────────────────────────────────────
        cloud_result = _research_cloud(
            f"¿Qué es {topic}? Explica qué es, para qué sirve, "
            f"y da ejemplos concretos. Responde en español."
        )
        if cloud_result:
            answer, source = cloud_result
            result["definition"] = answer
            result["source"] = source
            result["elapsed_s"] = round(time.time() - t0, 2)
            self._stats["by_source"][source] = self._stats["by_source"].get(source, 0) + 1
            return result

        # ── Capa 4: Navegador web ───────────────────────────────────────
        web_result = _research_browser(topic)
        if web_result:
            result["definition"] = web_result
            result["source"] = "web"
            result["elapsed_s"] = round(time.time() - t0, 2)
            self._stats["by_source"]["web"] += 1
            return result

        result["elapsed_s"] = round(time.time() - t0, 2)
        return result

    # ── Cycle ────────────────────────────────────────────────────────────

    def cycle(self) -> Dict[str, Any]:
        """Una iteración completa del ciclo de investigación autónoma."""
        t0 = time.time()
        self._stats["total_cycles"] += 1

        # 1. Elegir tema
        topic = self._choose_topic()
        if not topic:
            log.debug("autoresearch: no topic found")
            return {"ok": False, "reason": "no_topic"}

        # Verificar límite diario
        if len(self._researched_today) >= MAX_RESEARCH_PER_DAY:
            log.info("autoresearch: límite diario alcanzado (%d)", MAX_RESEARCH_PER_DAY)
            return {"ok": False, "reason": "daily_limit"}

        # 2. Investigar
        result = self._research_cascade(topic)
        definition = result.get("definition", "")

        # 3. Extraer hechos
        facts = 0
        if definition and len(definition) > 60:
            facts = _extract_and_learn(definition, topic)
            result["facts_learned"] = facts
            self._stats["total_facts_learned"] += facts

        # 4. Marcar como investigado
        self._researched_today.add(topic.lower())
        self._stats["total_researched"] += 1
        self._stats["last_cycle_at"] = datetime.now().isoformat()
        result["ok"] = True

        # 5. Log
        elapsed = time.time() - t0
        log.info(
            "autoresearch cycle #%d: '%s' → %s (%d facts, %.1fs)",
            self._stats["total_cycles"], topic[:50],
            result.get("source", "?"), facts, elapsed,
        )

        # 6. Guardar en learning log
        self._log_learning(topic, result)

        # 7. Persistir stats
        self._save_stats()

        return result

    def _log_learning(self, topic: str, result: Dict[str, Any]):
        """Registra lo aprendido en el log de autonomía."""
        try:
            LEARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = (
                f"### {timestamp}\n"
                f"**Tema:** {topic}\n"
                f"**Fuente:** {result.get('source', '?')}\n"
                f"**Hechos extraídos:** {result.get('facts_learned', 0)}\n"
                f"**Tiempo:** {result.get('elapsed_s', 0)}s\n"
                f"**Definición:** {result.get('definition', '')[:500]}\n\n"
                f"---\n"
            )
            with open(LEARNING_LOG, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            log.debug("log_learning: %s", e)

    # ── Background loop ──────────────────────────────────────────────────

    def start(self, interval: Optional[int] = None) -> None:
        """Inicia el ciclo autónomo en background."""
        if self._thread and self._thread.is_alive():
            log.warning("autoresearch: already running")
            return

        if interval is not None:
            self.interval = interval

        self._running = True
        self._stop_event.clear()
        self._stats["started_at"] = datetime.now().isoformat()
        self._save_stats()

        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name="eidos-autoresearch",
        )
        self._thread.start()
        log.info("autoresearch: iniciado (intervalo=%ds, max=%d/día)",
                 self.interval, MAX_RESEARCH_PER_DAY)

    def stop(self) -> None:
        """Detiene el ciclo autónomo."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._save_stats()
        log.info("autoresearch: detenido (%d ciclos, %d investigaciones, %d hechos)",
                 self._stats["total_cycles"], self._stats["total_researched"],
                 self._stats["total_facts_learned"])

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict[str, Any]:
        s = dict(self._stats)
        s["running"] = self.is_running()
        s["researched_today"] = len(self._researched_today)
        s["current_topic"] = self._current_topic
        return s

    def _loop(self) -> None:
        """Loop principal en background."""
        # Esperar 30s antes del primer ciclo (dar tiempo a arrancar)
        if self._stop_event.wait(30):
            return

        while self._running and not self._stop_event.is_set():
            try:
                self.cycle()
            except Exception as e:
                log.exception("autoresearch cycle error: %s", e)
            # Esperar hasta el próximo ciclo (con posibilidad de cancelación)
            if self._stop_event.wait(self.interval):
                break

    def force_cycle(self, topic: Optional[str] = None) -> Dict[str, Any]:
        """Ejecuta un ciclo forzado sobre un tema específico (o auto-detectado).

        Útil para testing y para comandos manuales de SER.
        """
        if topic:
            self._current_topic = topic
            result = self._research_cascade(topic)
            definition = result.get("definition", "")
            if definition:
                facts = _extract_and_learn(definition, topic)
                result["facts_learned"] = facts
                self._stats["total_facts_learned"] += facts
                self._log_learning(topic, result)
            result["ok"] = bool(definition)
            self._save_stats()
            return result
        else:
            return self.cycle()


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: Optional[AutonomousResearchLoop] = None
_lock = threading.Lock()


def get_autonomous_loop(interval: int = DEFAULT_INTERVAL) -> AutonomousResearchLoop:
    """Singleton del orquestador autónomo."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = AutonomousResearchLoop(interval=interval)
    return _instance


def run_forever(interval: int = DEFAULT_INTERVAL):
    """Entry point para systemd: ejecuta el ciclo para siempre."""
    import signal
    loop = get_autonomous_loop(interval=interval)
    loop.start()

    def _stop(sig, frame):
        loop.stop()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Mantener vivo
    while loop.is_running():
        time.sleep(10)


# ── CLI rápido ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    loop = get_autonomous_loop()

    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        run_forever()
    elif len(sys.argv) > 1 and sys.argv[1] == "--stats":
        import json
        print(json.dumps(loop.get_stats(), indent=2, ensure_ascii=False))
    elif len(sys.argv) > 1:
        # Investigar un tema específico
        topic = " ".join(sys.argv[1:])
        print(f"Investigando: {topic}")
        result = loop.force_cycle(topic)
        print(f"Fuente: {result.get('source')}")
        print(f"Hechos: {result.get('facts_learned')}")
        print(f"Tiempo: {result.get('elapsed_s')}s")
        print(f"Definición: {result.get('definition', '')[:500]}")
    else:
        # Un solo ciclo automático
        print("Ciclo automático...")
        result = loop.cycle()
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
