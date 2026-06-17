"""
Tests para ColonyCommunity - 6 tests para 85% autonomía
"""
import sys
sys.path.insert(0, '/home/ser/EIDOS')

import unittest
import tempfile
import sqlite3
import shutil
from pathlib import Path
from unittest import mock

# Importar y parchear DB_PATH antes de crear instancias
from core import colony_community


class TestColonyCommunity(unittest.TestCase):
    """Tests para el sistema Colony Community de EIDOS."""
    
    def setUp(self):
        """Setup con DB temporal para cada test."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_colony.db"
        # Parchear DB_PATH temporalmente
        self.original_db_path = colony_community.DB_PATH
        colony_community.DB_PATH = self.db_path
        # Importar clase después del parche
        from core.colony_community import ColonyCommunity
        self.ColonyClass = ColonyCommunity
        self.colony = ColonyCommunity()
    
    def tearDown(self):
        """Cleanup después de cada test."""
        colony_community.DB_PATH = self.original_db_path
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_init_creates_brain_tasks_table(self):
        """BUG-9: brain_tasks debe existir desde _init_db()."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='brain_tasks'"
            )
            result = cursor.fetchone()
            self.assertIsNotNone(result)
            self.assertEqual(result[0], 'brain_tasks')
    
    def test_init_creates_registered_modules_table(self):
        """BUG-9: registered_modules debe existir desde _init_db()."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='registered_modules'"
            )
            result = cursor.fetchone()
            self.assertIsNotNone(result)
            self.assertEqual(result[0], 'registered_modules')
    
    def test_get_colony_status_works(self):
        """get_colony_status debe retornar dict sin crashear."""
        status = self.colony.get_colony_status()
        self.assertIsInstance(status, dict)
        self.assertIn('colony_brain', status)
        self.assertIn('tasks', status)
        self.assertIn('active_modules', status)
    
    def test_make_decision_returns_valid_action(self):
        """make_decision debe retornar decisión válida."""
        context = {'cycle_count': 0, 'timestamp': 1234567890}
        decision = self.colony.make_decision(context)
        self.assertIsInstance(decision, dict)
        self.assertIn('action', decision)
        valid_actions = ['wait', 'process_task', 'scan_for_tasks',
                         'communicate', 'optimize', 'request_input',
                         'observe_system', 'learn', 'evolve', 'patrol']
        self.assertIn(decision['action'], valid_actions)
    
    def test_submit_and_complete_task(self):
        """submit_task y complete_task deben funcionar sin crashear."""
        # Submit task - retorna formato "pending:module_target"
        result = self.colony.submit_task(
            task_id='test_task_001',
            task_type='test_task',
            payload={'data': 'test'},
            priority=5
        )
        # Verifica que retorna string con formato esperado
        self.assertIsInstance(result, str)
        self.assertTrue(result.startswith('pending:'))
        # Complete task - solo verificar que no crashea
        self.colony.complete_task('test_task_001', {'result': 'success'}, success=True)
    
    def test_execute_in_background(self):
        """execute_in_background debe aceptar task sin crashear."""
        result = self.colony.execute_in_background(
            task_id='test_bg_1',
            command='echo "hello"'
        )
        self.assertIsInstance(result, dict)
        self.assertIn('success', result)


class TestColonySingleton(unittest.TestCase):
    """Test singleton pattern (BUG-11)."""
    
    def test_singleton_returns_same_instance(self):
        """BUG-11: Singleton debe retornar misma instancia."""
        from core.colony_community import get_colony_community, _community
        # Reset singleton para test limpio
        import core.colony_community as cc
        cc._community = None
        colony1 = get_colony_community()
        colony2 = get_colony_community()
        self.assertIs(colony1, colony2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
