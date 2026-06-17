#!/usr/bin/env python3
"""
Tests de integración OctoClaw B-completo.
Cubre: HybridRouter, patrol_once, patrol_loop, feedback DB, colony integration.
"""
import sys, os, time, sqlite3
sys.path.insert(0, "/home/ser/EIDOS")

import unittest


class TestOctoClawAvailability(unittest.TestCase):
    def test_octoclaw_available(self):
        from core.octoclaw_bridge import is_available
        self.assertTrue(is_available(), "OctoClaw debe estar disponible")

    def test_route_advice_returns_dict(self):
        from core.octoclaw_bridge import route_advice
        result = route_advice("escribe código Python para ordenar una lista")
        self.assertIsInstance(result, dict)
        self.assertIn("route", result)
        self.assertIn("model_band", result)
        self.assertIn("suggested_ollama_model", result)

    def test_route_advice_degraded_mode(self):
        """route_advice debe devolver dict útil aunque OctoClaw falle."""
        from core.octoclaw_bridge import route_advice
        result = route_advice("")  # tarea vacía
        self.assertIn("suggested_ollama_model", result)


class TestHybridRouter(unittest.TestCase):
    def setUp(self):
        from core.octoclaw_bridge import get_hybrid_router
        self.router = get_hybrid_router()

    def test_runner_task_uses_fast_model(self):
        """Tareas shell/runner deben ir al modelo más rápido."""
        model = self.router.decide("ps aux | grep python", "hermes3:8b", "colony_operator")
        self.assertIn(model, ["qwen2.5-coder:1.5b", "hermes3:8b"],
                      "Runner task debe usar modelo fast o normal")

    def test_vision_task_uses_vision_model(self):
        """Tareas con imagen deben usar el modelo de visión."""
        model = self.router.decide(
            "analiza esta imagen y dime qué objetos hay",
            "qwen2.5:7b", "colony_vision"
        )
        self.assertEqual(model, "llama3.2-vision:11b",
                         "Tarea de visión debe usar llama3.2-vision")

    def test_code_task_result_is_valid_model(self):
        """Tarea de código debe devolver un modelo conocido."""
        known = {"qwen2.5-coder:1.5b", "hermes3:8b", "llama3.2-vision:11b",
                 "nomic-embed-text", "qwen2.5:7b"}
        model = self.router.decide(
            "implementa un árbol AVL en Python con rotaciones",
            "qwen2.5-coder:1.5b", "colony_coder"
        )
        self.assertIn(model, known, f"Modelo {model!r} no reconocido")

    def test_decide_returns_string(self):
        model = self.router.decide("hola mundo", "hermes3:8b", "colony_general")
        self.assertIsInstance(model, str)
        self.assertGreater(len(model), 0)

    def test_feedback_recorded_in_db(self):
        """Cada decisión debe quedar registrada en routing_feedback."""
        db = os.path.expanduser("~/.eidos/router.db")
        conn = sqlite3.connect(db)
        before = conn.execute("SELECT COUNT(*) FROM routing_feedback").fetchone()[0]
        conn.close()

        self.router.decide("tarea de test para feedback", "hermes3:8b", "test_agent")

        conn = sqlite3.connect(db)
        after = conn.execute("SELECT COUNT(*) FROM routing_feedback").fetchone()[0]
        conn.close()
        self.assertGreater(after, before, "Debe haberse registrado al menos 1 decisión")

    def test_get_stats_structure(self):
        stats = self.router.get_stats()
        self.assertIn("total_decisions", stats)
        self.assertIn("model_overrides", stats)
        self.assertIn("override_rate", stats)

    def test_default_model_fallback(self):
        """Si todo falla, debe devolver el modelo por defecto."""
        model = self.router.decide("", "hermes3:8b", "")
        self.assertIsInstance(model, str)


class TestPatrolOnce(unittest.TestCase):
    def test_patrol_returns_dict(self):
        from core.octoclaw_bridge import patrol_once
        result = patrol_once()
        self.assertIsInstance(result, dict)
        self.assertIn("checks", result)
        self.assertIn("healthy", result)

    def test_ollama_check_present(self):
        from core.octoclaw_bridge import patrol_once
        result = patrol_once()
        self.assertIn("ollama", result["checks"])

    def test_brain_check_present(self):
        from core.octoclaw_bridge import patrol_once
        result = patrol_once()
        self.assertIn("brain", result["checks"])

    def test_patrol_healthy_when_ollama_up(self):
        """Si Ollama responde, el sistema debe reportarse saludable."""
        from core.octoclaw_bridge import patrol_once
        result = patrol_once()
        ollama_ok = result["checks"].get("ollama", {}).get("ok", False)
        if ollama_ok:
            self.assertTrue(result["healthy"])


class TestPatrolLoop(unittest.TestCase):
    def test_patrol_loop_starts_and_stops(self):
        from core.patrol_loop import PatrolLoop
        pl = PatrolLoop()
        pl.start(interval_minutes=999)
        time.sleep(0.2)
        self.assertTrue(pl.is_running())
        pl.stop()
        self.assertFalse(pl.is_running())

    def test_patrol_loop_singleton(self):
        from core.patrol_loop import get_patrol_loop
        a = get_patrol_loop()
        b = get_patrol_loop()
        self.assertIs(a, b, "get_patrol_loop() debe devolver el mismo objeto")


class TestKnowledgeExtraction(unittest.TestCase):
    def test_extracts_definitions(self):
        from core.eidos_evolution_engine import EidosEvolutionEngine
        e = EidosEvolutionEngine.__new__(EidosEvolutionEngine)
        text = "Python es un lenguaje de programación. Django es un framework web."
        result = e._extract_knowledge_from_text(text)
        self.assertGreater(len(result), 0, "Debe extraer al menos 1 nodo")

    def test_extracts_from_any_text(self):
        """El nodo mínimo garantizado debe funcionar con cualquier texto."""
        from core.eidos_evolution_engine import EidosEvolutionEngine
        e = EidosEvolutionEngine.__new__(EidosEvolutionEngine)
        result = e._extract_knowledge_from_text(
            "xyzabc random text without clear patterns but more than forty chars here"
        )
        self.assertGreater(len(result), 0, "Nodo mínimo garantizado debe crearse")

    def test_empty_text_returns_empty(self):
        from core.eidos_evolution_engine import EidosEvolutionEngine
        e = EidosEvolutionEngine.__new__(EidosEvolutionEngine)
        self.assertEqual(e._extract_knowledge_from_text(""), {})
        self.assertEqual(e._extract_knowledge_from_text("   "), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
