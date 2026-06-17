"""
EIDOS core/rate_limiter.py — Rate Limiting & Circuit Breaker
===========================================================
Sistema de protección contra bucles infinitos, recursos agotados,
y auto-destrucción por cambios masivos.

Responsabilidades:
- Ventana deslizante: X acciones por unidad de tiempo
- Circuit breaker: N fallos consecutivos = bloqueo temporal
- Exponential backoff: Espera creciente entre reintentos
- Priority queue: Acciones críticas vs mejora vs análisis

Uso:
    from core.rate_limiter import RateLimiter, CircuitBreaker
    
    limiter = RateLimiter()
    
    # Verificar si se permite acción
    if limiter.allow("auto_change"):
        try:
            result = perform_auto_change()
            limiter.record("auto_change", success=True)
        except Exception as e:
            limiter.record("auto_change", success=False)
            if limiter.is_circuit_open("auto_change"):
                logger.critical("Circuit breaker activado!")
    
    # Configuración personalizada
    config = RateLimitConfig(
        action_type="claude_api",
        max_per_hour=3,
        max_failures=2,
        cooldown_minutes=120,
        priority=1  # 0=crítico, 1=alta, 2=media, 3=baja
    )

Autor: EIDOS Autonomy System
Versión: 1.0.0
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Literal, Any, Callable

# Configuración de logging estructurado
log = logging.getLogger("eidos.rate_limiter")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENUMS & DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class CircuitState(Enum):
    """Estados del circuit breaker."""
    CLOSED = "closed"      # Funcionamiento normal
    OPEN = "open"          # Circuito abierto, rechazando peticiones
    HALF_OPEN = "half_open"  # Prueba de recuperación


class Priority(Enum):
    """Niveles de prioridad para acciones."""
    CRITICAL = 0   # No se ratea (ej: emergency stop)
    HIGH = 1       # API calls, user requests
    MEDIUM = 2     # Auto-improvements
    LOW = 3        # Background analysis


@dataclass
class RateLimitConfig:
    """Configuración de rate limiting para un tipo de acción."""
    action_type: str
    max_per_hour: int = 10
    max_failures: int = 3
    cooldown_minutes: int = 60
    priority: int = 2  # Priority.MEDIUM
    window_minutes: int = 60
    description: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RateLimitConfig":
        return cls(**data)


@dataclass
class ActionRecord:
    """Registro de una acción ejecutada."""
    action_type: str
    timestamp: datetime
    success: bool
    duration_ms: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CircuitBreakerState:
    """Estado interno del circuit breaker."""
    state: CircuitState
    failures: int = 0
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    total_failures: int = 0
    total_successes: int = 0
    opened_at: Optional[datetime] = None


# ═══════════════════════════════════════════════════════════════════════════════
#  CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """
    Implementación del patrón Circuit Breaker.
    
    Si N fallos consecutivos ocurren, el circuito se "abre"
    y rechaza peticiones durante un tiempo de recuperación.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 3600,  # 1 hora
        half_open_max_calls: int = 1
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        # Estado por tipo de acción
        self._states: Dict[str, CircuitBreakerState] = {}
        self._lock = threading.RLock()
        
        log.debug(f"CircuitBreaker inicializado: threshold={failure_threshold}")
    
    def is_open(self, action_type: str) -> bool:
        """
        Verifica si el circuito está abierto para esta acción.
        
        Args:
            action_type: Tipo de acción a verificar
            
        Returns:
            True si el circuito está abierto (rechazar peticiones)
        """
        with self._lock:
            state = self._states.get(action_type)
            
            if not state:
                return False
            
            if state.state == CircuitState.OPEN:
                # Verificar si pasó el tiempo de recuperación
                if state.opened_at:
                    elapsed = (datetime.now() - state.opened_at).total_seconds()
                    if elapsed > self.recovery_timeout:
                        log.info(f"Circuit {action_type}: OPEN → HALF_OPEN")
                        state.state = CircuitState.HALF_OPEN
                        state.failures = 0
                        return False
                return True
            
            return False
    
    def can_execute(self, action_type: str) -> bool:
        """
        Verifica si se puede ejecutar una acción.
        
        En estado HALF_OPEN, solo permite un número limitado de llamadas
        para probar si el servicio se recuperó.
        
        Args:
            action_type: Tipo de acción
            
        Returns:
            True si se permite ejecutar
        """
        with self._lock:
            if self.is_open(action_type):
                return False
            
            state = self._states.get(action_type)
            if state and state.state == CircuitState.HALF_OPEN:
                # En half_open, limitar número de pruebas
                return state.failures < self.half_open_max_calls
            
            return True
    
    def record_failure(self, action_type: str) -> bool:
        """
        Registra un fallo para esta acción.
        
        Args:
            action_type: Tipo de acción que falló
            
        Returns:
            True si el circuito se acaba de abrir
        """
        with self._lock:
            if action_type not in self._states:
                self._states[action_type] = CircuitBreakerState(
                    state=CircuitState.CLOSED
                )
            
            state = self._states[action_type]
            state.failures += 1
            state.total_failures += 1
            state.last_failure_time = datetime.now()
            
            # Verificar si debemos abrir el circuito
            if state.failures >= self.failure_threshold:
                if state.state != CircuitState.OPEN:
                    state.state = CircuitState.OPEN
                    state.opened_at = datetime.now()
                    self._on_circuit_open(action_type, state)
                    return True
            
            return False
    
    def record_success(self, action_type: str):
        """Registra un éxito, reseteando contadores de fallos."""
        with self._lock:
            if action_type not in self._states:
                self._states[action_type] = CircuitBreakerState(
                    state=CircuitState.CLOSED
                )
            
            state = self._states[action_type]
            
            # Si estábamos en HALF_OPEN y tuvimos éxito, cerrar circuito
            if state.state == CircuitState.HALF_OPEN:
                log.info(f"Circuit {action_type}: HALF_OPEN → CLOSED")
                state.state = CircuitState.CLOSED
                state.failures = 0
                state.opened_at = None
            
            state.total_successes += 1
            state.last_success_time = datetime.now()
    
    def _on_circuit_open(self, action_type: str, state: CircuitBreakerState):
        """Callback cuando un circuito se abre."""
        log.critical(
            f"🚨 CIRCUIT BREAKER ABIERTO: {action_type}\n"
            f"   Fallos consecutivos: {state.failures}\n"
            f"   Total fallos: {state.total_failures}\n"
            f"   Recuperación en: {self.recovery_timeout}s"
        )
        # Aquí se podría notificar a Colony, enviar alerta, etc.
    
    def get_state(self, action_type: str) -> Optional[CircuitBreakerState]:
        """Obtiene el estado actual del circuito para una acción."""
        with self._lock:
            return self._states.get(action_type)
    
    def get_all_states(self) -> Dict[str, CircuitBreakerState]:
        """Obtiene todos los estados de circuitos."""
        with self._lock:
            return dict(self._states)


# ═══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITER PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    """
    Rate Limiter con ventana deslizante y priority queue.
    
    Protege contra:
    - Bucle infinito de auto-mejora
    - Cambios masivos que saturan git
    - Llamadas a API externas costosas
    - Recursos agotados
    """
    
    # Configuraciones por defecto para acciones conocidas
    DEFAULT_CONFIGS: Dict[str, RateLimitConfig] = {
        "auto_change": RateLimitConfig(
            action_type="auto_change",
            max_per_hour=10,
            max_failures=3,
            cooldown_minutes=60,
            priority=Priority.MEDIUM.value,
            description="Auto-modificación de código"
        ),
        "claude_api": RateLimitConfig(
            action_type="claude_api",
            max_per_hour=3,
            max_failures=2,
            cooldown_minutes=120,
            priority=Priority.HIGH.value,
            description="Llamadas API externas (costosas)"
        ),
        "ollama_request": RateLimitConfig(
            action_type="ollama_request",
            max_per_hour=30,
            max_failures=5,
            cooldown_minutes=30,
            priority=Priority.MEDIUM.value,
            description="Consultas a modelos locales"
        ),
        "git_operation": RateLimitConfig(
            action_type="git_operation",
            max_per_hour=20,
            max_failures=3,
            cooldown_minutes=30,
            priority=Priority.HIGH.value,
            description="Operaciones de git (commits, branches)"
        ),
        "file_modification": RateLimitConfig(
            action_type="file_modification",
            max_per_hour=50,
            max_failures=10,
            cooldown_minutes=30,
            priority=Priority.LOW.value,
            description="Modificación de archivos de código"
        ),
        "core_modification": RateLimitConfig(
            action_type="core_modification",
            max_per_hour=5,
            max_failures=2,
            cooldown_minutes=120,
            priority=Priority.HIGH.value,
            description="Modificación de archivos core (kernel, brain)"
        ),
    }
    
    def __init__(
        self,
        config_path: Optional[Path] = None,
        custom_configs: Optional[Dict[str, RateLimitConfig]] = None
    ):
        """
        Inicializa RateLimiter.
        
        Args:
            config_path: Path a archivo de configuración JSON
            custom_configs: Configuraciones personalizadas (override defaults)
        """
        self._configs: Dict[str, RateLimitConfig] = {}
        self._windows: Dict[str, List[datetime]] = {}
        self._records: Dict[str, List[ActionRecord]] = {}
        self._lock = threading.RLock()
        
        # Inicializar circuit breaker
        self.breaker = CircuitBreaker()
        
        # Cargar configuraciones
        self._load_configs(config_path, custom_configs)
        
        # Cargar historial si existe
        self._load_history()
        
        log.info(f"RateLimiter inicializado con {len(self._configs)} configs")
    
    def allow(
        self,
        action_type: str,
        priority_override: Optional[int] = None
    ) -> bool:
        """
        Verifica si se permite ejecutar una acción.
        
        Checks:
        1. Circuit breaker no está abierto
        2. No se excede rate limit (ventana deslizante)
        3. Prioridad permite ejecución
        
        Args:
            action_type: Tipo de acción
            priority_override: Override de prioridad (opcional)
            
        Returns:
            True si se permite ejecutar la acción
        """
        with self._lock:
            # 1. Verificar circuit breaker
            if self.breaker.is_open(action_type):
                log.warning(f"RateLimiter: Circuito abierto para {action_type}")
                return False
            
            if not self.breaker.can_execute(action_type):
                log.warning(f"RateLimiter: Circuito en half_open, esperando {action_type}")
                return False
            
            # 2. Verificar configuración existe
            if action_type not in self._configs:
                log.debug(f"No hay config para {action_type}, permitiendo")
                return True
            
            config = self._configs[action_type]
            
            # Prioridad CRITICAL siempre permite
            if priority_override == Priority.CRITICAL.value or config.priority == Priority.CRITICAL.value:
                return True
            
            # 3. Verificar ventana deslizante
            window = self._windows.get(action_type, [])
            cutoff = datetime.now() - timedelta(minutes=config.window_minutes)
            recent = [t for t in window if t > cutoff]
            
            # Actualizar ventana limpia
            self._windows[action_type] = recent
            
            if len(recent) >= config.max_per_hour:
                log.warning(
                    f"RateLimiter: Límite alcanzado para {action_type}\n"
                    f"   {len(recent)}/{config.max_per_hour} en última hora"
                )
                return False
            
            return True
    
    def record(
        self,
        action_type: str,
        success: bool,
        duration_ms: int = 0,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Registra la ejecución de una acción.
        
        Args:
            action_type: Tipo de acción
            success: True si tuvo éxito
            duration_ms: Duración en milisegundos
            metadata: Metadata adicional
        """
        with self._lock:
            # Registrar en ventana
            if action_type not in self._windows:
                self._windows[action_type] = []
            self._windows[action_type].append(datetime.now())
            
            # Registrar en historial
            record = ActionRecord(
                action_type=action_type,
                timestamp=datetime.now(),
                success=success,
                duration_ms=duration_ms,
                metadata=metadata or {}
            )
            
            if action_type not in self._records:
                self._records[action_type] = []
            self._records[action_type].append(record)
            
            # Limpiar historial antiguo (mantener últimas 1000)
            self._records[action_type] = self._records[action_type][-1000:]
            
            # Actualizar circuit breaker
            if success:
                self.breaker.record_success(action_type)
            else:
                opened = self.breaker.record_failure(action_type)
                if opened:
                    log.critical(f"Circuit breaker activado para {action_type}")
            
            # Guardar historial periódicamente
            if len(self._records[action_type]) % 100 == 0:
                self._save_history()
    
    def is_circuit_open(self, action_type: str) -> bool:
        """Verifica si el circuit breaker está abierto."""
        return self.breaker.is_open(action_type)
    
    def get_remaining_quota(self, action_type: str) -> int:
        """
        Obtiene cuántas acciones más se permiten en esta ventana.
        
        Returns:
            Número de acciones restantes, o -1 si no hay límite
        """
        with self._lock:
            if action_type not in self._configs:
                return -1
            
            config = self._configs[action_type]
            window = self._windows.get(action_type, [])
            cutoff = datetime.now() - timedelta(minutes=config.window_minutes)
            recent = [t for t in window if t > cutoff]
            
            return max(0, config.max_per_hour - len(recent))
    
    def get_stats(self, action_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Obtiene estadísticas de rate limiting.
        
        Args:
            action_type: Si especificado, solo stats de ese tipo
            
        Returns:
            Diccionario con estadísticas
        """
        with self._lock:
            if action_type:
                return self._get_action_stats(action_type)
            
            return {
                action: self._get_action_stats(action)
                for action in self._configs.keys()
            }
    
    def _get_action_stats(self, action_type: str) -> Dict[str, Any]:
        """Stats para un tipo de acción específico."""
        config = self._configs.get(action_type)
        records = self._records.get(action_type, [])
        circuit_state = self.breaker.get_state(action_type)
        
        if not config:
            return {"error": "No config found"}
        
        # Calcular métricas
        total = len(records)
        successes = len([r for r in records if r.success])
        failures = total - successes
        
        recent = records[-100:] if records else []
        recent_success = len([r for r in recent if r.success])
        recent_failures = len(recent) - recent_success
        
        return {
            "action_type": action_type,
            "config": config.to_dict(),
            "total_records": total,
            "total_successes": successes,
            "total_failures": failures,
            "success_rate": successes / total if total > 0 else 0,
            "recent_100_success": recent_success,
            "recent_100_failures": recent_failures,
            "circuit_state": circuit_state.state.value if circuit_state else "unknown",
            "circuit_failures": circuit_state.failures if circuit_state else 0,
            "remaining_quota": self.get_remaining_quota(action_type),
        }
    
    def add_config(self, config: RateLimitConfig):
        """Añade o actualiza configuración para un tipo de acción."""
        with self._lock:
            self._configs[config.action_type] = config
            log.debug(f"Config añadida para {config.action_type}")
    
    def remove_config(self, action_type: str):
        """Elimina configuración de rate limiting."""
        with self._lock:
            if action_type in self._configs:
                del self._configs[action_type]
    
    def reset_circuit(self, action_type: str):
        """Fuerza reset del circuit breaker para una acción."""
        with self._lock:
            self.breaker.record_success(action_type)
            log.info(f"Circuit breaker reseteado manualmente para {action_type}")
    
    # ═════════════════════════════════════════════════════════════════════════
    #  PERSISTENCIA
    # ═════════════════════════════════════════════════════════════════════════
    
    def _load_configs(
        self,
        config_path: Optional[Path],
        custom_configs: Optional[Dict[str, RateLimitConfig]]
    ):
        """Carga configuraciones desde archivo y/o parámetros."""
        # Empezar con defaults
        self._configs = dict(self.DEFAULT_CONFIGS)
        
        # Cargar desde archivo si existe
        if config_path and config_path.exists():
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)
                    for action_type, config_data in data.get("configs", {}).items():
                        self._configs[action_type] = RateLimitConfig.from_dict(config_data)
                log.info(f"Configs cargadas desde {config_path}")
            except Exception as e:
                log.error(f"Error cargando configs: {e}")
        
        # Aplicar overrides personalizados
        if custom_configs:
            for action_type, config in custom_configs.items():
                self._configs[action_type] = config
    
    def _load_history(self):
        """Carga historial de acciones desde disco."""
        history_file = Path("/home/ser/EIDOS/.eidos/rate_limiter_history.json")
        
        if not history_file.exists():
            return
        
        try:
            with open(history_file, "r") as f:
                data = json.load(f)
                
                for action_type, records_data in data.get("records", {}).items():
                    self._records[action_type] = [
                        ActionRecord(
                            action_type=r["action_type"],
                            timestamp=datetime.fromisoformat(r["timestamp"]),
                            success=r["success"],
                            duration_ms=r["duration_ms"],
                            metadata=r.get("metadata", {})
                        )
                        for r in records_data[-1000:]  # Limitar a últimas 1000
                    ]
            
            log.debug(f"Historial cargado: {len(self._records)} tipos de acción")
            
        except Exception as e:
            log.error(f"Error cargando historial: {e}")
    
    def _save_history(self):
        """Persiste historial de acciones a disco."""
        history_file = Path("/home/ser/EIDOS/.eidos/rate_limiter_history.json")
        history_file.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(history_file, "w") as f:
                json.dump({
                    "records": {
                        action_type: [
                            {
                                "action_type": r.action_type,
                                "timestamp": r.timestamp.isoformat(),
                                "success": r.success,
                                "duration_ms": r.duration_ms,
                                "metadata": r.metadata
                            }
                            for r in records[-500:]  # Guardar últimas 500
                        ]
                        for action_type, records in self._records.items()
                    },
                    "last_saved": datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            log.error(f"Error guardando historial: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON & UTILIDADES
# ═══════════════════════════════════════════════════════════════════════════════

_limiter: Optional[RateLimiter] = None


def get_rate_limiter(
    config_path: Optional[Path] = None,
    custom_configs: Optional[Dict[str, RateLimitConfig]] = None
) -> RateLimiter:
    """
    Obtiene instancia singleton de RateLimiter.
    
    Args:
        config_path: Path a archivo de configuración
        custom_configs: Configuraciones personalizadas
        
    Returns:
        Instancia de RateLimiter
    """
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(config_path, custom_configs)
    return _limiter


def reset_rate_limiter():
    """Resetea singleton (útil para tests)."""
    global _limiter
    _limiter = None


def check_rate_limit(action_type: str) -> bool:
    """Función helper para verificar rápidamente."""
    return get_rate_limiter().allow(action_type)


# ═══════════════════════════════════════════════════════════════════════════════
#  DECORATOR
# ═══════════════════════════════════════════════════════════════════════════════

def rate_limited(
    action_type: str,
    priority: Optional[int] = None,
    on_rate_limit: Optional[Callable] = None
):
    """
    Decorador para rate-limitar funciones.
    
    Uso:
        @rate_limited("claude_api", priority=1)
        def call_claude_api(prompt: str) -> str:
            ...
    
    Args:
        action_type: Tipo de acción para rate limiting
        priority: Override de prioridad
        on_rate_limit: Callback cuando se alcanza límite
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            limiter = get_rate_limiter()
            
            if not limiter.allow(action_type, priority):
                if on_rate_limit:
                    return on_rate_limit(*args, **kwargs)
                raise RateLimitExceeded(
                    f"Rate limit excedido para {action_type}"
                )
            
            start = time.time()
            try:
                result = func(*args, **kwargs)
                limiter.record(action_type, success=True, 
                              duration_ms=int((time.time() - start) * 1000))
                return result
            except Exception as e:
                limiter.record(action_type, success=False,
                              duration_ms=int((time.time() - start) * 1000),
                              metadata={"error": str(e)})
                raise
        
        return wrapper
    return decorator


class RateLimitExceeded(Exception):
    """Excepción lanzada cuando se excede rate limit."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN (tests rápidos)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Configurar logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("RateLimiter - Test de inicialización")
    print("=" * 50)
    
    try:
        limiter = RateLimiter()
        print(f"✅ RateLimiter inicializado")
        print(f"   Configs: {len(limiter._configs)}")
        print(f"   Tipos: {list(limiter._configs.keys())}")
        
        # Test básico
        print("\n📊 Test allow():")
        for action in ["auto_change", "claude_api", "desconocido"]:
            allowed = limiter.allow(action)
            remaining = limiter.get_remaining_quota(action)
            print(f"   {action}: allow={allowed}, remaining={remaining}")
        
        # Test circuit breaker
        print("\n🔄 Test circuit breaker:")
        for i in range(5):
            limiter.record("test_action", success=False)
            is_open = limiter.is_circuit_open("test_action")
            print(f"   Fallo {i+1}: circuit_open={is_open}")
        
        print("\n✅ Tests básicos completados")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
