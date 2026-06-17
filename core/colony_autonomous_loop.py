#!/usr/bin/env python3
"""
EIDOS Colony Autonomous Loop
=============================
Loop autónomo de aprendizaje con Ollama.
Los agentes rotan, cada uno pregunta a Ollama, aprenden y comparten.
Ciclo de 45 segundos entre consultas.
"""

import os
import sys
import time
import json
from core.db import get_conn, get_conn_ctx
import threading
import urllib.request
from typing import Optional, Dict, Any, List

EIDOS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, EIDOS_ROOT)


class ColonyAutonomousLoop:
    """Loop autónomo donde los agentes de Colony consultan Ollama y aprenden."""

    AGENTS = [
        {"id": "colony_coder", "model": "lfm2.5-thinking:1.2b",
         "role": "programador", "topics": ["Python", "algoritmos", "diseño software"]},
        {"id": "colony_analyst", "model": "lfm2.5-thinking:1.2b",
         "role": "analista", "topics": ["datos", "patrones", "estadísticas"]},
        {"id": "colony_vision", "model": "moondream:latest",
         "role": "visión", "topics": ["imágenes", "UI", "diseño visual"]},
        {"id": "colony_operator", "model": "lfm2.5-thinking:1.2b",
         "role": "operador", "topics": ["sistemas", "Linux", "automatización"]},
        {"id": "colony_general", "model": "lfm2.5-thinking:1.2b",
         "role": "generalista", "topics": ["conocimiento general", "ciencia", "historia"]},
    ]

    LEARNING_QUESTIONS = [
        "Explica brevemente qué es {topic} y da un ejemplo práctico",
        "Cuáles son los 3 conceptos más importantes sobre {topic}?",
        "Describe un patrón común en {topic} y cómo evitar errores",
        "Qué herramientas se usan para {topic} en 2025?",
        "Explica la diferencia entre conceptos básicos y avanzados en {topic}",
    ]

    def __init__(self):
        self.db_path = os.path.expanduser("~/.eidos/colony_loop.db")
        self._init_db()
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.cycle_interval = 45  # segundos entre consultas
        self.total_learnings = 0
        self.current_agent_idx = 0

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with get_conn_ctx(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    agent_id TEXT,
                    model TEXT,
                    question TEXT,
                    response TEXT,
                    knowledge_extracted TEXT
                )
            """)
            conn.commit()

    def _check_ollama(self) -> bool:
        try:
            req = urllib.request.urlopen(
                "http://localhost:11434/api/tags", timeout=3)
            return req.status == 200
        except Exception:
            return False

    def _query_ollama(self, model: str, prompt: str) -> Optional[str]:
        try:
            data = json.dumps({
                "model": model,
                "prompt": prompt,
                "stream": False,
                # [S123] num_predict eliminado (regla de SER: sin límites)
                "options": {}
            }).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=data,
                headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read().decode())
            return result.get("response", "").strip()
        except Exception as e:
            return None

    def _extract_knowledge(self, text: str) -> Dict[str, str]:
        knowledge = {}
        for line in text.split('\n'):
            line = line.strip()
            if ' es ' in line and len(line) < 200:
                parts = line.split(' es ', 1)
                if len(parts) == 2 and len(parts[0].strip()) > 2:
                    knowledge[parts[0].strip()] = parts[1].strip()
        return knowledge

    def _learning_cycle(self):
        agent = self.AGENTS[self.current_agent_idx % len(self.AGENTS)]
        self.current_agent_idx += 1

        topic = agent["topics"][self.total_learnings % len(agent["topics"])]
        question_template = self.LEARNING_QUESTIONS[
            self.total_learnings % len(self.LEARNING_QUESTIONS)]
        question = question_template.format(topic=topic)

        if not self._check_ollama():
            return

        response = self._query_ollama(agent["model"], question)
        if not response:
            return

        extracted = self._extract_knowledge(response)

        with get_conn_ctx(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("""
                INSERT INTO learnings
                (timestamp, agent_id, model, question, response,
                 knowledge_extracted)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (time.time(), agent["id"], agent["model"],
                  question, response[:2000],
                  json.dumps(extracted)))
            conn.commit()

        self.total_learnings += 1

        # Compartir con Evolution Engine
        try:
            from core.eidos_evolution_engine import get_evolution_engine
            evolution = get_evolution_engine()
            evolution.learn_from_ollama(
                agent["model"], question, response)
        except Exception:
            pass  # error no crítico, continuar
        if self.total_learnings % 5 == 0:
            print(f"   🧠 Colony Loop: {self.total_learnings} aprendizajes"
                  f" (último: {agent['id']} → {topic})")

    def _run_loop(self, duration_minutes: int = 10080):
        end_time = time.time() + (duration_minutes * 60)
        while self.running and time.time() < end_time:
            try:
                self._learning_cycle()
            except Exception:
                pass  # error no crítico, continuar
            time.sleep(self.cycle_interval)

    def start_background(self, duration_minutes: int = 10080):
        self.running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(duration_minutes,),
            daemon=True,
            name="ColonyAutonomousLoop"
        )
        self._thread.start()

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_stats(self) -> Dict[str, Any]:
        try:
            with get_conn_ctx(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=30000")
                count = conn.execute(
                    "SELECT COUNT(*) FROM learnings").fetchone()[0]
                return {"total_learnings": count, "running": self.running}
        except Exception:
            return {"total_learnings": 0, "running": self.running}


def get_colony_autonomous_loop() -> ColonyAutonomousLoop:
    return ColonyAutonomousLoop()


if __name__ == "__main__":
    loop = ColonyAutonomousLoop()
    print("🧠 Colony Autonomous Loop - Test")
    print("   Iniciando ciclo de aprendizaje...")
    loop.start_background(duration_minutes=5)
    time.sleep(60)
    loop.stop()
    print(f"   Stats: {loop.get_stats()}")
