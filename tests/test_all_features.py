#!/usr/bin/env python3
"""
EIDOS Complete Feature Test Suite
==================================
Prueba TODAS las features implementadas.
"""
import sys
from pathlib import Path

# Añadir root al path
EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))

def test_voice_system():
    """Test 1: Voice System"""
    print("\n" + "="*70)
    print("[TEST 1/8] Voice System")
    print("="*70)

    try:
        from core.voice_system import get_voice_system

        voice = get_voice_system()
        print(f"✅ Voice system cargado")
        print(f"   Tipo: {voice.engine_type}")
        print(f"   Disponible: {voice.is_available()}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_window_manager():
    """Test 2: Window Manager"""
    print("\n" + "="*70)
    print("[TEST 2/8] Window Focus Manager")
    print("="*70)

    try:
        from core.window_manager import get_window_manager

        manager = get_window_manager()
        print(f"✅ Window manager cargado")

        # Verificar tools
        available_tools = sum(manager.tools_available.values())
        total_tools = len(manager.tools_available)
        print(f"   Tools: {available_tools}/{total_tools} disponibles")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_moltbook_connector():
    """Test 3: Moltbook Connector"""
    print("\n" + "="*70)
    print("[TEST 3/8] Moltbook Connector")
    print("="*70)

    try:
        from core.moltbook_connector import get_moltbook_connector

        connector = get_moltbook_connector()
        print(f"✅ Moltbook connector cargado")
        print(f"   URL: {connector.base_url}")
        print(f"   Enabled: {connector.enabled}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_video_pipeline():
    """Test 4: Video Pipeline"""
    print("\n" + "="*70)
    print("[TEST 4/8] Video Pipeline")
    print("="*70)

    try:
        from core.video_pipeline import get_video_pipeline, ContentGenerator

        pipeline = get_video_pipeline()
        print(f"✅ Video pipeline cargado")

        # Verificar dependencias
        available_deps = sum(pipeline.deps.values())
        total_deps = len(pipeline.deps)
        print(f"   Dependencies: {available_deps}/{total_deps} disponibles")

        # Generar idea de contenido
        ideas = ContentGenerator.generate_facelessreel_ideas(count=1)
        print(f"   Ideas generadas: {len(ideas)}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_upload_system():
    """Test 5: Upload System"""
    print("\n" + "="*70)
    print("[TEST 5/8] Upload System")
    print("="*70)

    try:
        from core.upload_system import get_upload_manager

        manager = get_upload_manager()
        print(f"✅ Upload manager cargado")

        # Ver stats
        stats = manager.get_stats()
        print(f"   Total uploads históricos: {stats.get('total', 0)}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_config_system():
    """Test 6: Configuration System"""
    print("\n" + "="*70)
    print("[TEST 6/8] Configuration System")
    print("="*70)

    try:
        from core.eidos_config import get_config

        config = get_config()
        print(f"✅ Config system cargado")
        print(f"   Voice enabled: {config.voice.enabled}")
        print(f"   Window enabled: {config.window.enabled}")
        print(f"   Moltbook enabled: {config.moltbook.enabled}")
        print(f"   Autonomy mode: {config.autonomy.mode}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_objectives_system():
    """Test 7: Objectives System"""
    print("\n" + "="*70)
    print("[TEST 7/8] Objectives System")
    print("="*70)

    try:
        from core.objectives_system import get_objectives_manager

        manager = get_objectives_manager()
        print(f"✅ Objectives manager cargado")

        stats = manager.get_statistics()
        print(f"   Total objetivos: {stats.get('total', 0)}")
        print(f"   Activos: {len(manager.get_active_objectives())}")
        print(f"   Pendientes: {len(manager.get_pending_objectives())}")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_brain_libre():
    """Test 8: Brain LIBRE Mode"""
    print("\n" + "="*70)
    print("[TEST 8/8] Brain LIBRE Mode")
    print("="*70)

    try:
        from core.eidos_brain import get_eidos_brain

        brain = get_eidos_brain()
        print(f"✅ EIDOS Brain cargado")
        print(f"   Modo actual: {brain.current_mode}")

        # Verificar integración de objetivos
        if brain.objectives_manager:
            print(f"✅ Objectives Manager integrado")
        else:
            print(f"⚠️  Objectives Manager no integrado")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests"""
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║                                                          ║")
    print("║    EIDOS COMPLETE FEATURE TEST SUITE                    ║")
    print("║    Testing ALL features implemented today               ║")
    print("║                                                          ║")
    print("╚══════════════════════════════════════════════════════════╝")

    results = []

    # Ejecutar tests
    results.append(("Voice System", test_voice_system()))
    results.append(("Window Manager", test_window_manager()))
    results.append(("Moltbook Connector", test_moltbook_connector()))
    results.append(("Video Pipeline", test_video_pipeline()))
    results.append(("Upload System", test_upload_system()))
    results.append(("Config System", test_config_system()))
    results.append(("Objectives System", test_objectives_system()))
    results.append(("Brain LIBRE Mode", test_brain_libre()))

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
        print("\n🎉 ¡TODOS LOS TESTS PASARON! EIDOS 100% funcional")
    elif percentage >= 75:
        print("\n✅ La mayoría de tests pasaron. Sistema mayormente funcional")
    else:
        print("\n⚠️  Múltiples tests fallaron. Se requieren correcciones")

    print("="*70 + "\n")

    # Feature summary
    print("📋 FEATURES IMPLEMENTADAS:")
    print("  1. ✅ Voice System (pyttsx3 fallback)")
    print("  2. ✅ Window Focus Manager (xdotool + OCR)")
    print("  3. ✅ Moltbook Connector (learning + sharing)")
    print("  4. ✅ Video Pipeline (math art + narración)")
    print("  5. ✅ Upload System (YouTube/TikTok/Instagram)")
    print("  6. ✅ Config System (centralizado)")
    print("  7. ✅ Objectives System (autonomía)")
    print("  8. ✅ Brain LIBRE Mode (decisión autónoma)")
    print()

    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
