"""
core/self_study.py — Sistema de estudio autónomo para EIDOS

EIDOS detecta gaps de conocimiento y los llena autónomamente:
1. Reasoner identifica temas con pocos nodos (pocas coincidencias)
2. Busca documentación en DuckDuckGo/Google
3. Estudia usando /study del bridge
4. Inyecta nuevo conocimiento en brain.db
5. Reporta progreso via proactive messages

Uso:
    from core.self_study import SelfStudy
    ss = SelfStudy()
    ss.run_cycle()  # Un ciclo de estudio
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.self_study")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
EIDOS_ROOT = Path.home() / "EIDOS"

# Temas prioritarios que EIDOS debería conocer
PRIORITY_TOPICS = [
    "docker", "kubernetes", "nginx", "postgresql", "redis",
    "python asyncio", "fastapi", "pytest", "git advanced",
    "linux systemd", "bash scripting", "networking",
    "machine learning", "neural networks", "transformers",
    "sql", "nosql", "mongodb", "chromadb", "vector database",
    "rest api", "graphql", "websockets", "grpc",
    "ci/cd", "github actions", "docker compose",
    "cybersecurity", "owasp top 10", "penetration testing",
    "blockchain", "smart contracts",
    "eidos architecture", "colony design", "multi-agent systems",
]

# Stopwords for topic extraction
_STOPWORDS = {
    "que", "del", "las", "los", "con", "por", "para", "como",
    "qué", "cómo", "puede", "todo", "esta", "este", "más",
    "eso", "esa", "entre", "pero", "sin", "tiene", "sobre",
    "eres", "what", "does", "the", "and", "for", "are", "not",
    "you", "your", "can", "all", "has", "have", "its", "how",
}


class SelfStudy:
    def __init__(self):
        self._running = False
        self._studied: set = set()
        self._cycle_count = 0

    def detect_gaps(self, n_samples: int = 10) -> List[str]:
        """Detecta gaps de conocimiento: temas con pocos nodos en brain.db."""
        gaps = []
        try:
            conn = get_conn(BRAIN_DB, timeout=3)
            for topic in random.sample(PRIORITY_TOPICS, min(n_samples, len(PRIORITY_TOPICS))):
                main_word = topic.split()[0].lower()
                exact = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE "
                    "concept LIKE ? AND confidence >= 0.3",
                    (f"%{main_word}%",)
                ).fetchone()[0]
                if exact < 2:
                    gaps.append(topic)

        except Exception as e:
            log.error("detect_gaps error: %s", e)
        return gaps

    def search_docs(self, topic: str, max_results: int = 3) -> List[str]:
        """Busca URLs de documentación para un tema usando DuckDuckGo."""
        urls = []
        try:
            query = urllib.parse.quote(f"{topic} documentation tutorial")
            url = f"https://html.duckduckgo.com/html/?q={query}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) EIDOS/1.0",
                    "Accept": "text/html",
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                # Extract URLs from DuckDuckGo results
                for m in re.finditer(
                    r'<a[^>]*class="result__a"[^>]*href="(https?://[^"]+)"',
                    html
                ):
                    url_found = m.group(1)
                    if any(skip in url_found for skip in {"duckduckgo.com", "twitter.com", "facebook.com"}):
                        continue
                    urls.append(url_found)
                    if len(urls) >= max_results:
                        break
        except Exception as e:
            log.debug("search_docs error for %s: %s", topic, e)
        return urls

    def study_topic(self, topic: str, url: str = "") -> Dict[str, Any]:
        """S67: Estudia un tema SIN depender del bridge.

        Estrategia local sin LLM:
        1. Si URL dada → scrapear y extraer conceptos
        2. _self_research multicanal (code/man/help/apt/ddg/wiki)
        3. Probar URLs típicas del topic (docs.X.io, X.io/docs, github/X, wiki)
        4. Persiste nodos en grafo directamente vía SQLite
        """
        if topic in self._studied:
            return {"status": "already_studied", "topic": topic}

        result = {"topic": topic, "status": "pending",
                  "nodes_added": 0, "url": url, "urls_tried": []}

        # Estrategia 1: URL dada → scrapear
        if url:
            scraped = self._scrape_url_local(url)
            if scraped:
                result["nodes_added"] += self._inject_concepts_from_text(
                    topic, scraped, source=f"web:{url[:60]}",
                )
                result["urls_tried"].append(url)

        # Estrategia 2: _self_research multicanal (man/help/apt/ddg/wiki)
        try:
            sys.path.insert(0, str(EIDOS_ROOT))
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            if r._ready and hasattr(r, "_self_research"):
                added = r._self_research(topic)
                result["nodes_added"] += added
        except Exception as e:
            log.debug("self_research falló para %s: %s", topic, e)

        # Estrategia 3: URLs típicas del topic + DDG search
        if result["nodes_added"] < 3:  # más agresivo: si poco aprendido, intentar URLs
            urls_to_try = self._smart_url_guess(topic)
            # Añadir DDG como complemento
            ddg_urls = self.search_docs(topic, max_results=2)
            urls_to_try.extend(ddg_urls)
            for u in urls_to_try[:4]:
                if u in result["urls_tried"]:
                    continue
                result["urls_tried"].append(u)
                scraped = self._scrape_url_local(u)
                if scraped and len(scraped) > 200:
                    added = self._inject_concepts_from_text(
                        topic, scraped, source=f"web:{u[:60]}",
                    )
                    result["nodes_added"] += added
                    if not result["url"]:
                        result["url"] = u
                    if result["nodes_added"] >= 5:
                        break  # ya tenemos bastante

        self._studied.add(topic)
        result["status"] = "studied" if result["nodes_added"] > 0 else "no_content"

        # Notificar via colony_proactive
        try:
            from core.colony_proactive import push_message
            push_message(
                actor="EIDOS",
                message=f"Estudié '{topic}': +{result['nodes_added']} nodos. "
                        f"Fuente: {url or 'self_research'}",
                topic=f"self_study:{topic}",
                priority=5,
            )
        except Exception:
            pass

        return result

    def _smart_url_guess(self, topic: str) -> List[str]:
        """Genera URLs típicas para un topic (docs, github, wiki, etc.)."""
        t = topic.lower().strip().replace(" ", "-")
        urls = []
        # Docs oficiales más comunes
        urls.append(f"https://docs.{t}.io/")
        urls.append(f"https://{t}.io/docs/")
        urls.append(f"https://{t}.io/")
        urls.append(f"https://docs.{t}.com/")
        urls.append(f"https://{t}.readthedocs.io/")
        # GitHub README
        urls.append(f"https://github.com/{t}/{t}")
        urls.append(f"https://raw.githubusercontent.com/{t}/{t}/main/README.md")
        # Wikipedia
        urls.append(f"https://en.wikipedia.org/wiki/{topic.replace(' ', '_')}")
        urls.append(f"https://es.wikipedia.org/wiki/{topic.replace(' ', '_')}")
        # MDN para tecnologías web
        urls.append(f"https://developer.mozilla.org/en-US/docs/Web/{topic}")
        return urls

    def _scrape_url_local(self, url: str, max_chars: int = 4000) -> str:
        """Descarga URL y devuelve texto limpio (html2text)."""
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) EIDOS/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            try:
                import html2text
                h = html2text.HTML2Text()
                h.ignore_links = True
                h.ignore_images = True
                text = h.handle(html)
            except ImportError:
                # Fallback: strip básico
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text)
            return text[:max_chars]
        except Exception as e:
            log.debug("scrape %s: %s", url, e)
            return ""

    def _inject_concepts_from_text(self, topic: str, text: str,
                                     source: str = "self_study") -> int:
        """Extrae conceptos del texto (h1/h2/h3/palabras clave) e inyecta nodos al grafo.
        Retorna nº de nodos añadidos."""
        if not text or len(text) < 50:
            return 0
        added = 0
        from datetime import datetime as _dt
        try:
            conn = get_conn(BRAIN_DB, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")

            # Nodo del topic principal con resumen del texto
            summary = text[:500].replace("\n", " ").strip()
            existing = conn.execute(
                "SELECT id FROM knowledge_nodes WHERE concept=?", (topic,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE knowledge_nodes SET definition=?, source=?, "
                    "confidence=MAX(confidence, 0.75) WHERE concept=?",
                    (summary, source, topic),
                )
            else:
                conn.execute(
                    "INSERT INTO knowledge_nodes "
                    "(id, concept, definition, category, source, confidence, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:16], topic, summary, "studied", source,
                     0.75, _dt.now().isoformat()),
                )
                added += 1

            # Extraer headers markdown (h1/h2/h3) como sub-conceptos
            headers = re.findall(r"^#{1,4}\s+(.+?)$", text, re.MULTILINE)
            for h in headers[:15]:
                concept = h.strip().lower()[:80]
                if len(concept) < 3 or len(concept) > 80:
                    continue
                # Limpiar markdown del header
                concept = re.sub(r"[*_`#\[\]]", "", concept).strip()
                if not concept:
                    continue
                exists = conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE concept=?", (concept,)
                ).fetchone()
                if exists:
                    continue
                # Buscar contexto del header (líneas siguientes)
                idx = text.find(h)
                context = text[idx:idx+300].split("\n", 1)[-1][:200].strip()
                conn.execute(
                    "INSERT INTO knowledge_nodes "
                    "(id, concept, definition, category, source, confidence, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:16], concept,
                     f"De {topic}: {context}",
                     "subconcept", source, 0.6, _dt.now().isoformat()),
                )
                added += 1

            conn.commit()

        except Exception as e:
            log.warning("_inject_concepts_from_text fallo: %s", e)
        return added

    def run_cycle(self, max_topics: int = 3) -> Dict[str, Any]:
        """Ejecuta un ciclo completo de auto-estudio."""
        self._cycle_count += 1
        t0 = time.time()
        log.info("=== Ciclo de auto-estudio #%d ===", self._cycle_count)

        # 1. Detectar gaps
        gaps = self.detect_gaps(n_samples=15)
        log.info("Gaps detectados: %d", len(gaps))
        if not gaps:
            return {"status": "no_gaps", "cycle": self._cycle_count, "elapsed_s": 0}

        # 2. Estudiar los primeros N gaps
        to_study = gaps[:max_topics]
        results = []
        for topic in to_study:
            log.info("Estudiando: %s", topic)
            result = self.study_topic(topic)
            results.append(result)
            time.sleep(2)  # Pausa entre estudios

        elapsed = round(time.time() - t0, 1)
        log.info("Ciclo completado: %d temas estudiados en %ss", len(results), elapsed)

        return {
            "status": "ok",
            "cycle": self._cycle_count,
            "gaps_found": len(gaps),
            "studied": len(results),
            "results": results,
            "elapsed_s": elapsed,
        }

    def start_auto_study(self, interval: int = 600):
        """Inicia el estudio autónomo en loop (cada N segundos)."""
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._loop, args=(interval,), daemon=True,
                             name="self-study")
        t.start()
        log.info("Auto-estudio iniciado (cada %ds)", interval)

    def stop(self):
        self._running = False

    def _loop(self, interval: int):
        while self._running:
            try:
                self.run_cycle(max_topics=2)
            except Exception as e:
                log.error("Study loop error: %s", e)
            time.sleep(interval)


# ── Singleton ──
_instance = None
_lock = threading.Lock()


def get_self_study() -> SelfStudy:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = SelfStudy()
    return _instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ss = get_self_study()
    print("Detectando gaps de conocimiento...")
    gaps = ss.detect_gaps(20)
    print(f"Gaps encontrados: {len(gaps)}")
    for g in gaps:
        print(f"  - {g}")
    if gaps:
        print(f"\nEstudiando: {gaps[0]}")
        result = ss.study_topic(gaps[0])
        print(json.dumps(result, indent=2))
