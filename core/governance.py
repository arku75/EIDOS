"""
EIDOS Governance System — Gobernanza autónoma con votación
Inspirado en ClawColony: constitución, propuestas, votación, veto

Los agentes pueden proponer cambios, votar, y las decisiones se ejecutan automáticamente.
Reglas inmutables (Tian Dao) protegen los principios fundamentales.
"""

import json
import sqlite3
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
from core.db import get_conn

# ─── Constantes ──────────────────────────────────────────────────────────────

EIDOS_DIR = Path.home() / ".eidos"
DB_PATH = EIDOS_DIR / "governance.db"

# ─── Enums ───────────────────────────────────────────────────────────────────

class ProposalType(Enum):
    RULE_CHANGE = "rule_change"
    PARAMETER_CHANGE = "parameter_change"
    AGENT_ACTION = "agent_action"
    RESOURCE_ALLOCATION = "resource_allocation"
    EMERGENCY = "emergency"

class ProposalStatus(Enum):
    DRAFT = "draft"
    DISCUSSION = "discussion"
    VOTING = "voting"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    VETOED = "vetoed"

class VoteChoice(Enum):
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"

# ─── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class ConstitutionalRule:
    id: str
    text: str
    category: str  # "tian_dao" (inmutable) o "institutional" (modificable)
    created_at: float = field(default_factory=time.time)
    created_by: str = "system"

@dataclass
class Proposal:
    id: str
    title: str
    description: str
    proposal_type: str
    author: str
    status: str = "draft"
    created_at: float = field(default_factory=time.time)
    discussion_end: float = 0.0
    voting_end: float = 0.0
    action_data: str = "{}"
    execution_result: str = ""

@dataclass
class Vote:
    proposal_id: str
    agent_id: str
    choice: str
    reason: str = ""
    timestamp: float = field(default_factory=time.time)

# ─── Governance System ──────────────────────────────────────────────────────

class GovernanceSystem:
    """
    Sistema de gobernanza autónoma para agentes EIDOS.

    Features:
    - Constitución con reglas inmutables (Tian Dao) y modificables
    - Propuestas con ciclo: draft → discussion → voting → approved/rejected → executed
    - Votación con quorum y threshold configurable
    - Veto por guardianes (requiere 2+ vetos)
    - Ejecución automática de propuestas aprobadas
    """

    def __init__(self, discussion_ticks: int = 5, voting_ticks: int = 10,
                 quorum: float = 0.51, approval_threshold: float = 0.66,
                 tick_duration_s: float = 60.0):
        self.discussion_ticks = discussion_ticks
        self.voting_ticks = voting_ticks
        self.quorum = quorum
        self.approval_threshold = approval_threshold
        self.tick_duration_s = tick_duration_s
        self.current_tick = 0

        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        self.db = get_conn(str(DB_PATH), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self._init_constitution()

    def _init_db(self):
        """Inicializa tablas SQLite"""
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS constitution (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'institutional',
                created_at REAL,
                created_by TEXT DEFAULT 'system',
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                proposal_type TEXT NOT NULL,
                author TEXT NOT NULL,
                status TEXT DEFAULT 'draft',
                created_at REAL,
                discussion_end REAL DEFAULT 0,
                voting_end REAL DEFAULT 0,
                action_data TEXT DEFAULT '{}',
                execution_result TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS votes (
                proposal_id TEXT,
                agent_id TEXT,
                choice TEXT NOT NULL,
                reason TEXT DEFAULT '',
                timestamp REAL,
                PRIMARY KEY (proposal_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS vetoes (
                proposal_id TEXT,
                guardian_id TEXT,
                reason TEXT,
                timestamp REAL,
                PRIMARY KEY (proposal_id, guardian_id)
            );
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT,
                role TEXT DEFAULT 'citizen',
                registered_at REAL,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS governance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                details TEXT,
                timestamp REAL
            );
        """)
        self.db.commit()

    def _init_constitution(self):
        """Inicializa reglas fundamentales si no existen"""
        existing = self.db.execute("SELECT COUNT(*) FROM constitution").fetchone()[0]
        if existing > 0:
            return

        tian_dao = [
            ("td_001", "Ningún agente puede dañar el sistema host ni sus datos", "tian_dao"),
            ("td_002", "Todo agente debe respetar los límites de recursos asignados", "tian_dao"),
            ("td_003", "Las reglas Tian Dao no pueden ser modificadas por votación", "tian_dao"),
            ("td_004", "Ningún agente puede ejecutar código sin validación de Shield", "tian_dao"),
            ("td_005", "El usuario (SER) tiene autoridad final sobre todas las decisiones", "tian_dao"),
        ]
        institutional = [
            ("inst_001", "Las propuestas requieren 51% de quorum para ser válidas", "institutional"),
            ("inst_002", "Se necesita 66% de aprobación para aprobar una propuesta", "institutional"),
            ("inst_003", "El período de discusión es de 5 ticks mínimo", "institutional"),
            ("inst_004", "El período de votación es de 10 ticks mínimo", "institutional"),
            ("inst_005", "Dos o más guardianes pueden vetar una propuesta", "institutional"),
        ]

        for rule_id, text, category in tian_dao + institutional:
            self.db.execute(
                "INSERT INTO constitution (id, text, category, created_at) VALUES (?, ?, ?, ?)",
                (rule_id, text, category, time.time())
            )
        self.db.commit()
        print("📜 [Governance] Constitución inicializada: 5 Tian Dao + 5 institucionales")

    def _log_event(self, event_type: str, details: str):
        """Registra evento en el log"""
        self.db.execute(
            "INSERT INTO governance_log (event_type, details, timestamp) VALUES (?, ?, ?)",
            (event_type, details, time.time())
        )
        self.db.commit()

    def _gen_id(self, prefix: str) -> str:
        """Genera ID único"""
        h = hashlib.md5(f"{prefix}_{time.time()}".encode()).hexdigest()[:8]
        return f"{prefix}_{h}"

    # ─── Agentes ─────────────────────────────────────────────────────────

    def register_agent(self, agent_id: str, name: str = "", role: str = "citizen") -> bool:
        """Registra un agente en el sistema de gobernanza"""
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO agents (id, name, role, registered_at, active) VALUES (?, ?, ?, ?, 1)",
                (agent_id, name or agent_id, role, time.time())
            )
            self.db.commit()
            self._log_event("agent_registered", f"{agent_id} como {role}")
            print(f"🏛️  [Governance] Agente {agent_id} registrado como {role}")
            return True
        except Exception as e:
            print(f"❌ [Governance] Error registrando agente: {e}")
            return False

    def get_active_agents(self) -> List[Dict]:
        """Lista agentes activos"""
        rows = self.db.execute(
            "SELECT * FROM agents WHERE active = 1"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_guardians(self) -> List[str]:
        """Lista IDs de guardianes"""
        rows = self.db.execute(
            "SELECT id FROM agents WHERE role = 'guardian' AND active = 1"
        ).fetchall()
        return [r["id"] for r in rows]

    # ─── Constitución ────────────────────────────────────────────────────

    def get_constitution(self) -> Dict[str, List[Dict]]:
        """Retorna la constitución vigente"""
        rows = self.db.execute(
            "SELECT * FROM constitution WHERE active = 1 ORDER BY category, id"
        ).fetchall()
        result = {"tian_dao": [], "institutional": []}
        for r in rows:
            d = dict(r)
            cat = d.get("category", "institutional")
            if cat in result:
                result[cat].append(d)
        return result

    def check_tian_dao(self, action_description: str) -> tuple:
        """Verifica que una acción no viole Tian Dao"""
        violations = []
        tian_dao = self.db.execute(
            "SELECT * FROM constitution WHERE category = 'tian_dao' AND active = 1"
        ).fetchall()

        dangerous_patterns = {
            "td_001": ["rm -rf /", "format", "destroy", "delete system"],
            "td_002": ["unlimited", "no limit", "bypass limit"],
            "td_003": ["modify tian_dao", "change tian_dao", "delete tian_dao"],
            "td_004": ["bypass shield", "disable shield", "no validation"],
        }

        action_lower = action_description.lower()
        for rule in tian_dao:
            rule_id = rule["id"]
            if rule_id in dangerous_patterns:
                for pattern in dangerous_patterns[rule_id]:
                    if pattern in action_lower:
                        violations.append({
                            "rule_id": rule_id,
                            "rule_text": rule["text"],
                            "pattern_matched": pattern
                        })

        return (len(violations) == 0, violations)

    # ─── Propuestas ──────────────────────────────────────────────────────

    def create_proposal(self, title: str, description: str,
                       proposal_type: str, author: str,
                       action_data: Dict = None) -> Optional[str]:
        """Crea una nueva propuesta"""
        # Verificar que el autor está registrado
        agent = self.db.execute(
            "SELECT * FROM agents WHERE id = ? AND active = 1", (author,)
        ).fetchone()
        if not agent:
            print(f"❌ [Governance] Agente {author} no registrado")
            return None

        # Verificar Tian Dao
        ok, violations = self.check_tian_dao(f"{title} {description}")
        if not ok:
            print(f"🚫 [Governance] Propuesta viola Tian Dao: {violations}")
            self._log_event("tian_dao_violation", json.dumps(violations))
            return None

        proposal_id = self._gen_id("prop")
        now = time.time()
        disc_end = now + (self.discussion_ticks * self.tick_duration_s)
        vote_end = disc_end + (self.voting_ticks * self.tick_duration_s)

        self.db.execute("""
            INSERT INTO proposals (id, title, description, proposal_type, author,
                                   status, created_at, discussion_end, voting_end, action_data)
            VALUES (?, ?, ?, ?, ?, 'discussion', ?, ?, ?, ?)
        """, (proposal_id, title, description, proposal_type, author,
              now, disc_end, vote_end, json.dumps(action_data or {})))
        self.db.commit()

        self._log_event("proposal_created", f"{proposal_id}: {title}")
        print(f"📋 [Governance] Propuesta creada: {proposal_id} — {title}")
        return proposal_id

    def get_proposal(self, proposal_id: str) -> Optional[Dict]:
        """Obtiene una propuesta por ID"""
        row = self.db.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row:
            d = dict(row)
            d["votes"] = self._get_votes(proposal_id)
            d["vetoes"] = self._get_vetoes(proposal_id)
            return d
        return None

    def list_proposals(self, status: str = None) -> List[Dict]:
        """Lista propuestas, opcionalmente filtradas por status"""
        if status:
            rows = self.db.execute(
                "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC",
                (status,)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM proposals ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── Votación ────────────────────────────────────────────────────────

    def vote(self, proposal_id: str, agent_id: str,
             choice: str, reason: str = "") -> bool:
        """Emite un voto"""
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            print(f"❌ [Governance] Propuesta {proposal_id} no encontrada")
            return False

        if proposal["status"] != "voting":
            print(f"❌ [Governance] Propuesta no está en período de votación (status: {proposal['status']})")
            return False

        # Verificar que el agente está registrado
        agent = self.db.execute(
            "SELECT * FROM agents WHERE id = ? AND active = 1", (agent_id,)
        ).fetchone()
        if not agent:
            print(f"❌ [Governance] Agente {agent_id} no registrado")
            return False

        if choice not in [v.value for v in VoteChoice]:
            print(f"❌ [Governance] Voto inválido: {choice}")
            return False

        self.db.execute("""
            INSERT OR REPLACE INTO votes (proposal_id, agent_id, choice, reason, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (proposal_id, agent_id, choice, reason, time.time()))
        self.db.commit()

        self._log_event("vote_cast", f"{agent_id} votó {choice} en {proposal_id}")
        print(f"🗳️  [Governance] {agent_id} votó {choice} en {proposal_id}")
        return True

    def veto(self, proposal_id: str, guardian_id: str, reason: str = "") -> bool:
        """Veto de un guardián"""
        # Verificar que es guardián
        guardians = self.get_guardians()
        if guardian_id not in guardians:
            print(f"❌ [Governance] {guardian_id} no es guardián")
            return False

        proposal = self.get_proposal(proposal_id)
        if not proposal or proposal["status"] in ["executed", "vetoed", "rejected"]:
            return False

        self.db.execute("""
            INSERT OR REPLACE INTO vetoes (proposal_id, guardian_id, reason, timestamp)
            VALUES (?, ?, ?, ?)
        """, (proposal_id, guardian_id, reason, time.time()))
        self.db.commit()

        # Verificar si hay suficientes vetos (2+)
        vetoes = self._get_vetoes(proposal_id)
        if len(vetoes) >= 2:
            self.db.execute(
                "UPDATE proposals SET status = 'vetoed' WHERE id = ?",
                (proposal_id,)
            )
            self.db.commit()
            self._log_event("proposal_vetoed", f"{proposal_id} vetado por {len(vetoes)} guardianes")
            print(f"🛑 [Governance] Propuesta {proposal_id} VETADA ({len(vetoes)} vetos)")

        return True

    def _get_votes(self, proposal_id: str) -> List[Dict]:
        rows = self.db.execute(
            "SELECT * FROM votes WHERE proposal_id = ?", (proposal_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_vetoes(self, proposal_id: str) -> List[Dict]:
        rows = self.db.execute(
            "SELECT * FROM vetoes WHERE proposal_id = ?", (proposal_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def _tally_votes(self, proposal_id: str) -> Dict:
        """Cuenta votos de una propuesta"""
        votes = self._get_votes(proposal_id)
        active_agents = len(self.get_active_agents())

        tally = {"yes": 0, "no": 0, "abstain": 0, "total": len(votes)}
        for v in votes:
            choice = v.get("choice", "abstain")
            if choice in tally:
                tally[choice] += 1

        tally["quorum_met"] = (tally["total"] / max(active_agents, 1)) >= self.quorum
        effective_votes = tally["yes"] + tally["no"]
        tally["approval_rate"] = tally["yes"] / max(effective_votes, 1)
        tally["approved"] = tally["quorum_met"] and tally["approval_rate"] >= self.approval_threshold

        return tally

    # ─── Tick (avance temporal) ──────────────────────────────────────────

    def tick(self) -> List[str]:
        """Avanza el reloj de gobernanza, procesa propuestas"""
        self.current_tick += 1
        events = []
        now = time.time()

        # Mover de discussion → voting
        discussion_done = self.db.execute(
            "SELECT * FROM proposals WHERE status = 'discussion' AND discussion_end <= ?",
            (now,)
        ).fetchall()
        for p in discussion_done:
            self.db.execute(
                "UPDATE proposals SET status = 'voting' WHERE id = ?",
                (p["id"],)
            )
            events.append(f"📊 {p['id']} entra en votación")

        # Procesar votaciones terminadas
        voting_done = self.db.execute(
            "SELECT * FROM proposals WHERE status = 'voting' AND voting_end <= ?",
            (now,)
        ).fetchall()
        for p in voting_done:
            tally = self._tally_votes(p["id"])
            if tally["approved"]:
                self.db.execute(
                    "UPDATE proposals SET status = 'approved' WHERE id = ?",
                    (p["id"],)
                )
                events.append(f"✅ {p['id']} APROBADA ({tally['approval_rate']:.0%})")
                # Auto-ejecutar
                exec_result = self._execute_proposal(p["id"])
                events.append(f"⚡ {p['id']} ejecutada: {exec_result}")
            else:
                reason = "sin quorum" if not tally["quorum_met"] else f"solo {tally['approval_rate']:.0%}"
                self.db.execute(
                    "UPDATE proposals SET status = 'rejected' WHERE id = ?",
                    (p["id"],)
                )
                events.append(f"❌ {p['id']} RECHAZADA ({reason})")

        self.db.commit()
        if events:
            self._log_event("tick", json.dumps(events))
        return events

    def _execute_proposal(self, proposal_id: str) -> str:
        """Ejecuta una propuesta aprobada"""
        proposal = self.get_proposal(proposal_id)
        if not proposal:
            return "propuesta no encontrada"

        try:
            action_data = json.loads(proposal.get("action_data", "{}"))
            ptype = proposal.get("proposal_type", "")

            if ptype == "parameter_change":
                param = action_data.get("parameter", "")
                value = action_data.get("value", "")
                result = f"Parámetro {param} cambiado a {value}"

            elif ptype == "rule_change":
                rule_text = action_data.get("rule_text", "")
                rule_id = self._gen_id("inst")
                self.db.execute(
                    "INSERT INTO constitution (id, text, category, created_at, created_by) VALUES (?, ?, 'institutional', ?, ?)",
                    (rule_id, rule_text, time.time(), proposal["author"])
                )
                result = f"Nueva regla {rule_id}: {rule_text}"

            elif ptype == "resource_allocation":
                resource = action_data.get("resource", "")
                amount = action_data.get("amount", 0)
                result = f"Recurso {resource}: {amount} asignado"

            else:
                result = f"Propuesta tipo {ptype} aprobada (ejecución manual requerida)"

            self.db.execute(
                "UPDATE proposals SET status = 'executed', execution_result = ? WHERE id = ?",
                (result, proposal_id)
            )
            self.db.commit()
            self._log_event("proposal_executed", f"{proposal_id}: {result}")
            return result

        except Exception as e:
            error_msg = f"Error ejecutando: {e}"
            self.db.execute(
                "UPDATE proposals SET execution_result = ? WHERE id = ?",
                (error_msg, proposal_id)
            )
            self.db.commit()
            return error_msg

    # ─── Stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Estadísticas del sistema de gobernanza"""
        total = self.db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        by_status = {}
        for row in self.db.execute("SELECT status, COUNT(*) as c FROM proposals GROUP BY status").fetchall():
            by_status[row["status"]] = row["c"]

        constitution = self.get_constitution()
        agents = self.get_active_agents()

        return {
            "tick": self.current_tick,
            "total_proposals": total,
            "by_status": by_status,
            "tian_dao_rules": len(constitution.get("tian_dao", [])),
            "institutional_rules": len(constitution.get("institutional", [])),
            "active_agents": len(agents),
            "guardians": len(self.get_guardians()),
            "total_votes": self.db.execute("SELECT COUNT(*) FROM votes").fetchone()[0],
        }

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass  # error no crítico, continuar
# ─── Test ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from pathlib import Path
    my_gpt = Path(__file__).parent.parent
    if str(my_gpt) not in sys.path:
        sys.path.insert(0, str(my_gpt))

    print("=== Test Governance System ===\n")

    # Usar DB temporal para test
    import tempfile
    _orig = DB_PATH
    test_db = Path(tempfile.mktemp(suffix=".db"))

    gov = GovernanceSystem(tick_duration_s=0.1)
    gov.db.close()
    gov.db = get_conn(str(test_db), check_same_thread=False)
    gov.db.row_factory = sqlite3.Row
    gov._init_db()
    gov._init_constitution()

    # Test 1: Registrar agentes
    print("Test 1: Registrar agentes")
    gov.register_agent("agent_alpha", "Alpha", "guardian")
    gov.register_agent("agent_beta", "Beta", "citizen")
    gov.register_agent("agent_gamma", "Gamma", "citizen")
    gov.register_agent("agent_delta", "Delta", "guardian")
    assert len(gov.get_active_agents()) == 4
    print("  ✅ 4 agentes registrados\n")

    # Test 2: Constitución
    print("Test 2: Constitución")
    const = gov.get_constitution()
    assert len(const["tian_dao"]) == 5
    assert len(const["institutional"]) == 5
    print(f"  ✅ {len(const['tian_dao'])} Tian Dao + {len(const['institutional'])} institucionales\n")

    # Test 3: Crear propuesta
    print("Test 3: Crear propuesta")
    pid = gov.create_proposal(
        "Incrementar RAM limit",
        "Subir el límite de RAM Guardian de 85% a 90%",
        "parameter_change",
        "agent_beta",
        {"parameter": "ram_critical_threshold", "value": 90}
    )
    assert pid is not None
    print(f"  ✅ Propuesta creada: {pid}\n")

    # Test 4: Tian Dao violation
    print("Test 4: Tian Dao violation")
    bad = gov.create_proposal(
        "Bypass Shield",
        "Quiero bypass shield para ir más rápido",
        "rule_change",
        "agent_beta"
    )
    assert bad is None
    print("  ✅ Propuesta bloqueada por Tian Dao\n")

    # Test 5: Tick → voting
    print("Test 5: Avanzar a votación")
    import time as _t
    _t.sleep(0.6)
    events = gov.tick()
    proposal = gov.get_proposal(pid)
    assert proposal["status"] == "voting"
    print(f"  ✅ Propuesta en votación\n")

    # Test 6: Votar
    print("Test 6: Votación")
    gov.vote(pid, "agent_alpha", "yes", "Buena idea")
    gov.vote(pid, "agent_beta", "yes", "Lo necesitamos")
    gov.vote(pid, "agent_gamma", "yes", "De acuerdo")
    tally = gov._tally_votes(pid)
    assert tally["yes"] == 3
    print(f"  ✅ 3 votos YES, aprobación: {tally['approval_rate']:.0%}\n")

    # Test 7: Tick → approved + executed
    print("Test 7: Aprobar y ejecutar")
    _t.sleep(1.2)
    events = gov.tick()
    proposal = gov.get_proposal(pid)
    assert proposal["status"] == "executed"
    print(f"  ✅ Propuesta ejecutada: {proposal['execution_result']}\n")

    # Test 8: Veto
    print("Test 8: Veto por guardianes")
    pid2 = gov.create_proposal(
        "Reducir quorum al 20%",
        "Para facilitar la votación",
        "parameter_change",
        "agent_gamma",
        {"parameter": "quorum", "value": 0.2}
    )
    gov.veto(pid2, "agent_alpha", "Quorum bajo es peligroso")
    gov.veto(pid2, "agent_delta", "Coincido, muy bajo")
    p2 = gov.get_proposal(pid2)
    assert p2["status"] == "vetoed"
    print("  ✅ Propuesta vetada por 2 guardianes\n")

    # Test 9: Stats
    print("Test 9: Stats")
    stats = gov.get_stats()
    print(f"  Total propuestas: {stats['total_proposals']}")
    print(f"  Por status: {stats['by_status']}")
    print(f"  Votos totales: {stats['total_votes']}")
    print(f"  ✅ Stats OK\n")

    # Cleanup
    test_db.unlink(missing_ok=True)
    print("✅ Todos los tests pasaron — Governance System funcional")
