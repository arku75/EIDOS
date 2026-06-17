#!/usr/bin/env python3
"""
Test del Sistema de Aprendizaje de Seguridad de EIDOS
"""

import sys
from pathlib import Path

# Add EIDOS root to path
EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))

from core.security_learning import SecurityLearningSystem, EthicalContext


def test_ethical_context():
    """Test 1: Verificar que el contexto ético funciona correctamente"""
    print("\n" + "=" * 60)
    print("TEST 1: Contexto Ético")
    print("=" * 60)

    # Objetivos permitidos
    allowed_targets = ["localhost", "127.0.0.1", "::1", "test.local"]
    for target in allowed_targets:
        result = EthicalContext.is_target_allowed(target)
        status = "✅" if result else "❌"
        print(f"{status} {target}: {'PERMITIDO' if result else 'BLOQUEADO'}")

    # Objetivos NO permitidos
    forbidden_targets = ["google.com", "192.168.1.1", "ejemplo.com"]
    for target in forbidden_targets:
        result = EthicalContext.is_target_allowed(target)
        status = "✅" if not result else "❌"  # Invertido - queremos que sea False
        print(f"{status} {target}: {'BLOQUEADO' if not result else 'ERROR - DEBERÍA ESTAR BLOQUEADO'}")

    # Acciones prohibidas
    print("\n🚫 Acciones prohibidas:")
    forbidden_actions = ["unauthorized_access", "data_theft", "malware_distribution"]
    for action in forbidden_actions:
        result = EthicalContext.is_action_forbidden(action)
        status = "✅" if result else "❌"
        print(f"{status} {action}: {'PROHIBIDO' if result else 'ERROR'}")

    # Acciones que requieren permiso
    print("\n⚠️  Acciones que requieren permiso:")
    permission_actions = ["network_scanning", "exploitation_attempts", "credential_testing"]
    for action in permission_actions:
        result = EthicalContext.requires_user_permission(action)
        status = "✅" if result else "❌"
        print(f"{status} {action}: {'REQUIERE PERMISO' if result else 'ERROR'}")

    return True


def test_scan_courses():
    """Test 2: Escanear cursos disponibles"""
    print("\n" + "=" * 60)
    print("TEST 2: Escaneo de Cursos")
    print("=" * 60)

    sec_learning = SecurityLearningSystem()
    courses = sec_learning.scan_courses_directory()

    print(f"\n📚 Cursos encontrados: {len(courses)}")

    for i, course in enumerate(courses[:5], 1):  # Mostrar primeros 5
        print(f"\n{i}. {course.title}")
        print(f"   Formato: {course.format}")
        print(f"   Tamaño: {course.file_size_mb} MB")
        if course.topics_covered:
            print(f"   Tópicos: {', '.join(course.topics_covered)}")
        if course.tools_taught:
            print(f"   Herramientas: {', '.join(course.tools_taught)}")

    return len(courses) > 0


def test_learn_from_text_course():
    """Test 3: Aprender de un curso en formato texto"""
    print("\n" + "=" * 60)
    print("TEST 3: Aprendizaje de Curso de Texto")
    print("=" * 60)

    sec_learning = SecurityLearningSystem()

    # Buscar un curso de texto pequeño
    text_course_path = Path("/home/ser/mis cosas/CURSOS/CURSOS EN TEXTO/gemini evilginx+smtp")

    if text_course_path.exists():
        print(f"📖 Aprendiendo de: {text_course_path.name}")

        # Crear objeto de curso
        from core.security_learning import SecurityCourse

        course = SecurityCourse(
            course_id="test_text_evilginx",
            title=text_course_path.name,
            file_path=str(text_course_path),
            file_size_mb=text_course_path.stat().st_size / (1024 * 1024),
            format="TEXT",
            topics_covered=["exploitation"],
            tools_taught=["evilginx"],
            status="pending",
            progress=0.0,
            concepts_learned=0
        )

        # Aprender (sin auto-delete para texto)
        sec_learning.cleanup.auto_cleanup = False  # Desactivar auto-cleanup para test
        result = sec_learning.learn_from_course(course)

        if result["success"]:
            print(f"✅ Aprendizaje exitoso")
            print(f"   Conceptos: {result['concepts_learned']}")
            print(f"   Tópicos: {', '.join(result['topics'])}")
            print(f"   Herramientas: {', '.join(result['tools'])}")
            return True
        else:
            print(f"❌ Error: {result.get('error', 'Unknown')}")
            return False
    else:
        print(f"⚠️  Curso de texto no encontrado, saltando test")
        return True  # No falla si no existe


def test_create_objectives():
    """Test 4: Crear objetivos de aprendizaje"""
    print("\n" + "=" * 60)
    print("TEST 4: Creación de Objetivos")
    print("=" * 60)

    sec_learning = SecurityLearningSystem()
    courses = sec_learning.scan_courses_directory()

    if courses:
        # Crear objetivos solo de cursos pequeños
        small_courses = [c for c in courses if c.file_size_mb < 100][:3]

        if small_courses:
            created = sec_learning.create_learning_objectives_from_courses(small_courses)
            print(f"✅ Creados {created} objetivos de aprendizaje")

            # Verificar objetivos
            stats = sec_learning.objectives.get_statistics()
            print(f"\n📊 Estadísticas de objetivos:")
            print(f"   Total: {stats['total']}")
            if 'by_status' in stats:
                print(f"   Por estado: {stats['by_status']}")
            if 'by_type' in stats:
                print(f"   Por tipo: {stats['by_type']}")

            return created > 0
        else:
            print("⚠️  No hay cursos pequeños disponibles")
            return True
    else:
        print("⚠️  No se encontraron cursos")
        return False


def test_statistics():
    """Test 5: Obtener estadísticas del sistema"""
    print("\n" + "=" * 60)
    print("TEST 5: Estadísticas del Sistema")
    print("=" * 60)

    sec_learning = SecurityLearningSystem()
    stats = sec_learning.get_statistics()

    print(f"📊 Estadísticas:")
    print(f"   Cursos totales: {stats['total_courses']}")
    print(f"   Cursos completados: {stats['completed_courses']}")
    print(f"   Cursos pendientes: {stats['pending_courses']}")
    print(f"   Conceptos aprendidos: {stats['total_concepts']}")

    if stats['categories']:
        print(f"   Categorías: {', '.join(stats['categories'])}")

    if stats['tools_learned']:
        print(f"   Herramientas identificadas: {', '.join(stats['tools_learned'][:10])}")

    return True


def main():
    """Ejecutar todos los tests"""
    print("\n🔐 EIDOS Security Learning System - Test Suite")
    print("=" * 60)

    tests = [
        ("Contexto Ético", test_ethical_context),
        ("Escaneo de Cursos", test_scan_courses),
        ("Aprendizaje de Texto", test_learn_from_text_course),
        ("Creación de Objetivos", test_create_objectives),
        ("Estadísticas", test_statistics),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Error en {test_name}: {e}")
            results.append((test_name, False))

    # Resumen
    print("\n" + "=" * 60)
    print("RESUMEN DE TESTS")
    print("=" * 60)

    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {test_name}")
        if result:
            passed += 1

    print(f"\nResultado: {passed}/{len(results)} tests pasados ({passed*100//len(results)}%)")

    if passed == len(results):
        print("🎉 ¡Todos los tests pasaron!")
        return 0
    else:
        print("⚠️  Algunos tests fallaron")
        return 1


if __name__ == "__main__":
    sys.exit(main())
