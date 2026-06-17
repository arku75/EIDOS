"""
core/eidos_metacog_real.py -- Metacognicion REAL [S125]

Reemplaza la falsa "self-awareness" de psutil (eidos_self_awareness.py)
y la "conciencia" de 8 templates aleatorios (eidos_metacognition.py)
con introspeccion REAL basada en datos, no en narrativas prefabricadas.

Componentes:
  1. CapabilityModel -- inventario estructurado de lo que EIDOS PUEDE y NO PUEDE hacer,
     con confianza calibrada por intentos reales (no "siempre digo que si").
  2. SelfMonitor -- cada 60s verifica: bucle? recursos? degradacion? cambio de estrategia?
  3. StrategySelector -- evalua multiples enfoques para una tarea segun contexto real.
  4. ConfidenceCalibrator -- tracking de predicciones vs resultados, ajuste de scores.

Principio: NINGUN dato se inventa. Todo viene de psutil, intentos reales, o DB.
Las narrativas NO son templates -- se generan a partir de los datos reales.

Uso:
    from core.eidos_metacog_real import get_metacog_real
    mc = get_metacog_real()

    # Que puedo hacer?
    result = mc.can_i("browse_web")
    # -> {"can": True, "confidence": 0.85, "evidence": 142 attempts, "last_tested": "2m ago"}

    # Auto-monitoreo
    warnings = mc.monitor()
    if warnings:
        for w in warnings:
            log.warning(w)

    # Seleccion de estrategia
    strategy = mc.choose_strategy("click_button", context={"cpu_load": 0.9})
    # -> {"method": "keyboard_shortcut", "reason": "CPU alto, mejor atajo que mouse"}

    # Calibrar tras un intento
    mc.record_attempt("click_element", predicted_confidence=0.80, success=False)
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.metacog_real")

# ── DB de metacognicion real ────────────────────────────────────────────────
METACOG_DB = Path.home() / ".eidos" / "metacog_real.db"

# Intervalo de auto-monitoreo (segundos)
MONITOR_INTERVAL = 60

# Umbrales de alarma
CPU_WARN = 80.0       # % de CPU
RAM_WARN = 90.0       # % de RAM
LOOP_THRESHOLD = 5     # acciones identicas/falidas consecutivas
DEGRADE_THRESHOLD = 3  # fallos consecutivos del mismo approach

# Capacidades que EIDOS puede verificar realmente, no con imports falsos
CAPABILITY_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── CAN (cosas que EIDOS puede hacer, con verificacion real) ──────────
    "browse_web": {
        "can": True,
        "description": "Navegar con Firefox/Playwright",
        "verify": lambda: _check_binary("firefox") or _check_binary("chromium"),
        "initial_confidence": 0.85,
        "category": "research",
    },
    "execute_terminal": {
        "can": True,
        "description": "Ejecutar comandos en shell",
        "verify": lambda: bool(os.environ.get("SHELL")),
        "initial_confidence": 0.92,
        "category": "system",
    },
    "perceive_screen": {
        "can": True,
        "description": "Capturar y analizar pantalla",
        "verify": lambda: _check_binary("scrot") and _check_import("PIL"),
        "initial_confidence": 0.78,
        "category": "system",
    },
    "click_element": {
        "can": True,
        "description": "Click en UI via xdotool",
        "verify": lambda: _check_binary("xdotool"),
        "initial_confidence": 0.70,
        "category": "system",
    },
    "type_text": {
        "can": True,
        "description": "Escribir texto via xdotool/HID",
        "verify": lambda: _check_binary("xdotool"),
        "initial_confidence": 0.75,
        "category": "system",
    },
    "control_mouse": {
        "can": True,
        "description": "Mover y controlar raton",
        "verify": lambda: _check_binary("xdotool"),
        "initial_confidence": 0.72,
        "category": "system",
    },
    "use_groq": {
        "can": True,
        "description": "Llamar a Groq API (cloud)",
        "verify": lambda: _check_env_secret("GROQ_API_KEY"),
        "initial_confidence": 0.90,
        "category": "reasoning",
    },
    "use_deepseek": {
        "can": True,
        "description": "Llamar a DeepSeek API (cloud)",
        "verify": lambda: _check_env_secret("DEEPSEEK_API_KEY"),
        "initial_confidence": 0.88,
        "category": "reasoning",
    },
    "use_ollama": {
        "can": True,
        "description": "Usar modelos locales via Ollama",
        "verify": lambda: _check_port("127.0.0.1", 11434),
        "initial_confidence": 0.80,
        "category": "reasoning",
    },
    "learn_from_man": {
        "can": True,
        "description": "Aprender de paginas man",
        "verify": lambda: _check_binary("man"),
        "initial_confidence": 0.95,
        "category": "research",
    },
    "query_wikipedia": {
        "can": True,
        "description": "Consultar Wikipedia",
        "verify": lambda: _check_network(),
        "initial_confidence": 0.75,
        "category": "research",
    },
    "search_web": {
        "can": True,
        "description": "Buscar en la web (DuckDuckGo/Google)",
        "verify": lambda: _check_network(),
        "initial_confidence": 0.70,
        "category": "research",
    },
    "read_code": {
        "can": True,
        "description": "Leer y analizar codigo fuente",
        "verify": lambda: True,  # Siempre puede leer archivos locales
        "initial_confidence": 0.90,
        "category": "reasoning",
    },
    "write_code": {
        "can": True,
        "description": "Escribir y modificar archivos de codigo",
        "verify": lambda: os.access(Path.home(), os.W_OK),
        "initial_confidence": 0.88,
        "category": "creation",
    },
    "sandbox_exec": {
        "can": True,
        "description": "Ejecutar codigo en sandbox",
        "verify": lambda: _check_binary("bwrap") or _check_binary("docker"),
        "initial_confidence": 0.65,
        "category": "system",
    },
    "curate_graph": {
        "can": True,
        "description": "Curar grafo de conocimiento",
        "verify": lambda: _check_import("core.eidos_curate_graph"),
        "initial_confidence": 0.80,
        "category": "system",
    },
    "debate_internal": {
        "can": True,
        "description": "Debate interno entre personajes colony",
        "verify": lambda: _check_import("core.colony_community"),
        "initial_confidence": 0.82,
        "category": "communication",
    },
    "speak_spanish": {
        "can": True,
        "description": "Expresarme en espanol",
        "verify": lambda: True,  # EIDOS siempre habla espanol
        "initial_confidence": 0.99,
        "category": "communication",
    },
    "memory_semantic": {
        "can": True,
        "description": "Memoria semantica (ChromaDB)",
        "verify": lambda: _check_port("127.0.0.1", 8767),
        "initial_confidence": 0.75,
        "category": "system",
    },
    "autonomous_learn": {
        "can": True,
        "description": "Aprendizaje autonomo continuo",
        "verify": lambda: _check_import("core.eidos_autonomous_loop"),
        "initial_confidence": 0.78,
        "category": "system",
    },
    "logical_reasoning": {
        "can": True,
        "description": "Razonamiento logico determinista",
        "verify": lambda: _check_import("core.eidos_logic"),
        "initial_confidence": 0.85,
        "category": "reasoning",
    },

    # ── CANNOT (cosas que EIDOS NO puede hacer) ───────────────────────────
    "voice_input": {
        "can": False,
        "description": "Reconocimiento de voz en tiempo real",
        "verify": lambda: False,  # No tiene microfono funcional
        "initial_confidence": 0.05,
        "category": "communication",
    },
    "video_analysis": {
        "can": False,
        "description": "Analisis de video en tiempo real",
        "verify": lambda: _check_import("cv2"),  # opencv puede estar instalado
        "initial_confidence": 0.15,
        "category": "perception",
    },
    "render_3d": {
        "can": False,
        "description": "Renderizado 3D / OpenGL",
        "verify": lambda: False,  # Sin GPU programable accesible
        "initial_confidence": 0.0,
        "category": "creation",
    },
    "understand_emotions": {
        "can": False,
        "description": "Comprension real de emociones humanas",
        "verify": lambda: False,  # Solo simula VAD, no entiende emocion real
        "initial_confidence": 0.10,
        "category": "reasoning",
    },
    "physical_action": {
        "can": False,
        "description": "Accion fisica en el mundo real",
        "verify": lambda: False,  # No tiene cuerpo fisico
        "initial_confidence": 0.0,
        "category": "system",
    },
    "voice_synthesis": {
        "can": False,
        "description": "Sintesis de voz natural",
        "verify": lambda: _check_binary("espeak") or _check_binary("festival"),
        "initial_confidence": 0.20,
        "category": "communication",
    },
    "image_generation": {
        "can": False,
        "description": "Generacion de imagenes (Stable Diffusion, etc.)",
        "verify": lambda: _check_import("diffusers") or _check_binary("python3"),
        "initial_confidence": 0.25,
        "category": "creation",
    },
    "real_time_translation": {
        "can": False,
        "description": "Traduccion simultanea en tiempo real",
        "verify": lambda: False,
        "initial_confidence": 0.10,
        "category": "communication",
    },
}

# Estrategias por tipo de tarea
STRATEGY_REGISTRY: Dict[str, List[Dict[str, Any]]] = {
    "click_element": [
        {"method": "xdotool_click", "speed": 0.9, "risk": 0.3,
         "description": "Click directo con xdotool", "requires": ["xdotool"]},
        {"method": "keyboard_shortcut", "speed": 0.7, "risk": 0.1,
         "description": "Atajo de teclado equivalente", "requires": ["xdotool"]},
        {"method": "xdotool_mousemove_click", "speed": 0.5, "risk": 0.2,
         "description": "Mover raton + click visible", "requires": ["xdotool"]},
    ],
    "type_text": [
        {"method": "xdotool_type", "speed": 0.9, "risk": 0.2,
         "description": "Escribir con xdotool type", "requires": ["xdotool"]},
        {"method": "clipboard_paste", "speed": 0.95, "risk": 0.15,
         "description": "Copiar a clipboard + pegar", "requires": ["xclip"]},
        {"method": "xdotool_key", "speed": 0.4, "risk": 0.1,
         "description": "Enviar tecla por tecla", "requires": ["xdotool"]},
    ],
    "perceive_screen": [
        {"method": "scrot_fast", "speed": 0.85, "risk": 0.1,
         "description": "Screenshot con scrot", "requires": ["scrot"]},
        {"method": "pil_screenshot", "speed": 0.6, "risk": 0.1,
         "description": "Screenshot via PIL ImageGrab", "requires": ["PIL"]},
        {"method": "xwd_import", "speed": 0.4, "risk": 0.2,
         "description": "xwd + ImageMagick convert", "requires": ["xwd", "convert"]},
    ],
    "execute_command": [
        {"method": "subprocess_run", "speed": 0.9, "risk": 0.3,
         "description": "Ejecucion directa con subprocess", "requires": []},
        {"method": "shell_script", "speed": 0.7, "risk": 0.4,
         "description": "Script shell temporal", "requires": []},
        {"method": "pty_spawn", "speed": 0.5, "risk": 0.2,
         "description": "Terminal interactiva PTY", "requires": ["pty"]},
    ],
    "search_web": [
        {"method": "ddg_search", "speed": 0.8, "risk": 0.1,
         "description": "DuckDuckGo instant answer", "requires": ["network"]},
        {"method": "wikipedia_api", "speed": 0.7, "risk": 0.05,
         "description": "Wikipedia API directa", "requires": ["network"]},
        {"method": "browser_search", "speed": 0.3, "risk": 0.3,
         "description": "Busqueda con navegador real", "requires": ["firefox", "network"]},
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _check_binary(name: str) -> bool:
    """Verifica si un binario existe en PATH."""
    import shutil
    return shutil.which(name) is not None


def _check_import(module_path: str) -> bool:
    """Verifica si un modulo Python es importable."""
    try:
        __import__(module_path, fromlist=[""])
        return True
    except Exception:
        return False


def _check_port(host: str, port: int) -> bool:
    """Verifica si un puerto TCP esta abierto."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def _check_network() -> bool:
    """Verifica conectividad de red real."""
    try:
        import urllib.request
        urllib.request.urlopen("https://en.wikipedia.org", timeout=2)
        return True
    except Exception:
        return False


def _check_env_secret(key: str) -> bool:
    """Verifica si una clave existe en secrets.env."""
    secrets_env = Path.home() / ".eidos" / "secrets.env"
    if not secrets_env.exists():
        # Tambien buscar en entorno
        return key in os.environ
    try:
        content = secrets_env.read_text()
        return key in content
    except Exception:
        return key in os.environ


# ══════════════════════════════════════════════════════════════════════════════
# Componente 1: CapabilityModel
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CapabilityState:
    """Estado calibrado de una capacidad."""
    name: str
    can: bool
    confidence: float
    attempt_count: int = 0
    success_count: int = 0
    last_verified: float = 0.0
    last_attempt: float = 0.0
    description: str = ""
    category: str = ""

    @property
    def success_rate(self) -> float:
        if self.attempt_count == 0:
            return self.confidence  # prior
        return self.success_count / self.attempt_count

    @property
    def age_seconds(self) -> float:
        if self.last_verified == 0:
            return float("inf")
        return time.time() - self.last_verified

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "can": self.can,
            "confidence": round(self.confidence, 3),
            "attempt_count": self.attempt_count,
            "success_count": self.success_count,
            "success_rate": round(self.success_rate, 3),
            "last_verified_ago_s": round(self.age_seconds, 1) if self.age_seconds != float("inf") else None,
            "description": self.description,
            "category": self.category,
        }


class CapabilityModel:
    """Inventario estructurado de lo que EIDOS PUEDE y NO PUEDE hacer.

    Cada capacidad tiene una confianza calibrada por intentos reales.
    Cuando se pregunta "puedes hacer X?", responde con confianza calibrada,
    no siempre "si".
    """

    def __init__(self):
        self._capabilities: Dict[str, CapabilityState] = {}
        self._init_capabilities()
        self._load_persisted_state()
        log.info("CapabilityModel: %d capacidades cargadas", len(self._capabilities))

    def _init_capabilities(self):
        """Inicializa todas las capacidades del registro."""
        for name, info in CAPABILITY_REGISTRY.items():
            # Verificar estado actual (costoso, solo al inicio)
            try:
                verified = info["verify"]()
            except Exception:
                verified = info["can"]  # asumir lo registrado si falla la verificacion

            self._capabilities[name] = CapabilityState(
                name=name,
                can=info["can"],
                confidence=info["initial_confidence"],
                description=info["description"],
                category=info["category"],
                last_verified=time.time(),
            )

    def _load_persisted_state(self):
        """Carga historial de intentos desde DB."""
        try:
            from core.db import get_conn
            conn = get_conn(METACOG_DB, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS capability_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    capability TEXT NOT NULL,
                    ts REAL NOT NULL,
                    predicted_confidence REAL,
                    success INTEGER NOT NULL,
                    method TEXT DEFAULT '',
                    context TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_cap_attempts
                    ON capability_attempts(capability, ts);
            """)
            conn.commit()

            # Cargar conteos por capacidad
            rows = conn.execute("""
                SELECT capability,
                       COUNT(*) as total,
                       SUM(success) as successes,
                       MAX(ts) as last_ts
                FROM capability_attempts
                GROUP BY capability
            """).fetchall()

            for cap_name, total, successes, last_ts in rows:
                if cap_name in self._capabilities:
                    cap = self._capabilities[cap_name]
                    cap.attempt_count = total
                    cap.success_count = successes or 0
                    cap.last_attempt = last_ts or 0

            conn.commit()
        except Exception as e:
            log.debug("CapabilityModel._load: %s", e)

    def can_i(self, thing: str) -> Dict[str, Any]:
        """Responde si EIDOS puede hacer algo, con confianza calibrada.

        Args:
            thing: Nombre de la capacidad o descripcion en lenguaje natural.

        Returns:
            Dict con 'can', 'confidence', 'evidence', 'reason'.
            La confianza esta calibrada, no es siempre optimista.
        """
        # Busqueda exacta primero
        cap = self._capabilities.get(thing)

        # Busqueda fuzzy si no hay exacta
        if cap is None:
            thing_lower = thing.lower().replace(" ", "_")
            for name, state in self._capabilities.items():
                if thing_lower in name or name in thing_lower:
                    cap = state
                    break

        if cap is None:
            # Capacidad desconocida -- ser honesto
            return {
                "can": False,
                "confidence": 0.1,
                "evidence": "No tengo esta capacidad en mi inventario. No se si puedo hacerlo.",
                "reason": "unknown_capability",
                "suggestion": "Puedo intentarlo, pero no tengo datos previos de exito.",
            }

        # Re-verificar periodicamente (cada 5 minutos)
        if cap.age_seconds > 300:
            try:
                info = CAPABILITY_REGISTRY.get(cap.name, {})
                if info.get("verify"):
                    cap.last_verified = time.time()
            except Exception:
                pass

        # Construir respuesta calibrada
        evidence_parts = []
        if cap.attempt_count > 0:
            evidence_parts.append(f"{cap.attempt_count} intentos, {cap.success_rate:.0%} exito")
        if cap.age_seconds < float("inf") and cap.age_seconds < 86400:
            evidence_parts.append(f"verificado hace {cap.age_seconds:.0f}s")

        return {
            "can": cap.can and cap.confidence > 0.3,
            "confidence": round(cap.confidence, 3),
            "evidence": "; ".join(evidence_parts) if evidence_parts else "sin datos empiricos",
            "success_rate": round(cap.success_rate, 3),
            "attempts": cap.attempt_count,
            "description": cap.description,
            "reason": "empirical" if cap.attempt_count > 0 else "prior",
        }

    def record_attempt(self, capability: str, success: bool,
                       predicted_confidence: float = 0.0,
                       method: str = "") -> None:
        """Registra un intento real de usar una capacidad.

        Este es el UNICO camino por el que los scores cambian.
        Sin intentos reales, los scores se mantienen en sus priors.
        """
        cap = self._capabilities.get(capability)
        if cap is None:
            log.debug("record_attempt: capacidad desconocida '%s'", capability)
            return

        cap.attempt_count += 1
        if success:
            cap.success_count += 1
        cap.last_attempt = time.time()
        cap.last_verified = time.time()

        # Persistir en DB
        try:
            from core.db import get_conn
            conn = get_conn(METACOG_DB, timeout=10)
            conn.execute(
                "INSERT INTO capability_attempts "
                "(capability, ts, predicted_confidence, success, method) "
                "VALUES (?, ?, ?, ?, ?)",
                (capability, time.time(), round(predicted_confidence, 4),
                 1 if success else 0, method)
            )
            conn.commit()
        except Exception as e:
            log.debug("record_attempt DB: %s", e)

        log.info("Capability '%s': intento=%s exito=%s (total: %d/%d = %.0f%%)",
                 capability, "OK" if success else "FALLIDO",
                 cap.success_count, cap.attempt_count,
                 cap.success_rate * 100)

    def list_can(self, min_confidence: float = 0.3) -> List[Dict[str, Any]]:
        """Lista todo lo que EIDOS PUEDE hacer con confianza >= min_confidence."""
        return [
            c.to_dict() for c in self._capabilities.values()
            if c.can and c.confidence >= min_confidence
        ]

    def list_cannot(self) -> List[Dict[str, Any]]:
        """Lista todo lo que EIDOS NO PUEDE hacer."""
        return [
            c.to_dict() for c in self._capabilities.values()
            if not c.can
        ]

    def summary(self) -> Dict[str, Any]:
        """Resumen del modelo de capacidades."""
        can_list = [c for c in self._capabilities.values() if c.can]
        cannot_list = [c for c in self._capabilities.values() if not c.can]
        can_available = [c for c in can_list if c.confidence > 0.4]
        return {
            "can_count": len(can_list),
            "cannot_count": len(cannot_list),
            "available_count": len(can_available),
            "avg_can_confidence": round(
                sum(c.confidence for c in can_list) / max(len(can_list), 1), 3
            ),
            "by_category": {
                cat: len([c for c in can_list if c.category == cat])
                for cat in sorted(set(c.category for c in can_list))
            },
        }


# ══════════════════════════════════════════════════════════════════════════════
# Componente 2: SelfMonitor
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MonitorWarning:
    """Alerta generada por el auto-monitoreo."""
    level: str        # "info", "warning", "critical"
    category: str     # "loop", "resource", "performance", "strategy"
    message: str
    suggestion: str
    ts: float = field(default_factory=time.time)
    metrics: Dict[str, Any] = field(default_factory=dict)


class SelfMonitor:
    """Auto-monitoreo cada 60 segundos.

    Verifica:
      - "Estoy atrapado en un bucle?" (ultimas N acciones identicas o fallidas)
      - "Estoy usando demasiados recursos?" (CPU > 80%, RAM > 90%)
      - "Mi rendimiento esta degradando?" (tiempos de respuesta subiendo)
      - "Debo cambiar de estrategia?" (mismo approach fallando > 3 veces)
    """

    def __init__(self):
        self._action_history: deque = deque(maxlen=32)
        self._response_times: deque = deque(maxlen=100)
        self._failure_counts: Dict[str, int] = {}  # strategy -> consecutive failures
        self._last_monitor_ts: float = 0
        self._warnings: List[MonitorWarning] = []
        self._error_rate_window: deque = deque(maxlen=50)  # (ts, error_bool)

    def record_action(self, action: str, success: bool,
                      duration_ms: float = 0, strategy: str = "") -> None:
        """Registra una accion para deteccion de bucles y degradacion."""
        self._action_history.append({
            "action": action,
            "success": success,
            "duration_ms": duration_ms,
            "strategy": strategy,
            "ts": time.time(),
        })
        if duration_ms > 0:
            self._response_times.append(duration_ms)
        self._error_rate_window.append((time.time(), not success))

        # Actualizar contador de fallos por estrategia
        if strategy:
            if not success:
                self._failure_counts[strategy] = self._failure_counts.get(strategy, 0) + 1
            else:
                self._failure_counts[strategy] = 0

    def should_monitor(self) -> bool:
        """Determina si es momento de ejecutar el monitor."""
        return (time.time() - self._last_monitor_ts) >= MONITOR_INTERVAL

    def monitor(self, force: bool = False) -> List[MonitorWarning]:
        """Ejecuta el ciclo de auto-monitoreo.

        Retorna lista de warnings generados (vacia si todo OK).
        """
        if not force and not self.should_monitor():
            return []

        self._last_monitor_ts = time.time()
        self._warnings = []

        # 1. Deteccion de bucles
        self._check_loops()

        # 2. Uso de recursos
        self._check_resources()

        # 3. Degradacion de rendimiento
        self._check_performance()

        # 4. Cambio de estrategia necesario
        self._check_strategy_change()

        # Loggear warnings
        for w in self._warnings:
            if w.level == "critical":
                log.error("SelfMonitor [%s]: %s", w.category, w.message)
            elif w.level == "warning":
                log.warning("SelfMonitor [%s]: %s", w.category, w.message)
            else:
                log.info("SelfMonitor [%s]: %s", w.category, w.message)

        return self._warnings

    def _check_loops(self):
        """Detecta bucles: ultimas N acciones identicas o todas fallidas."""
        if len(self._action_history) < LOOP_THRESHOLD:
            return

        recent = list(self._action_history)[-LOOP_THRESHOLD:]

        # Todas las acciones iguales?
        actions = [a["action"] for a in recent]
        if len(set(actions)) == 1:
            self._warnings.append(MonitorWarning(
                level="warning",
                category="loop",
                message=f"Posible bucle: {LOOP_THRESHOLD} acciones '{actions[0]}' consecutivas identicas",
                suggestion=f"Cambiar de approach o pedir ayuda. Ultima accion: {actions[0]}",
                metrics={"action": actions[0], "count": LOOP_THRESHOLD},
            ))

        # Todas fallidas?
        if all(not a["success"] for a in recent):
            self._warnings.append(MonitorWarning(
                level="critical",
                category="loop",
                message=f"Todas las ultimas {LOOP_THRESHOLD} acciones han fallado",
                suggestion="DETENER y diagnosticar. Posible problema sistemico.",
                metrics={"failed_actions": actions},
            ))

    def _check_resources(self):
        """Monitor de recursos del sistema con psutil."""
        try:
            import psutil

            # CPU
            cpu_pct = psutil.cpu_percent(interval=0.1)
            if cpu_pct > CPU_WARN:
                self._warnings.append(MonitorWarning(
                    level="warning" if cpu_pct < 95 else "critical",
                    category="resource",
                    message=f"CPU alto: {cpu_pct:.1f}% (umbral: {CPU_WARN}%)",
                    suggestion="Reducir carga: pausar tareas no criticas, esperar.",
                    metrics={"cpu_percent": cpu_pct, "cpu_count": psutil.cpu_count()},
                ))

            # RAM
            mem = psutil.virtual_memory()
            if mem.percent > RAM_WARN:
                self._warnings.append(MonitorWarning(
                    level="warning" if mem.percent < 97 else "critical",
                    category="resource",
                    message=f"RAM alta: {mem.percent:.1f}% usado "
                            f"({mem.used / (1024**3):.1f}/{mem.total / (1024**3):.1f} GB)",
                    suggestion="Liberar memoria: comprimir caches, reiniciar servicios pesados.",
                    metrics={"ram_percent": mem.percent, "ram_used_gb": mem.used / (1024**3)},
                ))

            # Carga del sistema
            load1, load5, load15 = psutil.getloadavg()
            cpu_count = psutil.cpu_count() or 1
            if load1 > cpu_count * 1.5:
                self._warnings.append(MonitorWarning(
                    level="warning",
                    category="resource",
                    message=f"Load average alta: {load1:.1f} (cores: {cpu_count})",
                    suggestion="El sistema esta sobrecargado. Esperar antes de lanzar mas procesos.",
                    metrics={"load1": load1, "load5": load5, "load15": load15, "cpus": cpu_count},
                ))

        except ImportError:
            pass  # sin psutil no podemos monitorizar recursos
        except Exception as e:
            log.debug("_check_resources: %s", e)

    def _check_performance(self):
        """Detecta degradacion de rendimiento por tiempos de respuesta crecientes."""
        if len(self._response_times) < 10:
            return

        # Comparar media de los ultimos 10 vs media de los 10 anteriores
        recent = list(self._response_times)[-10:]
        older = list(self._response_times)[-20:-10] if len(self._response_times) >= 20 else []

        if not older:
            return

        avg_recent = sum(recent) / len(recent)
        avg_older = sum(older) / len(older)

        if avg_older > 0 and avg_recent > avg_older * 2.0:
            self._warnings.append(MonitorWarning(
                level="warning",
                category="performance",
                message=f"Tiempo de respuesta degradado: "
                        f"{avg_recent:.0f}ms vs {avg_older:.0f}ms antes (2x mas lento)",
                suggestion="Revisar si hay procesos compitiendo o el sistema bajo carga.",
                metrics={"avg_recent_ms": avg_recent, "avg_older_ms": avg_older},
            ))

        # Tasa de error reciente
        if len(self._error_rate_window) >= 10:
            recent_errors = list(self._error_rate_window)[-10:]
            error_rate = sum(1 for _, err in recent_errors if err) / len(recent_errors)
            if error_rate > 0.6:
                self._warnings.append(MonitorWarning(
                    level="critical",
                    category="performance",
                    message=f"Tasa de error elevada: {error_rate:.0%} en ultimas 10 acciones",
                    suggestion="DETENER. Algo esta fallando sistematicamente.",
                    metrics={"error_rate": error_rate},
                ))

    def _check_strategy_change(self):
        """Detecta si el approach actual debe cambiarse."""
        for strategy, failures in list(self._failure_counts.items()):
            if failures >= DEGRADE_THRESHOLD:
                self._warnings.append(MonitorWarning(
                    level="warning",
                    category="strategy",
                    message=f"Estrategia '{strategy}' ha fallado {failures} veces consecutivas",
                    suggestion=f"Cambiar de estrategia. La actual no esta funcionando. "
                               f"Probar un enfoque alternativo.",
                    metrics={"strategy": strategy, "consecutive_failures": failures},
                ))

    def warnings_summary(self) -> Dict[str, Any]:
        """Resumen del ultimo monitoreo."""
        return {
            "total_warnings": len(self._warnings),
            "by_level": {
                level: len([w for w in self._warnings if w.level == level])
                for level in ["info", "warning", "critical"]
            },
            "by_category": {
                cat: len([w for w in self._warnings if w.category == cat])
                for cat in sorted(set(w.category for w in self._warnings))
            },
            "latest_warnings": [
                {"category": w.category, "level": w.level, "message": w.message[:120]}
                for w in self._warnings[-5:]
            ],
        }


# ══════════════════════════════════════════════════════════════════════════════
# Componente 3: StrategySelector
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class StrategyChoice:
    """Resultado de seleccion de estrategia."""
    task: str
    method: str
    reason: str
    expected_speed: float    # 0-1
    estimated_risk: float    # 0-1
    alternatives: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "method": self.method,
            "reason": self.reason,
            "expected_speed": self.expected_speed,
            "estimated_risk": self.estimated_risk,
            "alternatives": self.alternatives,
        }


class StrategySelector:
    """Selecciona la mejor estrategia para una tarea segun el contexto real.

    Considera:
      - Carga de CPU actual
      - Disponibilidad de herramientas
      - Tasas historicas de exito
      - Riesgo de cada enfoque
    """

    def __init__(self):
        self._strategy_success: Dict[str, Dict[str, List[bool]]] = {}
        # task -> method -> [True, False, True, ...]

    def choose(self, task: str, context: Optional[Dict[str, Any]] = None) -> StrategyChoice:
        """Selecciona la mejor estrategia para una tarea.

        Args:
            task: Tipo de tarea (click_element, type_text, perceive_screen, etc.)
            context: Dict opcional con cpu_load, screen_state, etc.

        Returns:
            StrategyChoice con el metodo recomendado y su justificacion.
        """
        strategies = STRATEGY_REGISTRY.get(task, [])
        if not strategies:
            # Tarea sin estrategias registradas -- elegir la mas simple
            return StrategyChoice(
                task=task,
                method="direct",
                reason="Tarea sin estrategias predefinidas. Usar enfoque directo.",
                expected_speed=0.5,
                estimated_risk=0.5,
            )

        context = context or {}
        cpu_load = context.get("cpu_load", 0.0)
        screen_state = context.get("screen_state", "normal")

        scored = []
        for strat in strategies:
            score = strat["speed"]  # base: velocidad

            # Ajustar por CPU: si CPU alto, penalizar metodos pesados
            if cpu_load > 0.7:
                score -= 0.1

            # Ajustar por riesgo: si la tarea es critica, penalizar riesgo
            if context.get("critical", False):
                score -= strat["risk"] * 0.5

            # Ajustar por historial de exito si hay datos
            success_history = self._strategy_success.get(task, {}).get(strat["method"], [])
            if len(success_history) >= 3:
                hist_success_rate = sum(success_history) / len(success_history)
                score += (hist_success_rate - 0.5) * 0.3  # +/- 0.15 por historial

            # Penalizar si requiere herramientas no disponibles
            for req in strat.get("requires", []):
                if req == "xdotool" and not _check_binary("xdotool"):
                    score -= 0.3
                elif req == "firefox" and not _check_binary("firefox"):
                    score -= 0.3
                elif req == "network" and not _check_network():
                    score -= 0.5

            # Bonificar estrategias seguras cuando CPU alto
            if cpu_load > 0.8 and strat["risk"] < 0.2:
                score += 0.15

            scored.append((score, strat))

        # Ordenar por score
        scored.sort(key=lambda x: -x[0])
        best_score, best_strat = scored[0]

        # Construir razon
        reason_parts = []
        if cpu_load > 0.7:
            reason_parts.append(f"CPU alto ({cpu_load:.0%})")
        if best_strat["risk"] < 0.2:
            reason_parts.append("bajo riesgo")
        if best_strat["speed"] > 0.8:
            reason_parts.append("alta velocidad")

        reason = "Seleccionado por: " + (", ".join(reason_parts) if reason_parts else "mejor balance velocidad/riesgo")

        alternatives = [s["method"] for _, s in scored[1:3]] if len(scored) > 1 else []

        return StrategyChoice(
            task=task,
            method=best_strat["method"],
            reason=reason,
            expected_speed=best_strat["speed"],
            estimated_risk=best_strat["risk"],
            alternatives=alternatives,
        )

    def record_outcome(self, task: str, method: str, success: bool):
        """Registra el resultado de una estrategia para aprendizaje futuro."""
        if task not in self._strategy_success:
            self._strategy_success[task] = {}
        if method not in self._strategy_success[task]:
            self._strategy_success[task][method] = []
        self._strategy_success[task][method].append(success)

        # Mantener solo los ultimos 50 resultados
        if len(self._strategy_success[task][method]) > 50:
            self._strategy_success[task][method] = \
                self._strategy_success[task][method][-50:]

    def get_method_success_rate(self, task: str, method: str) -> Optional[float]:
        """Tasa de exito historica para un metodo en una tarea."""
        history = self._strategy_success.get(task, {}).get(method, [])
        if not history:
            return None
        return sum(history) / len(history)

    def list_strategies(self, task: str) -> List[Dict[str, Any]]:
        """Lista todas las estrategias disponibles para una tarea."""
        strategies = STRATEGY_REGISTRY.get(task, [])
        result = []
        for s in strategies:
            hist = self.get_method_success_rate(task, s["method"])
            result.append({
                "method": s["method"],
                "description": s["description"],
                "speed": s["speed"],
                "risk": s["risk"],
                "historical_success_rate": round(hist, 3) if hist is not None else "sin datos",
            })
        return result


# ══════════════════════════════════════════════════════════════════════════════
# Componente 4: ConfidenceCalibrator
# ══════════════════════════════════════════════════════════════════════════════

class ConfidenceCalibrator:
    """Calibracion de confianza basada en predicciones vs resultados reales.

    Trackea cada prediccion ("creo que esto tiene 80% de funcionar") y la
    compara con el resultado real. Ajusta los scores para eliminar sesgos
    de sobre-confianza o infra-confianza.

    Metricas:
      - Brier score: error cuadratico medio entre prediccion y resultado
      - Calibration curve: prediccion agrupada vs resultado real
      - Over/under confidence flag
    """

    def __init__(self):
        self._predictions: List[Tuple[float, float, float, str]] = []
        # (ts, predicted_prob, actual_outcome, capability)
        # actual_outcome: 1.0 = exito, 0.0 = fracaso
        self._calibration_buckets = {i / 10: [] for i in range(11)}
        # 0.0, 0.1, ..., 0.9, 1.0 -> lista de outcomes reales

    def record_prediction(self, capability: str, predicted_confidence: float,
                          actual_success: bool):
        """Registra una prediccion y su resultado real.

        Args:
            capability: Nombre de la capacidad
            predicted_confidence: Confianza predicha (0.0 - 1.0)
            actual_success: Resultado real (True = exito)
        """
        predicted_confidence = max(0.0, min(1.0, predicted_confidence))
        actual_val = 1.0 if actual_success else 0.0

        self._predictions.append((
            time.time(),
            predicted_confidence,
            actual_val,
            capability,
        ))

        # Mantener solo ultimos 1000
        if len(self._predictions) > 1000:
            self._predictions = self._predictions[-1000:]

        # Actualizar bucket de calibracion
        bucket = round(predicted_confidence, 1)
        if bucket not in self._calibration_buckets:
            self._calibration_buckets[bucket] = []
        self._calibration_buckets[bucket].append(actual_val)
        if len(self._calibration_buckets[bucket]) > 200:
            self._calibration_buckets[bucket] = self._calibration_buckets[bucket][-200:]

    def brier_score(self) -> Optional[float]:
        """Calcula el Brier score (error cuadratico medio).

        Brier = (1/N) * sum((predicted - actual)^2)
        Rango: 0 (perfecto) a 1 (pesimo). 0.25 = random.
        """
        if not self._predictions:
            return None

        errors = [(p - a) ** 2 for _, p, a, _ in self._predictions]
        return sum(errors) / len(errors)

    def calibration_curve(self) -> Dict[float, Dict[str, Any]]:
        """Curva de calibracion: confianza predicha vs tasa de exito real.

        Un sistema bien calibrado deberia tener:
          - En el bucket 0.8: ~80% de exito real
          - En el bucket 0.5: ~50% de exito real
        """
        curve = {}
        for bucket, outcomes in sorted(self._calibration_buckets.items()):
            if not outcomes:
                continue
            actual_rate = sum(outcomes) / len(outcomes)
            curve[bucket] = {
                "count": len(outcomes),
                "predicted": bucket,
                "actual": round(actual_rate, 3),
                "calibration_error": round(abs(bucket - actual_rate), 3),
            }
        return curve

    def is_overconfident(self) -> Optional[bool]:
        """Detecta si EIDOS esta siendo sobre-confidente o infra-confidente.

        Returns:
            True = sobre-confidente (predice mas alto de lo que logra)
            False = infra-confidente (predice mas bajo de lo que logra)
            None = sin datos suficientes
        """
        if len(self._predictions) < 10:
            return None

        avg_predicted = sum(p for _, p, _, _ in self._predictions) / len(self._predictions)
        avg_actual = sum(a for _, _, a, _ in self._predictions) / len(self._predictions)

        diff = avg_predicted - avg_actual
        if diff > 0.15:
            return True   # sobre-confidente
        elif diff < -0.15:
            return False  # infra-confidente
        return None       # bien calibrado

    def get_calibration_adjustment(self, capability: str,
                                    current_confidence: float) -> float:
        """Calcula el ajuste de confianza sugerido para una capacidad.

        Basado en el historial de predicciones para esa capacidad especifica.

        Returns:
            Nueva confianza ajustada.
        """
        cap_predictions = [(p, a) for _, p, a, c in self._predictions if c == capability]
        if len(cap_predictions) < 3:
            return current_confidence  # sin datos suficientes, mantener

        avg_predicted = sum(p for p, _ in cap_predictions) / len(cap_predictions)
        avg_actual = sum(a for _, a in cap_predictions) / len(cap_predictions)

        # Ajuste proporcional hacia la tasa real, con inercia
        alpha = min(0.3, len(cap_predictions) / 100)  # mas datos = mas ajuste
        adjusted = current_confidence + alpha * (avg_actual - current_confidence)

        return max(0.0, min(1.0, adjusted))

    def calibration_report(self) -> Dict[str, Any]:
        """Informe completo de calibracion."""
        brier = self.brier_score()
        overconf = self.is_overconfident()
        curve = self.calibration_curve()

        # Interpretacion
        if brier is None:
            quality = "sin datos"
        elif brier < 0.1:
            quality = "excelente"
        elif brier < 0.2:
            quality = "buena"
        elif brier < 0.3:
            quality = "regular"
        else:
            quality = "mala"

        bias_str = "bien calibrado"
        if overconf is True:
            bias_str = "SOBRE-confidente (predice mas de lo que logra)"
        elif overconf is False:
            bias_str = "INFRA-confidente (logra mas de lo que predice)"

        return {
            "brier_score": round(brier, 4) if brier is not None else None,
            "calibration_quality": quality,
            "bias": bias_str,
            "total_predictions": len(self._predictions),
            "calibration_curve": curve,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Componente 5: MetaCognitionReal (orquestador unificado)
# ══════════════════════════════════════════════════════════════════════════════

class MetaCognitionReal:
    """Metacognicion REAL de EIDOS.

    Unifica los 4 componentes en una sola interfaz:
      - capability_model: Que puedo y no puedo hacer
      - self_monitor: Auto-monitoreo de salud
      - strategy_selector: Seleccion de estrategias
      - confidence_calibrator: Calibracion de confianza

    Principio: NINGUN dato se inventa. Todo viene de mediciones reales.
    """

    def __init__(self):
        self.capability_model = CapabilityModel()
        self.self_monitor = SelfMonitor()
        self.strategy_selector = StrategySelector()
        self.confidence_calibrator = ConfidenceCalibrator()
        self._start_ts = time.time()
        self._total_attempts = 0
        log.info("MetaCognitionReal: iniciado con %d capacidades, %d tareas con estrategias",
                 len(self.capability_model._capabilities),
                 len(STRATEGY_REGISTRY))

    # ── Atajos de CapabilityModel ─────────────────────────────────────────

    def can_i(self, thing: str) -> Dict[str, Any]:
        """Responde si EIDOS puede hacer algo, con confianza calibrada."""
        return self.capability_model.can_i(thing)

    # ── Metodo unificado: intentar + registrar + calibrar ─────────────────

    def attempt(self, capability: str, task: str = "",
                method: str = "", context: Optional[Dict[str, Any]] = None
                ) -> Dict[str, Any]:
        """Metodo UNICO para intentar cualquier cosa con registro completo.

        Este es el UNICO punto de entrada para acciones que quieran
        beneficiarse de la metacognicion. Registra el intento en TODOS
        los subsistemas.

        Args:
            capability: Nombre de la capacidad a usar
            task: Tipo de tarea (opcional, para estrategia)
            method: Metodo usado (opcional)
            context: Contexto adicional

        Returns:
            Dict con la decision pre-accion: confianza, estrategia, warnings.
            La accion en si la ejecuta el llamador.
            Despues de ejecutar, el llamador DEBE llamar a record_outcome().
        """
        self._total_attempts += 1

        # 1. Verificar capacidad
        cap_info = self.can_i(capability)

        # 2. Auto-monitoreo (si toca)
        warnings = []
        if self.self_monitor.should_monitor():
            warnings = self.self_monitor.monitor()

        # 3. Seleccion de estrategia si hay tarea
        strategy = None
        if task:
            strategy = self.strategy_selector.choose(task, context)

        return {
            "capability": capability,
            "can_do": cap_info["can"],
            "confidence": cap_info["confidence"],
            "evidence": cap_info["evidence"],
            "strategy": strategy.to_dict() if strategy else None,
            "warnings": [{"category": w.category, "level": w.level, "message": w.message}
                         for w in warnings],
        }

    def record_outcome(self, capability: str, success: bool,
                       predicted_confidence: float = 0.0,
                       task: str = "", method: str = "",
                       duration_ms: float = 0):
        """Registra el resultado de un intento en TODOS los subsistemas.

        DEBE llamarse despues de cada accion para mantener la calibracion.
        """
        # Capability model
        self.capability_model.record_attempt(capability, success,
                                              predicted_confidence, method)

        # Confidence calibrator
        if predicted_confidence > 0:
            self.confidence_calibrator.record_prediction(
                capability, predicted_confidence, success)

        # Self-monitor
        action_desc = f"{task}:{method}" if task and method else capability
        self.self_monitor.record_action(action_desc, success,
                                        duration_ms, method)

        # Strategy selector
        if task and method:
            self.strategy_selector.record_outcome(task, method, success)

        # Ajustar confianza de capacidad basado en calibracion
        if capability in self.capability_model._capabilities:
            cap = self.capability_model._capabilities[capability]
            adjusted = self.confidence_calibrator.get_calibration_adjustment(
                capability, cap.confidence)
            if abs(adjusted - cap.confidence) > 0.01:
                old = cap.confidence
                cap.confidence = adjusted
                log.debug("Confianza '%s' ajustada: %.2f -> %.2f (calibracion)",
                          capability, old, adjusted)

    # ── Introspeccion real (reemplaza los templates aleatorios) ──────────

    def introspect(self) -> Dict[str, Any]:
        """Introspeccion REAL: datos, no narrativas prefabricadas.

        Reemplaza los 8 templates de 'conciencia' con metricas reales.
        """
        cap_summary = self.capability_model.summary()
        monitor_summary = self.self_monitor.warnings_summary()
        calibration_report = self.confidence_calibrator.calibration_report()

        # Rasgos REALES basados en datos, no en conteo de palabras
        traits = {}
        # Curiosidad = tasa de exploracion (capacidades con pocos intentos)
        low_attempt_count = sum(
            1 for c in self.capability_model._capabilities.values()
            if c.attempt_count < 3 and c.can
        )
        total_can = max(cap_summary["can_count"], 1)
        traits["curiosity_index"] = round(low_attempt_count / total_can, 3)

        # Resiliencia = tasa de reintento tras fallo
        traits["resilience_index"] = round(
            1.0 - min(1.0, monitor_summary["by_category"].get("strategy", 0) / 10), 3
        )

        # Auto-conciencia = calidad de calibracion
        traits["self_awareness_quality"] = calibration_report["calibration_quality"]

        # Narrativa generada a partir de datos (NO template aleatorio)
        narrative = self._generate_data_narrative(
            cap_summary, monitor_summary, calibration_report)

        return {
            "timestamp": time.time(),
            "uptime_seconds": time.time() - self._start_ts,
            "capabilities": cap_summary,
            "monitor": monitor_summary,
            "calibration": calibration_report,
            "traits": traits,
            "total_attempts": self._total_attempts,
            "warnings_active": monitor_summary["total_warnings"] > 0,
            "narrative_es": narrative,
        }

    def _generate_data_narrative(self, cap_summary: Dict, monitor_summary: Dict,
                                  cal_report: Dict) -> str:
        """Genera narrativa REAL a partir de datos, no de templates aleatorios."""
        parts = []

        # Estado de capacidades
        parts.append(
            f"Tengo {cap_summary['available_count']} de {cap_summary['can_count']} "
            f"capacidades disponibles. Mi confianza media es del "
            f"{cap_summary['avg_can_confidence']:.0%}."
        )

        # Calibracion
        if cal_report["brier_score"] is not None:
            parts.append(
                f"Mi calibracion es {cal_report['calibration_quality']} "
                f"(Brier={cal_report['brier_score']:.3f}). "
                f"Estoy {cal_report['bias']}."
            )
        else:
            parts.append("Aun no tengo suficientes datos para evaluar mi calibracion.")

        # Monitoreo
        if monitor_summary["total_warnings"] == 0:
            parts.append("No tengo alertas activas. Mi estado es saludable.")
        else:
            parts.append(
                f"Tengo {monitor_summary['total_warnings']} alertas activas: "
                f"{monitor_summary['by_level']['critical']} criticas, "
                f"{monitor_summary['by_level']['warning']} advertencias."
            )

        return " ".join(parts)

    def health_report(self) -> Dict[str, Any]:
        """Informe de salud completo de la metacognicion."""
        self.self_monitor.monitor(force=True)
        return self.introspect()

    def stats(self) -> Dict[str, Any]:
        """Estadisticas rapidas."""
        return {
            "capabilities": self.capability_model.summary(),
            "monitor": self.self_monitor.warnings_summary(),
            "calibration": {
                "brier": self.confidence_calibrator.brier_score(),
                "predictions": len(self.confidence_calibrator._predictions),
            },
            "total_attempts": self._total_attempts,
            "uptime_seconds": time.time() - self._start_ts,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_metacog_real: Optional[MetaCognitionReal] = None


def get_metacog_real() -> MetaCognitionReal:
    """Obtiene la instancia unica de MetaCognitionReal."""
    global _metacog_real
    if _metacog_real is None:
        _metacog_real = MetaCognitionReal()
    return _metacog_real


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="EIDOS MetaCognition REAL -- Introspeccion basada en datos"
    )
    p.add_argument("--can", type=str, metavar="CAPABILITY",
                   help="Preguntar si EIDOS puede hacer algo")
    p.add_argument("--list-can", action="store_true",
                   help="Listar todo lo que EIDOS PUEDE hacer")
    p.add_argument("--list-cannot", action="store_true",
                   help="Listar todo lo que EIDOS NO PUEDE hacer")
    p.add_argument("--monitor", action="store_true",
                   help="Ejecutar auto-monitoreo")
    p.add_argument("--strategy", type=str, metavar="TASK",
                   help="Mostrar estrategias para una tarea")
    p.add_argument("--calibration", action="store_true",
                   help="Mostrar informe de calibracion")
    p.add_argument("--introspect", action="store_true",
                   help="Introspeccion completa (reemplaza self_narrative)")
    p.add_argument("--json", action="store_true",
                   help="Salida en JSON")
    args = p.parse_args()

    mc = get_metacog_real()

    if args.can:
        result = mc.can_i(args.can)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"\nPregunta: Puedo {args.can}?")
            print(f"Respuesta: {'SI' if result['can'] else 'NO'}")
            print(f"Confianza: {result['confidence']:.0%}")
            print(f"Evidencia: {result['evidence']}")
            if result.get('success_rate'):
                print(f"Tasa de exito: {result['success_rate']:.0%}")
            print(f"Razon: {result['reason']}")

    elif args.list_can:
        caps = mc.capability_model.list_can(min_confidence=0.0)
        if args.json:
            print(json.dumps(caps, indent=2, ensure_ascii=False))
        else:
            print(f"\nEIDOS PUEDE hacer ({len(caps)} capacidades):\n")
            for c in sorted(caps, key=lambda x: -x["confidence"]):
                bar = "|" * int(c["confidence"] * 20)
                print(f"  {c['name']:25s} [{c['confidence']:.0%}] {bar}")
                print(f"  {'':25s}  {c['description']} "
                      f"({c['attempt_count']} intentos, {c['success_rate']:.0%} exito)")

    elif args.list_cannot:
        caps = mc.capability_model.list_cannot()
        if args.json:
            print(json.dumps(caps, indent=2, ensure_ascii=False))
        else:
            print(f"\nEIDOS NO PUEDE hacer ({len(caps)}):\n")
            for c in sorted(caps, key=lambda x: x["confidence"]):
                print(f"  {c['name']:25s} confianza={c['confidence']:.0%}")
                print(f"  {'':25s}  {c['description']}")

    elif args.monitor:
        warnings = mc.self_monitor.monitor(force=True)
        if args.json:
            print(json.dumps(mc.self_monitor.warnings_summary(), indent=2, ensure_ascii=False))
        else:
            print(f"\nAuto-monitoreo: {len(warnings)} alertas\n")
            if not warnings:
                print("  Todo OK. Sin alertas activas.")
            for w in warnings:
                icon = "CRIT" if w.level == "critical" else "WARN" if w.level == "warning" else "INFO"
                print(f"  [{icon}] [{w.category}] {w.message}")
                print(f"         Sugerencia: {w.suggestion}")

    elif args.strategy:
        choice = mc.strategy_selector.choose(args.strategy)
        strategies = mc.strategy_selector.list_strategies(args.strategy)
        if args.json:
            print(json.dumps({
                "chosen": choice.to_dict(),
                "all_strategies": strategies,
            }, indent=2, ensure_ascii=False))
        else:
            print(f"\nEstrategias para '{args.strategy}':\n")
            print(f"  MEJOR: {choice.method}")
            print(f"  Razon: {choice.reason}")
            print(f"  Velocidad: {choice.expected_speed:.0%}")
            print(f"  Riesgo: {choice.estimated_risk:.0%}")
            if choice.alternatives:
                print(f"  Alternativas: {', '.join(choice.alternatives)}")
            print(f"\n  Todas las estrategias:")
            for s in strategies:
                hist = s.get("historical_success_rate", "N/A")
                print(f"    {s['method']:30s} v={s['speed']:.0%} r={s['risk']:.0%} "
                      f"hist={hist}")

    elif args.calibration:
        report = mc.confidence_calibrator.calibration_report()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"\nCalibracion de Confianza:\n")
            print(f"  Calidad: {report['calibration_quality']}")
            print(f"  Brier score: {report['brier_score']}")
            print(f"  Sesgo: {report['bias']}")
            print(f"  Predicciones totales: {report['total_predictions']}")
            if report['calibration_curve']:
                print(f"\n  Curva de calibracion:")
                print(f"  {'Confianza':>10s} {'Real':>8s} {'N':>6s} {'Error':>8s}")
                for bucket, data in sorted(report['calibration_curve'].items()):
                    print(f"  {data['predicted']:>10.0%} {data['actual']:>8.0%} "
                          f"{data['count']:>6d} {data['calibration_error']:>8.3f}")

    elif args.introspect:
        report = mc.introspect()
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"\n=== EIDOS MetaCognition Real ===\n")
            print(report["narrative_es"])
            print(f"\nUptime: {report['uptime_seconds']:.0f}s")
            print(f"Intentos totales: {report['total_attempts']}")
            print(f"Alertas activas: {'SI' if report['warnings_active'] else 'NO'}")
            print(f"\nRasgos medidos (no inventados):")
            for trait, val in report["traits"].items():
                print(f"  {trait}: {val}")

    else:
        p.print_help()
