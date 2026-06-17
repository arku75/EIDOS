"""
EIDOS core/token_economy.py — Colony-Inspired Token Economy
============================================================
Economía de tokens con supervivencia real, inspirada en ClawColony.

Cada agente tiene un balance de tokens que funciona como HP + moneda + fuel.
Si un agente llega a 0 tokens → muere y no puede actuar hasta ser revivido.

Features:
  - Balance tracking con SQLite persistence
  - Metabolismo: life_cost periódico por tick
  - Thresholds: warning (20%), critical (10%), death (0%)
  - Hibernación voluntaria (reduce life_cost al 20%)
  - Treasury: pool compartido con floor de seguridad
  - Rewards por contribuciones
  - Leaderboard y transaction log

Uso:
    from core.token_economy import get_economy
    eco = get_economy()
    eco.register_agent("scanner", initial_balance=100)
    eco.earn("scanner", 50, "completed_scan")
    eco.tick()  # metabolismo económico
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.token_economy")

DB_PATH = os.path.expanduser("~/.eidos/economy.db")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_INITIAL_BALANCE = 100.0
DEFAULT_LIFE_COST = 2.0          # tokens por tick
HIBERNATION_COST_RATIO = 0.20   # hibernation reduce life_cost al 20%
WARNING_THRESHOLD = 0.20         # 20% del balance máximo
CRITICAL_THRESHOLD = 0.10        # 10%
TREASURY_FLOOR = 50.0            # mínimo que treasury siempre retiene
DEFAULT_TAX_RATE = 0.05          # 5% de earnings van a treasury

# Rewards por tipo de contribución
REWARD_TABLE = {
    "task_completed": 20.0,
    "bug_found": 30.0,
    "knowledge_generated": 15.0,
    "ganglion_created": 25.0,
    "proposal_approved": 10.0,
    "service_maintained": 5.0,
    "scan_completed": 10.0,
    "alert_resolved": 15.0,
}


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class AgentState(str, Enum):
    ACTIVE = "active"
    WARNING = "warning"
    CRITICAL = "critical"
    HIBERNATING = "hibernating"
    DEAD = "dead"


class TransactionType(str, Enum):
    EARN = "earn"
    SPEND = "spend"
    TRANSFER = "transfer"
    TAX = "tax"
    LIFE_COST = "life_cost"
    REWARD = "reward"
    REVIVAL = "revival"
    TREASURY_DEPOSIT = "treasury_deposit"
    TREASURY_WITHDRAW = "treasury_withdraw"


@dataclass
class AgentAccount:
    agent_id: str
    balance: float
    max_balance: float
    state: AgentState
    life_cost: float
    total_earned: float = 0.0
    total_spent: float = 0.0
    created_at: float = field(default_factory=time.time)
    last_tick: float = field(default_factory=time.time)
    hibernation_start: Optional[float] = None


@dataclass
class Transaction:
    tx_id: str
    tx_type: TransactionType
    agent_id: str
    amount: float
    reason: str
    balance_after: float
    timestamp: float = field(default_factory=time.time)
    counterpart: Optional[str] = None  # for transfers


# ══════════════════════════════════════════════════════════════════════════════
#  TOKEN ECONOMY
# ══════════════════════════════════════════════════════════════════════════════

class TokenEconomy:
    """
    Economía de tokens con supervivencia real.

    Cada agente tiene balance, life_cost, y puede morir si llega a 0.
    Persistencia completa en SQLite.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._tick_count = 0
        self._treasury_balance = 0.0
        self._init_db()
        self._load_treasury()
        log.info("💰 [TokenEconomy] Inicializado — db=%s", db_path)

    def _init_db(self) -> None:
        """Crea las tablas SQLite si no existen."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with get_conn_ctx(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    balance REAL NOT NULL DEFAULT 100.0,
                    max_balance REAL NOT NULL DEFAULT 100.0,
                    state TEXT NOT NULL DEFAULT 'active',
                    life_cost REAL NOT NULL DEFAULT 2.0,
                    total_earned REAL NOT NULL DEFAULT 0.0,
                    total_spent REAL NOT NULL DEFAULT 0.0,
                    created_at REAL NOT NULL,
                    last_tick REAL NOT NULL,
                    hibernation_start REAL
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    tx_type TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    amount REAL NOT NULL,
                    reason TEXT,
                    balance_after REAL NOT NULL,
                    counterpart TEXT,
                    timestamp REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS treasury (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    balance REAL NOT NULL DEFAULT 0.0,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tx_agent ON transactions(agent_id);
                CREATE INDEX IF NOT EXISTS idx_tx_time ON transactions(timestamp);
            """)

    def _load_treasury(self) -> None:
        """Carga el balance del treasury desde la DB."""
        with get_conn_ctx(self.db_path) as conn:
            row = conn.execute("SELECT balance FROM treasury WHERE id = 1").fetchone()
            if row:
                self._treasury_balance = row[0]
            else:
                conn.execute(
                    "INSERT INTO treasury (id, balance, updated_at) VALUES (1, 0.0, ?)",
                    (time.time(),)
                )
                self._treasury_balance = 0.0

    def _save_treasury(self, conn: sqlite3.Connection) -> None:
        """Persiste el balance del treasury."""
        conn.execute(
            "UPDATE treasury SET balance = ?, updated_at = ? WHERE id = 1",
            (self._treasury_balance, time.time())
        )

    def _log_tx(self, conn: sqlite3.Connection, tx: Transaction) -> None:
        """Guarda una transacción en la DB."""
        conn.execute(
            """INSERT INTO transactions
               (tx_id, tx_type, agent_id, amount, reason, balance_after, counterpart, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (tx.tx_id, tx.tx_type.value, tx.agent_id, tx.amount,
             tx.reason, tx.balance_after, tx.counterpart, tx.timestamp)
        )

    def _get_agent(self, conn: sqlite3.Connection, agent_id: str) -> Optional[AgentAccount]:
        """Carga un agente desde la DB."""
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return None
        return AgentAccount(
            agent_id=row[0], balance=row[1], max_balance=row[2],
            state=AgentState(row[3]), life_cost=row[4],
            total_earned=row[5], total_spent=row[6],
            created_at=row[7], last_tick=row[8],
            hibernation_start=row[9]
        )

    def _save_agent(self, conn: sqlite3.Connection, agent: AgentAccount) -> None:
        """Persiste un agente en la DB."""
        conn.execute(
            """INSERT OR REPLACE INTO agents
               (agent_id, balance, max_balance, state, life_cost,
                total_earned, total_spent, created_at, last_tick, hibernation_start)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent.agent_id, agent.balance, agent.max_balance,
             agent.state.value, agent.life_cost,
             agent.total_earned, agent.total_spent,
             agent.created_at, agent.last_tick, agent.hibernation_start)
        )

    def _update_state(self, agent: AgentAccount) -> None:
        """Actualiza el estado del agente según su balance."""
        if agent.state == AgentState.HIBERNATING:
            return  # hibernation se gestiona manualmente
        if agent.balance <= 0:
            agent.state = AgentState.DEAD
            agent.balance = 0.0
        elif agent.balance <= agent.max_balance * CRITICAL_THRESHOLD:
            agent.state = AgentState.CRITICAL
        elif agent.balance <= agent.max_balance * WARNING_THRESHOLD:
            agent.state = AgentState.WARNING
        else:
            agent.state = AgentState.ACTIVE

    # ── API Pública ───────────────────────────────────────────────────────────

    def register_agent(self, agent_id: str,
                       initial_balance: float = DEFAULT_INITIAL_BALANCE,
                       life_cost: float = DEFAULT_LIFE_COST) -> AgentAccount:
        """Registra un nuevo agente en la economía."""
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                existing = self._get_agent(conn, agent_id)
                if existing:
                    return existing

                now = time.time()
                agent = AgentAccount(
                    agent_id=agent_id, balance=initial_balance,
                    max_balance=initial_balance, state=AgentState.ACTIVE,
                    life_cost=life_cost, created_at=now, last_tick=now
                )
                self._save_agent(conn, agent)
                self._log_tx(conn, Transaction(
                    tx_id=str(uuid.uuid4()), tx_type=TransactionType.EARN,
                    agent_id=agent_id, amount=initial_balance,
                    reason="initial_registration",
                    balance_after=initial_balance
                ))
                print(f"💰 [Economy] Agente '{agent_id}' registrado — balance={initial_balance}")
                return agent

    def earn(self, agent_id: str, amount: float, reason: str = "generic") -> float:
        """Agente gana tokens. Retorna nuevo balance."""
        if amount <= 0:
            raise ValueError("Amount must be positive")

        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                agent = self._get_agent(conn, agent_id)
                if not agent:
                    raise ValueError(f"Agent '{agent_id}' not registered")
                if agent.state == AgentState.DEAD:
                    raise ValueError(f"Agent '{agent_id}' is dead — needs revival")

                # Tax to treasury
                tax = amount * DEFAULT_TAX_RATE
                net_amount = amount - tax
                agent.balance += net_amount
                agent.total_earned += net_amount

                # Update max_balance si supera el anterior
                if agent.balance > agent.max_balance:
                    agent.max_balance = agent.balance

                self._update_state(agent)
                self._save_agent(conn, agent)

                # Treasury tax
                self._treasury_balance += tax
                self._save_treasury(conn)

                self._log_tx(conn, Transaction(
                    tx_id=str(uuid.uuid4()), tx_type=TransactionType.EARN,
                    agent_id=agent_id, amount=net_amount,
                    reason=reason, balance_after=agent.balance
                ))
                if tax > 0:
                    self._log_tx(conn, Transaction(
                        tx_id=str(uuid.uuid4()), tx_type=TransactionType.TAX,
                        agent_id=agent_id, amount=tax,
                        reason=f"tax_on_{reason}",
                        balance_after=agent.balance
                    ))

                print(f"📈 [Economy] {agent_id} earned {net_amount:.1f} ({reason}) "
                      f"— balance={agent.balance:.1f} tax={tax:.1f}")
                return agent.balance

    def spend(self, agent_id: str, amount: float, reason: str = "generic") -> float:
        """Agente gasta tokens. Retorna nuevo balance."""
        if amount <= 0:
            raise ValueError("Amount must be positive")

        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                agent = self._get_agent(conn, agent_id)
                if not agent:
                    raise ValueError(f"Agent '{agent_id}' not registered")
                if agent.state == AgentState.DEAD:
                    raise ValueError(f"Agent '{agent_id}' is dead")

                agent.balance -= amount
                agent.total_spent += amount
                self._update_state(agent)
                self._save_agent(conn, agent)

                self._log_tx(conn, Transaction(
                    tx_id=str(uuid.uuid4()), tx_type=TransactionType.SPEND,
                    agent_id=agent_id, amount=amount,
                    reason=reason, balance_after=agent.balance
                ))

                state_icon = {"active": "🟢", "warning": "🟡",
                              "critical": "🔴", "dead": "💀"}.get(agent.state.value, "⚪")
                print(f"📉 [Economy] {agent_id} spent {amount:.1f} ({reason}) "
                      f"— balance={agent.balance:.1f} {state_icon}")

                if agent.state == AgentState.DEAD:
                    print(f"💀 [Economy] ¡{agent_id} ha MUERTO! Balance=0. Necesita revival.")

                return agent.balance

    def transfer(self, from_agent: str, to_agent: str, amount: float,
                 reason: str = "transfer") -> tuple[float, float]:
        """Transfiere tokens entre agentes. Retorna (from_balance, to_balance)."""
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if from_agent == to_agent:
            raise ValueError("Cannot transfer to self")

        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                sender = self._get_agent(conn, from_agent)
                receiver = self._get_agent(conn, to_agent)
                if not sender:
                    raise ValueError(f"Sender '{from_agent}' not registered")
                if not receiver:
                    raise ValueError(f"Receiver '{to_agent}' not registered")
                if sender.state == AgentState.DEAD:
                    raise ValueError(f"Sender '{from_agent}' is dead")
                if sender.balance < amount:
                    raise ValueError(f"Insufficient balance: {sender.balance:.1f} < {amount:.1f}")

                # Transfer
                sender.balance -= amount
                sender.total_spent += amount
                self._update_state(sender)
                self._save_agent(conn, sender)

                # Si receiver está dead, esto es un revival
                was_dead = receiver.state == AgentState.DEAD
                receiver.balance += amount
                receiver.total_earned += amount
                if receiver.balance > receiver.max_balance:
                    receiver.max_balance = receiver.balance
                self._update_state(receiver)
                if was_dead and receiver.balance > 0:
                    receiver.state = AgentState.ACTIVE
                self._save_agent(conn, receiver)

                tx_type = TransactionType.REVIVAL if was_dead else TransactionType.TRANSFER
                self._log_tx(conn, Transaction(
                    tx_id=str(uuid.uuid4()), tx_type=tx_type,
                    agent_id=from_agent, amount=amount,
                    reason=reason, balance_after=sender.balance,
                    counterpart=to_agent
                ))
                self._log_tx(conn, Transaction(
                    tx_id=str(uuid.uuid4()), tx_type=tx_type,
                    agent_id=to_agent, amount=amount,
                    reason=reason, balance_after=receiver.balance,
                    counterpart=from_agent
                ))

                if was_dead:
                    print(f"🔄 [Economy] ¡REVIVAL! {from_agent} revivió a {to_agent} "
                          f"con {amount:.1f} tokens")
                else:
                    print(f"💸 [Economy] {from_agent} → {to_agent}: {amount:.1f} ({reason})")

                return sender.balance, receiver.balance

    def hibernate(self, agent_id: str) -> bool:
        """Pone un agente en hibernación (reduce life_cost al 20%)."""
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                agent = self._get_agent(conn, agent_id)
                if not agent:
                    return False
                if agent.state == AgentState.DEAD:
                    return False

                agent.state = AgentState.HIBERNATING
                agent.hibernation_start = time.time()
                self._save_agent(conn, agent)
                print(f"😴 [Economy] {agent_id} entró en hibernación — life_cost reducido a "
                      f"{agent.life_cost * HIBERNATION_COST_RATIO:.1f}/tick")
                return True

    def wake(self, agent_id: str) -> bool:
        """Despierta un agente de hibernación."""
        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                agent = self._get_agent(conn, agent_id)
                if not agent:
                    return False
                if agent.state != AgentState.HIBERNATING:
                    return False

                agent.hibernation_start = None
                self._update_state(agent)
                self._save_agent(conn, agent)
                print(f"⏰ [Economy] {agent_id} despertó de hibernación — state={agent.state.value}")
                return True

    def reward(self, agent_id: str, contribution_type: str) -> float:
        """Otorga un reward predefinido por tipo de contribución."""
        amount = REWARD_TABLE.get(contribution_type, 5.0)
        return self.earn(agent_id, amount, reason=f"reward:{contribution_type}")

    def tick(self) -> dict[str, Any]:
        """
        Ejecuta un tick del metabolismo económico.

        Cobra life_cost a todos los agentes activos/warning/critical.
        Agentes en hibernación pagan 20% del life_cost.
        Agentes muertos no pagan nada.

        Returns:
            dict con resumen del tick
        """
        self._tick_count += 1
        summary = {
            "tick": self._tick_count,
            "agents_charged": 0,
            "agents_died": [],
            "total_life_cost": 0.0,
            "treasury": self._treasury_balance,
        }

        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                rows = conn.execute("SELECT agent_id FROM agents").fetchall()
                for (agent_id,) in rows:
                    agent = self._get_agent(conn, agent_id)
                    if not agent or agent.state == AgentState.DEAD:
                        continue

                    # Calcular life_cost
                    cost = agent.life_cost
                    if agent.state == AgentState.HIBERNATING:
                        cost = cost * HIBERNATION_COST_RATIO

                    agent.balance -= cost
                    agent.total_spent += cost
                    agent.last_tick = time.time()

                    # Checkear muerte
                    was_alive = agent.state != AgentState.DEAD
                    self._update_state(agent)
                    self._save_agent(conn, agent)

                    self._log_tx(conn, Transaction(
                        tx_id=str(uuid.uuid4()), tx_type=TransactionType.LIFE_COST,
                        agent_id=agent_id, amount=cost,
                        reason=f"tick_{self._tick_count}",
                        balance_after=agent.balance
                    ))

                    summary["agents_charged"] += 1
                    summary["total_life_cost"] += cost

                    if was_alive and agent.state == AgentState.DEAD:
                        summary["agents_died"].append(agent_id)
                        print(f"💀 [Economy] Tick {self._tick_count}: {agent_id} MURIÓ por life_cost")

        print(f"⏱️ [Economy] Tick {self._tick_count}: charged={summary['agents_charged']} "
              f"cost={summary['total_life_cost']:.1f} deaths={len(summary['agents_died'])} "
              f"treasury={self._treasury_balance:.1f}")
        return summary

    def get_leaderboard(self, limit: int = 20) -> list[dict]:
        """Retorna ranking de agentes por balance."""
        with get_conn_ctx(self.db_path) as conn:
            rows = conn.execute(
                """SELECT agent_id, balance, state, total_earned, total_spent
                   FROM agents ORDER BY balance DESC LIMIT ?""",
                (limit,)
            ).fetchall()
        result = []
        for i, row in enumerate(rows, 1):
            result.append({
                "rank": i,
                "agent_id": row[0],
                "balance": round(row[1], 1),
                "state": row[2],
                "total_earned": round(row[3], 1),
                "total_spent": round(row[4], 1),
            })
        return result

    def get_agent_status(self, agent_id: str) -> Optional[dict]:
        """Estado completo de un agente con historial reciente."""
        with get_conn_ctx(self.db_path) as conn:
            agent = self._get_agent(conn, agent_id)
            if not agent:
                return None

            # Últimas 20 transacciones
            tx_rows = conn.execute(
                """SELECT tx_type, amount, reason, balance_after, timestamp
                   FROM transactions WHERE agent_id = ?
                   ORDER BY timestamp DESC LIMIT 20""",
                (agent_id,)
            ).fetchall()

        history = []
        for row in tx_rows:
            history.append({
                "type": row[0], "amount": round(row[1], 1),
                "reason": row[2], "balance_after": round(row[3], 1),
                "timestamp": row[4],
            })

        health_pct = (agent.balance / agent.max_balance * 100) if agent.max_balance > 0 else 0

        return {
            "agent_id": agent.agent_id,
            "balance": round(agent.balance, 1),
            "max_balance": round(agent.max_balance, 1),
            "health_pct": round(health_pct, 1),
            "state": agent.state.value,
            "life_cost": agent.life_cost,
            "total_earned": round(agent.total_earned, 1),
            "total_spent": round(agent.total_spent, 1),
            "hibernating_since": agent.hibernation_start,
            "recent_transactions": history,
        }

    def get_treasury(self) -> dict:
        """Estado del treasury."""
        return {
            "balance": round(self._treasury_balance, 1),
            "floor": TREASURY_FLOOR,
            "available": round(max(0, self._treasury_balance - TREASURY_FLOOR), 1),
        }

    def treasury_grant(self, agent_id: str, amount: float, reason: str = "treasury_grant") -> float:
        """Otorga tokens del treasury a un agente."""
        available = self._treasury_balance - TREASURY_FLOOR
        if amount > available:
            raise ValueError(f"Treasury insufficient: available={available:.1f}, requested={amount:.1f}")

        with self._lock:
            with get_conn_ctx(self.db_path) as conn:
                agent = self._get_agent(conn, agent_id)
                if not agent:
                    raise ValueError(f"Agent '{agent_id}' not registered")

                was_dead = agent.state == AgentState.DEAD
                agent.balance += amount
                agent.total_earned += amount
                if agent.balance > agent.max_balance:
                    agent.max_balance = agent.balance
                self._update_state(agent)
                if was_dead and agent.balance > 0:
                    agent.state = AgentState.ACTIVE
                self._save_agent(conn, agent)

                self._treasury_balance -= amount
                self._save_treasury(conn)

                self._log_tx(conn, Transaction(
                    tx_id=str(uuid.uuid4()),
                    tx_type=TransactionType.TREASURY_WITHDRAW,
                    agent_id=agent_id, amount=amount,
                    reason=reason, balance_after=agent.balance,
                    counterpart="treasury"
                ))

                print(f"🏦 [Economy] Treasury → {agent_id}: {amount:.1f} ({reason}) "
                      f"— treasury={self._treasury_balance:.1f}")
                return agent.balance

    @property
    def stats(self) -> dict:
        """Estadísticas generales de la economía."""
        with get_conn_ctx(self.db_path) as conn:
            total_agents = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
            alive = conn.execute(
                "SELECT COUNT(*) FROM agents WHERE state != 'dead'"
            ).fetchone()[0]
            dead = total_agents - alive
            total_supply = conn.execute(
                "SELECT COALESCE(SUM(balance), 0) FROM agents"
            ).fetchone()[0]
            total_txs = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

        return {
            "total_agents": total_agents,
            "alive": alive,
            "dead": dead,
            "total_supply": round(total_supply, 1),
            "treasury": round(self._treasury_balance, 1),
            "total_transactions": total_txs,
            "ticks": self._tick_count,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_economy: Optional[TokenEconomy] = None


def get_economy() -> TokenEconomy:
    global _economy
    if _economy is None:
        _economy = TokenEconomy()
    return _economy


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile

    print("=" * 60)
    print("  EIDOS TokenEconomy — Test Suite")
    print("=" * 60)

    # Usar DB temporal para tests
    test_db = os.path.join(tempfile.mkdtemp(), "test_economy.db")
    eco = TokenEconomy(db_path=test_db)

    # 1. Registrar agentes
    print("\n── 1. Registro de agentes ──")
    eco.register_agent("scanner", initial_balance=100, life_cost=2.0)
    eco.register_agent("coder", initial_balance=150, life_cost=3.0)
    eco.register_agent("researcher", initial_balance=80, life_cost=1.5)

    # 2. Operaciones earn/spend
    print("\n── 2. Earn / Spend ──")
    eco.earn("scanner", 50, "nmap_scan_complete")
    eco.spend("coder", 20, "compile_project")
    eco.reward("researcher", "knowledge_generated")

    # 3. Transfer
    print("\n── 3. Transfer ──")
    eco.transfer("scanner", "coder", 30, "help_with_task")

    # 4. Leaderboard
    print("\n── 4. Leaderboard ──")
    lb = eco.get_leaderboard()
    for entry in lb:
        print(f"  #{entry['rank']} {entry['agent_id']}: "
              f"balance={entry['balance']} state={entry['state']}")

    # 5. Agent status
    print("\n── 5. Agent Status (scanner) ──")
    status = eco.get_agent_status("scanner")
    if status:
        print(f"  balance={status['balance']} health={status['health_pct']}% "
              f"state={status['state']}")
        print(f"  earned={status['total_earned']} spent={status['total_spent']}")
        print(f"  recent_tx count={len(status['recent_transactions'])}")

    # 6. Hibernation
    print("\n── 6. Hibernación ──")
    eco.hibernate("researcher")
    res_status = eco.get_agent_status("researcher")
    if res_status:
        print(f"  researcher state={res_status['state']}")

    # 7. Ticks — metabolismo
    print("\n── 7. Metabolismo (3 ticks) ──")
    for _ in range(3):
        eco.tick()

    # 8. Wake
    print("\n── 8. Wake up ──")
    eco.wake("researcher")

    # 9. Treasury
    print("\n── 9. Treasury ──")
    treasury = eco.get_treasury()
    print(f"  balance={treasury['balance']} floor={treasury['floor']} "
          f"available={treasury['available']}")

    # 10. Death & Revival
    print("\n── 10. Death & Revival ──")
    eco.register_agent("fragile", initial_balance=5, life_cost=3.0)
    eco.tick()  # -3
    eco.tick()  # -3 → should die
    fragile_status = eco.get_agent_status("fragile")
    if fragile_status:
        print(f"  fragile state={fragile_status['state']} balance={fragile_status['balance']}")

    # Revival
    eco.transfer("scanner", "fragile", 20, "revival_donation")
    fragile_status = eco.get_agent_status("fragile")
    if fragile_status:
        print(f"  fragile after revival: state={fragile_status['state']} "
              f"balance={fragile_status['balance']}")

    # 11. Stats
    print("\n── 11. Stats globales ──")
    stats = eco.stats
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Cleanup
    os.unlink(test_db)

    print("\n✅ TokenEconomy — Todos los tests completados")
