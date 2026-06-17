"""
EIDOS core/wasm_sandbox.py — Secure Execution Sandbox
======================================================
Sandbox de ejecución aislada inspirado en IronClaw (Rust WASM sandbox).

Como WASM real requiere compilación compleja, implementa sandbox con:
  - Subprocess con resource limits (Linux rlimit)
  - Timeout estricto (configurable, default 30s)
  - Memory limit (configurable, default 256MB)
  - No network access (unshare --net)
  - Filesystem restringido (tmpdir aislado)
  - Whitelist de syscalls conceptual

Fallback chain:
  1. Docker (si disponible) → máximo aislamiento
  2. unshare + resource limits → aislamiento Linux nativo
  3. subprocess + rlimit → mínimo viable

Capability system: cada ejecución tiene permisos explícitos.
Log de ejecuciones en SQLite ~/.eidos/sandbox.db

Uso:
    from core.wasm_sandbox import WASMSandbox, SandboxCapabilities

    sandbox = WASMSandbox()

    # Ejecución básica
    result = sandbox.execute("print('hello')")
    print(result.stdout, result.exit_code)

    # Con capabilities personalizadas
    caps = SandboxCapabilities(can_network=False, max_memory_mb=128, max_time_s=10)
    result = sandbox.execute("import os; print(os.listdir('.'))", capabilities=caps)

    # Ejecutar archivo
    result = sandbox.execute_file("/tmp/script.py", timeout=60)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from core.db import get_conn, get_conn_ctx
import subprocess
import tempfile
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger("eidos.wasm_sandbox")

DB_PATH = Path.home() / ".eidos" / "sandbox.db"


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class SandboxBackend(Enum):
    DOCKER = "docker"
    UNSHARE = "unshare"
    SUBPROCESS = "subprocess"


@dataclass
class SandboxCapabilities:
    """Permisos explícitos para una ejecución sandboxed."""
    can_network: bool = False
    can_filesystem: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    can_subprocess: bool = False
    max_memory_mb: int = 256
    max_time_s: int = 30

    def to_dict(self) -> dict:
        return {
            "can_network": self.can_network,
            "can_filesystem": self.can_filesystem,
            "allowed_paths": self.allowed_paths,
            "can_subprocess": self.can_subprocess,
            "max_memory_mb": self.max_memory_mb,
            "max_time_s": self.max_time_s,
        }


@dataclass
class SandboxResult:
    """Resultado de una ejecución sandboxed."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    execution_time: float = 0.0
    memory_used_mb: float = 0.0
    backend: str = "unknown"
    timed_out: bool = False
    killed: bool = False
    error: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.killed

    def to_dict(self) -> dict:
        return {
            "stdout": self.stdout[:2000],
            "stderr": self.stderr[:2000],
            "exit_code": self.exit_code,
            "execution_time": round(self.execution_time, 3),
            "memory_used_mb": round(self.memory_used_mb, 2),
            "backend": self.backend,
            "timed_out": self.timed_out,
            "killed": self.killed,
            "success": self.success,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  SYSCALL WHITELIST (conceptual — para documentación de seguridad)
# ═══════════════════════════════════════════════════════════════════════════════

SYSCALL_WHITELIST_DEFAULT = frozenset({
    "read", "write", "open", "close", "stat", "fstat", "lstat",
    "poll", "lseek", "mmap", "mprotect", "munmap", "brk",
    "access", "pipe", "select", "sched_yield", "mremap",
    "clone", "execve", "exit", "wait4", "uname", "fcntl",
    "flock", "fsync", "fdatasync", "truncate", "ftruncate",
    "getcwd", "chdir", "mkdir", "rmdir", "unlink", "readlink",
    "gettimeofday", "getrlimit", "getuid", "getgid",
    "clock_gettime", "clock_nanosleep", "exit_group",
    "openat", "newfstatat", "readlinkat", "faccessat",
    "set_tid_address", "set_robust_list", "futex",
    "getrandom", "memfd_create", "copy_file_range",
})

SYSCALL_DENY_DANGEROUS = frozenset({
    "mount", "umount", "reboot", "init_module", "delete_module",
    "pivot_root", "swapon", "swapoff", "kexec_load",
    "ptrace", "process_vm_readv", "process_vm_writev",
    "kcmp", "userfaultfd",
})


# ═══════════════════════════════════════════════════════════════════════════════
#  CODE SANITIZER — pre-check antes de ejecutar
# ═══════════════════════════════════════════════════════════════════════════════

_DANGEROUS_IMPORTS = frozenset({
    "ctypes", "subprocess", "shutil", "signal",
    "multiprocessing", "socket", "http", "urllib",
    "requests", "ftplib", "smtplib", "telnetlib",
})

_DANGEROUS_CALLS = [
    "os.system(", "os.popen(", "os.exec",
    "os.remove(", "os.unlink(", "os.rmdir(",
    "shutil.rmtree(", "__import__(",
    "eval(", "exec(", "compile(",
    "open('/etc/", "open('/dev/",
]


def _sanitize_code(code: str, capabilities: SandboxCapabilities) -> tuple[bool, str]:
    """
    Pre-check de código antes de ejecución.
    Returns: (safe, reason)
    """
    if not code or not code.strip():
        return False, "Empty code"

    code_lower = code.lower()

    # Check dangerous calls
    if not capabilities.can_subprocess:
        for call in _DANGEROUS_CALLS:
            if call.lower() in code_lower:
                return False, f"Blocked call: {call}"

    # Check network imports
    if not capabilities.can_network:
        for imp in ("socket", "http", "urllib", "requests", "ftplib", "smtplib"):
            if f"import {imp}" in code or f"from {imp}" in code:
                return False, f"Network import blocked: {imp}"

    # Check filesystem access outside allowed paths
    if not capabilities.can_filesystem:
        for pattern in ("open('/", 'open("/'):
            if pattern in code:
                # Check if path is in allowed_paths
                allowed = False
                for ap in capabilities.allowed_paths:
                    if ap in code:
                        allowed = True
                        break
                if not allowed:
                    return False, "Filesystem access blocked"

    return True, "OK"


# ═══════════════════════════════════════════════════════════════════════════════
#  RESOURCE LIMIT WRAPPER — genera script wrapper con rlimit
# ═══════════════════════════════════════════════════════════════════════════════

_RLIMIT_WRAPPER_TEMPLATE = '''
import resource
import sys
import os
from core.db import get_conn
from core.db import get_conn_ctx

# Memory limit: {memory_mb} MB
mem_bytes = {memory_mb} * 1024 * 1024
try:
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
except (ValueError, resource.error):
    pass  # Some systems don't support RLIMIT_AS

# CPU time limit: {time_s} seconds
try:
    resource.setrlimit(resource.RLIMIT_CPU, ({time_s}, {time_s}))
except (ValueError, resource.error):
    pass

# No core dumps
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except (ValueError, resource.error):
    pass

# Max file size: 10 MB
try:
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
except (ValueError, resource.error):
    pass

# Max open files: 64
try:
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
except (ValueError, resource.error):
    pass

# Change to temp dir
os.chdir("{workdir}")

# Execute user code
{code}
'''


# ═══════════════════════════════════════════════════════════════════════════════
#  WASM SANDBOX
# ═══════════════════════════════════════════════════════════════════════════════

class WASMSandbox:
    """
    Sandbox de ejecución aislada para código arbitrario.

    Fallback chain:
      1. Docker → máximo aislamiento
      2. unshare → aislamiento Linux nativo (requiere root/capabilities)
      3. subprocess + rlimit → mínimo viable
    """

    DEFAULT_CAPS = SandboxCapabilities()

    def __init__(self, db_path: str = None, preferred_backend: str = None):
        self.db_path = db_path or str(DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._exec_count = 0

        # Detectar backend disponible
        self._backend = self._detect_backend(preferred_backend)
        log.info(f"🏗️  [WASMSandbox] Backend: {self._backend.value}")

    def _detect_backend(self, preferred: str = None) -> SandboxBackend:
        """Detecta el mejor backend disponible."""
        if preferred:
            try:
                return SandboxBackend(preferred)
            except ValueError:
                pass

        # Docker check
        if shutil.which("docker"):
            try:
                r = subprocess.run(
                    ["docker", "info"], capture_output=True, timeout=5
                )
                if r.returncode == 0:
                    return SandboxBackend.DOCKER
            except Exception:
                pass  # error no crítico, continuar
        # Unshare check
        if shutil.which("unshare"):
            return SandboxBackend.UNSHARE

        # Fallback
        return SandboxBackend.SUBPROCESS

    def _init_db(self):
        """Crea tabla de log de ejecuciones."""
        with get_conn_ctx(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    language TEXT DEFAULT 'python',
                    code_hash TEXT,
                    exit_code INTEGER,
                    execution_time REAL,
                    memory_used_mb REAL,
                    backend TEXT,
                    timed_out INTEGER DEFAULT 0,
                    killed INTEGER DEFAULT 0,
                    stdout_preview TEXT,
                    stderr_preview TEXT,
                    capabilities TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_exec_ts ON executions(timestamp);
            """)

    def _log_execution(self, result: SandboxResult, language: str,
                       code_hash: str, capabilities: SandboxCapabilities):
        """Registra ejecución en SQLite."""
        try:
            with get_conn_ctx(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO executions
                       (timestamp, language, code_hash, exit_code, execution_time,
                        memory_used_mb, backend, timed_out, killed,
                        stdout_preview, stderr_preview, capabilities)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (time.time(), language, code_hash, result.exit_code,
                     result.execution_time, result.memory_used_mb,
                     result.backend, int(result.timed_out), int(result.killed),
                     result.stdout[:500], result.stderr[:500],
                     json.dumps(capabilities.to_dict()))
                )
        except Exception as e:
            log.warning(f"[WASMSandbox] Log failed: {e}")

    # ── Ejecución principal ──────────────────────────────────────────────

    def execute(self, code: str, language: str = "python",
                timeout: int = None, memory_mb: int = None,
                capabilities: SandboxCapabilities = None) -> SandboxResult:
        """
        Ejecuta código en sandbox aislado.

        Args:
            code: Código a ejecutar
            language: Lenguaje (solo 'python' soportado actualmente)
            timeout: Timeout en segundos (override de capabilities)
            memory_mb: Memory limit en MB (override de capabilities)
            capabilities: Permisos de ejecución

        Returns:
            SandboxResult con stdout, stderr, exit_code, etc.
        """
        caps = capabilities or self.DEFAULT_CAPS
        if timeout is not None:
            caps = SandboxCapabilities(
                can_network=caps.can_network, can_filesystem=caps.can_filesystem,
                allowed_paths=caps.allowed_paths, can_subprocess=caps.can_subprocess,
                max_memory_mb=memory_mb or caps.max_memory_mb,
                max_time_s=timeout,
            )
        elif memory_mb is not None:
            caps = SandboxCapabilities(
                can_network=caps.can_network, can_filesystem=caps.can_filesystem,
                allowed_paths=caps.allowed_paths, can_subprocess=caps.can_subprocess,
                max_memory_mb=memory_mb,
                max_time_s=caps.max_time_s,
            )

        if language != "python":
            return SandboxResult(
                error=f"Language '{language}' not supported yet (only python)",
                exit_code=1, backend=self._backend.value,
            )

        # Pre-check
        safe, reason = _sanitize_code(code, caps)
        if not safe:
            return SandboxResult(
                stderr=f"Code sanitization failed: {reason}",
                exit_code=1, backend="sanitizer",
            )

        code_hash = str(hash(code) % 999999999)

        # Dispatch to backend
        if self._backend == SandboxBackend.DOCKER:
            result = self._execute_docker(code, caps)
        elif self._backend == SandboxBackend.UNSHARE:
            result = self._execute_unshare(code, caps)
        else:
            result = self._execute_subprocess(code, caps)

        self._exec_count += 1
        self._log_execution(result, language, code_hash, caps)

        if result.success:
            log.info(f"🏗️  [Sandbox] ✅ exit=0 time={result.execution_time:.2f}s")
        else:
            log.warning(f"🏗️  [Sandbox] ❌ exit={result.exit_code} stderr={result.stderr[:100]}")

        return result

    def execute_file(self, path: str, timeout: int = 30,
                     capabilities: SandboxCapabilities = None) -> SandboxResult:
        """
        Ejecuta un archivo Python en sandbox.

        Args:
            path: Path al archivo a ejecutar
            timeout: Timeout en segundos
            capabilities: Permisos de ejecución

        Returns:
            SandboxResult
        """
        filepath = Path(path)
        if not filepath.exists():
            return SandboxResult(
                stderr=f"File not found: {path}",
                exit_code=1, backend="file_check",
            )
        if not filepath.suffix in (".py", ".pyw"):
            return SandboxResult(
                stderr=f"Unsupported file type: {filepath.suffix} (only .py)",
                exit_code=1, backend="file_check",
            )

        try:
            code = filepath.read_text(encoding="utf-8")
        except Exception as e:
            return SandboxResult(
                stderr=f"Cannot read file: {e}",
                exit_code=1, backend="file_check",
            )

        return self.execute(code, timeout=timeout, capabilities=capabilities)

    # ── Backend: Docker ──────────────────────────────────────────────────

    def _execute_docker(self, code: str, caps: SandboxCapabilities) -> SandboxResult:
        """Ejecuta en contenedor Docker aislado."""
        result = SandboxResult(backend="docker")
        tmpdir = tempfile.mkdtemp(prefix="eidos_sandbox_")

        try:
            # Escribir código a archivo temporal
            code_file = os.path.join(tmpdir, "run.py")
            with open(code_file, "w") as f:
                f.write(code)

            # Construir comando docker
            docker_cmd = [
                "docker", "run", "--rm",
                "--memory", f"{caps.max_memory_mb}m",
                "--memory-swap", f"{caps.max_memory_mb}m",
                "--cpus", "1",
                "--pids-limit", "50",
                "--read-only",
                "--tmpfs", "/tmp:size=10m",
                "--no-healthcheck",
                "-v", f"{code_file}:/sandbox/run.py:ro",
                "-w", "/sandbox",
            ]

            # Network isolation
            if not caps.can_network:
                docker_cmd.extend(["--network", "none"])

            # Security options
            docker_cmd.extend([
                "--security-opt", "no-new-privileges",
                "--cap-drop", "ALL",
            ])

            docker_cmd.extend([
                "python:3.11-slim",
                "python", "/sandbox/run.py",
            ])

            t0 = time.time()
            proc = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=caps.max_time_s + 5,  # +5s for container overhead
            )
            result.execution_time = time.time() - t0
            result.stdout = proc.stdout
            result.stderr = proc.stderr
            result.exit_code = proc.returncode

        except subprocess.TimeoutExpired:
            result.timed_out = True
            result.exit_code = 124
            result.stderr = f"Timeout after {caps.max_time_s}s"
        except Exception as e:
            result.error = str(e)
            result.exit_code = 1
            result.stderr = f"Docker execution failed: {e}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return result

    # ── Backend: unshare ─────────────────────────────────────────────────

    def _execute_unshare(self, code: str, caps: SandboxCapabilities) -> SandboxResult:
        """Ejecuta con unshare para aislamiento de namespaces."""
        result = SandboxResult(backend="unshare")
        tmpdir = tempfile.mkdtemp(prefix="eidos_sandbox_")

        try:
            # Generar wrapper con rlimits
            wrapped = _RLIMIT_WRAPPER_TEMPLATE.format(
                memory_mb=caps.max_memory_mb,
                time_s=caps.max_time_s,
                workdir=tmpdir,
                code=code,
            )
            code_file = os.path.join(tmpdir, "run.py")
            with open(code_file, "w") as f:
                f.write(wrapped)

            # Construir comando unshare
            unshare_cmd = ["unshare", "--map-root-user"]

            # Network namespace (no network)
            if not caps.can_network:
                unshare_cmd.append("--net")

            # PID namespace
            unshare_cmd.append("--pid")
            unshare_cmd.append("--fork")

            # Mount namespace (filesystem isolation)
            unshare_cmd.append("--mount")

            unshare_cmd.extend(["python3", code_file])

            t0 = time.time()
            proc = subprocess.run(
                unshare_cmd,
                capture_output=True,
                text=True,
                timeout=caps.max_time_s + 2,
                cwd=tmpdir,
            )
            result.execution_time = time.time() - t0
            result.stdout = proc.stdout
            result.stderr = proc.stderr
            result.exit_code = proc.returncode

        except subprocess.TimeoutExpired:
            result.timed_out = True
            result.exit_code = 124
            result.stderr = f"Timeout after {caps.max_time_s}s"
        except PermissionError:
            # unshare sin privilegios → fallback a subprocess
            log.info("🏗️  [Sandbox] unshare needs privileges, falling back to subprocess")
            shutil.rmtree(tmpdir, ignore_errors=True)
            return self._execute_subprocess(code, caps)
        except Exception as e:
            result.error = str(e)
            result.exit_code = 1
            result.stderr = f"Unshare execution failed: {e}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return result

    # ── Backend: subprocess (fallback mínimo) ────────────────────────────

    def _execute_subprocess(self, code: str, caps: SandboxCapabilities) -> SandboxResult:
        """Ejecuta con subprocess + rlimit (mínimo viable)."""
        result = SandboxResult(backend="subprocess")
        tmpdir = tempfile.mkdtemp(prefix="eidos_sandbox_")

        try:
            # Generar wrapper con rlimits
            wrapped = _RLIMIT_WRAPPER_TEMPLATE.format(
                memory_mb=caps.max_memory_mb,
                time_s=caps.max_time_s,
                workdir=tmpdir,
                code=code,
            )
            code_file = os.path.join(tmpdir, "run.py")
            with open(code_file, "w") as f:
                f.write(wrapped)

            # Entorno limpio
            clean_env = {
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": tmpdir,
                "TMPDIR": tmpdir,
                "LANG": "C.UTF-8",
            }

            t0 = time.time()
            proc = subprocess.run(
                ["python3", code_file],
                capture_output=True,
                text=True,
                timeout=caps.max_time_s,
                cwd=tmpdir,
                env=clean_env,
            )
            result.execution_time = time.time() - t0
            result.stdout = proc.stdout
            result.stderr = proc.stderr
            result.exit_code = proc.returncode

            # Estimar memoria usada (heurístico)
            try:
                import resource as res_mod
                usage = res_mod.getrusage(res_mod.RUSAGE_CHILDREN)
                result.memory_used_mb = usage.ru_maxrss / 1024.0  # KB → MB
            except Exception:
                pass  # error no crítico, continuar
        except subprocess.TimeoutExpired:
            result.timed_out = True
            result.exit_code = 124
            result.stderr = f"Timeout after {caps.max_time_s}s"
        except Exception as e:
            result.error = str(e)
            result.exit_code = 1
            result.stderr = f"Subprocess execution failed: {e}"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return result

    # ── Utilidades ───────────────────────────────────────────────────────

    def get_execution_history(self, limit: int = 20) -> list[dict]:
        """Obtiene historial de ejecuciones recientes."""
        with get_conn_ctx(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM executions
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Estadísticas del sandbox."""
        with get_conn_ctx(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
            success = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE exit_code = 0"
            ).fetchone()[0]
            timeouts = conn.execute(
                "SELECT COUNT(*) FROM executions WHERE timed_out = 1"
            ).fetchone()[0]
            avg_time = conn.execute(
                "SELECT AVG(execution_time) FROM executions WHERE exit_code = 0"
            ).fetchone()[0] or 0.0

        return {
            "backend": self._backend.value,
            "total_executions": total,
            "successful": success,
            "timeouts": timeouts,
            "success_rate": round(success / max(total, 1), 3),
            "avg_execution_time": round(avg_time, 3),
            "session_executions": self._exec_count,
        }

    def cleanup_old_logs(self, max_age_days: int = 30) -> int:
        """Limpia logs de ejecución antiguos."""
        cutoff = time.time() - (max_age_days * 86400)
        with get_conn_ctx(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM executions WHERE timestamp < ?", (cutoff,)
            )
            return cur.rowcount

    @property
    def backend(self) -> str:
        return self._backend.value

    def __repr__(self) -> str:
        return f"WASMSandbox(backend={self._backend.value}, execs={self._exec_count})"


# ═══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_sandbox: Optional[WASMSandbox] = None


def get_sandbox() -> WASMSandbox:
    """Obtiene la instancia singleton del sandbox."""
    global _sandbox
    if _sandbox is None:
        _sandbox = WASMSandbox()
    return _sandbox


# ═══════════════════════════════════════════════════════════════════════════════
#  TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("=" * 60)
    print("  EIDOS WASMSandbox — Test Suite")
    print("=" * 60)

    sb = WASMSandbox(db_path="/tmp/eidos_sandbox_test.db")
    print(f"\n🏗️  Backend detectado: {sb.backend}")
    passed = 0
    total = 0

    # Test 1: Ejecución simple
    total += 1
    r = sb.execute("print('Hello from sandbox!')")
    ok = "Hello from sandbox!" in r.stdout
    print(f"\n{'✅' if ok else '❌'} Test 1 — Ejecución simple: exit={r.exit_code}, stdout={r.stdout.strip()!r}")
    if ok:
        passed += 1

    # Test 2: Error en código
    total += 1
    r = sb.execute("raise ValueError('test error')")
    ok = r.exit_code != 0 and "ValueError" in r.stderr
    print(f"{'✅' if ok else '❌'} Test 2 — Error handling: exit={r.exit_code}, stderr contains ValueError={ok}")
    if ok:
        passed += 1

    # Test 3: Timeout
    total += 1
    r = sb.execute("import time; time.sleep(60)", timeout=2)
    ok = r.timed_out or r.exit_code != 0
    print(f"{'✅' if ok else '❌'} Test 3 — Timeout (2s): timed_out={r.timed_out}, exit={r.exit_code}")
    if ok:
        passed += 1

    # Test 4: Code sanitization — network blocked
    total += 1
    r = sb.execute("import socket; s = socket.socket()")
    ok = r.exit_code != 0 or "blocked" in r.stderr.lower()
    print(f"{'✅' if ok else '❌'} Test 4 — Network block: exit={r.exit_code}")
    if ok:
        passed += 1

    # Test 5: Capabilities personalizadas
    total += 1
    caps = SandboxCapabilities(max_memory_mb=64, max_time_s=5)
    r = sb.execute("print(2 + 2)", capabilities=caps)
    ok = r.success and "4" in r.stdout
    print(f"{'✅' if ok else '❌'} Test 5 — Custom capabilities: success={r.success}, stdout={r.stdout.strip()!r}")
    if ok:
        passed += 1

    # Test 6: Multi-line code
    total += 1
    code = """
def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

result = fibonacci(10)
print(f"fib(10) = {result}")
"""
    r = sb.execute(code)
    ok = r.success and "55" in r.stdout
    print(f"{'✅' if ok else '❌'} Test 6 — Multi-line fibonacci: success={r.success}, stdout={r.stdout.strip()!r}")
    if ok:
        passed += 1

    # Test 7: Execute file
    total += 1
    tmpf = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
    tmpf.write("print('file execution works')\n")
    tmpf.close()
    r = sb.execute_file(tmpf.name, timeout=10)
    ok = r.success and "file execution works" in r.stdout
    print(f"{'✅' if ok else '❌'} Test 7 — Execute file: success={r.success}")
    os.unlink(tmpf.name)
    if ok:
        passed += 1

    # Test 8: Stats
    total += 1
    stats = sb.get_stats()
    ok = stats["total_executions"] >= 7
    print(f"{'✅' if ok else '❌'} Test 8 — Stats: {stats['total_executions']} executions logged")
    if ok:
        passed += 1

    # Test 9: Execution history
    total += 1
    history = sb.get_execution_history(limit=5)
    ok = len(history) > 0
    print(f"{'✅' if ok else '❌'} Test 9 — History: {len(history)} entries")
    if ok:
        passed += 1

    # Test 10: Empty code
    total += 1
    r = sb.execute("")
    ok = r.exit_code != 0
    print(f"{'✅' if ok else '❌'} Test 10 — Empty code rejected: exit={r.exit_code}")
    if ok:
        passed += 1

    # Cleanup
    try:
        os.unlink("/tmp/eidos_sandbox_test.db")
    except Exception:
        pass  # error no crítico, continuar
    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{total} passed")
    print(f"{'=' * 60}")
