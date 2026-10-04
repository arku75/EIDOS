"""
core/self_sandbox.py — EIDOS experimenta en sí mismo de forma segura.

Entornos de aislamiento disponibles (EIDOS los aprende y elige el mejor):
  - Python venv      → experimentos de código rápidos
  - Docker           → réplica exacta del sistema EIDOS
  - QEMU/KVM         → VM completa (más pesado, máximo aislamiento)
  - unshare/namespaces → contenedor ligero sin Docker
  - LXC/LXD          → si está disponible

EIDOS estudia el clon en NO TOCAR, experimenta en sandbox y aplica si funciona.
"""
import subprocess
import os
from core.db import get_conn
import logging
import time
import json
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List

from core.paths import ARCHIVE_ROOT, EIDOS_HOME, REPO_ROOT, SANDBOX_ROOT

log = logging.getLogger("self_sandbox")
EIDOS_ROOT = REPO_ROOT
NO_TOCAR = ARCHIVE_ROOT
SANDBOX_DIR = SANDBOX_ROOT


# ── Detección de entornos disponibles ─────────────────────────────────────────

def detect_environments() -> Dict[str, Any]:
    """
    Detecta qué entornos de aislamiento están disponibles en el sistema.
    EIDOS aprende estos capacidades y las guarda en su brain.
    """
    envs = {}

    checks = {
        "docker":   "docker --version",
        "podman":   "podman --version",
        "lxc":      "lxc --version",
        "lxd":      "lxd --version",
        "qemu":     "qemu-system-x86_64 --version",
        "kvm":      "ls /dev/kvm",
        "virsh":    "virsh --version",
        "venv":     "python3 -m venv --help",
        "conda":    "conda --version",
        "firejail": "firejail --version",
        "unshare":  "unshare --help",
        "bwrap":    "bwrap --version",
    }

    for name, cmd in checks.items():
        try:
            ret = subprocess.run(cmd.split(), capture_output=True, timeout=5)
            available = ret.returncode == 0
            raw_out = (ret.stdout or ret.stderr).decode(errors="replace")[:100].strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            available = False
            raw_out = ""
        envs[name] = {"available": available, "output": raw_out if available else ""}
        if available:
            log.info("entorno disponible: %s", name)

    # Guardar en brain
    _save_envs_to_brain(envs)
    return envs


def learn_tool_help(tool: str) -> Dict[str, Any]:
    """
    EIDOS aprende un tool ejecutando --help, man y buscando documentación.
    Guarda todo en el brain.
    """
    learned = {}

    # --help
    for flag in ["--help", "-h", "help"]:
        try:
            ret = subprocess.run([tool, flag], capture_output=True, timeout=10, text=True)
            output = (ret.stdout or ret.stderr).strip()
            if output and len(output) > 50:
                learned["help"] = output[:3000]
                break
        except Exception:
            pass

    # man page
    try:
        ret = subprocess.run(["man", tool], capture_output=True, timeout=10,
                             env={**os.environ, "MANPAGER": "cat"}, text=True)
        if ret.returncode == 0 and ret.stdout:
            learned["man"] = ret.stdout.strip()[:3000]
    except Exception:
        pass

    # Guardar en brain
    if learned:
        _save_tool_knowledge(tool, learned)
        log.info("aprendí %s: %d chars de documentación", tool, sum(len(v) for v in learned.values()))

    return {"ok": bool(learned), "tool": tool, "learned": list(learned.keys())}


# ── Python venv sandbox ────────────────────────────────────────────────────────

def create_venv_sandbox(name: str = "eidos_experiment") -> Dict[str, Any]:
    """Crea un venv aislado para experimentos de Python."""
    venv_path = SANDBOX_DIR / "venvs" / name
    venv_path.parent.mkdir(parents=True, exist_ok=True)

    if not venv_path.exists():
        ret = subprocess.run(
            ["python3", "-m", "venv", str(venv_path)],
            capture_output=True, timeout=30, text=True
        )
        if ret.returncode != 0:
            return {"ok": False, "error": ret.stderr}
        log.info("venv creado: %s", venv_path)

    return {"ok": True, "path": str(venv_path), "python": str(venv_path / "bin/python3")}


def run_in_venv(code: str, venv_name: str = "eidos_experiment",
                timeout: int = 30) -> Dict[str, Any]:
    """Ejecuta código Python en el venv sandbox."""
    venv = create_venv_sandbox(venv_name)
    if not venv["ok"]:
        return venv

    python = venv["python"]
    ts = int(time.time())
    script = SANDBOX_DIR / f"script_{ts}.py"
    script.write_text(code)

    try:
        ret = subprocess.run(
            [python, str(script)],
            capture_output=True, timeout=timeout, text=True,
            cwd=str(EIDOS_ROOT)
        )
        result = {
            "ok": ret.returncode == 0,
            "stdout": ret.stdout[:2000],
            "stderr": ret.stderr[:1000],
            "returncode": ret.returncode
        }
        log.info("venv exec: rc=%d, %d chars output", ret.returncode, len(ret.stdout))
        return result
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try: script.unlink()
        except: pass


# ── Docker sandbox ─────────────────────────────────────────────────────────────

DOCKERFILE = """
FROM python:3.13-slim

# Sistema base igual que el de SER
RUN apt-get update && apt-get install -y \\
    git curl wget nano sqlite3 \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /eidos

# Copiar dependencias
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

# Copiar código EIDOS (montado como volumen en runtime)
# Los datos del sandbox NO se persisten en imagen — solo el código

ENV EIDOS_SANDBOX=1
ENV PYTHONPATH=/eidos

CMD ["python3", "-c", "print('EIDOS sandbox listo')"]
"""


def setup_docker_sandbox() -> Dict[str, Any]:
    """Crea la imagen Docker de EIDOS para experimentos."""
    sandbox_dir = SANDBOX_DIR / "docker"
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    # Escribir Dockerfile
    (sandbox_dir / "Dockerfile").write_text(DOCKERFILE)

    # Copiar requirements si existen
    req = EIDOS_ROOT / "requirements.txt"
    if req.exists():
        shutil.copy(req, sandbox_dir / "requirements.txt")
    else:
        (sandbox_dir / "requirements.txt").write_text("")

    # Construir imagen
    log.info("construyendo imagen Docker eidos-sandbox...")
    ret = subprocess.run(
        ["docker", "build", "-t", "eidos-sandbox:latest", "."],
        cwd=str(sandbox_dir),
        capture_output=True, timeout=120, text=True
    )

    if ret.returncode != 0:
        return {"ok": False, "error": ret.stderr[:500]}

    log.info("imagen eidos-sandbox lista")
    return {"ok": True, "image": "eidos-sandbox:latest"}


def run_in_docker(code: str, timeout: int = 60) -> Dict[str, Any]:
    """
    Ejecuta código Python en contenedor Docker con acceso al código EIDOS (readonly).
    El contenedor se destruye al terminar — sin estado persistente.
    """
    ts = int(time.time())
    script_path = SANDBOX_DIR / f"docker_script_{ts}.py"
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    script_path.write_text(code)

    try:
        ret = subprocess.run([
            "docker", "run", "--rm",
            "--network", "host",           # acceso a Ollama local
            "--memory", "512m",            # límite RAM
            "--cpus", "1.0",              # límite CPU
            "-v", f"{EIDOS_ROOT}:/eidos:ro",  # código EIDOS readonly
            "-v", f"{script_path}:/script.py:ro",
            "-e", "PYTHONPATH=/eidos",
            "-e", f"OLLAMA_URL={os.environ.get('OLLAMA_URL', 'http://localhost:11434')}",
            "eidos-sandbox:latest",
            "python3", "/script.py"
        ], capture_output=True, timeout=timeout, text=True)

        return {
            "ok": ret.returncode == 0,
            "stdout": ret.stdout[:2000],
            "stderr": ret.stderr[:500],
            "returncode": ret.returncode,
            "env": "docker"
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"docker timeout ({timeout}s)"}
    except FileNotFoundError:
        return {"ok": False, "error": "docker no disponible — usar venv"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try: script_path.unlink()
        except: pass


# ── unshare sandbox (sin Docker, usando namespaces del kernel) ─────────────────

def run_in_namespace(code: str, timeout: int = 30) -> Dict[str, Any]:
    """
    Ejecuta código en namespace aislado con unshare.
    Más ligero que Docker, sin daemon.
    """
    ts = int(time.time())
    script_path = SANDBOX_DIR / f"ns_script_{ts}.py"
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    script_path.write_text(code)

    try:
        ret = subprocess.run([
            "unshare", "--net", "--pid", "--fork",
            "python3", str(script_path)
        ], capture_output=True, timeout=timeout, text=True,
           env={**os.environ, "PYTHONPATH": str(EIDOS_ROOT)})

        return {
            "ok": ret.returncode == 0,
            "stdout": ret.stdout[:2000],
            "stderr": ret.stderr[:500],
            "env": "namespace"
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try: script_path.unlink()
        except: pass


# ── Auto-estudio del clon NO TOCAR ────────────────────────────────────────────

def study_self_archive() -> Dict[str, Any]:
    """
    EIDOS estudia su propia copia en NO TOCAR.
    Extrae el archivo más reciente en un directorio temporal y lo analiza.
    """
    archives = sorted(NO_TOCAR.glob("EIDOS_*.tar.gz"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not archives:
        archives = sorted(NO_TOCAR.glob("EIDOS*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)

    if not archives:
        return {"ok": False, "error": "No hay archivos en NO TOCAR"}

    archive = archives[0]
    extract_dir = SANDBOX_DIR / "self_study" / archive.stem
    extract_dir.mkdir(parents=True, exist_ok=True)

    log.info("estudiando clon: %s → %s", archive.name, extract_dir)

    # Extraer (si no está ya extraído)
    if not any(extract_dir.iterdir()):
        if archive.suffix == ".gz":
            ret = subprocess.run(
                ["tar", "xzf", str(archive), "-C", str(extract_dir), "--strip-components=1"],
                capture_output=True, timeout=120, text=True
            )
        else:
            ret = subprocess.run(
                ["unzip", "-q", str(archive), "-d", str(extract_dir)],
                capture_output=True, timeout=120, text=True
            )
        if ret.returncode != 0:
            return {"ok": False, "error": ret.stderr[:200]}

    # Analizar diferencias con versión actual
    differences = _compare_with_current(extract_dir)
    stats = _analyze_clone_stats(extract_dir)

    # Guardar en brain
    _save_self_study(archive.name, stats, differences)

    return {
        "ok": True,
        "archive": archive.name,
        "extract_dir": str(extract_dir),
        "stats": stats,
        "differences": differences[:10]
    }


def _compare_with_current(clone_dir: Path) -> List[str]:
    """Compara el clon con el EIDOS actual — encuentra diferencias."""
    diffs = []
    for current_file in EIDOS_ROOT.rglob("*.py"):
        rel = current_file.relative_to(EIDOS_ROOT)
        clone_file = clone_dir / rel
        if not clone_file.exists():
            diffs.append(f"NUEVO en actual: {rel}")
        else:
            try:
                if current_file.read_text() != clone_file.read_text():
                    diffs.append(f"MODIFICADO: {rel}")
            except Exception:
                pass
    return diffs


def _analyze_clone_stats(clone_dir: Path) -> Dict[str, Any]:
    """Estadísticas del clon."""
    py_files = list(clone_dir.rglob("*.py"))
    total_lines = 0
    for f in py_files:
        try:
            total_lines += len(f.read_text().splitlines())
        except Exception:
            pass
    return {
        "python_files": len(py_files),
        "total_lines": total_lines,
        "clone_dir": str(clone_dir)
    }


# ── Helpers brain ─────────────────────────────────────────────────────────────

def _save_envs_to_brain(envs: Dict):
    try:
        db = EIDOS_HOME / "evolution_brain.db"
        c = get_conn(db, timeout=3)
        now = t.time()
        available = [k for k, v in envs.items() if v["available"]]
        content = (
            f"Entornos de aislamiento disponibles en el sistema de SER:\n"
            f"Disponibles: {', '.join(available)}\n\n"
        )
        for name, info in envs.items():
            status = "✅" if info["available"] else "❌"
            content += f"{status} {name}: {info['output'][:60]}\n"
        content += (
            "\nOrden de preferencia para sandboxing:\n"
            "1. Python venv — para experimentos de código rápidos\n"
            "2. Docker — para réplica completa del entorno\n"
            "3. unshare — ligero sin daemon\n"
            "4. QEMU/KVM — máximo aislamiento (VM completa)\n"
            "5. LXC — si está disponible\n"
        )
        existing = c.execute("SELECT id FROM knowledge_nodes WHERE concept='self:sandbox:environments'").fetchone()
        if existing:
            c.execute("UPDATE knowledge_nodes SET definition=?,last_used=? WHERE concept=?",
                      (content, now, 'self:sandbox:environments'))
        else:
            c.execute(
                "INSERT INTO knowledge_nodes (id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
                "VALUES (?,?,?,?,?,?,?,?,1)",
                (str(uuid.uuid4())[:16], 'self:sandbox:environments', content,
                 'eidos_self', 0.99, 'self_sandbox', now, now)
            )
    except Exception:
        pass


def _save_tool_knowledge(tool: str, learned: Dict):
    try:
        db = EIDOS_HOME / "evolution_brain.db"
        c = get_conn(db, timeout=3)
        now = t.time()
        content = f"Documentación de '{tool}':\n\n"
        for section, text in learned.items():
            content += f"=== {section} ===\n{text}\n\n"
        node_id = hashlib.md5(f"tool:{tool}".encode()).hexdigest()[:16]
        c.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (node_id, f"tool:{tool}:docs", content[:4000], 'tool', 0.95, 'self_sandbox', now, now)
        )
    except Exception:
        pass


def _save_self_study(archive_name: str, stats: Dict, diffs: List):
    try:
        db = EIDOS_HOME / "evolution_brain.db"
        c = get_conn(db, timeout=3)
        now = t.time()
        content = (
            f"Auto-estudio del clon NO TOCAR — {archive_name}\n"
            f"Archivos Python: {stats.get('python_files', 0)}\n"
            f"Líneas totales: {stats.get('total_lines', 0)}\n"
            f"Diferencias con versión actual: {len(diffs)} archivos\n\n"
            f"Cambios detectados:\n" + "\n".join(f"- {d}" for d in diffs[:20])
        )
        c.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (str(uuid.uuid4())[:16], 'self:study:clone', content,
             'eidos_self', 0.95, 'self_sandbox', now, now)
        )
    except Exception:
        pass
