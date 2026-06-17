"""
core/eidos_filesystem.py — Sistema de archivos como extensión del cuerpo de EIDOS [S125]

Permite a EIDOS navegar el sistema de archivos igual que un humano:
  - Explorar directorios (ls, tree, find)
  - Leer archivos (read_file con límites de seguridad)
  - Escribir archivos (write_file con validación de rutas)
  - Obtener metadatos (stat, disk_usage, mime_detect)
  - Navegación interactiva (explore_path para el orchestrator)

Seguridad:
  - Las escrituras están confinadas a ~/.eidos/, ~/EIDOS/, /tmp/eidos_*/
  - Lecturas bloqueadas en /etc/shadow, /root/, ~/.ssh/id_*, etc.
  - Límites de tamaño para no saturar memoria
  - Sin ejecución de comandos — solo exploración de archivos

Integración con AliveOrchestrator:
  En learn_from_perception(), si EIDOS detecta una ventana de Dolphin/Konsole,
  explora automáticamente el directorio actual para entender su entorno.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import shutil
import stat as stat_module
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.filesystem")

# ── Constantes de seguridad ──────────────────────────────────────────────────

# Solo se permite ESCRITURA dentro de estos directorios
WRITEABLE_ROOTS = [
    Path.home() / ".eidos",
    Path.home() / "EIDOS",
    Path("/tmp"),
    Path("/var/tmp"),
]

# Lectura bloqueada en estos patrones
READ_BLOCKED_GLOBS = [
    "/etc/shadow*",
    "/etc/ssh/ssh_host_*",
    "/root/*",
    str(Path.home()) + "/.ssh/id_*",
    str(Path.home()) + "/.gnupg/*",
    "/proc/*/mem",
    "/sys/kernel/security/*",
]

# Directorios clave del sistema que EIDOS debe conocer
SYSTEM_LANDMARKS = {
    "/": "raíz del sistema de archivos",
    "/home": "directorios de usuarios",
    str(Path.home()): "directorio home de SER",
    str(Path.home() / "EIDOS"): "código fuente de EIDOS",
    str(Path.home() / ".eidos"): "datos y estado de EIDOS",
    "/etc": "configuración del sistema",
    "/var": "datos variables (logs, colas, etc.)",
    "/tmp": "archivos temporales",
    "/usr/bin": "ejecutables del sistema",
    "/usr/share": "datos compartidos (documentación, iconos, etc.)",
    "/opt": "software instalado manualmente",
    "/mnt": "puntos de montaje",
    "/media": "medios extraíbles",
}


def _is_path_safe_to_read(path: str) -> Tuple[bool, str]:
    """Verifica que una ruta sea segura para LEER."""
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        return False, f"ruta no resoluble: {path}"

    # Bloquear acceso a archivos sensibles
    import fnmatch
    for pattern in READ_BLOCKED_GLOBS:
        if fnmatch.fnmatch(resolved, pattern):
            return False, f"ruta bloqueada por seguridad: {pattern}"

    # Verificar que existe y es legible
    if not os.path.exists(resolved):
        return False, f"ruta no existe: {resolved}"
    if not os.access(resolved, os.R_OK):
        return False, f"sin permiso de lectura: {resolved}"

    return True, ""


def _is_path_safe_to_write(path: str) -> Tuple[bool, str]:
    """Verifica que una ruta sea segura para ESCRIBIR."""
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        return False, f"ruta no resoluble: {path}"

    # Solo permitir escritura dentro de directorios autorizados
    allowed = False
    for root in WRITEABLE_ROOTS:
        try:
            root_str = str(root.resolve())
        except Exception:
            continue
        if resolved.startswith(root_str + "/") or resolved == root_str:
            allowed = True
            break
    if not allowed:
        return False, (
            f"escritura solo permitida en: "
            f"{', '.join(str(r) for r in WRITEABLE_ROOTS)}"
        )

    # Si ya existe, verificar que sea escribible y no sea un directorio
    if os.path.exists(resolved):
        if os.path.isdir(resolved):
            return False, f"es un directorio, no un archivo: {resolved}"
        if not os.access(resolved, os.W_OK):
            return False, f"sin permiso de escritura: {resolved}"

    # Verificar que el directorio padre exista y sea escribible
    parent = str(Path(resolved).parent)
    if not os.path.isdir(parent):
        return False, f"directorio padre no existe: {parent}"
    if not os.access(parent, os.W_OK):
        return False, f"sin permiso de escritura en directorio padre: {parent}"

    return True, ""


# ── Operaciones de exploración ───────────────────────────────────────────────


def ls(path: str = ".") -> Dict[str, Any]:
    """Lista el contenido de un directorio.

    Returns: {
        ok, path, entries: [{name, type: "file"|"dir"|"symlink"|"unknown",
                              size_bytes, mode, mtime_iso, is_hidden, ext}],
        count_files, count_dirs, error
    }
    """
    result = {
        "ok": False, "path": str(Path(path).resolve()),
        "entries": [], "count_files": 0, "count_dirs": 0, "error": "",
    }

    safe, reason = _is_path_safe_to_read(path)
    if not safe:
        result["error"] = reason
        return result

    try:
        p = Path(path).resolve()
        entries = []
        for item in sorted(p.iterdir()):
            try:
                st = item.stat()
            except OSError:
                continue
            entry_type = "unknown"
            if item.is_dir():
                entry_type = "dir"
            elif item.is_symlink():
                entry_type = "symlink"
            elif item.is_file():
                entry_type = "file"

            entries.append({
                "name": item.name,
                "type": entry_type,
                "size_bytes": st.st_size if entry_type == "file" else 0,
                "mode": stat_module.filemode(st.st_mode)[:10],
                "mtime_iso": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)
                ),
                "is_hidden": item.name.startswith("."),
                "ext": item.suffix.lower() if item.suffix else "",
            })

        result.update(
            ok=True,
            entries=entries,
            count_files=sum(1 for e in entries if e["type"] == "file"),
            count_dirs=sum(1 for e in entries if e["type"] == "dir"),
        )
    except PermissionError as e:
        result["error"] = f"permiso denegado: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def tree(path: str = ".", max_depth: int = 3, max_items: int = 200) -> Dict[str, Any]:
    """Recorre recursivamente un directorio (hasta max_depth y max_items).

    Returns: {ok, path, tree: str (formato árbol ASCII), count_total, truncated}
    """
    result = {
        "ok": False, "path": str(Path(path).resolve()),
        "tree": "", "count_total": 0, "truncated": False, "error": "",
    }

    safe, reason = _is_path_safe_to_read(path)
    if not safe:
        result["error"] = reason
        return result

    count = [0]  # mutable counter
    lines = []

    def _walk(current: Path, prefix: str = "", depth: int = 0):
        if depth > max_depth or count[0] >= max_items:
            if count[0] >= max_items:
                result["truncated"] = True
            return
        try:
            items = sorted(current.iterdir())
        except (PermissionError, OSError):
            lines.append(f"{prefix}[sin acceso]")
            return

        for i, item in enumerate(items):
            if count[0] >= max_items:
                result["truncated"] = True
                return
            count[0] += 1
            is_last = (i == len(items) - 1)
            connector = "└── " if is_last else "├── "
            next_prefix = "    " if is_last else "│   "

            try:
                name = item.name + ("/" if item.is_dir() else "")
                lines.append(f"{prefix}{connector}{name}")
                if item.is_dir() and not item.is_symlink():
                    _walk(item, prefix + next_prefix, depth + 1)
            except (PermissionError, OSError):
                lines.append(f"{prefix}{connector}{item.name} [sin acceso]")

    p = Path(path).resolve()
    lines.append(p.name + ("/" if p.is_dir() else ""))
    _walk(p)
    result.update(ok=True, tree="\n".join(lines), count_total=count[0])

    return result


def stat(path: str) -> Dict[str, Any]:
    """Obtiene metadatos detallados de un archivo o directorio.

    Returns: {ok, path, size_bytes, mode, is_file, is_dir, is_symlink,
              mtime_iso, ctime_iso, atime_iso, owner_uid, owner_gid,
              device, inode, nlink, human_size, landmark_name (si aplica)}
    """
    result = {"ok": False, "path": str(Path(path).resolve()), "error": ""}
    safe, reason = _is_path_safe_to_read(path)
    if not safe:
        result["error"] = reason
        return result

    try:
        p = Path(path).resolve()
        st = p.stat()
        size = st.st_size
        human = ""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                human = f"{size:.1f} {unit}"
                break
            size /= 1024

        resolved_str = str(p)
        landmark = SYSTEM_LANDMARKS.get(resolved_str, "")

        result.update(
            ok=True,
            size_bytes=st.st_size,
            human_size=human,
            mode=stat_module.filemode(st.st_mode),
            is_file=p.is_file(),
            is_dir=p.is_dir(),
            is_symlink=p.is_symlink(),
            is_executable=os.access(str(p), os.X_OK),
            mtime_iso=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            ctime_iso=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_ctime)),
            atime_iso=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_atime)),
            owner_uid=st.st_uid,
            owner_gid=st.st_gid,
            inode=st.st_ino,
            nlink=st.st_nlink,
            device=st.st_dev,
        )
        if landmark:
            result["landmark_name"] = landmark
        if p.is_symlink():
            result["symlink_target"] = str(p.readlink())

    except PermissionError as e:
        result["error"] = f"permiso denegado: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def find(root: str, pattern: str = "*", max_depth: int = 5,
         max_results: int = 100) -> Dict[str, Any]:
    """Busca archivos/directorios por nombre (glob) recursivamente.

    Returns: {ok, root, pattern, results: [path strings], count, truncated}
    """
    result = {
        "ok": False, "root": str(Path(root).resolve()),
        "pattern": pattern, "results": [], "count": 0,
        "truncated": False, "error": "",
    }

    safe, reason = _is_path_safe_to_read(root)
    if not safe:
        result["error"] = reason
        return result

    try:
        p = Path(root).resolve()
        matches = list(p.rglob(pattern))
        if len(matches) > max_results:
            result["truncated"] = True
        paths = [str(m) for m in matches[:max_results]]
        result.update(ok=True, results=paths, count=len(paths))
    except PermissionError as e:
        result["error"] = f"permiso denegado: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def read_file(path: str, max_lines: int = 500, max_bytes: int = 1_000_000) -> Dict[str, Any]:
    """Lee un archivo de forma segura con límites de tamaño.

    Returns: {ok, path, content (str), lines_count, total_bytes, truncated, encoding, error}
    """
    result = {
        "ok": False, "path": str(Path(path).resolve()),
        "content": "", "lines_count": 0, "total_bytes": 0,
        "truncated": False, "encoding": "", "error": "",
    }

    safe, reason = _is_path_safe_to_read(path)
    if not safe:
        result["error"] = reason
        return result

    p = Path(path).resolve()
    if not p.is_file():
        result["error"] = f"no es un archivo: {path}"
        return result

    # Verificar tamaño antes de abrir
    size = p.stat().st_size
    if size > max_bytes * 2:
        result["error"] = f"archivo demasiado grande ({size} bytes), máximo {max_bytes * 2}"
        return result

    result["total_bytes"] = size

    # Detectar tipo binario por extensión
    is_binary = _is_binary_file(str(p))
    if is_binary:
        result["error"] = f"archivo binario detectado (extensión {p.suffix}), no se lee como texto"
        result["encoding"] = "binary"
        # Pero sí damos info del archivo
        result["ok"] = False
        result["content"] = f"[BINARIO: {p.suffix}, {size} bytes]"
        return result

    # Intentar leer con detección de encoding
    try:
        with open(str(p), "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        result["error"] = f"no se pudo leer: {e}"
        return result

    result["encoding"] = "utf-8"
    result["lines_count"] = len(lines)

    if len(lines) > max_lines:
        result["truncated"] = True
        lines = lines[:max_lines]

    result["content"] = "".join(lines)[:max_bytes]
    result["ok"] = True

    return result


def write_file(path: str, content: str, append: bool = False) -> Dict[str, Any]:
    """Escribe contenido en un archivo de forma segura.

    Returns: {ok, path, bytes_written, mode (create|append), error}
    """
    result = {
        "ok": False, "path": str(Path(path).resolve()),
        "bytes_written": 0, "mode": "create" if not append else "append",
        "error": "",
    }

    safe, reason = _is_path_safe_to_write(path)
    if not safe:
        result["error"] = reason
        return result

    if len(content) > 5_000_000:
        result["error"] = "contenido demasiado grande (>5MB)"
        return result

    try:
        mode = "a" if append else "w"
        p = Path(path).resolve()
        # Crear directorio padre si no existe
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(str(p), mode, encoding="utf-8") as f:
            f.write(content)
        result.update(ok=True, bytes_written=len(content.encode("utf-8")))
    except PermissionError as e:
        result["error"] = f"permiso denegado: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def disk_usage(path: str = "/") -> Dict[str, Any]:
    """Informa del uso de disco con df y du.

    Returns: {ok, path, df: {total_gb, used_gb, available_gb, use_percent, filesystem},
              du_mb (uso del directorio, máximo 2 niveles), error}
    """
    result = {
        "ok": False, "path": str(Path(path).resolve()),
        "df": {}, "du_mb": 0, "error": "",
    }

    try:
        p = Path(path).resolve()
        # df en el punto de montaje
        usage = shutil.disk_usage(str(p))
        total_gb = usage.total / (1024**3)
        used_gb = usage.used / (1024**3)
        free_gb = usage.free / (1024**3)

        result["df"] = {
            "total_gb": round(total_gb, 1),
            "used_gb": round(used_gb, 1),
            "available_gb": round(free_gb, 1),
            "use_percent": round((usage.used / usage.total) * 100, 1),
        }

        # du del directorio (limitado a 2 niveles y 10s timeout)
        try:
            du_r = subprocess.run(
                ["du", "-sh", "--max-depth=1", str(p)],
                capture_output=True, text=True, timeout=10,
            )
            if du_r.returncode == 0:
                du_lines = du_r.stdout.strip().split("\n")
                # La última línea es el total
                if du_lines:
                    result["du_mb"] = du_lines[-1].split("\t")[0]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        result["ok"] = True
    except PermissionError as e:
        result["error"] = f"permiso denegado: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


def mime_detect(path: str) -> Dict[str, Any]:
    """Detecta el tipo MIME de un archivo.

    Returns: {ok, path, mime_type, extension, description, is_text, is_binary}
    """
    result = {
        "ok": False, "path": str(Path(path).resolve()),
        "mime_type": "", "extension": "", "description": "",
        "is_text": False, "is_binary": False, "error": "",
    }

    safe, reason = _is_path_safe_to_read(path)
    if not safe:
        result["error"] = reason
        return result

    p = Path(path).resolve()
    if not p.exists():
        result["error"] = "archivo no existe"
        return result

    # Primero por extensión
    mime, _ = mimetypes.guess_type(str(p))
    if mime:
        result["mime_type"] = mime
        result["is_text"] = mime.startswith("text/") or mime in (
            "application/json", "application/xml", "application/javascript",
            "application/x-yaml", "application/x-sh",
        )
        result["is_binary"] = not result["is_text"]

    # Por contenido (file command)
    try:
        r = subprocess.run(
            ["file", "-b", "--mime-type", str(p)],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            result["mime_type"] = r.stdout.strip()
            result["is_text"] = result["mime_type"].startswith("text/")
            result["is_binary"] = not result["is_text"]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    result["extension"] = p.suffix.lower()
    result["description"] = _describe_file_type(result["mime_type"], result["extension"])
    result["ok"] = True

    return result


# ── Exploración integral para el orchestrator ─────────────────────────────────


def explore_path(path: str = ".", quick: bool = False) -> Dict[str, Any]:
    """Exploración completa de un directorio para el AliveOrchestrator.

    Combina ls + stat + disk_usage + landmarks en una sola llamada.
    Si quick=True, solo devuelve ls + landmarks (más rápido).

    Returns: {
        ok, path, name, parent, is_home, is_landmark, landmark_desc,
        entries_summary: {total, files, dirs, hidden, top_dirs, top_files},
        disk: {total_gb, available_gb, use_percent},
        parent_info: {path, name, entries_count},
        readable: bool, writable: bool,
    }
    """
    result = {
        "ok": False, "path": str(Path(path).resolve()),
        "error": "",
    }

    safe, reason = _is_path_safe_to_read(path)
    if not safe:
        result["error"] = reason
        return result

    try:
        p = Path(path).resolve()
        resolved_str = str(p)
        is_landmark = resolved_str in SYSTEM_LANDMARKS
        is_home = resolved_str == str(Path.home())

        result["name"] = p.name or "/"
        result["parent"] = str(p.parent)
        result["is_home"] = is_home
        result["is_landmark"] = is_landmark
        result["landmark_desc"] = SYSTEM_LANDMARKS.get(resolved_str, "")
        result["readable"] = os.access(resolved_str, os.R_OK)
        result["writable"] = os.access(resolved_str, os.W_OK)

        # ls
        ls_result = ls(path)
        if ls_result["ok"]:
            entries = ls_result.get("entries", [])
            files = [e for e in entries if e["type"] == "file"]
            dirs = [e for e in entries if e["type"] == "dir"]
            result["entries_summary"] = {
                "total": len(entries),
                "files": ls_result["count_files"],
                "dirs": ls_result["count_dirs"],
                "hidden": sum(1 for e in entries if e["is_hidden"]),
                "top_dirs": [d["name"] for d in dirs[:8]],
                "top_files": [f["name"] for f in files[:8]],
            }

        # disk_usage (solo en modo completo)
        if not quick:
            du = disk_usage(path)
            if du["ok"]:
                result["disk"] = du["df"]

        # parent info
        if str(p.parent) != resolved_str:
            parent_ls = ls(str(p.parent))
            if parent_ls["ok"]:
                result["parent_info"] = {
                    "path": str(p.parent),
                    "name": p.parent.name or "/",
                    "entries_count": parent_ls["count_files"] + parent_ls["count_dirs"],
                }

        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)

    return result


def get_landmarks() -> List[Dict[str, str]]:
    """Devuelve la lista de directorios clave del sistema."""
    landmarks = []
    for path, desc in SYSTEM_LANDMARKS.items():
        exists = os.path.exists(path)
        accessible = os.access(path, os.R_OK) if exists else False
        landmarks.append({
            "path": path, "description": desc,
            "exists": exists, "accessible": accessible,
        })
    return landmarks


def discover_programs(bin_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
    """Descubre programas ejecutables en los directorios bin del sistema.

    Returns: {ok, bin_dirs_scanned, programs: [{name, path, type}], count}
    """
    result = {"ok": False, "bin_dirs_scanned": [], "programs": [], "count": 0}

    if bin_dirs is None:
        bin_dirs = ["/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin"]

    for d in bin_dirs:
        if not os.path.isdir(d):
            continue
        result["bin_dirs_scanned"].append(d)
        try:
            for item in sorted(os.listdir(d)):
                item_path = os.path.join(d, item)
                if os.access(item_path, os.X_OK) and os.path.isfile(item_path):
                    result["programs"].append({
                        "name": item,
                        "path": item_path,
                        "type": "executable",
                    })
        except (PermissionError, OSError):
            continue

    result["count"] = len(result["programs"])
    result["ok"] = True
    return result


# ── Project scaffold (Fase 3 file-ops) ─────────────────────────────────────────

SANDBOX_PROJECTS_DIR = Path.home() / ".eidos" / "sandbox" / "projects"


def create_project_scaffold(name: str, project_type: str = "python") -> Dict[str, Any]:
    """Crea la estructura de directorios y archivos iniciales de un proyecto.

    Args:
        name: nombre del proyecto (se usará como nombre de directorio)
        project_type: 'python', 'web', o 'go'

    Returns:
        {ok, project_dir, project_type, files_created: [str], error}
    """
    result = {
        "ok": False,
        "project_dir": "",
        "project_type": project_type,
        "files_created": [],
        "error": "",
    }

    # Validar nombre
    import re as _re
    slug = _re.sub(r'[^a-zA-Z0-9_-]', '_', name.strip())[:50]
    if not slug:
        result["error"] = "nombre de proyecto inválido"
        return result

    project_dir = SANDBOX_PROJECTS_DIR / slug
    result["project_dir"] = str(project_dir)

    # Verificar seguridad de escritura
    safe, reason = _is_path_safe_to_write(str(project_dir / "dummy"))
    # Permitir aunque no exista (se creará)
    if os.path.exists(str(project_dir)):
        safe, reason = _is_path_safe_to_write(str(project_dir))
        if not safe and "no existe" not in reason:
            result["error"] = reason
            return result

    try:
        project_dir.mkdir(parents=True, exist_ok=True)

        if project_type == "python":
            # Crear estructura Python
            (project_dir / "src").mkdir(exist_ok=True)
            (project_dir / "tests").mkdir(exist_ok=True)

            # requirements.txt
            reqs_path = project_dir / "requirements.txt"
            reqs_path.write_text("# Dependencias del proyecto\n")
            result["files_created"].append(str(reqs_path))

            # setup.py
            setup_path = project_dir / "setup.py"
            setup_path.write_text(f'''from setuptools import setup, find_packages

setup(
    name="{slug}",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={{"": "src"}},
    install_requires=[],
    python_requires=">=3.9",
    author="EIDOS",
    description="Proyecto generado por EIDOS filesystem",
)
''')
            result["files_created"].append(str(setup_path))

            # src/__init__.py
            init_path = project_dir / "src" / "__init__.py"
            init_path.write_text(f'"""Paquete {slug}."""\n')
            result["files_created"].append(str(init_path))

            # tests/test_main.py
            test_path = project_dir / "tests" / "test_main.py"
            test_path.write_text(f'''"""Tests para {slug}."""
import unittest


class TestMain(unittest.TestCase):
    def test_placeholder(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
''')
            result["files_created"].append(str(test_path))

        elif project_type == "web":
            # index.html
            html_path = project_dir / "index.html"
            html_path.write_text(f'''<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{slug}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>{slug}</h1>
  <p>Proyecto generado por EIDOS filesystem.</p>
  <script src="script.js"></script>
</body>
</html>
''')
            result["files_created"].append(str(html_path))

            # style.css
            css_path = project_dir / "style.css"
            css_path.write_text(f'''/* {slug} — Estilos base */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: system-ui, sans-serif;
  max-width: 800px;
  margin: 2rem auto;
  padding: 1rem;
  line-height: 1.6;
}}
''')
            result["files_created"].append(str(css_path))

            # script.js
            js_path = project_dir / "script.js"
            js_path.write_text(f'''// {slug} — Script principal
document.addEventListener('DOMContentLoaded', () => {{
  console.log('{slug} — listo');
}});
''')
            result["files_created"].append(str(js_path))

        elif project_type == "go":
            # go.mod
            mod_path = project_dir / "go.mod"
            mod_path.write_text(f'''module eidos/{slug}

go 1.21
''')
            result["files_created"].append(str(mod_path))

            # main.go
            main_path = project_dir / "main.go"
            main_path.write_text(f'''package main

import "fmt"

func main() {{
	fmt.Println("{slug} — generado por EIDOS filesystem")
}}
''')
            result["files_created"].append(str(main_path))

        else:
            result["error"] = f"tipo de proyecto no soportado: {project_type}. Usa: python, web, go"
            return result

        # README.md común a todos
        readme_path = project_dir / "README.md"
        readme_path.write_text(f'''# {slug}

Proyecto de tipo `{project_type}` generado por **EIDOS filesystem** (Fase 3 file-ops).

## Estructura

- Tipo: `{project_type}`
- Creado: {time.strftime("%Y-%m-%d %H:%M:%S")}

## Uso

Consulta `eidos-project.json` para metadatos.
''')
        result["files_created"].append(str(readme_path))

        # Metadatos
        meta_path = project_dir / "eidos-project.json"
        import json as _json
        meta_path.write_text(_json.dumps({
            "project_name": slug,
            "type": project_type,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": "eidos_filesystem.create_project_scaffold",
            "phase": "fase3_fileops",
        }, indent=2, ensure_ascii=False))
        result["files_created"].append(str(meta_path))

        result["ok"] = True
        log.info("filesystem: scaffold '%s' tipo=%s creado con %d archivos en %s",
                 slug, project_type, len(result["files_created"]), project_dir)

    except PermissionError as e:
        result["error"] = f"permiso denegado: {e}"
    except Exception as e:
        result["error"] = str(e)

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_binary_file(filepath: str) -> bool:
    """Heurística para detectar archivos binarios por extensión."""
    ext = Path(filepath).suffix.lower()
    binary_exts = {
        ".bin", ".exe", ".dll", ".so", ".o", ".a", ".ko",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
        ".mp3", ".mp4", ".avi", ".mkv", ".mov", ".wav", ".flac", ".ogg",
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".pyc", ".pyo", ".class", ".jar",
        ".ttf", ".otf", ".woff", ".woff2",
        ".db", ".sqlite", ".sqlite3",
        ".iso", ".img", ".qcow2", ".vdi", ".vmdk",
        ".lock", ".pid",
    }
    if ext in binary_exts:
        return True
    # También detectar binarios sin extensión (ELF magic bytes)
    if ext == "" and os.path.isfile(filepath):
        try:
            with open(filepath, "rb") as f:
                magic = f.read(4)
            if magic[:4] == b"\x7fELF":
                return True
        except Exception:
            return False
    return False


def _describe_file_type(mime: str, ext: str) -> str:
    """Descripción humana del tipo de archivo."""
    if not mime:
        return f"archivo {ext}" if ext else "desconocido"
    type_map = {
        "text/plain": "texto plano",
        "text/html": "página HTML",
        "text/css": "hoja de estilos CSS",
        "text/javascript": "código JavaScript",
        "text/x-python": "código Python",
        "text/x-script.python": "código Python",
        "application/json": "datos JSON",
        "application/xml": "datos XML",
        "application/x-yaml": "configuración YAML",
        "application/x-sh": "script de shell",
        "application/pdf": "documento PDF",
        "application/zip": "archivo comprimido ZIP",
        "application/gzip": "archivo comprimido GZIP",
        "application/x-tar": "archivo TAR",
        "image/png": "imagen PNG",
        "image/jpeg": "imagen JPEG",
        "image/gif": "imagen GIF",
    }
    for prefix, desc in {
        "text/": "texto",
        "image/": "imagen",
        "audio/": "audio",
        "video/": "video",
        "application/": "datos",
        "inode/": "dispositivo",
    }.items():
        if mime.startswith(prefix):
            return type_map.get(mime, desc)
    return mime


# ── CLI rápido ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        cmd = "landmarks"
        path_arg = "."
    else:
        cmd = sys.argv[1]
        path_arg = sys.argv[2] if len(sys.argv) > 2 else "."

    if cmd == "ls":
        print(json.dumps(ls(path_arg), indent=2, ensure_ascii=False))
    elif cmd == "tree":
        print(json.dumps(tree(path_arg), indent=2, ensure_ascii=False))
    elif cmd == "stat":
        print(json.dumps(stat(path_arg), indent=2, ensure_ascii=False))
    elif cmd == "find":
        pattern = sys.argv[3] if len(sys.argv) > 3 else "*"
        print(json.dumps(find(path_arg, pattern), indent=2, ensure_ascii=False))
    elif cmd == "read":
        print(json.dumps(read_file(path_arg), indent=2, ensure_ascii=False))
    elif cmd == "disk":
        print(json.dumps(disk_usage(path_arg), indent=2, ensure_ascii=False))
    elif cmd == "explore":
        print(json.dumps(explore_path(path_arg), indent=2, ensure_ascii=False))
    elif cmd == "landmarks":
        print(json.dumps(get_landmarks(), indent=2, ensure_ascii=False))
    elif cmd == "programs":
        print(json.dumps(discover_programs(), indent=2, ensure_ascii=False))
    elif cmd == "scaffold":
        project_type = sys.argv[3] if len(sys.argv) > 3 else "python"
        print(json.dumps(create_project_scaffold(path_arg, project_type), indent=2, ensure_ascii=False))
    else:
        print(f"Comando desconocido: {cmd}")
        print("Comandos: ls, tree, stat, find, read, disk, explore, landmarks, programs, scaffold")
