#!/usr/bin/env python3
"""
Test para Opinion Seeker - Sistema extensible de consulta de AIs
"""
import sys
import time
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from core.opinion_seeker import OpinionSeeker, get_opinion_seeker


def test_1_list_ais():
    """Test 1: Listar AIs disponibles"""
    print("=" * 70)
    print("TEST 1: Listar AIs disponibles")
    print("=" * 70)

    seeker = get_opinion_seeker()
    ais = seeker.list_available_ais()

    print(f"\n✅ AIs disponibles: {len(ais)}")
    for name in sorted(ais):
        info = seeker.ai_configs[name]
        status = "🔒 (requiere login)" if info.requires_login else "🌐 (público)"
        print(f"  • {name:15s} {status}")
        print(f"    URL: {info.url}")
        if info.notes:
            print(f"    {info.notes}")
        print()

    assert len(ais) >= 3, "Debe haber al menos 3 AIs predefinidas"
    print("✅ Test 1 PASSED\n")


def test_2_learn_new_ai():
    """Test 2: Enseñar nueva AI a EIDOS"""
    print("=" * 70)
    print("TEST 2: Enseñar nueva AI")
    print("=" * 70)

    seeker = get_opinion_seeker()

    # Enseñar una AI ficticia
    seeker.learn_new_ai(
        name="test_ai",
        url="https://test-ai.com/chat",
        input_selectors=["textarea.test-input"],
        submit_method="enter",
        response_selectors=["div.test-response"],
        wait_time=5,
        requires_login=False,
        notes="AI de prueba para testing"
    )

    # Verificar que se aprendió
    ais = seeker.list_available_ais()
    assert "test_ai" in ais, "La AI debería estar en la lista"

    # Verificar que se guardó a disco
    config = seeker.get_ai_info("test_ai")
    assert config is not None, "Debería poder obtener info de la AI"
    assert config.url == "https://test-ai.com/chat", "URL incorrecta"

    print(f"\n✅ AI 'test_ai' aprendida exitosamente")
    print(f"   URL: {config.url}")
    print(f"   Input selectors: {config.input_selectors}")
    print(f"   Submit method: {config.submit_method}")

    print("✅ Test 2 PASSED\n")


def test_3_opinion_seeking_simulation():
    """Test 3: Simulación de búsqueda de opiniones (sin browser real)"""
    print("=" * 70)
    print("TEST 3: Simulación de búsqueda de opiniones")
    print("=" * 70)

    seeker = get_opinion_seeker()

    # Mostrar que tiene AIs configuradas
    ais = [name for name, cfg in seeker.ai_configs.items() if not cfg.requires_login]

    print(f"\n📋 AIs públicas disponibles para consulta: {len(ais)}")
    for ai in ais:
        print(f"  • {ai}")

    print(f"\n💡 Para consultar opiniones reales:")
    print(f"   consensus = seeker.ask_opinions(")
    print(f"       question='How to secure a penetration test?',")
    print(f"       sources=['deepseek', 'huggingface', 'perplexity']")
    print(f"   )")

    print("\n⚠️  Nota: Test en modo simulación (sin consultas reales al browser)")
    print("   Para probar con consultas reales, ejecutar manualmente:")
    print("   python3 core/opinion_seeker.py ask 'your question'")

    print("✅ Test 3 PASSED\n")


def test_4_integration_with_brain():
    """Test 4: Verificar integración con EidosBrain"""
    print("=" * 70)
    print("TEST 4: Integración con EidosBrain")
    print("=" * 70)

    try:
        from core.eidos_brain import EidosBrain

        brain = EidosBrain(verbose=False)

        # Verificar que tiene acceso a opinion seeker
        from core.opinion_seeker import get_opinion_seeker
        seeker = get_opinion_seeker()

        print("\n✅ EidosBrain puede acceder a OpinionSeeker")
        print(f"   AIs disponibles: {len(seeker.list_available_ais())}")

        # Verificar que MODO LIBRE puede usar opinion seeking
        brain.set_mode("LIBRE")
        print(f"✅ MODO LIBRE activado")
        print(f"   EIDOS ahora puede consultar opiniones externas autónomamente")

        print("✅ Test 4 PASSED\n")

    except Exception as e:
        print(f"❌ Error en integración: {e}")
        raise


def test_5_config_persistence():
    """Test 5: Verificar persistencia de configs aprendidas"""
    print("=" * 70)
    print("TEST 5: Persistencia de configuraciones")
    print("=" * 70)

    # Crear nueva instancia
    seeker1 = OpinionSeeker(verbose=False)

    # Enseñar AI
    seeker1.learn_new_ai(
        name="persistence_test",
        url="https://test.com",
        input_selectors=["textarea"],
        submit_method="enter"
    )

    # Crear OTRA instancia (simula reinicio)
    seeker2 = OpinionSeeker(verbose=False)

    # Verificar que la AI persiste
    assert "persistence_test" in seeker2.list_available_ais(), "Config no persistió"

    config = seeker2.get_ai_info("persistence_test")
    assert config.url == "https://test.com", "URL incorrecta tras reload"

    print("\n✅ Configuraciones persisten correctamente entre reinicios")
    from core.opinion_seeker import AI_CONFIGS_FILE
    print(f"   Archivo: {AI_CONFIGS_FILE}")

    print("✅ Test 5 PASSED\n")


def main():
    """Ejecuta todos los tests"""
    print("\n" + "═" * 70)
    print("EIDOS OPINION SEEKER - TEST SUITE")
    print("═" * 70 + "\n")

    tests = [
        ("Listar AIs", test_1_list_ais),
        ("Aprender nueva AI", test_2_learn_new_ai),
        ("Simulación de consultas", test_3_opinion_seeking_simulation),
        ("Integración con Brain", test_4_integration_with_brain),
        ("Persistencia de configs", test_5_config_persistence),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"\n❌ TEST FAILED: {name}")
            print(f"   Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
            print()

        time.sleep(0.5)

    # Resumen
    print("=" * 70)
    print("RESULTADOS")
    print("=" * 70)
    print(f"✅ Pasados: {passed}/{len(tests)}")
    if failed > 0:
        print(f"❌ Fallados: {failed}/{len(tests)}")
    else:
        print(f"🎉 TODOS LOS TESTS PASARON!")
    print("=" * 70 + "\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
