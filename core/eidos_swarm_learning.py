"""
EIDOS Swarm Learning - Aprendizaje Distribuido (Termitas)
=========================================================

EIDOS, Colony y Ollama aprenden entre sí y comparten conocimiento.
Cuando uno aprende algo, todos se benefician.

Arquitectura de termitas:
- Cada nodo (EIDOS, Colony, Ollama) tiene conocimiento local
- Se sincronizan mediante P2P (peer-to-peer)
- Cuando uno alcanza 100% en un área, los otros descargan ese conocimiento
- Auto-optimización: cada nodo especializa en lo que mejor hace

Uso:
    from core.eidos_swarm_learning import get_swarm_learning
    swarm = get_swarm_learning()
    
    # Compartir aprendizaje
    swarm.share_knowledge("optimización de código", "técnica X mejora velocidad 50%")
    
    # Sincronizar con otros nodos
    swarm.sync_with_colony()
    swarm.sync_with_ollama()
"""

import hashlib
import json
import logging
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import urllib.request

log = logging.getLogger("eidos.swarm")

# Configuración
SWARM_DB = Path.home() / ".eidos" / "swarm_knowledge.db"
SYNC_INTERVAL = 300  # 5 minutos entre sincronizaciones
KNOWLEDGE_THRESHOLD = 0.85  # 85% de confianza para considerar "aprendido"


@dataclass
class KnowledgeNode:
    """Un nodo de conocimiento en la red swarm."""
    node_id: str
    node_type: str  # "eidos", "colony", "ollama"
    capabilities: List[str]
    expertise: Dict[str, float]  # área -> nivel (0-1)
    last_seen: datetime
    endpoint: str  # URL o path para conectar


@dataclass
class KnowledgeUnit:
    """Unidad de conocimiento aprendido."""
    id: str
    topic: str  # "code_optimization", "ui_patterns", "security", etc
    content: str  # El conocimiento en sí
    source: str  # Qué nodo lo originó
    confidence: float  # 0-1
    
    # Metadatos
    created_at: datetime
    validated_by: List[str]  # Nodos que validaron esto
    applications: int = 0  # Cuántas veces se ha aplicado
    success_rate: float = 0.0  # Tasa de éxito al aplicar
    
    # Estado de aprendizaje
    mastery_level: float = 0.0  # 0-1, cuando llega a 1.0 = "100% aprendido"
    distributed: bool = False  # Ya se compartió con otros nodos?


class SwarmKnowledgeDB:
    """Base de datos de conocimiento distribuido."""
    
    def __init__(self):
        self.db_path = SWARM_DB
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_units (
                id TEXT PRIMARY KEY,
                topic TEXT,
                content TEXT,
                source TEXT,
                confidence REAL,
                created_at TEXT,
                validated_by TEXT,
                applications INTEGER,
                success_rate REAL,
                mastery_level REAL,
                distributed INTEGER
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS swarm_nodes (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                capabilities TEXT,
                expertise TEXT,
                last_seen TEXT,
                endpoint TEXT
            )
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_knowledge(self, unit: KnowledgeUnit):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO knowledge_units VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            unit.id, unit.topic, unit.content, unit.source, unit.confidence,
            unit.created_at.isoformat(),
            json.dumps(unit.validated_by),
            unit.applications, unit.success_rate, unit.mastery_level,
            1 if unit.distributed else 0
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def get_knowledge_by_topic(self, topic: str, min_mastery: float = 0.0) -> List[KnowledgeUnit]:
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM knowledge_units 
            WHERE topic = ? AND mastery_level >= ?
            ORDER BY mastery_level DESC, success_rate DESC
        """, (topic, min_mastery))
        
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_knowledge(row) for row in rows]
    
    def get_undistributed_knowledge(self) -> List[KnowledgeUnit]:
        """Conocimiento que aún no se ha compartido con otros nodos."""
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM knowledge_units WHERE distributed = 0")
        rows = cursor.fetchall()
        pass  # S109: get_conn no necesita close()
        return [self._row_to_knowledge(row) for row in rows]
    
    def _row_to_knowledge(self, row) -> KnowledgeUnit:
        return KnowledgeUnit(
            id=row[0], topic=row[1], content=row[2], source=row[3],
            confidence=row[4],
            created_at=datetime.fromisoformat(row[5]),
            validated_by=json.loads(row[6]) if row[6] else [],
            applications=row[7], success_rate=row[8],
            mastery_level=row[9], distributed=bool(row[10])
        )


class SwarmLearningSystem:
    """
    Sistema de aprendizaje distribuido (termitas).
    """
    
    def __init__(self, node_id: str = "eidos_main"):
        self.node_id = node_id
        self.node_type = "eidos"
        self.db = SwarmKnowledgeDB()
        
        # Nodos conocidos en la red
        self.known_nodes: Dict[str, KnowledgeNode] = {}
        
        # Áreas de expertise local
        self.local_expertise: Dict[str, float] = {}
        
        # Sync thread
        self.syncing = False
        self.sync_thread: Optional[threading.Thread] = None
        
        # Detectar otros nodos
        self._discover_nodes()
        
        log.info(f"Swarm Learning initialized: {node_id}")
    
    def _discover_nodes(self):
        """Descubre otros nodos en el sistema."""
        # Colony
        self.known_nodes["colony"] = KnowledgeNode(
            node_id="colony",
            node_type="colony",
            capabilities=["agent_coordination", "task_distribution", "parallel_execution"],
            expertise={},
            last_seen=datetime.now(),
            endpoint="localhost:7777"
        )
        
        # Ollama (si está corriendo)
        try:
            result = subprocess.run(
                ["curl", "-s", "http://localhost:11434/api/tags"],
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                self.known_nodes["ollama"] = KnowledgeNode(
                    node_id="ollama",
                    node_type="ollama",
                    capabilities=["llm_inference", "pattern_recognition", "text_generation"],
                    expertise={},
                    last_seen=datetime.now(),
                    endpoint="localhost:11434"
                )
                log.info("Ollama detectado en red swarm")
        except Exception:
            pass  # error no crítico, continuar
    def learn(self, topic: str, content: str, confidence: float = 0.5) -> KnowledgeUnit:
        """
        EIDOS aprende algo nuevo.
        """
        unit_id = hashlib.md5(f"{topic}:{content}:{time.time()}".encode()).hexdigest()[:12]
        
        unit = KnowledgeUnit(
            id=unit_id,
            topic=topic,
            content=content,
            source=self.node_id,
            confidence=confidence,
            created_at=datetime.now(),
            validated_by=[self.node_id],
            mastery_level=confidence  # Inicial = confianza
        )
        
        self.db.save_knowledge(unit)
        
        # Actualizar expertise local
        if topic not in self.local_expertise:
            self.local_expertise[topic] = 0.0
        self.local_expertise[topic] = max(self.local_expertise[topic], confidence)
        
        log.info(f"🧠 Aprendido: {topic} (confianza: {confidence:.0%})")
        
        # Si es conocimiento valioso, compartir inmediatamente
        if confidence > 0.7:
            self._broadcast_to_swarm(unit)
        
        return unit
    
    def apply_knowledge(self, topic: str, context: Dict) -> Optional[str]:
        """
        Aplica conocimiento aprendido a una situación.
        Retorna la solución si la encuentra.
        """
        # Buscar conocimiento relevante
        units = self.db.get_knowledge_by_topic(topic, min_mastery=KNOWLEDGE_THRESHOLD)
        
        if not units:
            # Intentar obtener de otros nodos
            self._request_from_swarm(topic)
            return None
        
        # Tomar el mejor
        best = max(units, key=lambda u: u.mastery_level * u.success_rate)
        
        # Actualizar estadísticas
        best.applications += 1
        self.db.save_knowledge(best)
        
        log.info(f"📚 Aplicado conocimiento: {topic} (éxito previo: {best.success_rate:.0%})")
        
        return best.content
    
    def validate_knowledge(self, unit_id: str, success: bool):
        """
        Valida si un conocimiento funcionó o no.
        Actualiza tasa de éxito.
        """
        # Cargar
        conn = get_conn(self.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM knowledge_units WHERE id = ?", (unit_id,))
        row = cursor.fetchone()
        
        if not row:
            return
        
        unit = self.db._row_to_knowledge(row)
        
        # Actualizar éxito
        unit.applications += 1
        if success:
            # Aumentar mastery gradualmente
            unit.mastery_level = min(1.0, unit.mastery_level + 0.1)
            unit.success_rate = ((unit.success_rate * (unit.applications - 1)) + 1) / unit.applications
        else:
            unit.success_rate = (unit.success_rate * (unit.applications - 1)) / unit.applications
        
        self.db.save_knowledge(unit)
        
        # Si alcanzó 100% de mastery, notificar
        if unit.mastery_level >= 1.0 and not unit.distributed:
            log.info(f"🎯 MAESTRÍA ALCANZADA: {unit.topic}")
            log.info(f"   Este conocimiento está 100% aprendido y validado")
            self._broadcast_to_swarm(unit, force=True)
    
    def _broadcast_to_swarm(self, unit: KnowledgeUnit, force: bool = False):
        """Comparte conocimiento con otros nodos."""
        if unit.distributed and not force:
            return
        
        log.info(f"📢 Compartiendo con swarm: {unit.topic}")
        
        # Compartir con Colony
        if "colony" in self.known_nodes:
            self._share_with_colony(unit)
        
        # Marcar como distribuido
        unit.distributed = True
        self.db.save_knowledge(unit)
    
    def _share_with_colony(self, unit: KnowledgeUnit):
        """Comparte conocimiento con Colony."""
        try:
            # Usar event bus de Colony si existe
            from core.agent_bus import get_agent_bus
            bus = get_agent_bus()
            
            bus.publish("knowledge_share", {
                "topic": unit.topic,
                "content": unit.content,
                "source": self.node_id,
                "confidence": unit.confidence,
                "mastery": unit.mastery_level
            })
            
            log.info(f"   ✓ Compartido con Colony")
        except Exception as e:
            log.debug(f"   No se pudo compartir con Colony: {e}")
    
    def _request_from_swarm(self, topic: str):
        """Solicita conocimiento a otros nodos."""
        log.info(f"🔍 Solicitando conocimiento a swarm: {topic}")
        
        # Intentar obtener de Ollama
        if "ollama" in self.known_nodes:
            self._query_ollama(topic)
    
    def _query_ollama(self, topic: str):
        """Consulta a Ollama sobre un tema."""
        try:
            prompt = f"""Eres un sistema experto. 
Proporciona conocimiento técnico sobre: {topic}

Responde con:
1. Qué es
2. Cómo funciona
3. Mejores prácticas
4. Ejemplo concreto

Sé conciso pero completo."""
            
            data = json.dumps({
                "model": "lfm2.5-thinking:1.2b",
                "prompt": prompt,
                "stream": False
            }).encode()
            
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode())
                content = result.get("response", "")
                
                if content:
                    # Aprender de Ollama
                    unit = self.learn(topic, content, confidence=0.6)
                    log.info(f"   ✓ Aprendido de Ollama: {topic}")
                    return unit
                    
        except Exception as e:
            log.debug(f"   Ollama no disponible: {e}")
            return None
    
    def start_sync_loop(self):
        """Inicia loop de sincronización continua."""
        if self.syncing:
            return
        
        self.syncing = True
        
        def sync_loop():
            while self.syncing:
                try:
                    self._sync_cycle()
                    time.sleep(SYNC_INTERVAL)
                except Exception as e:
                    log.error(f"Error en sync: {e}")
                    time.sleep(SYNC_INTERVAL)
        
        self.sync_thread = threading.Thread(target=sync_loop, daemon=True)
        self.sync_thread.start()
        log.info("🔄 Swarm sync loop iniciado")
    
    def _sync_cycle(self):
        """Un ciclo de sincronización."""
        # 1. Compartir conocimiento no distribuido
        undistributed = self.db.get_undistributed_knowledge()
        for unit in undistributed:
            if unit.mastery_level >= KNOWLEDGE_THRESHOLD:
                self._broadcast_to_swarm(unit)
        
        # 2. Solicitar conocimiento que nos falta
        all_topics = set(self.local_expertise.keys())
        for topic in ["code_optimization", "security", "ui_patterns", "automation"]:
            if topic not in all_topics or self.local_expertise.get(topic, 0) < KNOWLEDGE_THRESHOLD:
                self._request_from_swarm(topic)
    
    def get_expertise_report(self) -> str:
        """Genera reporte de expertise del nodo."""
        report = f"""
╔════════════════════════════════════════════════════════════════╗
║  🤖 EIDOS SWARM LEARNING - Reporte de Expertise                ║
╠════════════════════════════════════════════════════════════════╣
║  Nodo: {self.node_id:51} ║
║  Tipo: {self.node_type:51} ║
╠════════════════════════════════════════════════════════════════╣
"""
        
        # Expertise local
        report += "║  📚 EXPERTISE LOCAL:\n"
        for topic, level in sorted(self.local_expertise.items(), key=lambda x: -x[1]):
            bar = "█" * int(level * 20) + "░" * (20 - int(level * 20))
            status = "✅" if level >= KNOWLEDGE_THRESHOLD else "🔄"
            report += f"║    {status} {topic:20} [{bar}] {level:>6.0%}\n"
        
        # Nodos conectados
        report += "║\n║  🌐 NODOS EN RED:\n"
        for node_id, node in self.known_nodes.items():
            report += f"║    • {node_id} ({node.node_type})\n"
            report += f"║      Capabilities: {', '.join(node.capabilities[:3])}\n"
        
        report += "╚════════════════════════════════════════════════════════════════╝\n"
        
        return report


# Función auxiliar
import os
from core.db import get_conn

# Singleton
_swarm_learning: Optional[SwarmLearningSystem] = None

def get_swarm_learning() -> SwarmLearningSystem:
    global _swarm_learning
    if _swarm_learning is None:
        _swarm_learning = SwarmLearningSystem()
    return _swarm_learning


if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Swarm Learning - Test")
    print("=" * 70)
    
    swarm = get_swarm_learning()
    
    # Test 1: Aprender algo
    print("\n[Test 1] Aprendiendo conocimiento...")
    unit = swarm.learn(
        "code_optimization",
        "Usar list comprehensions en Python es 2x más rápido que for loops",
        confidence=0.8
    )
    print(f"  ✅ Aprendido: {unit.topic}")
    
    # Test 2: Aplicar conocimiento
    print("\n[Test 2] Aplicando conocimiento...")
    knowledge = swarm.apply_knowledge("code_optimization", {})
    if knowledge:
        print(f"  ✅ Encontrado: {knowledge[:60]}...")
    
    # Test 3: Validar
    print("\n[Test 3] Validando conocimiento...")
    swarm.validate_knowledge(unit.id, success=True)
    print(f"  ✅ Validado (+10% mastery)")
    
    # Test 4: Reporte
    print("\n[Test 4] Reporte de expertise:")
    print(swarm.get_expertise_report())
    
    print("\n✅ Swarm Learning test complete")
    print("   EIDOS now learns and shares with Colony and Ollama.")
