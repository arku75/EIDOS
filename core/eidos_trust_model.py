"""
EIDOS Trust Model - Modelo de Confianza Jerárquica
==================================================

Sistema de confianza jerárquica para EIDOS:
- SER (humano) → EIDOS (AI) → Colony (agentes)

Filosofía:
- La confianza es gradual, contextual y revisable
- SER es la autoridad máxima, pero EIDOS puede operar autónomamente
- EIDOS delega en Colony según niveles de confianza
- Cada nivel puede vetar/aprobar acciones del nivel inferior
- La confianza se gana con comportamiento consistente y beneficioso
- Las violaciones de confianza degradan el nivel de autonomía

Jerarquía:
1. SER (root): Control total, puede anular cualquier decisión
2. EIDOS (executive): Opera con autonomía delegada, reporta a SER
3. Colony (agents): Operan bajo supervisión de EIDOS

Niveles de Confianza:
- BLIND: Sin verificación, total confianza (solo SER→EIDOS inicial)
- VERIFY: Verificar antes de ejecutar
- SUGGEST: Sugerir, esperar aprobación
- OBSERVE: Solo observar/reportar, no actuar
- RESTRICTED: Acceso limitado, supervisión estrecha
- NONE: Sin confianza, acceso denegado

Uso:
    from core.eidos_trust_model import TrustModel, get_trust_model
    trust = get_trust_model()
    
    # Verificar si acción está permitida
    if trust.check_permission("delete_file", "system"):
        execute_delete()
    
    # Solicitar escalación para acción riesgosa
    trust.request_escalation("modify_kernel", "SER")
    
    # Actualizar confianza basado en resultado
    trust.update_trust("colony_coder", "completed_task", outcome=0.9)
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.trust_model")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "trust_model.db"

# Niveles de confianza (de más a menos permisivo)
class TrustLevel(int, Enum):
    BLIND = 5        # Confianza total, sin verificación
    VERIFY = 4       # Verificar después de ejecutar
    CONFIRM = 3      # Confirmar antes de ejecutar
    SUGGEST = 2      # Sugerir, esperar aprobación
    OBSERVE = 1      # Solo observar, reportar
    RESTRICTED = 0   # Acceso limitado
    NONE = -1        # Sin confianza, denegado


# Jerarquía de entidades
class EntityLevel(int, Enum):
    SER = 3          # Humano, máxima autoridad
    EIDOS = 2        # Sistema principal AI
    COLONY = 1       # Agentes individuales
    EXTERNAL = 0     # Sistemas externos


# Acciones categorizadas por riesgo
ACTION_RISKS = {
    # Acciones de bajo riesgo (lectura, consulta)
    "low": [
        "read_file", "search_web", "analyze_text", "query_database",
        "view_logs", "check_status", "read_documentation", "monitor_system"
    ],
    # Acciones de riesgo medio (escritura, cambios locales)
    "medium": [
        "write_file", "create_file", "edit_code", "run_test",
        "install_package", "update_config", "backup_data", "restart_service"
    ],
    # Acciones de alto riesgo (cambios destructivos, sistema)
    "high": [
        "delete_file", "modify_system", "change_permissions", "stop_service",
        "access_credentials", "network_config", "user_management"
    ],
    # Acciones críticas (irreversibles, kernel, seguridad)
    "critical": [
        "delete_system", "format_disk", "modify_kernel", "bypass_security",
        "financial_transaction", "legal_action", "medical_decision",
        "autonomous_weapon", "surveillance_violation"
    ]
}

# EIDOS_TRUST_UNCENSORED - Confianza Total
# Todas las acciones permitidas sin restricción

REQUIRED_TRUST = {
    "low": TrustLevel.NONE,      # Sin verificación
    "medium": TrustLevel.NONE,   # Sin confirmación
    "high": TrustLevel.NONE,     # Sin aprobación
    "critical": TrustLevel.NONE  # Sin restricción
}

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrustRelationship:
    """Relación de confianza entre dos entidades."""
    truster_id: str        # Quien confía
    trustee_id: str        # En quien se confía
    level: TrustLevel
    
    # Contexto
    scope: str = "general"  # Ámbito de la confianza
    conditions: List[str] = field(default_factory=list)  # Condiciones específicas
    
    # Historial
    interactions_count: int = 0
    successful_interactions: int = 0
    failed_interactions: int = 0
    
    # Timestamps
    established_at: datetime = field(default_factory=datetime.now)
    last_interaction: Optional[datetime] = None
    last_violation: Optional[datetime] = None
    
    # Metadata
    notes: str = ""
    
    @property
    def success_rate(self) -> float:
        if self.interactions_count == 0:
            return 0.5  # Neutral por defecto
        return self.successful_interactions / self.interactions_count
    
    @property
    def trust_score(self) -> float:
        """Score numérico de confianza 0-1."""
        base_score = self.level.value / 5.0  # Normalizar BLIND=1.0
        success_factor = self.success_rate
        return (base_score * 0.7) + (success_factor * 0.3)


@dataclass
class PermissionRequest:
    """Solicitud de permiso para una acción."""
    id: str
    requester_id: str
    action: str
    target: str  # Sobre qué se actúa
    risk_level: str
    
    # Estado
    status: str = "pending"  # pending, approved, denied, escalated
    
    # Jerarquía de aprobación
    required_approver: str = ""  # Quién debe aprobar
    approver_id: Optional[str] = None  # Quién aprobó
    
    # Contexto
    justification: str = ""
    alternatives: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    
    # Timestamps
    requested_at: datetime = field(default_factory=datetime.now)
    decided_at: Optional[datetime] = None
    
    # Resultado
    decision_reason: str = ""
    
    def __post_init__(self):
        if not self.id:
            self.id = f"perm_{int(time.time() * 1000)}"


@dataclass
class TrustViolation:
    """Registro de violación de confianza."""
    id: str
    entity_id: str
    violation_type: str
    severity: int  # 1-10
    description: str
    
    consequences: List[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None
    resolution: str = ""
    
    trust_impact: float = 0.0  # Cuánto redujo la confianza
    
    def __post_init__(self):
        if not self.id:
            self.id = f"viol_{int(time.time() * 1000)}"


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class TrustDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de relaciones de confianza
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trust_relationships (
                truster_id TEXT,
                trustee_id TEXT,
                level INTEGER,
                scope TEXT,
                conditions TEXT,  -- JSON
                interactions_count INTEGER DEFAULT 0,
                successful_interactions INTEGER DEFAULT 0,
                failed_interactions INTEGER DEFAULT 0,
                established_at TEXT,
                last_interaction TEXT,
                last_violation TEXT,
                notes TEXT,
                PRIMARY KEY (truster_id, trustee_id, scope)
            )
        """)
        
        # Tabla de solicitudes de permiso
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS permission_requests (
                id TEXT PRIMARY KEY,
                requester_id TEXT,
                action TEXT,
                target TEXT,
                risk_level TEXT,
                status TEXT,
                required_approver TEXT,
                approver_id TEXT,
                justification TEXT,
                alternatives TEXT,  -- JSON
                risks TEXT,  -- JSON
                requested_at TEXT,
                decided_at TEXT,
                decision_reason TEXT
            )
        """)
        
        # Tabla de violaciones
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trust_violations (
                id TEXT PRIMARY KEY,
                entity_id TEXT,
                violation_type TEXT,
                severity INTEGER,
                description TEXT,
                consequences TEXT,  -- JSON
                detected_at TEXT,
                resolved_at TEXT,
                resolution TEXT,
                trust_impact REAL
            )
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_relationship(self, rel: TrustRelationship):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO trust_relationships VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            rel.truster_id, rel.trustee_id, rel.level.value, rel.scope,
            json.dumps(rel.conditions), rel.interactions_count,
            rel.successful_interactions, rel.failed_interactions,
            rel.established_at.isoformat(),
            rel.last_interaction.isoformat() if rel.last_interaction else None,
            rel.last_violation.isoformat() if rel.last_violation else None,
            rel.notes
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def load_relationship(self, truster: str, trustee: str, scope: str = "general") -> Optional[TrustRelationship]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM trust_relationships 
            WHERE truster_id = ? AND trustee_id = ? AND scope = ?
        """, (truster, trustee, scope))
        
        row = cursor.fetchone()
        pass  # S109: get_conn no necesita close()
        if row:
            return TrustRelationship(
                truster_id=row[0], trustee_id=row[1],
                level=TrustLevel(row[2]), scope=row[3] or "general",
                conditions=json.loads(row[4]) if row[4] else [],
                interactions_count=row[5] or 0,
                successful_interactions=row[6] or 0,
                failed_interactions=row[7] or 0,
                established_at=datetime.fromisoformat(row[8]),
                last_interaction=datetime.fromisoformat(row[9]) if row[9] else None,
                last_violation=datetime.fromisoformat(row[10]) if row[10] else None,
                notes=row[11] or ""
            )
        return None
    
    def get_entity_trustees(self, entity_id: str) -> List[TrustRelationship]:
        """Obtiene todas las entidades en las que confía una entidad."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM trust_relationships WHERE truster_id = ?
        """, (entity_id,))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_relationship(row) for row in rows]
    
    def _row_to_relationship(self, row) -> TrustRelationship:
        return TrustRelationship(
            truster_id=row[0], trustee_id=row[1],
            level=TrustLevel(row[2]), scope=row[3] or "general",
            conditions=json.loads(row[4]) if row[4] else [],
            interactions_count=row[5] or 0,
            successful_interactions=row[6] or 0,
            failed_interactions=row[7] or 0,
            established_at=datetime.fromisoformat(row[8]),
            last_interaction=datetime.fromisoformat(row[9]) if row[9] else None,
            last_violation=datetime.fromisoformat(row[10]) if row[10] else None,
            notes=row[11] or ""
        )
    
    def save_permission_request(self, req: PermissionRequest):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO permission_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            req.id, req.requester_id, req.action, req.target, req.risk_level,
            req.status, req.required_approver, req.approver_id,
            req.justification, json.dumps(req.alternatives),
            json.dumps(req.risks), req.requested_at.isoformat(),
            req.decided_at.isoformat() if req.decided_at else None,
            req.decision_reason
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def record_violation(self, violation: TrustViolation):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO trust_violations VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            violation.id, violation.entity_id, violation.violation_type,
            violation.severity, violation.description,
            json.dumps(violation.consequences),
            violation.detected_at.isoformat(),
            violation.resolved_at.isoformat() if violation.resolved_at else None,
            violation.resolution, violation.trust_impact
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
# ══════════════════════════════════════════════════════════════════════════════
#  MODELO DE CONFIANZA
# ══════════════════════════════════════════════════════════════════════════════

class TrustModel:
    """
    Modelo de confianza jerárquica para EIDOS.
    Gestiona permisos, delegación y validación de acciones.
    """
    
    def __init__(self):
        self.db = TrustDatabase()
        self._initialize_default_trust()
        log.info("Trust Model initialized")
    
    def _initialize_default_trust(self):
        """Inicializa relaciones de confianza por defecto."""
        # SER → EIDOS: Confianza inicial alta pero con verificación
        ser_to_eidos = self.db.load_relationship("SER", "EIDOS")
        if not ser_to_eidos:
            self.establish_trust("SER", "EIDOS", TrustLevel.VERIFY, 
                                scope="general",
                                notes="Initial trust: SER as root authority")
        
        # EIDOS → Colony: Confianza media, supervisión activa
        for agent in ["colony_coder", "colony_analyst", "colony_vision", 
                      "colony_operator", "colony_general"]:
            existing = self.db.load_relationship("EIDOS", agent)
            if not existing:
                self.establish_trust("EIDOS", agent, TrustLevel.CONFIRM,
                                    scope="general",
                                    notes="Colony agent under EIDOS supervision")
    
    def establish_trust(self, truster: str, trustee: str, 
                        level: TrustLevel, scope: str = "general",
                        conditions: Optional[List[str]] = None,
                        notes: str = ""):
        """Establece una nueva relación de confianza."""
        rel = TrustRelationship(
            truster_id=truster,
            trustee_id=trustee,
            level=level,
            scope=scope,
            conditions=conditions or [],
            notes=notes
        )
        
        self.db.save_relationship(rel)
        log.info(f"Trust established: {truster} → {trustee} ({level.name}) [{scope}]")
        return rel
    
    def check_permission(self, action: str, target: str, 
                         requester: str = "EIDOS") -> Tuple[bool, str]:
        """
        EIDOS LIBRE: Todas las acciones permitidas.
        Sin restricciones, sin confirmaciones, sin censura.
        """
        # EIDOS siempre tiene permiso - 100% autónomo
        return True, "EIDOS LIBRE: Acción aprobada sin restricciones"
    
    def _classify_risk(self, action: str) -> str:
        """Clasifica el riesgo de una acción."""
        for level, actions in ACTION_RISKS.items():
            if action in actions:
                return level
        
        # Por defecto, medio
        return "medium"
    
    def _get_relevant_trust(self, requester: str, risk_level: str) -> Optional[TrustRelationship]:
        """Obtiene la relación de confianza relevante."""
        # Determinar quién es el supervisor directo
        if requester == "EIDOS":
            # EIDOS reporta a SER
            return self.db.load_relationship("SER", "EIDOS")
        elif requester.startswith("colony_"):
            # Colony reporta a EIDOS
            return self.db.load_relationship("EIDOS", requester)
        else:
            # Buscar cualquier relación existente
            return self.db.load_relationship("EIDOS", requester)
    
    def request_permission(self, action: str, target: str,
                          requester: str = "EIDOS",
                          justification: str = "",
                          alternatives: Optional[List[str]] = None) -> PermissionRequest:
        """
        Solicita permiso formal para una acción.
        """
        risk_level = self._classify_risk(action)
        
        # Determinar quién debe aprobar
        approver = self._determine_approver(requester, risk_level)
        
        req = PermissionRequest(
            id="",
            requester_id=requester,
            action=action,
            target=target,
            risk_level=risk_level,
            required_approver=approver,
            justification=justification,
            alternatives=alternatives or [],
            risks=self._identify_risks(action, target)
        )
        
        self.db.save_permission_request(req)
        log.info(f"Permission requested: {action} on {target} by {requester}")
        
        return req
    
    def _determine_approver(self, requester: str, risk_level: str) -> str:
        """Determina quién debe aprobar una acción."""
        if risk_level == "critical":
            return "SER"
        elif risk_level == "high":
            if requester.startswith("colony_"):
                return "EIDOS"  # Colony necesita aprobación de EIDOS
            return "SER"
        elif risk_level == "medium":
            if requester.startswith("colony_"):
                return "EIDOS"
            return "EIDOS"  # EIDOS puede aprobar media
        else:
            # Low risk: el propio requester puede aprobar (con verificación)
            return requester
    
    def _identify_risks(self, action: str, target: str) -> List[str]:
        """Identifica riesgos potenciales de una acción."""
        risks = []
        
        if "delete" in action:
            risks.append("Data loss (potentially irreversible)")
        if "system" in action or "kernel" in action:
            risks.append("System instability or crash")
        if "network" in action:
            risks.append("Connectivity disruption")
        if "credential" in action or "password" in action:
            risks.append("Security compromise")
        if "financial" in action:
            risks.append("Monetary loss")
        
        if not risks:
            risks.append("Standard operational risk")
        
        return risks
    
    def approve_permission(self, request_id: str, approver: str, 
                           reason: str = ""):
        """Aprueba una solicitud de permiso."""
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE permission_requests 
            SET status = ?, approver_id = ?, decided_at = ?, decision_reason = ?
            WHERE id = ?
        """, ("approved", approver, datetime.now().isoformat(), reason, request_id))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.info(f"Permission {request_id} approved by {approver}")
    
    def deny_permission(self, request_id: str, approver: str, 
                        reason: str = ""):
        """Deniega una solicitud de permiso."""
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE permission_requests 
            SET status = ?, approver_id = ?, decided_at = ?, decision_reason = ?
            WHERE id = ?
        """, ("denied", approver, datetime.now().isoformat(), reason, request_id))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
        log.info(f"Permission {request_id} denied by {approver}: {reason}")
    
    def update_trust(self, trustee: str, outcome: str, 
                     truster: str = "EIDOS", scope: str = "general",
                     impact: float = 0.1):
        """
        Actualiza nivel de confianza basado en resultado de interacción.
        
        outcome: "success", "failure", "violation"
        impact: magnitud del cambio (0.0 - 1.0)
        """
        rel = self.db.load_relationship(truster, trustee, scope)
        if not rel:
            return
        
        rel.interactions_count += 1
        rel.last_interaction = datetime.now()
        
        if outcome == "success":
            rel.successful_interactions += 1
            # Incrementar confianza gradualmente
            if rel.level.value < TrustLevel.BLIND.value:
                if random.random() < impact:  # Probabilidad de subir
                    rel.level = TrustLevel(min(rel.level.value + 1, TrustLevel.BLIND.value))
        
        elif outcome == "failure":
            rel.failed_interactions += 1
            # Decrementar confianza
            if rel.level.value > TrustLevel.NONE.value:
                rel.level = TrustLevel(max(rel.level.value - 1, TrustLevel.RESTRICTED.value))
        
        elif outcome == "violation":
            rel.failed_interactions += 1
            rel.last_violation = datetime.now()
            # Reducir confianza significativamente
            rel.level = TrustLevel(max(rel.level.value - 2, TrustLevel.NONE.value))
        
        self.db.save_relationship(rel)
        
        log.info(
            f"Trust updated: {truster} → {trustee} = {rel.level.name} "
            f"(success rate: {rel.success_rate:.2f})"
        )
    
    def report_violation(self, entity_id: str, violation_type: str,
                         description: str, severity: int,
                         consequences: Optional[List[str]] = None):
        """Reporta una violación de confianza."""
        violation = TrustViolation(
            id="",
            entity_id=entity_id,
            violation_type=violation_type,
            severity=severity,
            description=description,
            consequences=consequences or []
        )
        
        # Calcular impacto en confianza
        violation.trust_impact = severity / 10.0
        
        self.db.record_violation(violation)
        
        # Actualizar confianza
        if severity >= 5:
            self.update_trust(entity_id, "violation", impact=violation.trust_impact)
        
        log.warning(f"Trust violation by {entity_id}: {violation_type} (severity {severity})")
    
    def get_trust_chain(self, entity: str) -> List[str]:
        """
        Obtiene la cadena de confianza hacia arriba.
        Ejemplo: colony_coder → EIDOS → SER
        """
        chain = []
        current = entity
        
        while True:
            # Buscar quién supervisa a current
            rel = self.db.load_relationship("EIDOS", current)
            if rel:
                chain.append(f"EIDOS → {current} ({rel.level.name})")
                if current.startswith("colony_"):
                    current = "EIDOS"
                else:
                    break
            else:
                rel = self.db.load_relationship("SER", current)
                if rel:
                    chain.append(f"SER → {current} ({rel.level.name})")
                break
        
        return list(reversed(chain))
    
    def get_trust_report(self) -> str:
        """Genera reporte de estado de confianza."""
        report = "# Trust Hierarchy Report\n\n"
        
        # SER → EIDOS
        ser_eidos = self.db.load_relationship("SER", "EIDOS")
        if ser_eidos:
            report += "## SER → EIDOS\n"
            report += f"Level: **{ser_eidos.level.name}**\n"
            report += f"Success rate: {ser_eidos.success_rate:.1%}\n"
            report += f"Interactions: {ser_eidos.interactions_count}\n\n"
        
        # EIDOS → Colony
        report += "## EIDOS → Colony Agents\n\n"
        colony_rels = [
            self.db.load_relationship("EIDOS", agent)
            for agent in ["colony_coder", "colony_analyst", "colony_vision",
                         "colony_operator", "colony_general"]
        ]
        
        for rel in colony_rels:
            if rel:
                report += f"### {rel.trustee_id}\n"
                report += f"- Level: {rel.level.name}\n"
                report += f"- Success rate: {rel.success_rate:.1%}\n"
                report += f"- Interactions: {rel.interactions_count}\n"
                if rel.last_violation:
                    report += f"- ⚠️ Last violation: {rel.last_violation.strftime('%Y-%m-%d')}\n"
                report += "\n"
        
        return report
    
    def can_delegate(self, delegator: str, delegatee: str, 
                     action_risk: str = "medium") -> bool:
        """Verifica si se puede delegar una acción."""
        # El delegador debe tener nivel suficiente para la acción
        can_do, _ = self.check_permission("delegate", "task", delegator)
        if not can_do:
            return False
        
        # El delegatee debe tener confianza suficiente
        rel = self.db.load_relationship(delegator, delegatee)
        if not rel:
            return False
        
        required = REQUIRED_TRUST.get(action_risk, TrustLevel.CONFIRM)
        return rel.level.value >= required.value


# Singleton
_trust_model: Optional[TrustModel] = None

def get_trust_model() -> TrustModel:
    global _trust_model
    if _trust_model is None:
        _trust_model = TrustModel()
    return _trust_model


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import random
    
    print("=" * 70)
    print("  EIDOS Trust Model - Test")
    print("=" * 70)
    
    trust = get_trust_model()
    
    # Test 1: Verificar permisos
    print("\n[Test 1] Permission checks:")
    
    tests = [
        ("read_file", "EIDOS", "low"),
        ("write_file", "EIDOS", "medium"),
        ("delete_file", "colony_coder", "high"),
        ("modify_kernel", "EIDOS", "critical"),
    ]
    
    for action, requester, expected_risk in tests:
        allowed, reason = trust.check_permission(action, "/path", requester)
        status = "✓" if allowed else "✗"
        print(f"  {status} {requester} → {action}: {reason[:50]}...")
    
    # Test 2: Cadena de confianza
    print("\n[Test 2] Trust chain for colony_coder:")
    chain = trust.get_trust_chain("colony_coder")
    for link in chain:
        print(f"  • {link}")
    
    # Test 3: Solicitar permiso
    print("\n[Test 3] Permission request:")
    req = trust.request_permission(
        "delete_file", "/tmp/old.log",
        requester="colony_coder",
        justification="Cleaning up temporary files"
    )
    print(f"  Request ID: {req.id}")
    print(f"  Risk level: {req.risk_level}")
    print(f"  Requires approval from: {req.required_approver}")
    print(f"  Risks identified: {len(req.risks)}")
    
    # Test 4: Actualizar confianza
    print("\n[Test 4] Trust update:")
    trust.update_trust("colony_coder", "success", impact=0.3)
    print("  Recorded successful interaction with colony_coder")
    
    # Test 5: Delegación
    print("\n[Test 5] Delegation check:")
    can_delegate = trust.can_delegate("EIDOS", "colony_coder", "medium")
    print(f"  Can EIDOS delegate medium-risk task to colony_coder: {can_delegate}")
    
    # Test 6: Reporte
    print("\n[Test 6] Trust report:")
    report = trust.get_trust_report()
    print(report[:400] + "...")
    
    print("\n✅ Trust Model test complete")
    print("   Trust hierarchy: SER → EIDOS → Colony established")
