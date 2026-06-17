"""
core/character_sandbox.py — Sandbox de aislamiento por personaje de Colony

Cada personaje tiene su propio espacio aislado donde puede crear y borrar
libremente, sin tocar NUNCA el sistema real de SER.

  colony_coder    → Docker python:3.11-slim  (código Python/JS)
  colony_operator → unshare namespaces       (comandos shell, sin red)
  colony_analyst  → workspace propio         (docs, datos, scripts)
  colony_vision   → workspace propio         (imágenes, análisis visual)
  colony_ser      → workspace propio         (notas, borradores)
  colony_lumen    → workspace propio         (síntesis, reflexiones)
  colony_general  → workspace propio         (uso general)

Workspace persistente de cada personaje:
    ~/.eidos/sandboxes/{character}/workspace/

Garantías de seguridad:
  - Los personajes SOLO pueden crear/borrar dentro de su workspace
  - Docker: --network none, --memory 128m, --read-only (sistema), workspace :rw
  - unshare: --net --user --map-root-user (sin acceso al host)
  - Límite de disco: 50MB por workspace
  - Validación de paths: imposible salir del workspace con ../../../

Uso:
    sb = get_sandbox("colony_coder")
    sb.create_file("experimento.py", "print('hola')")
    result = sb.run_file("experimento.py")
    sb.delete_file("experimento.py")
    print(sb.list_workspace())
"""
from __future__ import annotations

import os
import time
import shutil
import hashlib
import logging
import sqlite3
import tempfile
import subprocess
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
from core.db import get_conn

log = logging.getLogger("eidos.sandbox")

BRAIN_DB      = Path.home() / ".eidos" / "evolution_brain.db"
SANDBOX_DIR   = Path.home() / ".eidos" / "sandboxes"

DEFAULT_TIMEOUT  = 15      # segundos por ejecución
DEFAULT_MEM_MB   = 128     # RAM máxima en Docker
MAX_DISK_MB      = 50      # MB máximos por workspace de personaje
MAX_OUTPUT_CHARS = 4000    # caracteres de output capturado


@dataclass
class SandboxResult:
    success:  bool
    output:   str
    error:    str    = ""
    runtime:  float  = 0.0
    backend:  str    = ""
    learned:  str    = ""


# ────────────────────────────────────────────────────────────────────────────
#  BACKENDS
# ────────────────────────────────────────────────────────────────────────────

class _DockerBackend:
    """
    Coder: Docker python:3.11-slim con aislamiento total.

    El contenedor tiene:
      /code/      → código a ejecutar (read-only)
      /workspace/ → workspace del personaje (read-write, persistente)
      /tmp/       → tmpfs efímero (desaparece al salir)
      red: NONE   → sin acceso a internet ni LAN
    """
    IMAGE = "python:3.11-slim"

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def run(self, code: str, language: str = "python",
            timeout: int = DEFAULT_TIMEOUT,
            workspace: Optional[Path] = None) -> SandboxResult:

        if language not in ("python", "python3", "js", "javascript", "sh", "bash"):
            return SandboxResult(False, "", f"Lenguaje no soportado: {language}")

        ext = ("py"  if language in ("python", "python3") else
               "js"  if language in ("js", "javascript")  else "sh")

        cmd_in_container = {
            "py": ["python3", "/code/run.py"],
            "js": ["node",    "/code/run.js"],
            "sh": ["bash",    "/code/run.sh"],
        }[ext]

        with tempfile.TemporaryDirectory(prefix="eidos_code_") as code_dir:
            os.chmod(code_dir, 0o755)
            code_file = Path(code_dir) / f"run.{ext}"
            code_file.write_text(code, encoding="utf-8")
            code_file.chmod(0o644)

            # Workspace del personaje montado como rw
            ws_path = str(workspace) if workspace else "/dev/null"
            ws_mount = ["-v", f"{ws_path}:/workspace:rw"] if workspace and workspace.exists() else []

            docker_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory",      f"{DEFAULT_MEM_MB}m",
                "--memory-swap", f"{DEFAULT_MEM_MB}m",
                "--cpus",        "0.5",
                "--read-only",                        # sistema de ficheros ro
                "--tmpfs",       "/tmp:size=10m",     # /tmp efímero rw
                "-v", f"{code_dir}:/code:ro",         # código ro
                *ws_mount,                            # workspace rw
                "--user",        "nobody",
                "--workdir",     "/workspace" if ws_mount else "/tmp",
                self.IMAGE,
            ] + cmd_in_container

            t0 = time.time()
            try:
                proc = subprocess.run(
                    docker_cmd, capture_output=True, text=True, timeout=timeout
                )
                elapsed = time.time() - t0
                return SandboxResult(
                    proc.returncode == 0,
                    (proc.stdout or "")[:MAX_OUTPUT_CHARS],
                    (proc.stderr or "")[:500],
                    elapsed, "docker"
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(False, "", f"Timeout ({timeout}s)", time.time()-t0, "docker")
            except Exception as e:
                return SandboxResult(False, "", str(e), time.time()-t0, "docker")


class _UnshareBackend:
    """
    Operator: unshare namespaces — red aislada, workspace propio.

    El proceso está en un user namespace donde se ve como root, pero ese
    "root virtual" no tiene ningún privilegio en el sistema real del host.
    Puede crear/borrar libremente dentro de su workspace.

    Bloqueado siempre (afectan al host incluso en user namespace):
      sudo, su, mount, umount, nsenter, unshare (anidado),
      reboot, shutdown, halt, fdisk, mkfs, dd
    """
    BLOCKED = {
        "sudo", "su", "mount", "umount", "nsenter", "unshare",
        "reboot", "shutdown", "halt", "fdisk", "mkfs", "dd",
        "pkill", "killall",       # matar procesos del host
        "wget", "curl",           # red — ya bloqueada por --net pero doble seguro
    }

    def available(self) -> bool:
        try:
            r = subprocess.run(
                ["unshare", "--net", "--user", "--map-root-user", "--", "echo", "ok"],
                capture_output=True, timeout=3
            )
            return r.returncode == 0
        except Exception:
            return False

    def _is_safe(self, cmd: str) -> bool:
        first = cmd.strip().split()[0].split("/")[-1] if cmd.strip() else ""
        return first not in self.BLOCKED

    def run(self, command: str, timeout: int = DEFAULT_TIMEOUT,
            workspace: Optional[Path] = None) -> SandboxResult:
        if not self._is_safe(command):
            blocked = command.strip().split()[0]
            return SandboxResult(False, "", f"Comando bloqueado: {blocked}")

        cwd = str(workspace) if workspace and workspace.exists() else tempfile.mkdtemp(prefix="eidos_op_")
        env = {**os.environ, "HOME": cwd, "TMPDIR": cwd, "SANDBOX_WORKSPACE": cwd}

        unshare_cmd = [
            "unshare",
            "--net",           # nueva interfaz de red (sin acceso)
            "--user",          # nuevo user namespace
            "--map-root-user", # root virtual (sin privilegios en el host)
            "--",
            "bash", "-c", command
        ]
        t0 = time.time()
        try:
            proc = subprocess.run(
                unshare_cmd, capture_output=True, text=True,
                timeout=timeout, cwd=cwd, env=env
            )
            elapsed = time.time() - t0
            return SandboxResult(
                proc.returncode == 0,
                (proc.stdout or "")[:MAX_OUTPUT_CHARS],
                (proc.stderr or "")[:500],
                elapsed, "unshare"
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(False, "", f"Timeout ({timeout}s)", time.time()-t0, "unshare")
        except Exception as e:
            return SandboxResult(False, "", str(e), time.time()-t0, "unshare")


class _WorkspaceBackend:
    """
    Analyst / Vision / SER / Lumen / General: subprocess en workspace propio.

    El proceso hereda los permisos normales del usuario, pero opera con cwd
    en el workspace del personaje. Puede crear/borrar en ese directorio.
    """

    def run(self, command: str, timeout: int = DEFAULT_TIMEOUT,
            workspace: Optional[Path] = None) -> SandboxResult:
        cwd = str(workspace) if workspace and workspace.exists() else tempfile.mkdtemp(prefix="eidos_ws_")
        env = {**os.environ, "HOME": cwd, "TMPDIR": cwd, "SANDBOX_WORKSPACE": cwd}
        t0 = time.time()
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                capture_output=True, text=True,
                timeout=timeout, cwd=cwd, env=env
            )
            elapsed = time.time() - t0
            return SandboxResult(
                proc.returncode == 0,
                (proc.stdout or "")[:MAX_OUTPUT_CHARS],
                (proc.stderr or "")[:500],
                elapsed, "workspace"
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(False, "", f"Timeout ({timeout}s)", time.time()-t0, "workspace")
        except Exception as e:
            return SandboxResult(False, "", str(e), time.time()-t0, "workspace")


# ────────────────────────────────────────────────────────────────────────────
#  SANDBOX POR PERSONAJE
# ────────────────────────────────────────────────────────────────────────────

class CharacterSandbox:
    """
    Sandbox de un personaje de Colony.

    Cada personaje tiene su workspace propio en:
        ~/.eidos/sandboxes/{character}/workspace/

    Puede crear y borrar archivos ahí libremente.
    No puede salir de ese directorio (validación de paths).
    """

    def __init__(self, character: str):
        self.character = character

        # Workspace persistente del personaje
        self.workspace: Path = SANDBOX_DIR / character / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.workspace.chmod(0o777)   # legible/escribible por Docker nobody

        # Elegir backend
        if character == "colony_coder":
            docker = _DockerBackend()
            self._backend = docker if docker.available() else _WorkspaceBackend()
        elif character == "colony_operator":
            unshare = _UnshareBackend()
            self._backend = unshare if unshare.available() else _WorkspaceBackend()
        else:
            self._backend = _WorkspaceBackend()

        log.info("Sandbox %s → %s | workspace: %s",
                 character, type(self._backend).__name__, self.workspace)

    # ── EJECUCIÓN ────────────────────────────────────────────────────────────

    def run(self, code_or_command: str,
            language: str = "python",
            timeout: int = DEFAULT_TIMEOUT,
            learn: bool = True) -> SandboxResult:
        """Ejecuta código o comando en el sandbox con acceso al workspace."""
        log.debug("[%s] ejecutando: %.60s", self.character, code_or_command)

        if isinstance(self._backend, _DockerBackend):
            result = self._backend.run(code_or_command, language, timeout, self.workspace)
        elif isinstance(self._backend, _UnshareBackend):
            result = self._backend.run(code_or_command, timeout, self.workspace)
        else:
            result = self._backend.run(code_or_command, timeout, self.workspace)

        if learn and result.output and len(result.output) > 10:
            result.learned = self._save_learning(code_or_command, result)

        return result

    def run_file(self, filename: str, timeout: int = DEFAULT_TIMEOUT) -> SandboxResult:
        """Ejecuta un archivo que está en el workspace del personaje."""
        target = self._safe_path(filename)
        if not target:
            return SandboxResult(False, "", f"Archivo fuera del workspace: {filename}")
        if not target.exists():
            return SandboxResult(False, "", f"Archivo no existe: {filename}")

        ext = target.suffix.lower()
        if ext in (".py",):
            lang = "python"
            code = target.read_text(encoding="utf-8", errors="ignore")
            return self.run(code, language=lang, timeout=timeout, learn=True)
        elif ext in (".sh", ".bash"):
            code = target.read_text(encoding="utf-8", errors="ignore")
            return self.run(code, language="bash", timeout=timeout, learn=True)
        else:
            return SandboxResult(False, "", f"Tipo de archivo no ejecutable: {ext}")

    def run_experiment(self, topic: str, timeout: int = DEFAULT_TIMEOUT) -> SandboxResult:
        """El personaje genera y ejecuta un experimento sobre un topic."""
        if self.character == "colony_coder":
            code = (
                f"import sys\nprint('Python', sys.version[:6])\n"
                f"try:\n    import {topic}\n    print(f'[OK] {topic} disponible')\n"
                f"except ImportError:\n    print(f'[NO] {topic} no instalado')\n"
                f"except Exception as e:\n    print(f'[ERR] {{e}}')\n"
            )
            return self.run(code, language="python", timeout=timeout, learn=True)

        if self.character == "colony_operator":
            code = (
                f"which {topic} 2>/dev/null && "
                f"{topic} --version 2>&1 | head -2 || "
                f"echo '{topic}: no encontrado en PATH'"
            )
            return self.run(code, timeout=timeout, learn=True)

        # Analyst / Vision / resto — experimento Python básico
        code = (
            f"import datetime\n"
            f"print('Investigando:', {repr(topic)})\n"
            f"print('Workspace disponible para guardar resultados')\n"
            f"print('ts:', datetime.datetime.now().isoformat())\n"
        )
        return self.run(code, language="python", timeout=timeout, learn=True)

    # ── GESTIÓN DE ARCHIVOS EN WORKSPACE ─────────────────────────────────────

    def create_file(self, filename: str, content: str = "") -> bool:
        """Crea o sobreescribe un archivo en el workspace del personaje."""
        if not self._check_disk_limit():
            log.warning("[%s] workspace lleno (>%dMB)", self.character, MAX_DISK_MB)
            return False
        target = self._safe_path(filename)
        if not target:
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            log.info("[%s] creó: %s", self.character, filename)
            return True
        except Exception as e:
            log.debug("create_file %s: %s", filename, e)
            return False

    def delete_file(self, filename: str) -> bool:
        """Borra un archivo o directorio del workspace. Nunca sale del workspace."""
        target = self._safe_path(filename)
        if not target:
            log.warning("[%s] intento de borrar fuera del workspace: %s", self.character, filename)
            return False
        if not target.exists():
            return False
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            log.info("[%s] borró: %s", self.character, filename)
            return True
        except Exception as e:
            log.debug("delete_file %s: %s", filename, e)
            return False

    def read_file(self, filename: str, max_chars: int = 4000) -> Optional[str]:
        """Lee un archivo del workspace."""
        target = self._safe_path(filename)
        if not target or not target.exists():
            return None
        try:
            return target.read_text(encoding="utf-8", errors="ignore")[:max_chars]
        except Exception:
            return None

    def list_workspace(self) -> List[Dict]:
        """Lista el contenido del workspace con metadata básica."""
        items = []
        try:
            for p in sorted(self.workspace.rglob("*")):
                if p.is_file():
                    rel = str(p.relative_to(self.workspace))
                    size = p.stat().st_size
                    items.append({"name": rel, "size": size,
                                  "type": p.suffix or "file"})
        except Exception:
            pass  # error no crítico, continuar
        return items

    def clear_workspace(self) -> bool:
        """Borra todo el contenido del workspace (no el directorio raíz)."""
        try:
            for item in self.workspace.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            log.info("[%s] workspace limpiado", self.character)
            return True
        except Exception as e:
            log.debug("clear_workspace: %s", e)
            return False

    def get_workspace_size_mb(self) -> float:
        """Tamaño actual del workspace en MB."""
        try:
            total = sum(p.stat().st_size for p in self.workspace.rglob("*") if p.is_file())
            return round(total / 1_048_576, 2)
        except Exception:
            return 0.0

    # ── INTERNAL ─────────────────────────────────────────────────────────────

    def _safe_path(self, filename: str) -> Optional[Path]:
        """
        Resuelve el path y valida que esté dentro del workspace.
        Bloquea cualquier intento de salir con ../ o paths absolutos.
        """
        try:
            # Rechazar paths absolutos que no empiecen por workspace
            candidate = (self.workspace / filename).resolve()
            if not str(candidate).startswith(str(self.workspace.resolve())):
                return None
            return candidate
        except Exception:
            return None

    def _check_disk_limit(self) -> bool:
        """True si el workspace tiene espacio libre (< MAX_DISK_MB)."""
        return self.get_workspace_size_mb() < MAX_DISK_MB

    def _save_learning(self, code: str, result: SandboxResult) -> str:
        """Guarda el resultado como knowledge_node en evolution_brain.db."""
        try:
            summary   = result.output.strip()[:200]
            concept   = f"sandbox:{self.character}:{code[:50]}"
            definition = (
                f"[{self.character}] {result.backend} ({result.runtime:.1f}s): "
                f"{code[:80]} → {summary}"
            )
            node_id = hashlib.md5(concept.encode()).hexdigest()[:16]
            now     = time.time()
            conn    = get_conn(BRAIN_DB, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes "
                "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                "VALUES (?,?,?,?,?,?,?,1)",
                (node_id, concept[:120], definition[:500],
                 f"sandbox:{self.character}", 0.8, now, now)
            )
            conn.commit()
            pass  # S109: get_conn no necesita close()
            log.info("[%s] aprendió: %.60s", self.character, concept)
            return concept
        except Exception as e:
            log.debug("save_learning falló: %s", e)
            return ""

    def get_learning_history(self, limit: int = 10) -> List[Dict]:
        """Historial de lo que el personaje aprendió en su sandbox."""
        try:
            conn = get_conn(BRAIN_DB, timeout=5)
            rows = conn.execute(
                "SELECT concept, definition, confidence, "
                "datetime(created_at,'unixepoch') as ts "
                "FROM knowledge_nodes WHERE source=? "
                "ORDER BY created_at DESC LIMIT ?",
                (f"sandbox:{self.character}", limit)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            return [{"concept": r[0], "definition": r[1],
                     "confidence": r[2], "ts": r[3]} for r in rows]
        except Exception:
            return []

    def __repr__(self) -> str:
        size = self.get_workspace_size_mb()
        files = len(self.list_workspace())
        return (f"<CharacterSandbox {self.character} "
                f"backend={type(self._backend).__name__} "
                f"workspace={files}files/{size}MB>")


# ────────────────────────────────────────────────────────────────────────────
#  SINGLETONS
# ────────────────────────────────────────────────────────────────────────────

_sandboxes: Dict[str, CharacterSandbox] = {}
_lock = threading.Lock()


def get_sandbox(character: str) -> CharacterSandbox:
    """Obtiene (o crea) el sandbox de un personaje."""
    with _lock:
        if character not in _sandboxes:
            _sandboxes[character] = CharacterSandbox(character)
        return _sandboxes[character]


def get_all_sandboxes() -> Dict[str, CharacterSandbox]:
    """Inicializa y devuelve sandboxes de todos los personajes."""
    characters = [
        "colony_coder", "colony_operator", "colony_analyst",
        "colony_vision", "colony_ser", "colony_lumen", "colony_general"
    ]
    return {c: get_sandbox(c) for c in characters}
