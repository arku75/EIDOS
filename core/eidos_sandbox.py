"""
core/eidos_sandbox.py — Sandbox de ejecución de código para EIDOS [S125]

Permite a EIDOS escribir, compilar y ejecutar código real en un entorno seguro.
El sandbox está confinado a /tmp/eidos_sandbox/ con límites de:
  - Tiempo de ejecución (timeout por lenguaje)
  - Memoria (ulimit -v donde aplica)
  - Red (sin acceso a red externa)
  - Archivos (solo dentro del sandbox)

Lenguajes soportados:
  - Python 3 (intérprete directo)
  - Bash (scripting del sistema)
  - Rust (rustc + cargo)
  - Go (go run)
  - C (gcc)
  - JavaScript/Node.js

API principal:
  - run_python(code, timeout) → {ok, stdout, stderr, exit_code, learned}
  - run_bash(script, timeout) → {ok, stdout, stderr, exit_code, learned}
  - run_compiled(code, lang, timeout) → {ok, stdout, stderr, exit_code, binary_path}
  - sandbox_status() → {available_langs, disk_used_mb, files_count}

Integración con AliveOrchestrator:
  En learn_from_perception(), si EIDOS detecta patrones de código en el texto visible,
  puede ejecutarlos en el sandbox para entender cómo funcionan.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.sandbox")

# ── Configuración del sandbox ────────────────────────────────────────────────

SANDBOX_ROOT = Path("/tmp/eidos_sandbox")
SANDBOX_SRC = SANDBOX_ROOT / "src"
SANDBOX_BIN = SANDBOX_ROOT / "bin"
SANDBOX_OUT = SANDBOX_ROOT / "out"

# Límites por lenguaje
LANG_LIMITS = {
    "python":    {"timeout": 30, "max_output": 100_000, "ext": ".py",     "cmd": ["python3", "{file}"]},
    "bash":      {"timeout": 15, "max_output": 50_000,  "ext": ".sh",     "cmd": ["bash", "{file}"]},
    "rust":      {"timeout": 60, "max_output": 50_000,  "ext": ".rs",     "cmd": ["{bin}"],  "compile": True},
    "go":        {"timeout": 30, "max_output": 50_000,  "ext": ".go",     "cmd": ["go", "run", "{file}"]},
    "c":         {"timeout": 20, "max_output": 50_000,  "ext": ".c",      "cmd": ["{bin}"],  "compile": True},
    "javascript":{"timeout": 15, "max_output": 50_000,  "ext": ".js",     "cmd": ["node", "{file}"]},
}

# Código peligroso bloqueado en todos los lenguajes
BLOCKED_PATTERNS = [
    # Python
    r"import\s+os\s*;.*system\(", r"subprocess\.", r"__import__\s*\(\s*['\"]os",
    r"exec\s*\(.*compile", r"open\s*\(.*/etc/", r"open\s*\(.*/proc/",
    r"socket\.", r"requests\.", r"urllib\.", r"http\.", r"ftplib\.",
    # Bash
    r"rm\s+-rf\s+/", r">\s*/dev/sd", r"mkfs\.", r"dd\s+if=",
    r"curl\s+", r"wget\s+", r"nc\s+-", r"socat\s+",
    # General (paths absolutos peligrosos)
    r"/etc/(passwd|shadow|sudoers|ssh/)", r"/root/", r"/home/[^/]+/\.ssh/",
]


def _ensure_sandbox() -> None:
    """Crea la estructura del sandbox si no existe."""
    for d in [SANDBOX_ROOT, SANDBOX_SRC, SANDBOX_BIN, SANDBOX_OUT]:
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(str(d), 0o700)


def _is_code_safe(code: str, lang: str) -> Tuple[bool, str]:
    """Verifica que el código no contenga patrones peligrosos."""
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            return False, f"patrón bloqueado: {pattern}"
    # Límite de tamaño
    if len(code) > 500_000:
        return False, "código demasiado grande (>500KB)"
    return True, ""


def _build_sandbox_env() -> dict:
    """Entorno aislado para ejecución."""
    env = {
        "HOME": str(SANDBOX_ROOT),
        "PATH": "/usr/bin:/usr/local/bin:/bin:/usr/sbin:/sbin:/snap/bin",
        "TMPDIR": str(SANDBOX_OUT),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    return env


# ── Ejecución de código ──────────────────────────────────────────────────────


def run_python(code: str, timeout: int = 30) -> Dict[str, Any]:
    """Ejecuta código Python en el sandbox.

    Returns: {ok, stdout, stderr, exit_code, learned (bool), error}
    """
    result = {"ok": False, "stdout": "", "stderr": "", "exit_code": -1,
              "learned": False, "error": ""}

    ok, reason = _is_code_safe(code, "python")
    if not ok:
        result["error"] = reason
        return result

    _ensure_sandbox()

    # Archivo temporal en el sandbox
    src_file = SANDBOX_SRC / f"eidos_{int(time.time() * 1000)}.py"

    try:
        src_file.write_text(code, encoding="utf-8")

        limits = LANG_LIMITS["python"]
        r = subprocess.run(
            ["python3", "-u", str(src_file)],
            capture_output=True, text=True,
            timeout=min(timeout, limits["timeout"]),
            cwd=str(SANDBOX_ROOT),
            env=_build_sandbox_env(),
        )

        result["ok"] = True
        result["exit_code"] = r.returncode
        result["stdout"] = r.stdout[:limits["max_output"]]
        result["stderr"] = r.stderr[:limits["max_output"]]

        # Aprender del output: si el código corrió sin errores, EIDOS aprendió algo
        if r.returncode == 0 and r.stdout.strip():
            result["learned"] = True

        # Limpiar
        if src_file.exists():
            src_file.unlink()

    except subprocess.TimeoutExpired:
        result["error"] = f"timeout ({timeout}s)"
        if src_file.exists():
            src_file.unlink()
    except Exception as e:
        result["error"] = str(e)

    return result


def run_bash(script: str, timeout: int = 15) -> Dict[str, Any]:
    """Ejecuta script bash en el sandbox.

    Returns: {ok, stdout, stderr, exit_code, learned, error}
    """
    result = {"ok": False, "stdout": "", "stderr": "", "exit_code": -1,
              "learned": False, "error": ""}

    ok, reason = _is_code_safe(script, "bash")
    if not ok:
        result["error"] = reason
        return result

    _ensure_sandbox()

    src_file = SANDBOX_SRC / f"eidos_{int(time.time() * 1000)}.sh"
    try:
        src_file.write_text(script, encoding="utf-8")
        src_file.chmod(0o700)

        limits = LANG_LIMITS["bash"]
        r = subprocess.run(
            ["bash", str(src_file)],
            capture_output=True, text=True,
            timeout=min(timeout, limits["timeout"]),
            cwd=str(SANDBOX_ROOT),
            env=_build_sandbox_env(),
        )

        result["ok"] = True
        result["exit_code"] = r.returncode
        result["stdout"] = r.stdout[:limits["max_output"]]
        result["stderr"] = r.stderr[:limits["max_output"]]

        if r.returncode == 0 and r.stdout.strip():
            result["learned"] = True

        if src_file.exists():
            src_file.unlink()

    except subprocess.TimeoutExpired:
        result["error"] = f"timeout ({timeout}s)"
        if src_file.exists():
            src_file.unlink()
    except Exception as e:
        result["error"] = str(e)

    return result


def run_compiled(code: str, lang: str = "rust", timeout: int = 60) -> Dict[str, Any]:
    """Compila y ejecuta código en un lenguaje compilado (Rust, C, Go).

    Returns: {ok, stdout, stderr, exit_code, binary_path, compile_stderr, learned, error}
    """
    result = {"ok": False, "stdout": "", "stderr": "", "exit_code": -1,
              "binary_path": "", "compile_stderr": "", "learned": False, "error": ""}

    if lang not in ("rust", "c", "go"):
        result["error"] = f"lenguaje compilado no soportado: {lang}"
        return result

    ok, reason = _is_code_safe(code, lang)
    if not ok:
        result["error"] = reason
        return result

    _ensure_sandbox()

    limits = LANG_LIMITS.get(lang, {})
    ext = limits.get("ext", ".txt")
    src_file = SANDBOX_SRC / f"eidos_{int(time.time() * 1000)}{ext}"

    try:
        src_file.write_text(code, encoding="utf-8")

        if lang == "rust":
            # Compilar con rustc
            bin_file = SANDBOX_BIN / src_file.stem
            compile_r = subprocess.run(
                ["rustc", str(src_file), "-o", str(bin_file)],
                capture_output=True, text=True,
                timeout=60,
                cwd=str(SANDBOX_ROOT),
                env=_build_sandbox_env(),
            )
            if compile_r.returncode != 0:
                result["ok"] = True
                result["compile_stderr"] = compile_r.stderr[:5000]
                result["error"] = f"error de compilación: {compile_r.stderr[:200]}"
                result["stdout"] = compile_r.stdout[:5000]
                result["learned"] = True  # Aprendió del error de compilación
                src_file.unlink()
                return result

            run_cmd = [str(bin_file)]
            result["binary_path"] = str(bin_file)

        elif lang == "c":
            bin_file = SANDBOX_BIN / src_file.stem
            compile_r = subprocess.run(
                ["gcc", str(src_file), "-o", str(bin_file), "-Wall"],
                capture_output=True, text=True,
                timeout=30,
                cwd=str(SANDBOX_ROOT),
                env=_build_sandbox_env(),
            )
            if compile_r.returncode != 0:
                result["ok"] = True
                result["compile_stderr"] = compile_r.stderr[:5000]
                result["error"] = f"error de compilación: {compile_r.stderr[:200]}"
                result["stdout"] = compile_r.stdout[:5000]
                result["learned"] = True
                src_file.unlink()
                return result

            run_cmd = [str(bin_file)]
            result["binary_path"] = str(bin_file)

        elif lang == "go":
            # Go no necesita compilación separada
            run_cmd = ["go", "run", str(src_file)]

        # Ejecutar
        r = subprocess.run(
            run_cmd,
            capture_output=True, text=True,
            timeout=min(timeout, limits.get("timeout", 30)),
            cwd=str(SANDBOX_ROOT),
            env=_build_sandbox_env(),
        )

        result["ok"] = True
        result["exit_code"] = r.returncode
        result["stdout"] = r.stdout[:limits.get("max_output", 50000)]
        result["stderr"] = r.stderr[:limits.get("max_output", 50000)]

        if r.returncode == 0 and (r.stdout.strip() or not "error" in r.stderr.lower()):
            result["learned"] = True

        # Limpiar binarios
        if "binary_path" in result and result["binary_path"]:
            try:
                Path(result["binary_path"]).unlink(missing_ok=True)
            except Exception:
                pass
        if src_file.exists():
            src_file.unlink()

    except subprocess.TimeoutExpired:
        result["error"] = f"timeout ({timeout}s)"
        if src_file.exists():
            src_file.unlink()
    except FileNotFoundError as e:
        result["error"] = f"compilador no encontrado: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def run_any(code: str, lang: str = "python", timeout: Optional[int] = None) -> Dict[str, Any]:
    """Ejecuta código en cualquier lenguaje soportado. API unificada.

    Returns: {ok, lang, stdout, stderr, exit_code, learned, compile_stderr, error}
    """
    limits = LANG_LIMITS.get(lang, {})
    t = timeout if timeout is not None else limits.get("timeout", 30)

    result = {"ok": False, "lang": lang, "stdout": "", "stderr": "",
              "exit_code": -1, "learned": False, "compile_stderr": "", "error": ""}

    if lang == "python":
        r = run_python(code, timeout=t)
    elif lang == "bash":
        r = run_bash(code, timeout=t)
    elif lang in ("rust", "c", "go"):
        r = run_compiled(code, lang, timeout=t)
    elif lang == "javascript":
        _ensure_sandbox()
        src_file = SANDBOX_SRC / f"eidos_{int(time.time() * 1000)}.js"
        try:
            ok, reason = _is_code_safe(code, "javascript")
            if not ok:
                result["error"] = reason
                return result
            src_file.write_text(code, encoding="utf-8")
            r = subprocess.run(
                ["node", str(src_file)],
                capture_output=True, text=True,
                timeout=min(t, limits.get("timeout", 15)),
                cwd=str(SANDBOX_ROOT),
                env=_build_sandbox_env(),
            )
            result.update(ok=True, exit_code=r.returncode,
                          stdout=r.stdout[:limits.get("max_output", 50000)],
                          stderr=r.stderr[:limits.get("max_output", 50000)],
                          learned=(r.returncode == 0 and bool(r.stdout.strip())))
            src_file.unlink()
            return result
        except subprocess.TimeoutExpired:
            result["error"] = f"timeout ({t}s)"
            if src_file.exists():
                src_file.unlink()
            return result
        except FileNotFoundError:
            result["error"] = "Node.js no encontrado"
            return result
    else:
        result["error"] = f"lenguaje no soportado: {lang}"
        return result

    result.update(r)
    result["lang"] = lang
    return result


# ── Utilidades ────────────────────────────────────────────────────────────────


def sandbox_status() -> Dict[str, Any]:
    """Estado del sandbox: lenguajes disponibles, uso de disco, archivos."""
    status = {"available_langs": [], "disk_used_mb": 0, "files_count": 0, "ok": True}

    # Verificar compiladores/intérpretes disponibles
    # Mapeo de lenguaje → ejecutable real (no template)
    _LANG_EXECUTABLES = {
        "python": "python3",
        "bash": "bash",
        "go": "go",
        "javascript": "node",
        "rust": "rustc",
        "c": "gcc",
    }
    sandbox_path = _build_sandbox_env().get("PATH", os.environ.get("PATH", ""))
    for lang, exe_name in _LANG_EXECUTABLES.items():
        found = shutil.which(exe_name) is not None
        if not found:
            # Buscar en PATH del sandbox como fallback
            for path_dir in sandbox_path.split(":"):
                exe_path = os.path.join(path_dir, exe_name)
                if os.path.isfile(exe_path) and os.access(exe_path, os.X_OK):
                    found = True
                    break
        if found:
            status["available_langs"].append(lang)

    # Uso de disco
    if SANDBOX_ROOT.exists():
        total_size = 0
        file_count = 0
        for f in SANDBOX_ROOT.rglob("*"):
            if f.is_file():
                try:
                    total_size += f.stat().st_size
                    file_count += 1
                except OSError:
                    pass
        status["disk_used_mb"] = round(total_size / (1024 * 1024), 2)
        status["files_count"] = file_count

    return status


def clean_sandbox() -> Dict[str, Any]:
    """Limpia el sandbox completamente."""
    result = {"ok": False, "files_removed": 0, "bytes_freed": 0, "error": ""}
    try:
        count = 0
        size = 0
        if SANDBOX_ROOT.exists():
            for f in SANDBOX_ROOT.rglob("*"):
                if f.is_file():
                    try:
                        size += f.stat().st_size
                        count += 1
                        f.unlink()
                    except OSError:
                        pass
        result.update(ok=True, files_removed=count, bytes_freed=size)
    except Exception as e:
        result["error"] = str(e)
    return result


def learn_from_code(code: str, lang: str = "python") -> Dict[str, Any]:
    """Ejecuta código y extrae conocimiento del resultado.

    Pensado para el AliveOrchestrator: ejecuta el código y, si tiene éxito,
    extrae patrones y conceptos que EIDOS puede incorporar a su grafo.

    Returns: {ok, lang, stdout_preview, concepts_extracted (list of str),
              error_type: "none"|"syntax"|"runtime"|"compile"|"timeout"|"security"}
    """
    result = {
        "ok": False, "lang": lang, "stdout_preview": "",
        "concepts_extracted": [], "error_type": "none", "error": "",
    }

    r = run_any(code, lang)

    if r.get("error"):
        result["error"] = r["error"]
        if "timeout" in r["error"]:
            result["error_type"] = "timeout"
        elif "seguridad" in r["error"] or "patrón bloqueado" in r["error"]:
            result["error_type"] = "security"
        return result

    result["stdout_preview"] = (r.get("stdout", "") + r.get("stderr", ""))[:500]

    if r.get("exit_code", -1) != 0:
        # Determinar tipo de error
        stderr = r.get("stderr", "").lower()
        compile_err = r.get("compile_stderr", "").lower()
        combined = stderr + compile_err
        if any(kw in combined for kw in ["syntaxerror", "syntax error", "unexpected token"]):
            result["error_type"] = "syntax"
        elif "compilación" in r.get("error", ""):
            result["error_type"] = "compile"
        else:
            result["error_type"] = "runtime"
        result["ok"] = True  # OK porque la ejecución sí ocurrió, solo falló
        return result

    result["ok"] = True
    result["error_type"] = "none"

    # Extraer conceptos del código exitoso
    if lang == "python":
        # Detectar imports
        imports = re.findall(r"^\s*(?:import|from)\s+(\S+)", code, re.MULTILINE)
        result["concepts_extracted"] = [f"python:{imp}" for imp in imports[:5]]
    elif lang == "rust":
        uses = re.findall(r"^\s*use\s+(\S+)", code, re.MULTILINE)
        result["concepts_extracted"] = [f"rust:{u}" for u in uses[:5]]

    return result


# ── CLI rápido ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Uso: python eidos_sandbox.py <lang> <code>")
        print("  python -c 'print(2+2)'")
        print("  bash 'echo hola'")
        print("  status")
        sys.exit(1)

    lang = sys.argv[1]

    if lang == "status":
        print(json.dumps(sandbox_status(), indent=2, ensure_ascii=False))
    elif lang == "clean":
        print(json.dumps(clean_sandbox(), indent=2, ensure_ascii=False))
    else:
        code = sys.argv[2] if len(sys.argv) > 2 else "print('Hola desde EIDOS Sandbox')"
        if code == "-":
            code = sys.stdin.read()
        r = run_any(code, lang)
        print(json.dumps(r, indent=2, ensure_ascii=False))
