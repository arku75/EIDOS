"""
core/eidos_vivo.py — Núcleo vital de EIDOS

Ciclo mínimo que corre 24/7:
    1. Curiosidad → ¿qué no sé que debería saber?
    2. Aprender → ingesta con filtro de confianza
    3. Consolidar → spreading activation + hebbiano
    4. Dormir 10 min → repetir

Sin dependencias de stubs. Solo lo que funciona.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from core.db import get_conn

log = logging.getLogger("eidos.vivo")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
VIVO_DB = Path.home() / ".eidos" / "vivo.db"
CYCLE_INTERVAL = 600  # 10 minutos entre ciclos
_BRIDGE_KEY = os.environ.get("EIDOS_BRIDGE_KEY", "")
_BRIDGE_HEADERS = {"Content-Type": "application/json"}
if _BRIDGE_KEY:
    _BRIDGE_HEADERS["X-API-Key"] = _BRIDGE_KEY

_TOPICS_TO_EXPLORE = [
    "python", "docker", "linux", "git", "sql", "api", "rest", "graphql",
    "machine learning", "neural networks", "nlp", "deep learning",
    "devops", "ci/cd", "kubernetes", "terraform",
    "databases", "postgresql", "mongodb", "redis", "nosql",
    "security", "authentication", "encryption",
    "web development", "fastapi", "flask",
    "automation", "n8n", "selenium", "playwright",
    "cloud", "aws", "docker compose",
]

_LEARNED_TOPICS: Set[str] = set()


KILL_SWITCH = Path.home() / ".eidos" / "vivo.stop"
FAILURES_FILE = Path.home() / ".eidos" / "vivo_failures.json"
STATUS_FILE = Path.home() / ".eidos" / "vivo_status.json"  # S67: IPC entre procesos
MAX_FAILURES_PER_DESIRE = 3


class EidosVivo:
    """El latido de EIDOS. Ciclo autónomo de curiosidad-aprendizaje-consolidación.

    S67 hardening:
    - SIGTERM handler con _graceful_shutdown
    - Kill switch ~/.eidos/vivo.stop (touch=stop instantáneo)
    - Anti-bucle: max 3 fallos por deseo → pedir ayuda Telegram
    - Flag _acting=True para evitar recursión visual (otros módulos lo consultan)
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cycle_count = 0
        self._total_learned = 0
        self._total_consolidated = 0
        self._last_cycle: float = 0
        self._errors: List[str] = []
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        # S67 hardening
        self._acting = 0  # S82 fix: contador atómico, no bool (evita race condition con ThreadPoolExecutor)
        self._failures = self._load_failures()  # desire_id → count
        self._opened_processes = []  # subprocess.Popen abiertos para cleanup
        self._init_db()
        self._init_soul_subgraphs()
        self._marocuai = None  # S76: inicializado bajo demanda

    # ── S76 · Marocuai ──────────────────────────────────────────────────────

    def _get_marocuai(self):
        """Obtiene la instancia Marocuai (inicialización lazy)."""
        if self._marocuai is None:
            try:
                from core.eidos_marocuai import get_marocuai
                self._marocuai = get_marocuai()
            except Exception as e:
                log.debug("Marocuai no disponible: %s", e)
                self._marocuai = False  # Marcar como no disponible
        return self._marocuai if self._marocuai is not False else None

    # ── S67 · Hardening helpers ──────────────────────────────────────────────

    def _load_failures(self) -> Dict[str, int]:
        try:
            if FAILURES_FILE.exists():
                return json.loads(FAILURES_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save_failures(self):
        try:
            FAILURES_FILE.parent.mkdir(parents=True, exist_ok=True)
            FAILURES_FILE.write_text(json.dumps(self._failures, indent=2))
        except Exception:
            pass

    def _record_failure(self, desire_id: str, reason: str) -> int:
        """Registra un fallo. Si alcanza MAX_FAILURES_PER_DESIRE, avisa por Telegram."""
        self._failures[desire_id] = self._failures.get(desire_id, 0) + 1
        count = self._failures[desire_id]
        self._save_failures()
        if count >= MAX_FAILURES_PER_DESIRE:
            try:
                from core.telegram_bot import send_proactive_message
                send_proactive_message(
                    f"🆘 He fallado {count} veces con: *{desire_id}*\n"
                    f"Razón: {reason[:200]}\n\n"
                    f"¿Quieres ayudarme? Responde con instrucciones o "
                    f"`olvida {desire_id}` para que lo deje.",
                    priority=8, topic=f"vivo_help:{desire_id}",
                )
            except Exception:
                pass
        return count

    def _reset_failure(self, desire_id: str):
        if desire_id in self._failures:
            del self._failures[desire_id]
            self._save_failures()

    def is_acting(self) -> bool:
        """API pública: otros módulos consultan esto para evitar recursión.
        S82 fix: contador > 0 en lugar de bool para thread-safety con ThreadPoolExecutor."""
        return self._acting > 0

    def _check_kill_switch(self) -> bool:
        """Si existe ~/.eidos/vivo.stop, detiene vivo."""
        if KILL_SWITCH.exists():
            log.warning("Kill switch detectado: %s — deteniendo vivo", KILL_SWITCH)
            self._running = False
            try:
                from core.telegram_bot import send_proactive_message
                send_proactive_message(
                    "🛑 Kill switch activado. Vivo detenido. "
                    f"Borra {KILL_SWITCH} para permitir restart.",
                    priority=9, topic="vivo_stopped",
                )
            except Exception:
                pass
            return True
        return False

    _shutdown_in_progress = False  # class-level guard contra re-entrada

    def _graceful_shutdown(self, signum=None, frame=None):
        """SIGTERM handler. Cierra procesos huérfanos y guarda estado.
        S68-P6: guard contra re-entrada + anti-spam de mensaje (24h cooldown)."""
        # Guard: evitar 2+ ejecuciones simultáneas (causa de spam de 9 msgs)
        if EidosVivo._shutdown_in_progress:
            log.debug("_graceful_shutdown YA EN PROGRESO, saltando re-entrada")
            return
        EidosVivo._shutdown_in_progress = True
        log.info("_graceful_shutdown: signal=%s acting=%s", signum, self._acting)
        self._running = False
        # Cerrar procesos abiertos por vivo (firefox, etc.)
        for p in self._opened_processes:
            try:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except Exception:
                    p.kill()
            except Exception:
                pass
        self._opened_processes.clear()
        self._save_failures()
        # S68-P6: anti-spam — solo mandar Telegram una vez cada 24h
        shutdown_marker = Path.home() / ".eidos" / "last_shutdown_msg.txt"
        should_notify = True
        try:
            if shutdown_marker.exists():
                last_ts = float(shutdown_marker.read_text().strip() or "0")
                if time.time() - last_ts < 86400:  # menos de 24h
                    should_notify = False
                    log.info("Anti-spam: shutdown msg silenciado (último hace %ds)",
                             int(time.time() - last_ts))
        except Exception:
            pass
        if should_notify:
            try:
                from core.telegram_bot import send_proactive_message
                send_proactive_message(
                    f"😴 Vivo se duerme.\n"
                    f"Ciclos: {self._cycle_count} · "
                    f"Aprendidos: {self._total_learned} · "
                    f"Errores: {len(self._errors)}",
                    priority=6, topic="vivo_shutdown", silent=True,
                )
                shutdown_marker.parent.mkdir(parents=True, exist_ok=True)
                shutdown_marker.write_text(str(time.time()))
            except Exception:
                pass
        log.info("_graceful_shutdown completado")

    def _init_soul_subgraphs(self):
        """Crea neuronas en el grafo para cada alma de Colony."""
        try:
            from core.colony_openclaw_souls import OPENCLAW_SOULS
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            if not r._ready:
                return
            for soul_id, soul in OPENCLAW_SOULS.items():
                name = soul.get("name", soul_id)
                specialty = soul.get("specialty", "")
                category = soul.get("category", "general")
                keywords = soul.get("keywords", [])
                r.inject_node(f"soul:{soul_id}", name, {
                    "type": "soul",
                    "category": category,
                    "specialty": specialty,
                    "keywords": ",".join(keywords[:5]),
                })
                for kw in keywords[:3]:
                    kw_lower = kw.lower().strip()
                    if len(kw_lower) > 2:
                        r.inject_edge(name, kw_lower, 0.8)
            log.info("Vivo: %d almas registradas en sub-grafo neuronal", len(OPENCLAW_SOULS))
        except Exception as e:
            log.debug("init_soul_subgraphs: %s", e)

    def _init_db(self):
        VIVO_DB.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = get_conn(VIVO_DB, timeout=5)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vivo_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    cycle INTEGER,
                    phase TEXT,
                    learned INTEGER,
                    consolidated INTEGER,
                    gaps_found INTEGER,
                    curiosity TEXT,
                    errors TEXT
                )
            """)
            conn.commit()

        except Exception as e:
            log.debug("vivo db init: %s", e)

    def _get_knowledge_gaps(self, max_gaps: int = 5) -> List[str]:
        """Detecta qué no sabe usando el grafo neuronal + objetivos + brain."""
        gaps = []

        # 1. Gaps del self-study (temas con pocos nodos)
        try:
            import urllib.request as U
            import json as J
            req = U.Request("http://localhost:8003/self_study/gaps", headers=_BRIDGE_HEADERS)
            resp = U.urlopen(req, timeout=5)
            data = J.loads(resp.read())
            gaps.extend(data.get("gaps", [])[:max_gaps])
        except Exception:
            pass

        # 2. Gaps del grafo neuronal — conceptos con baja conectividad
        if len(gaps) < max_gaps:
            try:
                from core.knowledge_reasoner import get_reasoner
                r = get_reasoner()
                if r._ready and hasattr(r.graph, 'nodes'):
                    # Buscar nodos con menos de 3 conexiones y baja confianza
                    weak_nodes = []
                    for nid, node in r.graph.nodes.items():
                        if node.confidence < 0.5:
                            edge_count = sum(1 for e in r.graph.edges
                                             if e.source_id == nid or e.target_id == nid)
                            if edge_count < 3 and node.concept not in _LEARNED_TOPICS:
                                weak_nodes.append((node.concept, edge_count, node.confidence))
                    weak_nodes.sort(key=lambda x: x[1])
                    for concept, _, _ in weak_nodes[:2]:
                        if concept not in gaps and len(gaps) < max_gaps:
                            gaps.append(concept)
            except Exception:
                pass

        # 3. Objetivos pendientes del sistema de objetivos
        if len(gaps) < max_gaps:
            try:
                from core.objectives_system import get_objectives_manager
                om = get_objectives_manager()
                pending = om.get_pending_objectives()
                for obj in pending[:2]:
                    topic = obj.title.split(':')[0].strip().lower()
                    if topic and topic not in gaps and len(gaps) < max_gaps:
                        gaps.append(topic)
            except Exception:
                pass

        # 4. Metas activas desde goals.py
        if len(gaps) < max_gaps:
            try:
                from core.eidos_goals import get_active_goals
                active = get_active_goals()
                for goal in active[:2]:
                    topic = goal.get('description', '').lower().strip()
                    if topic and topic not in gaps and len(gaps) < max_gaps:
                        gaps.append(topic)
            except Exception:
                pass

        # 5. Fallback: temas de la lista fija que no se han aprendido
        if not gaps:
            topics_pool = [t for t in _TOPICS_TO_EXPLORE if t not in _LEARNED_TOPICS]
            if topics_pool:
                gaps.append(random.choice(topics_pool))

        return gaps[:max_gaps]

    def _curiosity_phase(self) -> List[str]:
        """Fase 1: curiosidad — detecta qué aprender."""
        gaps = self._get_knowledge_gaps(max_gaps=3)
        log.info("Vivo curiosidad: %d gaps encontrados: %s", len(gaps), gaps[:3])
        return gaps

    def _learn_phase(self, topics: List[str]) -> int:
        """Fase 2: aprender — ingesta con filtro de confianza."""
        total = 0
        for topic in topics:
            try:
                import urllib.request as U
                import json as J
                payload = J.dumps({
                    "topic": topic,
                    "depth": 1,
                    "max_pages": 2,
                }).encode()
                req = U.Request(
                    "http://localhost:8003/auto_learn",
                    data=payload,
                    headers=_BRIDGE_HEADERS,
                )
                resp = U.urlopen(req, timeout=45)
                result = J.loads(resp.read())
                injected = result.get("nodes_injected", 0)
                total += injected
                _LEARNED_TOPICS.add(topic.lower())
                if injected > 0:
                    log.info("Vivo aprendió '%s': %d nodos (confianza >= 0.4)", topic, injected)
                else:
                    log.info("Vivo exploró '%s': sin nodos nuevos (ya sabe o baja confianza)", topic)
            except Exception as e:
                self._errors.append(f"learn {topic}: {e}")
                log.debug("Vivo learn error '%s': %s", topic, e)
        return total

    def _consolidate_phase(self) -> int:
        """Fase 3: consolidar — spreading activation + hebbiano + evolve."""
        total = 0
        try:
            # Trigger evolve (inferencia + spreading activation)
            import urllib.request as U
            import json as J
            evolve_req = U.Request(
                "http://localhost:8003/evolve",
                data=J.dumps({}).encode(),
                headers=_BRIDGE_HEADERS,
            )
            resp = U.urlopen(evolve_req, timeout=30)
            result = J.loads(resp.read())
            persisted = result.get("persisted", 0)
            total += persisted
            if persisted > 0:
                log.info("Vivo consolidó: %d nuevas inferencias", persisted)

            # Verificar ChromaDB antes de sync — auto-reparar si está caído
            try:
                req = U.Request("http://localhost:8003/chroma_stats", headers=_BRIDGE_HEADERS)
                c_resp = U.urlopen(req, timeout=5)
                c_data = J.loads(c_resp.read())
                chroma_ready = c_data.get("ready", False)
                if not chroma_ready:
                    log.warning("ChromaDB no ready — forzando re-sync...")
                    sync_req = U.Request(
                        "http://localhost:8003/sync_chroma?force=1",
                        headers=_BRIDGE_HEADERS,
                    )
                    U.urlopen(sync_req, timeout=15)
                    log.info("ChromaDB re-sync forzado")
            except Exception:
                pass

            # Sync ChromaDB
            try:
                sync_req = U.Request(
                    "http://localhost:8003/sync_chroma",
                    data=J.dumps({"force": True}).encode(),
                    headers=_BRIDGE_HEADERS,
                )
                U.urlopen(sync_req, timeout=15)
            except Exception as e:
                log.debug("Vivo sync_chroma: %s", e)

        except Exception as e:
            self._errors.append(f"consolidate: {e}")
            log.debug("Vivo consolidate: %s", e)
        return total

    def _broadcast_cycle(self, learned: int, consolidated: int, gaps: List[str]):
        """Broadcast del ciclo a todos los agentes Colony para que sepan qué aprendió EIDOS."""
        try:
            from core.colony_broadcast import get_broadcast
            bc = get_broadcast()
            topic = gaps[0] if gaps else "general"
            bc.broadcast(
                "eidos_vivo",
                f"Ciclo #{self._cycle_count}: aprendí {learned} nodos nuevos, "
                f"consolidé {consolidated} inferencias. Tema: {topic}",
                msg_type="learning"
            )
            if consolidated > 5:
                from core.colony_proactive import push_message
                push_message(
                    actor="EIDOS",
                    message=f"Acabo de consolidar {consolidated} inferencias nuevas "
                            f"en mi grafo neuronal sobre '{topic}'.",
                    topic=f"vivo:cycle:{self._cycle_count}",
                    priority=4
                )
        except Exception:
            pass

    def _save_cycle(self, phase: str, learned: int, consolidated: int,
                     gaps_found: int, curiosity: str):
        try:
            conn = get_conn(VIVO_DB, timeout=5)
            conn.execute(
                "INSERT INTO vivo_cycles "
                "(timestamp, cycle, phase, learned, consolidated, gaps_found, curiosity, errors) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), self._cycle_count, phase, learned, consolidated,
                 gaps_found, curiosity[:200],
                 json.dumps(self._errors[-5:])),
            )
            conn.commit()

        except Exception:
            pass

    def _perceive_phase(self) -> Dict[str, Any]:
        """Fase 1: percibir — recolectar estado del sistema, gaps, métricas."""
        state = {}
        state["gaps"] = self._curiosity_phase()
        try:
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            if r._ready and hasattr(r.graph, 'nodes'):
                state["graph_nodes"] = len(r.graph.nodes)
                state["graph_edges"] = len(r.graph.edges)
        except Exception:
            pass
        try:
            conn = get_conn(VIVO_DB, timeout=5)
            row = conn.execute("SELECT COUNT(*) FROM vivo_cycles").fetchone()
            state["total_cycles"] = row[0] if row else 0

        except Exception:
            pass
        try:
            from core.objectives_system import get_objectives_manager
            om = get_objectives_manager()
            state["pending_objectives"] = om.get_pending_objectives()
        except Exception:
            pass
        log.info("Vivo percibe: %d gaps, %d ciclos previos",
                 len(state.get("gaps", [])), state.get("total_cycles", 0))
        return state

    def _decide_phase(self, state: Dict[str, Any]) -> List[str]:
        """Fase 2: decidir — activar souls, generar metas desde el grafo."""
        actions = []
        gaps = state.get("gaps", [])
        if gaps:
            # Activar souls relevantes a los gaps detectados
            try:
                from core.colony_openclaw_souls import OPENCLAW_SOULS
                from core.colony_broadcast import get_broadcast
                bc = get_broadcast()
                for gap in gaps[:2]:
                    activated = []
                    for soul_id, soul in OPENCLAW_SOULS.items():
                        keywords = soul.get("keywords", [])
                        if any(k.lower() in gap.lower() for k in keywords):
                            activated.append(soul_id)
                            bc.broadcast(
                                soul_id,
                                f"EIDOS detectó gap en '{gap}' — se requiere tu especialidad: {soul.get('specialty', '')}",
                                msg_type="activation"
                            )
                    if activated:
                        actions.append(f"activar_souls:{','.join(activated)}:gap={gap}")
            except Exception:
                pass
        # S82: Ciclo de pensamiento automático (pensar → refinar → consolidar)
        if self._cycle_count % 6 == 0:  # cada ~1 hora
            try:
                from core.eidos_thoughts import get_thoughts
                thoughts = get_thoughts()
                result = thoughts.auto_think()
                if result.get("status") == "consolidated":
                    log.info("S82 thought: %s → consolidated", result.get("topic", "")[:50])
            except Exception as e:
                log.debug("S82 thoughts error: %s", e)

        # S79: Planificar metas usando el grafo (GraphPlanner)
        try:
            from core.eidos_planner import get_planner
            planner = get_planner()
            for gap in gaps[:1]:  # Planificar solo el primer gap
                plan = planner.plan(gap, max_steps=3)
                if plan["confidence"] > 0.5 and plan["steps"]:
                    actions.append(f"ejecutar_plan:{gap}")
                    log.info("S79 Planner: plan para '%s' con %d pasos (conf=%.2f)",
                             gap, len(plan["steps"]), plan["confidence"])
        except Exception as e:
            log.debug("S79 planner error: %s", e)

        # Generar metas desde el grafo — nodos de baja confianza como objetivos
        try:
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            if r._ready and hasattr(r.graph, 'nodes'):
                low_conf = [(nid, n.concept, n.confidence) for nid, n in r.graph.nodes.items()
                            if n.confidence < 0.3 and n.concept not in _LEARNED_TOPICS][:2]
                for nid, concept, conf in low_conf:
                    actions.append(f"reforzar:{concept}:confianza={conf:.2f}")
        except Exception:
            pass
        if not actions:
            actions.append("explorar:curiosidad_general")
        log.info("Vivo decide: %d acciones", len(actions))
        return actions

    def _act_phase(self, actions: List[str]):
        """Fase 3: actuar — sin bloqueo, sin LLM forzado (S67 fix).

        Cambios respecto al original:
        - skip_knowledge=False → usa cortocircuito neural (grafo primero, no Ollama)
        - Cada acción en thread con timeout 60s → ciclo no se atora
        - Flag _acting=True solo cuando ejecuta acción grande
        """
        import concurrent.futures as _cf

        def _one_action(action: str):
            # S82 fix: contador en lugar de bool → thread-safe con ThreadPoolExecutor
            self._acting += 1
            try:
                if action.startswith("activar_souls:"):
                    parts = action.split(":gap=", 1)
                    topic = parts[1] if len(parts) > 1 else "general"
                    from core.colony_community import get_colony_community
                    colony = get_colony_community()
                    # S82 fix: verificar que Colony tiene sesión activa antes de deliberar
                    if not getattr(colony, '_session_active', False):
                        log.debug("Vivo: Colony no activa, saltando deliberate")
                        return
                    # S67: skip_knowledge=False (cortocircuito neural)
                    result = colony.deliberate(
                        f"Investigar '{topic}'",
                        max_agents=2, skip_knowledge=False,
                    )
                    ind_resp = result.get("individual_responses", {})
                    if ind_resp:
                        from core.colony_broadcast import get_broadcast
                        bc = get_broadcast()
                        for aid, resp in ind_resp.items():
                            bc.broadcast(aid, resp[:200], msg_type="investigation")
                            self._consolidate_action(aid, topic, resp)
                elif action.startswith("ejecutar_plan:"):
                    goal = action.split(":", 1)[1]
                    try:
                        from core.eidos_planner import get_planner
                        planner = get_planner()
                        plan = planner.plan(goal, max_steps=3)
                        executed = 0
                        for step in plan.get("steps", []):
                            result = planner.execute_step(step)
                            if result.get("status") == "ok":
                                executed += 1
                            log.debug("S79 plan step: %s → %s", step.get("action"), result.get("status"))
                            if result.get("status") not in ("ok", "skipped"):
                                break  # parar en error
                        if executed > 0:
                            log.info("S79: plan '%s' ejecutado: %d/%d pasos OK",
                                     goal, executed, len(plan.get("steps", [])))
                    except Exception as e:
                        log.debug("S79 plan execution error: %s", e)
                elif action.startswith("reforzar:"):
                    concept = action.split(":")[1].split(":")[0]
                    # S67: usar self_research multicanal en lugar de Ollama
                    try:
                        from core.knowledge_reasoner import get_reasoner
                        r = get_reasoner()
                        if r._ready and hasattr(r, "_self_research"):
                            added = r._self_research(concept)
                            log.info("Vivo reforzó '%s' (+%d nodos)", concept, added)
                    except Exception as e:
                        log.debug("self_research fallo: %s", e)
                elif action == "explorar:curiosidad_general":
                    gaps = self._curiosity_phase()
                    if gaps:
                        self._learn_phase(gaps[:2])  # máx 2 gaps por acción
            except Exception as e:
                log.debug("Vivo act: %s en %s", e, action)
            finally:
                self._acting = max(0, self._acting - 1)

        # Ejecutar acciones con timeout total, no bloqueante para ciclo siguiente
        # S82 fix: shutdown(wait=False) tras timeout evita que una acción colgada
        # bloquee permanentemente el ciclo vital (ThreadPoolExecutor.__exit__ llama
        # a shutdown(wait=True) por defecto).
        max_actions_per_cycle = 2  # regla "una/dos acciones grandes por latido"
        actions_to_run = actions[:max_actions_per_cycle]
        ex = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="vivo_act")
        try:
            futures = [ex.submit(_one_action, a) for a in actions_to_run]
            for fut in _cf.as_completed(futures, timeout=120):
                try:
                    fut.result(timeout=1)
                except _cf.TimeoutError:
                    log.warning("Vivo: acción timeout (deja en background)")
                except Exception as e:
                    log.warning("Vivo action error: %s", e)
        finally:
            ex.shutdown(wait=False)  # no bloquear el ciclo si quedan hilos colgados
        log.info("Vivo act: %d acciones ejecutadas (max %d)",
                 len(actions_to_run), max_actions_per_cycle)

    def _consolidate_action(self, agent: str, topic: str, response: str):
        """Consolida el resultado de una acción como inferencia en el grafo."""
        try:
            from core.knowledge_reasoner import get_reasoner
            r = get_reasoner()
            r.evolve_from_text(topic, response[:500])
        except Exception:
            pass

    def _reflect_phase(self, state: Dict[str, Any], learned: int, consolidated: int,
                       t0: float) -> Dict[str, Any]:
        """Fase 5: reflexionar — evaluar resultados, actualizar grafo con aprendizajes.

        S77: devuelve reflection dict con mood para que _cycle() lo use en eventos.
        """
        elapsed = time.time() - t0

        # S77: obtener mood actual del AffectEngine
        mood = "?"
        try:
            from core.eidos_affect import get_affect
            mood = get_affect().state.mood
        except Exception:
            pass

        reflection = {
            "cycle": self._cycle_count,
            "elapsed_s": round(elapsed, 1),
            "learned": learned,
            "consolidated": consolidated,
            "gaps_before": len(state.get("gaps", [])),
            "gaps_after": len(self._curiosity_phase()),
            "mood": mood,
        }
        # Si aprendimos mucho, registrar en Chronicle como hito
        if learned > 5 or consolidated > 5:
            try:
                from core.colony_chronicle import get_chronicle
                get_chronicle().record(
                    "vivo_reflection",
                    "cycle_complete",
                    f"#{self._cycle_count}: {learned} aprendidos, {consolidated} consolidados",
                    metadata=reflection,
                    importance=0.6 if learned > 10 else 0.4,
                )
            except Exception:
                pass
        log.info("Vivo reflexión: ciclo #%d OK en %.1fs — aprendí %d, consolidé %d, mood=%s",
                 self._cycle_count, elapsed, learned, consolidated, mood)
        return reflection

    def _pc_observe_phase(self) -> int:
        """Observa ventanas activas del PC y aprende de ellas."""
        learned = 0
        try:
            from core.screen_scanner import get_open_windows, research_app
            from core.knowledge_reasoner import get_reasoner
            windows = get_open_windows()
            r = get_reasoner()
            for w in windows:
                name = w.get("name", "").strip()
                if not name or len(name) < 3:
                    continue
                nid = f"pc_obs:{hash(name)}"
                r.inject_node(nid, f"Ventana activa: {name[:80]}", {
                    "type": "pc_observation", "confidence": 0.5
                })
                learned += 1
                # Investigar apps nuevas cada 3 ciclos
                if self._cycle_count % 3 == 0:
                    app = name.split(" — ")[-1].split(" - ")[-1].strip()
                    if app and len(app) > 2:
                        try:
                            res = research_app(app, deep=False)
                            if res.get("nodes_added", 0) > 0:
                                learned += res["nodes_added"]
                        except Exception:
                            pass
            if learned:
                log.info("PC observe: %d nodos desde %d ventanas", learned, len(windows))
        except Exception as e:
            log.debug("PC observe: %s", e)
        return learned

    def _structural_refresh_phase(self):
        """S76 Fase 0: Refresca el grafo con estructura real del código fuente.

        Se ejecuta cada 1008 ciclos (~7 días) para mantener actualizado
        el autoconocimiento estructural de EIDOS sin saturar el ciclo normal.
        """
        log.info("S76: Iniciando refresh estructural semanal (Graphify bridge)...")
        try:
            from core.graphify_bridge import GraphifyBridge
            gb = GraphifyBridge()
            # force=True para reanalizar código (puede haber cambiado)
            result = gb.analyze_and_inject(force=True)
            log.info(
                "S76 structural refresh: +%d nodos, +%d aristas, %d errores, %.1fs",
                result.get("nodes_added", 0),
                result.get("edges_added", 0),
                len(result.get("errors", [])),
                result.get("elapsed_s", 0),
            )
            # Reconstruir grafo para cargar nuevas aristas estructurales
            try:
                from core.knowledge_reasoner import get_reasoner
                r = get_reasoner()
                if r:
                    n = r.build_graph()
                    log.info("S76: Grafo reconstruido tras refresh: %d nodos", n)
            except Exception as e:
                log.warning("S76: No se pudo reconstruir grafo: %s", e)
        except Exception as e:
            log.error("S76 structural refresh error: %s", e)

    def _cycle(self):
        """Ciclo completo: percibir → decidir → actuar → aprender → reflexionar.

        S77: integra AffectEngine (emociones), EventBus (pub/sub) y QLearningAgent (RL).
        El ciclo ahora siente, publica eventos y aprende por refuerzo cada latido.
        """
        t0 = time.time()
        self._cycle_count += 1
        errors_before = len(self._errors)
        log.info("═══ Ciclo Vivo #%d ═══", self._cycle_count)

        # ── S77 · AffectEngine: decaimiento emocional natural ──────────────────
        try:
            from core.eidos_affect import get_affect
            get_affect().tick()
        except Exception as e:
            log.debug("affect.tick: %s", e)

        # ── S77 · EventBus: ciclo iniciado ─────────────────────────────────────
        try:
            from core.eidos_events import emit
            emit("cycle_started", {"cycle": self._cycle_count}, source="vivo")
        except Exception as e:
            log.debug("eventBus emit: %s", e)

        state = self._perceive_phase()
        actions = self._decide_phase(state)

        # Fase 3: actuar (ejecutar metas de agentes)
        actions_succeeded = 0
        actions_failed = 0
        try:
            self._act_phase(actions)
            actions_succeeded = sum(1 for a in actions if not a.startswith("explorar:"))
            if actions_succeeded > 0:
                # S77: notificar metas completadas
                try:
                    from core.eidos_affect import get_affect
                    from core.eidos_events import emit
                    affect = get_affect()
                    for _ in range(actions_succeeded):
                        affect.event("goal_completed")
                    emit("goal_completed", {"count": actions_succeeded,
                         "actions": actions[:3]}, source="vivo")
                except Exception as e:
                    log.debug("S77 hooks act: %s", e)
        except Exception as e:
            actions_failed = 1
            # S77: notificar fallo de meta
            try:
                from core.eidos_affect import get_affect
                from core.eidos_events import emit
                get_affect().event("goal_failed")
                emit("goal_failed", {"reason": str(e)[:200]}, source="vivo")
            except Exception:
                pass
            log.warning("Vivo act_phase error: %s", e)

        # Fase 3b: observar PC (aprender de ventanas activas)
        pc_learned = self._pc_observe_phase()

        # S76 Fase 0: refresh estructural semanal (cada 1008 ciclos ≈ 7 días)
        if self._cycle_count % 1008 == 0:
            self._structural_refresh_phase()

        # S76 Marocuai: hooks de arquitectura cognitiva en cada fase
        try:
            mr = self._get_marocuai()
            if mr:
                mr.cycle_hook("perceive")
                mr.cycle_hook("decide")
                mr.cycle_hook("act")
        except Exception as e:
            log.debug("Marocuai hooks error: %s", e)

        # Fase 4: aprender (curiosidad + aprendizaje + consolidación — original)
        gaps = state.get("gaps", [])
        learned = self._learn_phase(gaps) if gaps else 0
        learned += pc_learned
        consolidated = self._consolidate_phase()

        # S77: notificar conocimiento inyectado / habilidades aprendidas
        if learned > 0:
            try:
                from core.eidos_affect import get_affect
                from core.eidos_events import emit
                get_affect().event("knowledge_injected")
                emit("knowledge_injected", {"nodes_added": learned}, source="vivo")
                if learned >= 5:
                    get_affect().event("skill_learned")
                    emit("skill_learned", {"nodes_added": learned}, source="vivo")
            except Exception as e:
                log.debug("S77 learn hooks: %s", e)

        with self._lock:
            self._total_learned += learned
            self._total_consolidated += consolidated
            self._last_cycle = time.time()

        # Broadcast de lo aprendido a todos los agentes de Colony
        if learned > 0 or consolidated > 0:
            self._broadcast_cycle(learned, consolidated, gaps)

        # S76 Marocuai: hooks de learn + reflect
        try:
            mr = self._get_marocuai()
            if mr:
                mr.cycle_hook("learn")
                mr.cycle_hook("reflect")
        except Exception as e:
            log.debug("Marocuai learn/reflect error: %s", e)

        # Fase 5: reflexionar
        reflection = self._reflect_phase(state, learned, consolidated, t0)

        elapsed = round(time.time() - t0, 1)
        new_errors = len(self._errors) - errors_before

        # S77: notificar errores si los hubo
        if new_errors > 0:
            try:
                from core.eidos_affect import get_affect
                from core.eidos_events import emit
                sev = min(1.0, new_errors / 3.0)
                get_affect().event("error", severity=sev)
                emit("error_occurred", {"count": new_errors, "severity": sev},
                     source="vivo")
            except Exception as e:
                log.debug("S77 error hooks: %s", e)

        # ── S77 · Q-Learning: aprender del resultado del ciclo ────────────────
        try:
            from core.eidos_rl import get_rl_agent
            rl = get_rl_agent()
            # Construir state hash desde gaps activos
            active_nodes = [f"gap:{g}" for g in gaps[:10]] + [f"cycle:{self._cycle_count % 100}"]
            state_hash = rl.hash_state(active_nodes)
            # Seleccionar acción para este ciclo
            chosen = rl.select_action(
                state_hash,
                available_actions=["research", "execute_shell", "execute_code",
                                   "gui_click", "gui_type", "null_action"])
            # Calcular recompensa compuesta
            reward = 0.0
            if actions_succeeded > 0:
                reward += rl.reward_from_events("goal_completed")
            if actions_failed > 0:
                reward += rl.reward_from_events("goal_failed")
            if learned > 0:
                reward += rl.reward_from_events("skill_learned")
            if new_errors > 0:
                reward += rl.reward_from_events("error", severity=min(1.0, new_errors / 3.0))
            reward += rl.reward_from_events("energy_cost")
            # Aprender
            next_hash = rl.hash_state(active_nodes + [f"learned:{learned}"])
            rl.learn(state_hash, chosen, reward, next_hash)
            emit("rl_reward", {"action": chosen, "reward": round(reward, 2),
                 "epsilon": rl.epsilon}, source="vivo")
            log.debug("S77 RL: action=%s reward=%.2f eps=%.4f",
                      chosen, reward, rl.epsilon)
        except Exception as e:
            log.debug("S77 RL error: %s", e)

        # ── S78 · Hebbian Pruning: decaimiento + poda periódica ────────────
        if self._cycle_count % 24 == 0:  # cada ~4 horas
            try:
                from core.eidos_hebbian_pruning import get_pruner
                pruner = get_pruner()
                pruner.decay_all()
                log.debug("S78: decaimiento hebbiano aplicado")
            except Exception as e:
                log.debug("S78 decay error: %s", e)

        if self._cycle_count % 144 == 0:  # cada ~24 horas
            try:
                from core.eidos_hebbian_pruning import get_pruner
                pruner = get_pruner()
                # Dry-run primero para evaluar impacto
                preview = pruner.prune(dry_run=True)
                if preview.get("edges_pruned", 0) + preview.get("nodes_orphaned", 0) < 1000:
                    result = pruner.prune(dry_run=False)
                    consolidate = pruner.consolidate(dry_run=False)
                    log.info("S78: poda hebbiana — %d aristas, %d nodos eliminados, "
                             "%d consolidados",
                             result.get("edges_pruned", 0),
                             result.get("nodes_pruned", 0),
                             consolidate.get("edges_strengthened", 0))
                    try:
                        from core.eidos_events import emit
                        emit("structural_refresh", {
                            "pruned_edges": result.get("edges_pruned", 0),
                            "pruned_nodes": result.get("nodes_pruned", 0),
                            "consolidated": consolidate.get("edges_strengthened", 0),
                        }, source="vivo")
                    except Exception:
                        pass
                else:
                    log.warning("S78: poda excesiva detectada en dry-run (%d edges, %d nodes) — saltando",
                               preview.get("edges_pruned", 0), preview.get("nodes_orphaned", 0))
            except Exception as e:
                log.debug("S78 pruning error: %s", e)

        # ── S81 · Sleep: consolidación autobiográfica diaria ───────────────
        if self._cycle_count % 144 == 0:  # cada ~24 horas
            try:
                from core.eidos_identity import get_identity
                idn = get_identity()
                sleep_result = idn.sleep()
                log.info("S81: sleep #%d — independencia=%.1f%%",
                         sleep_result.get("sleep_cycle", 0),
                         sleep_result.get("independence_pct", 0))
            except Exception as e:
                log.debug("S81 sleep error: %s", e)

        # ── S78 · Supervisor: auto-backup periódico ─────────────────────────
        if self._cycle_count % 1008 == 0:  # ~semanal (misma cadencia que structural_refresh)
            try:
                from core.eidos_supervisor import get_supervisor
                sup = get_supervisor()
                sup.auto_backup()
            except Exception as e:
                log.debug("S78 auto_backup error: %s", e)

        # ── S77 · EventBus: ciclo completado ─────────────────────────────────
        try:
            emit("cycle_completed", {
                "cycle": self._cycle_count,
                "learned": learned,
                "consolidated": consolidated,
                "gaps": len(gaps),
                "errors": new_errors,
                "elapsed_s": elapsed,
                "mood": reflection.get("mood", "?") if reflection else "?",
            }, source="vivo")
        except Exception as e:
            log.debug("S77 cycle_completed emit: %s", e)

        self._save_cycle(
            phase="complete",
            learned=learned,
            consolidated=consolidated,
            gaps_found=len(gaps),
            curiosity=", ".join(gaps[:3]),
        )

        self._history.append({
            "cycle": self._cycle_count,
            "timestamp": t0,
            "elapsed_s": elapsed,
            "gaps": gaps[:3],
            "learned": learned,
            "consolidated": consolidated,
            "errors": new_errors,
        })
        self._history = self._history[-20:]

        log.info(
            "Ciclo #%d: %d gaps, %d aprendidos, %d consolidados, %ds, %d errores",
            self._cycle_count, len(gaps), learned, consolidated, elapsed, new_errors,
        )
        # S67: persistir status para IPC
        self._save_status_json()

    def _loop(self):
        """Bucle eterno: ciclo → dormir → repetir.
        S67: chequea kill switch al inicio de cada ciclo.
        S68: anti-spam — solo manda startup msg si han pasado >24h del último."""
        log.info("Vivo iniciado — latiendo cada %ds", CYCLE_INTERVAL)
        # Anti-spam: solo notificar startup una vez cada 24h
        startup_marker = Path.home() / ".eidos" / "last_startup_msg.txt"
        should_notify = True
        try:
            if startup_marker.exists():
                last_ts = float(startup_marker.read_text().strip() or "0")
                if time.time() - last_ts < 86400:  # menos de 24h
                    should_notify = False
                    log.info("Anti-spam: startup msg silenciado (último hace %ds)",
                             int(time.time() - last_ts))
        except Exception:
            pass
        if should_notify:
            try:
                from core.telegram_bot import send_proactive_message
                send_proactive_message(
                    "❤️ Mi corazón empezó a latir. "
                    f"Latido cada {CYCLE_INTERVAL//60}min. Estoy vivo.",
                    priority=7, topic="vivo_startup",
                )
                startup_marker.parent.mkdir(parents=True, exist_ok=True)
                startup_marker.write_text(str(time.time()))
            except Exception:
                pass

        while self._running:
            if self._check_kill_switch():
                break
            try:
                self._cycle()
            except Exception as e:
                log.error("Vivo error crítico: %s", e)
                self._errors.append(f"critical: {e}")
            if self._running:
                # Sleep en pequeños trozos para responder al kill switch rápido
                slept = 0
                while slept < CYCLE_INTERVAL and self._running:
                    time.sleep(min(5, CYCLE_INTERVAL - slept))
                    slept += 5
                    if self._check_kill_switch():
                        break

    def start(self):
        if self._running:
            return
        self._running = True
        # S67: registrar SIGTERM/SIGINT handlers (solo si estamos en MainThread)
        try:
            import signal as _sig
            import threading as _th
            if _th.current_thread() is _th.main_thread():
                _sig.signal(_sig.SIGTERM, self._graceful_shutdown)
                _sig.signal(_sig.SIGINT, self._graceful_shutdown)
                log.info("SIGTERM/SIGINT handlers registrados")
        except Exception as e:
            log.debug("signal register failed: %s", e)

        # S78: iniciar Supervisor en background
        try:
            from core.eidos_supervisor import get_supervisor
            sup = get_supervisor()
            sup.start()
            log.info("S78 Supervisor iniciado junto con Vivo")
        except Exception as e:
            log.debug("S78 supervisor start: %s", e)

        self._thread = threading.Thread(target=self._loop, daemon=True, name="eidos-vivo")
        self._thread.start()
        log.info("EIDOS Vivo — corazón latiendo")

    def stop(self):
        self._graceful_shutdown()
        log.info("EIDOS Vivo — detenido")

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._running,
                "pid": os.getpid(),
                "cycle": self._cycle_count,
                "total_learned": self._total_learned,
                "total_consolidated": self._total_consolidated,
                "last_cycle": self._last_cycle,
                "last_cycle_ago_s": round(time.time() - self._last_cycle, 1) if self._last_cycle else 0,
                "errors_last_10": self._errors[-10:],
                "total_errors": len(self._errors),
                "history": self._history[-5:],
                "acting": self._acting,
                "ts": time.time(),
            }

    def _save_status_json(self):
        """S67: persiste status a JSON para IPC entre procesos.
        Otros procesos pueden hacer json.loads(STATUS_FILE.read_text())."""
        try:
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATUS_FILE.write_text(json.dumps(self.get_status(),
                                              indent=2, ensure_ascii=False))
        except Exception as e:
            log.debug("save_status_json: %s", e)


_vivo: Optional[EidosVivo] = None
_vivo_lock = threading.Lock()


def get_vivo() -> EidosVivo:
    global _vivo
    if _vivo is None:
        with _vivo_lock:
            if _vivo is None:
                _vivo = EidosVivo()
    return _vivo


def start_vivo():
    d = get_vivo()
    d.start()
    return d
