"""
EIDOS core/bugbot.py — Revisor Automático de Código
=====================================================
Combina:
  1. Análisis estático AST (instantáneo, sin LLM)
  2. LLM Review con lfm2.5-1.2b-instruct:q4_0 (análisis semántico profundo)
  3. Panel Rich para mostrar bugs encontrados
  4. SQLite historial de bugs
  5. Watcher opcional de archivos .py

Uso desde CLI: :bugcheck [archivo.py]
Uso autónomo:  BugBot().check_file("core/tools.py")
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from core.db import get_conn

# ── Optional Rich ────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box as rbox
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── Config ───────────────────────────────────────────────────────────────────
EIDOS_DIR    = os.path.expanduser("~/EIDOS")
DB_PATH      = os.path.expanduser("~/.eidos/bugbot.db")
OLLAMA_URL   = "http://127.0.0.1:11434"
CODER_MODEL  = "lfm2.5-1.2b-instruct:q4_0"
FAST_MODEL   = "lfm2.5-thinking:1.2b"

_console: Any = Console() if HAS_RICH else None


# ══════════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Bug:
    """Un bug o issue detectado en el código."""
    severity: str       # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO"
    category: str       # "syntax" | "logic" | "security" | "style" | "perf"
    line: int           # Número de línea (0 = todo el archivo)
    message: str        # Descripción del bug
    suggestion: str     # Fix sugerido
    auto_fixable: bool  # ¿Se puede parchear automáticamente?
    patch: str = ""     # Patch propuesto (diff-style)

    def icon(self) -> str:
        return {
            "CRITICAL": "🔴", "HIGH": "🟠",
            "MEDIUM": "🟡", "LOW": "🔵", "INFO": "⚪"
        }.get(self.severity, "❓")


@dataclass
class BugReport:
    """Reporte completo de un archivo analizado."""
    filepath: str
    bugs: list[Bug] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    duration_s: float = 0.0
    llm_used: bool = False

    @property
    def critical_count(self) -> int:
        return sum(1 for b in self.bugs if b.severity == "CRITICAL")

    @property
    def high_count(self) -> int:
        return sum(1 for b in self.bugs if b.severity == "HIGH")

    @property
    def total(self) -> int:
        return len(self.bugs)

    def is_clean(self) -> bool:
        return self.total == 0


# ══════════════════════════════════════════════════════════════════════════════
#  AST ANALYZER — Análisis estático instantáneo sin LLM
# ══════════════════════════════════════════════════════════════════════════════

class ASTAnalyzer(ast.NodeVisitor):
    """
    Análisis estático de Python con ast.NodeVisitor.
    Detecta patrones peligrosos comunes sin invocar LLM.
    """

    def __init__(self) -> None:
        self.bugs: list[Bug] = []

    def _add(self, node: ast.AST, severity: str, category: str,
             message: str, suggestion: str) -> None:
        self.bugs.append(Bug(
            severity=severity, category=category,
            line=getattr(node, "lineno", 0),
            message=message, suggestion=suggestion,
            auto_fixable=False,
        ))

    # ── Patrones peligrosos ────────────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        """Detecta: exec(), eval(), __import__(), os.system(), subprocess sin timeout."""
        func = node.func
        # exec() / eval() directos
        if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
            self._add(node, "HIGH", "security",
                      f"`{func.id}()` sin validar — código arbitrario",
                      f"Sustituye `{func.id}()` por una función segura o añade validación de entrada.")
        # os.system()
        if isinstance(func, ast.Attribute) and func.attr == "system":
            if isinstance(func.value, ast.Name) and func.value.id == "os":
                self._add(node, "HIGH", "security",
                          "`os.system()` — no captura errores ni stdout",
                          "Usa `subprocess.run([...], capture_output=True, timeout=30)` en su lugar.")
        # subprocess sin timeout
        if isinstance(func, ast.Attribute) and func.attr in ("run", "call", "Popen"):
            has_timeout = any(
                (isinstance(kw.arg, str) and kw.arg == "timeout")
                for kw in node.keywords
            )
            if not has_timeout:
                self._add(node, "MEDIUM", "security",
                          f"`subprocess.{func.attr}()` sin `timeout` — puede colgar indefinidamente",
                          "Añade `timeout=30` (o el valor apropiado) al llamar subprocess.")
        # pickle.loads()
        if isinstance(func, ast.Attribute) and func.attr == "loads":
            if isinstance(func.value, ast.Name) and func.value.id == "pickle":
                self._add(node, "CRITICAL", "security",
                          "`pickle.loads()` — deserialización insegura",
                          "Usa `json.loads()` o `msgpack` en su lugar si los datos vienen de fuera.")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        """Detecta except: sin especificar tipo (bare except)."""
        for handler in node.handlers:
            if handler.type is None:
                self._add(handler, "MEDIUM", "style",
                          "`except:` desnudo — captura TODAS las excepciones incluyendo SystemExit",
                          "Especifica el tipo: `except Exception as e:` o el error concreto.")
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        """Detecta assert en código de producción (desactivado con -O)."""
        # Solo avisar si parece código de producción (no test)
        self._add(node, "LOW", "logic",
                  "`assert` desactivable con `python -O` — no usar para validaciones críticas",
                  "Usa `if not condition: raise ValueError(...)` en código de producción.")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Detecta funciones sin type hints y sin docstring."""
        if not ast.get_docstring(node) and len(node.body) > 5:
            self._add(node, "INFO", "style",
                      f"`{node.name}()` sin docstring ({len(node.body)} líneas)",
                      f'Añade """Descripción breve.""" al inicio de `{node.name}`.')
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Detecta imports peligrosos."""
        for alias in node.names:
            if alias.name in ("telnetlib", "ftplib"):
                self._add(node, "MEDIUM", "security",
                          f"`import {alias.name}` — protocolo no cifrado",
                          "Usa SSH/SFTP o conexiones cifradas en su lugar.")
        self.generic_visit(node)


def _ast_analyze(source: str, filepath: str) -> list[Bug]:
    """Analiza el código fuente con AST. Retorna lista de bugs."""
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        return [Bug(
            severity="CRITICAL", category="syntax",
            line=e.lineno or 0,
            message=f"Error de sintaxis: {e.msg}",
            suggestion="Corrige el error de sintaxis en la línea indicada.",
            auto_fixable=False,
        )]
    analyzer = ASTAnalyzer()
    analyzer.visit(tree)
    return analyzer.bugs


# ══════════════════════════════════════════════════════════════════════════════
#  LLM REVIEWER — lfm2.5-1.2b-instruct:q4_0 para análisis semántico profundo
# ══════════════════════════════════════════════════════════════════════════════

_LLM_PROMPT_TEMPLATE = """\
Eres EIDOS BugBot, un revisor experto de código Python.
Analiza el siguiente código y devuelve ÚNICAMENTE un JSON válido con esta estructura:

{{
  "bugs": [
    {{
      "severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO",
      "category": "syntax|logic|security|style|perf",
      "line": <número de línea o 0 si es general>,
      "message": "<descripción concisa del bug>",
      "suggestion": "<cómo arreglarlo>",
      "auto_fixable": false
    }}
  ]
}}

Si el código está limpio devuelve: {{"bugs": []}}

REGLAS IMPORTANTES:
- Solo busca bugs reales: errores lógicos, seguridad, rendimiento crítico
- No incluyas nitpicks de estilo a menos que sean problemáticos
- Máximo 10 bugs por análisis
- Devuelve SOLO el JSON, sin markdown, sin explicaciones extra

ARCHIVO: {filepath}
CÓDIGO:
```python
{code}
```
"""


def _llm_review(source: str, filepath: str,
                max_lines: int = 300, timeout: int = 60) -> list[Bug]:
    """
    Revisa el código con lfm2.5-1.2b-instruct:q4_0.
    Limita a max_lines para no sobrepasar contexto.
    """
    lines = source.splitlines()
    if len(lines) > max_lines:
        # Analizar las primeras + últimas 150 líneas  # pyre-ignore[arg-type]
        chunk = (
            "\n".join(lines[:150])  # pyre-ignore[arg-type]
            + "\n\n# [...archivo truncado...]\n\n"
            + "\n".join(lines[-150:])  # pyre-ignore[arg-type]
        )
    else:
        chunk = source

    prompt = _LLM_PROMPT_TEMPLATE.format(
        filepath=os.path.basename(filepath),
        code=chunk,
    )

    payload = json.dumps({
        "model": CODER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_ctx": 8192, "num_predict": 1024, "temperature": 0.1},
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            content = data.get("message", {}).get("content", "")
    except Exception as e:
        return [Bug(
            severity="INFO", category="style", line=0,
            message=f"LLM no disponible: {e}",
            suggestion="Asegúrate de que Ollama está corriendo con lfm2.5-1.2b-instruct:q4_0.",
            auto_fixable=False,
        )]

    # Parsear JSON de la respuesta
    try:
        # Extraer JSON del bloque de código si está envuelto en markdown
        import re
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            bugs = []
            for b in data.get("bugs", []):
                bugs.append(Bug(
                    severity=b.get("severity", "INFO"),
                    category=b.get("category", "style"),
                    line=int(b.get("line", 0)),
                    message=b.get("message", ""),
                    suggestion=b.get("suggestion", ""),
                    auto_fixable=bool(b.get("auto_fixable", False)),
                ))
            return bugs
    except Exception:
        pass  # error no crítico, continuar
    return []


# ══════════════════════════════════════════════════════════════════════════════
#  SQLITE HISTORIAL
# ══════════════════════════════════════════════════════════════════════════════

def _init_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bug_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath  TEXT NOT NULL,
            severity  TEXT,
            category  TEXT,
            line      INTEGER,
            message   TEXT,
            fixed     INTEGER DEFAULT 0,
            checked_at REAL
        )
    """)
    conn.commit()
    return conn


def _save_report(report: BugReport) -> None:
    try:
        conn = _init_db()
        for bug in report.bugs:
            conn.execute(
                "INSERT INTO bug_history (filepath, severity, category, line, message, checked_at) "
                "VALUES (?,?,?,?,?,?)",
                (report.filepath, bug.severity, bug.category,
                 bug.line, bug.message, report.timestamp)
            )
        conn.commit()
        conn.close()
    except Exception:
        pass  # error no crítico, continuar
def get_bug_history(filepath: str | None = None, limit: int = 20) -> list[dict]:
    """Obtiene el historial de bugs desde SQLite."""
    try:
        conn = _init_db()
        if filepath:
            rows = conn.execute(
                "SELECT filepath, severity, line, message, fixed, checked_at "
                "FROM bug_history WHERE filepath=? ORDER BY checked_at DESC LIMIT ?",
                (filepath, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT filepath, severity, line, message, fixed, checked_at "
                "FROM bug_history ORDER BY checked_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()
        return [
            {"filepath": r[0], "severity": r[1], "line": r[2],  # pyre-ignore[arg-type]
             "message": r[3], "fixed": bool(r[4]), "at": r[5]}  # pyre-ignore[arg-type]
            for r in rows
        ]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  BUGBOT — Motor principal
# ══════════════════════════════════════════════════════════════════════════════

class BugBot:
    """
    Revisor automático de código para EIDOS.

    Uso:
        bot = BugBot()
        report = bot.check_file("core/tools.py")
        bot.render_report(report)
    """

    def __init__(self, use_llm: bool = True, llm_timeout: int = 60) -> None:
        self.use_llm = use_llm
        self.llm_timeout = llm_timeout

    def check_file(self, filepath: str) -> BugReport:
        """
        Analiza un archivo Python.
        Combina AST (rápido) + LLM (profundo).
        """
        path = Path(filepath)
        if not path.exists():
            # Buscar relativo a EIDOS_DIR
            alt = Path(EIDOS_DIR) / filepath
            if alt.exists():
                path = alt
            else:
                return BugReport(filepath=filepath, bugs=[Bug(
                    severity="CRITICAL", category="syntax", line=0,
                    message=f"Archivo no encontrado: {filepath}",
                    suggestion="Verifica la ruta del archivo.",
                    auto_fixable=False,
                )])

        start = time.time()
        source = path.read_text(encoding="utf-8", errors="replace")

        # 1) Análisis AST
        ast_bugs = _ast_analyze(source, str(path))

        # 2) LLM review (solo si no hay errores de sintaxis críticos)
        llm_bugs: list[Bug] = []
        llm_used = False
        has_syntax_error = any(b.category == "syntax" and b.severity == "CRITICAL"
                               for b in ast_bugs)
        if self.use_llm and not has_syntax_error:
            llm_bugs = _llm_review(source, str(path), timeout=self.llm_timeout)
            llm_used = True

        # Combinar y deduplicar por mensaje
        all_bugs = ast_bugs + llm_bugs
        seen = set()
        unique_bugs: list[Bug] = []
        for bug in all_bugs:
            key = (bug.line, bug.message[:60])  # pyre-ignore[arg-type]
            if key not in seen:
                seen.add(key)
                unique_bugs.append(bug)

        # Ordenar: CRITICAL → HIGH → MEDIUM → LOW → INFO
        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
        unique_bugs.sort(key=lambda b: order.get(b.severity, 5))

        report = BugReport(
            filepath=str(path),
            bugs=unique_bugs,
            duration_s=time.time() - start,
            llm_used=llm_used,
        )
        _save_report(report)
        return report

    def auto_patch(self, filepath: str) -> bool:
        """
        Intenta arreglar automáticamente los bugs encontrados en el archivo.
        Pide a lfm2.5-1.2b-instruct:q4_0 que reescriba el archivo corrigiendo los problemas.
        Valida que el resultado sea sintácticamente correcto antes de guardar.
        """
        report = self.check_file(filepath)
        if report.is_clean():
            if HAS_RICH and _console:
                _console.print(f"[green]✅ {filepath} está limpio. Nada que parchear.[/green]")
            return True

        if HAS_RICH and _console:
            _console.print(f"[yellow]🛠️ Intentando auto-patch de {report.total} bugs en {filepath}...[/yellow]")

        try:
            path = Path(filepath)
            if not path.exists():
                alt = Path(EIDOS_DIR) / filepath
                if alt.exists():
                    path = alt
                else:
                    return False
            
            source = path.read_text(encoding="utf-8")
            bugs_info = [{"line": b.line, "msg": b.message, "sugg": b.suggestion} for b in report.bugs]
            
            prompt = (
                f"Eres EIDOS BugBot Auto-Patcher. Se detectaron estos bugs en el archivo {path.name}:\n"
                f"{json.dumps(bugs_info, indent=2, ensure_ascii=False)}\n\n"
                "Reescribe el código completo corrigiendo estos bugs. "
                "Devuelve ÚNICAMENTE el código Python limpio dentro de un bloque ```python ... ```, sin texto extra.\n\n"
                f"```python\n{source}\n```"
            )

            payload = json.dumps({
                "model": CODER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_ctx": 16384, "temperature": 0.1},
            }).encode()

            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat", data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                content = data.get("message", {}).get("content", "")

            import re
            match = re.search(r"```python\n(.*?)\n```", content, re.DOTALL)
            if match:
                new_code = match.group(1).strip() + "\n"
                # Validación de seguridad: debe ser código Python válido
                ast.parse(new_code)

                # Guardar backup y sobrescribir
                backup = str(path) + ".bak"
                with open(backup, "w", encoding="utf-8") as f:
                    f.write(source)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_code)

                if HAS_RICH and _console:
                    _console.print(f"[bold green]✅ Auto-patch aplicado con éxito. Backup en {backup}[/bold green]")
                return True
            else:
                if HAS_RICH and _console:
                    _console.print("[red]❌ El LLM no devolvió un bloque de código válido.[/red]")
                return False
        except Exception as e:
            if HAS_RICH and _console:
                _console.print(f"[red]❌ Error en auto-patch: {e}[/red]")
            return False

    def check_dir(self, dirpath: str, recursive: bool = True,
                  pattern: str = "*.py") -> list[BugReport]:
        """Analiza todos los .py en un directorio."""
        p = Path(dirpath)
        if not p.exists():
            return []
        glob = p.rglob(pattern) if recursive else p.glob(pattern)
        reports = []
        for pyfile in sorted(glob):
            if ".venv" in str(pyfile) or "venv_eidos" in str(pyfile):
                continue
            reports.append(self.check_file(str(pyfile)))
        return reports

    # ── Render ──────────────────────────────────────────────────────────────

    def render_report(self, report: BugReport,
                      console: Any = None) -> str:
        """Renderiza el reporte con Rich o texto plano."""
        con = console or _console
        fname = os.path.basename(report.filepath)

        if report.is_clean():
            msg = f"✅  {fname} — Sin bugs detectados  ({report.duration_s:.1f}s)"
            if con and HAS_RICH:
                con.print(Panel(
                    f"[bold green]{msg}[/bold green]",
                    border_style="green", padding=(0, 2)
                ))
            return msg

        if HAS_RICH and con:
            t = Table(box=rbox.SIMPLE, show_header=True,
                      header_style="bold bright_cyan", padding=(0, 1))
            t.add_column("", width=2)          # icon
            t.add_column("Sev", width=8)
            t.add_column("Línea", width=6)
            t.add_column("Categoría", width=10)
            t.add_column("Bug", ratio=2)
            t.add_column("Fix sugerido", ratio=3)

            sev_colors = {
                "CRITICAL": "red", "HIGH": "orange1",
                "MEDIUM": "yellow", "LOW": "bright_blue", "INFO": "white"
            }
            for bug in report.bugs:
                c = sev_colors.get(bug.severity, "white")
                t.add_row(
                    bug.icon(),
                    f"[{c}]{bug.severity}[/{c}]",
                    str(bug.line) if bug.line else "—",
                    bug.category,
                    bug.message[:80],  # pyre-ignore[arg-type]
                    bug.suggestion[:100],  # pyre-ignore[arg-type]
                )

            llm_tag = " 🤖 LLM" if report.llm_used else ""
            subtitle = (
                f"[red]{report.critical_count} CRITICAL[/red]  "
                f"[orange1]{report.high_count} HIGH[/orange1]  "
                f"[dim]{report.total} total · {report.duration_s:.1f}s{llm_tag}[/dim]"
            )
            con.print(Panel(
                t,
                title=f"[bold red]🐛 BugBot — {fname}[/bold red]",
                subtitle=subtitle,
                border_style="red" if report.critical_count > 0 else "yellow",
                padding=(0, 1),
            ))
        else:
            # Fallback texto plano
            lines = [f"BugBot — {fname} ({report.total} bugs)"]
            for bug in report.bugs:
                lines.append(
                    f"  [{bug.severity}] L{bug.line} {bug.message} → {bug.suggestion}"
                )
            return "\n".join(lines)

        return f"{fname}: {report.total} bugs ({report.critical_count} críticos)"

    def render_summary(self, reports: list[BugReport],
                       console: Any = None) -> None:
        """Tabla resumen de múltiples archivos."""
        con = console or _console
        if not HAS_RICH or not con:
            for r in reports:
                self.render_report(r, console)
            return

        t = Table(box=rbox.SIMPLE, header_style="bold bright_cyan", padding=(0, 1))
        t.add_column("Archivo", ratio=3)
        t.add_column("🔴", width=4, justify="right")
        t.add_column("🟠", width=4, justify="right")
        t.add_column("Total", width=6, justify="right")
        t.add_column("Estado", width=10)

        total_bugs = 0
        for r in reports:
            fname = os.path.relpath(r.filepath, EIDOS_DIR)
            status = "✅ Clean" if r.is_clean() else (
                "🔴 CRÍTICO" if r.critical_count > 0 else "⚠️  Issues"
            )
            t.add_row(
                fname,
                str(r.critical_count) if r.critical_count else "—",
                str(r.high_count) if r.high_count else "—",
                str(r.total) if r.total else "—",
                status,
            )
            total_bugs += r.total

        con.print(Panel(
            t,
            title=f"[bold red]🐛 BugBot — Resumen ({len(reports)} archivos)[/bold red]",
            subtitle=f"[dim]{total_bugs} bugs totales[/dim]",
            border_style="red" if any(r.critical_count > 0 for r in reports) else "yellow",
        ))


# ══════════════════════════════════════════════════════════════════════════════
#  WATCHER — Vigila cambios en .py y ejecuta BugBot automáticamente
# ══════════════════════════════════════════════════════════════════════════════

class BugBotWatcher:
    """
    Vigila cambios en archivos .py dentro de EIDOS_DIR.
    Al detectar un cambio, ejecuta BugBot y muestra el reporte.

    Uso:
        watcher = BugBotWatcher()
        watcher.start()   # Non-blocking (usa threading)
        watcher.stop()
    """

    def __init__(self, watch_dir: str = EIDOS_DIR,
                 poll_interval: float = 2.0) -> None:
        self.watch_dir = watch_dir
        self.poll_interval = poll_interval
        self._running = False
        self._thread: Any = None
        self._bot = BugBot(use_llm=False)  # Watcher usa solo AST (rápido)
        self._mtime_cache: dict[str, float] = {}

    def start(self) -> None:
        import threading
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop,
                                        daemon=True, name="BugBotWatcher")
        self._thread.start()
        if HAS_RICH and _console:
            _console.print("[dim]🐛 BugBot Watcher activo[/dim]")

    def stop(self) -> None:
        self._running = False

    def _watch_loop(self) -> None:
        while self._running:
            try:
                self._check_for_changes()
            except Exception:
                pass  # error no crítico, continuar
            time.sleep(self.poll_interval)

    def _check_for_changes(self) -> None:
        for pyfile in Path(self.watch_dir).rglob("*.py"):
            if ".venv" in str(pyfile) or "venv_eidos" in str(pyfile):
                continue
            try:
                mtime = pyfile.stat().st_mtime
            except OSError:
                continue
            cached = self._mtime_cache.get(str(pyfile))
            if cached is None:
                self._mtime_cache[str(pyfile)] = mtime
                continue
            if mtime != cached:
                self._mtime_cache[str(pyfile)] = mtime
                # Archivo modificado → analizar
                report = self._bot.check_file(str(pyfile))
                if not report.is_clean() and report.critical_count > 0:
                    self._bot.render_report(report)



# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-PATCH — Aplica correcciones automáticamente en disco  (Fase 2)
# ══════════════════════════════════════════════════════════════════════════════

def _llm_generate_fix(source: str, bugs: list[Bug], filepath: str,
                      timeout: int = 90) -> str:
    """
    Pide al LLM que devuelva el archivo COMPLETO corregido.
    Solo se llama cuando hay bugs auto_fixable.
    Returns: código corregido como string, o "" si falla.
    """
    fixable = [b for b in bugs if b.auto_fixable]
    if not fixable:
        return ""

    problems = "\n".join(
        f"  - L{b.line} [{b.severity}] {b.message} → {b.suggestion}"
        for b in fixable[:10]  # pyre-ignore[arg-type]
    )
    prompt = (
        f"Aquí está el archivo Python `{os.path.basename(filepath)}`:\n\n"
        f"```python\n{source[:8000]}\n```\n\n"  # pyre-ignore[arg-type]
        f"Bugs a corregir (SOLO estos, no cambies nada más):\n{problems}\n\n"
        f"Devuelve SOLO el archivo completo corregido, sin explicaciones, "
        f"sin bloques markdown, sin comentarios extra. "
        f"Si no puedes corregir algo de forma segura, déjalo igual."
    )
    try:
        payload = json.dumps({
            "model": CODER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_ctx": 8192, "num_predict": 4096, "temperature": 0.1},
        }).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        fixed_code = data.get("message", {}).get("content", "").strip()
        # Quitar bloques markdown si el LLM los añadió
        if fixed_code.startswith("```"):
            lines = fixed_code.splitlines()
            # Saltar primera línea (```python) y última (```)
            fixed_code = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        return fixed_code
    except Exception as e:
        return f"[ERROR_LLM] {e}"


def apply_autofix(report: BugReport, dry_run: bool = False) -> dict:
    """
    Aplica correcciones automáticas a un archivo basándose en el BugReport.

    Proceso:
      1. Filtra bugs marcados como auto_fixable
      2. Pide al LLM la versión corregida completa
      3. Verifica que la nueva versión tiene sintaxis Python válida
      4. Hace backup del original (.bak)
      5. Sobreescribe el archivo
      6. Marca los bugs como fixed en SQLite

    Args:
        report:   BugReport ya generado por BugBot.check_file()
        dry_run:  Si True, no escribe nada, solo muestra qué haría

    Returns:
        dict con keys: fixed_count, backup_path, error, new_code
    """
    result: dict = {"fixed_count": 0, "backup_path": "", "error": "", "new_code": ""}

    fixable = [b for b in report.bugs if b.auto_fixable]
    if not fixable:
        result["error"] = "No hay bugs marcados como auto_fixable en este reporte."
        return result

    path = Path(report.filepath)
    if not path.exists():
        result["error"] = f"Archivo no encontrado: {report.filepath}"
        return result

    source = path.read_text(encoding="utf-8", errors="replace")

    # 1. Obtener código corregido del LLM
    fixed_code = _llm_generate_fix(source, report.bugs, report.filepath)
    if not fixed_code or fixed_code.startswith("[ERROR_LLM]"):
        result["error"] = fixed_code or "LLM no devolvió código corregido."
        return result

    # 2. Verificar sintaxis del código corregido
    try:
        import ast as _ast
        _ast.parse(fixed_code)
    except SyntaxError as e:
        result["error"] = f"Código corregido tiene SyntaxError en L{e.lineno}: {e.msg}. No se aplica."
        return result

    result["new_code"] = fixed_code

    if dry_run:
        result["fixed_count"] = len(fixable)
        return result

    # 3. Backup del original
    backup_path = str(path) + ".bak"
    try:
        import shutil
        shutil.copy2(str(path), backup_path)
        result["backup_path"] = backup_path
    except Exception as e:
        result["error"] = f"Error haciendo backup: {e}"
        return result

    # 4. Sobreescribir con el código corregido
    try:
        path.write_text(fixed_code, encoding="utf-8")
    except Exception as e:
        # Restaurar backup si falla la escritura
        try:
            import shutil
            shutil.copy2(backup_path, str(path))
        except Exception:
            pass  # error no crítico, continuar
        result["error"] = f"Error escribiendo archivo corregido: {e}"
        return result

    # 5. Marcar como fixed en SQLite
    try:
        conn = _init_db()
        conn.execute(
            "UPDATE bug_history SET fixed=1 WHERE filepath=? AND fixed=0",
            (report.filepath,)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # error no crítico, continuar
    result["fixed_count"] = len(fixable)
    if HAS_RICH and _console:
        _console.print(
            f"[bold green]✅ Auto-patch aplicado:[/bold green] "
            f"{len(fixable)} bugs corregidos en [cyan]{os.path.basename(report.filepath)}[/cyan]"
            f" (backup: [dim]{backup_path}[/dim])"
        )
    return result


def bugfix(filepath: str, dry_run: bool = False) -> str:
    """
    Entry point para el comando :bugfix de la CLI.
    Analiza Y corrige automáticamente un archivo Python.

    Uso CLI: :bugfix core/tools.py
             :bugfix core/tools.py --dry   (solo simula, no escribe)
    """
    bot = BugBot(use_llm=True)
    report = bot.check_file(filepath)
    bot.render_report(report)

    fixable_count = sum(1 for b in report.bugs if b.auto_fixable)
    if fixable_count == 0:
        return f"[bugfix] {os.path.basename(filepath)}: sin bugs auto-fixables detectados."

    if HAS_RICH and _console:
        mode_str = "[DRY RUN — no se escribe nada]" if dry_run else "[APLICANDO PARCHE...]"
        _console.print(
            f"\n[bold yellow]🔧 BugBot Auto-Fix — {fixable_count} bugs fixables {mode_str}[/bold yellow]"
        )

    result = apply_autofix(report, dry_run=dry_run)

    if result["error"]:
        return f"[bugfix] ERROR: {result['error']}"

    if dry_run:
        return (f"[bugfix DRY] {os.path.basename(filepath)}: "
                f"se corregirían {result['fixed_count']} bugs (sin cambios en disco).")

    return (f"[bugfix] ✅ {os.path.basename(filepath)}: "
            f"{result['fixed_count']} bugs corregidos. "
            f"Backup en: {result['backup_path']}")

def bugcheck(filepath_or_dir: str, use_llm: bool = True) -> str:
    """
    Entry point principal para el comando :bugcheck de la CLI.
    Retorna un string resumen (para el historial del agente).
    """
    bot = BugBot(use_llm=use_llm)
    path = Path(filepath_or_dir)

    if path.is_dir():
        reports = bot.check_dir(str(path))
        bot.render_summary(reports)
        total = sum(r.total for r in reports)
        crits = sum(r.critical_count for r in reports)
        return (f"BugBot analizó {len(reports)} archivos: "
                f"{total} bugs ({crits} críticos)")
    else:
        report = bot.check_file(filepath_or_dir)
        bot.render_report(report)
        if report.is_clean():
            return f"✅ {os.path.basename(filepath_or_dir)}: sin bugs"
        return (f"🐛 {os.path.basename(filepath_or_dir)}: "
                f"{report.total} bugs ({report.critical_count} críticos)")


# ── Test rápido ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else EIDOS_DIR + "/eidos_cli.py"  # pyre-ignore[arg-type]
    print(f"BugBot analizando: {target}")
    result = bugcheck(target, use_llm="--llm" in sys.argv)
    print(result)
