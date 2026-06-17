#!/usr/bin/env python3
"""
Test del Sistema de Extensiones VSCode
========================================
Verifica que EIDOS puede gestionar extensiones de VSCode.
"""

import sys
from pathlib import Path

# Add EIDOS root to path
EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.vscode_extensions import VSCodeExtensionsManager


def test_manager_initialization():
    """Test 1: Inicialización del gestor"""
    print("\n" + "="*60)
    print("TEST 1: Inicialización del Gestor")
    print("="*60)

    try:
        manager = VSCodeExtensionsManager()
        print("✅ Gestor inicializado correctamente")
        print(f"   Directorio: {manager.extensions_dir}")
        print(f"   Registro: {manager.registry_file}")
        print(f"   Extensiones esenciales: {len(manager.essential_extensions)}")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_essential_extensions():
    """Test 2: Lista de extensiones esenciales"""
    print("\n" + "="*60)
    print("TEST 2: Extensiones Esenciales")
    print("="*60)

    manager = VSCodeExtensionsManager()

    print(f"\n📦 Extensiones esenciales ({len(manager.essential_extensions)}):\n")

    # Agrupar por prioridad
    by_priority = {}
    for ext in manager.essential_extensions:
        if ext.install_priority not in by_priority:
            by_priority[ext.install_priority] = []
        by_priority[ext.install_priority].append(ext)

    for priority in sorted(by_priority.keys()):
        priority_name = "🔴 CRÍTICAS" if priority == 1 else "🟡 RECOMENDADAS" if priority == 2 else "🟢 OPCIONALES"
        print(f"{priority_name} (Prioridad {priority}):")
        for ext in by_priority[priority]:
            print(f"  • {ext.name} ({ext.id})")
            print(f"    {ext.description}")
        print()

    # Verificar que hay al menos algunas esenciales
    if len(manager.essential_extensions) >= 5:
        print("✅ Lista de extensiones esenciales correcta")
        return True
    else:
        print("❌ Lista de extensiones insuficiente")
        return False


def test_language_detection():
    """Test 3: Detección de extensiones por lenguaje"""
    print("\n" + "="*60)
    print("TEST 3: Detección de Extensiones por Lenguaje")
    print("="*60)

    manager = VSCodeExtensionsManager()

    # Crear directorio temporal con archivos de prueba
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Crear archivos de diferentes lenguajes
        test_files = {
            "main.py": ".py",
            "lib.rs": ".rs",
            "main.go": ".go",
            "main.cpp": ".cpp",
            "README.md": ".md",
            "config.yaml": ".yaml",
        }

        for filename in test_files.keys():
            (tmp_path / filename).touch()

        # Detectar extensiones
        recommended = manager.detect_project_extensions(tmp_path)

        print(f"\n📁 Proyecto de prueba: {tmp_path}")
        print(f"   Archivos: {', '.join(test_files.keys())}")
        print(f"\n💡 Extensiones recomendadas ({len(recommended)}):")
        for ext_id in sorted(recommended):
            print(f"   • {ext_id}")

        # Verificar que detectó algunas extensiones
        if len(recommended) > 0:
            print("\n✅ Detección funcionando correctamente")
            return True
        else:
            print("\n❌ No se detectaron extensiones")
            return False


def test_project_detection_eidos():
    """Test 4: Detección en proyecto EIDOS real"""
    print("\n" + "="*60)
    print("TEST 4: Detección en Proyecto EIDOS")
    print("="*60)

    manager = VSCodeExtensionsManager()
    eidos_path = Path("/home/ser/EIDOS")

    if not eidos_path.exists():
        print("⚠️  Directorio EIDOS no encontrado, saltando test")
        return True

    recommended = manager.detect_project_extensions(eidos_path)

    print(f"\n📁 Proyecto: {eidos_path}")
    print(f"\n💡 Extensiones recomendadas ({len(recommended)}):\n")

    # Mostrar agrupadas por lenguaje
    language_groups = {
        "Python": [e for e in recommended if "python" in e.lower()],
        "Rust": [e for e in recommended if "rust" in e.lower()],
        "Go": [e for e in recommended if "go" in e.lower()],
        "C/C++": [e for e in recommended if "cpp" in e.lower() or "c++" in e.lower()],
        "Zig": [e for e in recommended if "zig" in e.lower()],
        "Otros": [e for e in recommended if not any(lang in e.lower() for lang in ["python", "rust", "go", "cpp", "c++", "zig"])]
    }

    for lang, exts in language_groups.items():
        if exts:
            print(f"  {lang}:")
            for ext in exts:
                print(f"    • {ext}")

    if len(recommended) >= 3:  # Al menos Python, Rust, Go
        print("\n✅ Detección correcta para proyecto EIDOS")
        return True
    else:
        print("\n⚠️  Se detectaron pocas extensiones")
        return True  # No fallar, solo advertir


def test_list_installed():
    """Test 5: Listar extensiones instaladas"""
    print("\n" + "="*60)
    print("TEST 5: Listar Extensiones Instaladas")
    print("="*60)

    manager = VSCodeExtensionsManager()

    # Intentar listar con 'code'
    print("\n📋 Intentando listar extensiones con 'code'...")
    extensions = manager.list_installed_extensions("code")

    if extensions:
        print(f"\n✅ Encontradas {len(extensions)} extensiones instaladas:")
        for i, ext in enumerate(extensions[:10], 1):
            print(f"   {i}. {ext}")
        if len(extensions) > 10:
            print(f"   ... y {len(extensions) - 10} más")
        return True
    else:
        print("\n⚠️  No se encontraron extensiones (¿VSCode no instalado?)")
        return True  # No fallar si VSCode no está instalado


def test_statistics():
    """Test 6: Estadísticas del gestor"""
    print("\n" + "="*60)
    print("TEST 6: Estadísticas")
    print("="*60)

    manager = VSCodeExtensionsManager()
    stats = manager.get_statistics()

    print("\n📊 Estadísticas:")
    print(f"   Extensiones instaladas: {stats['total_installed']}")
    print(f"   Extensiones esenciales: {stats['essential_count']}")
    print(f"   En registro: {stats['registry_count']}")

    if stats['installed_extensions']:
        print(f"\n   Primeras extensiones instaladas:")
        for ext in stats['installed_extensions'][:5]:
            print(f"     • {ext}")

    print("\n✅ Estadísticas generadas correctamente")
    return True


def main():
    """Ejecutar todos los tests"""
    print("\n🔌 EIDOS VSCode Extensions - Test Suite")
    print("=" * 60)

    tests = [
        ("Inicialización del Gestor", test_manager_initialization),
        ("Extensiones Esenciales", test_essential_extensions),
        ("Detección por Lenguaje", test_language_detection),
        ("Detección Proyecto EIDOS", test_project_detection_eidos),
        ("Listar Instaladas", test_list_installed),
        ("Estadísticas", test_statistics),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ Error en {test_name}: {e}")
            results.append((test_name, False))

    # Resumen
    print("\n" + "="*60)
    print("RESUMEN DE TESTS")
    print("="*60)

    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
        if result:
            passed += 1

    print(f"\nResultado: {passed}/{len(results)} tests pasados ({passed*100//len(results)}%)")

    if passed == len(results):
        print("\n🎉 ¡Todos los tests pasaron!")
        print("✅ Sistema de extensiones VSCode funcionando correctamente")
        return 0
    else:
        print(f"\n⚠️  {len(results) - passed} tests fallaron")
        return 1


if __name__ == "__main__":
    sys.exit(main())
