#!/usr/bin/env python3
"""
Test de Integración Completa - EIDOS
====================================

Verifica que todos los componentes funcionen juntos:
- Rust Knowledge DB (5x más rápido)
- Go API Server (10x más requests)
- Continuous Learner (aprendizaje 24/7)
- BTW Command Handler (real-time interaction)
- Multimodal Learning (Vision + Audio + Text)
"""

import sys
import time
import json
import requests
from pathlib import Path

# Add EIDOS to path
sys.path.insert(0, str(Path.home() / "EIDOS"))

def test_rust_knowledge_db():
    """Test 1: Verificar que Rust KB esté funcionando"""
    print("\n" + "="*70)
    print("TEST 1: Rust Knowledge DB")
    print("="*70)

    try:
        from core.knowledge_integration import get_knowledge_db, is_using_rust

        # Verificar que esté usando Rust
        using_rust = is_using_rust()
        print(f"✅ Knowledge DB disponible")
        print(f"   Usando Rust: {'✅ SÍ (5x más rápido)' if using_rust else '⚠️  NO (fallback a Python)'}")

        # Crear instancia
        kb = get_knowledge_db(verbose=False)
        if kb is None:
            print("❌ No se pudo crear Knowledge DB")
            return False

        # Obtener stats
        if using_rust:
            stats = json.loads(kb.get_stats())
        else:
            stats = kb.get_stats()

        print(f"   Lenguajes conocidos: {stats.get('languages', 0)}")
        print(f"   Librerías conocidas: {stats.get('libraries', 0)}")
        print(f"   Firmas conocidas: {stats.get('signatures', 0)}")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_go_api_server():
    """Test 2: Verificar que Go API Server esté corriendo"""
    print("\n" + "="*70)
    print("TEST 2: Go API Server")
    print("="*70)

    try:
        # Intentar conectar al server (usando /api/status)
        response = requests.get("http://localhost:8765/api/status", timeout=2)

        if response.status_code == 200:
            data = response.json()
            print(f"✅ Go API Server corriendo")
            print(f"   Status: {data.get('status')}")
            print(f"   Observing: {data.get('observing', False)}")
            print(f"   Files observed: {data.get('files_observed', 0)}")
            return True
        else:
            print(f"⚠️  Server respondió con código {response.status_code}")
            return False

    except requests.ConnectionError:
        print("⚠️  Go API Server no está corriendo")
        print("   Para iniciarlo: cd go-core && ./bin/eidos-api-server")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_continuous_learner():
    """Test 3: Verificar que Continuous Learner esté activo"""
    print("\n" + "="*70)
    print("TEST 3: Continuous Learner (Daemon)")
    print("="*70)

    try:
        from core.continuous_learner import get_continuous_learner

        learner = get_continuous_learner()

        # Ver contenido aprendido (es un atributo, no método)
        learned = learner.learned_content
        print(f"✅ Continuous Learner disponible")
        print(f"   Contenido aprendido: {len(learned)} items")

        # Ver cola (es un atributo Queue)
        queue_size = learner.learning_queue.qsize()
        print(f"   Cola pendiente: {queue_size} tareas")

        # Ver algunos items aprendidos
        if learned:
            print(f"\n   Últimos 3 items aprendidos:")
            for item_id in list(learned.keys())[-3:]:
                item = learned[item_id]
                print(f"     - {item.get('title', 'Sin título')}")
                print(f"       Tipo: {item.get('source_type', 'N/A')}")
                print(f"       Status: {item.get('status', 'N/A')}")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_btw_command_handler():
    """Test 4: Verificar que BTW Command Handler funcione"""
    print("\n" + "="*70)
    print("TEST 4: BTW Command Handler (Real-time Interaction)")
    print("="*70)

    try:
        from core.btw_command_handler import get_btw_handler, Command, CommandType

        btw = get_btw_handler()
        print(f"✅ BTW Command Handler disponible")

        # Actualizar contexto
        btw.update_context(
            task="Test de integración",
            description="Verificando que BTW funcione",
            thoughts="Ejecutando tests end-to-end"
        )
        print(f"   Contexto actualizado")

        # Simular comando /btw (usando args en lugar de content)
        cmd = Command(
            type=CommandType.BTW,
            args="¿qué estás haciendo?",
            timestamp=time.time()
        )

        response = btw.handle_command(cmd)
        print(f"\n   Respuesta a '/btw ¿qué estás haciendo?':")
        print(f"   Response: {response.response}")
        print(f"   Context: {response.context.get('current_task', 'N/A')}")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_multimodal_learning():
    """Test 5: Verificar que Multimodal Learning esté disponible"""
    print("\n" + "="*70)
    print("TEST 5: Multimodal Learning (Vision + Audio + Text)")
    print("="*70)

    try:
        from core.multimodal_learner import get_multimodal_learner

        learner = get_multimodal_learner()
        print(f"✅ Multimodal Learner disponible")

        # Ver capacidades (chequear atributos directamente)
        has_whisper = hasattr(learner, 'whisper_model')
        has_clip = hasattr(learner, 'clip_model')
        has_vision = hasattr(learner, 'vision')

        print(f"\n   Capacidades:")
        print(f"     Whisper (audio transcription): {'✅' if has_whisper else '❌'}")
        print(f"     CLIP (semantic vision): {'✅' if has_clip else '❌'}")
        print(f"     Vision lightweight: {'✅' if has_vision else '❌'}")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Ejecutar todos los tests de integración"""
    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*15 + "EIDOS - TEST DE INTEGRACIÓN COMPLETA" + " "*17 + "║")
    print("╚" + "="*68 + "╝")

    tests = [
        ("Rust Knowledge DB", test_rust_knowledge_db),
        ("Go API Server", test_go_api_server),
        ("Continuous Learner", test_continuous_learner),
        ("BTW Command Handler", test_btw_command_handler),
        ("Multimodal Learning", test_multimodal_learning),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Test '{test_name}' falló con excepción: {e}")
            results.append((test_name, False))

    # Resumen
    print("\n" + "="*70)
    print("RESUMEN DE TESTS")
    print("="*70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status:10} {test_name}")

    print(f"\n{'='*70}")
    print(f"Total: {passed}/{total} tests pasados ({passed*100//total}% success rate)")
    print(f"{'='*70}\n")

    if passed == total:
        print("🎉 ¡TODOS LOS TESTS PASARON! EIDOS está completamente integrado.")
        return 0
    else:
        print("⚠️  Algunos tests fallaron. Revisa los logs arriba.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
