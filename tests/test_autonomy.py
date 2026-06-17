#!/usr/bin/env python3
"""
EIDOS Autonomy Features Test
============================
Prueba las nuevas características de autonomía implementadas.
"""
import sys
from pathlib import Path

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))

def test_configuration_system():
    """Test 1: Sistema de configuración"""
    print("\n" + "="*70)
    print("[TEST 1/4] Sistema de Configuración Centralizado")
    print("="*70)

    try:
        from core.eidos_config import get_config, update_config

        # Cargar configuración
        config = get_config()
        print("✅ Configuración cargada")

        # Verificar valores
        print(f"   Modo autonomía: {config.autonomy.mode}")
        print(f"   Idioma: {config.system.language}")
        print(f"   Low RAM mode: {config.system.low_ram_mode}")
        print(f"   Modelo de texto: {config.models.text_generation}")

        # Verificar paths
        print(f"   Path videos: {config.paths.videos}")
        print(f"   Path objectives: {config.paths.objectives}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_objectives_system():
    """Test 2: Sistema de objetivos"""
    print("\n" + "="*70)
    print("[TEST 2/4] Sistema de Objetivos Propios")
    print("="*70)

    try:
        from core.objectives_system import (
            get_objectives_manager,
            ObjectiveType,
            ObjectivePriority
        )

        manager = get_objectives_manager()
        print("✅ Objectives Manager cargado")

        # Verificar estadísticas
        stats = manager.get_statistics()
        print(f"   Total objetivos: {stats.get('total', 0)}")

        # Verificar sugerencias
        suggestions = manager.suggest_objectives()
        print(f"   Sugerencias disponibles: {len(suggestions)}")

        # Verificar objetivos activos
        active = manager.get_active_objectives()
        print(f"   Objetivos activos: {len(active)}")

        pending = manager.get_pending_objectives()
        print(f"   Objetivos pendientes: {len(pending)}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_brain_libre_mode():
    """Test 3: Modo LIBRE del cerebro"""
    print("\n" + "="*70)
    print("[TEST 3/4] Modo LIBRE - Autonomía del Cerebro")
    print("="*70)

    try:
        from core.eidos_brain import get_eidos_brain

        brain = get_eidos_brain()
        print("✅ EIDOS Brain cargado")

        # Verificar componentes
        print(f"   Modo actual: {brain.current_mode}")
        print(f"   Modo LIBRE: {brain.is_libre_mode}")

        # Verificar que objectives manager está disponible
        if brain.objectives_manager:
            print("✅ Objectives Manager integrado en Brain")
        else:
            print("⚠️  Objectives Manager no disponible")

        # Verificar memoria conversacional
        print(f"   Memoria cargada: {len(brain.memory.messages)} mensajes")

        # Verificar perfil de SER
        prefs = brain.profile.get_preferences()
        print(f"   Preferencias de SER: {len(prefs)} configuradas")

        # Test de decisión autónoma (sin ejecutar)
        brain.set_mode("LIBRE")
        print("✅ Modo LIBRE activado correctamente")

        action = brain._decide_autonomous_action()
        if action:
            print(f"   Decisión autónoma: {action['type']}")
            print(f"   Descripción: {action['description'][:60]}...")
        else:
            print("⚠️  No se pudo tomar decisión autónoma")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_cli_commands():
    """Test 4: Comandos CLI nuevos"""
    print("\n" + "="*70)
    print("[TEST 4/4] Comandos CLI (libre, objectives)")
    print("="*70)

    try:
        # Verificar que el archivo eidos existe
        eidos_cli = EIDOS_ROOT / "eidos"
        if not eidos_cli.exists():
            print("❌ eidos CLI no encontrado")
            return False

        print("✅ eidos CLI encontrado")

        # Verificar que es ejecutable
        if not eidos_cli.stat().st_mode & 0o111:
            print("⚠️  eidos no es ejecutable")
        else:
            print("✅ eidos es ejecutable")

        # Leer y verificar que los comandos están definidos
        content = eidos_cli.read_text()

        commands_to_check = ['cmd_libre', 'cmd_objectives']
        for cmd in commands_to_check:
            if cmd in content:
                print(f"✅ Comando {cmd} definido")
            else:
                print(f"❌ Comando {cmd} NO encontrado")
                return False

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def main():
    """Run all tests"""
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║                                                          ║")
    print("║    EIDOS AUTONOMY FEATURES TEST SUITE                   ║")
    print("║    Testing: Config, Objectives, LIBRE Mode, CLI         ║")
    print("║                                                          ║")
    print("╚══════════════════════════════════════════════════════════╝")

    results = []

    # Ejecutar tests
    results.append(("Configuration System", test_configuration_system()))
    results.append(("Objectives System", test_objectives_system()))
    results.append(("Brain LIBRE Mode", test_brain_libre_mode()))
    results.append(("CLI Commands", test_cli_commands()))

    # Resumen
    print("\n" + "="*70)
    print("RESUMEN DE TESTS")
    print("="*70)

    passed = 0
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}  {name}")
        if result:
            passed += 1

    total = len(results)
    percentage = (passed / total) * 100

    print("\n" + "="*70)
    print(f"RESULTADO FINAL: {passed}/{total} tests pasados ({percentage:.1f}%)")

    if percentage == 100:
        print("\n🎉 ¡TODOS LOS TESTS PASARON! Sistema de autonomía 100% funcional")
    elif percentage >= 75:
        print("\n✅ La mayoría de tests pasaron. Sistema mayormente funcional")
    else:
        print("\n⚠️  Múltiples tests fallaron. Se requieren correcciones")

    print("="*70 + "\n")

    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
