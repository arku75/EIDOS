"""
EIDOS core/extension_intelligence.py — Extension Intelligence
===============================================================
Módulo Python que analiza workspaces, detecta lenguajes/frameworks/tools,
y auto-instala/desinstala extensiones VSCode óptimas para VSEIDOS.

Features:
  - Escaneo de workspace: detecta lenguajes por extensión, frameworks por config files
  - Base de conocimiento: mapeo lenguaje/framework → extensiones recomendadas
  - Auto-install/uninstall via `code --install-extension` / `code --uninstall-extension`
  - Detección de extensiones redundantes o en conflicto
  - Aprendizaje: registra qué extensiones el usuario usa/desinstala
  - Integración con ColonyQueryEngine para consultar si algo conviene
  - Soporte para VSEIDOS (custom code binary) y VSCode estándar

Uso:
    from core.extension_intelligence import get_extension_intelligence
    ei = get_extension_intelligence()
    ei.analyze_workspace("~/EIDOS")
    ei.auto_install()
    ei.install("rust-lang.rust-analyzer")
    ei.uninstall("ms-python.isort")
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Dict, List, Set
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.extension_intelligence")

DB_PATH = os.path.expanduser("~/.eidos/extensions.db")

# ══════════════════════════════════════════════════════════════════════════════
#  DETECCIÓN DE VSEIDOS vs CODE
# ══════════════════════════════════════════════════════════════════════════════

VSEIDOS_BIN = Path.home() / "EIDOS" / "VSEIDOS" / "bin" / "vseidos"
CODE_BIN_CANDIDATES = [
    str(VSEIDOS_BIN),
    "/usr/bin/code",
    "/usr/local/bin/code",
    str(Path.home() / ".local" / "bin" / "code"),
    "code",  # PATH fallback
]


def _find_code_binary() -> str:
    """Encuentra el binario de VSCode/VSEIDOS disponible."""
    for candidate in CODE_BIN_CANDIDATES:
        try:
            path = Path(candidate)
            if path.exists() and path.is_file():
                return str(path)
        except Exception:
            pass  # error no crítico, continuar
    # Fallback: intentar `code` del PATH
    try:
        result = subprocess.run(["which", "code"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass  # error no crítico, continuar
    return "code"


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE CONOCIMIENTO — Extensiones por lenguaje/framework
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtensionInfo:
    ext_id: str             # e.g. "ms-python.python"
    name: str               # display name
    reason: str             # por qué instalar
    priority: str = "high"  # critical, high, medium, low
    conflicts: List[str] = field(default_factory=list)  # IDs que entran en conflicto


# Mapeo: detector_key → [extensiones recomendadas]
EXTENSION_DB: Dict[str, List[ExtensionInfo]] = {
    # ── Python ───────────────────────────────────────────
    "python": [
        ExtensionInfo("ms-python.python", "Python", "Soporte oficial Python (LSP, debug, linting)", "critical"),
        ExtensionInfo("ms-python.vscode-pylance", "Pylance", "Type checking y autocompletado avanzado", "critical"),
        ExtensionInfo("ms-python.black-formatter", "Black Formatter", "Formateo automático PEP8", "high"),
        ExtensionInfo("ms-python.debugpy", "Python Debugger", "Debugger oficial Python", "high"),
    ],
    # ── Rust ─────────────────────────────────────────────
    "rust": [
        ExtensionInfo("rust-lang.rust-analyzer", "rust-analyzer", "LSP para Rust (autocompletado, errores, refactor)", "critical"),
        ExtensionInfo("serayuzgur.crates", "crates", "Gestión de dependencias Cargo", "high"),
        ExtensionInfo("vadimcn.vscode-lldb", "CodeLLDB", "Debugger LLDB para Rust/C++", "high"),
        ExtensionInfo("tamasfe.even-better-toml", "Even Better TOML", "Soporte para Cargo.toml", "medium"),
    ],
    # ── JavaScript/TypeScript ────────────────────────────
    "javascript": [
        ExtensionInfo("dbaeumer.vscode-eslint", "ESLint", "Linting JavaScript/TypeScript", "critical"),
        ExtensionInfo("esbenp.prettier-vscode", "Prettier", "Formateo de código consistente", "high"),
    ],
    "typescript": [
        ExtensionInfo("dbaeumer.vscode-eslint", "ESLint", "Linting TypeScript", "critical"),
        ExtensionInfo("esbenp.prettier-vscode", "Prettier", "Formateo de código", "high"),
    ],
    "react": [
        ExtensionInfo("dsznajder.es7-react-js-snippets", "ES7+ React Snippets", "Snippets para React", "high"),
    ],
    "vue": [
        ExtensionInfo("vue.volar", "Volar", "Soporte oficial Vue 3", "critical"),
    ],
    "svelte": [
        ExtensionInfo("svelte.svelte-vscode", "Svelte", "Soporte Svelte", "critical"),
    ],
    # ── Go ───────────────────────────────────────────────
    "go": [
        ExtensionInfo("golang.go", "Go", "Soporte oficial Go (gopls, debug)", "critical"),
    ],
    # ── C/C++ ────────────────────────────────────────────
    "cpp": [
        ExtensionInfo("ms-vscode.cpptools", "C/C++", "Soporte C/C++ (IntelliSense, debug)", "critical"),
        ExtensionInfo("ms-vscode.cmake-tools", "CMake Tools", "Soporte CMake", "high"),
    ],
    # ── Java/Kotlin ──────────────────────────────────────
    "java": [
        ExtensionInfo("redhat.java", "Java", "Language Support for Java (Red Hat)", "critical"),
        ExtensionInfo("vscjava.vscode-java-debug", "Java Debug", "Debugger para Java", "high"),
    ],
    # ── Docker ───────────────────────────────────────────
    "docker": [
        ExtensionInfo("ms-azuretools.vscode-docker", "Docker", "Soporte Docker y Docker Compose", "critical"),
    ],
    # ── Kubernetes ───────────────────────────────────────
    "kubernetes": [
        ExtensionInfo("ms-kubernetes-tools.vscode-kubernetes-tools", "Kubernetes", "Soporte K8s", "high"),
    ],
    # ── Shell/Bash ───────────────────────────────────────
    "shell": [
        ExtensionInfo("timonwong.shellcheck", "ShellCheck", "Linting para scripts Bash/Shell", "high"),
        ExtensionInfo("foxundermoon.shell-format", "shell-format", "Formateo Bash/Shell", "medium"),
    ],
    # ── Git ──────────────────────────────────────────────
    "git": [
        ExtensionInfo("eamodio.gitlens", "GitLens", "Superpoderes para Git (blame, history, etc.)", "high"),
    ],
    # ── Markdown ─────────────────────────────────────────
    "markdown": [
        ExtensionInfo("yzhang.markdown-all-in-one", "Markdown All in One", "Preview, TOC, shortcuts", "medium"),
    ],
    # ── YAML ─────────────────────────────────────────────
    "yaml": [
        ExtensionInfo("redhat.vscode-yaml", "YAML", "Validación y autocompletado YAML", "high"),
    ],
    # ── SQL ──────────────────────────────────────────────
    "sql": [
        ExtensionInfo("mtxr.sqltools", "SQLTools", "Cliente SQL integrado", "high"),
    ],
    # ── Terraform ────────────────────────────────────────
    "terraform": [
        ExtensionInfo("hashicorp.terraform", "Terraform", "Soporte HCL/Terraform", "critical"),
    ],
    # ── Universales ──────────────────────────────────────
    "_universal": [
        ExtensionInfo("streetsidesoftware.code-spell-checker", "Code Spell Checker", "Detector de typos en código", "low"),
        ExtensionInfo("usernamehw.errorlens", "Error Lens", "Muestra errores inline", "medium"),
        ExtensionInfo("gruntfuggly.todo-tree", "Todo Tree", "Busca TODO/FIXME en código", "low"),
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
#  DETECTORES — Qué lenguajes/frameworks tiene un workspace
# ══════════════════════════════════════════════════════════════════════════════

# extension → detector key
FILE_EXT_MAP: Dict[str, str] = {
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".rs": "rust",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go",
    ".c": "cpp", ".cpp": "cpp", ".cc": "cpp", ".h": "cpp", ".hpp": "cpp",
    ".java": "java", ".kt": "java",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".md": "markdown", ".mdx": "markdown",
    ".yml": "yaml", ".yaml": "yaml",
    ".sql": "sql",
    ".tf": "terraform", ".tfvars": "terraform",
    ".svelte": "svelte",
    ".vue": "vue",
}

# Config files → detector key
CONFIG_FILE_MAP: Dict[str, str] = {
    "Dockerfile": "docker",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "CMakeLists.txt": "cpp",
    "Makefile": "cpp",
    "pom.xml": "java",
    "build.gradle": "java",
    ".gitignore": "git",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Pipfile": "python",
    "Gemfile": "ruby",
    "helmfile.yaml": "kubernetes",
    "kustomization.yaml": "kubernetes",
}

# Dependency detection in package.json
PACKAGE_JSON_DETECTORS: Dict[str, str] = {
    "react": "react",
    "react-dom": "react",
    "vue": "vue",
    "svelte": "svelte",
    "next": "react",
    "nuxt": "vue",
}


# ══════════════════════════════════════════════════════════════════════════════
#  EXTENSION INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ScanResult:
    """Resultado de escanear un workspace."""
    workspace: str
    detected_languages: Set[str]
    file_counts: Dict[str, int]
    recommended: List[ExtensionInfo]
    already_installed: List[str]
    to_install: List[ExtensionInfo]
    scan_time: float = 0.0


class ExtensionIntelligence:
    """
    Analiza workspaces y gestiona extensiones VSCode/VSEIDOS inteligentemente.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._code_bin = _find_code_binary()
        self._installed_cache: Optional[Set[str]] = None
        self._cache_time: float = 0.0
        self._last_scan: Optional[ScanResult] = None
        self._init_db()
        log.info("[ExtIntel] Inicializado — binary=%s", self._code_bin)

    def _init_db(self) -> None:
        """Tablas para historial de extensiones."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with get_conn_ctx(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS extension_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ext_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT,
                    workspace TEXT,
                    timestamp REAL
                );
                CREATE TABLE IF NOT EXISTS workspace_scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace TEXT NOT NULL,
                    languages TEXT,
                    recommended_count INTEGER,
                    installed_count INTEGER,
                    timestamp REAL
                );
                CREATE TABLE IF NOT EXISTS user_preferences (
                    ext_id TEXT PRIMARY KEY,
                    preference TEXT NOT NULL,
                    reason TEXT,
                    updated_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_eh_ext ON extension_history(ext_id);
                CREATE INDEX IF NOT EXISTS idx_ws_time ON workspace_scans(timestamp);
            """)

    # ── INSTALADAS ───────────────────────────────────────────────────────────

    def get_installed(self, force_refresh: bool = False) -> Set[str]:
        """Lista extensiones actualmente instaladas."""
        now = time.time()
        if not force_refresh and self._installed_cache and (now - self._cache_time) < 60:
            return self._installed_cache

        try:
            result = subprocess.run(
                [self._code_bin, "--list-extensions"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                extensions = set(
                    line.strip().lower()
                    for line in result.stdout.strip().split("\n")
                    if line.strip()
                )
                self._installed_cache = extensions
                self._cache_time = now
                return extensions
        except Exception as e:
            log.warning("[ExtIntel] Error listing extensions: %s", e)

        return self._installed_cache or set()

    # ── ESCANEO DE WORKSPACE ─────────────────────────────────────────────────

    def analyze_workspace(self, workspace_path: str = None,
                          max_files: int = 500, max_depth: int = 4) -> ScanResult:
        """
        Escanea un workspace y detecta lenguajes, frameworks y extensiones necesarias.

        Args:
            workspace_path: Ruta al workspace (default: ~/EIDOS)
            max_files: Máximo de archivos a escanear
            max_depth: Profundidad máxima de directorios

        Returns:
            ScanResult con todo lo detectado
        """
        t0 = time.time()
        workspace_path = workspace_path or str(Path.home() / "EIDOS")
        root = Path(workspace_path)

        detected: Set[str] = set()
        file_counts: Dict[str, int] = {}
        files_scanned = 0

        # Directorios a ignorar
        ignore_dirs = {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            "target", "build", "dist", ".tox", ".mypy_cache",
            ".cache", ".eidos", "screenshots",
        }

        for dirpath, dirnames, filenames in os.walk(root):
            # Respetar max_depth
            depth = str(dirpath).replace(str(root), "").count(os.sep)
            if depth > max_depth:
                dirnames.clear()
                continue

            # Filtrar directorios ignorados
            dirnames[:] = [d for d in dirnames if d not in ignore_dirs]

            for fname in filenames:
                if files_scanned >= max_files:
                    break

                files_scanned += 1

                # Detectar por nombre de archivo config
                if fname in CONFIG_FILE_MAP:
                    key = CONFIG_FILE_MAP[fname]
                    detected.add(key)
                    file_counts[key] = file_counts.get(key, 0) + 1

                    # package.json → detectar frameworks
                    if fname == "package.json":
                        self._detect_from_package_json(
                            Path(dirpath) / fname, detected
                        )

                # Detectar por extensión
                _, ext = os.path.splitext(fname)
                if ext.lower() in FILE_EXT_MAP:
                    key = FILE_EXT_MAP[ext.lower()]
                    detected.add(key)
                    file_counts[key] = file_counts.get(key, 0) + 1

        # Git siempre si hay .git
        if (root / ".git").exists():
            detected.add("git")

        # Recopilar recomendaciones
        recommended = self._get_recommendations(detected)
        installed = self.get_installed()

        # Filtrar ya instaladas
        to_install = [
            ext for ext in recommended
            if ext.ext_id.lower() not in installed
        ]

        # Filtrar por preferencias del usuario (no instalar lo que rechazó)
        user_prefs = self._get_user_preferences()
        to_install = [
            ext for ext in to_install
            if user_prefs.get(ext.ext_id.lower()) != "never"
        ]

        scan_time = time.time() - t0
        result = ScanResult(
            workspace=workspace_path,
            detected_languages=detected,
            file_counts=file_counts,
            recommended=recommended,
            already_installed=[ext.ext_id for ext in recommended if ext.ext_id.lower() in installed],
            to_install=to_install,
            scan_time=scan_time,
        )

        self._last_scan = result

        # Registrar scan
        try:
            with get_conn_ctx(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO workspace_scans (workspace, languages, recommended_count, installed_count, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (workspace_path, json.dumps(list(detected)),
                      len(recommended), len(result.already_installed), time.time()))
        except Exception:
            pass  # error no crítico, continuar
        return result

    def _detect_from_package_json(self, path: Path, detected: Set[str]) -> None:
        """Detecta frameworks desde package.json."""
        try:
            with open(path) as f:
                pkg = json.load(f)
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            for dep_name, det_key in PACKAGE_JSON_DETECTORS.items():
                if dep_name in all_deps:
                    detected.add(det_key)
        except Exception:
            pass  # error no crítico, continuar
    def _get_recommendations(self, detected: Set[str]) -> List[ExtensionInfo]:
        """Genera lista de extensiones recomendadas."""
        recommendations = []
        seen_ids = set()

        for lang in detected:
            exts = EXTENSION_DB.get(lang, [])
            for ext in exts:
                if ext.ext_id not in seen_ids:
                    seen_ids.add(ext.ext_id)
                    recommendations.append(ext)

        # Añadir universales
        for ext in EXTENSION_DB.get("_universal", []):
            if ext.ext_id not in seen_ids:
                seen_ids.add(ext.ext_id)
                recommendations.append(ext)

        # Ordenar por prioridad
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        recommendations.sort(key=lambda e: priority_order.get(e.priority, 4))

        return recommendations

    # ── INSTALAR / DESINSTALAR ───────────────────────────────────────────────

    def install(self, ext_id: str, reason: str = "manual") -> bool:
        """Instala una extensión."""
        try:
            result = subprocess.run(
                [self._code_bin, "--install-extension", ext_id, "--force"],
                capture_output=True, text=True, timeout=120,
            )
            success = result.returncode == 0
            self._record_action(ext_id, "install" if success else "install_failed", reason)
            if success:
                self._installed_cache = None  # Invalidar cache
                print(f"[ExtIntel] Instalada: {ext_id}")
            else:
                print(f"[ExtIntel] Error instalando {ext_id}: {result.stderr[:200]}")
            return success
        except Exception as e:
            print(f"[ExtIntel] Error: {e}")
            return False

    def uninstall(self, ext_id: str, reason: str = "manual") -> bool:
        """Desinstala una extensión."""
        try:
            result = subprocess.run(
                [self._code_bin, "--uninstall-extension", ext_id],
                capture_output=True, text=True, timeout=60,
            )
            success = result.returncode == 0
            self._record_action(ext_id, "uninstall" if success else "uninstall_failed", reason)
            if success:
                self._installed_cache = None
                print(f"[ExtIntel] Desinstalada: {ext_id}")
            return success
        except Exception as e:
            print(f"[ExtIntel] Error: {e}")
            return False

    def auto_install(self, workspace: str = None, dry_run: bool = False) -> Dict[str, Any]:
        """
        Analiza workspace y auto-instala extensiones recomendadas.

        Args:
            workspace: Path al workspace (default ~/EIDOS)
            dry_run: Si True, solo muestra qué instalaría sin hacerlo

        Returns:
            dict con resultado de la operación
        """
        scan = self.analyze_workspace(workspace)
        result = {
            "detected": list(scan.detected_languages),
            "recommended": len(scan.recommended),
            "already_installed": len(scan.already_installed),
            "to_install": len(scan.to_install),
            "installed": [],
            "failed": [],
            "dry_run": dry_run,
        }

        if dry_run:
            result["would_install"] = [
                {"id": ext.ext_id, "name": ext.name, "priority": ext.priority, "reason": ext.reason}
                for ext in scan.to_install
            ]
            return result

        for ext in scan.to_install:
            if ext.priority in ("critical", "high"):
                success = self.install(ext.ext_id, reason=f"auto:{ext.priority}")
                if success:
                    result["installed"].append(ext.ext_id)
                else:
                    result["failed"].append(ext.ext_id)

        return result

    def auto_cleanup(self) -> Dict[str, Any]:
        """
        Detecta extensiones redundantes o no usadas y sugiere desinstalarlas.
        No desinstala automáticamente sin confirmación.
        """
        installed = self.get_installed(force_refresh=True)

        # Extensiones conocidas que pueden ser redundantes
        redundant_pairs = [
            ("ms-python.isort", "ms-python.black-formatter"),  # black hace ambos
            ("ms-vscode.vscode-typescript-tslint-plugin", "dbaeumer.vscode-eslint"),
        ]

        suggestions = []
        for old, replacement in redundant_pairs:
            if old.lower() in installed and replacement.lower() in installed:
                suggestions.append({
                    "remove": old,
                    "reason": f"Redundante con {replacement}",
                })

        return {
            "total_installed": len(installed),
            "redundant_suggestions": suggestions,
        }

    # ── PREFERENCIAS ─────────────────────────────────────────────────────────

    def set_preference(self, ext_id: str, preference: str, reason: str = "") -> None:
        """
        Guarda preferencia del usuario sobre una extensión.

        preference: "always", "never", "ask"
        """
        with get_conn_ctx(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO user_preferences (ext_id, preference, reason, updated_at)
                VALUES (?, ?, ?, ?)
            """, (ext_id.lower(), preference, reason, time.time()))

    def _get_user_preferences(self) -> Dict[str, str]:
        """Carga preferencias del usuario."""
        try:
            with get_conn_ctx(self.db_path) as conn:
                rows = conn.execute("SELECT ext_id, preference FROM user_preferences").fetchall()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}

    # ── REGISTRO ─────────────────────────────────────────────────────────────

    def _record_action(self, ext_id: str, action: str, reason: str,
                       workspace: str = "") -> None:
        """Registra una acción en el historial."""
        try:
            with get_conn_ctx(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO extension_history (ext_id, action, reason, workspace, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                """, (ext_id, action, reason, workspace, time.time()))
        except Exception:
            pass  # error no crítico, continuar
    # ── API PÚBLICA ──────────────────────────────────────────────────────────

    def get_status(self) -> Dict:
        """Estado del módulo."""
        installed = self.get_installed()
        return {
            "code_binary": self._code_bin,
            "is_vseidos": "vseidos" in self._code_bin.lower(),
            "installed_count": len(installed),
            "last_scan": {
                "workspace": self._last_scan.workspace if self._last_scan else None,
                "languages": list(self._last_scan.detected_languages) if self._last_scan else [],
                "to_install": len(self._last_scan.to_install) if self._last_scan else 0,
            } if self._last_scan else None,
        }

    def get_stats(self) -> Dict:
        """Estadísticas históricas."""
        stats = {
            "installed_count": len(self.get_installed()),
        }
        try:
            with get_conn_ctx(self.db_path) as conn:
                stats["total_installs"] = conn.execute(
                    "SELECT COUNT(*) FROM extension_history WHERE action = 'install'"
                ).fetchone()[0]
                stats["total_uninstalls"] = conn.execute(
                    "SELECT COUNT(*) FROM extension_history WHERE action = 'uninstall'"
                ).fetchone()[0]
                stats["total_scans"] = conn.execute(
                    "SELECT COUNT(*) FROM workspace_scans"
                ).fetchone()[0]
                stats["user_preferences"] = conn.execute(
                    "SELECT COUNT(*) FROM user_preferences"
                ).fetchone()[0]
        except Exception:
            pass  # error no crítico, continuar
        return stats

    def get_history(self, limit: int = 20) -> List[Dict]:
        """Historial de acciones recientes."""
        try:
            with get_conn_ctx(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT ext_id, action, reason, timestamp FROM extension_history ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [{"ext_id": r[0], "action": r[1], "reason": r[2], "timestamp": r[3]} for r in rows]
        except Exception:
            return []

    def search_extensions(self, query: str) -> List[Dict]:
        """Busca extensiones en la base de conocimiento."""
        query_lower = query.lower()
        results = []
        for lang, exts in EXTENSION_DB.items():
            if lang.startswith("_"):
                continue
            for ext in exts:
                score = 0
                if query_lower in ext.ext_id.lower():
                    score += 5
                if query_lower in ext.name.lower():
                    score += 3
                if query_lower in ext.reason.lower():
                    score += 1
                if query_lower in lang:
                    score += 2
                if score > 0:
                    results.append({
                        "ext_id": ext.ext_id,
                        "name": ext.name,
                        "language": lang,
                        "reason": ext.reason,
                        "priority": ext.priority,
                        "score": score,
                        "installed": ext.ext_id.lower() in (self._installed_cache or set()),
                    })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:20]


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_ext_intel: Optional[ExtensionIntelligence] = None


def get_extension_intelligence() -> ExtensionIntelligence:
    global _ext_intel
    if _ext_intel is None:
        _ext_intel = ExtensionIntelligence()
    return _ext_intel


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  EIDOS ExtensionIntelligence — Status")
    print("=" * 60)

    ei = get_extension_intelligence()
    status = ei.get_status()
    print(f"\n  Binary: {status['code_binary']}")
    print(f"  VSEIDOS: {status['is_vseidos']}")
    print(f"  Installed: {status['installed_count']}")

    print("\n  Scanning workspace...")
    scan = ei.analyze_workspace()
    print(f"  Languages: {sorted(scan.detected_languages)}")
    print(f"  Files: {scan.file_counts}")
    print(f"  Recommended: {len(scan.recommended)}")
    print(f"  Already installed: {len(scan.already_installed)}")
    print(f"  To install: {len(scan.to_install)}")
    print(f"  Scan time: {scan.scan_time:.2f}s")

    if scan.to_install:
        print("\n  Pendientes:")
        for ext in scan.to_install[:10]:
            print(f"    [{ext.priority}] {ext.ext_id} — {ext.reason}")

    print("\n  ExtensionIntelligence funcional")
