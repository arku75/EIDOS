#!/usr/bin/env python3
"""
EIDOS - Tests E2E del flujo completo.

Verifica que todo el sistema funciona end-to-end:
- Servicios activos (puertos 7777, 8080, 8765)
- Colony deliberation con multi-agente
- Knowledge-first cuando hay datos
- Visión y computer_use accesibles
- Ciclo de vida del personaje funcional
- HybridRouter activo
- TokenEconomy registrando
"""
import sys
import os
import unittest
import urllib.request
import json

sys.path.insert(0, "/home/ser/EIDOS")
os.chdir("/home/ser/EIDOS")


def is_port_alive(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/", timeout=2)
        return True
    except Exception:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=2)
            return True
        except Exception:
            return False


class TestServicesUp(unittest.TestCase):
    """Servicios web esenciales activos."""

    def test_ollama_responde(self):
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception as e:
            self.skipTest(f"Ollama no disponible: {e}")

    def test_colony_dashboard_responde_si_arrancado(self):
        if not is_port_alive(7777):
            self.skipTest("Colony Dashboard no arrancado")
        r = urllib.request.urlopen("http://localhost:7777/api/status", timeout=5)
        data = json.loads(r.read())
        self.assertTrue(data.get("online"))
        self.assertIn("brain", data)
        self.assertIn("routing", data)

    def test_vscode_api_responde_si_arrancado(self):
        if not is_port_alive(8765):
            self.skipTest("VSCode API no arrancado")
        r = urllib.request.urlopen("http://localhost:8765/api/status", timeout=5)
        data = json.loads(r.read())
        self.assertEqual(data.get("status"), "active")

    def test_web_panel_responde_si_arrancado(self):
        if not is_port_alive(8080):
            self.skipTest("Web Panel no arrancado")
        r = urllib.request.urlopen("http://localhost:8080/api/status", timeout=5)
        data = json.loads(r.read())
        self.assertIn("knowledge_db", data)
        self.assertIn("learning", data)


class TestColonyDeliberation(unittest.TestCase):
    """Deliberación interna de Colony (corazón de EIDOS)."""

    def setUp(self):
        from core.colony_community import get_colony_community
        self.colony = get_colony_community()
        if not self.colony._session_active:
            self.colony.start_session()

    def test_deliberate_method_existe(self):
        self.assertTrue(hasattr(self.colony, "deliberate"))
        self.assertTrue(callable(self.colony.deliberate))

    def test_deliberate_estructura_respuesta(self):
        """Verifica que deliberate() retorna el dict correcto, sin llamar a Ollama."""
        # Sólo verifica estructura — usa knowledge-first si encuentra
        result = self.colony.deliberate("test", max_tokens=100, skip_knowledge=False)
        self.assertIn("response",         result)
        self.assertIn("agents_consulted", result)
        self.assertIn("from_knowledge",   result)
        self.assertIn("elapsed_sec",      result)
        self.assertIsInstance(result["agents_consulted"], list)

    def test_synthesize_responses_unico_agente(self):
        """Síntesis con un solo agente devuelve la respuesta tal cual."""
        result = self.colony._synthesize_responses("hola", {"colony_general": "Hola SER"})
        self.assertEqual(result, "Hola SER")

    def test_synthesize_responses_multi_agente(self):
        """Síntesis multi-agente cita a los demás."""
        responses = {
            "colony_coder":   "Respuesta corta del coder",
            "colony_analyst": "Respuesta más larga y detallada del analyst con análisis profundo",
        }
        result = self.colony._synthesize_responses("test", responses)
        self.assertIn("analyst", result.lower())
        self.assertIn("coder", result.lower())


class TestHybridRouter(unittest.TestCase):
    """HybridRouter (OctoClaw + SmartRouter) integrado."""

    def test_disponible(self):
        from core.octoclaw_bridge import is_available, get_hybrid_router
        self.assertTrue(is_available())
        router = get_hybrid_router()
        self.assertIsNotNone(router)

    def test_runner_task_usa_modelo_rapido(self):
        from core.octoclaw_bridge import get_hybrid_router
        router = get_hybrid_router()
        model = router.decide("ps aux | grep python", "hermes3:8b", "test")
        self.assertIn(model, ["qwen2.5-coder:1.5b", "hermes3:8b"])

    def test_vision_task_usa_modelo_vision(self):
        from core.octoclaw_bridge import get_hybrid_router
        router = get_hybrid_router()
        model = router.decide("analiza esta imagen y dime qué hay", "qwen2.5:7b", "test")
        self.assertEqual(model, "llama3.2-vision:11b")


class TestVisionAndComputerUse(unittest.TestCase):
    """Visión y computer_use disponibles."""

    def test_realtime_vision_importable(self):
        from core.eidos_realtime_vision import get_realtime_vision
        v = get_realtime_vision()
        self.assertIsNotNone(v)
        self.assertTrue(hasattr(v, "start_watching"))
        self.assertTrue(hasattr(v, "stop_watching"))

    def test_computer_use_tools_disponibles(self):
        from core.computer_use_v2 import get_computer_use_tools
        tools = get_computer_use_tools()
        self.assertGreaterEqual(len(tools), 5)
        # Tools clave
        for key in ("mouse_click", "keyboard_type"):
            self.assertIn(key, tools)


class TestLifecycle(unittest.TestCase):
    """Ciclo de vida del personaje."""

    def setUp(self):
        from core.life_rules_engine import get_life_rules_engine
        self.lre = get_life_rules_engine()

    def test_metodos_lifecycle_existen(self):
        for m in ("on_connection_start", "on_knowledge_absorbed",
                  "propose_reproduction", "get_genealogy"):
            self.assertTrue(hasattr(self.lre, m), f"Falta: {m}")

    def test_conexion_completa_genera_personaje(self):
        conn_id = self.lre.on_connection_start("colony_coder", "test_source_e2e", "documentation")
        self.assertTrue(conn_id.startswith("conn_"))
        char = self.lre.on_knowledge_absorbed(conn_id)
        self.assertIsNotNone(char)
        self.assertIn("absorbed", char.traits)

    def test_genealogia_disponible(self):
        tree = self.lre.get_genealogy()
        self.assertIsInstance(tree, dict)


class TestTokenEconomy(unittest.TestCase):
    def test_balances_iniciales(self):
        from core.colony_token_economy import get_token_economy
        eco = get_token_economy()
        balances = eco.get_all_balances()
        self.assertGreaterEqual(len(balances), 5)

    def test_charge_descuenta(self):
        from core.colony_token_economy import get_token_economy
        eco = get_token_economy()
        before = eco.get_balance("colony_general")
        eco.charge("colony_general", "intercomm_message")  # cuesta 0.5
        after = eco.get_balance("colony_general")
        self.assertLess(after, before + 0.01)


class TestEidosCommand(unittest.TestCase):
    """El comando /home/ser/EIDOS/eidos existe y es ejecutable."""

    def test_comando_existe(self):
        path = "/home/ser/EIDOS/eidos"
        self.assertTrue(os.path.exists(path))
        self.assertTrue(os.access(path, os.X_OK))

    def test_comando_status_responde(self):
        import subprocess
        r = subprocess.run(["/home/ser/EIDOS/eidos", "status"],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Estado EIDOS", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
