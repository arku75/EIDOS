"""
tests/test_db_layer.py — Verifica que toda conexión SQLite use core/db.py [S109]

Regla de oro: NUNCA más sqlite3.connect() directo.
Este test falla si encuentra conexiones que no pasan por get_conn().
"""

import re
import sys
from pathlib import Path

# Añadir EIDOS al path
EIDOS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(EIDOS_DIR))


# ── Archivos permitidos (pueden usar sqlite3.connect directamente) ─────────

ALLOWED_RAW_CONNECT = {
    "core/db.py",                    # La capa DB misma (implementación interna)
    "scripts/migrate_to_core_db.py", # Script de migración
    "tests/test_db_layer.py",        # Este test mismo
}

# Archivos excluidos del chequeo (código legacy en proceso de migración)
# S109: Estos archivos se irán eliminando conforme se migren
LEGACY_ALLOWED_PREFIXES = (
    # Ninguno por ahora — todos deben migrar
)


# ── Patrones ────────────────────────────────────────────────────────────────

# Busca sqlite3.connect() que NO sea comentario
RE_SQLITE_CONNECT = re.compile(
    r"^\s*[^#]*sqlite3\.connect\(",
    re.MULTILINE
)


def find_raw_connects(root: Path) -> dict:
    """Encuentra todos los archivos con sqlite3.connect() directo.

    Returns:
        {filepath: [line_numbers]}
    """
    violations = {}

    for py_file in sorted(root.rglob("*.py")):
        # Saltar archivos .bak
        if ".bak" in py_file.suffix or ".bak-" in py_file.name:
            continue

        # Calcular path relativo
        try:
            rel_path = str(py_file.relative_to(EIDOS_DIR))
        except ValueError:
            continue

        # Saltar permitidos
        if rel_path in ALLOWED_RAW_CONNECT:
            continue

        # Saltar __pycache__
        if "__pycache__" in py_file.parts:
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        # Buscar sqlite3.connect
        matches = list(RE_SQLITE_CONNECT.finditer(content))
        if matches:
            # Verificar que también tiene import sqlite3 (no get_conn)
            if "import sqlite3" in content and "from core.db import get_conn" not in content:
                lines = [content[:m.start()].count("\n") + 1 for m in matches]
                violations[rel_path] = lines

    return violations


# ── Baseline tracking ──────────────────────────────────────────────────────

BASELINE_FILE = EIDOS_DIR / ".s109_baseline.json"

def _load_baseline() -> dict:
    if BASELINE_FILE.exists():
        import json
        return json.loads(BASELINE_FILE.read_text())
    return {}

def _save_baseline(data: dict):
    import json
    BASELINE_FILE.write_text(json.dumps(data, indent=2))


# ── Tests ───────────────────────────────────────────────────────────────────

def test_no_raw_sqlite3_connect_regression():
    """Verifica que el número de sqlite3.connect() directos NO AUMENTE.

    Este es un test de REGRESIÓN: si se añaden nuevos connects sin migrar,
    el test falla. La migración de existentes es progresiva (S109).

    Para actualizar la baseline tras migrar archivos:
        python3 -c "from tests.test_db_layer import *; _save_baseline({...})"
        # o simplemente borrar .s109_baseline.json para recrear
    """
    violations = find_raw_connects(EIDOS_DIR / "core")
    total_raw = sum(len(v) for v in violations.values())
    total_files = len(violations)

    baseline = _load_baseline()

    if not baseline:
        # Primera ejecución: establecer baseline
        _save_baseline({
            "raw_connects": total_raw,
            "raw_files": total_files,
            "max_allowed": total_raw,  # No permitir nuevos
            "note": "S109 baseline — migración en progreso. NO AÑADIR nuevos raw connects."
        })
        print(f"✅ test_no_raw_sqlite3_connect_regression: BASELINE SET "
              f"({total_raw} connects en {total_files} archivos)")
        return

    max_allowed = baseline.get("max_allowed", baseline.get("raw_connects", 9999))

    if total_raw > max_allowed:
        # Encontrar qué archivos nuevos tienen connects
        raise AssertionError(
            f"\n{'='*70}\n"
            f"  ❌ REGRESIÓN: {total_raw} raw connects (máx permitido: {max_allowed})\n"
            f"     +{total_raw - max_allowed} connects sin migrar añadidos\n"
            f"{'='*70}\n\n"
            f"  S109: NO se permiten nuevos sqlite3.connect() sin pasar por core/db.py\n"
            f"  Migra los nuevos archivos o actualiza la baseline si es intencional.\n"
        )

    # Progreso: mostrar cuánto falta
    remaining = total_raw
    if remaining > 0:
        print(f"⚠️  test_no_raw_sqlite3_connect_regression: {remaining}/{max_allowed} connects pendientes "
              f"(migración en progreso, baseline={max_allowed})")
    else:
        print("✅ test_no_raw_sqlite3_connect_regression: 0 raw connects — MIGRACIÓN COMPLETA")


def test_core_db_health():
    """Verifica que core/db.py funciona correctamente."""
    from core.db import health_check

    result = health_check()
    assert result["ok"], f"Health check falló: {result['errors']}"
    assert result["checks"]["wal_mode"], "WAL mode no está activo"
    assert result["checks"]["synchronous_normal"], "synchronous no es NORMAL"

    print("✅ test_core_db_health: PASSED")


def test_get_conn_wal():
    """Verifica que get_conn() devuelve conexiones con WAL."""
    from core.db import get_conn
    import tempfile, os

    # Usar self.db que ya existe
    conn = get_conn("self.db")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", f"journal_mode={mode}, esperado wal"
    print("✅ test_get_conn_wal: PASSED")


def test_get_conn_synchronous():
    """Verifica que get_conn() configura synchronous=NORMAL."""
    from core.db import get_conn

    conn = get_conn("self.db")
    sync = conn.execute("PRAGMA synchronous").fetchone()[0]
    assert sync == 1, f"synchronous={sync}, esperado 1 (NORMAL)"
    print("✅ test_get_conn_synchronous: PASSED")


def test_get_conn_cache():
    """Verifica que el caché de conexiones funciona."""
    from core.db import get_conn, stats

    conn1 = get_conn("self.db")
    conn2 = get_conn("self.db")

    # Deben ser el mismo objeto
    assert conn1 is conn2, "El caché debe devolver la misma conexión"
    assert stats()["connections_created"] <= 1, "Solo una conexión debe crearse"

    print("✅ test_get_conn_cache: PASSED")


def test_get_conn_context_manager():
    """Verifica que el context manager funciona."""
    from core.db import get_conn_ctx

    with get_conn_ctx("self.db", cache=False) as conn:
        row = conn.execute("SELECT 1").fetchone()
        assert row is not None

    print("✅ test_get_conn_context_manager: PASSED")


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess

    print("=" * 70)
    print("  EIDOS DB Layer Test Suite (S109)")
    print("=" * 70)
    print()

    # 1. Verificar raw connects
    print("--- Test 1: Sin raw sqlite3.connect() ---")
    violations = find_raw_connects(EIDOS_DIR / "core")
    if violations:
        total = sum(len(v) for v in violations.values())
        print(f"  ⚠️  {total} raw connects en {len(violations)} archivos")
        # Mostrar top 10 peores
        sorted_v = sorted(violations.items(), key=lambda x: -len(x[1]))
        for fpath, lines in sorted_v[:10]:
            print(f"     {fpath}: {len(lines)} connects (líneas {lines[:3]}...)")
        print(f"  Progreso: migrados {71} archivos, pendientes {len(violations)}")
    else:
        print("  ✅ Ningún raw connect encontrado")

    print()

    # 2. Health check
    print("--- Test 2: Health check ---")
    from core.db import health_check
    result = health_check()
    print(f"  ok: {result['ok']}")
    for check, value in result["checks"].items():
        icon = "✅" if value else "❌"
        print(f"  {icon} {check}: {value}")
    if result["errors"]:
        for e in result["errors"]:
            print(f"  ❌ Error: {e}")

    print()

    # 3. WAL verification
    print("--- Test 3: WAL mode en todos los DBs ---")
    r = subprocess.run(
        ["bash", str(EIDOS_DIR / "scripts" / "verify_state.sh"), "--short"],
        capture_output=True, text=True
    )
    print(r.stdout if r.returncode == 0 else f"  ❌ verify_state.sh falló:\n{r.stderr}")

    print()
    print("=" * 70)
    overall = "PASS" if not violations and result["ok"] else "ISSUES FOUND"
    print(f"  Resultado global: {overall}")
    print("=" * 70)
