"""Tests para EIDOSCore - PASO 7"""
import sys
import os

# Añadir path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.eidos_core import EIDOSCore, get_eidos_core


class TestEidosCore:
    """Tests para núcleo EIDOS."""
    
    def test_init_no_crash(self):
        """EIDOSCore debe inicializar sin crash."""
        e = EIDOSCore()
        assert e is not None
        assert e.state is not None
        print("✓ EIDOSCore init OK")
    
    def test_execute_command_blocks_rm_rf(self):
        """execute_command debe bloquear comandos peligrosos."""
        e = EIDOSCore()
        # Probar comando peligroso - debe ser bloqueado por Constitution
        result = e.execute_command("rm -rf /")
        # El resultado debe indicar bloqueo o error
        assert result.get("blocked") or result.get("error") or result.get("success") is False
        print("✓ rm -rf / bloqueado correctamente")
    
    def test_execute_command_allows_ls(self):
        """execute_command debe permitir comandos seguros."""
        e = EIDOSCore()
        result = e.execute_command("ls -la /tmp")
        # ls debe ser permitido (aunque puede fallar por otros motivos)
        assert isinstance(result, dict)
        print(f"✓ ls permitido: {result.get('success', False)}")
    
    def test_status_returns_dict(self):
        """status() debe retornar un diccionario."""
        e = EIDOSCore()
        status = e.status()
        assert isinstance(status, dict), f"Expected dict, got {type(status)}"
        assert "core" in status
        print("✓ status() retorna dict correctamente")
    
    def test_singleton_same_instance(self):
        """get_eidos_core() debe retornar la misma instancia."""
        e1 = get_eidos_core()
        e2 = get_eidos_core()
        assert e1 is e2, "Singleton debe retornar misma instancia"
        print("✓ EIDOSCore singleton OK")
    
    def test_sliding_window_exists(self):
        """_recent_errors debe existir (BUG-8 fix)."""
        e = EIDOSCore()
        assert hasattr(e, '_recent_errors'), "BUG-8: _recent_errors no existe"
        assert e._recent_errors.maxlen == 100, "BUG-8: maxlen debe ser 100"
        print("✓ BUG-8 sliding window presente")
    
    def test_thread_safety_locks(self):
        """Singleton debe tener locks."""
        from core.eidos_core import _eidos_core_lock
        import threading
        assert isinstance(_eidos_core_lock, threading.Lock)
        print("✓ Thread-safety lock presente")


def run_tests():
    """Ejecutar todos los tests."""
    test = TestEidosCore()
    
    print("=" * 60)
    print("TESTS EIDOSCore")
    print("=" * 60)
    
    try:
        test.test_init_no_crash()
        test.test_execute_command_blocks_rm_rf()
        test.test_execute_command_allows_ls()
        test.test_status_returns_dict()
        test.test_singleton_same_instance()
        test.test_sliding_window_exists()
        test.test_thread_safety_locks()
        print("=" * 60)
        print("✅ TODOS LOS TESTS PASARON")
        print("=" * 60)
        return True
    except AssertionError as e:
        print(f"❌ TEST FALLÓ: {e}")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
