#!/usr/bin/env python3
"""
Test de Protección de Archivos del Usuario
===========================================
Verifica que EIDOS NUNCA borre archivos del usuario.
"""

import sys
import tempfile
from pathlib import Path

# Add EIDOS root to path
EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.cleanup_system import CleanupManager


def test_user_files_protected():
    """
    Test: Archivos del usuario están PROTEGIDOS
    """
    print("\n" + "="*60)
    print("TEST: Protección de Archivos del Usuario")
    print("="*60)

    cleanup = CleanupManager()

    # Casos de prueba
    test_cases = [
        # (ruta, debería_ser_seguro_borrar, descripción)
        (Path("/home/ser/mis cosas/CURSOS/test.zip"), False, "Archivo en CURSOS del usuario"),
        (Path("/home/ser/EIDOS/test.py"), False, "Código fuente de EIDOS"),
        (Path("/home/ser/Documents/test.pdf"), False, "Documento del usuario"),
        (Path("/home/ser/.eidos/downloads/test.zip"), True, "Descarga de EIDOS"),
        (Path("/home/ser/.eidos/frames/test.png"), True, "Frame temporal de EIDOS"),
        (Path("/home/ser/.eidos/videos/test.mp4"), True, "Video generado por EIDOS"),
        (Path("/tmp/test.txt"), False, "Archivo en /tmp (fuera de EIDOS)"),
    ]

    passed = 0
    failed = 0

    for file_path, should_be_safe, description in test_cases:
        is_safe = cleanup._is_safe_to_delete(file_path)

        if is_safe == should_be_safe:
            print(f"✅ PASS: {description}")
            print(f"   {file_path}")
            print(f"   Safe to delete: {is_safe} (esperado: {should_be_safe})")
            passed += 1
        else:
            print(f"❌ FAIL: {description}")
            print(f"   {file_path}")
            print(f"   Safe to delete: {is_safe} (esperado: {should_be_safe})")
            failed += 1

        print()

    # Resumen
    print("="*60)
    print(f"RESULTADO: {passed}/{passed+failed} tests pasados")
    print("="*60)

    if failed == 0:
        print("\n🎉 ¡Todos los tests pasaron!")
        print("✅ Los archivos del usuario están PROTEGIDOS")
        return True
    else:
        print(f"\n⚠️  {failed} tests fallaron")
        return False


def test_protection_summary():
    """
    Muestra resumen de protecciones
    """
    print("\n" + "="*60)
    print("RESUMEN DE PROTECCIONES")
    print("="*60)

    cleanup = CleanupManager()

    print("\n🛡️  Directorios seguros para borrar (solo estos):")
    for safe_dir in cleanup.safe_delete_dirs:
        print(f"   ✅ {safe_dir}")

    print("\n🚫 Directorios PROTEGIDOS (nunca se borran):")
    protected_dirs = [
        "/home/ser/mis cosas/",
        "/home/ser/EIDOS/",
        "/home/ser/Documents/",
        "/home/ser/Downloads/",
        "Cualquier directorio en /home/ser/ (excepto ~/.eidos/)",
    ]
    for protected in protected_dirs:
        print(f"   🛡️  {protected}")

    print("\n💡 Regla simple:")
    print("   ✅ Borrar: Archivos en ~/.eidos/downloads/, frames/, videos/")
    print("   🛡️  Proteger: TODO lo demás en /home/ser/")


def main():
    """Ejecutar todos los tests"""
    print("\n🔐 EIDOS - Test de Protección de Archivos")
    print("=" * 60)

    # Test de protección
    protection_ok = test_user_files_protected()

    # Resumen de protecciones
    test_protection_summary()

    # Resultado final
    print("\n" + "="*60)
    if protection_ok:
        print("✅ Sistema de protección funcionando correctamente")
        print("🛡️  Tus archivos están SEGUROS")
        return 0
    else:
        print("⚠️  Hay problemas con el sistema de protección")
        return 1


if __name__ == "__main__":
    sys.exit(main())
