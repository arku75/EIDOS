#!/usr/bin/env python3
"""
Test Completo de EIDOS Guardians & Advanced Systems
=====================================================
Tests exhaustivos para verificar que todos los sistemas nuevos funcionan.

Tests:
  1. Phoenix Guardian
  2. Mirror Guardian
  3. Auto-Checkpoint
  4. Slash Commands
  5. Config TUI
  6. Operation Modes
  7. Auto-Learner
  8. Comando Global EIDOS
  9. Integración Kernel
"""
import sys
import os
import time
from pathlib import Path

# Agregar EIDOS al path
EIDOS_DIR = Path("/home/ser/EIDOS")
sys.path.insert(0, str(EIDOS_DIR))

# Color output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
NC = '\033[0m'

def print_header(text):
    print(f"\n{CYAN}╔═══════════════════════════════════════════════════════════════════════╗{NC}")
    print(f"{CYAN}║{text:^71}║{NC}")
    print(f"{CYAN}╚═══════════════════════════════════════════════════════════════════════╝{NC}\n")

def test(name):
    print(f"{BLUE}▶ {name}...{NC}", end=" ")

def ok(msg=""):
    print(f"{GREEN}✅ {msg}{NC}")

def fail(msg=""):
    print(f"{RED}❌ {msg}{NC}")

def warn(msg=""):
    print(f"{YELLOW}⚠️  {msg}{NC}")

# ============================================================================
# TEST 1: Phoenix Guardian
# ============================================================================
def test_phoenix():
    print_header("TEST 1: PHOENIX GUARDIAN")

    test("Import Phoenix")
    try:
        from core.phoenix import get_phoenix, PhoenixGuardian, HeartbeatData
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear instancia Phoenix")
    try:
        phoenix = get_phoenix()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Enviar heartbeat")
    try:
        phoenix.heartbeat(mode="PLAN+EDIT", task="Test unitario")
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Leer heartbeat")
    try:
        hb = phoenix._load_heartbeat()
        assert hb is not None, "Heartbeat no encontrado"
        assert hb.mode == "PLAN+EDIT", f"Modo incorrecto: {hb.mode}"
        ok(f"Modo: {hb.mode}, Tarea: {hb.task}")
    except Exception as e:
        fail(str(e))
        return False

    test("Verificar heartbeat vivo")
    try:
        hb = phoenix._load_heartbeat()
        assert hb.is_alive(), "Heartbeat reporta muerto"
        ok(f"Tiempo desde beat: {hb.time_since_last_beat():.1f}s")
    except Exception as e:
        fail(str(e))
        return False

    test("Status de Phoenix")
    try:
        status = phoenix.status()
        assert "phoenix" in status
        assert "eidos" in status
        ok(f"Resurrecciones: {status['phoenix']['resurrections']}")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# TEST 2: Mirror Guardian
# ============================================================================
def test_mirror():
    print_header("TEST 2: MIRROR GUARDIAN")

    test("Import Mirror")
    try:
        from core.mirror import get_mirror, MirrorGuardian, ChangeProposal
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear instancia Mirror")
    try:
        mirror = get_mirror()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear propuesta de cambio")
    try:
        from core.mirror import create_code_improvement_proposal

        proposal = create_code_improvement_proposal(
            file_path="test.py",
            old_code="print('old')",
            new_code="print('new')",
            reason="Test de Mirror Guardian",
            tests=["python -c \"print('test')\""]
        )

        assert proposal is not None
        ok(f"Proposal ID: {proposal.id}")
    except Exception as e:
        fail(str(e))
        return False

    test("Validar cambio (esto puede tardar)")
    try:
        # Crear propuesta simple
        result = mirror.validate_change(proposal)

        assert result is not None
        ok(f"Aprobado: {result.approved}, Confianza: {result.confidence:.2%}")
    except Exception as e:
        fail(str(e))
        return False

    test("Estadísticas de Mirror")
    try:
        stats = mirror.get_validation_stats()
        ok(f"Validaciones: {stats['total']}, Aprobadas: {stats['approved']}")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# TEST 3: Auto-Checkpoint
# ============================================================================
def test_checkpoint():
    print_header("TEST 3: AUTO-CHECKPOINT")

    test("Import Checkpoint")
    try:
        from core.checkpoint import get_auto_checkpoint, load_latest_checkpoint, list_checkpoints
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear instancia AutoCheckpoint")
    try:
        ac = get_auto_checkpoint()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Guardar checkpoint")
    try:
        checkpoint = ac.save_checkpoint(
            mode="PLAN+EDIT",
            task="Test de Auto-Checkpoint"
        )

        assert checkpoint is not None
        ok(f"ID: {checkpoint.id}")
    except Exception as e:
        fail(str(e))
        return False

    test("Cargar último checkpoint")
    try:
        latest = load_latest_checkpoint()
        assert latest is not None
        ok(f"Último: {latest.id}, Modo: {latest.mode}")
    except Exception as e:
        fail(str(e))
        return False

    test("Listar checkpoints")
    try:
        checkpoints = list_checkpoints(limit=5)
        ok(f"Checkpoints disponibles: {len(checkpoints)}")
    except Exception as e:
        fail(str(e))
        return False

    test("Actualizar conversación")
    try:
        ac.update_conversation({"role": "user", "content": "test message"})
        ok(f"Mensajes: {len(ac.conversation_history)}")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# TEST 4: Slash Commands
# ============================================================================
def test_slash_commands():
    print_header("TEST 4: SLASH COMMANDS")

    test("Import Slash Commands")
    try:
        from core.slash_commands import get_command_handler, SlashCommandHandler
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear handler")
    try:
        handler = get_command_handler()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Detectar slash command")
    try:
        assert handler.is_slash_command("/help")
        assert not handler.is_slash_command("not a command")
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Ejecutar /help")
    try:
        result = handler.handle("/help")
        assert result.success
        assert "EIDOS SLASH COMMANDS" in result.output
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Ejecutar /status")
    try:
        result = handler.handle("/status")
        assert result.success
        assert "SYSTEM STATUS" in result.output
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Ejecutar /checkpoint list")
    try:
        result = handler.handle("/checkpoint list")
        assert result.success
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Comando inválido")
    try:
        result = handler.handle("/invalid_command_xyz")
        assert not result.success
        ok("Correctamente rechazado")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# TEST 5: Config TUI
# ============================================================================
def test_config_tui():
    print_header("TEST 5: CONFIG TUI")

    test("Import Config TUI")
    try:
        from core.config_tui import get_config, save_config, EidosConfig
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Cargar configuración")
    try:
        config = get_config()
        assert config is not None
        ok(f"Modo: {config.current_mode}")
    except Exception as e:
        fail(str(e))
        return False

    test("Modificar configuración")
    try:
        config.verbose = True
        config.auto_learning = True
        save_config(config)
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Recargar configuración")
    try:
        config2 = get_config()
        assert config2.verbose == True
        assert config2.auto_learning == True
        ok()
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# TEST 6: Operation Modes
# ============================================================================
def test_operation_modes():
    print_header("TEST 6: OPERATION MODES")

    test("Import Operation Modes")
    try:
        from core.operation_modes import get_operation_mode_manager, OperationModeManager
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear manager")
    try:
        omm = get_operation_mode_manager()
        ok(f"Modo inicial: {omm.current_mode}")
    except Exception as e:
        fail(str(e))
        return False

    test("Cambiar a PLAN")
    try:
        success = omm.set_mode("PLAN")
        assert success
        assert omm.current_mode == "PLAN"
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Verificar permisos PLAN")
    try:
        assert omm.permissions.can_read == True
        assert omm.permissions.can_write_real == False
        assert omm.permissions.can_use_kali_tools == False
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Cambiar a PLAN+EDIT")
    try:
        success = omm.set_mode("PLAN+EDIT")
        assert success
        assert omm.current_mode == "PLAN+EDIT"
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Verificar permisos PLAN+EDIT")
    try:
        assert omm.permissions.can_read == True
        assert omm.permissions.can_write_real == True
        assert omm.permissions.can_use_kali_tools == True
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Ciclar modos")
    try:
        omm.set_mode("PLAN")
        new_mode = omm.cycle_mode()
        assert new_mode == "EDIT"
        ok(f"Nuevo modo: {new_mode}")
    except Exception as e:
        fail(str(e))
        return False

    test("Verificar comando permitido")
    try:
        omm.set_mode("PLAN")
        can_read = omm.can_execute_command("ls -la")
        can_write = omm.can_execute_command("rm -rf /")

        assert can_read == True
        assert can_write == False
        ok()
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# TEST 7: Auto-Learner
# ============================================================================
def test_auto_learner():
    print_header("TEST 7: AUTO-LEARNER")

    test("Import Auto-Learner")
    try:
        from core.auto_learner import get_auto_learner, AutoLearner, LearnedSkill
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear learner")
    try:
        learner = get_auto_learner()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Buscar repo en GitHub (puede tardar)")
    try:
        repo = learner._search_github("nmap")
        if repo:
            ok(f"Encontrado: {repo['full_name']} ({repo['stars']} ⭐)")
        else:
            warn("No se encontró (limite de API?)")
    except Exception as e:
        warn(str(e))

    test("Listar skills aprendidas")
    try:
        skills = learner.list_skills()
        ok(f"Skills: {len(skills)}")
    except Exception as e:
        fail(str(e))
        return False

    # Crear skill manualmente para testing
    test("Crear skill de prueba")
    try:
        skill = LearnedSkill(
            name="test_tool",
            description="Tool de prueba",
            examples=["test_tool --help"],
            usage_pattern="test_tool {target}",
        )
        skill.save()
        ok(f"Skill guardada: {skill.name}")
    except Exception as e:
        fail(str(e))
        return False

    test("Cargar skill")
    try:
        loaded = learner.get_skill("test_tool")
        assert loaded is not None
        assert loaded.name == "test_tool"
        ok()
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# TEST 8: Comando Global EIDOS
# ============================================================================
def test_global_command():
    print_header("TEST 8: COMANDO GLOBAL EIDOS")

    test("Verificar symlink")
    try:
        import subprocess
        result = subprocess.run(["which", "EIDOS"], capture_output=True, text=True)
        if result.returncode == 0:
            ok(f"Ubicación: {result.stdout.strip()}")
        else:
            warn("Symlink no encontrado - necesita instalación manual")
    except Exception as e:
        warn(str(e))

    test("Ejecutar EIDOS --version")
    try:
        import subprocess
        result = subprocess.run(["EIDOS", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            ok(result.stdout.strip())
        else:
            warn("Comando falló")
    except Exception as e:
        warn(str(e))

    return True

# ============================================================================
# TEST 9: Integración Kernel
# ============================================================================
def test_kernel_integration():
    print_header("TEST 9: INTEGRACIÓN KERNEL")

    test("Import Kernel")
    try:
        from core.kernel import DeterministicKernel
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Crear kernel")
    try:
        kernel = DeterministicKernel()
        ok()
    except Exception as e:
        fail(str(e))
        return False

    test("Verificar Advanced Planner integrado")
    try:
        has_planner = hasattr(kernel, 'advanced_planner') and kernel.advanced_planner is not None
        if has_planner:
            ok("Advanced Planner disponible")
        else:
            warn("Advanced Planner no inicializado")
    except Exception as e:
        warn(str(e))

    test("Verificar Trainer integrado")
    try:
        has_trainer = hasattr(kernel, 'trainer') and kernel.trainer is not None
        if has_trainer:
            ok("Trainer disponible")
        else:
            warn("Trainer no inicializado")
    except Exception as e:
        warn(str(e))

    test("Verificar tools disponibles")
    try:
        tool_count = len(kernel.tools)
        ok(f"Tools disponibles: {tool_count}")
    except Exception as e:
        fail(str(e))
        return False

    return True

# ============================================================================
# MAIN
# ============================================================================
def main():
    print(f"\n{CYAN}╔═══════════════════════════════════════════════════════════════════════╗{NC}")
    print(f"{CYAN}║                                                                       ║{NC}")
    print(f"{CYAN}║    EIDOS GUARDIANS & ADVANCED SYSTEMS - TEST SUITE COMPLETO          ║{NC}")
    print(f"{CYAN}║                                                                       ║{NC}")
    print(f"{CYAN}╚═══════════════════════════════════════════════════════════════════════╝{NC}\n")

    tests = [
        ("Phoenix Guardian", test_phoenix),
        ("Mirror Guardian", test_mirror),
        ("Auto-Checkpoint", test_checkpoint),
        ("Slash Commands", test_slash_commands),
        ("Config TUI", test_config_tui),
        ("Operation Modes", test_operation_modes),
        ("Auto-Learner", test_auto_learner),
        ("Comando Global", test_global_command),
        ("Integración Kernel", test_kernel_integration),
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
        print(f"{GREEN}✅ TODOS LOS TESTS PASARON - EIDOS COMPLETAMENTE FUNCIONAL{NC}")
        print(f"{GREEN}{'═' * 75}{NC}\n")
        return 0
    else:
        print(f"{YELLOW}{'═' * 75}{NC}")
        print(f"{YELLOW}⚠️  ALGUNOS TESTS FALLARON - REVISAR ARRIBA{NC}")
        print(f"{YELLOW}{'═' * 75}{NC}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
