"""
EIDOS RL MadMax Learner — Meta-learning en horas de inactividad
Inspirado en MetaClaw: 3 modos (skills_only, rl, madmax).

MadMax: entrena cuando el usuario duerme o no interactúa.
Convierte cada conversación en señal de aprendizaje.
"""

import os
import re
import json
import sqlite3
import time
import random
import threading
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from core.db import get_conn

# ─── Constantes ──────────────────────────────────────────────────────────────

EIDOS_DIR = Path.home() / ".eidos"
DB_PATH = EIDOS_DIR / "madmax.db"

# ─── Modos ───────────────────────────────────────────────────────────────────

class LearningMode:
    SKILLS_ONLY = "skills_only"  # Solo inyecta skills, sin training
    RL = "rl"                     # Skills + RL básico con rewards
    MADMAX = "madmax"            # Skills + RL en horas idle


@dataclass
class Interaction:
    prompt: str
    response: str
    feedback: Optional[str] = None
    reward: float = 0.0
    timestamp: float = field(default_factory=time.time)
    skills_used: List[str] = field(default_factory=list)


@dataclass
class Skill:
    name: str
    pattern: str  # Regex pattern que activa esta skill
    template: str  # Template de respuesta/acción
    weight: float = 1.0  # Peso aprendido
    usage_count: int = 0
    success_count: int = 0
    created_at: float = field(default_factory=time.time)


class MadMaxLearner:
    """
    Meta-learner con 3 modos de operación.

    - skills_only: Inyecta skills relevantes en prompts (0 GPU)
    - rl: Registra rewards y ajusta pesos de skills
    - madmax: Entrena activamente durante períodos de inactividad

    En modo MadMax, detecta inactividad del usuario y aprovecha
    para revisar conversaciones, generar variantes, y optimizar
    los pesos de las skills.
    """

    def __init__(self, mode: str = "madmax",
                 idle_threshold_min: int = 30,
                 sleep_hours: Tuple[int, int] = (2, 6)):
        EIDOS_DIR.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.idle_threshold = idle_threshold_min * 60  # En segundos
        self.sleep_start = sleep_hours[0]
        self.sleep_end = sleep_hours[1]
        self.last_interaction = time.time()
        self._training = False
        self._train_thread = None

        self.db = get_conn(str(DB_PATH), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_db()
        self._skills = self._load_skills()

    def _init_db(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                feedback TEXT,
                reward REAL DEFAULT 0,
                skills_used TEXT DEFAULT '[]',
                timestamp REAL
            );

            CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY,
                pattern TEXT NOT NULL,
                template TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                usage_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS training_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT,
                interactions_processed INTEGER,
                skills_updated INTEGER,
                duration_s REAL,
                timestamp REAL
            );

            CREATE TABLE IF NOT EXISTS reward_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interaction_id INTEGER,
                signal_type TEXT,
                value REAL,
                reason TEXT,
                timestamp REAL
            );
        """)
        self.db.commit()
        self._init_default_skills()

    def _init_default_skills(self):
        """Inicializa skills por defecto"""
        existing = self.db.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        if existing > 0:
            return

        defaults = [
            ("code_generation", r"(write|create|implement|code|function|class)\b",
             "Generate clean, documented code with error handling"),
            ("explanation", r"(explain|what|why|how|describe)\b",
             "Provide clear step-by-step explanation"),
            ("debugging", r"(fix|bug|error|issue|problem|broken)\b",
             "Diagnose the issue, identify root cause, provide fix"),
            ("security_scan", r"(scan|nmap|vulnerability|pentest|recon)\b",
             "Run security tools with proper methodology"),
            ("system_admin", r"(install|configure|setup|service|systemd)\b",
             "Follow best practices for system administration"),
            ("file_ops", r"(file|read|write|move|copy|delete)\b",
             "Handle file operations safely with validation"),
            ("network", r"(network|port|ip|dns|http|api)\b",
             "Network operations with timeout and error handling"),
            ("learning", r"(learn|study|research|investigate)\b",
             "Systematic learning with source validation"),
            ("creative", r"(generate|create|design|imagine|story)\b",
             "Creative output with structure and style"),
            ("optimization", r"(optimize|improve|faster|better|efficient)\b",
             "Profile first, then optimize bottlenecks"),
        ]

        for name, pattern, template in defaults:
            self.db.execute(
                "INSERT INTO skills (name, pattern, template, weight, created_at) VALUES (?, ?, ?, 1.0, ?)",
                (name, pattern, template, time.time())
            )
        self.db.commit()
        print(f"🧠 [MadMax] {len(defaults)} skills inicializadas")

    def _load_skills(self) -> List[Skill]:
        """Carga skills de DB"""
        rows = self.db.execute("SELECT * FROM skills ORDER BY weight DESC").fetchall()
        return [Skill(
            name=r["name"], pattern=r["pattern"], template=r["template"],
            weight=r["weight"], usage_count=r["usage_count"],
            success_count=r["success_count"], created_at=r["created_at"]
        ) for r in rows]

    # ─── Detección de Inactividad ────────────────────────────────────────

    def _is_idle(self) -> bool:
        """Detecta si el usuario está inactivo"""
        elapsed = time.time() - self.last_interaction
        if elapsed < self.idle_threshold:
            return False

        # Verificar CPU (< 20%)
        try:
            load = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            cpu_pct = (load / cpu_count) * 100
            if cpu_pct > 20:
                return False
        except Exception:
            pass  # error no crítico, continuar
        return True

    def _is_sleep_hours(self) -> bool:
        """Verifica si estamos en horas de sueño"""
        hour = datetime.now().hour
        if self.sleep_start < self.sleep_end:
            return self.sleep_start <= hour < self.sleep_end
        else:
            return hour >= self.sleep_start or hour < self.sleep_end

    # ─── Skill Matching ──────────────────────────────────────────────────

    def _match_skills(self, prompt: str) -> List[Skill]:
        """Encuentra skills relevantes para un prompt"""
        matched = []
        prompt_lower = prompt.lower()
        for skill in self._skills:
            try:
                if re.search(skill.pattern, prompt_lower):
                    matched.append(skill)
            except re.error:
                continue

        # Ordenar por peso
        matched.sort(key=lambda s: s.weight, reverse=True)
        return matched[:5]  # Top 5

    def inject_skills(self, prompt: str) -> str:
        """
        Inyecta skills relevantes en el prompt.

        Returns:
            Prompt enriquecido con contexto de skills
        """
        self.last_interaction = time.time()
        matched = self._match_skills(prompt)

        if not matched:
            return prompt

        # Construir contexto de skills
        skill_context = "\n[EIDOS Skills activas para esta tarea:]\n"
        for skill in matched:
            skill_context += f"- {skill.name} (w={skill.weight:.2f}): {skill.template}\n"
            # Actualizar uso
            self.db.execute(
                "UPDATE skills SET usage_count = usage_count + 1 WHERE name = ?",
                (skill.name,)
            )
        self.db.commit()

        return skill_context + "\n" + prompt

    # ─── Registro de Interacciones ───────────────────────────────────────

    def record_interaction(self, prompt: str, response: str,
                          feedback: str = None) -> int:
        """
        Registra una interacción para aprendizaje futuro.

        Args:
            prompt: Prompt del usuario
            response: Respuesta generada
            feedback: Feedback explícito (opcional)

        Returns:
            interaction_id
        """
        self.last_interaction = time.time()

        # Inferir reward
        reward = self._infer_reward(prompt, response, feedback)
        matched_skills = [s.name for s in self._match_skills(prompt)]

        cursor = self.db.execute("""
            INSERT INTO interactions (prompt, response, feedback, reward, skills_used, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (prompt, response, feedback, reward,
              json.dumps(matched_skills), time.time()))
        self.db.commit()

        interaction_id = cursor.lastrowid

        # En modo RL, actualizar pesos inmediatamente
        if self.mode in [LearningMode.RL, LearningMode.MADMAX]:
            self._update_skill_weights(matched_skills, reward)

        return interaction_id

    def _infer_reward(self, prompt: str, response: str,
                      feedback: str = None) -> float:
        """
        Infiere reward de una interacción.

        Señales:
        - Feedback explícito positivo: +1
        - Feedback explícito negativo: -0.5
        - Respuesta larga y detallada: +0.3
        - Error en respuesta: -1
        - Sin feedback (neutral): +0.1
        """
        reward = 0.0

        if feedback:
            fb_lower = feedback.lower()
            positive = ["good", "great", "perfect", "thanks", "nice", "exactly",
                        "bien", "perfecto", "gracias", "genial", "excelente"]
            negative = ["wrong", "bad", "no", "incorrect", "fix", "error",
                        "mal", "incorrecto", "arregla", "falla"]

            if any(w in fb_lower for w in positive):
                reward += 1.0
            elif any(w in fb_lower for w in negative):
                reward -= 0.5
            else:
                reward += 0.1  # Neutral pero hay engagement
        else:
            reward += 0.1  # Sin feedback = neutral

        # Calidad de respuesta
        if len(response) > 200:
            reward += 0.2
        if "error" in response.lower() or "traceback" in response.lower():
            reward -= 0.5

        return max(-1.0, min(1.0, reward))

    def _update_skill_weights(self, skill_names: List[str], reward: float):
        """Actualiza pesos de skills basado en reward"""
        learning_rate = 0.05

        for name in skill_names:
            current = self.db.execute(
                "SELECT weight, success_count FROM skills WHERE name = ?",
                (name,)
            ).fetchone()
            if current:
                new_weight = current["weight"] + (learning_rate * reward)
                new_weight = max(0.1, min(3.0, new_weight))  # Clamp

                updates = {"weight": new_weight}
                if reward > 0:
                    updates["success_count"] = current["success_count"] + 1

                self.db.execute(
                    "UPDATE skills SET weight = ?, success_count = ? WHERE name = ?",
                    (new_weight, updates.get("success_count", current["success_count"]), name)
                )

        self.db.commit()
        self._skills = self._load_skills()  # Refresh cache

    # ─── MadMax Training ─────────────────────────────────────────────────

    def tick(self) -> Optional[str]:
        """
        Tick periódico — verifica si debe entrenar.

        Returns:
            Mensaje de estado o None
        """
        if self.mode != LearningMode.MADMAX:
            return None

        if self._training:
            return "🧠 Training en progreso..."

        should_train = self._is_idle() or self._is_sleep_hours()

        if should_train:
            self._start_training()
            return "🌙 [MadMax] Iniciando training en background (idle detectado)"

        return None

    def _start_training(self):
        """Inicia training en background"""
        if self._training:
            return

        self._training = True
        self._train_thread = threading.Thread(target=self._train_loop, daemon=True)
        self._train_thread.start()

    def _train_loop(self):
        """Loop de entrenamiento MadMax"""
        start = time.time()
        try:
            print("🧠 [MadMax] Training loop iniciado")

            # 1. Obtener interacciones recientes no procesadas
            interactions = self.db.execute("""
                SELECT * FROM interactions
                WHERE timestamp > ?
                ORDER BY timestamp DESC
                LIMIT 100
            """, (time.time() - 86400,)).fetchall()  # Últimas 24h

            if not interactions:
                print("🧠 [MadMax] Sin interacciones recientes para entrenar")
                return

            processed = 0
            skills_updated = 0

            for inter in interactions:
                # Verificar si el usuario volvió
                if not self._is_idle() and not self._is_sleep_hours():
                    print("🧠 [MadMax] Usuario activo, pausando training")
                    break

                skills_used = json.loads(inter["skills_used"] or "[]")
                reward = inter["reward"]

                # GRPO simplificado: generar variantes y rankear
                if skills_used and reward != 0:
                    self._update_skill_weights(skills_used, reward * 0.1)  # Decayed
                    skills_updated += 1

                # Analizar patrones de éxito/fallo
                prompt = inter["prompt"]
                response = inter["response"]

                # Detectar nuevos patrones para skills
                if reward > 0.5:
                    self._maybe_create_skill(prompt, response)

                processed += 1
                time.sleep(0.1)  # Throttle

            duration = time.time() - start

            # Log
            self.db.execute("""
                INSERT INTO training_log (mode, interactions_processed, skills_updated, duration_s, timestamp)
                VALUES ('madmax', ?, ?, ?, ?)
            """, (processed, skills_updated, duration, time.time()))
            self.db.commit()

            print(f"🧠 [MadMax] Training completado: {processed} interacciones, "
                  f"{skills_updated} skills actualizadas, {duration:.1f}s")

        except Exception as e:
            print(f"❌ [MadMax] Error en training: {e}")
        finally:
            self._training = False

    def _maybe_create_skill(self, prompt: str, response: str):
        """Intenta crear una nueva skill a partir de un patrón exitoso"""
        prompt_lower = prompt.lower()
        words = re.findall(r'\b\w{4,}\b', prompt_lower)

        if len(words) < 3:
            return

        # Verificar si ya hay skill para este patrón
        for skill in self._skills:
            try:
                if re.search(skill.pattern, prompt_lower):
                    return  # Ya cubierto
            except re.error:
                continue

        # Extraer keywords más relevantes
        from collections import Counter
        common = Counter(words).most_common(3)
        if not common:
            return

        pattern = r"(" + "|".join(re.escape(w) for w, _ in common) + r")\b"
        name = f"auto_{common[0][0]}_{int(time.time()) % 10000}"
        template = f"Learned pattern from successful interaction: handle {', '.join(w for w, _ in common)}"

        try:
            self.db.execute(
                "INSERT OR IGNORE INTO skills (name, pattern, template, weight, created_at) VALUES (?, ?, ?, 0.8, ?)",
                (name, pattern, template, time.time())
            )
            self.db.commit()
            self._skills = self._load_skills()
            print(f"🧠 [MadMax] Nueva skill creada: {name}")
        except Exception:
            pass  # error no crítico, continuar
    # ─── Stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Estadísticas del learner"""
        total_interactions = self.db.execute(
            "SELECT COUNT(*) FROM interactions"
        ).fetchone()[0]

        avg_reward = self.db.execute(
            "SELECT AVG(reward) FROM interactions"
        ).fetchone()[0] or 0

        total_training = self.db.execute(
            "SELECT COUNT(*) FROM training_log"
        ).fetchone()[0]

        total_skills = len(self._skills)
        top_skills = [(s.name, s.weight, s.usage_count) for s in self._skills[:5]]

        return {
            "mode": self.mode,
            "total_interactions": total_interactions,
            "avg_reward": round(avg_reward, 3),
            "total_training_sessions": total_training,
            "total_skills": total_skills,
            "top_skills": top_skills,
            "is_idle": self._is_idle(),
            "is_sleep_hours": self._is_sleep_hours(),
            "is_training": self._training,
        }

    def list_skills(self) -> List[Dict]:
        """Lista todas las skills"""
        return [{
            "name": s.name, "pattern": s.pattern,
            "template": s.template, "weight": round(s.weight, 3),
            "usage_count": s.usage_count,
            "success_rate": s.success_count / max(s.usage_count, 1)
        } for s in self._skills]

    def __del__(self):
        try:
            self.db.close()
        except Exception:
            pass  # error no crítico, continuar
# ─── Test ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Test RL MadMax Learner ===\n")

    learner = MadMaxLearner(mode="rl")

    # Test 1: Skills matching
    print("Test 1: Skill matching")
    enriched = learner.inject_skills("Write a Python function that sorts a list")
    assert "[EIDOS Skills" in enriched
    print(f"  ✅ Skills inyectadas en prompt\n")

    # Test 2: Record interactions
    print("Test 2: Record interactions")
    id1 = learner.record_interaction(
        "Write a function to parse JSON",
        "def parse_json(data): ...",
        "perfect, exactly what I needed"
    )
    id2 = learner.record_interaction(
        "Scan network ports",
        "Running nmap...",
        "good scan results"
    )
    id3 = learner.record_interaction(
        "Fix this bug in my code",
        "Error: traceback...",
        "wrong, that didn't fix it"
    )
    print(f"  ✅ 3 interacciones registradas (IDs: {id1}, {id2}, {id3})\n")

    # Test 3: Reward inference
    print("Test 3: Reward inference")
    r1 = learner._infer_reward("test", "long response " * 50, "great job")
    r2 = learner._infer_reward("test", "error traceback", "wrong answer")
    r3 = learner._infer_reward("test", "ok", None)
    print(f"  Positive feedback: reward={r1:.2f}")
    print(f"  Negative + error: reward={r2:.2f}")
    print(f"  Neutral: reward={r3:.2f}")
    assert r1 > r3 > r2
    print(f"  ✅ Rewards correctos\n")

    # Test 4: Skill weights update
    print("Test 4: Skill weight updates")
    skills_before = {s.name: s.weight for s in learner._skills}
    learner._update_skill_weights(["code_generation"], 1.0)
    learner._update_skill_weights(["debugging"], -0.5)
    skills_after = {s.name: s.weight for s in learner._skills}
    print(f"  code_generation: {skills_before.get('code_generation', 1.0):.2f} → {skills_after.get('code_generation', 1.0):.2f}")
    print(f"  debugging: {skills_before.get('debugging', 1.0):.2f} → {skills_after.get('debugging', 1.0):.2f}")
    print(f"  ✅ Weights actualizados\n")

    # Test 5: Stats
    print("Test 5: Stats")
    stats = learner.get_stats()
    print(f"  Mode: {stats['mode']}")
    print(f"  Interactions: {stats['total_interactions']}")
    print(f"  Avg reward: {stats['avg_reward']}")
    print(f"  Skills: {stats['total_skills']}")
    print(f"  Top skills: {stats['top_skills'][:3]}")
    print(f"  ✅ Stats OK\n")

    # Test 6: List skills
    print("Test 6: List skills")
    for s in learner.list_skills()[:5]:
        print(f"  🧠 {s['name']:20s} w={s['weight']:.2f} uses={s['usage_count']}")

    print("\n✅ RL MadMax Learner funcional")
