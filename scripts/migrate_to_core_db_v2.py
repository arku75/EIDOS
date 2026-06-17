#!/usr/bin/env python3
"""
scripts/migrate_to_core_db_v2.py — Migración ROBUSTA sqlite3.connect → get_conn [S109]

v2: Maneja try/finally, with statements, self.xxx paths, y preserva estructura.
Cada archivo se respalda en .bak-S109 antes de modificar.
"""

import re
import sys
from pathlib import Path

CORE_DIR = Path(__file__).parent.parent / "core"

SKIP_FILES = {"db.py"}  # La capa DB misma

# ── Patrones de sqlite3.connect ──────────────────────────────────────────

# sqlite3.connect(str(VAR), timeout=X)  —  VAR es variable global/local
RE_VAR_TIMEOUT = re.compile(
    r"(\bconn\d*\s*=\s*)sqlite3\.connect\(\s*str\(\s*(\w+)\s*\)\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# sqlite3.connect(str(self.attr), timeout=X)
RE_SELF_TIMEOUT = re.compile(
    r"(\bconn\d*\s*=\s*)sqlite3\.connect\(\s*str\(\s*self\.(\w+)\s*\)\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# sqlite3.connect(str(self.attr))  sin timeout
RE_SELF_NO_TIMEOUT = re.compile(
    r"(\bconn\d*\s*=\s*)sqlite3\.connect\(\s*str\(\s*self\.(\w+)\s*\)\s*\)"
)

# sqlite3.connect(str(VAR))  sin timeout
RE_VAR_NO_TIMEOUT = re.compile(
    r"(\bconn\d*\s*=\s*)sqlite3\.connect\(\s*str\(\s*(\w+)\s*\)\s*\)"
)

# sqlite3.connect(str(Path.home() / ".eidos" / "xxx.db"), timeout=X)
RE_PATH_TIMEOUT = re.compile(
    r"(\bconn\d*\s*=\s*)sqlite3\.connect\(\s*str\(\s*(Path\.home\(\)\s*/\s*\"\.eidos\"\s*/\s*\"[^\"]+\")\s*\)\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# sqlite3.connect(VAR, timeout=X)  — VAR es variable (sin str())
RE_DIRECT_TIMEOUT = re.compile(
    r"(\bconn\d*\s*=\s*)sqlite3\.connect\(\s*(\w+)\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# sqlite3.connect(str(VAR), timeout=X, **extra)
RE_VAR_TIMEOUT_EXTRA = re.compile(
    r"(\bconn\d*\s*=\s*)sqlite3\.connect\(\s*str\(\s*(\w+)\s*\)\s*,\s*timeout\s*=\s*(\d+)\s*,\s*(\*\*?\w+)\s*\)"
)

# with sqlite3.connect(VAR) as conn:
RE_WITH_VAR = re.compile(
    r"with sqlite3\.connect\((\w+)\) as (\w+):"
)

# with sqlite3.connect(str(VAR)) as conn:
RE_WITH_STR = re.compile(
    r"with sqlite3\.connect\(str\((\w+)\)\) as (\w+):"
)

# with sqlite3.connect(str(self.attr)) as conn:
RE_WITH_SELF = re.compile(
    r"with sqlite3\.connect\(str\(self\.(\w+)\)\) as (\w+):"
)


def add_import(content: str) -> str:
    """Añade 'from core.db import get_conn' si no existe."""
    if "from core.db import get_conn" in content:
        return content

    lines = content.split("\n")
    # Buscar última línea de import
    last_import = -1
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            # No insertar después de __future__
            if "__future__" not in line:
                last_import = i

    if last_import >= 0:
        lines.insert(last_import + 1, "from core.db import get_conn")
    else:
        # Insertar después de docstring
        in_docstring = False
        for i, line in enumerate(lines):
            if line.strip().startswith('"""') or line.strip().startswith("'''"):
                if not in_docstring:
                    in_docstring = True
                else:
                    lines.insert(i + 1, "")
                    lines.insert(i + 2, "from core.db import get_conn")
                    break
            elif not in_docstring:
                lines.insert(i, "")
                lines.insert(i, "from core.db import get_conn")
                break

    return "\n".join(lines)


def fix_empty_blocks(content: str) -> str:
    """Arregla bloques try/finally o with que quedaron vacíos tras eliminar close()."""
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip()
        # Buscar finally: que está seguido por una línea vacía o no-indentada (bloque vacío)
        if re.match(r"^\s*finally\s*:\s*$", stripped):
            # Ver si la siguiente línea tiene indentación
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if next_line.strip() == "":
                    # finally: seguido de línea vacía → añadir pass
                    indent = len(stripped) - len(stripped.lstrip())
                    lines.insert(i + 1, " " * (indent + 4) + "pass  # S109: get_conn no necesita close()")
                    i += 2
                    continue
                elif not next_line.startswith(" " * (len(stripped) - len(stripped.lstrip()) + 4)):
                    # La siguiente línea no está indentada correctamente → bloque vacío
                    indent = len(stripped) - len(stripped.lstrip())
                    lines.insert(i + 1, " " * (indent + 4) + "pass  # S109: get_conn no necesita close()")
                    i += 2
                    continue
        i += 1
    return "\n".join(lines)


def migrate_file(filepath: Path) -> tuple:
    """Migra un archivo. Retorna (connects_migrados, closes_eliminados, errores)."""
    try:
        original = filepath.read_text(encoding="utf-8")
    except Exception as e:
        return (0, 0, [str(e)])

    # Ya migrado completamente?
    if "sqlite3.connect" not in original:
        return (0, 0, [])

    content = original
    replaces = 0

    # Aplicar patrones uno por uno
    patterns = [
        (RE_VAR_TIMEOUT, r"\1get_conn(\2, timeout=\3)"),
        (RE_SELF_TIMEOUT, r"\1get_conn(self.\2, timeout=\3)"),
        (RE_SELF_NO_TIMEOUT, r"\1get_conn(self.\2)"),
        (RE_VAR_NO_TIMEOUT, r"\1get_conn(\2)"),
        (RE_PATH_TIMEOUT, r"\1get_conn(\2, timeout=\3)"),
        (RE_DIRECT_TIMEOUT, r"\1get_conn(\2, timeout=\3)"),
        (RE_VAR_TIMEOUT_EXTRA, r"\1get_conn(\2, timeout=\3, \4)"),
        (RE_WITH_VAR, r"with get_conn_ctx(\1) as \2:"),
        (RE_WITH_STR, r"with get_conn_ctx(\1) as \2:"),
        (RE_WITH_SELF, r"with get_conn_ctx(self.\1) as \2:"),
    ]

    for pattern, replacement in patterns:
        new_content, n = pattern.subn(replacement, content)
        if n > 0:
            replaces += n
            content = new_content

    if replaces == 0:
        return (0, 0, [])

    # Eliminar líneas conn.close() — pero NO dentro de bloques try/finally
    # Estrategia: reemplazar conn.close() por pass # S109 (preserva estructura)
    close_pattern = re.compile(r"^(\s*)conn\d*\.close\(\)\s*$", re.MULTILINE)
    content, closes = close_pattern.subn(r"\1pass  # S109: get_conn no necesita close()", content)

    # Añadir import
    if "get_conn" in content or "get_conn_ctx" in content:
        if "from core.db import get_conn" not in content:
            content = add_import(content)
        # Si se usa get_conn_ctx, asegurar que está importado
        if "get_conn_ctx" in content and "get_conn_ctx" not in content.split("from core.db import")[1].split("\n")[0] if "from core.db import" in content else True:
            content = content.replace(
                "from core.db import get_conn",
                "from core.db import get_conn, get_conn_ctx"
            )

    # Arreglar bloques vacíos
    content = fix_empty_blocks(content)

    # Eliminar import sqlite3 si ya no se usa
    if "sqlite3.connect" not in content and "sqlite3." not in content.replace("import sqlite3", ""):
        # No eliminar — sqlite3 puede usarse para otras cosas (Row, etc.)
        pass

    if content != original:
        # Backup
        backup = filepath.with_suffix(filepath.suffix + ".bak-S109")
        backup.write_text(original, encoding="utf-8")
        filepath.write_text(content, encoding="utf-8")
        return (replaces, closes, [])

    return (0, 0, [])


def main():
    dry_run = "--dry-run" in sys.argv

    total_connects = 0
    total_closes = 0
    files_migrated = 0
    errors_total = 0

    for py_file in sorted(CORE_DIR.glob("*.py")):
        if py_file.name in SKIP_FILES:
            continue
        if ".bak" in py_file.name:
            continue

        if dry_run:
            content = py_file.read_text(encoding="utf-8")
            n = len(re.findall(r"sqlite3\.connect\(", content))
            if n > 0:
                print(f"  WOULD MIGRATE {py_file.name}: {n} connects")
                total_connects += n
                files_migrated += 1
        else:
            n_conn, n_close, errors = migrate_file(py_file)
            if n_conn > 0:
                print(f"  ✅ {py_file.name}: {n_conn} connects → get_conn, {n_close} closes")
                total_connects += n_conn
                total_closes += n_close
                files_migrated += 1
            if errors:
                for e in errors:
                    print(f"  ❌ {py_file.name}: ERROR {e}")
                    errors_total += 1

    print(f"\n{'DRY RUN — ' if dry_run else ''}Resultado: {files_migrated} archivos migrados, "
          f"{total_connects} connects reemplazados, {total_closes} closes eliminados")

    # Verificar imports
    if not dry_run:
        import_errors = 0
        for py_file in sorted(CORE_DIR.glob("*.py")):
            if ".bak" in py_file.name:
                continue
            try:
                content = py_file.read_text()
                # Solo verificar archivos que fueron migrados
                if "from core.db import get_conn" not in content:
                    continue
                # Compilar para verificar sintaxis
                compile(content, str(py_file), "exec")
            except SyntaxError as e:
                print(f"  ❌ SYNTAX ERROR {py_file.name}:{e.lineno}: {e.msg}")
                import_errors += 1

        if import_errors:
            print(f"\n⚠️  {import_errors} archivos con errores de sintaxis tras migración")
        else:
            print(f"\n✅ Todos los archivos migrados compilan sin errores de sintaxis")


if __name__ == "__main__":
    main()
