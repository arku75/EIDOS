#!/usr/bin/env python3
"""
Test de Whisper + Vision Lightweight
=====================================

Tests de las nuevas capacidades de EIDOS:
1. Whisper: Transcripción de audio
2. Vision: Observación eficiente de pantalla/video
"""

import sys
from pathlib import Path

# Add EIDOS to path
sys.path.insert(0, str(Path(__file__).parent))

from core.continuous_learner import get_continuous_learner, Priority
from core.vision_lightweight import get_lightweight_vision
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_whisper():
    """Test Whisper transcription"""
    print("\n" + "="*60)
    print("🎤 TEST 1: WHISPER TRANSCRIPTION")
    print("="*60 + "\n")

    learner = get_continuous_learner()

    # Agregar un video corto para probar
    print("Agregando video de prueba a la cola de aprendizaje...")
    learner.add_youtube_video(
        "https://www.youtube.com/watch?v=5C_HPTJg5ek",
        "Rust Crash Course - Test Whisper",
        priority=Priority.HIGH
    )

    print(f"\n✅ Video agregado a la cola")
    print(f"📊 Estado: {learner.get_stats()}")
    print("\nEl daemon procesará el video en background.")
    print("Ver progreso: sudo journalctl -u eidos-learning -f")


def test_vision_screen():
    """Test vision watching screen"""
    print("\n" + "="*60)
    print("👁️  TEST 2: VISION - OBSERVAR PANTALLA")
    print("="*60 + "\n")

    vision = get_lightweight_vision()

    print("EIDOS observará tu pantalla por 30 segundos...")
    print("Abre un editor de código o terminal para que detecte código.")
    print("")

    input("Presiona ENTER cuando estés listo...")

    # Callback para mostrar resultados
    def on_frame(analysis):
        if analysis.has_changed:
            print(f"\n⚡ Cambio detectado ({analysis.timestamp:.1f}s)")
            if analysis.text_detected:
                print(f"   📝 Texto: {len(analysis.text_detected)} chars")
            if analysis.code_detected:
                print(f"   💻 Código detectado: {len(analysis.code_detected)} snippets")
                for i, code in enumerate(analysis.code_detected[:2], 1):
                    print(f"      Snippet {i}: {code[:60]}...")

    # Observar por 30 segundos
    results = vision.watch_screen(duration=30.0, callback=on_frame)

    # Resumen
    print(f"\n📊 Resumen:")
    print(f"   Total frames: {len(results)}")
    print(f"   Con cambios: {sum(1 for r in results if r.has_changed)}")
    print(f"   Con código: {sum(1 for r in results if r.code_detected)}")

    # Mostrar código detectado
    all_code = []
    for r in results:
        all_code.extend(r.code_detected)

    if all_code:
        print(f"\n💻 Ejemplos de código detectado:")
        for i, code in enumerate(all_code[:3], 1):
            print(f"\n   --- Snippet {i} ---")
            print(f"   {code[:200]}")


def test_vision_stats():
    """Show vision optimization stats"""
    print("\n" + "="*60)
    print("📊 TEST 3: ESTADÍSTICAS DE OPTIMIZACIÓN")
    print("="*60 + "\n")

    vision = get_lightweight_vision()

    print("Configuración de sampling adaptativo:")
    print(f"   Min interval: {vision.min_sample_interval}s (alta actividad)")
    print(f"   Max interval: {vision.max_sample_interval}s (sin actividad)")
    print(f"   Current: {vision.current_interval}s")
    print("")
    print("Esto significa que EIDOS:")
    print("   - Muestrea hasta 2 FPS cuando detecta cambios")
    print("   - Baja a 0.2 FPS cuando no hay actividad")
    print("   - Ahorra hasta 90% de CPU vs. procesar todos los frames")


def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("🎓 EIDOS - Test de Whisper + Vision Lightweight")
    print("="*70)

    print("\nNuevas capacidades:")
    print("  1. 🎤 Whisper: Transcribir videos de YouTube sin subtítulos")
    print("  2. 👁️  Vision: Ver pantalla/videos con consumo mínimo de CPU")
    print("")

    print("Selecciona test:")
    print("  1. Test Whisper (agregar video a cola)")
    print("  2. Test Vision (observar pantalla 30s)")
    print("  3. Test Vision stats (mostrar optimizaciones)")
    print("  4. Todos")
    print("  0. Salir")

    choice = input("\nOpción: ").strip()

    if choice == "1":
        test_whisper()
    elif choice == "2":
        test_vision_screen()
    elif choice == "3":
        test_vision_stats()
    elif choice == "4":
        test_whisper()
        test_vision_screen()
        test_vision_stats()
    elif choice == "0":
        print("👋 Adiós!")
        return
    else:
        print("❌ Opción inválida")

    print("\n✅ Tests completados!\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Tests interrumpidos por usuario")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
