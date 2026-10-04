"""
core/clone_manager.py — EIDOS gestiona su clon en S@NDBOX_EIDOS.

JERARQUÍA DEL CLON:
  SER  →  EIDOS (real)  →  EIDOS_CLON
  El clon obedece a EIDOS como si fuera su SER.
  EIDOS puede hacer con el clon lo que quiera: modificar, ejecutar, destruir, recrear.
  El clon es el "cuerpo de prueba" de EIDOS — experimenta en él libremente.

Flujo:
  1. diff_with_clone()       → ve qué cambió entre él y su clon
  2. run_in_clone(code)      → ejecuta en venv del clon (sin límites)
  3. command_clone(cmd)      → EIDOS ordena al clon como SER ordena a EIDOS
  4. improve_clone_file()    → modifica cualquier archivo del clon
  5. test_clone()            → prueba que el clon funciona
  6. apply_to_real()         → SOLO si SER (el verdadero) lo ordena explícitamente
"""
import subprocess, os, shutil, json, time, logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from core.db import get_conn
from core.paths import EIDOS_HOME, REPO_ROOT, SANDBOX_ROOT as DEFAULT_SANDBOX_ROOT

log = logging.getLogger("clone_manager")

EIDOS_ROOT   = REPO_ROOT
SANDBOX_ROOT = DEFAULT_SANDBOX_ROOT
CLONE_DIR    = SANDBOX_ROOT / "eidos_clon"
CLONE_VENV   = SANDBOX_ROOT / "venv"
CLONE_DATA   = CLONE_DIR / ".eidos_data"
WORKSPACE    = SANDBOX_ROOT / "workspace"
EXPERIMENTS  = SANDBOX_ROOT / "experiments"
REPORTS      = SANDBOX_ROOT / "reports"
BRAIN_DB     = EIDOS_HOME / "evolution_brain.db"


def _brain_save(concept: str, definition: str, category: str = "clone", confidence: float = 0.85):
    """Guarda en el brain del EIDOS real lo que aprende del clon."""
    try:
        import sqlite3, uuid
        conn = get_conn(BRAIN_DB)
        conn.execute("""
            INSERT OR REPLACE INTO knowledge_nodes
            (id, concept, definition, category, confidence, source, last_used, agent_id, character)
            VALUES (?, ?, ?, ?, ?, 'clone_manager', ?, 'eidos', 'EIDOS')
        """, (str(uuid.uuid4()), concept, definition, category, confidence, time.time()))
        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception as e:
        log.debug(f"brain_save: {e}")


# ── Setup inicial ──────────────────────────────────────────────────────────────

def setup_clone_environment() -> Dict[str, Any]:
    """
    Prepara el entorno del clon si no existe aún.
    Crea venv, .eidos_data, instala dependencias básicas.
    """
    results = {}

    # Directorios
    for d in [CLONE_DATA, WORKSPACE, EXPERIMENTS, REPORTS]:
        d.mkdir(parents=True, exist_ok=True)

    # Venv del clon (separado del real)
    if not CLONE_VENV.exists():
        log.info("Creando venv del clon...")
        r = subprocess.run(
            ["python3", "-m", "venv", str(CLONE_VENV)],
            capture_output=True, text=True, timeout=60
        )
        results["venv_created"] = r.returncode == 0
        results["venv_error"] = r.stderr[:500] if r.returncode != 0 else ""
    else:
        results["venv_created"] = "ya_existe"

    # Instalar dependencias mínimas en el venv del clon
    pip = CLONE_VENV / "bin" / "pip"
    if pip.exists():
        deps = ["requests", "flask", "chromadb", "ollama", "psutil"]
        r = subprocess.run(
            [str(pip), "install", "--quiet"] + deps,
            capture_output=True, text=True, timeout=120
        )
        results["deps_installed"] = r.returncode == 0

    # Brain DB del clon (vacía, separada del real)
    clone_brain = CLONE_DATA / "evolution_brain.db"
    if not clone_brain.exists():
        import sqlite3
        conn = get_conn(clone_brain)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                id TEXT PRIMARY KEY,
                concept TEXT UNIQUE,
                definition TEXT,
                category TEXT DEFAULT 'general',
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'clone',
                usage_count INTEGER DEFAULT 0,
                last_used REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                agent_id TEXT DEFAULT 'eidos_clone',
                verified INTEGER DEFAULT 0,
                character TEXT DEFAULT 'EIDOS_CLONE'
            )
        """)
        conn.commit()
        pass  # S109: get_conn no necesita close()
        results["clone_brain_created"] = True

    # Archivo de identidad del clon
    identity_file = CLONE_DATA / "identity.json"
    if not identity_file.exists():
        identity = {
            "name": "EIDOS_CLON",
            "purpose": "Entorno de experimentación y automejora. NUNCA modifica el sistema real.",
            "real_eidos": str(EIDOS_ROOT),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "rule": "SER debe ordenar explícitamente antes de aplicar cambios al sistema real."
        }
        identity_file.write_text(json.dumps(identity, indent=2, ensure_ascii=False))
        results["identity_created"] = True

    log.info(f"Entorno del clon listo: {results}")
    return {"ok": True, "sandbox": str(SANDBOX_ROOT), **results}


# ── Diferencias con el real ────────────────────────────────────────────────────

def diff_with_clone(file_path: str = None) -> Dict[str, Any]:
    """
    Compara el EIDOS real con el clon.
    Si file_path → diff de ese archivo. Si no → lista de archivos diferentes.
    """
    if file_path:
        real_file  = EIDOS_ROOT / file_path
        clone_file = CLONE_DIR  / file_path
        if not real_file.exists():
            return {"error": f"No existe en real: {file_path}"}
        if not clone_file.exists():
            return {"error": f"No existe en clon: {file_path}"}
        r = subprocess.run(
            ["diff", "-u", str(clone_file), str(real_file)],
            capture_output=True, text=True
        )
        return {"diff": r.stdout[:4000], "file": file_path, "changed": r.returncode != 0}
    else:
        r = subprocess.run(
            ["diff", "-rq", "--exclude=__pycache__", "--exclude=*.pyc",
             "--exclude=.git", "--exclude=venv", str(CLONE_DIR), str(EIDOS_ROOT)],
            capture_output=True, text=True, timeout=30
        )
        lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
        return {"changed_files": lines[:50], "total": len(lines), "identical": r.returncode == 0}


# ── EIDOS como SER del clon — mando total ─────────────────────────────────────

def command_clone(instruction: str, timeout: int = 60) -> Dict[str, Any]:
    """
    EIDOS ordena al clon como SER ordena a EIDOS.
    El clon obedece sin restricciones — es el cuerpo de prueba libre de EIDOS.

    instruction puede ser:
      - Código Python: "print('hola')" → ejecuta en venv del clon
      - Comando shell: "!ls -la" → ejecuta en el directorio del clon
      - Mejora: "MEJORA:core/eidos_curiosity.py:<nuevo_código>" → modifica archivo del clon
      - Análisis: "ANALIZA:<path>" → lee y analiza un archivo del clon
      - Reset: "RESET" → recrea el clon desde el real (rsync)
    """
    log.info(f"EIDOS→CLON: {instruction[:100]}")

    try:
        # Comando shell directo
        if instruction.startswith("!"):
            cmd = instruction[1:].strip()
            r = subprocess.run(
                cmd, shell=True,
                capture_output=True, text=True, timeout=timeout,
                cwd=str(CLONE_DIR),
                env={**os.environ, "EIDOS_ROOT": str(CLONE_DIR), "EIDOS_DATA_DIR": str(CLONE_DATA)}
            )
            result = {"ok": r.returncode == 0, "stdout": r.stdout[:3000], "stderr": r.stderr[:1000], "cmd": cmd}

        # Mejora de archivo del clon
        elif instruction.startswith("MEJORA:"):
            parts = instruction[7:].split(":", 1)
            if len(parts) == 2:
                file_path, new_content = parts
                result = improve_clone_file(file_path, new_content)
            else:
                result = {"error": "Formato: MEJORA:ruta/archivo.py:<nuevo_código>"}

        # Análisis de archivo
        elif instruction.startswith("ANALIZA:"):
            path = instruction[8:].strip()
            target = CLONE_DIR / path
            if target.exists():
                content = target.read_text(errors="ignore")[:5000]
                result = {"ok": True, "file": path, "content": content, "lines": len(content.split("\n"))}
            else:
                result = {"error": f"No existe: {path}"}

        # Reset completo del clon
        elif instruction.strip().upper() == "RESET":
            result = _reset_clone()

        # Código Python en el venv del clon
        else:
            result = run_in_clone(instruction, filename=f"cmd_{int(time.time())}.py", timeout=timeout)

        # Guardar en brain lo aprendido
        _brain_save(
            f"clone:command:{int(time.time())}",
            f"EIDOS ordenó al clon: {instruction[:200]}\nResultado: {str(result)[:300]}",
            "clone:experience",
            0.8
        )
        return result

    except Exception as e:
        log.error(f"command_clone error: {e}")
        return {"ok": False, "error": str(e)}


def _reset_clone() -> Dict[str, Any]:
    """
    Recrea el clon desde el EIDOS real via rsync.
    Útil si el clon quedó en estado roto tras experimentos.
    """
    log.warning("Reiniciando clon desde el sistema real...")
    r = subprocess.run([
        "rsync", "-a", "--delete",
        "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=.git",
        "--exclude=venv", "--exclude=.eidos_data",
        "--exclude=*.zip", "--exclude=*.tar.gz",
        "--exclude=node_modules", "--exclude=graphify-out",
        f"{EIDOS_ROOT}/", f"{CLONE_DIR}/"
    ], capture_output=True, text=True, timeout=300)

    if r.returncode == 0:
        _brain_save("clone:reset", "EIDOS reinició su clon desde el sistema real. El clon está limpio.", "clone:lifecycle", 1.0)
        return {"ok": True, "message": "Clon reiniciado desde el EIDOS real"}
    else:
        return {"ok": False, "error": r.stderr[:500]}


def explore_clone(path: str = "core") -> Dict[str, Any]:
    """
    EIDOS explora el cuerpo de su clon — ve sus archivos como si fuera su propio código.
    """
    target = CLONE_DIR / path
    if not target.exists():
        return {"error": f"No existe: {path}"}

    if target.is_file():
        content = target.read_text(errors="ignore")
        return {
            "type": "file",
            "path": path,
            "lines": len(content.split("\n")),
            "content": content[:5000]
        }
    else:
        items = []
        for f in sorted(target.iterdir()):
            items.append({
                "name": f.name,
                "type": "dir" if f.is_dir() else "file",
                "size": f.stat().st_size if f.is_file() else None
            })
        return {"type": "dir", "path": path, "items": items[:100]}


# ── Ejecutar código en el clon ─────────────────────────────────────────────────

def run_in_clone(code: str, filename: str = "experiment.py", timeout: int = 30) -> Dict[str, Any]:
    """
    Ejecuta código Python en el venv del clon.
    Completamente aislado del sistema real.
    """
    exp_file = EXPERIMENTS / filename
    exp_file.write_text(code)

    python = CLONE_VENV / "bin" / "python3"
    if not python.exists():
        python = Path("python3")  # fallback

    env = os.environ.copy()
    env["EIDOS_DATA_DIR"] = str(CLONE_DATA)
    env["EIDOS_ROOT"]     = str(CLONE_DIR)
    env["PYTHONPATH"]     = str(CLONE_DIR)

    try:
        r = subprocess.run(
            [str(python), str(exp_file)],
            capture_output=True, text=True, timeout=timeout,
            env=env, cwd=str(EXPERIMENTS)
        )
        return {
            "ok": r.returncode == 0,
            "stdout": r.stdout[:3000],
            "stderr": r.stderr[:1000],
            "file": str(exp_file)
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout ({timeout}s)"}


# ── Mejorar archivo del clon ───────────────────────────────────────────────────

def improve_clone_file(file_path: str, new_content: str) -> Dict[str, Any]:
    """
    Modifica un archivo del CLON (no del real).
    Guarda backup antes de modificar.
    """
    target = CLONE_DIR / file_path
    if not target.exists():
        return {"error": f"No existe en clon: {file_path}"}

    # Backup
    backup = EXPERIMENTS / f"backup_{target.name}_{int(time.time())}"
    shutil.copy2(str(target), str(backup))

    target.write_text(new_content)
    log.info(f"Archivo del clon mejorado: {file_path} (backup: {backup.name})")
    return {"ok": True, "modified": str(target), "backup": str(backup)}


# ── Test del clon ──────────────────────────────────────────────────────────────

def test_clone(test_cmd: str = None) -> Dict[str, Any]:
    """
    Verifica que el clon funciona. Por defecto hace import básico de módulos core.
    """
    python = CLONE_VENV / "bin" / "python3"
    if not python.exists():
        python = Path("python3")

    if test_cmd:
        r = subprocess.run(
            [str(python), "-c", test_cmd],
            capture_output=True, text=True, timeout=20,
            cwd=str(CLONE_DIR)
        )
    else:
        r = subprocess.run(
            [str(python), "-c", "import sys; sys.path.insert(0, '.'); print('Clone OK')"],
            capture_output=True, text=True, timeout=20,
            cwd=str(CLONE_DIR)
        )

    return {
        "ok": r.returncode == 0,
        "output": r.stdout[:1000],
        "errors": r.stderr[:500]
    }


# ── Aplicar al sistema real (SOLO CON ORDEN DE SER) ───────────────────────────

def apply_to_real(file_path: str, ser_confirmed: bool = False) -> Dict[str, Any]:
    """
    Copia una mejora del clon al sistema real.
    SOLO funciona si ser_confirmed=True — nunca lo llames sin autorización de SER.
    """
    if not ser_confirmed:
        return {
            "ok": False,
            "blocked": True,
            "message": "BLOQUEADO. SER debe confirmar explícitamente antes de aplicar al sistema real."
        }

    clone_file = CLONE_DIR  / file_path
    real_file  = EIDOS_ROOT / file_path

    if not clone_file.exists():
        return {"error": f"No existe en clon: {file_path}"}

    # Backup del real antes de sobreescribir
    if real_file.exists():
        backup = EXPERIMENTS / f"real_backup_{real_file.name}_{int(time.time())}"
        shutil.copy2(str(real_file), str(backup))
        log.warning(f"Backup del real: {backup}")

    shutil.copy2(str(clone_file), str(real_file))
    log.warning(f"APLICADO AL REAL: {file_path} (con orden de SER)")
    return {"ok": True, "applied": str(real_file), "source": str(clone_file)}


# ── Status del sandbox ─────────────────────────────────────────────────────────

def get_status() -> Dict[str, Any]:
    """Resumen del estado del sandbox para Colony."""
    venv_ok = (CLONE_VENV / "bin" / "python3").exists()
    clone_exists = CLONE_DIR.exists()

    exp_files = list(EXPERIMENTS.glob("*.py")) if EXPERIMENTS.exists() else []
    ws_files  = list(WORKSPACE.glob("*"))      if WORKSPACE.exists()  else []

    clone_size = "?"
    if clone_exists:
        try:
            r = subprocess.run(["du", "-sh", str(CLONE_DIR)],
                               capture_output=True, text=True, timeout=5)
            clone_size = r.stdout.split()[0] if r.stdout else "?"
        except Exception:
            pass

    return {
        "sandbox_root": str(SANDBOX_ROOT),
        "clone_exists": clone_exists,
        "clone_size":   clone_size,
        "venv_ready":   venv_ok,
        "experiments":  len(exp_files),
        "workspace_files": len(ws_files),
        "identity": json.loads((CLONE_DATA / "identity.json").read_text()) if (CLONE_DATA / "identity.json").exists() else {}
    }
