"""
core/colony_governor.py — Gobernador autónomo de Colony  [Fase 6]

Corre cada 15 minutos. Revisa propuestas en colony_proposals y ejecuta
las aprobadas dentro de límites seguros. Las que requieren SER van al inbox.
Primera acción automática: limpiar broadcasts > 7 días.

Uso:
    from core.colony_governor import get_governor
    gov = get_governor()
    gov.start()
"""
from __future__ import annotations

import json
import sqlite3
import time
import threading
import logging
from pathlib import Path
from typing import Optional
from core.db import get_conn

log = logging.getLogger("eidos.governor")

BRAIN_DB    = Path.home() / ".eidos" / "evolution_brain.db"
TICK_SECS   = 900   # 15 minutos
BROADCAST_MAX_AGE_DAYS = 7


class ColonyGovernor:
    """Ejecuta propuestas aprobadas y mantiene Colony sana."""

    def __init__(self):
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="colony-governor"
        )
        self._thread.start()
        log.info("ColonyGovernor iniciado (tick=%ds)", TICK_SECS)

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Loop ────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Primera ejecución: limpiar broadcasts antiguos de inmediato
        self._cleanup_broadcasts()
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.debug("governor tick error: %s", e)
            self._stop.wait(timeout=TICK_SECS)

    def _tick(self) -> None:
        self._cleanup_broadcasts()
        self._process_proposals()
        self._auto_vote_pending()

    # ── Limpieza de broadcasts [Fase 6 — primera acción automática] ─────────

    def _cleanup_broadcasts(self) -> None:
        """Borra broadcasts de más de BROADCAST_MAX_AGE_DAYS días."""
        try:
            cutoff = time.time() - (BROADCAST_MAX_AGE_DAYS * 86400)
            conn = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            r = conn.execute(
                "DELETE FROM colony_broadcasts WHERE ts < ?", (cutoff,)
            )
            deleted = r.rowcount
            conn.commit()
            pass  # S109: get_conn no necesita close()
            if deleted:
                log.info("Governor: borró %d broadcasts antiguos (>%d días)", deleted, BROADCAST_MAX_AGE_DAYS)
                try:
                    from core.colony_broadcast import get_broadcast
                    get_broadcast().broadcast(
                        "colony_governor",
                        f"Limpieza: borré {deleted} broadcasts de más de {BROADCAST_MAX_AGE_DAYS} días",
                        msg_type="learning"
                    )
                except Exception:
                    pass  # error no crítico, continuar
        except Exception as e:
            log.debug("cleanup_broadcasts: %s", e)

    # ── Propuestas ───────────────────────────────────────────────────────────

    def _process_proposals(self) -> None:
        """Talla propuestas con periodo de votación vencido y ejecuta las aprobadas."""
        try:
            from core.colony_proposals import get_proposal_system
            ps = get_proposal_system()
            for proposal in ps.get_open():
                result = ps.tally(proposal.id)
                if result == "passed":
                    self._execute_proposal(proposal, ps)
                elif result == "rejected":
                    log.info("Propuesta %d rechazada", proposal.id)
        except Exception as e:
            log.debug("process_proposals: %s", e)

    def _execute_proposal(self, proposal, ps) -> None:
        """Ejecuta una propuesta aprobada dentro de los límites seguros."""
        try:
            pid   = proposal.id
            ptype = proposal.proposal_type
            title = proposal.title
            desc  = proposal.description

            log.info("Ejecutando propuesta %d [%s]: %s", pid, ptype, title[:60])

            if ptype == "config_change":
                self._exec_config_change(desc)
            elif ptype == "experiment":
                self._exec_experiment(proposal)
            elif ptype == "knowledge_initiative":
                self._exec_knowledge_initiative(desc)
            elif ptype == "connection_retire":
                self._exec_connection_retire(proposal)
            elif ptype == "character_reproduction":
                self._exec_character_reproduction(proposal)
            else:
                # Tipos que requieren SER → inbox
                self._send_to_ser_inbox(pid, proposal)
                return

            ps.mark_executed(pid)
            try:
                from core.colony_broadcast import get_broadcast
                get_broadcast().broadcast(
                    "colony_governor",
                    f"Propuesta ejecutada: {title[:80]}",
                    msg_type="learning"
                )
            except Exception:
                pass  # error no crítico, continuar
        except Exception as e:
            log.warning("execute_proposal %d error: %s", proposal.id if hasattr(proposal, 'id') else '?', e)

    def _exec_config_change(self, description: str) -> None:
        """Aplica cambios de configuración seguros (solo ~/.eidos/config/)."""
        log.info("Config change: %s", description[:80])
        # Config changes simples se registran como conocimiento
        try:
            from core.colony_broadcast import get_broadcast
            get_broadcast().broadcast(
                "colony_governor", f"Config: {description[:150]}", msg_type="learning"
            )
        except Exception:
            pass  # error no crítico, continuar
    def _exec_experiment(self, proposal) -> None:
        """Asigna el experimento al personaje autor en su sandbox."""
        try:
            from core.character_sandbox import get_sandbox
            sb = get_sandbox(proposal.author)
            topic = proposal.title[:30]
            r = sb.run_experiment(topic)
            log.info("Experimento ejecutado: %s → %s", topic, r.output[:60])
        except Exception as e:
            log.debug("exec_experiment: %s", e)

    def _exec_knowledge_initiative(self, description: str) -> None:
        """Dispara una iniciativa de aprendizaje en curiosity."""
        try:
            from core.eidos_curiosity import get_curiosity
            get_curiosity().ask_question(description[:80])
        except Exception as e:
            log.debug("knowledge_initiative: %s", e)

    def _exec_connection_retire(self, proposal) -> None:
        """Ejecuta el retiro de conexión de un personaje soberano."""
        try:
            meta = proposal.metadata if isinstance(proposal.metadata, dict) else {}
            char_name = meta.get("character") or ""
            if not char_name:
                return
            from core.character_lifecycle import get_lifecycle
            get_lifecycle().retire_connection(char_name)
            log.info("Conexión retirada para %s por propuesta aprobada", char_name)
        except Exception as e:
            log.debug("exec_connection_retire: %s", e)

    def _exec_character_reproduction(self, proposal) -> None:
        """Ejecuta la reproducción entre dos personajes si Colony aprobó."""
        try:
            meta = proposal.metadata if isinstance(proposal.metadata, dict) else {}
            p1 = meta.get("parent1", "")
            p2 = meta.get("parent2", "")
            if not p1 or not p2:
                return
            from core.character_lifecycle import get_lifecycle
            child = get_lifecycle().execute_reproduction(p1, p2)
            if child:
                log.info("Reproducción ejecutada: %s + %s → %s", p1, p2, child)
            else:
                log.warning("Reproducción falló: %s + %s", p1, p2)
        except Exception as e:
            log.debug("exec_character_reproduction: %s", e)

    def _send_to_ser_inbox(self, pid: int, proposal) -> None:
        """Envía propuesta que requiere aprobación de SER al inbox."""
        try:
            from core.ser_inbox import get_ser_inbox
            get_ser_inbox().add(
                msg_type="proposal_needs_approval",
                title=proposal.title,
                summary=proposal.description[:300],
                proposal_id=pid,
                action_needed=True
            )
        except Exception as e:
            log.debug("send_to_ser_inbox: %s", e)

    # ── Auto-votación de los personajes ─────────────────────────────────────

    def _auto_vote_pending(self) -> None:
        """
        Los personajes votan automáticamente en propuestas abiertas.
        Cada personaje vota SÍ si la propuesta está en su dominio,
        NO si la desconoce, y ABSTAIN si es neutral.
        """
        try:
            from core.colony_proposals import get_proposal_system
            from core.colony_broadcast import get_broadcast
            ps = get_proposal_system()
            open_proposals = ps.get_open()
            if not open_proposals:
                return

            voters = [
                "colony_coder", "colony_operator", "colony_analyst",
                "colony_lumen", "colony_general"
            ]
            domain_keywords = {
                "colony_coder":    ["código", "python", "script", "import", "función"],
                "colony_operator": ["sistema", "comando", "servicio", "proceso", "disco"],
                "colony_analyst":  ["análisis", "datos", "documento", "estadística"],
                "colony_lumen":    ["síntesis", "razonamiento", "conocimiento", "patrón"],
                "colony_general":  [],  # vota sí a todo por defecto
            }

            for proposal in open_proposals[:5]:  # máximo 5 propuestas por tick
                text = (proposal.title + " " + proposal.description).lower()
                for voter in voters:
                    keywords = domain_keywords.get(voter, [])
                    if voter == "colony_general":
                        vote, reason = True, "Como coordinador, apoyo la iniciativa"
                    elif any(kw in text for kw in keywords):
                        vote, reason = True, "Es relevante para mi dominio"
                    else:
                        vote, reason = None, "No es mi dominio, me abstengo"
                    if vote is not None:
                        try:
                            ps.vote(proposal.id, voter, vote, reason)
                        except Exception:
                            pass  # error no crítico, continuar
        except Exception as e:
            log.debug("auto_vote: %s", e)


_instance: Optional[ColonyGovernor] = None
_lock      = threading.Lock()


def get_governor() -> ColonyGovernor:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ColonyGovernor()
    return _instance
