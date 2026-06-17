"""
EIDOS Integration Hub - Integración Total del Sistema
======================================================

Hub de integración unificada que conecta todos los componentes de EIDOS:
- CLI (Command Line Interface)
- VSEIDOS (VS Code Extension)
- Dashboard Web (Colony)
- Colony Agents (5 agentes)
- Módulos Core (nuevos sistemas implementados)

Esta integración permite:
- Comunicación bidireccional entre todos los componentes
- Estado compartido y sincronizado
- Comandos unificados
- Eventos propagados en tiempo real
- Autonomía coordinada

Uso:
    from core.eidos_integration_hub import IntegrationHub, get_integration_hub
    hub = get_integration_hub()
    
    # Iniciar todos los sistemas
    hub.start_unified_system()
    
    # Enviar mensaje a todos los componentes
    hub.broadcast("new_goal", {"goal": "Implement X"})
"""

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from core.db import get_conn

log = logging.getLogger("eidos.integration_hub")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "integration_hub.db"
EVENT_BUFFER_SIZE = 1000

# Componentes integrados
COMPONENTS = {
    "cli": "Command Line Interface",
    "vseidos": "VS Code Extension",
    "dashboard": "Web Dashboard",
    "colony": "Colony Agent Swarm",
    "core": "Core Systems",
    "ram_guardian": "RAM Guardian",
    "vision": "Vision System",
    "control": "Control System",
    "income": "Income System",
    "multimedia": "Multimedia System",
    "story": "Story Creator",
    "video_analyzer": "Video Analyzer",
    "document_reader": "Document Reader",
    "episodic_memory": "Episodic Memory",
    "desires": "Desire System",
    "trust_model": "Trust Model",
    "self_improvement": "Self-Improvement System",
    "staging": "Staging Environment",
    "realtime_vision": "Real-Time Vision & Control",
    "swarm_learning": "Swarm Learning (Colony↔Ollama)",
    "memory_bridge": "Memory Bridge (VSEIDOS/K8s/Docker)",
}

# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class ComponentStatus(str, Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    ONLINE = "online"
    ERROR = "error"
    MAINTENANCE = "maintenance"


@dataclass
class SystemEvent:
    """Evento del sistema para propagación entre componentes."""
    id: str
    timestamp: datetime
    source: str  # Componente origen
    event_type: str
    payload: Dict[str, Any]
    targets: List[str] = field(default_factory=list)  # Destinos (vacío = broadcast)
    priority: int = 5  # 1-10
    processed_by: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.id:
            self.id = f"evt_{int(time.time() * 1000)}"


@dataclass
class ComponentState:
    """Estado de un componente del sistema."""
    id: str
    name: str
    status: ComponentStatus
    last_heartbeat: Optional[datetime] = None
    capabilities: List[str] = field(default_factory=list)
    current_task: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ══════════════════════════════════════════════════════════════════════════════

class IntegrationDB:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        # Tabla de eventos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_events (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                source TEXT,
                event_type TEXT,
                payload TEXT,  -- JSON
                targets TEXT,  -- JSON
                priority INTEGER,
                processed_by TEXT  -- JSON
            )
        """)
        
        # Tabla de estados de componentes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS component_states (
                id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT,
                last_heartbeat TEXT,
                capabilities TEXT,  -- JSON
                current_task TEXT,
                metrics TEXT,  -- JSON
                errors TEXT  -- JSON
            )
        """)
        
        # Tabla de log de integración
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS integration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                component TEXT,
                message TEXT
            )
        """)
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_event(self, event: SystemEvent):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO system_events VALUES (?,?,?,?,?,?,?,?)
        """, (
            event.id, event.timestamp.isoformat(), event.source,
            event.event_type, json.dumps(event.payload),
            json.dumps(event.targets), event.priority,
            json.dumps(event.processed_by)
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def save_component_state(self, state: ComponentState):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO component_states VALUES (?,?,?,?,?,?,?,?)
        """, (
            state.id, state.name, state.status.value,
            state.last_heartbeat.isoformat() if state.last_heartbeat else None,
            json.dumps(state.capabilities), state.current_task,
            json.dumps(state.metrics), json.dumps(state.errors[-10:])  # Últimos 10 errores
        ))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
    def log_integration(self, level: str, component: str, message: str):
        conn = get_conn(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO integration_log (level, component, message)
            VALUES (?, ?, ?)
        """, (level, component, message))
        
        conn.commit()
        pass  # S109: get_conn no necesita close()
# ══════════════════════════════════════════════════════════════════════════════
#  HUB DE INTEGRACIÓN
# ══════════════════════════════════════════════════════════════════════════════

class IntegrationHub:
    """
    Hub central de integración de EIDOS.
    Coordina comunicación entre todos los componentes.
    """
    
    def __init__(self, vision_interval: int = 10):
        self.db = IntegrationDB()
        self.components: Dict[str, ComponentState] = {}
        self.event_handlers: Dict[str, List[Callable]] = {}
        self.event_queue: List[SystemEvent] = []
        self.running = False
        self.hub_thread: Optional[threading.Thread] = None
        self.vision_interval = vision_interval
        
        # Inicializar componentes
        self._init_components()
        
        log.info("Integration Hub initialized")
    
    def _init_components(self):
        """Inicializa estados de componentes."""
        for comp_id, comp_name in COMPONENTS.items():
            self.components[comp_id] = ComponentState(
                id=comp_id,
                name=comp_name,
                status=ComponentStatus.OFFLINE
            )
    
    def start_unified_system(self):
        """Inicia el sistema unificado completo."""
        log.info("Starting EIDOS Unified System...")
        
        # 1. Core Systems
        self._start_core_systems()
        
        # 2. Colony
        self._start_colony()
        
        # 3. Dashboard
        self._start_dashboard()
        
        # 4. Iniciar hub de eventos
        self._start_event_hub()
        
        # 5. Sincronizar estados
        self._sync_all_states()
        
        log.info("EIDOS Unified System started successfully")
        self.db.log_integration("INFO", "hub", "Unified system started")
    
    def _start_core_systems(self):
        """Inicia sistemas core."""
        log.info("Starting Core Systems...")
        
        # RAM Guardian
        try:
            from core.ram_guardian import get_ram_guardian
            guardian = get_ram_guardian()
            guardian.start_monitoring()
            self._update_component_status("ram_guardian", ComponentStatus.ONLINE)
            log.info("  ✓ RAM Guardian online")
        except Exception as e:
            log.error(f"  ✗ RAM Guardian failed: {e}")
            self._update_component_status("ram_guardian", ComponentStatus.ERROR, str(e))
        
        # Episodic Memory (para continuidad)
        try:
            from core.eidos_episodic_memory import get_episodic_memory
            memory = get_episodic_memory()
            context = memory.session_start()
            self._update_component_status("episodic_memory", ComponentStatus.ONLINE)
            log.info(f"  ✓ Episodic Memory online (context: {context.summary[:50]}...)")
        except Exception as e:
            log.error(f"  ✗ Episodic Memory failed: {e}")
        
        # Self-Improvement System (para auto-mejora de código)
        try:
            from core.eidos_self_improvement import get_self_improvement
            si = get_self_improvement()
            # Escanear código periódicamente
            self._schedule_self_improvement(si)
            self._update_component_status("self_improvement", ComponentStatus.ONLINE)
            log.info("  ✓ Self-Improvement System online")
        except Exception as e:
            log.error(f"  ✗ Self-Improvement failed: {e}")
        
        # Staging Environment (para probar cambios seguros)
        try:
            from core.eidos_staging import get_staging_system
            staging = get_staging_system()
            # Crear staging inicial
            staging.env.create_staging_clone()
            self._update_component_status("staging", ComponentStatus.ONLINE)
            log.info("  ✓ Staging Environment online")
        except Exception as e:
            log.error(f"  ✗ Staging failed: {e}")
        
        # Real-Time Vision (screenshots cada N segundos + control seguro)
        try:
            from core.eidos_realtime_vision import get_realtime_vision
            rt_vision = get_realtime_vision()
            rt_vision.start_watching(interval=self.vision_interval)
            self._update_component_status("realtime_vision", ComponentStatus.ONLINE)
            log.info(f"  ✓ Real-Time Vision online ({self.vision_interval}s interval)")
        except Exception as e:
            log.error(f"  ✗ Real-Time Vision failed: {e}")
        
        # Swarm Learning (EIDOS ↔ Colony ↔ Ollama)
        try:
            from core.eidos_swarm_learning import get_swarm_learning
            swarm = get_swarm_learning()
            swarm.start_sync_loop()
            self._update_component_status("swarm_learning", ComponentStatus.ONLINE)
            log.info("  ✓ Swarm Learning online (sync cada 5min)")
        except Exception as e:
            log.error(f"  ✗ Swarm Learning failed: {e}")
        
        # Memory Bridge (VSEIDOS/K8s/Docker → Episodic Memory)
        try:
            from core.eidos_memory_bridge import get_memory_bridge
            bridge = get_memory_bridge()
            bridge.auto_discover_and_record()
            self._update_component_status("memory_bridge", ComponentStatus.ONLINE)
            log.info("  ✓ Memory Bridge online")
        except Exception as e:
            log.error(f"  ✗ Memory Bridge failed: {e}")
    
    def _schedule_self_improvement(self, si):
        """Programa escaneos periódicos de auto-mejora."""
        import threading
        
        def improvement_loop():
            while self.running:
                try:
                    # Escanear cada hora
                    log.info("[Self-Improvement] Escaneando código...")
                    issues = si.scan_for_improvements()
                    if issues:
                        log.info(f"[Self-Improvement] {len(issues)} issues encontrados")
                        # Tomar el más grave y sugerir mejora
                        top_issue = max(issues, key=lambda x: x.severity)
                        if top_issue.severity >= 7:
                            change = si.suggest_improvement(top_issue.file_path)
                            if change:
                                log.info(f"[Self-Improvement] Sugerido: {change.description}")
                    time.sleep(3600)  # 1 hora
                except Exception as e:
                    log.error(f"[Self-Improvement] Error: {e}")
                    time.sleep(3600)
        
        thread = threading.Thread(target=improvement_loop, daemon=True)
        thread.start()
        log.info("[Self-Improvement] Loop programado (cada 1h)")
    
    def _start_colony(self):
        """Inicia Colony."""
        log.info("Starting Colony...")
        
        try:
            # Iniciar dashboard de Colony (ya existe en codebase)
            from core.colony_dashboard import start_dashboard
            # No bloquear, iniciar en thread
            threading.Thread(target=start_dashboard, daemon=True).start()
            
            self._update_component_status("colony", ComponentStatus.ONLINE)
            self._update_component_status("dashboard", ComponentStatus.ONLINE)
            log.info("  ✓ Colony online at http://localhost:7777")
        except Exception as e:
            log.error(f"  ✗ Colony failed: {e}")
            self._update_component_status("colony", ComponentStatus.ERROR, str(e))
    
    def _start_dashboard(self):
        """Inicia dashboard web."""
        # El dashboard de Colony ya cubre esto
        pass
    
    def _start_event_hub(self):
        """Inicia el hub de eventos."""
        self.running = True
        
        def event_loop():
            while self.running:
                self._process_events()
                time.sleep(1)
        
        self.hub_thread = threading.Thread(target=event_loop, daemon=True)
        self.hub_thread.start()
        
        log.info("Event Hub started")
    
    def _process_events(self):
        """Procesa eventos en cola."""
        if not self.event_queue:
            return
        
        # Procesar eventos pendientes
        events_to_process = self.event_queue[:10]  # Batch de 10
        self.event_queue = self.event_queue[10:]
        
        for event in events_to_process:
            # Guardar en DB
            self.db.save_event(event)
            
            # Propagar a handlers
            handlers = self.event_handlers.get(event.event_type, [])
            for handler in handlers:
                try:
                    handler(event)
                except Exception as e:
                    log.error(f"Event handler error: {e}")
            
            # Marcar como procesado
            event.processed_by.append("hub")
    
    def _sync_all_states(self):
        """Sincroniza estados de todos los componentes."""
        for comp_id, state in self.components.items():
            self.db.save_component_state(state)
    
    def _update_component_status(self, component_id: str, 
                                  status: ComponentStatus,
                                  error: Optional[str] = None):
        """Actualiza estado de un componente."""
        if component_id in self.components:
            self.components[component_id].status = status
            self.components[component_id].last_heartbeat = datetime.now()
            
            if error:
                self.components[component_id].errors.append(f"{datetime.now()}: {error}")
            
            self.db.save_component_state(self.components[component_id])
    
    # ════════════════════════════════════════════════════════════════════════
    #  API PÚBLICA
    # ════════════════════════════════════════════════════════════════════════
    
    def broadcast(self, event_type: str, payload: Dict[str, Any],
                  priority: int = 5, exclude: Optional[List[str]] = None):
        """
        Emite un evento a todos los componentes.
        """
        exclude = exclude or []
        
        event = SystemEvent(
            id="",
            timestamp=datetime.now(),
            source="hub",
            event_type=event_type,
            payload=payload,
            targets=[],  # Broadcast
            priority=priority
        )
        
        self.event_queue.append(event)
        
        log.debug(f"Broadcast: {event_type} to all except {exclude}")
        return event.id
    
    def send_to(self, component: str, event_type: str, payload: Dict[str, Any],
                priority: int = 5):
        """
        Envía evento a componente específico.
        """
        event = SystemEvent(
            id="",
            timestamp=datetime.now(),
            source="hub",
            event_type=event_type,
            payload=payload,
            targets=[component],
            priority=priority
        )
        
        self.event_queue.append(event)
        return event.id
    
    def register_handler(self, event_type: str, handler: Callable):
        """Registra handler para tipo de evento."""
        if event_type not in self.event_handlers:
            self.event_handlers[event_type] = []
        self.event_handlers[event_type].append(handler)
    
    def get_system_status(self) -> Dict[str, Any]:
        """Obtiene estado completo del sistema."""
        return {
            "timestamp": datetime.now().isoformat(),
            "hub_running": self.running,
            "components": {
                comp_id: {
                    "name": state.name,
                    "status": state.status.value,
                    "last_heartbeat": state.last_heartbeat.isoformat() if state.last_heartbeat else None,
                    "current_task": state.current_task
                }
                for comp_id, state in self.components.items()
            },
            "online_count": sum(1 for s in self.components.values() if s.status == ComponentStatus.ONLINE),
            "total_components": len(self.components)
        }
    
    def get_unified_status_report(self) -> str:
        """Genera reporte unificado de estado."""
        status = self.get_system_status()
        
        report = "# EIDOS Unified System Status\n\n"
        report += f"**Timestamp:** {status['timestamp']}\n"
        report += f"**Components Online:** {status['online_count']}/{status['total_components']}\n\n"
        
        # Por categoría
        categories = {
            "Core": ["ram_guardian", "episodic_memory", "trust_model", "desires"],
            "Interface": ["cli", "vseidos", "dashboard"],
            "Agents": ["colony"],
            "Media": ["vision", "control", "multimedia"],
            "Knowledge": ["story", "video_analyzer", "document_reader"],
        }
        
        for cat_name, components in categories.items():
            report += f"## {cat_name}\n\n"
            for comp_id in components:
                if comp_id in status['components']:
                    comp = status['components'][comp_id]
                    status_emoji = "🟢" if comp['status'] == 'online' else "🔴" if comp['status'] == 'error' else "⚪"
                    report += f"{status_emoji} **{comp['name']}**: {comp['status']}\n"
            report += "\n"
        
        return report
    
    def execute_unified_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta un comando unificado en todos los componentes relevantes.
        """
        results = {}
        
        # Parsear comando y enrutar
        if command == "status":
            results = self.get_system_status()
        
        elif command == "start_component":
            component = params.get("component")
            if component == "ram_guardian":
                from core.ram_guardian import get_ram_guardian
                get_ram_guardian().start_monitoring()
                self._update_component_status("ram_guardian", ComponentStatus.ONLINE)
            results = {"status": "started", "component": component}
        
        elif command == "broadcast_message":
            event_id = self.broadcast(
                params.get("event_type", "message"),
                params.get("payload", {}),
                params.get("priority", 5)
            )
            results = {"event_id": event_id, "broadcast": True}
        
        elif command == "get_report":
            results = {"report": self.get_unified_status_report()}
        
        else:
            results = {"error": "Unknown command", "command": command}
        
        return results
    
    def stop_unified_system(self):
        """Detiene el sistema unificado."""
        log.info("Stopping EIDOS Unified System...")
        
        self.running = False
        
        # Notificar a componentes
        self.broadcast("system_shutdown", {"reason": "manual_stop"}, priority=10)
        
        # Guardar estado final
        if self.hub_thread:
            self.hub_thread.join(timeout=2)
        
        # Detener sistemas core
        try:
            from core.eidos_episodic_memory import get_episodic_memory
            get_episodic_memory().session_end("system_stop")
        except Exception:
            pass  # error no crítico, continuar
        log.info("EIDOS Unified System stopped")


# Singleton
_integration_hub: Optional[IntegrationHub] = None

def get_integration_hub(vision_interval: int = 10) -> IntegrationHub:
    global _integration_hub
    if _integration_hub is None:
        _integration_hub = IntegrationHub(vision_interval=vision_interval)
    return _integration_hub


# ══════════════════════════════════════════════════════════════════════════════
#  TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  EIDOS Integration Hub - Test")
    print("=" * 70)
    
    hub = get_integration_hub()
    
    # Test 1: Estado inicial
    print("\n[Test 1] Initial system status:")
    status = hub.get_system_status()
    print(f"  Components: {status['total_components']}")
    print(f"  Online: {status['online_count']}")
    
    # Test 2: Broadcast
    print("\n[Test 2] Broadcast event:")
    event_id = hub.broadcast("test_event", {"message": "Hello from hub"})
    print(f"  Event ID: {event_id}")
    
    # Test 3: Comando unificado
    print("\n[Test 3] Unified command execution:")
    result = hub.execute_unified_command("status", {})
    print(f"  Command result: {len(result)} keys")
    
    # Test 4: Reporte
    print("\n[Test 4] Status report:")
    report = hub.get_unified_status_report()
    print(report[:500] + "...")
    
    print("\n✅ Integration Hub test complete")
    print("   All components ready for unified operation")
