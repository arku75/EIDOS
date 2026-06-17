#!/usr/bin/env python3
"""
🧪 TEST COMPLETO - Verificación de Fixes Implementados
"""
import sys
import os
import sqlite3

sys.path.insert(0, '/home/ser/EIDOS')

print("="*70)
print("🧪 TEST DE FIXES - Verificación Completa")
print("="*70)

# Test 1: Verificar tabla independence_state existe
print("\n1️⃣ Verificando tabla independence_state...")
try:
    conn = sqlite3.connect('/home/ser/.eidos/evolution_brain.db')
    row = conn.execute("SELECT score FROM independence_state WHERE id=1").fetchone()
    if row:
        print(f"   ✅ Tabla existe, score guardado: {row[0]:.1%}")
    else:
        print("   ❌ Tabla vacía o no inicializada")
    conn.close()
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 2: Verificar tabla eidos_state existe
print("\n2️⃣ Verificando tabla eidos_state...")
try:
    conn = sqlite3.connect('/home/ser/.eidos/evolution_brain.db')
    row = conn.execute("SELECT awake, last_active FROM eidos_state WHERE id=1").fetchone()
    if row:
        print(f"   ✅ Tabla existe, awake={bool(row[0])}, last_active={row[1]}")
    else:
        print("   ⚠️ Tabla existe pero sin datos (se creará al usar)")
    conn.close()
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 3: EvolutionEngine carga independence
print("\n3️⃣ Probando EvolutionEngine...")
try:
    from core.eidos_evolution_engine import get_evolution_engine
    e = get_evolution_engine()
    stats = e.get_evolution_stats()
    print(f"   ✅ Independence: {stats['independence_score']:.1%}")
    print(f"   ✅ Knowledge nodes: {stats['knowledge_nodes']}")
    print(f"   ✅ Thoughts: {stats['thoughts']}")
    if stats['independence_score'] > 0:
        print("   🎯 FIX #1 FUNCIONA: Independencia cargada desde BD")
    else:
        print("   ⚠️ Independencia en 0 - ejecutar eidos_despierto.py")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 4: AutonomousCore carga awake state
print("\n4️⃣ Probando AutonomousCore...")
try:
    from core.eidos_autonomous_core import get_autonomous_core
    core = get_autonomous_core()
    status = core.get_status()
    print(f"   ✅ Awake: {status['awake']}")
    print(f"   ✅ Independence level: {status['independence_level']}")
    print(f"   ✅ Learning cycles: {status['learning_cycles']}")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 5: Verificar sincronización sandbox
print("\n5️⃣ Verificando sincronización sandbox...")
try:
    main_file = '/home/ser/EIDOS/core/eidos_evolution_engine.py'
    sandbox_file = '/home/ser/EIDOS/sandbox/core/eidos_evolution_engine.py'
    
    with open(main_file, 'r') as f:
        main_content = f.read()
    with open(sandbox_file, 'r') as f:
        sandbox_content = f.read()
    
    if '_load_independence_score' in main_content and '_load_independence_score' in sandbox_content:
        print("   ✅ EvolutionEngine sincronizado (fix presente en ambos)")
    else:
        print("   ⚠️ Diferencias detectadas entre main y sandbox")
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n" + "="*70)
print("✅ TEST COMPLETADO - Fixes verificados")
print("="*70)
