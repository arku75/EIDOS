#!/usr/bin/env python3
"""
scripts/migrate_to_core_db.py — Migración automática de sqlite3.connect() a core.db.get_conn()

S109: Reemplaza sqlite3.connect() → get_conn() en todos los archivos .py de core/.
Mantiene backup .bak-S109 de cada archivo modificado.
"""

import re
import sys
from pathlib import Path

CORE_DIR = Path(__file__).parent.parent / "core"

# Archivos a SALTAR (no migrar)
SKIP_FILES = {
    "db.py",                    # La capa DB misma
    "graphify_bridge.py",       # Usa sqlite3 de graphify, no de EIDOS
}

# Patrones de reemplazo
# 1. sqlite3.connect(str(DB_VAR), timeout=X) -> get_conn(DB_VAR, timeout=X)
RE_CONNECT_VAR = re.compile(
    r"sqlite3\.connect\(\s*str\(\s*(\w+)\s*\)\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# 2. sqlite3.connect(str(Path.home() / ".eidos" / "xxx.db"), ...) -> get_conn(Path...)
RE_CONNECT_PATH = re.compile(
    r"sqlite3\.connect\(\s*str\((Path\.home\(\)\s*/\s*\"\.eidos\"\s*/\s*\"([^\"]+)\")\s*\)\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# 3. sqlite3.connect("...", ...) -> get_conn("...", ...)
RE_CONNECT_STR = re.compile(
    r"sqlite3\.connect\((\"[^\"]+\")\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# 4. sqlite3.connect(DB_PATH, timeout=X) - where DB_PATH is a pre-computed variable
RE_CONNECT_SIMPLE = re.compile(
    r"sqlite3\.connect\(\s*(\w+)\s*,\s*timeout\s*=\s*(\d+)\s*\)"
)

# 5. conn.close() lines (standalone)
RE_CLOSE = re.compile(r"^\s*(conn\d*)\.close\(\)\s*$", re.MULTILINE)


def migrate_file(filepath: Path) -> tuple[int, int]:
    """Migra un archivo. Retorna (connects_reemplazados, closes_eliminados)."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return (0, 0)

    original = content
    connects = 0
    closes = 0

    # Ya migrado?
    if "from core.db import get_conn" in content:
        # Solo eliminar .close() si no se ha hecho
        new_content, n = RE_CLOSE.subn("", content)
        if n > 0:
            closes = n
            content = new_content
    else:
        # Contar cuántos sqlite3.connect hay
        connect_count = len(re.findall(r"sqlite3\.connect\(", content))
        if connect_count == 0:
            return (0, 0)

        # Reemplazar sqlite3.connect por get_conn
        # Patrón 1: sqlite3.connect(str(VAR), timeout=X)
        new_content, n1 = RE_CONNECT_VAR.subn(r"get_conn(\1, timeout=\2)", content)
        # Patrón 2: sqlite3.connect(str(Path.home() / ".eidos" / "xxx.db"), timeout=X)
        new_content, n2 = RE_CONNECT_PATH.subn(r"get_conn(\1, timeout=\3)", new_content)
        # Patrón 3: sqlite3.connect("...", timeout=X)
        new_content, n3 = RE_CONNECT_STR.subn(r"get_conn(\1, timeout=\2)", new_content)
        # Patrón 4: sqlite3.connect(DB_VAR, timeout=X)
        new_content, n4 = RE_CONNECT_SIMPLE.subn(r"get_conn(\1, timeout=\2)", new_content)

        total_replaced = n1 + n2 + n3 + n4
        if total_replaced == 0:
            return (0, 0)

        # Añadir import de get_conn si no existe
        if "from core.db import get_conn" not in new_content:
            # Buscar el último import o la primera línea no-import
            lines = new_content.split("\n")
            last_import_idx = -1
            for i, line in enumerate(lines):
                if line.startswith("import ") or line.startswith("from "):
                    last_import_idx = i

            if last_import_idx >= 0:
                lines.insert(last_import_idx + 1, "from core.db import get_conn")
            new_content = "\n".join(lines)

        # Eliminar líneas .close()
        new_content, n_close = RE_CLOSE.subn("", new_content)

        connects = total_replaced
        closes = n_close
        content = new_content

    if content != original:
        # Backup
        backup = filepath.with_suffix(filepath.suffix + ".bak-S109")
        backup.write_text(original, encoding="utf-8")
        filepath.write_text(content, encoding="utf-8")
        return (connects, closes)

    return (0, 0)


def main():
    dry_run = "--dry-run" in sys.argv
    verbose = "--verbose" in sys.argv

    total_connects = 0
    total_closes = 0
    files_migrated = 0
    files_skipped = 0

    for py_file in sorted(CORE_DIR.glob("*.py")):
        if py_file.name in SKIP_FILES:
            if verbose:
                print(f"  SKIP {py_file.name} (en lista de exclusión)")
            files_skipped += 1
            continue

        if dry_run:
            # Solo contar
            content = py_file.read_text(encoding="utf-8")
            n = len(re.findall(r"sqlite3\.connect\(", content))
            if n > 0:
                print(f"  WOULD MIGRATE {py_file.name}: {n} connects")
                total_connects += n
                files_migrated += 1
        else:
            n_conn, n_close = migrate_file(py_file)
            if n_conn > 0 or n_close > 0:
                print(f"  ✅ {py_file.name}: {n_conn} connects → get_conn, {n_close} closes eliminados")
                total_connects += n_conn
                total_closes += n_close
                files_migrated += 1
            else:
                if verbose and "sqlite3.connect" in py_file.read_text(encoding="utf-8"):
                    print(f"  ⚠️  {py_file.name}: tiene sqlite3.connect pero no se pudo migrar automáticamente")

    print(f"\n{'DRY RUN — ' if dry_run else ''}Resultado: {files_migrated} archivos migrados, "
          f"{total_connects} connects reemplazados, {total_closes} closes eliminados, "
          f"{files_skipped} excluidos")

    if dry_run:
        print(f"  Conectores restantes después de migración: {609 - total_connects}")


if __name__ == "__main__":
    main()
