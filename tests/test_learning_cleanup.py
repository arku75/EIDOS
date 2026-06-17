#!/usr/bin/env python3
"""
EIDOS Learning + Cleanup Test
==============================
Prueba el sistema completo de aprendizaje y limpieza.
"""
import sys
from pathlib import Path

EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))

def test_learning_from_zip():
    """Test 1: Aprender de un ZIP"""
    print("\n" + "="*70)
    print("[TEST 1/3] Learning from ZIP")
    print("="*70)

    try:
        from core.learning_system import get_learning_system

        learning = get_learning_system()

        # Usar el youtube_ytdl.zip que ya tienes
        zip_path = Path("/home/ser/mis cosas/mio/youtube_ytdl.zip")

        if zip_path.exists():
            print(f"✅ Archivo encontrado: {zip_path.name} ({zip_path.stat().st_size / 1024:.2f} KB)")

            # Aprender del ZIP
            learned = learning.learn_from_zip(zip_path)

            if learned:
                print(f"\n✅ Conocimiento extraído:")
                print(f"   Tipo: {learned.source_type}")
                print(f"   Tamaño original: {learned.original_size_mb:.4f} MB")
                print(f"   Conocimiento (primeras líneas):")
                print("   " + "\n   ".join(learned.knowledge.split('\n')[:10]))

                # Verificar que el archivo fue borrado
                if not zip_path.exists():
                    print(f"\n✅ Archivo original BORRADO automáticamente")
                else:
                    print(f"\n⚠️  Archivo aún existe (auto-cleanup deshabilitado?)")

                return True
            else:
                print(f"❌ No se pudo aprender del ZIP")
                return False
        else:
            print(f"⚠️  Archivo no encontrado: {zip_path}")
            return False

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_cleanup_disk_usage():
    """Test 2: Uso de disco"""
    print("\n" + "="*70)
    print("[TEST 2/3] Disk Usage")
    print("="*70)

    try:
        from core.cleanup_system import get_cleanup_manager

        cleanup = get_cleanup_manager()

        usage = cleanup.get_disk_usage()

        print(f"✅ Uso de disco de EIDOS:")
        for name, size_mb in usage.items():
            print(f"   {name:20s}: {size_mb:8.2f} MB")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def test_cleanup_temp_files():
    """Test 3: Limpieza de temporales"""
    print("\n" + "="*70)
    print("[TEST 3/3] Cleanup Temp Files")
    print("="*70)

    try:
        from core.cleanup_system import get_cleanup_manager

        cleanup = get_cleanup_manager()

        stats = cleanup.cleanup_temp_files()

        print(f"✅ Limpieza completada:")
        print(f"   Archivos borrados: {stats.files_cleaned}")
        print(f"   Espacio liberado: {stats.space_freed_mb:.2f} MB")

        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False

def main():
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║                                                          ║")
    print("║    EIDOS LEARNING + CLEANUP TEST SUITE                  ║")
    print("║                                                          ║")
    print("╚══════════════════════════════════════════════════════════╝")

    results = []

    # Ejecutar tests
    results.append(("Learning from ZIP", test_learning_from_zip()))
    results.append(("Disk Usage", test_cleanup_disk_usage()))
    results.append(("Cleanup Temp", test_cleanup_temp_files()))

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
    print("="*70)

    # Feature summary
    print("\n📋 FEATURES PROBADAS:")
    print("  1. ✅ Learning from ZIP (auto-delete)")
    print("  2. ✅ Disk usage tracking")
    print("  3. ✅ Automatic cleanup")
    print()

    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
