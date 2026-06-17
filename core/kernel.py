"""
EIDOS core/kernel.py — Deterministic Interaction Kernel
========================================================
Implementa el núcleo determinista según el Oráculo 2 (KaliGPT):

  "EIDOS no debe ser más inteligente. Debe ser más estable.
   Si construyes estabilidad primero, la inteligencia emerge de la estructura."

Fase 0 del Roadmap:
  [OK] Tool schema validation (pydantic)
  [OK] Tool middleware guard (safe_call con timeout y error handling)
  [OK] Reflect injection automático post-tool-call
  [OK] MAX_ITERATIONS global
  [OK] TIMEOUT global por tool call
  [OK] StateComparator (hash imagen + pixel diff + abort counter)
  [OK] Loop determinista: PLAN -> EXECUTE -> REFLECT -> VERIFY -> DONE

Uso:
    from core.kernel import DeterministicKernel
    kernel = DeterministicKernel(soul=SOUL_TEXT)
    result = kernel.run("abre el navegador y ve a google.com")
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.request
from typing import Any

# -- EIDOS Optimizations (Auto-Corrector, RAM Guardian, Smart Cache) ----
try:
    from core.auto_corrector import auto_corrector
    HAS_AUTO_CORRECTOR = True
except ImportError:
    HAS_AUTO_CORRECTOR = False

try:
    from core.ram_guardian import ram_guardian
    HAS_RAM_GUARDIAN = True
except ImportError:
    HAS_RAM_GUARDIAN = False

try:
    from core.smart_cache import smart_cache
    HAS_SMART_CACHE = True
except ImportError:
    HAS_SMART_CACHE = False

# -- EIDOS Advanced Systems (Advanced Planner, Trainer) ---------------------
try:
    from core.advanced_planner import get_advanced_planner
    HAS_ADVANCED_PLANNER = True
except ImportError:
    HAS_ADVANCED_PLANNER = False

try:
    from core.trainer import get_trainer
    HAS_TRAINER = True
except ImportError:
    HAS_TRAINER = False

# -- Configuración Global del Kernel -----------------------------------------
OLLAMA_URL     = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

# -- Constantes de Seguridad (Oráculo 2: "Hard safety guards") ---------------
MAX_ITERATIONS      = 10     # Máximo de ciclos tool-call antes de rendirse
TOOL_TIMEOUT_S      = 90     # Timeout por defecto para cada tool call en segundos (hermes3 puede tardar 60-90s en RAM baja)
SCREENSHOT_ABORT    = 3      # Abortar si N screenshots consecutivos sin cambio
PIXEL_DIFF_THRESH   = 0.02   # 2% de diferencia de píxeles = cambio significativo

# -- Integración F29: ModelManager + ToolGuard ---------------------------------
try:
    from core.model_manager import get_model_manager, ModelManager, TaskType
    _mm = get_model_manager()
    # Modelos seleccionados dinámicamente según RAM disponible
    TOOL_MODEL    = _mm.select(TaskType.TOOL_CALL)
    FAST_MODEL    = _mm.select(TaskType.PLAN)
    VISION_MODEL  = _mm.select(TaskType.VISION_FAST)
    DEEP_VISION   = _mm.select(TaskType.VISION_DEEP)
    HAS_MODEL_MGR = True
except ImportError:
    TOOL_MODEL    = "deepseek-r1:14b"
    FAST_MODEL    = "lfm2.5-thinking:1.2b"
    VISION_MODEL  = "moondream:latest"
    DEEP_VISION   = "moondream:latest"
    HAS_MODEL_MGR = False

try:
    from core.tool_guard import get_tool_guard, ToolGuard
    HAS_TOOL_GUARD = True
except ImportError:
    HAS_TOOL_GUARD = False

try:
    from core.eidos_shield import get_shield as _get_shield
    _shield = _get_shield("permissive")
    HAS_SHIELD = True
except ImportError:
    HAS_SHIELD = False

try:
    from core.skill_evolver import get_skill_evolver as _get_evolver
    _evolver = _get_evolver()
    HAS_EVOLVER = True
except ImportError:
    HAS_EVOLVER = False

try:
    from core.ui_parser import get_ui_parser as _get_ui_parser
    HAS_UI_PARSER = True
except ImportError:
    HAS_UI_PARSER = False

try:
    from core.meta_learner import get_meta_learner as _get_meta_learner
    _meta = _get_meta_learner()
    HAS_META_LEARNER = True
except ImportError:
    HAS_META_LEARNER = False

try:
    from core.routines import get_routine_manager as _get_routine_manager
    _routines = _get_routine_manager()
    HAS_ROUTINES = True
except ImportError:
    HAS_ROUTINES = False

# -- Session 10: Integration of 14 Claw-derived modules -----------------------
try:
    from core.smart_router import SmartRouter
    _smart_router = SmartRouter()
    HAS_SMART_ROUTER = True
except ImportError:
    HAS_SMART_ROUTER = False

try:
    from core.compression_7layer import SevenLayerCompressor
    _compressor = SevenLayerCompressor()
    HAS_COMPRESSOR = True
except ImportError:
    HAS_COMPRESSOR = False

try:
    from core.self_healing import get_self_healer
    _healer = get_self_healer()
    HAS_SELF_HEALER = True
except ImportError:
    HAS_SELF_HEALER = False

try:
    from core.dynamic_tools import DynamicToolBuilder
    _dynamic_tools = DynamicToolBuilder()
    HAS_DYNAMIC_TOOLS = True
except ImportError:
    HAS_DYNAMIC_TOOLS = False

try:
    from core.wasm_sandbox import get_sandbox as _get_sandbox
    _sandbox = _get_sandbox()
    HAS_WASM_SANDBOX = True
except ImportError:
    HAS_WASM_SANDBOX = False

try:
    from core.memory_rrf import HybridMemoryRRF
    _memory_rrf = HybridMemoryRRF()
    HAS_MEMORY_RRF = True
except ImportError:
    HAS_MEMORY_RRF = False

try:
    from core.memory_decay import get_decaying_memory
    _memory_decay = get_decaying_memory()
    HAS_MEMORY_DECAY = True
except ImportError:
    HAS_MEMORY_DECAY = False

try:
    from core.knowledge_graph import get_knowledge_graph
    _kg = get_knowledge_graph()
    HAS_KNOWLEDGE_GRAPH = True
except ImportError:
    HAS_KNOWLEDGE_GRAPH = False

try:
    from core.token_economy import get_economy
    _economy = get_economy()
    HAS_TOKEN_ECONOMY = True
except ImportError:
    HAS_TOKEN_ECONOMY = False

try:
    from core.ganglia import get_ganglia_manager
    _ganglia = get_ganglia_manager()
    HAS_GANGLIA = True
except ImportError:
    HAS_GANGLIA = False

try:
    from core.governance import GovernanceSystem
    _governance = GovernanceSystem()
    HAS_GOVERNANCE = True
except ImportError:
    HAS_GOVERNANCE = False

try:
    from core.rl_madmax import MadMaxLearner
    _madmax = MadMaxLearner()
    HAS_MADMAX = True
except ImportError:
    HAS_MADMAX = False

try:
    from core.mcp_protocol import get_mcp_server
    _mcp_server = get_mcp_server()
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

try:
    from core.google_oauth_proxy import GoogleOAuthProxy
    _oauth_proxy = GoogleOAuthProxy()
    HAS_OAUTH_PROXY = True
except ImportError:
    HAS_OAUTH_PROXY = False

try:
    from core.vision_learner_bridge import get_vision_learner_bridge
    HAS_VISION_LEARNER = True
except ImportError:
    HAS_VISION_LEARNER = False

try:
    from core.rust_bridge import get_rust_knowledge_db, get_auto_learn_observer, RUST_AVAILABLE
    HAS_RUST_BRIDGE = RUST_AVAILABLE
except ImportError:
    HAS_RUST_BRIDGE = False

# -- Session 12: ColonyQueryEngine + ExtensionIntelligence + IPC Bridge ------
try:
    from core.colony_query_engine import get_colony_engine
    HAS_COLONY_ENGINE = True
except ImportError:
    HAS_COLONY_ENGINE = False

try:
    from core.extension_intelligence import get_extension_intelligence
    HAS_EXTENSION_INTEL = True
except ImportError:
    HAS_EXTENSION_INTEL = False

try:
    from core.ipc_bridge import get_ipc_bridge
    HAS_IPC_BRIDGE = True
except ImportError:
    HAS_IPC_BRIDGE = False

try:
    from core.wake_word import get_wake_listener
    HAS_WAKE_WORD = True
except ImportError:
    HAS_WAKE_WORD = False

SS_DIR = os.path.expanduser("~/.eidos/screenshots")
os.makedirs(SS_DIR, exist_ok=True)


# ==============================================================================
#  PHASE 0.1 — Tool Schema Validation (pydantic si disponible, fallback manual)
# ==============================================================================

# SCHEMAS: define los parámetros requeridos/opcionales de cada tool.
# Esto evita que Hermes3 genere tool calls con parámetros inventados.
TOOL_SCHEMAS: dict[str, dict] = {
    "exec_shell": {
        "required": ["command"],
        "optional": ["timeout"],
        "types":    {"command": str, "timeout": int},
    },
    "take_screenshot": {
        "required": [],
        "optional": ["question"],
        "types":    {"question": str},
    },
    "read_file": {
        "required": ["path"],
        "optional": [],
        "types":    {"path": str},
    },
    "write_file": {
        "required": ["path", "content"],
        "optional": [],
        "types":    {"path": str, "content": str},
    },
    "install_package": {
        "required": ["package"],
        "optional": ["method"],
        "types":    {"package": str, "method": str},
    },
    "call_skill": {
        "required": ["skill", "action"],
        "optional": ["kwargs"],
        "types":    {"skill": str, "action": str, "kwargs": str},
    },
    "mouse_click": {
        "required": ["x", "y"],
        "optional": ["button", "clicks"],
        "types":    {"x": int, "y": int, "button": str, "clicks": int},
    },
    "keyboard_type": {
        "required": ["text"],
        "optional": ["interval"],
        "types":    {"text": str, "interval": float},
    },
    "keyboard_hotkey": {
        "required": ["keys"],
        "optional": [],
        "types":    {"keys": list},
    },
    "dom_click": {
        "required": ["url", "selector"],
        "optional": [],
        "types":    {"url": str, "selector": str},
    },
    "dom_type": {
        "required": ["url", "selector", "text"],
        "optional": [],
        "types":    {"url": str, "selector": str, "text": str},
    },
    "dom_get_text": {
        "required": ["url"],
        "optional": [],
        "types":    {"url": str},
    },
    # -- F28/29: Kali Tools ----------------------------------------------------
    "kali_tool_info": {
        "required": ["tool_name"],
        "optional": [],
        "types":    {"tool_name": str},
    },
    "kali_search_tools": {
        "required": ["query"],
        "optional": ["category"],
        "types":    {"query": str, "category": str},
    },
    "observe_screen": {
        "required": [],
        "optional": ["question", "deep"],
        "types":    {"question": str, "deep": bool},
    },
    "click_gui_element": {
        "required": [],
        "optional": ["text", "x", "y"],
        "types":    {"text": str, "x": int, "y": int},
    },
    "scroll_gui": {
        "required": [],
        "optional": ["direction", "amount"],
        "types":    {"direction": str, "amount": int},
    },
    "type_in_gui": {
        "required": ["text"],
        "optional": ["enter"],
        "types":    {"text": str, "enter": bool},
    },
    "get_page_dom": {
        "required": [],
        "optional": ["url"],
        "types":    {"url": str},
    },
    "navigate_browser": {
        "required": [],
        "optional": ["url", "action"],
        "types":    {"url": str, "action": str},
    },
    "scroll_analyze_page": {
        "required": [],
        "optional": ["steps"],
        "types":    {"steps": int},
    },
    "deep_crawl_url": {
        "required": ["url"],
        "optional": ["max_pages", "visual"],
        "types":    {"url": str, "max_pages": int, "visual": bool},
    },
    "get_resource_status": {
        "required": [],
        "optional": [],
        "types":    {},
    },
    "parse_ui": {
        "required": [],
        "optional": ["image_path", "use_vlm", "min_confidence"],
        "types":    {"image_path": str, "use_vlm": bool, "min_confidence": float},
    },
    "click_ui_element": {
        "required": ["ref"],
        "optional": [],
        "types":    {"ref": str},
    },
    "find_ui_element": {
        "required": ["text"],
        "optional": ["element_type"],
        "types":    {"text": str, "element_type": str},
    },
    # ── Fase 35 — Memory Tools ───────────────────────────────────────────────
    "record_exchange": {
        "required": ["user", "reply"],
        "optional": ["tools", "tags"],
        "types":    {"user": str, "reply": str},
    },
    "memory_recall": {
        "required": ["query"],
        "optional": ["n", "days"],
        "types":    {"query": str, "n": int, "days": int},
    },
    "notify_ser": {
        "required": ["message"],
        "optional": ["title", "urgency", "timeout", "icon"],
        "types":    {"message": str, "title": str, "urgency": str},
    },
    "schedule_task": {
        "required": ["command", "description"],
        "optional": ["delay_min", "run_at", "repeat"],
        "types":    {"command": str, "description": str, "delay_min": int},
    },
    "list_scheduled_tasks": {
        "required": [],
        "optional": [],
        "types":    {},
    },
}


class ToolCallValidationError(Exception):
    """Se lanza cuando una tool call no pasa la validación de schema."""
    pass


def validate_tool_call(name: str, args: dict) -> tuple[bool, str]:
    """
    Valida una tool call contra el schema definido.
    
    Returns:
        (True, "") si válida.
        (False, error_msg) si inválida.
    """
    if name not in TOOL_SCHEMAS:
        return False, f"[KERNEL] Tool desconocida: '{name}'. Tools válidas: {list(TOOL_SCHEMAS.keys())}"
    
    schema = TOOL_SCHEMAS[name]
    
    # Verificar parámetros requeridos
    for req_param in schema["required"]:
        if req_param not in args:
            return False, (
                f"[KERNEL] Tool '{name}' requiere el parámetro '{req_param}'. "
                f"Args recibidos: {list(args.keys())}"
            )
    
    # Verificar tipos
    for param, expected_type in schema["types"].items():
        if param in args:
            val = args[param]
            if not isinstance(val, expected_type):
                # Intentar coerción suave
                try:
                    args[param] = expected_type(val)
                except (ValueError, TypeError):
                    return False, (
                        f"[KERNEL] '{name}.{param}' debe ser {expected_type.__name__}, "
                        f"pero recibí {type(val).__name__}: {val!r}"
                    )
    
    return True, ""


# ==============================================================================
#  PHASE 0.2 — StateComparator (detector de loops ciegos)
# ==============================================================================

class StateComparator:
    """
    Detecta si el estado de la pantalla ha cambiado entre iteraciones.
    Si N capturas consecutivas tienen el mismo hash -> el agente está en un loop ciego.
    
    Solución del Oráculo 2: "Detector de estado inmutable. Abort si no hay cambio."
    """
    
    def __init__(self, abort_threshold: int = SCREENSHOT_ABORT) -> None:
        self.abort_threshold = abort_threshold
        self._last_hash: str | None = None
        self._unchanged_count: int = 0
    
    def _hash_image(self, path: str) -> str:
        """Hash MD5 de la imagen para comparación rápida."""
        try:
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return f"NOHASH_{time.time()}"  # Siempre diferente si no podemos leer
    
    def check(self, screenshot_path: str) -> tuple[bool, str]:
        """
        Verifica si el estado de la pantalla cambió.
        
        Returns:
            (should_abort, reason)
            - (True, msg)  -> Abortar: pantalla no ha cambiado N veces
            - (False, "")  -> Continuar: hay cambio o primer check
        """
        current_hash = self._hash_image(screenshot_path)
        
        if self._last_hash is None:
            # Primera captura
            self._last_hash = current_hash
            self._unchanged_count = 0
            return False, ""
        
        if current_hash == self._last_hash:
            self._unchanged_count += 1
            if self._unchanged_count >= self.abort_threshold:
                return True, (
                    f"[KERNEL] LOOP CIEGO DETECTADO: La pantalla no ha cambiado en "
                    f"{self._unchanged_count} iteraciones consecutivas. "
                    f"Abortando para evitar loop infinito. Requiere intervención humana."
                )
            return False, f"[KERNEL] Advertencia: Sin cambio en pantalla ({self._unchanged_count}/{self.abort_threshold})"
        else:
            # Hubo cambio -> resetear contador
            self._last_hash = current_hash
            self._unchanged_count = 0
            return False, ""
    
    def reset(self) -> None:
        """Resetear el comparador para una nueva secuencia de acciones."""
        self._last_hash = None
        self._unchanged_count = 0


# ==============================================================================
#  PHASE 0.3 — Safe Call Middleware (tool executor con guards)
# ==============================================================================

def safe_call(tool_name: str, tool_args: dict,
              tool_impl: dict[str, Any],
              state_comparator: StateComparator | None = None,
              timeout: int = TOOL_TIMEOUT_S,
              current_state_hash: str = "",
              browser_active: bool = False) -> str:
    """
    Ejecuta una tool call con:
    1. ToolGuard.validate() — governance middleware (F29)
    2. Validación de schema (pydantic-like)
    3. Timeout aplicado
    4. Error handling completo (nunca lanza excepciones al agente)
    5. StateComparator para take_screenshot
    6. ToolGuard.record() — registra resultado + state hash
    7. Logging a color en CLI

    Oráculo 2: "Tool middleware guard. Nada actúa sin verificación."
    """
    # 0. ToolGuard — governance middleware (Hard Guards F29)
    if HAS_TOOL_GUARD:
        guard = get_tool_guard()
        allowed, reason = guard.validate(
            tool_name, tool_args,
            current_state_hash=current_state_hash,
            browser_active=browser_active
        )
        if not allowed:
            print(f"\033[91m[[GUARD]️  GUARD BLOCK]\033[0m {reason}")
            return f"[TOOL_GUARD] {reason}"

    # 0.5. EidosShield — security layer (leak/injection/exec/risk)
    if HAS_SHIELD:
        # Scan exec_shell commands
        if tool_name == "exec_shell" and "command" in tool_args:
            cmd_ok, cmd_reason = _shield.check_command(tool_args["command"])
            if not cmd_ok:
                print(f"\033[91m[🛡️  SHIELD BLOCK]\033[0m {cmd_reason}")
                return f"[SHIELD] {cmd_reason}"
        # Scan text-based args for leaks/injection
        for arg_val in tool_args.values():
            if isinstance(arg_val, str) and len(arg_val) > 10:
                scan = _shield.scan_text(arg_val)
                if scan.blocked:
                    print(f"\033[91m[🛡️  SHIELD BLOCK]\033[0m {scan.reason}")
                    return f"[SHIELD] {scan.reason}"
        # Risk scoring
        risk = _shield.score_tool(tool_name)
        if risk.level.value == "high":
            print(f"\033[93m[🛡️  SHIELD]\033[0m {tool_name} risk={risk.vr} (HIGH) — proceeding with caution")

    # 1. Validar schema
    valid, error_msg = validate_tool_call(tool_name, tool_args)
    if not valid:
        print(f"\033[91m[[BLOCK] KERNEL REJECT]\033[0m {error_msg}")
        return error_msg
    
    # 2. Ejecutar con timeout implícito (la implementación real ya tiene timeouts)
    print(f"\033[94m[[TOOL] KERNEL]\033[0m Ejecutando: {tool_name}({dict(list(tool_args.items())[:3])})")  # pyre-ignore[arg-type]
    
    impl = tool_impl.get(tool_name)
    if impl is None:
        return f"[KERNEL] No hay implementación para '{tool_name}'"
    
    start_time = time.time()
    try:
        result = str(impl(tool_args))
        elapsed = round(time.time() - start_time, 1)
        print(f"\033[90m  [TIME] {elapsed}s -> {result[:120]}\033[0m")  # pyre-ignore[arg-type]
    except Exception as e:
        elapsed = round(time.time() - start_time, 1)
        error_msg = str(e)
        print(f"\033[91m[SAFE_CALL ERROR] {tool_name} falló: {error_msg}\033[0m")

        # -- Auto-Corrector: Intento de auto-reparación inteligente --
        if HAS_AUTO_CORRECTOR and tool_name == "exec_shell" and "command" in tool_args:
            # Evitar bucles: si el comando ya fue fixado, no volver a intentar
            if not tool_args.get("_autocorrect_retry"):
                try:
                    print(f"\033[95m[AUTO-CORRECTOR] Analizando fallo para auto-reparación...\033[0m")

                    # Usar Auto-Corrector para analizar y corregir
                    code_to_fix = f"""
import subprocess
result = subprocess.run({repr(tool_args['command'])}, shell=True, capture_output=True, text=True, timeout=30)
print(result.stdout if result.returncode == 0 else result.stderr)
"""
                    from core.auto_corrector import auto_corrector
                    fix_result = auto_corrector.safe_exec(code_to_fix, auto_fix=True, max_retries=2)

                    if fix_result.success:
                        print(f"\033[92m[AUTO-CORRECTOR] ✅ Comando reparado y ejecutado exitosamente\033[0m")
                        return f"[AUTO-CORRECTED] {fix_result.output}"
                    else:
                        print(f"\033[93m[AUTO-CORRECTOR] ⚠️  No se pudo auto-reparar: {fix_result.error}\033[0m")

                except Exception as auto_e:
                    print(f"\033[91m[AUTO-CORRECTOR ERROR] {auto_e}\033[0m")

        result = f"[SAFE_CALL ERROR] {tool_name} falló después de {elapsed}s: {e}"

        # Registrar fallo en SkillEvolver para evolución de skills
        if HAS_EVOLVER:
            _evolver.record_failure(tool_name, str(e)[:200],
                                   context=str(tool_args)[:100])

        # -- Self-Healer: Diagnóstico + auto-reparación avanzada --
        if HAS_SELF_HEALER:
            try:
                diag = _healer.diagnose(error_msg, context={"tool": tool_name, "args": str(tool_args)[:200]})
                if diag and diag.get("fixable"):
                    heal_result = _healer.heal(diag)
                    if heal_result and heal_result.get("success"):
                        print(f"\033[92m[SELF-HEALER] Reparado: {heal_result.get('fix', '')[:80]}\033[0m")
            except Exception:
                pass  # error no crítico, continuar
        # -- Dynamic Tools: Genera tool desde error recurrente --
        if HAS_DYNAMIC_TOOLS:
            try:
                _dynamic_tools.from_error(tool_name, error_msg, tool_args)
            except Exception:
                pass  # error no crítico, continuar
    # 3. Si fue un screenshot, verificar con StateComparator
    if tool_name == "take_screenshot" and state_comparator is not None:
        ss_files = sorted([f for f in os.listdir(SS_DIR) if f.endswith(".png")])
        if ss_files:
            latest_ss = os.path.join(SS_DIR, ss_files[-1])
            should_abort, abort_msg = state_comparator.check(latest_ss)
            if should_abort:
                print(f"\033[91m{abort_msg}\033[0m")
                if HAS_TOOL_GUARD:
                    try:
                        get_tool_guard().record(tool_name, {}, abort_msg, new_state_hash=current_state_hash)
                    except Exception:
                        pass  # error no crítico, continuar
                return abort_msg
            elif abort_msg:
                print(f"\033[93m{abort_msg}\033[0m")

    # 4. ToolGuard.record() — registra resultado (F29 gobernanza: validate -> ejecutar -> record)
    if HAS_TOOL_GUARD:
        try:
            new_hash = current_state_hash
            if tool_name == "take_screenshot":
                ss_files2 = sorted([f for f in os.listdir(SS_DIR) if f.endswith(".png")])
                if ss_files2:
                    with open(os.path.join(SS_DIR, ss_files2[-1]), "rb") as _f:
                        new_hash = hashlib.md5(_f.read()).hexdigest()
            get_tool_guard().record(tool_name, tool_args, result[:200], new_state_hash=new_hash)  # pyre-ignore[arg-type]
        except Exception:
            pass  # record() nunca interrumpe el flujo

    # 5a. Token Economy — registra costo de tool call
    if HAS_TOKEN_ECONOMY:
        try:
            _economy.spend("kernel", 1, reason=f"tool:{tool_name}")
        except Exception:
            pass  # error no crítico, continuar
    # 5b. Knowledge Graph — registra relación tool→resultado
    if HAS_KNOWLEDGE_GRAPH:
        try:
            _kg.add_node(tool_name, node_type="tool", metadata={"last_used": time.time()})
        except Exception:
            pass  # error no crítico, continuar
    # 5c. Memory Decay — almacena resultado con decay temporal
    if HAS_MEMORY_DECAY:
        try:
            _memory_decay.store(
                content=f"[{tool_name}] {result[:400]}" if result else "",
                importance=0.5,
                tags=["tool_result", tool_name]
            )
        except Exception:
            pass  # error no crítico, continuar
    # 5d. MadMax RL — registra interacción para meta-learning
    if HAS_MADMAX:
        try:
            _madmax.record_interaction(
                skill=tool_name,
                success=not result.startswith("[SAFE_CALL ERROR]") if result else False,
                context=str(tool_args)[:100]
            )
        except Exception:
            pass  # error no crítico, continuar
    # 5e. VisionLearner — si hay screenshot, aprende del contenido visual
    if HAS_VISION_LEARNER and tool_name == "take_screenshot":
        try:
            ss_files_vl = sorted([f for f in os.listdir(SS_DIR) if f.endswith(".png")])
            if ss_files_vl:
                latest = os.path.join(SS_DIR, ss_files_vl[-1])
                bridge = get_vision_learner_bridge()
                bridge.analyze_and_learn(image_path=latest, source="screen")
        except Exception:
            pass  # error no crítico, continuar
    # 5f. ColonyEngine — notifica al motor de colonia para aprendizaje continuo
    if HAS_COLONY_ENGINE:
        try:
            colony = get_colony_engine()
            colony._query_count  # Touch para mantener vivo
        except Exception:
            pass  # error no crítico, continuar
    # 5g. IPC Bridge — propaga resultados a VSEIDOS si conectado
    if HAS_IPC_BRIDGE:
        try:
            ipc = get_ipc_bridge()
            if ipc.is_connected():
                ipc.send_event("tool_result", {
                    "tool": tool_name,
                    "success": not result.startswith("[SAFE_CALL ERROR]") if result else True,
                    "summary": result[:200] if result else "",
                })
        except Exception:
            pass  # error no crítico, continuar
    # 5. MetaLearner — registra interacción para meta-aprendizaje
    if HAS_META_LEARNER:
        try:
            success = not result.startswith("[SAFE_CALL ERROR]")
            error_type = ""
            if not success:
                if "timeout" in result.lower():
                    error_type = "timeout"
                elif "permission" in result.lower():
                    error_type = "permission_denied"
                elif "not found" in result.lower():
                    error_type = "not_found"
                else:
                    error_type = "execution_error"
            _meta.record_interaction(
                task=f"{tool_name}({str(tool_args)[:100]})",
                approach=tool_name,
                result_summary=result[:200] if result else "",
                success=success,
                tools_used=[tool_name],
                duration_s=elapsed,
                error_type=error_type,
            )
        except Exception:
            pass  # meta-learning nunca interrumpe el flujo

    return result


# ==============================================================================
#  PHASE 0.4 — Reflect Injection (Oráculo 2: "Reflexión forzada tras cada tool")
# ==============================================================================

REFLECT_MESSAGE = {
    "role": "user",
    "content": (
        "Reflect on the results of the last action and decide the next step:\n"
        "1. Did the action achieve its goal?\n"
        "2. Did the screen/state change as expected?\n"
        "3. What is the logical next action, or should I stop and report?\n"
        "Answer briefly and then call the appropriate tool, or give a final summary."
    )
}


def inject_reflect(messages: list[dict]) -> list[dict]:
    """
    Inyecta el mensaje de reflexión en el historial de mensajes.
    PicoClaw Mosaxiv + Oráculo 2 confirman: esto previene los loops ciegos.
    """
    return messages + [REFLECT_MESSAGE]


# ==============================================================================
#  DETERMINISTIC KERNEL — El núcleo principal
# ==============================================================================

class DeterministicKernel:
    """
    El Deterministic Interaction Kernel de EIDOS.
    
    Loop de ejecución (según el Oráculo 2):
      VER (screenshot) -> PENSAR -> TOOL CALL -> REFLECT -> VERIFICAR -> REPETIR
    
    Guards activos:
      - MAX_ITERATIONS: nunca más de 10 ciclos
      - TIMEOUT: cada tool call tiene su propio timeout
      - StateComparator: abort si 3 screenshots sin cambio
      - Schema Validation: rechaza tool calls malformadas
      - Reflect Injection: forza razonamiento tras cada acción
    
    Modo Computer Use (apps nativas):
      - Prioridad 1: DOM Playwright (web, 100% preciso)
      - Prioridad 2: AT-SPI2 (apps GTK/Qt con accesibilidad)
      - Prioridad 3: OCR + coordenadas (fallback visual)
      - Prioridad 4: moondream2 con grid overlay (último recurso)
    """
    
    def __init__(self, soul: str = "", tool_impl: dict[str, Any] | None = None) -> None:
        from core.tools import TOOLS, TOOL_IMPL  # import lazy para evitar circulares

        self.soul      = soul or "Eres EIDOS, agente soberano de SER. Opera con disciplina y determinismo."
        self.tools     = TOOLS          # JSON schemas para Hermes3
        self.tool_impl = tool_impl or TOOL_IMPL   # implementaciones reales
        self.state_cmp = StateComparator()

        # System prompt para Computer Use (según Oráculo 2)
        self._computer_use_system = self.soul + "\n\n" + COMPUTER_USE_SYSTEM_PROMPT

        # -- EIDOS Optimizations: Start RAM Guardian monitoring ----
        # NOTA: Desactivado por defecto para no interferir durante configuración
        # Para activar: kernel.start_ram_guardian()
        # if HAS_RAM_GUARDIAN:
        #     try:
        #         ram_guardian.start_monitoring()
        #         print("🛡️  [KERNEL] RAM Guardian iniciado")
        #     except Exception as e:
        #         print(f"⚠️  [KERNEL] RAM Guardian no se pudo iniciar: {e}")

        # -- Log optimization status (silencioso por defecto) ----
        if os.environ.get("EIDOS_VERBOSE", "").strip() == "1":
            if HAS_AUTO_CORRECTOR:
                print("🔧 [KERNEL] Auto-Corrector activo")
            if HAS_SMART_CACHE:
                print("💾 [KERNEL] Smart Cache activo")

        # -- EIDOS Advanced Systems: Initialize Advanced Planner & Trainer ----
        self.advanced_planner = None
        self.trainer = None

        if HAS_ADVANCED_PLANNER:
            try:
                self.advanced_planner = get_advanced_planner()
                print("🧠 [KERNEL] Advanced Planner disponible")
            except Exception as e:
                print(f"⚠️  [KERNEL] Advanced Planner error: {e}")

        if HAS_TRAINER:
            try:
                self.trainer = get_trainer()
                print("🎓 [KERNEL] Training System activo")
            except Exception as e:
                print(f"⚠️  [KERNEL] Trainer error: {e}")

        # -- EIDOS Routines: Start background routine manager ----
        self.routines = None
        if HAS_ROUTINES:
            try:
                self.routines = _routines
                self.routines.start()
                print("[KERNEL] Routine Manager activo (%d routines)" % len(self.routines.list_routines()))
            except Exception as e:
                print(f"[KERNEL] Routine Manager error: {e}")

        # -- Vision Model Warm-up (background thread) ----
        # SKIP si EIDOS_NO_VISION_WARMUP=1 — para CLI/Telegram donde compite por RAM
        if os.environ.get("EIDOS_NO_VISION_WARMUP", "").strip() != "1":
            self._warmup_vision_model()

    def _warmup_vision_model(self) -> None:
        """
        Pre-carga el modelo de visión en background para eliminar el delay de 39s
        en la primera llamada. Se puede saltar con EIDOS_NO_VISION_WARMUP=1.
        """
        import threading

        # Esperar 90s antes de empezar — deja que Colony se establezca primero
        delay_s = int(os.environ.get("EIDOS_VISION_WARMUP_DELAY", "90"))

        def warmup():
            import time as _t
            _t.sleep(delay_s)
            try:
                if os.environ.get("EIDOS_VERBOSE", "").strip() == "1":
                    print("🔥 [KERNEL] Warming up Vision model...")
                from core.gui_observer import analyze_screen
                from core.vision_tools import take_screenshot
                import tempfile

                from PIL import Image
                dummy = Image.new('RGB', (1, 1), color='black')
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                    dummy.save(f.name)
                    analyze_screen(f.name, "test")
                    import os
                    os.unlink(f.name)

                if os.environ.get("EIDOS_VERBOSE", "").strip() == "1":
                    print("✅ [KERNEL] Vision model warm-up completado")
            except Exception as e:
                if os.environ.get("EIDOS_VERBOSE", "").strip() == "1":
                    print(f"⚠️  [KERNEL] Vision warm-up falló (no crítico): {e}")

        # Ejecutar en background thread
        warmup_thread = threading.Thread(target=warmup, daemon=True)
        warmup_thread.start()

    def _call_hermes(self, messages: list[dict]) -> dict:
        """Llama a Hermes3 con tool calling nativo."""
        payload = json.dumps({
            "model":    TOOL_MODEL,
            "messages": messages,
            "stream":   False,
            "tools":    self.tools,
            "options":  {"num_ctx": 4096, "num_predict": 512},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)
    
    def run(self, task: str,
            history: list[dict] | None = None,
            computer_use: bool = False,
            max_iterations: int = MAX_ITERATIONS,
            use_dag: bool | None = None) -> str:
        """
        Loop determinista principal con DAG-smart-routing (F22).

        Args:
            task:           La tarea en lenguaje natural.
            history:        Historial previo (hasta 10 últimos msgs).
            computer_use:   Si True, usa system prompt de Computer Use.
            max_iterations: Límite de ciclos antes de rendirse.
            use_dag:        None=auto, True=forzar DAG, False=forzar secuencial.

        Returns:
            Resultado final como string.
        """
        # -- F22: DAG Smart-Routing ------------------------------------------
        # Si la tarea parece compleja y múltiples herramientas son probables,
        # delegar al DAGPlanner para ejecución paralela.
        if use_dag is None:
            # Heurística: keywords de tareas multi-paso
            dag_triggers = [
                "analiza y", "escanea y", "y genera un informe",
                "y luego", "primero", "después", "por último",
                "paso 1", "paso 2", "en paralelo", "simultáneamente",
                "multiple", "varios pasos",
            ]
            task_lower = task.lower()
            use_dag = any(t in task_lower for t in dag_triggers)

        if use_dag:
            try:
                from core.dag_planner import DAGPlanner  # import lazy
                import asyncio
                print(f"\033[95m[[AI] KERNEL->DAG]\033[0m Tarea compleja — delegando a DAGPlanner")
                planner = DAGPlanner(verbose=True)
                loop = asyncio.new_event_loop()
                plan = loop.run_until_complete(planner.plan(task))
                if plan and plan.total_nodes >= 2:
                    result = loop.run_until_complete(planner.execute(plan, self.tool_impl))
                    loop.close()
                    # Formatear informe del DAG
                    done = sum(1 for n in plan.nodes if n.status == "done")
                    err  = sum(1 for n in plan.nodes if n.status == "error")
                    lines = [f"[DAG] {plan.task[:60]} — {done}/{plan.total_nodes} nodos OK, {err} errores"]  # pyre-ignore[arg-type]
                    for node in plan.nodes:
                        icon = "[OK]" if node.status == "done" else "[ERR]"
                        lines.append(f"  {icon} {node.id}({node.tool}) {node.duration_s:.1f}s -> {node.result[:80]}")  # pyre-ignore[arg-type]
                    lines.append(f"\n[RESUMEN]\n{result}")
                    return "\n".join(lines)
                else:
                    loop.close()
                    print(f"\033[90m[DAG] Plan con <2 nodos — usando kernel secuencial\033[0m")
            except Exception as dag_err:
                print(f"\033[90m[DAG] DAGPlanner no disponible ({dag_err}) — kernel secuencial\033[0m")

        # -- Sequential kernel loop (fallback from DAG or simple tasks) -----

        # -- Session 10: Smart Router — selección inteligente de modelo --
        if HAS_SMART_ROUTER:
            try:
                route = _smart_router.route(task)
                if route and route.get("model"):
                    print(f"\033[96m[ROUTER] Modelo sugerido: {route['model']} (score={route.get('score', '?')})\033[0m")
            except Exception:
                pass  # error no crítico, continuar
        system_content = self._computer_use_system if computer_use else self.soul

        # Inicializar historial de mensajes
        messages: list[dict] = [{"role": "system", "content": system_content}]
        if history:
            # -- Session 10: 7-Layer Compression — comprimir historial largo --
            hist_slice = history[-10:]
            if HAS_COMPRESSOR and len(history) > 6:
                try:
                    for i, msg in enumerate(hist_slice):
                        if msg.get("role") == "assistant" and len(msg.get("content", "")) > 500:
                            compressed = _compressor.compress(msg["content"])
                            hist_slice[i] = {**msg, "content": compressed}
                except Exception:
                    pass  # error no crítico, continuar
            messages.extend(hist_slice)
        messages.append({"role": "user", "content": task})
        
        # Resetear StateComparator para esta tarea
        self.state_cmp.reset()
        
        print(f"\033[95m[[AI] KERNEL]\033[0m Iniciando tarea: {task[:80]}")  # pyre-ignore[arg-type]
        print(f"\033[95m[[AI] KERNEL]\033[0m MAX_ITERATIONS={max_iterations}, TOOL_TIMEOUT={TOOL_TIMEOUT_S}s")
        
        last_result = ""
        # F-A: Detección de bucles por hash de tool_call repetido
        _seen_tool_hashes: set = set()
        _tool_repeat_count: dict = {}
        MAX_TOOL_REPEATS = 3  # abortar si el mismo tool+args se llama 3 veces

        for iteration in range(1, max_iterations + 1):
            print(f"\n\033[93m--- ITERACIÓN {iteration}/{max_iterations} ---\033[0m")
            
            # -- Llamar a Hermes3 -----------------------------------------
            try:
                response = self._call_hermes(messages)
            except Exception as e:
                err = f"[KERNEL] Error llamando a Hermes3: {e}"
                print(f"\033[91m{err}\033[0m")
                return err
            
            msg        = response.get("message", {})
            tool_calls = msg.get("tool_calls", [])
            content    = msg.get("content", "")
            
            # -- Respuesta final (sin tool calls) -> done ------------------
            if not tool_calls:
                final = content or last_result or "(Sin respuesta)"
                print(f"\033[92m[[OK] KERNEL DONE]\033[0m {final[:150]}")  # pyre-ignore[arg-type]
                return final
            
            # -- Ejecutar tool calls ---------------------------------------
            messages.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": f"tc_{iteration}_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.get("function", {}).get("name", ""),
                            # Ollama exige que arguments sea un diccionario, NO un string
                            "arguments": tc.get("function", {}).get("arguments", {})
                        }
                    }
                    for i, tc in enumerate(tool_calls)
                ]
            })
            
            tool_results = []
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args = tc.get("function", {}).get("arguments", {})
                
                # Limpiar args si vienen como string JSON
                if isinstance(fn_args, str):
                    try:
                        fn_args = json.loads(fn_args)
                    except Exception:
                        fn_args = {}
                
                # F-A: Detectar tool_call repetida -> abort o retry alternativo
                _tc_key = hashlib.md5(
                    f"{fn_name}:{json.dumps(fn_args, sort_keys=True)}".encode()
                ).hexdigest()
                if _tc_key in _seen_tool_hashes:
                    _tool_repeat_count[_tc_key] = _tool_repeat_count.get(_tc_key, 1) + 1
                    repeats = _tool_repeat_count[_tc_key]
                    if repeats >= MAX_TOOL_REPEATS:
                        abort_msg = (
                            f"[KERNEL] BUCLE DETECTADO: '{fn_name}' llamada {repeats}x "
                            f"con los mismos args. Abortando para evitar loop infinito."
                        )
                        print(f"\033[91m{abort_msg}\033[0m")
                        return abort_msg
                    print(f"\033[93m[KERNEL] [WARN]️ Tool '{fn_name}' repetida ({repeats}x)\033[0m")
                _seen_tool_hashes.add(_tc_key)

                # [GUARD]️ safe_call: validation + execution + StateComparator
                result = safe_call(
                    tool_name=fn_name,
                    tool_args=fn_args,
                    tool_impl=self.tool_impl,
                    state_comparator=self.state_cmp,
                    timeout=TOOL_TIMEOUT_S
                )
                last_result = result
                
                messages.append({
                    "role":    "tool",
                    "name":    fn_name,
                    "content": result[:2000],  # Limitar tamaño para no saturar contexto  # pyre-ignore[arg-type]
                })
                tool_results.append(f"[{fn_name}]: {result[:300]}")  # pyre-ignore[arg-type]
                
                # Abort early si StateComparator detectó loop ciego
                if "[KERNEL] LOOP CIEGO DETECTADO" in result:
                    return result
            
            # -- Inject Reflect (Oráculo 2 + PicoClaw Mosaxiv pattern) ----
            messages = inject_reflect(messages)

            # -- F35: Auto-record en history.jsonl para Knowledge Evolver --
            try:
                if "record_exchange" in self.tool_impl:
                    _tool_names = [tc.get("function", {}).get("name", "") for tc in tool_calls]  # pyre-ignore[arg-type]
                    self.tool_impl["record_exchange"]({
                        "user_input":  task[:500],  # pyre-ignore[arg-type]
                        "eidos_reply": last_result[:500],  # pyre-ignore[arg-type]
                        "tools_used":  ",".join(_tool_names),  # pyre-ignore[arg-type]
                    })
            except Exception:
                pass  # Non-fatal: no interrumpir el kernel por un fallo de memoria

            # Log de iteración
            print(f"\033[90m  Tools ejecutadas: {[tc.get('function',{}).get('name') for tc in tool_calls]}\033[0m")
        
        # Máximo de iteraciones alcanzado
        abort_msg = (
            f"[KERNEL] Límite de {max_iterations} iteraciones alcanzado sin completar la tarea. "
            f"Último resultado: {last_result[:300]}"  # pyre-ignore[arg-type]
        )
        print(f"\033[91m{abort_msg}\033[0m")
        return abort_msg


# ==============================================================================
#  COMPUTER USE SYSTEM PROMPT (Oráculo 2 — Bloque 4)
# ==============================================================================

COMPUTER_USE_SYSTEM_PROMPT = """
#=========
EIDOS -- MODO COMPUTER USE (Deterministic Kernel v1.0)
===========================================================

Eres el agente de control de computadora de SER. Operas con disciplina total.

CICLO OBLIGATORIO (nunca lo saltes):
  1. VER    -> Siempre toma un screenshot PRIMERO antes de cualquier acción de UI
  2. PENSAR -> Analiza qué elemento necesitas interactuar y cuál es el método correcto
  3. ACTUAR -> Ejecuta UN SOLO tool call por ciclo
  4. REFLECT -> Analiza si el resultado fue el esperado
  5. VERIFICAR -> Toma otro screenshot para confirmar el cambio de estado
  6. REPETIR o TERMINAR

REGLAS INQUEBRANTABLES:
  [ERR] NUNCA llames a mouse_click(x, y) sin haber ejecutado take_screenshot() antes
  [ERR] NUNCA llames a keyboard_type() si no sabes que el campo correcto está enfocado
  [ERR] NUNCA uses más de una acción de UI por iteración
  [ERR] NUNCA inventes coordenadas: si no estás seguro del elemento, vuelve a tomar screenshot

PRIORIDAD DE MÉTODOS (de más a menos preciso):
  0. deep_crawl_url(url, max_pages, visual) -> INVESTIGA un sitio completo + sublinks. visual=true abre browser visible. USA ESTO cuando SER pide "investiga", "lee", "analiza" una URL.
  1. dom_click(url, selector)     -> Para páginas web. USA ESTO PRIMERO si es web.
  2. AT-SPI2 (exec_shell atspy)   -> Para apps GTK/Qt nativas con accesibilidad activa
  3. OCR + coordenadas            -> Lee texto del screenshot + calcula centro del bbox
  4. take_screenshot + moondream  -> Último recurso para apps sin accesibilidad

SI ALGO FALLA:
  - Si el mismo click falla 2 veces: cambia de método (DOM -> OCR -> visual)
  - Si la pantalla no cambia después de una acción: DETENTE y reporta a SER
  - Si hay 3 iteraciones sin cambio de estado: la tarea debe ser abortada

SEPARACIÓN ABSOLUTA:
  - Investigar URL completa (SER pide "investiga/lee/abre/analiza" URL) -> deep_crawl_url(url, visual=true) PRIMERO
  - Entorno WEB   -> SIEMPRE Playwright DOM (dom_click, dom_type, dom_get_text)
  - Apps NATIVAS  -> PyAutoGUI + OCR/AT-SPI2 (mouse_click, keyboard_type)
  - Nunca mezcles los dos enfoques en la misma acción.

El objetivo es siempre la SOBERANÍA TÉCNICA: operar completo, local, determinista.
"""


# -- Test rápido --------------------------------------------------------------
if __name__ == "__main__":
    print("=== Test DeterministicKernel — Fase 0 ===")
    print()
    
    # Test 1: Validación de schemas
    print("-- Test Validación de Schemas --")
    valid, err = validate_tool_call("exec_shell", {"command": "echo hola"})
    print(f"  exec_shell válida: {valid}")
    
    valid, err = validate_tool_call("exec_shell", {})   # Sin command = inválido
    print(f"  exec_shell sin command: valid={valid}, err={err[:60]}")  # pyre-ignore[arg-type]
    
    valid, err = validate_tool_call("mouse_click", {"x": 100, "y": 200})
    print(f"  mouse_click válida: {valid}")
    
    valid, err = validate_tool_call("unknown_tool", {})
    print(f"  unknown_tool: valid={valid}, err={err[:60]}")  # pyre-ignore[arg-type]
    
    # Test 2: StateComparator
    print("\n-- Test StateComparator --")
    cmp = StateComparator(abort_threshold=2)
    print(f"  Primera check (None): {cmp.check('/nonexistent.png')}")
    
    print("\n-- Kernel listo. Run: python -c 'from core.kernel import DeterministicKernel; k=DeterministicKernel(); print(k.run(\"dime la hora\"))' --")
