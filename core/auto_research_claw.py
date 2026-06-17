"""
EIDOS core/auto_research_claw.py — Pipeline de investigación autónoma
======================================================================
Adaptación de AutoResearchClaw para Ollama local.

Cuando EIDOS tiene curiosidad (eidos_desires.py), puede investigar un tema
de forma autónoma: busca en su knowledge base, sintetiza, y produce un
informe que enriquece knowledge_graph.db.

Pipeline simplificado (5 etapas, basado en AutoResearchClaw):
  1. TOPIC_INIT    — procesar tema, extraer keywords
  2. KNOWLEDGE_SCAN — escanear knowledge_nodes propio
  3. SYNTHESIS     — sintetizar con Ollama
  4. INSIGHTS      — extraer insights accionables
  5. ARCHIVE       — guardar en knowledge_graph + chronicle

Uso:
    from core.auto_research_claw import get_research_claw
    result = get_research_claw().research("¿Cómo mejorar el independence score?")
    print(result["report"])
"""
from __future__ import annotations

import sqlite3
import time
import threading
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from core.db import get_conn

log = logging.getLogger("eidos.auto_research_claw")

BRAIN_DB  = Path.home() / ".eidos" / "evolution_brain.db"
GRAPH_DB  = Path.home() / ".eidos" / "knowledge_graph.db"
RESEARCH_DB = Path.home() / ".eidos" / "research.db"


class ResearchPipeline:
    """
    Pipeline de investigación autónoma de 5 etapas.
    Funciona 100% local con Ollama.
    """

    def __init__(self, model: str = "lfm2.5-thinking:1.2b"):
        self.model = model
        self._init_db()

    def _init_db(self) -> None:
        try:
            RESEARCH_DB.parent.mkdir(parents=True, exist_ok=True)
            conn = get_conn(RESEARCH_DB)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic       TEXT,
                    status      TEXT DEFAULT 'pending',
                    stage       TEXT DEFAULT 'TOPIC_INIT',
                    report      TEXT,
                    insights    TEXT,
                    node_count  INTEGER DEFAULT 0,
                    started_at  REAL,
                    finished_at REAL
                )
            """)
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception as e:
            log.warning("ResearchPipeline DB init: %s", e)

    def research(self, topic: str, max_nodes: int = 20) -> Dict[str, Any]:
        """
        Ejecuta el pipeline completo de investigación.

        Args:
            topic:     Tema a investigar
            max_nodes: Máximo nodos de knowledge a escanear

        Returns:
            Dict con: report, insights, nodes_created, elapsed_sec
        """
        run_id = self._start_run(topic)
        t0 = time.time()

        try:
            # ── Etapa 1: TOPIC_INIT ──────────────────────────────────────
            self._update_stage(run_id, "TOPIC_INIT")
            keywords = self._extract_keywords(topic)
            log.info("[Research] Topic: '%s' | Keywords: %s", topic[:50], keywords)

            # ── Etapa 2: KNOWLEDGE_SCAN ──────────────────────────────────
            self._update_stage(run_id, "KNOWLEDGE_SCAN")
            existing = self._scan_knowledge(keywords, max_nodes)
            log.info("[Research] Encontrados %d nodos relevantes", len(existing))

            # ── Etapa 3: SYNTHESIS ───────────────────────────────────────
            self._update_stage(run_id, "SYNTHESIS")
            synthesis = self._synthesize(topic, existing)
            if not synthesis:
                synthesis = f"No hay suficiente conocimiento sobre '{topic}'. Necesito más experiencias."

            # ── Etapa 4: INSIGHTS ────────────────────────────────────────
            self._update_stage(run_id, "INSIGHTS")
            insights = self._extract_insights(topic, synthesis)

            # ── Etapa 5: ARCHIVE ─────────────────────────────────────────
            self._update_stage(run_id, "ARCHIVE")
            nodes_created = self._archive(topic, synthesis, insights)

            elapsed = time.time() - t0
            report  = self._format_report(topic, existing, synthesis, insights, elapsed)

            self._finish_run(run_id, report, insights, nodes_created)

            return {
                "run_id":        run_id,
                "topic":         topic,
                "report":        report,
                "insights":      insights,
                "nodes_created": nodes_created,
                "nodes_scanned": len(existing),
                "elapsed_sec":   round(elapsed, 1),
                "success":       True,
            }

        except Exception as e:
            log.exception("Research pipeline failed: %s", e)
            self._finish_run(run_id, f"Error: {e}", [], 0, status="failed")
            return {"success": False, "error": str(e), "topic": topic}

    def get_recent_runs(self, n: int = 5) -> List[Dict]:
        try:
            conn = get_conn(RESEARCH_DB, timeout=3)
            rows = conn.execute(
                "SELECT id, topic, status, node_count, started_at FROM research_runs "
                "ORDER BY started_at DESC LIMIT ?", (n,)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return [{"id": r[0], "topic": r[1], "status": r[2],
                     "nodes": r[3], "ts": r[4]} for r in rows]
        except Exception:
            return []

    # ── Métodos de etapa ──────────────────────────────────────────────────

    def _extract_keywords(self, topic: str) -> List[str]:
        """Extrae keywords del topic sin LLM (rápido)."""
        stop = {'el','la','los','las','un','una','es','son','de','del','al','y','o',
                'para','por','que','con','en','a','como','si','se','me','te','lo',
                'the','a','an','is','are','of','to','for','with','how','what','why'}
        words = [w.lower().strip('.,;:!?()[]"\'') for w in topic.split()]
        return [w for w in words if len(w) > 3 and w not in stop][:6]

    def _scan_knowledge(self, keywords: List[str], limit: int) -> List[Dict]:
        """Busca en knowledge_nodes los conceptos más relevantes."""
        results = []
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            seen = set()
            for kw in keywords:
                rows = conn.execute(
                    "SELECT concept, definition, confidence FROM knowledge_nodes "
                    "WHERE concept LIKE ? OR definition LIKE ? "
                    "ORDER BY confidence DESC, usage_count DESC LIMIT ?",
                    (f"%{kw}%", f"%{kw}%", limit // len(keywords) + 2)
                ).fetchall()
                for concept, definition, conf in rows:
                    if concept not in seen and len(results) < limit:
                        seen.add(concept)
                        results.append({"concept": concept,
                                        "definition": definition[:200],
                                        "confidence": conf})
            pass  # S109: get_conn no necesita close()
        except Exception as e:
            log.debug("_scan_knowledge: %s", e)
        return results

    def _synthesize(self, topic: str, existing: List[Dict]) -> str:
        """Usa Ollama para sintetizar conocimiento sobre el topic."""
        try:
            from core.ollama_fallback import is_ollama_available
            if not is_ollama_available():
                if existing:
                    return "Síntesis offline:\n" + "\n".join(
                        f"- {n['concept']}: {n['definition'][:100]}" for n in existing[:5]
                    )
                return ""

            import requests
            context = "\n".join(
                f"- {n['concept']}: {n['definition'][:150]}"
                for n in existing[:10]
            ) if existing else "(sin contexto previo)"

            prompt = (
                f"Investiga el siguiente tema usando el conocimiento disponible.\n\n"
                f"TEMA: {topic}\n\n"
                f"CONOCIMIENTO DISPONIBLE:\n{context}\n\n"
                f"Escribe un análisis conciso (3-5 párrafos) que:\n"
                f"1. Explique qué sabes sobre el tema\n"
                f"2. Identifique brechas o incertidumbres\n"
                f"3. Proponga líneas de exploración\n"
                f"Responde directamente, sin saludos."
            )
            r = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "Eres un investigador autónomo de EIDOS. Analiza y sintetiza conocimiento."},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"num_predict": 800, "temperature": 0.7},
                },
                timeout=120,
            )
            if r.status_code == 200:
                return r.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            log.debug("_synthesize: %s", e)
        return ""

    def _extract_insights(self, topic: str, synthesis: str) -> List[str]:
        """Extrae insights accionables del synthesis."""
        try:
            import re
            insights = []
            # Buscar frases que empiezan con verbos de acción
            action_patterns = [
                r'(?:debería|podría|habría que|se puede|es posible|recomiendo|propongo)[^.!?]{10,100}[.!?]',
                r'(?:mejorar|optimizar|implementar|crear|añadir|arreglar|revisar)[^.!?]{10,100}[.!?]',
            ]
            for pattern in action_patterns:
                matches = re.findall(pattern, synthesis.lower())
                for m in matches[:3]:
                    insights.append(m.strip().capitalize())
            # Fallback: primeras 3 frases del synthesis
            if not insights:
                sentences = [s.strip() for s in re.split(r'[.!?]', synthesis) if len(s.strip()) > 30]
                insights = sentences[:3]
            return insights[:5]
        except Exception:
            return []

    def _archive(self, topic: str, synthesis: str, insights: List[str]) -> int:
        """Guarda el conocimiento generado en knowledge_nodes."""
        try:
            from core.eidos_evolution_engine import EidosEvolutionEngine
            eng = EidosEvolutionEngine.__new__(EidosEvolutionEngine)
            # Inicializar solo lo necesario
            eng.db_path = str(BRAIN_DB)
            extracted = eng._extract_knowledge_from_text(synthesis)
            if not extracted:
                return 0

            import hashlib as _hs
            conn = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            count = 0
            for concept, definition in list(extracted.items())[:15]:
                node_id = _hs.md5(concept.encode()).hexdigest()[:16]
                existing = conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE concept=?", (concept,)
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO knowledge_nodes "
                        "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (node_id, concept[:100], definition[:500],
                         f"research:{topic[:50]}", 0.75,
                         time.time(), time.time(), 1)
                    )
                    count += 1
                else:
                    conn.execute(
                        "UPDATE knowledge_nodes SET usage_count=usage_count+1, last_used=? WHERE concept=?",
                        (time.time(), concept)
                    )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            # Registrar en Chronicle
            try:
                from core.colony_chronicle import get_chronicle
                get_chronicle().record(
                    "auto_research", "research_complete",
                    f"Investigación sobre '{topic[:60]}' — {count} nodos creados",
                    metadata={"topic": topic[:100], "nodes": count},
                    importance=0.7,
                )
            except Exception:
                pass  # error no crítico, continuar
            return count
        except Exception as e:
            log.debug("_archive: %s", e)
            return 0

    def _format_report(self, topic: str, existing: List[Dict],
                       synthesis: str, insights: List[str], elapsed: float) -> str:
        lines = [
            f"# Investigación: {topic}",
            f"*Tiempo: {elapsed:.1f}s | Nodos escaneados: {len(existing)}*\n",
        ]
        if synthesis:
            lines += ["## Síntesis\n", synthesis, ""]
        if insights:
            lines += ["## Insights accionables\n"]
            lines += [f"- {i}" for i in insights]
            lines.append("")
        if existing:
            lines += ["\n## Conocimiento base utilizado\n"]
            for n in existing[:5]:
                lines.append(f"- **{n['concept']}**: {n['definition'][:100]}...")
        return "\n".join(lines)

    def _start_run(self, topic: str) -> int:
        try:
            conn = get_conn(RESEARCH_DB)
            conn.execute("PRAGMA journal_mode=WAL")
            cur = conn.execute(
                "INSERT INTO research_runs (topic, status, started_at) VALUES (?,?,?)",
                (topic, "running", time.time())
            )
            run_id = cur.lastrowid
            conn.commit()
            pass  # S109: get_conn no necesita close()
            return run_id
        except Exception:
            return 0

    def _update_stage(self, run_id: int, stage: str) -> None:
        try:
            conn = get_conn(RESEARCH_DB)
            conn.execute("UPDATE research_runs SET stage=? WHERE id=?", (stage, run_id))
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception:
            pass  # error no crítico, continuar
    def _finish_run(self, run_id: int, report: str, insights: List[str],
                    node_count: int, status: str = "completed") -> None:
        try:
            conn = get_conn(RESEARCH_DB)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE research_runs SET status=?, report=?, insights=?, "
                "node_count=?, finished_at=? WHERE id=?",
                (status, report[:2000], str(insights)[:500], node_count, time.time(), run_id)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception:
            pass  # error no crítico, continuar
_instance: Optional[ResearchPipeline] = None
_lock = threading.Lock()


def get_research_claw(model: str = "lfm2.5-thinking:1.2b") -> ResearchPipeline:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ResearchPipeline(model=model)
    return _instance
