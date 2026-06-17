#!/usr/bin/env python3
"""
Test manual de observe_system para SER.
Ejecutar: python3 test_observe_system.py
"""
import sys
import sqlite3
sys.path.insert(0, '/home/ser/EIDOS')

from core.brain_memory import get_brain_memory
from core.eidos_core import EIDOSCore

print("=" * 50)
print("TEST: observe_system")
print("=" * 50)

# Contar antes
conn = sqlite3.connect('/home/ser/.eidos/memory_vec.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM memories')
before = c.fetchone()[0]
print(f"[1] Entradas en DB antes: {before}")

# Ejecutar observe_system
print("[2] Ejecutando _observe_system()...")
e = EIDOSCore()
e._observe_system()

# Contar después
c.execute('SELECT COUNT(*) FROM memories')
after = c.fetchone()[0]
print(f"[3] Entradas en DB después: {after}")

# Resultado
new = after - before
print(f"[4] Nuevas entradas: {new}")

if new >= 6:
    print("\n[✓] ÉXITO: observe_system funciona correctamente")
    print("    Se guardaron 6+ entradas de información del sistema")
else:
    print("\n[✗] FALLA: No se guardaron todas las entradas esperadas")
    print(f"    Esperadas: 6, Guardadas: {new}")

# Mostrar últimas entradas
print("\n[5] Últimas 3 entradas system:")
c.execute("SELECT substr(content,1,50) FROM memories ORDER BY id DESC LIMIT 3")
for i, row in enumerate(c.fetchall(), 1):
    print(f"    {i}. {row[0]}...")

conn.close()
print("\n" + "=" * 50)
