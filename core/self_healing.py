#!/usr/bin/env python3
"""
EIDOS core/self_healing.py — Self-Healing / Experiment Repair
==============================================================
Sistema de auto-reparacion que detecta fallos, los categoriza
y aplica fixes automaticos. Aprende de reparaciones exitosas.

Inspirado en AutoResearchClaw: cuando un experimento falla,
el sistema diagnostica, repara y reintenta autonomamente.

Categorias de error:
- import_error: modulo faltante → auto-instala con pip
- syntax_error: error de sintaxis → intenta fix con LLM
- runtime_error: error en ejecucion → retry con parametros ajustados
- timeout: timeout → incrementa timeout o reduce carga
- resource_error: sin RAM/disco → activa cleanup
- network_error: sin red → retry con exponential backoff

Complementa auto_corrector.py (no duplica):
- auto_corrector: analiza codigo ANTES de ejecutar, sandbox
- self_healing: repara DESPUES de un fallo en produccion

Backend: SQLite en ~/.eidos/healing.db

Uso:
    from core.self_healing import SelfHealer

    healer = SelfHealer()

    try:
        result = run_experiment()
    except Exception as e:
        category, severity, action = healer.diagnose(e)
        fixed, action_taken, new_code = healer.heal(e, context={"code": source})
        if fixed:
            print(f"Auto-reparado: {action_taken}")
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from core.db import get_conn

# ══════════════════════════════════════════════════════════════════════════════
# Configuracion
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "healing.db"

MAX_HEAL_ATTEMPTS = 3         # Maximo intentos de fix por error
BACKOFF_BASE = 2.0            # Base para exponential backoff (segundos)
BACKOFF_MAX = 60.0            # Maximo backoff (segundos)

ERROR_CATEGORIES = {
    "import_error",
    "syntax_error",
    "runtime_error",
    "timeout",
    "resource_error",
    "network_error",
    "unknown",
}

SEVERITY_LEVELS = {"low", "medium", "high", "critical"}


# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Diagnosis:
    """Resultado de diagnosticar un error."""
    category: str
    severity: str
    recommended_action: str
    error_type: str = ""
    error_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealResult:
    """Resultado de intentar reparar un error."""
    fixed: bool
    action_taken: str
    new_code: Optional[str] = None
    attempts: int = 0
    diagnosis: Optional[Diagnosis] = None


# ══════════════════════════════════════════════════════════════════════════════
# Self Healer
# ══════════════════════════════════════════════════════════════════════════════

class SelfHealer:
    """
    Sistema de auto-reparacion con aprendizaje.

    Diagnostica errores, aplica fixes, y recuerda reparaciones
    exitosas para errores similares en el futuro.
    """

    def __init__(self, db_path: str = None, verbose: bool = True):
        self.db_path = db_path or str(DB_PATH)
        self.verbose = verbose
        self._lock = threading.Lock()
        self._attempt_counts: Dict[str, int] = {}  # error_key → attempts

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = get_conn(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")

        self._init_db()

        total = self.conn.execute("SELECT COUNT(*) FROM healing_history").fetchone()[0]
        self._log(f"Inicializado: {total} reparaciones en historial")

    def _log(self, msg: str):
        if self.verbose:
            print(f"🩺 [SelfHealer] {msg}")

    def _init_db(self):
        """Crea tablas si no existen."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS healing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                error_type TEXT NOT NULL,
                error_message TEXT,
                category TEXT,
                severity TEXT,
                action_taken TEXT,
                fixed INTEGER DEFAULT 0,
                context TEXT DEFAULT '{}',
                timestamp REAL
            );

            CREATE TABLE IF NOT EXISTS learned_fixes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                error_pattern TEXT NOT NULL UNIQUE,
                fix_action TEXT NOT NULL,
                success_count INTEGER DEFAULT 1,
                fail_count INTEGER DEFAULT 0,
                last_used REAL
            );

            CREATE INDEX IF NOT EXISTS idx_healing_type ON healing_history(error_type);
            CREATE INDEX IF NOT EXISTS idx_healing_category ON healing_history(category);
            CREATE INDEX IF NOT EXISTS idx_fixes_pattern ON learned_fixes(error_pattern);
        """)
        self.conn.commit()

    def _error_key(self, error: Exception) -> str:
        """Genera clave unica para un error (para rate limiting)."""
        return f"{type(error).__name__}:{str(error)[:100]}"

    # ──────────────────────────────────────────────────────────────────────
    # Diagnostico
    # ──────────────────────────────────────────────────────────────────────

    def diagnose(self, error: Exception, context: Dict[str, Any] = None) -> Diagnosis:
        """
        Diagnostica un error: lo categoriza y recomienda accion.

        Args:
            error: La excepcion capturada
            context: Contexto adicional (codigo, parametros, etc.)

        Returns:
            Diagnosis con categoria, severidad y accion recomendada
        """
        error_type = type(error).__name__
        error_msg = str(error)
        context = context or {}

        self._log(f"🔍 Diagnosticando: {error_type}: {error_msg[:80]}")

        # ── Import errors ──
        if error_type in ("ModuleNotFoundError", "ImportError"):
            module = self._extract_module_name(error_msg)
            return Diagnosis(
                category="import_error",
                severity="medium",
                recommended_action=f"pip install {module}" if module else "check imports",
                error_type=error_type,
                error_message=error_msg,
                details={"module": module},
            )

        # ── Syntax errors ──
        if error_type == "SyntaxError":
            return Diagnosis(
                category="syntax_error",
                severity="high",
                recommended_action="fix_syntax_with_ast_or_llm",
                error_type=error_type,
                error_message=error_msg,
                details={"lineno": getattr(error, "lineno", None)},
            )

        # ── Timeout ──
        if error_type in ("TimeoutError", "asyncio.TimeoutError") or "timeout" in error_msg.lower():
            return Diagnosis(
                category="timeout",
                severity="medium",
                recommended_action="increase_timeout_or_reduce_load",
                error_type=error_type,
                error_message=error_msg,
            )

        # ── Resource errors ──
        if error_type == "MemoryError" or "No space left" in error_msg or "Cannot allocate" in error_msg:
            return Diagnosis(
                category="resource_error",
                severity="critical",
                recommended_action="activate_cleanup_and_free_resources",
                error_type=error_type,
                error_message=error_msg,
            )

        # ── Network errors ──
        network_indicators = [
            "ConnectionRefusedError", "ConnectionResetError", "ConnectionError",
            "URLError", "HTTPError", "socket.timeout", "ConnectionAbortedError",
        ]
        if error_type in network_indicators or any(ind in error_msg for ind in ["Connection refused", "Network is unreachable", "Name or service not known"]):
            return Diagnosis(
                category="network_error",
                severity="medium",
                recommended_action="retry_with_exponential_backoff",
                error_type=error_type,
                error_message=error_msg,
            )

        # ── Runtime errors (generico) ──
        severity = "medium"
        if error_type in ("TypeError", "ValueError"):
            severity = "low"
        elif error_type in ("PermissionError", "OSError"):
            severity = "high"

        return Diagnosis(
            category="runtime_error",
            severity=severity,
            recommended_action="retry_with_adjusted_params",
            error_type=error_type,
            error_message=error_msg,
        )

    def _extract_module_name(self, error_msg: str) -> Optional[str]:
        """Extrae nombre del modulo de un error de import."""
        match = re.search(r"No module named '([^']+)'", error_msg)
        if match:
            return match.group(1).split(".")[0]
        match = re.search(r"cannot import name '([^']+)'", error_msg)
        if match:
            return match.group(1)
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Healing
    # ──────────────────────────────────────────────────────────────────────

    def heal(self, error: Exception, context: Dict[str, Any] = None) -> HealResult:
        """
        Intenta reparar un error automaticamente.

        Rate limiting: maximo MAX_HEAL_ATTEMPTS intentos por error.

        Args:
            error: La excepcion capturada
            context: Contexto (code, params, timeout, etc.)

        Returns:
            HealResult con estado de la reparacion
        """
        context = context or {}
        error_key = self._error_key(error)

        # Rate limiting
        attempts = self._attempt_counts.get(error_key, 0)
        if attempts >= MAX_HEAL_ATTEMPTS:
            self._log(f"⛔ Max intentos alcanzado para: {error_key[:60]}")
            return HealResult(
                fixed=False,
                action_taken=f"max_attempts_reached ({MAX_HEAL_ATTEMPTS})",
                attempts=attempts,
            )

        self._attempt_counts[error_key] = attempts + 1

        # Diagnosticar
        diagnosis = self.diagnose(error, context)
        self._log(f"  Categoria: {diagnosis.category} | Severidad: {diagnosis.severity}")

        # Buscar fix aprendido
        learned = self._find_learned_fix(error_key)
        if learned:
            self._log(f"  📚 Fix aprendido encontrado: {learned}")
            result = self._apply_learned_fix(learned, context)
            result.diagnosis = diagnosis
            result.attempts = attempts + 1
            self._record_healing(error, diagnosis, result)
            return result

        # Aplicar fix segun categoria
        heal_methods = {
            "import_error": self._heal_import_error,
            "syntax_error": self._heal_syntax_error,
            "runtime_error": self._heal_runtime_error,
            "timeout": self._heal_timeout,
            "resource_error": self._heal_resource_error,
            "network_error": self._heal_network_error,
        }

        heal_fn = heal_methods.get(diagnosis.category, self._heal_unknown)
        result = heal_fn(error, diagnosis, context)
        result.diagnosis = diagnosis
        result.attempts = attempts + 1

        # Registrar y aprender
        self._record_healing(error, diagnosis, result)

        if result.fixed:
            self._learn_fix(error_key, result.action_taken)

        return result

    # ── Healers por categoria ──

    def _heal_import_error(self, error: Exception, diagnosis: Diagnosis,
                            context: Dict[str, Any]) -> HealResult:
        """Repara errores de import: auto-instala modulos."""
        module = diagnosis.details.get("module")
        if not module:
            return HealResult(fixed=False, action_taken="could_not_extract_module_name")

        # Mapa de nombres pip vs import (cuando difieren)
        pip_names = {
            "cv2": "opencv-python",
            "PIL": "Pillow",
            "sklearn": "scikit-learn",
            "yaml": "pyyaml",
            "bs4": "beautifulsoup4",
            "gi": "PyGObject",
            "Crypto": "pycryptodome",
        }

        pip_name = pip_names.get(module, module)
        self._log(f"  📦 Auto-instalando: {pip_name}")

        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", pip_name, "--quiet"],
                capture_output=True, text=True, timeout=60
            )

            if result.returncode == 0:
                self._log(f"  ✅ {pip_name} instalado correctamente")
                return HealResult(
                    fixed=True,
                    action_taken=f"pip_install_{pip_name}",
                )
            else:
                self._log(f"  ⚠️ pip install fallo: {result.stderr[:100]}")
                return HealResult(
                    fixed=False,
                    action_taken=f"pip_install_failed: {result.stderr[:100]}",
                )
        except subprocess.TimeoutExpired:
            return HealResult(fixed=False, action_taken="pip_install_timeout")
        except Exception as e:
            return HealResult(fixed=False, action_taken=f"pip_install_exception: {e}")

    def _heal_syntax_error(self, error: Exception, diagnosis: Diagnosis,
                            context: Dict[str, Any]) -> HealResult:
        """
        Repara errores de sintaxis.
        Intenta usar auto_corrector si esta disponible; si no, aplica fixes basicos.
        """
        code = context.get("code", "")
        if not code:
            return HealResult(fixed=False, action_taken="no_code_in_context")

        # Intentar con auto_corrector existente
        try:
            from core.auto_corrector import auto_corrector
            analysis = auto_corrector.analyze_code(code)

            if not analysis.valid_syntax:
                # Intentar fixes basicos de sintaxis
                fixed_code = self._try_basic_syntax_fixes(code)
                if fixed_code and fixed_code != code:
                    return HealResult(
                        fixed=True,
                        action_taken="basic_syntax_fix",
                        new_code=fixed_code,
                    )
        except ImportError:
            pass

        # Fixes basicos sin auto_corrector
        fixed_code = self._try_basic_syntax_fixes(code)
        if fixed_code and fixed_code != code:
            return HealResult(
                fixed=True,
                action_taken="basic_syntax_fix",
                new_code=fixed_code,
            )

        return HealResult(fixed=False, action_taken="syntax_fix_not_possible_without_llm")

    def _try_basic_syntax_fixes(self, code: str) -> Optional[str]:
        """Intenta fixes basicos de sintaxis."""
        fixes_applied = []
        fixed = code

        # Fix 1: parentesis/corchetes sin cerrar
        open_parens = fixed.count("(") - fixed.count(")")
        if open_parens > 0:
            fixed += ")" * open_parens
            fixes_applied.append(f"added {open_parens} closing parens")

        open_brackets = fixed.count("[") - fixed.count("]")
        if open_brackets > 0:
            fixed += "]" * open_brackets
            fixes_applied.append(f"added {open_brackets} closing brackets")

        open_braces = fixed.count("{") - fixed.count("}")
        if open_braces > 0:
            fixed += "}" * open_braces
            fixes_applied.append(f"added {open_braces} closing braces")

        # Fix 2: trailing colon sin bloque
        lines = fixed.split("\n")
        new_lines = []
        for i, line in enumerate(lines):
            new_lines.append(line)
            stripped = line.rstrip()
            if stripped.endswith(":") and i == len(lines) - 1:
                indent = len(line) - len(line.lstrip())
                new_lines.append(" " * (indent + 4) + "pass")
                fixes_applied.append("added pass after trailing colon")

        fixed = "\n".join(new_lines)

        if fixes_applied:
            # Verificar si el fix funciona
            try:
                compile(fixed, "<self_healing>", "exec")
                return fixed
            except SyntaxError:
                return None

        return None

    def _heal_runtime_error(self, error: Exception, diagnosis: Diagnosis,
                             context: Dict[str, Any]) -> HealResult:
        """Repara errores de runtime: ajusta parametros."""
        code = context.get("code", "")

        # TypeError: argumentos incorrectos
        if isinstance(error, TypeError):
            error_msg = str(error)
            # "func() takes X positional arguments but Y were given"
            if "positional arguments" in error_msg:
                return HealResult(
                    fixed=False,
                    action_taken="argument_count_mismatch_needs_code_review",
                )

            # "can't multiply sequence by non-int"
            if "can't multiply" in error_msg or "unsupported operand" in error_msg:
                return HealResult(
                    fixed=False,
                    action_taken="type_mismatch_needs_code_review",
                )

        # ValueError
        if isinstance(error, ValueError):
            return HealResult(
                fixed=False,
                action_taken="value_error_needs_input_validation",
            )

        # KeyError
        if isinstance(error, KeyError):
            key = str(error)
            if code:
                # Sugerir usar .get() en vez de []
                return HealResult(
                    fixed=False,
                    action_taken=f"suggest_dict_get_for_key_{key}",
                )

        return HealResult(fixed=False, action_taken="runtime_error_unhandled")

    def _heal_timeout(self, error: Exception, diagnosis: Diagnosis,
                       context: Dict[str, Any]) -> HealResult:
        """Repara timeouts: incrementa timeout y pide reintento.

        No marca fixed=True porque el timeout solo prepara las condiciones
        para un reintento; la operación original aún no ha tenido éxito.
        El caller debe reintentar la operación con context['timeout'].
        """
        current_timeout = context.get("timeout", 30)
        new_timeout = min(current_timeout * 2, 300)  # Max 5 minutos

        # Actualizar el contexto para que el caller pueda reintentar
        context["timeout"] = new_timeout
        context["_retry"] = True
        context["_retry_reason"] = f"timeout increased {current_timeout}s -> {new_timeout}s"

        self._log(f"  ⏱️ Timeout: {current_timeout}s → {new_timeout}s (reintento requerido)")

        return HealResult(
            fixed=False,  # La operación no ha tenido éxito todavía
            action_taken=f"timeout_increased_{current_timeout}_to_{new_timeout}_retry_pending",
        )

    def _heal_resource_error(self, error: Exception, diagnosis: Diagnosis,
                              context: Dict[str, Any]) -> HealResult:
        """Repara errores de recursos: libera memoria/disco."""
        actions = []

        # Intentar liberar memoria
        try:
            import gc
            gc.collect()
            actions.append("gc_collect")
        except Exception:
            pass  # error no crítico, continuar
        # Verificar espacio en disco
        try:
            disk = shutil.disk_usage(Path.home())
            free_gb = disk.free / (1024 ** 3)

            if free_gb < 1.0:
                # Intentar limpiar caches de EIDOS
                cache_dirs = [
                    Path.home() / ".eidos" / "cache",
                    Path.home() / ".eidos" / "tmp",
                ]
                for cache_dir in cache_dirs:
                    if cache_dir.exists():
                        for f in cache_dir.iterdir():
                            try:
                                if f.is_file():
                                    f.unlink()
                                    actions.append(f"deleted_cache_{f.name}")
                            except Exception:
                                pass  # error no crítico, continuar
                actions.append(f"disk_free_{free_gb:.1f}GB")
        except Exception:
            pass  # error no crítico, continuar
        # Intentar cleanup de modulo existente
        try:
            from core.cleanup_system import cleanup
            cleanup()
            actions.append("cleanup_system_executed")
        except ImportError:
            pass

        fixed = len(actions) > 0
        return HealResult(
            fixed=fixed,
            action_taken="; ".join(actions) if actions else "no_cleanup_possible",
        )

    def _heal_network_error(self, error: Exception, diagnosis: Diagnosis,
                              context: Dict[str, Any]) -> HealResult:
        """Repara errores de red: espera con exponential backoff y pide reintento.

        El backoff duerme el tiempo necesario y señala al caller que
        reintente la operación. No marca fixed=True porque dormir no
        es éxito; la operación de red original debe reintentarse.
        """
        attempt = self._attempt_counts.get(self._error_key(error), 1)
        backoff = min(BACKOFF_BASE ** attempt, BACKOFF_MAX)
        actual_sleep = min(backoff, 5.0)  # Sleep max 5s en tests

        self._log(f"  🌐 Network error: backoff {backoff:.1f}s (durmiendo {actual_sleep:.1f}s)")

        # Esperar el backoff antes de señalar el reintento
        time.sleep(actual_sleep)

        # Señalar al caller que reintente la operación
        context["_retry"] = True
        context["_retry_reason"] = f"network backoff {backoff:.1f}s, slept {actual_sleep:.1f}s"
        context["_retry_delay"] = backoff

        return HealResult(
            fixed=False,  # Dormir no es éxito — el caller debe reintentar
            action_taken=f"network_backoff_{backoff:.1f}s_retry_pending",
        )

    def _heal_unknown(self, error: Exception, diagnosis: Diagnosis,
                       context: Dict[str, Any]) -> HealResult:
        """Para errores desconocidos."""
        return HealResult(
            fixed=False,
            action_taken=f"unknown_error_type_{type(error).__name__}",
        )

    # ──────────────────────────────────────────────────────────────────────
    # Aprendizaje
    # ──────────────────────────────────────────────────────────────────────

    def _find_learned_fix(self, error_key: str) -> Optional[str]:
        """Busca un fix aprendido para un error similar."""
        # Buscar patron exacto
        row = self.conn.execute(
            "SELECT fix_action FROM learned_fixes WHERE error_pattern = ? AND success_count > fail_count",
            (error_key[:200],)
        ).fetchone()

        if row:
            return row[0]

        # Buscar patron parcial (primeros 50 chars)
        short_key = error_key[:50]
        row = self.conn.execute(
            "SELECT fix_action FROM learned_fixes WHERE error_pattern LIKE ? AND success_count > fail_count ORDER BY success_count DESC LIMIT 1",
            (f"{short_key}%",)
        ).fetchone()

        return row[0] if row else None

    def _learn_fix(self, error_key: str, fix_action: str):
        """Registra un fix exitoso para aprendizaje futuro."""
        with self._lock:
            existing = self.conn.execute(
                "SELECT id, success_count FROM learned_fixes WHERE error_pattern = ?",
                (error_key[:200],)
            ).fetchone()

            now = time.time()

            if existing:
                self.conn.execute(
                    "UPDATE learned_fixes SET success_count = success_count + 1, last_used = ? WHERE id = ?",
                    (now, existing[0])
                )
            else:
                self.conn.execute(
                    "INSERT INTO learned_fixes (error_pattern, fix_action, success_count, last_used) VALUES (?, ?, 1, ?)",
                    (error_key[:200], fix_action, now)
                )

            self.conn.commit()
        self._log(f"  📚 Fix aprendido: {fix_action[:50]}")

    def _apply_learned_fix(self, fix_action: str, context: Dict[str, Any]) -> HealResult:
        """Aplica un fix previamente aprendido."""
        # Para fixes de pip install
        if fix_action.startswith("pip_install_"):
            module = fix_action.replace("pip_install_", "")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", module, "--quiet"],
                    capture_output=True, timeout=60
                )
                return HealResult(fixed=True, action_taken=f"learned_{fix_action}")
            except Exception:
                return HealResult(fixed=False, action_taken=f"learned_fix_failed_{fix_action}")

        # Para otros fixes
        return HealResult(fixed=True, action_taken=f"learned_{fix_action}")

    def _record_healing(self, error: Exception, diagnosis: Diagnosis, result: HealResult):
        """Registra un intento de reparacion en historial."""
        with self._lock:
            self.conn.execute(
                """INSERT INTO healing_history
                   (error_type, error_message, category, severity, action_taken, fixed, context, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    type(error).__name__,
                    str(error)[:500],
                    diagnosis.category,
                    diagnosis.severity,
                    result.action_taken,
                    1 if result.fixed else 0,
                    json.dumps({"attempts": result.attempts}),
                    time.time(),
                )
            )
            self.conn.commit()

    # ──────────────────────────────────────────────────────────────────────
    # Estadisticas
    # ──────────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Estadisticas del sistema de healing."""
        total = self.conn.execute("SELECT COUNT(*) FROM healing_history").fetchone()[0]
        fixed = self.conn.execute("SELECT COUNT(*) FROM healing_history WHERE fixed = 1").fetchone()[0]

        by_category = self.conn.execute(
            "SELECT category, COUNT(*), SUM(fixed) FROM healing_history GROUP BY category"
        ).fetchall()

        learned = self.conn.execute("SELECT COUNT(*) FROM learned_fixes").fetchone()[0]
        top_fixes = self.conn.execute(
            "SELECT error_pattern, fix_action, success_count FROM learned_fixes ORDER BY success_count DESC LIMIT 5"
        ).fetchall()

        return {
            "total_healings": total,
            "successful": fixed,
            "success_rate": round(fixed / max(total, 1) * 100, 1),
            "by_category": {
                r[0]: {"total": r[1], "fixed": r[2]} for r in by_category
            },
            "learned_fixes": learned,
            "top_fixes": [
                {"pattern": r[0][:60], "action": r[1], "count": r[2]}
                for r in top_fixes
            ],
            "db_path": self.db_path,
        }

    def reset_attempts(self, error: Exception = None):
        """Resetea el contador de intentos (para un error o todos)."""
        if error:
            key = self._error_key(error)
            self._attempt_counts.pop(key, None)
        else:
            self._attempt_counts.clear()

    def close(self):
        """Cierra la conexion."""
        self.conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_healer: Optional[SelfHealer] = None

def get_self_healer() -> SelfHealer:
    """Obtiene la instancia singleton de SelfHealer."""
    global _healer
    if _healer is None:
        _healer = SelfHealer()
    return _healer


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import tempfile

    print(f"\n{'=' * 70}")
    print(f"EIDOS SELF-HEALER — Test Suite")
    print(f"{'=' * 70}\n")

    # Usar DB temporal para tests
    test_db = tempfile.mktemp(suffix=".db")
    healer = SelfHealer(db_path=test_db, verbose=True)

    passed = 0
    failed = 0

    def test(name, condition):
        global passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            failed += 1

    # ── Test 1: Diagnostico import_error ──
    print("\n📌 Test 1: Diagnostico import_error")
    err = ModuleNotFoundError("No module named 'nonexistent_pkg'")
    diag = healer.diagnose(err)
    test("categoria = import_error", diag.category == "import_error")
    test("severidad = medium", diag.severity == "medium")
    test("module extraido", diag.details.get("module") == "nonexistent_pkg")
    print(f"      Accion: {diag.recommended_action}")

    # ── Test 2: Diagnostico syntax_error ──
    print("\n📌 Test 2: Diagnostico syntax_error")
    err2 = SyntaxError("invalid syntax")
    diag2 = healer.diagnose(err2)
    test("categoria = syntax_error", diag2.category == "syntax_error")
    test("severidad = high", diag2.severity == "high")

    # ── Test 3: Diagnostico timeout ──
    print("\n📌 Test 3: Diagnostico timeout")
    err3 = TimeoutError("Operation timed out")
    diag3 = healer.diagnose(err3)
    test("categoria = timeout", diag3.category == "timeout")

    # ── Test 4: Diagnostico resource_error ──
    print("\n📌 Test 4: Diagnostico resource_error")
    err4 = MemoryError("Cannot allocate memory")
    diag4 = healer.diagnose(err4)
    test("categoria = resource_error", diag4.category == "resource_error")
    test("severidad = critical", diag4.severity == "critical")

    # ── Test 5: Diagnostico network_error ──
    print("\n📌 Test 5: Diagnostico network_error")
    err5 = ConnectionRefusedError("Connection refused")
    diag5 = healer.diagnose(err5)
    test("categoria = network_error", diag5.category == "network_error")

    # ── Test 6: Diagnostico runtime_error ──
    print("\n📌 Test 6: Diagnostico runtime_error")
    err6 = TypeError("can't multiply sequence by non-int")
    diag6 = healer.diagnose(err6)
    test("categoria = runtime_error", diag6.category == "runtime_error")
    test("severidad = low", diag6.severity == "low")

    # ── Test 7: Heal timeout ──
    print("\n📌 Test 7: Heal timeout")
    err7 = TimeoutError("timed out")
    result7 = healer.heal(err7, context={"timeout": 30})
    test("heal timeout fixed", result7.fixed)
    test("heal timeout action", "timeout" in result7.action_taken)

    # ── Test 8: Heal resource ──
    print("\n📌 Test 8: Heal resource_error")
    healer.reset_attempts()
    err8 = MemoryError("out of memory")
    result8 = healer.heal(err8)
    test("heal resource ejecutado", result8.action_taken != "")
    print(f"      Action: {result8.action_taken}")

    # ── Test 9: Heal syntax con fix basico ──
    print("\n📌 Test 9: Heal syntax_error con fix basico")
    healer.reset_attempts()
    err9 = SyntaxError("unexpected EOF while parsing")
    code_broken = "def hello(\n    print('hi'"
    result9 = healer.heal(err9, context={"code": code_broken})
    test("syntax heal intento", result9.action_taken != "")
    print(f"      Action: {result9.action_taken}")
    if result9.new_code:
        print(f"      New code: {result9.new_code[:60]}...")

    # ── Test 10: Rate limiting ──
    print("\n📌 Test 10: Rate limiting")
    healer.reset_attempts()
    err10 = ValueError("bad value")
    for i in range(MAX_HEAL_ATTEMPTS):
        healer.heal(err10)
    result10 = healer.heal(err10)
    test("rate limit alcanzado", "max_attempts_reached" in result10.action_taken)

    # ── Test 11: Aprendizaje ──
    print("\n📌 Test 11: Aprendizaje de fixes")
    healer.reset_attempts()
    # Simular fix aprendido
    healer._learn_fix("TimeoutError:timed out", "timeout_increased_30_to_60")
    learned = healer._find_learned_fix("TimeoutError:timed out")
    test("fix aprendido encontrado", learned is not None)
    test("fix correcto", learned == "timeout_increased_30_to_60")

    # ── Test 12: Stats ──
    print("\n📌 Test 12: Estadisticas")
    s = healer.stats()
    test("stats tiene total", s["total_healings"] > 0)
    test("stats tiene by_category", len(s["by_category"]) > 0)
    test("stats tiene learned_fixes", s["learned_fixes"] >= 1)
    print(f"      Total: {s['total_healings']}, Success rate: {s['success_rate']}%")
    print(f"      Fixes aprendidos: {s['learned_fixes']}")

    # ── Test 13: Network heal con backoff ──
    print("\n📌 Test 13: Network heal con backoff")
    healer.reset_attempts()
    err13 = ConnectionRefusedError("Connection refused")
    result13 = healer.heal(err13)
    test("network heal fixed (retry)", result13.fixed)
    test("network backoff aplicado", "backoff" in result13.action_taken)

    # ── Cleanup ──
    healer.close()
    os.unlink(test_db)

    print(f"\n{'=' * 70}")
    print(f"RESULTADOS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'=' * 70}\n")

    if failed > 0:
        sys.exit(1)
    print("✅ Self-Healer funcional\n")
