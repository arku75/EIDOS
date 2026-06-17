#!/usr/bin/env python3
"""
Test manual del watchdog para SER.
Requiere: EIDOS corriendo en otra terminal
Ejecutar: python3 test_watchdog.py
"""
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, '/home/ser/EIDOS')

HEALTH_FILE = Path.home() / ".eidos" / "health.json"
HEARTBEAT_FILE = Path.home() / ".eidos" / "heartbeat"
MAX_STALE = 120

print("=" * 50)
print("TEST: Watchdog")
print("=" * 50)

# Test 1: Archivos existen
print("\n[1] Verificando archivos...")
print(f"    health.json: {'✓' if HEALTH_FILE.exists() else '✗'}")
print(f"    heartbeat: {'✓' if HEARTBEAT_FILE.exists() else '✗'}")

# Test 2: Heartbeat
print("\n[2] Verificando heartbeat...")
if HEARTBEAT_FILE.exists():
    try:
        lines = HEARTBEAT_FILE.read_text().strip().split("\n")
        last_beat = float(lines[0])
        age = time.time() - last_beat
        status = "FRESH" if age < MAX_STALE else "STALE (requiere reinicio)"
        print(f"    Último beat: {age:.0f}s ago")
        print(f"    Status: {status}")
    except Exception as e:
        print(f"    ✗ Error leyendo heartbeat: {e}")
else:
    print("    ✗ No existe heartbeat")

# Test 3: Health
print("\n[3] Verificando health.json...")
if HEALTH_FILE.exists():
    try:
        health = json.loads(HEALTH_FILE.read_text())
        print(f"    Status: {health.get('status', 'unknown')}")
        print(f"    Ciclos: {health.get('cycle_count', 0)}")
        print(f"    Errores: {health.get('errors_total', 0)}")
    except Exception as e:
        print(f"    ✗ Error leyendo health: {e}")
else:
    print("    ✗ No existe health.json")

# Test 4: Watchdog check
print("\n[4] Ejecutando check_health...")
try:
    from scripts.eidos_watchdog import check_health
    status, detail = check_health()
    print(f"    Resultado: {status}")
    print(f"    Detalle: {detail}")
    if status == "healthy":
        print("    ✓ EIDOS está healthy")
    elif status == "dead":
        print("    ⚠️  EIDOS está muerto - watchdog reiniciará")
    else:
        print(f"    ? Status desconocido: {status}")
except Exception as e:
    print(f"    ✗ Error en check_health: {e}")

print("\n" + "=" * 50)
print("Para test completo del reinicio:")
print("1. Ejecutar: python3 scripts/eidos_watchdog.py")
print("2. En otra terminal, ejecutar EIDOS")
print("3. Matar EIDOS: kill -9 $(pgrep -f eidos_core)")
print("4. Verificar que watchdog reinicia EIDOS en < 2 min")
print("=" * 50)
