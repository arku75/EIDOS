#!/usr/bin/env python3
"""
Test Completo de EidosBrain y Sistemas Inteligentes
=====================================================
Verifica que todos los sistemas del cerebro de EIDOS funcionen correctamente.

Tests:
1. EidosBrain (orquestador)
2. ConversationMemory
3. SERProfile
4. IntentRecognizer (NLU)
5. TaskDecomposer
6. Modo LIBRE
7. Integración completa
"""
import sys
import os
from pathlib import Path

# Agregar EIDOS al path
EIDOS_DIR = Path("/home/ser/EIDOS")
sys.path.insert(0, str(EIDOS_DIR))

# Colors
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
NC = '\033[0m'

def test(name):
    print(f"{BLUE}▶ {name}...{NC}", end=" ")

def ok(msg=""):
    print(f"{GREEN}✅ {msg}{NC}")

def fail(msg=""):
    print(f"{RED}❌ {msg}{NC}")

def warn(msg=""):
    print(f"{YELLOW}⚠️  {msg}{NC}")

def header(text):
    print(f"\n{CYAN}╔═══════════════════════════════════════════════════════════════════════╗{NC}")
    print(f"{CYAN}║{text:^71}║{NC}")
    print(f"{CYAN}╚═══════════════════════════════════════════════════════════════════════╝{NC}\n")

# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: EidosBrain
# ══════════════════════════════════════════════════════════════════════════════

def test_eidos_brain():
    header("TEST 1: EIDOS BRAIN")

    test("Import EidosBrain")
    try:
        from core.eidos_brain import EidosBrain, get_eidos_brain
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear instancia")
    try:
        brain = get_eidos_brain()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Procesar comando simple")
    try:
        result = brain.process("di hola", show_thinking=False)
        ok(f"Output: '{result.output[:30]}...'")
    except Exception as e:
        fail(str(e))
        return False

    test("Verificar ThoughtProcess")
    try:
        assert result.thought_process is not None
        ok(f"Confidence: {result.thought_process.confidence:.0%}")
    except Exception as e:
        fail(str(e))
        return False

    test("Cambiar a modo LIBRE")
    try:
        brain.set_mode("LIBRE")
        assert brain.is_libre_mode == True
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Volver a modo PLAN+EDIT")
    try:
        brain.set_mode("PLAN+EDIT")
        assert brain.is_libre_mode == False
        ok()
    except Exception as e:
        fail(str(e))
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: ConversationMemory
# ══════════════════════════════════════════════════════════════════════════════

def test_conversation_memory():
    header("TEST 2: CONVERSATION MEMORY")

    test("Import ConversationMemory")
    try:
        from core.eidos_brain import ConversationMemory
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear memoria")
    try:
        memory = ConversationMemory()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Agregar intercambio")
    try:
        memory.add_exchange(
            user="Hola EIDOS",
            assistant="Hola SER, ¿en qué puedo ayudarte?",
            metadata={"test": True}
        )
        ok(f"Mensajes: {len(memory.messages)}")
    except Exception as e:
        fail(str(e))
        return False

    test("Obtener contexto")
    try:
        context = memory.get_context()
        assert len(context) > 0
        ok(f"Contexto: {len(context)} chars")
    except Exception as e:
        fail(str(e))
        return False

    test("Limpiar memoria")
    try:
        memory.clear()
        assert len(memory.messages) == 0
        ok()
    except Exception as e:
        fail(str(e))
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: SERProfile
# ══════════════════════════════════════════════════════════════════════════════

def test_ser_profile():
    header("TEST 3: SER PROFILE")

    test("Import SERProfile")
    try:
        from core.eidos_brain import SERProfile
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear perfil")
    try:
        profile = SERProfile()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Aprender de interacción")
    try:
        profile.learn_from_interaction(
            "escanea la red rápido",
            type('obj', (object,), {"success": True, "command": "nmap"})()
        )
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Obtener preferencias")
    try:
        prefs = profile.get_preferences()
        ok(f"Preferencias: {len(prefs)} configuradas")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: IntentRecognizer
# ══════════════════════════════════════════════════════════════════════════════

def test_intent_recognizer():
    header("TEST 4: INTENT RECOGNIZER")

    test("Import IntentRecognizer")
    try:
        from core.intent_recognizer import IntentRecognizer, get_intent_recognizer
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear recognizer")
    try:
        recognizer = get_intent_recognizer()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Reconocer intent en español")
    try:
        intent = recognizer.recognize("escanea rápido la red 192.168.1.0/24")
        assert intent.action == "scan"
        assert intent.target_type in ["network", "host"]
        assert "fast" in intent.modifiers or "quick" in intent.modifiers
        ok(f"Action: {intent.action}, Target: {intent.target_type}, Confidence: {intent.confidence:.0%}")
    except Exception as e:
        fail(str(e))
        return False

    test("Reconocer intent en inglés")
    try:
        intent = recognizer.recognize("scan the network quickly")
        assert intent.action == "scan"
        ok(f"Language: {intent.language}, Confidence: {intent.confidence:.0%}")
    except Exception as e:
        fail(str(e))
        return False

    test("Detectar exploit")
    try:
        intent = recognizer.recognize("hackea esta web vulnerable.com")
        assert intent.action == "exploit"
        ok(f"Action: {intent.action}")
    except Exception as e:
        fail(str(e))
        return False

    test("Detectar learning")
    try:
        intent = recognizer.recognize("aprende a usar nmap")
        assert intent.action == "learn"
        ok(f"Action: {intent.action}")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: TaskDecomposer
# ══════════════════════════════════════════════════════════════════════════════

def test_task_decomposer():
    header("TEST 5: TASK DECOMPOSER")

    test("Import TaskDecomposer")
    try:
        from core.task_decomposer import TaskDecomposer, get_task_decomposer
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear decomposer")
    try:
        decomposer = get_task_decomposer()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Descomponer web audit")
    try:
        plan = decomposer.decompose("audita la web example.com")
        assert plan.task_type == "web_audit"
        assert plan.target == "example.com"
        assert len(plan.steps) > 0
        ok(f"Plan: {len(plan.steps)} pasos, {plan.total_estimated_time//60} min")
    except Exception as e:
        fail(str(e))
        return False

    test("Descomponer network scan")
    try:
        plan = decomposer.decompose("escanea la red 192.168.1.0/24")
        assert plan.task_type == "network_scan"
        assert "192.168.1.0/24" in plan.target
        ok(f"Pasos: {len(plan.steps)}")
    except Exception as e:
        fail(str(e))
        return False

    test("Descomponer learn tool")
    try:
        plan = decomposer.decompose("aprende a usar gobuster")
        assert plan.task_type == "learn_tool"
        assert "gobuster" in plan.target
        ok(f"Pasos: {len(plan.steps)}")
    except Exception as e:
        fail(str(e))
        return False

    test("Verificar dependencies")
    try:
        plan = decomposer.decompose("audita example.com")
        # Verificar que hay dependencias
        has_deps = any(len(step.dependencies) > 0 for step in plan.steps)
        assert has_deps
        ok("Dependencias detectadas")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: Modo LIBRE
# ══════════════════════════════════════════════════════════════════════════════

def test_modo_libre():
    header("TEST 6: MODO LIBRE")

    test("Import EidosBrain")
    try:
        from core.eidos_brain import get_eidos_brain
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Activar modo LIBRE")
    try:
        brain = get_eidos_brain()
        brain.set_mode("LIBRE")
        assert brain.is_libre_mode == True
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Decidir acción autónoma")
    try:
        action = brain._decide_autonomous_action()
        assert action is not None
        assert "type" in action
        assert "description" in action
        ok(f"Acción: {action['type']}")
    except Exception as e:
        fail(str(e))
        return False

    test("Ejecutar exploración autónoma")
    try:
        brain._autonomous_explore()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Proponer tarea autónoma")
    try:
        brain._autonomous_propose_task()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
# TEST 7: Integración Completa
# ══════════════════════════════════════════════════════════════════════════════

def test_integration():
    header("TEST 7: INTEGRACIÓN COMPLETA")

    test("Flujo completo: Input → Intent → Plan → Execute")
    try:
        from core.eidos_brain import get_eidos_brain
        from core.intent_recognizer import get_intent_recognizer
        from core.task_decomposer import get_task_decomposer

        brain = get_eidos_brain()
        recognizer = get_intent_recognizer()
        decomposer = get_task_decomposer()

        # Flujo completo
        user_input = "escanea la red 192.168.1.0/24"

        # 1. Reconocer intent
        intent = recognizer.recognize(user_input)

        # 2. Descomponer tarea
        plan = decomposer.decompose(user_input, intent)

        # 3. Procesar con brain (sin ejecutar realmente)
        brain.current_mode = "PLAN"  # Solo planear
        result = brain.process(user_input, show_thinking=False)

        ok(f"Flujo OK: Intent={intent.action}, Plan={len(plan.steps)} pasos")
    except Exception as e:
        fail(str(e))
        return False

    test("Memoria persistente entre sesiones")
    try:
        from core.eidos_brain import ConversationMemory

        memory1 = ConversationMemory()
        memory1.add_exchange("test 1", "response 1")

        # Simular nueva sesión
        memory2 = ConversationMemory()
        assert len(memory2.messages) >= 2
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Perfil persistente")
    try:
        from core.eidos_brain import SERProfile

        profile1 = SERProfile()
        profile1.profile["test"] = "value"
        profile1._save()

        # Nueva sesión
        profile2 = SERProfile()
        assert profile2.profile.get("test") == "value"
        ok()
    except Exception as e:
        fail(str(e))
        return False

    return True

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{CYAN}╔═══════════════════════════════════════════════════════════════════════╗{NC}")
    print(f"{CYAN}║                                                                       ║{NC}")
    print(f"{CYAN}║         EIDOS BRAIN & INTELLIGENT SYSTEMS - TEST SUITE               ║{NC}")
    print(f"{CYAN}║                                                                       ║{NC}")
    print(f"{CYAN}╚═══════════════════════════════════════════════════════════════════════╝{NC}\n")

    tests = [
        ("EidosBrain", test_eidos_brain),
        ("ConversationMemory", test_conversation_memory),
        ("SERProfile", test_ser_profile),
        ("IntentRecognizer", test_intent_recognizer),
        ("TaskDecomposer", test_task_decomposer),
        ("Modo LIBRE", test_modo_libre),
        ("Integración", test_integration),
    ]

    results = []

    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"{RED}❌ Test '{name}' crashed: {e}{NC}")
            results.append((name, False))

    # Resumen
    print(f"\n{CYAN}╔═══════════════════════════════════════════════════════════════════════╗{NC}")
    print(f"{CYAN}║                           RESUMEN DE TESTS                            ║{NC}")
    print(f"{CYAN}╚═══════════════════════════════════════════════════════════════════════╝{NC}\n")

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for name, success in results:
        icon = f"{GREEN}✅{NC}" if success else f"{RED}❌{NC}"
        print(f"  {icon} {name}")

    print(f"\n{BLUE}{'═' * 75}{NC}")
    print(f"\n  Total: {total}")
    print(f"  Pasados: {GREEN}{passed}{NC}")
    print(f"  Fallados: {RED}{total - passed}{NC}")
    print(f"  Tasa de éxito: {GREEN if passed == total else YELLOW}{passed/total*100:.1f}%{NC}\n")

    if passed == total:
        print(f"{GREEN}{'═' * 75}{NC}")
        print(f"{GREEN}✅ TODOS LOS TESTS PASARON - EIDOS BRAIN COMPLETAMENTE FUNCIONAL{NC}")
        print(f"{GREEN}{'═' * 75}{NC}\n")
        return 0
    else:
        print(f"{YELLOW}{'═' * 75}{NC}")
        print(f"{YELLOW}⚠️  ALGUNOS TESTS FALLARON - REVISAR ARRIBA{NC}")
        print(f"{YELLOW}{'═' * 75}{NC}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
