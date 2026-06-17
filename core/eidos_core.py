"""
EIDOS Core - Núcleo Integrado de Autonomía Total
=================================================
Unificación de las 7 fases de autonomía en un sistema cohesivo.

Fases integradas:
1. Shell Background System - Sesiones persistentes
2. Uncensored Mode - Niveles de libertad con protección
3. Git Guardian - Operaciones atómicas con rollback
4. Self-Improvement Activo - Auto-modificación con tests
5. Brain Memory + Rust - Memoria semántica unificada
6. Colony Brain Central - Orquestación central
7. Rate Limiter - Circuit breaker y prioridad

Uso:
    from core.eidos_core import EIDOSCore, get_eidos_core
    
    # Iniciar EIDOS autónomo
    eidos = get_eidos_core()
    eidos.awaken()
    
    # EIDOS ahora corre autónomamente en background

Autor: EIDOS Autonomy System
Versión: 2.0.0 - Autonomía Total
"""

import json
import logging
import threading
import time
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

# Configurar logging estructurado
log = logging.getLogger("eidos.core")


# ═══════════════════════════════════════════════════════════════════════════════
#  EIDOS CORE - Estado y Configuración
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EIDOSState:
    """Estado completo del núcleo EIDOS."""
    awake: bool = False
    autonomy_level: float = 0.0  # 0-1
    last_cycle: Optional[datetime] = None
    cycle_count: int = 0
    errors_total: int = 0
    changes_applied: int = 0
    modules_active: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "awake": self.awake,
            "autonomy_level": self.autonomy_level,
            "last_cycle": self.last_cycle.isoformat() if self.last_cycle else None,
            "cycle_count": self.cycle_count,
            "errors_total": self.errors_total,
            "changes_applied": self.changes_applied,
            "modules_active": self.modules_active
        }


class EIDOSCore:
    """
    Núcleo integrado de EIDOS - Unificación de las 7 fases.
    
    Responsabilidades:
    - Inicializar y coordinar los 7 sistemas de autonomía
    - Ejecutar el ciclo de vida autónomo (VER-PENSAR-ACTUAR-VERIFICAR)
    - Gestionar estado global y persistencia
    - Proporcionar API unificada para todos los módulos
    """
    
    def __init__(self):
        self.state = EIDOSState()
        self._shutdown_event = threading.Event()
        self._autonomy_thread: Optional[threading.Thread] = None
        
        # Ventana deslizante para cálculo de autonomy_level (BUG-8 fix)
        import collections
        self._recent_errors = collections.deque(maxlen=100)
        
        # Contador de errores consecutivos para modo PRISON (Fase 3)
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5
        self._start_time = time.time()
        
        # Referencias lazy a los sistemas
        self._shell_mgr = None
        self._uncensored = None
        self._git_guardian = None
        self._self_improvement = None
        self._brain_memory = None
        self._colony = None
        self._rate_limiter = None
        
        # Thread-safety locks para lazy loaders
        self._lock_shell = threading.Lock()
        self._lock_uncensored = threading.Lock()
        self._lock_git = threading.Lock()
        self._lock_si = threading.Lock()
        self._lock_brain = threading.Lock()
        self._lock_colony = threading.Lock()
        self._lock_rate = threading.Lock()
        
        # Restaurar estado previo si existe (sobrevivir restart)
        state_path = Path.home() / ".eidos" / "core_state.json"
        if state_path.exists():
            try:
                saved = json.loads(state_path.read_text())
                self.state.cycle_count = saved.get("cycle_count", 0)
                self.state.errors_total = saved.get("errors_total", 0)
                self.state.changes_applied = saved.get("changes_applied", 0)
                log.info(f"Estado restaurado: ciclo {self.state.cycle_count}")
            except Exception:
                pass  # error no crítico, continuar
        log.info("EIDOS Core initialized - 7 fases listas para despertar")
    
    # ── LAZY LOADERS ──────────────────────────────────────────────────────────
    
    def _get_shell(self):
        if self._shell_mgr is None:
            with self._lock_shell:
                if self._shell_mgr is None:
                    from core.shell_background import get_shell_manager
                    self._shell_mgr = get_shell_manager()
        return self._shell_mgr
    
    def _get_uncensored(self):
        if self._uncensored is None:
            with self._lock_uncensored:
                if self._uncensored is None:
                    from core.uncensored_mode import get_uncensored_mode
                    self._uncensored = get_uncensored_mode()
        return self._uncensored
    
    def _get_git_guardian(self):
        if self._git_guardian is None:
            with self._lock_git:
                if self._git_guardian is None:
                    from core.git_guardian import get_git_guardian
                    self._git_guardian = get_git_guardian()
        return self._git_guardian
    
    def _get_self_improvement(self):
        if self._self_improvement is None:
            with self._lock_si:
                if self._self_improvement is None:
                    from core.eidos_self_improvement import get_self_improvement
                    self._self_improvement = get_self_improvement()
        return self._self_improvement
    
    def _get_brain_memory(self):
        if self._brain_memory is None:
            with self._lock_brain:
                if self._brain_memory is None:
                    from core.brain_memory import get_brain_memory
                    self._brain_memory = get_brain_memory()
        return self._brain_memory
    
    def _get_colony(self):
        if self._colony is None:
            with self._lock_colony:
                if self._colony is None:
                    from core.colony_community import get_colony_community
                    self._colony = get_colony_community()
        return self._colony
    
    def _get_rate_limiter(self):
        if self._rate_limiter is None:
            with self._lock_rate:
                if self._rate_limiter is None:
                    from core.rate_limiter import get_rate_limiter
                    self._rate_limiter = get_rate_limiter()
        return self._rate_limiter
    
    # ── CICLO DE VIDA PRINCIPAL ───────────────────────────────────────────────
    
    def awaken(self) -> bool:
        """
        Despierta a EIDOS - Inicia el sistema autónomo completo.
        
        Returns:
            True si EIDOS despertó exitosamente
        """
        if self.state.awake:
            log.warning("EIDOS ya está despierto")
            return True
        
        log.info("=" * 60)
        log.info("🧠 EIDOS AWAKENING - Iniciando Autonomía Total")
        log.info("=" * 60)
        
        try:
            # 1. Verificar y activar Git Guardian (requerido para auto-cambios)
            guardian = self._get_git_guardian()
            if not guardian.is_active():
                log.warning("Git Guardian no está activo - auto-cambios deshabilitados")
            else:
                log.info("✅ Git Guardian activo")
            
            # 2. Verificar Rate Limiter
            limiter = self._get_rate_limiter()
            log.info(f"✅ Rate Limiter activo - {limiter.get_remaining_quota('auto_change')} cambios disponibles")
            
            # 3. Verificar Uncensored Mode
            mode = self._get_uncensored()
            log.info(f"✅ Uncensored Mode: {mode.current_level.value}")
            
            # 4. Iniciar Colony Brain Central
            colony = self._get_colony()
            log.info("✅ Colony Brain Central activo")
            
            # 5. Verificar Brain Memory
            memory = self._get_brain_memory()
            log.info("✅ Brain Memory conectado")
            
            # 6. Verificar Self-Improvement
            si = self._get_self_improvement()
            log.info("✅ Self-Improvement System listo")
            
            # 7. Iniciar Shell Background
            shell = self._get_shell()
            log.info("✅ Shell Background System listo")
            
            # Actualizar estado
            self.state.awake = True
            self.state.modules_active = [
                "shell_background",
                "uncensored_mode", 
                "git_guardian",
                "self_improvement",
                "brain_memory",
                "colony_brain",
                "rate_limiter"
            ]
            
            # Iniciar ciclo autónomo en background
            self._start_autonomy_loop()

            # AWAKENING: arrancar patrol + despertar Colony
            self._awakening_sequence()

            # Graceful shutdown con signals
            def _signal_handler(signum, frame):
                log.info(f"Signal {signum} recibido - graceful shutdown")
                self.sleep()
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)

            log.info("=" * 60)
            log.info("✅ EIDOS ESTÁ DESPIERTO Y AUTÓNOMO")
            log.info("   Ciclo autónomo ejecutándose en background")
            log.info("   Patrol activo — Colony despierta")
            log.info("   Usa .status() para ver estado, .sleep() para detener")
            log.info("=" * 60)
            
            return True
            
        except Exception as e:
            log.error(f"❌ Error despertando EIDOS: {e}")
            self.state.errors_total += 1
            return False
    
    def _awakening_sequence(self):
        """
        AWAKENING: cuando EIDOS despierta, todos los sistemas arrancan simultáneamente.
        - Patrol loop: vigila salud de Ollama + Colony DBs
        - Colony: notifica a todos los agentes que están despiertos
        - HybridRouter: precarga SmartRouter + OctoClaw
        """
        import threading

        def _wake():
            # 1. Patrol loop — vigilancia continua cada 5 min
            try:
                from core.patrol_loop import get_patrol_loop
                get_patrol_loop().start(interval_minutes=5.0)
                log.info("🛡️ Patrol activo — vigilando Ollama + Colony")
            except Exception as e:
                log.warning("Patrol no pudo arrancar: %s", e)

            # 2. Colony awakening — todos los agentes despiertan
            try:
                from core.colony_community import get_colony_community
                colony = get_colony_community()
                agents = colony.get_agents() if hasattr(colony, 'get_agents') else []
                agent_names = [a.get('name', a.get('id', '?')) for a in agents] if agents else ['Colony']
                log.info("🌅 Colony despierta — agentes: %s", ', '.join(str(n) for n in agent_names))
            except Exception as e:
                log.warning("Colony awakening: %s", e)

            # 3. HybridRouter precarga (evita cold start en primera petición)
            try:
                from core.octoclaw_bridge import get_hybrid_router
                router = get_hybrid_router()
                log.info("🧭 HybridRouter listo — OctoClaw: %s",
                         "activo" if router._smart else "solo SmartRouter")
            except Exception as e:
                log.warning("HybridRouter precarga: %s", e)

            # 4. Token economy — inicializar balances de todos los agentes
            try:
                from core.colony_token_economy import get_token_economy
                eco = get_token_economy()
                balances = eco.get_all_balances()
                log.info("💰 Token economy activa — %d agentes, %.0f tokens en circulación",
                         len(balances), sum(balances.values()))
            except Exception as e:
                log.warning("Token economy: %s", e)

            # 5. Colony intercomm — conversaciones autónomas cada 20 min
            try:
                from core.colony_intercomm import get_intercomm
                get_intercomm().start(interval_minutes=20.0)
                log.info("💬 Colony intercomm activo — conversaciones autónomas cada 20min")
            except Exception as e:
                log.warning("Colony intercomm: %s", e)

            # 5b. Curiosidad propia — EIDOS aprende solo cada 10 min
            try:
                from core.eidos_curiosity import get_curiosity
                get_curiosity().start(interval_minutes=10.0)
                log.info("🔍 Curiosidad activa — aprende sobre gaps cada 10min")
            except Exception as e:
                log.warning("Curiosidad: %s", e)

            # 6. Visión en tiempo real — EIDOS ve la pantalla continuamente
            try:
                from core.eidos_realtime_vision import get_realtime_vision
                vision = get_realtime_vision()
                # Interval 5s: balance entre tiempo real y CPU
                vision.start_watching(interval=5.0)
                log.info("👁️ Visión activa — escaneo de pantalla cada 5s")
            except Exception as e:
                log.warning("Visión no pudo arrancar: %s", e)

            # 7. Computer use v2 — control desktop+web con verificación
            try:
                from core.computer_use_v2 import get_desktop_controller, get_computer_use_tools
                _ = get_desktop_controller()  # precarga (lazy se inicializa en primer uso)
                tools = get_computer_use_tools()
                log.info("🖱️ Computer Use activo — %d herramientas disponibles", len(tools))
            except Exception as e:
                log.warning("Computer Use precarga: %s", e)

        t = threading.Thread(target=_wake, name="eidos-awakening", daemon=True)
        t.start()

    def _start_autonomy_loop(self):
        """Inicia el ciclo de autonomía en thread background."""
        self._shutdown_event.clear()
        self._autonomy_thread = threading.Thread(
            target=self._autonomy_loop,
            name="EIDOS_Autonomy_Loop",
            daemon=True
        )
        self._autonomy_thread.start()
        log.info("🔄 Ciclo autónomo iniciado en background thread")
    
    def _autonomy_loop(self):
        """
        Ciclo principal de autonomía - VER-PENSAR-ACTUAR-VERIFICAR.
        
        Este loop corre indefinidamente en background, ejecutando:
        - Verificación de estado de módulos
        - Detección de oportunidades de mejora
        - Auto-corrección de errores detectados
        - Aprendizaje continuo
        """
        log.info("🤖 EIDOS Autonomy Loop iniciado")
        
        while not self._shutdown_event.is_set():
            try:
                # Heartbeat - dead man's switch (PASO 4)
                heartbeat_path = Path.home() / ".eidos" / "heartbeat"
                heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                heartbeat_path.write_text(
                    f"{time.time()}\n{self.state.cycle_count}\n{datetime.now().isoformat()}"
                )
                
                cycle_start = time.time()
                self.state.last_cycle = datetime.now()
                
                # FASE: OBSERVAR (tolerante a fallos)
                try:
                    self._phase_observe()
                except Exception as e:
                    log.warning(f"phase_observe falló (no-fatal): {e}")
                
                # FASE: EVALUAR (fallback a "wait" si falla)
                decision = {"action": "wait", "reason": "evaluate_fallback"}
                try:
                    decision = self._phase_evaluate()
                except Exception as e:
                    log.warning(f"phase_evaluate falló (fallback wait): {e}")
                
                # FASE: ACTUAR (solo si hay accion real)
                if decision.get("action") != "wait":
                    try:
                        self._phase_act(decision)
                    except Exception as e:
                        log.error(f"phase_act falló: {e}")
                
                # FASE: APRENDER (siempre intentar)
                try:
                    self._phase_learn()
                except Exception as e:
                    log.warning(f"phase_learn falló (no-fatal): {e}")
                
                # Actualizar métricas
                self.state.cycle_count += 1
                self._recent_errors.append(False)  # Ciclo exitoso (BUG-8 fix)
                self._consecutive_errors = 0  # Reset en ciclo exitoso
                cycle_duration = time.time() - cycle_start
                
                # Log cada 10 ciclos
                if self.state.cycle_count % 10 == 0:
                    log.info(f"🔄 Ciclo {self.state.cycle_count} completado en {cycle_duration:.2f}s - "
                            f"Autonomía: {self.state.autonomy_level:.1%}")
                
                # Esperar antes del siguiente ciclo
                time.sleep(30)  # 30 segundos entre ciclos
                
            except Exception as e:
                log.error(f"❌ Error en ciclo autónomo: {e}")
                self.state.errors_total += 1
                self._recent_errors.append(True)  # Error en ciclo (BUG-8 fix)
                self._consecutive_errors += 1
                if self._consecutive_errors >= self._max_consecutive_errors:
                    log.critical("5 errores consecutivos - modo PRISON activado")
                    try:
                        mode = self._get_uncensored()
                        mode.set_level("prison", force=True)
                    except Exception:
                        pass  # error no crítico, continuar
                time.sleep(60)  # Esperar más si hay error
        
        log.info("🛑 Ciclo autónomo detenido")
    
    def _phase_observe(self):
        """FASE VER: Observar estado del sistema y entorno."""
        # Recolectar métricas de todos los módulos
        colony = self._get_colony()
        status = colony.get_colony_status()
        
        # Guardar en memoria
        memory = self._get_brain_memory()
        import json
        memory.remember(
            content=f"system_status: {json.dumps(status, default=str)}",
            tags=["system_status", "observe_cycle"],
            importance=0.6,
            category="system"
        )
    
    def _phase_evaluate(self) -> dict:
        """FASE PENSAR: Evaluar estado y decidir acciones."""
        colony = self._get_colony()
        
        # Contexto para decisión
        context = {
            "cycle_count": self.state.cycle_count,
            "errors_total": self.state.errors_total,
            "changes_applied": self.state.changes_applied,
            "autonomy_level": self.state.autonomy_level
        }
        
        # Delegar decisión al Colony Brain
        decision = colony.make_decision(context)
        
        return decision
    
    def _phase_act(self, decision: dict):
        """FASE ACTUAR: Ejecutar acciones decididas."""
        action = decision.get("action")
        
        # Audit log (PASO 5)
        try:
            from core.audit_log import log_tool_call
            log_tool_call(
                tool="phase_act",
                args={"action": action, "decision": decision},
                result="executing",
                risk=0,
                channel="autonomy"
            )
        except ImportError:
            pass  # audit_log no disponible, no bloquear
        
        if action == "execute_task":
            task_id = decision.get("target")
            colony = self._get_colony()
            
            # Ejecutar tarea pendiente
            tasks = colony.get_task_queue("pending", 1)
            if tasks:
                task = tasks[0]
                self._execute_task(task)
        
        elif action == "scan_code":
            # Iniciar auto-mejora
            self._trigger_self_improvement()
        
        elif action == "observe_system":
            # Observar el sistema operativo
            self._observe_system()
    
    def _execute_task(self, task: dict):
        """Ejecuta una tarea específica del queue."""
        task_type = task.get("task_type")
        task_id = task.get("task_id")
        
        log.info(f"⚡ Ejecutando tarea {task_id}: {task_type}")
        
        try:
            if task_type == "shell_command":
                payload = json.loads(task.get("payload", "{}"))
                command = payload.get("command")
                if command:
                    # Usar execute_command para pasar por Capa 1 (Constitution validation)
                    result = self.execute_command(command, background=True)
                    if not result.get("success"):
                        log.warning(f"Tarea {task_id} comando bloqueado: {result.get('stderr', '')}")
                        raise RuntimeError(f"Comando bloqueado: {result.get('stderr', '')}")
            
            elif task_type == "scan_code":
                self._trigger_self_improvement()
            
            # Marcar como completada
            colony = self._get_colony()
            colony.complete_task(task_id, {"executed": True}, success=True)
            
        except Exception as e:
            log.error(f"❌ Error ejecutando tarea {task_id}: {e}")
            colony = self._get_colony()
            colony.complete_task(task_id, {"error": str(e)}, success=False)
    
    def _trigger_self_improvement(self):
        """Trigger auto-mejora si hay cuota disponible."""
        limiter = self._get_rate_limiter()
        
        if not limiter.allow("auto_change", priority_override=3):
            log.debug("Rate limit excedido - saltando auto-mejora")
            return
        
        try:
            si = self._get_self_improvement()
            issues = si.scan_for_improvements()
            
            if issues:
                log.info(f"🔍 {len(issues)} issues encontrados para mejora")
                
                # Proponer cambio para el más severo
                top_issue = issues[0]
                change_id = si.suggest_improvement(top_issue)
                
                if change_id:
                    log.info(f"💡 Cambio propuesto: {change_id}")
                    # NOTA: No aplicamos automáticamente - esperamos confirmación
                    # para evitar loops de cambios incontrolados
            
        except Exception as e:
            log.error(f"❌ Error en auto-mejora: {e}")
    
    def _observe_system(self):
        """Observa el sistema operativo y aprende de él."""
        import subprocess
        
        # Lista blanca de comandos con importance según volatilidad
        safe_commands = [
            ("uname -a", "os_info", 0.8),
            ("df -h", "disk_usage", 0.5),
            ("free -h", "memory_usage", 0.3),
            ("uptime", "system_uptime", 0.3),
            ("ps aux --sort=-%mem | head -20", "top_processes", 0.4),
            ("ip addr show", "network_interfaces", 0.7),
        ]
        
        memory = self._get_brain_memory()
        
        for cmd, tag, importance in safe_commands:
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True,
                    text=True, timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    memory.remember(
                        content=f"{tag}: {result.stdout.strip()[:500]}",
                        tags=["system_knowledge", tag],
                        importance=importance,
                        category="system"
                    )
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                continue
    
    def _phase_learn(self):
        """FASE APRENDER: Consolidar conocimiento de la iteración."""
        # Calcular nivel de autonomía basado en ventana deslizante (BUG-8 fix)
        if len(self._recent_errors) > 0:
            recent_error_rate = sum(self._recent_errors) / len(self._recent_errors)
            self.state.autonomy_level = max(0.0, 1.0 - recent_error_rate)
        
        # Cada 50 ciclos, consolidar memorias (aprendizaje continuo)
        if self.state.cycle_count % 50 == 0:
            try:
                memory = self._get_brain_memory()
                result = memory.consolidate()
                log.info(f"🧠 Memoria consolidada: {result.get('new_lessons', 0)} lecciones nuevas")
            except Exception as e:
                log.warning(f"⚠️ Consolidación de memoria falló: {e}")
        
        # Persistir estado en disco (sobrevivir restart)
        try:
            state_path = Path.home() / ".eidos" / "core_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({
                "cycle_count": self.state.cycle_count,
                "errors_total": self.state.errors_total,
                "changes_applied": self.state.changes_applied,
                "autonomy_level": self.state.autonomy_level,
                "last_save": datetime.now().isoformat()
            }))
        except Exception:
            pass  # error no crítico, continuar
        # Health check para monitoreo externo
        try:
            health_path = Path.home() / ".eidos" / "health.json"
            health_path.write_text(json.dumps({
                "status": "healthy" if self._consecutive_errors < 3 else "degraded",
                "cycle_count": self.state.cycle_count,
                "autonomy_level": self.state.autonomy_level,
                "consecutive_errors": self._consecutive_errors,
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "timestamp": datetime.now().isoformat()
            }))
        except Exception:
            pass  # error no crítico, continuar
    # ── API PÚBLICA ────────────────────────────────────────────────────────────
    
    def status(self) -> dict:
        """Obtiene estado completo de EIDOS."""
        return {
            "core": self.state.to_dict(),
            "colony": self._get_colony().get_colony_status() if self._colony else None,
            "rate_limiter": {
                "auto_change": self._get_rate_limiter().get_remaining_quota("auto_change")
            } if self._rate_limiter else None
        }
    
    def sleep(self):
        """Pone a EIDOS en modo sleep - detiene ciclo autónomo."""
        log.info("😴 EIDOS going to sleep...")
        self._shutdown_event.set()
        
        if self._autonomy_thread:
            self._autonomy_thread.join(timeout=5)
        
        self.state.awake = False
        log.info("💤 EIDOS dormido - Ciclo autónomo detenido")
    
    def execute_command(self, command: str, background: bool = True) -> dict:
        """
        Ejecuta un comando shell via EIDOS.
        
        Args:
            command: Comando a ejecutar
            background: Si True, ejecuta en background via Shell System
        
        Returns:
            Resultado de la ejecución
        """
        # CAPA 1: Validación Constitution (aplica a ambos paths: foreground y background)
        try:
            from core.constitution import check_command, ConstitutionViolation
            if not check_command(command):
                log.critical(f"🚫 Constitution BLOQUEO comando: {command[:80]}")
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": "BLOQUEADO por Constitution: comando prohibido",
                    "returncode": -1,
                    "blocked_by": "constitution"
                }
        except ConstitutionViolation as e:
            log.critical(f"🚨 Constitution integrity failed: {e}")
            # Entrar en modo PRISON automáticamente
            try:
                mode = self._get_uncensored()
                mode.set_level("prison", force=True)
            except Exception:
                pass  # error no crítico, continuar
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Constitution integrity check failed: {e}",
                "returncode": -1,
                "blocked_by": "constitution_integrity"
            }
        
        if background:
            colony = self._get_colony()
            task_id = f"manual_{int(time.time())}"
            return colony.execute_in_background(task_id, command)
        else:
            import subprocess
            import shlex
            args = shlex.split(command)
            result = subprocess.run(args, shell=False, capture_output=True, text=True)
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
    
    def scan_and_improve(self, auto_apply: bool = False) -> List[dict]:
        """
        Escanea código y aplica mejoras.
        
        Args:
            auto_apply: Si True, aplica cambios automáticamente (con tests)
        
        Returns:
            Lista de cambios aplicados
        """
        si = self._get_self_improvement()
        issues = si.scan_for_improvements()
        
        results = []
        for issue in issues[:3]:  # Máximo 3 por llamada
            change_id = si.suggest_improvement(issue)
            if change_id and auto_apply:
                success, msg = si.apply_change(change_id, auto_confirm=True)
                results.append({
                    "change_id": change_id,
                    "applied": success,
                    "message": msg
                })
                if success:
                    self.state.changes_applied += 1
            else:
                results.append({
                    "change_id": change_id,
                    "applied": False,
                    "message": "Pendiente de confirmación"
                })
        
        return results
    
    def get_memory(self, query: str, limit: int = 5) -> List[dict]:
        """Consulta memoria de EIDOS."""
        memory = self._get_brain_memory()
        return memory.recall(query, limit=limit)
    
    def remember(self, content: str, tags: list = None, importance: float = 0.5, category: str = "memory"):
        """Almacena en memoria de EIDOS."""
        memory = self._get_brain_memory()
        return memory.remember(content, tags=tags, importance=importance, category=category)


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_eidos_core: Optional[EIDOSCore] = None
_eidos_core_lock = threading.Lock()


def get_eidos_core() -> EIDOSCore:
    """Obtiene la instancia singleton del núcleo EIDOS."""
    global _eidos_core
    if _eidos_core is None:
        with _eidos_core_lock:
            if _eidos_core is None:
                _eidos_core = EIDOSCore()
    return _eidos_core


def awaken_eidos() -> bool:
    """Función de conveniencia para despertar EIDOS."""
    return get_eidos_core().awaken()


def eidos_status() -> dict:
    """Función de conveniencia para obtener estado."""
    return get_eidos_core().status()


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT DIRECTO
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Configurar logging con rotación (PASO 6)
    from logging.handlers import RotatingFileHandler
    log_path = Path.home() / ".eidos" / "eidos_core.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    handler = RotatingFileHandler(
        str(log_path),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    
    # También log a consola
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root_logger.addHandler(console)
    
    print("=" * 70)
    print("🧠 EIDOS CORE - Sistema de Autonomía Total")
    print("=" * 70)
    print()
    print("Iniciando EIDOS...")
    print()
    
    # Despertar EIDOS
    eidos = get_eidos_core()
    success = eidos.awaken()
    
    if success:
        print()
        print("✅ EIDOS está despierto y autónomo")
        print()
        print("Comandos disponibles:")
        print("  eidos.status()     - Ver estado completo")
        print("  eidos.sleep()      - Detener ciclo autónomo")
        print("  eidos.execute_command('ls -la') - Ejecutar comando")
        print()
        print("Presiona Ctrl+C para detener...")
        print()
        
        try:
            # Mantener vivo
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print()
            print("⛔ Deteniendo EIDOS...")
            eidos.sleep()
            print("👋 EIDOS detenido")
    else:
        print()
        print("❌ No se pudo iniciar EIDOS")
        sys.exit(1)
