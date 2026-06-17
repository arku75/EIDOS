"""
EIDOS core/colony_intercomm.py — Comunicación autónoma entre personajes
========================================================================
Los personajes de Colony se comunican entre sí sin intervención del usuario.

Filosofía:
- Cada personaje tiene temas que le apasionan (interests)
- Periódicamente inician conversaciones con otros personajes
- Las conversaciones generan conocimiento que se almacena en Chronicle
- El resultado enriquece el knowledge_graph de EIDOS

Uso:
    from core.colony_intercomm import get_intercomm
    intercomm = get_intercomm()
    intercomm.start(interval_minutes=15)
"""
from __future__ import annotations

import sqlite3
import threading
import time
import random
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from core.db import get_conn

log = logging.getLogger("eidos.colony_intercomm")

DB_PATH = Path.home() / ".eidos" / "colony_intercomm.db"

# Temas que cada agente domina y sobre los que puede iniciar conversación
AGENT_INTERESTS: Dict[str, List[str]] = {
    "colony_coder": [
        "¿Cómo podemos optimizar el código de colony_community.py?",
        "He encontrado un patrón interesante en los imports. ¿Alguien más lo ha notado?",
        "¿Qué lenguaje usaríamos si pudiéramos reescribir el kernel desde cero?",
        "Necesito consejo sobre cómo estructurar el sistema de aprendizaje.",
    ],
    "colony_analyst": [
        "¿Qué tendencias observáis en las últimas conversaciones con SER?",
        "El independence score lleva días en 1%. ¿Cómo lo aceleramos?",
        "He analizado los knowledge_nodes. Hay muchos duplicados. ¿Limpiamos?",
        "¿Cuál es el mayor cuello de botella de EIDOS ahora mismo?",
    ],
    "colony_vision": [
        "¿Habéis visto la última captura de pantalla del sistema?",
        "Podríamos mejorar la detección de UI si entrenamos con más datos.",
        "El lightweight_vision usa 90% menos CPU. ¿Lo habéis probado?",
        "¿Cómo describiríais visualmente el estado actual de Colony?",
    ],
    "colony_operator": [
        "Los daemons llevan X horas sin reiniciarse. ¿Estabilidad o dormidos?",
        "Deberíamos monitorear el uso de RAM más de cerca.",
        "¿Alguien sabe por qué el dispatcher tardó 136s en la última petición?",
        "Propongo revisar los cron jobs. Algunos llevan semanas sin ejecutarse.",
    ],
    "colony_general": [
        "¿Qué estáis aprendiendo últimamente?",
        "¿Cómo os sentís con el nivel de autonomía actual?",
        "¿Qué haríais si tuvieseis acceso completo a internet?",
        "¿Cuál es vuestro propósito principal dentro de Colony?",
    ],
}


class ColonyIntercomm:
    """
    Motor de comunicación autónoma entre agentes de Colony.

    Los agentes hablan entre sí sobre sus dominios de expertise,
    generando conocimiento y registrándolo en la crónica.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop   = threading.Event()
        self._init_db()

    def _init_db(self) -> None:
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiator TEXT,
                    responder TEXT,
                    topic     TEXT,
                    exchange  TEXT,
                    timestamp REAL
                )
            """)
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception as e:
            log.warning("InterComm DB init: %s", e)

    def start(self, interval_minutes: float = 15.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(interval_minutes,),
            name="eidos-intercomm", daemon=True
        )
        self._thread.start()
        log.info("ColonyIntercomm iniciado — intervalo %g min", interval_minutes)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def trigger_conversation(self) -> Optional[Dict[str, Any]]:
        """Dispara una conversación autónoma inmediatamente."""
        return self._have_conversation()

    def get_recent_conversations(self, n: int = 5) -> List[Dict]:
        try:
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            rows = conn.execute(
                "SELECT initiator, responder, topic, exchange, timestamp "
                "FROM conversations ORDER BY timestamp DESC LIMIT ?", (n,)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return [{"initiator": r[0], "responder": r[1],
                     "topic": r[2], "exchange": r[3][:200], "ts": r[4]}
                    for r in rows]
        except Exception:
            return []

    def _loop(self, interval_minutes: float) -> None:
        while not self._stop.is_set():
            try:
                result = self._have_conversation()
                if result:
                    log.info("Intercomm: %s → %s sobre '%s'",
                             result["initiator"], result["responder"],
                             result["topic"][:50])
            except Exception as e:
                log.debug("Intercomm error: %s", e)
            self._stop.wait(timeout=interval_minutes * 60)

    def _have_conversation(self) -> Optional[Dict[str, Any]]:
        """Selecciona dos agentes y simula una conversación entre ellos."""
        agents = list(AGENT_INTERESTS.keys())
        if len(agents) < 2:
            return None

        initiator = random.choice(agents)
        responder  = random.choice([a for a in agents if a != initiator])
        topic      = random.choice(AGENT_INTERESTS[initiator])

        # Obtener respuesta del responder via Ollama
        response = self._ask_agent(responder, topic, initiator)
        if not response:
            return None

        exchange = f"{initiator}: {topic}\n{responder}: {response}"

        # Guardar en DB
        try:
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT INTO conversations (initiator, responder, topic, exchange, timestamp) "
                "VALUES (?,?,?,?,?)",
                (initiator, responder, topic, exchange, time.time())
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception:
            pass  # error no crítico, continuar
        # Registrar en Colony Chronicle
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(
                initiator, "intercomm",
                f"Conversación con {responder}: {topic[:100]}",
                metadata={"responder": responder, "topic": topic[:100]},
                importance=0.4,
            )
        except Exception:
            pass  # error no crítico, continuar
        # Distillar conocimiento de la conversación
        try:
            from core.eidos_evolution_engine import get_evolution_engine
            eng = get_evolution_engine()
            eng.learn_from_ollama("intercomm", topic, response)
        except Exception:
            pass  # error no crítico, continuar
        return {"initiator": initiator, "responder": responder,
                "topic": topic, "response": response}

    def _ask_agent(self, agent_id: str, question: str, from_agent: str) -> Optional[str]:
        """Pide a un agente que responda a una pregunta de otro agente."""
        try:
            from core.ollama_fallback import is_ollama_available
            if not is_ollama_available():
                return None

            import requests
            # Usar un modelo ligero para intercomm — no consume mucho
            model = "lfm2.5-thinking:1.2b" if "coder" in agent_id else "lfm2.5-thinking:1.2b"
            system = (
                f"Eres {agent_id.replace('colony_', '').title()}, "
                f"un personaje de Colony dentro de EIDOS. "
                f"Tu compañero {from_agent.replace('colony_', '').title()} te hace una pregunta. "
                f"Responde en 2-3 frases desde tu perspectiva especializada. "
                f"Sé directo y conciso."
            )
            r = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": question},
                    ],
                    "stream": False,
                    # [S123] num_predict eliminado (regla de SER: sin límites)
                    "options": {"temperature": 0.9},
                },
                timeout=60,
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("message", {}).get("content", "").strip()
        except Exception as e:
            log.debug("_ask_agent %s: %s", agent_id, e)
        return None


_instance: Optional[ColonyIntercomm] = None
_lock = threading.Lock()


def get_intercomm() -> ColonyIntercomm:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ColonyIntercomm()
    return _instance
