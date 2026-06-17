"""Tests para BrainMemory - PASO 7"""
import sys
import os
import threading
import time

# Añadir path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.brain_memory import BrainMemory, get_brain_memory


class TestBrainMemory:
    """Tests para sistema de memoria."""
    
    def test_remember_returns_int(self):
        """remember() debe retornar un int (memory ID)."""
        bm = BrainMemory()
        mem_id = bm.remember("test content", tags=["test"], importance=0.5)
        assert isinstance(mem_id, int), f"Expected int, got {type(mem_id)}"
        print(f"✓ remember() returns int: {mem_id}")
    
    def test_recall_returns_list(self):
        """recall() debe retornar una lista."""
        bm = BrainMemory()
        bm.remember("find this", tags=["searchable"], importance=0.8)
        results = bm.recall("find")
        assert isinstance(results, list), f"Expected list, got {type(results)}"
        print(f"✓ recall() returns list with {len(results)} items")
    
    def test_singleton_same_instance(self):
        """get_brain_memory() debe retornar la misma instancia."""
        bm1 = get_brain_memory()
        bm2 = get_brain_memory()
        assert bm1 is bm2, "Singleton debe retornar misma instancia"
        print("✓ Singleton retorna misma instancia")
    
    def test_working_memory_eviction(self):
        """Working memory debe evict cuando excede max_entries."""
        bm = BrainMemory()
        # Llenar working memory
        for i in range(150):
            bm.working.add("user", f"item {i}", importance=0.1)
        # Verificar que no excede max_entries
        assert len(bm.working.entries) <= bm.working.max_entries
        print(f"✓ Working memory eviction: {len(bm.working.entries)} <= {bm.working.max_entries}")
    
    def test_thread_safety(self):
        """Singleton debe ser thread-safe."""
        instances = []
        
        def get_instance():
            instances.append(get_brain_memory())
        
        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # Todas las instancias deben ser el mismo objeto
        first = instances[0]
        assert all(i is first for i in instances), "Thread-safety falló"
        print("✓ Thread-safety singleton confirmado")


def run_tests():
    """Ejecutar todos los tests."""
    test = TestBrainMemory()
    
    print("=" * 60)
    print("TESTS BrainMemory")
    print("=" * 60)
    
    try:
        test.test_remember_returns_int()
        test.test_recall_returns_list()
        test.test_singleton_same_instance()
        test.test_working_memory_eviction()
        test.test_thread_safety()
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
