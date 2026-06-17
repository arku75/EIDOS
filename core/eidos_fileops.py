"""
core/eidos_fileops.py — EIDOS crea/lee/edita archivos y scripts [S122 Fase 3]
============================================================================
SER dio acceso SIN LÍMITES (crear/editar dentro y fuera del sandbox) pero pidió
"sin romper mi PC". Por eso: acceso total CON cortafuegos anti-destrucción que
SOLO bloquea rutas de sistema y secretos críticos (no limita el trabajo útil).

Capacidades: write_file, read_file, append_file, mkdir, list_dir, run_script.
Workspace por defecto: ~/.eidos/workspace (pero puede escribir donde haga falta).
"""
from __future__ import annotations

import os
import subprocess
import logging
from pathlib import Path
from typing import Dict

log = logging.getLogger("eidos.fileops")

WORKSPACE = Path.home() / ".eidos" / "workspace"

# Rutas PROHIBIDAS para escritura (cortafuegos anti-destrucción del sistema).
_FORBIDDEN_WRITE = (
    "/etc", "/boot", "/sys", "/proc", "/dev", "/run", "/usr", "/bin", "/sbin",
    "/lib", "/lib64", "/var/lib/dpkg", "/var/lib/apt",
)
# Archivos críticos de EIDOS/SER que NUNCA se sobrescriben.
_FORBIDDEN_FILES = (
    str(Path.home() / ".eidos" / "secrets.env"),
    str(Path.home() / ".ssh"),
    "constitution.toml",
    str(Path.home() / ".eidos" / "owner_policy.toml"),
)


def _resolve(path: str) -> Path:
    """Resuelve la ruta. Relativa → dentro del workspace."""
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = WORKSPACE / p
    return p


def _safe_write_target(path: Path) -> tuple[bool, str]:
    """¿Es seguro ESCRIBIR aquí? (no toca sistema ni secretos)."""
    rp = str(path.resolve())
    for f in _FORBIDDEN_WRITE:
        if rp == f or rp.startswith(f + "/"):
            return False, f"ruta de sistema protegida ({f})"
    for f in _FORBIDDEN_FILES:
        if rp == f or rp.startswith(f + "/") or path.name in ("secrets.env", "constitution.toml"):
            return False, "archivo crítico protegido (secrets/constitution/ssh)"
    return True, ""


def write_file(path: str, content: str, overwrite: bool = True) -> Dict:
    """Crea/escribe un archivo. Crea directorios padre. Cortafuegos anti-sistema."""
    target = _resolve(path)
    ok, reason = _safe_write_target(target)
    if not ok:
        return {"ok": False, "error": f"⛔ {reason}", "path": str(target)}
    if target.exists() and not overwrite:
        return {"ok": False, "error": "ya existe (overwrite=False)", "path": str(target)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        log.info("escrito %s (%d bytes)", target, len(content))
        return {"ok": True, "path": str(target), "bytes": len(content)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "path": str(target)}


def read_file(path: str, max_bytes: int = 100_000) -> Dict:
    target = _resolve(path)
    try:
        if not target.exists():
            return {"ok": False, "error": "no existe", "path": str(target)}
        data = target.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
        return {"ok": True, "path": str(target), "content": data, "bytes": len(data)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "path": str(target)}


def append_file(path: str, content: str) -> Dict:
    target = _resolve(path)
    ok, reason = _safe_write_target(target)
    if not ok:
        return {"ok": False, "error": f"⛔ {reason}", "path": str(target)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(content)
        return {"ok": True, "path": str(target)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "path": str(target)}


def mkdir(path: str) -> Dict:
    target = _resolve(path)
    ok, reason = _safe_write_target(target)
    if not ok:
        return {"ok": False, "error": f"⛔ {reason}"}
    try:
        target.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(target)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def list_dir(path: str = "") -> Dict:
    target = _resolve(path) if path else WORKSPACE
    try:
        if not target.exists():
            return {"ok": False, "error": "no existe", "path": str(target)}
        items = sorted(os.listdir(target))[:200]
        return {"ok": True, "path": str(target), "items": items, "count": len(items)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def run_script(path: str = "", code: str = "", lang: str = "python",
               timeout: int = 30) -> Dict:
    """Ejecuta un script (archivo o código inline). Python o bash.
    Bloquea patrones destructivos (defensa en profundidad)."""
    blob = code or (read_file(path).get("content", "") if path else "")
    low = blob.lower()
    for bad in ("rm -rf /", "mkfs", "dd if=", "dd of=/dev", ":(){", "shutdown",
                "reboot", "> /dev/sd", "wipefs"):
        if bad in low:
            return {"ok": False, "error": f"⛔ bloqueado (patrón '{bad}')"}
    try:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        if path and not code:
            cmd = ["python3", str(_resolve(path))] if lang == "python" else ["bash", str(_resolve(path))]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               cwd=str(WORKSPACE))
        else:
            interp = "python3" if lang == "python" else "bash"
            r = subprocess.run([interp, "-c", blob], capture_output=True, text=True,
                               timeout=timeout, cwd=str(WORKSPACE))
        out = (r.stdout or "")[:4000]
        err = (r.stderr or "")[:2000]
        return {"ok": r.returncode == 0, "returncode": r.returncode,
                "stdout": out, "stderr": err}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout {timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("workspace:", WORKSPACE)
    print(write_file("test_eidos.py", "print('hola desde EIDOS fileops')\n"))
    print(run_script("test_eidos.py"))
    print(read_file("test_eidos.py")["content"][:50])
    # prueba cortafuegos
    print("anti-destrucción:", write_file("/etc/passwd", "x")["error"])
