"""
EIDOS core/colony_token_economy.py — Economía de tokens para Colony
====================================================================
Inspirado en ClawColony: cada agente tiene tokens como recurso de salud/energía.
Cada interacción tiene un coste en tokens. Agentes con más knowledge ganan más.

Conceptos de ClawColony adaptados:
- Token cost per model call (basado en tokens Ollama usados)
- Tokens ganados por interacciones de calidad
- Governance proposals requieren tokens para votar
- Balance visible en dashboard

La economía NO es punición — es tracking de actividad y recompensa.
"""
from __future__ import annotations

import sqlite3
import threading
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from core.db import get_conn

log = logging.getLogger("eidos.colony_token_economy")

DB_PATH = Path.home() / ".eidos" / "economy.db"

# Coste en tokens por tipo de operación
TOKEN_COSTS: Dict[str, float] = {
    "ollama_call_fast":    1.0,   # qwen:1.5b
    "ollama_call_normal":  3.0,   # qwen:7b, hermes:8b
    "ollama_call_heavy":   5.0,   # hermes:8b respuesta larga
    "ollama_call_vision":  8.0,   # llama3.2-vision
    "intercomm_message":   0.5,   # conversación autónoma
    "knowledge_node":     -2.0,   # GANA tokens por aprender (negativo = reward)
    "spawn_character":   -10.0,   # GANA tokens por generar personaje
    "proposal_create":     5.0,   # crear propuesta de governance
    "proposal_vote":       1.0,   # votar en propuesta
}

# Tokens iniciales por agente
INITIAL_TOKENS: Dict[str, float] = {
    "colony_coder":    100.0,
    "colony_analyst":  100.0,
    "colony_vision":   100.0,
    "colony_operator": 100.0,
    "colony_general":  100.0,
}


class ColonyTokenEconomy:
    """
    Sistema de tokens para Colony.
    Thread-safe. Persiste en economy.db.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        try:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = get_conn(DB_PATH)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS balances (
                    agent_id   TEXT PRIMARY KEY,
                    tokens     REAL DEFAULT 100.0,
                    total_spent REAL DEFAULT 0.0,
                    total_earned REAL DEFAULT 0.0,
                    updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id   TEXT,
                    operation  TEXT,
                    amount     REAL,
                    balance_after REAL,
                    timestamp  REAL
                );
            """)
            # Crear balances iniciales
            for agent, tokens in INITIAL_TOKENS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO balances (agent_id, tokens, updated_at) VALUES (?,?,?)",
                    (agent, tokens, time.time())
                )
            conn.commit()
            pass  # S109: get_conn no necesita close()
        except Exception as e:
            log.warning("TokenEconomy DB init: %s", e)

    def charge(self, agent_id: str, operation: str) -> float:
        """
        Cobra tokens por una operación. Devuelve el balance restante.
        Si el balance llega a 0, el agente entra en modo ahorro (no muere).
        """
        cost = TOKEN_COSTS.get(operation, 1.0)
        return self._apply(agent_id, operation, cost)

    def reward(self, agent_id: str, operation: str) -> float:
        """Otorga tokens como recompensa (operations con coste negativo)."""
        cost = TOKEN_COSTS.get(operation, -1.0)
        return self._apply(agent_id, operation, cost)

    def get_balance(self, agent_id: str) -> float:
        try:
            conn = get_conn(DB_PATH, timeout=3)
            row = conn.execute(
                "SELECT tokens FROM balances WHERE agent_id=?", (agent_id,)
            ).fetchone()
            pass  # S109: get_conn no necesita close()
            return float(row[0]) if row else 100.0
        except Exception:
            return 100.0

    def get_all_balances(self) -> Dict[str, float]:
        try:
            conn = get_conn(DB_PATH, timeout=3)
            rows = conn.execute("SELECT agent_id, tokens FROM balances").fetchall()
            pass  # S109: get_conn no necesita close()
            return {r[0]: float(r[1]) for r in rows}
        except Exception:
            return {}

    def get_stats(self) -> Dict[str, Any]:
        try:
            conn = get_conn(DB_PATH, timeout=3)
            balances = {r[0]: r[1] for r in
                        conn.execute("SELECT agent_id, tokens FROM balances").fetchall()}
            total_tx = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            top_earner = conn.execute(
                "SELECT agent_id, total_earned FROM balances ORDER BY total_earned DESC LIMIT 1"
            ).fetchone()
            pass  # S109: get_conn no necesita close()
            return {
                "balances": balances,
                "total_transactions": total_tx,
                "top_earner": top_earner[0] if top_earner else None,
                "total_in_circulation": sum(balances.values()),
            }
        except Exception:
            return {}

    def _apply(self, agent_id: str, operation: str, amount: float) -> float:
        with self._lock:
            try:
                conn = get_conn(DB_PATH)
                conn.execute("PRAGMA journal_mode=WAL")

                row = conn.execute(
                    "SELECT tokens, total_spent, total_earned FROM balances WHERE agent_id=?",
                    (agent_id,)
                ).fetchone()

                if not row:
                    conn.execute(
                        "INSERT INTO balances (agent_id, tokens, updated_at) VALUES (?,100.0,?)",
                        (agent_id, time.time())
                    )
                    current, spent, earned = 100.0, 0.0, 0.0
                else:
                    current, spent, earned = float(row[0]), float(row[1]), float(row[2])

                new_balance = max(0.0, current - amount)  # nunca negativo
                new_spent   = spent  + max(0, amount)
                new_earned  = earned + max(0, -amount)

                conn.execute(
                    "UPDATE balances SET tokens=?, total_spent=?, total_earned=?, updated_at=? WHERE agent_id=?",
                    (new_balance, new_spent, new_earned, time.time(), agent_id)
                )
                conn.execute(
                    "INSERT INTO transactions (agent_id, tx_type, reason, amount, balance_after, timestamp) "
                    "VALUES (?,?,?,?,?,?)",
                    (agent_id, "charge" if amount > 0 else "reward",
                     operation, amount, new_balance, time.time())
                )
                conn.commit()
                pass  # S109: get_conn no necesita close()
                if amount > 0 and new_balance < 20:
                    log.warning("Agente %s bajo en tokens: %.1f", agent_id, new_balance)

                return new_balance
            except Exception as e:
                log.debug("TokenEconomy _apply: %s", e)
                return 0.0


_instance: Optional[ColonyTokenEconomy] = None
_lock = threading.Lock()


def get_token_economy() -> ColonyTokenEconomy:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = ColonyTokenEconomy()
    return _instance
