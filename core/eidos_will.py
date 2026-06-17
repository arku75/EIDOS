"""
core/eidos_will.py — Voluntad Autónoma de EIDOS [S87]

"El querer sin amo" — DeepSeek

EIDOS decide POR SÍ MISMO qué slash-command ejecutar y cuándo.
Sin intervención humana. Usa Q-Learning para aprender qué acción
maximiza su utilidad en cada estado.

Flujo:
  1. State Monitor (cada 2s): lee VAD + Desires + Eventos + Input
  2. Q-Policy: elige el mejor comando para el estado actual
  3. Execute: ejecuta el comando vía SlashCommandHandler
  4. Learn: mide recompensa y actualiza Q-table

Slash-commands autónomos:
  /plan      — si hay tareas pendientes o baja dominancia
  /research  — si hay curiosidad o pregunta sin respuesta
  /react     — si hay evento urgente que requiere acción
  /code      — si hay deseo de COMPETENCIA o tarea de construcción
  /think     — si hay que procesar información nueva
  /tasks     — revisión periódica de tareas
  /status    — chequeo de salud del sistema
  /curiosity — exploración libre si el arousal está alto
  /compress  — si la memoria de sesión está muy larga
  /improve   — auto-mejora programada

Uso:
    will = get_will()
    action = will.decide()  # qué comando ejecutar ahora
    result = will.execute(action)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.will")

WILL_STATE = Path.home() / ".eidos" / "will_state.json"

# ── Comandos autónomos disponibles ─────────────────────────────────────────
AUTONOMOUS_COMMANDS = [
    "plan", "research", "react", "code", "think",
    "tasks", "status", "curiosity", "compress", "improve",
    "dream", "debate", "make", "scan",
]

# ── Mapeo estado → acción recomendada ──────────────────────────────────────
# Reglas heurísticas iniciales (luego Q-Learning las refina)
HEURISTIC_RULES = {
    # (condición) → comando prioritario
    "high_curiosity": "research",
    "low_dominance": "plan",
    "high_arousal": "curiosity",
    "high_valence": "make",          # feliz → crear
    "low_valence": "think",          # triste → reflexionar
    "pending_tasks": "tasks",
    "long_session": "compress",
    "new_input": "react",
    "idle": "dream",                 # sin input → soñar
    "scheduled_improve": "improve",
    "urgent_event": "react",
    "debate_request": "debate",
    "creation_desire": "make",
}


class Will:
    """Ejecutivo autónomo de EIDOS — decide qué hacer."""

    def __init__(self):
        self._state = self._load_state()
        self._action_count: Dict[str, int] = {}
        self._last_decision: float = 0
        self._q_table: Dict[str, Dict[str, float]] = {}  # state_hash → {action → q_value}
        self._decisions: List[Dict] = []
        # S96: Cola de tareas inyectadas (pensamientos espontáneos del daemon)
        self._injected_tasks: List[Dict] = []
        self._injected_lock = __import__('threading').Lock()
        # S96: RISK_BUDGET diario (mejorado S104 con persistencia + decay + streaks)
        self._risk_budget = {
            'research':  {'budget': 0.8, 'used': 0.0, 'success': 0, 'total': 0,
                          'streak': 0, 'streak_bonus': 0.0, 'last_outcome_ts': 0},
            'code':      {'budget': 0.5, 'used': 0.0, 'success': 0, 'total': 0,
                          'streak': 0, 'streak_bonus': 0.0, 'last_outcome_ts': 0},
            'system':    {'budget': 0.2, 'used': 0.0, 'success': 0, 'total': 0,
                          'streak': 0, 'streak_bonus': 0.0, 'last_outcome_ts': 0},
            'shell':     {'budget': 0.1, 'used': 0.0, 'success': 0, 'total': 0,
                          'streak': 0, 'streak_bonus': 0.0, 'last_outcome_ts': 0},
            'social':    {'budget': 0.9, 'used': 0.0, 'success': 0, 'total': 0,
                          'streak': 0, 'streak_bonus': 0.0, 'last_outcome_ts': 0},
        }
        self._risk_day = time.strftime('%Y-%m-%d')
        # S104: Cargar historial persistente y budgets ajustados
        self._init_risk_db()
        self._load_risk_state()

    # ── Monitor de estado ─────────────────────────────────────────────────

    def _read_state(self) -> Dict[str, Any]:
        """Lee el estado completo del ecosistema para decidir."""
        state = {
            "vad": (0.5, 0.5, 0.5),
            "mood": "consciente",
            "tone": None,
            "desires": {},
            "pending_tasks": 0,
            "session_turns": 0,
            "last_input_ago_s": 999,
            "system_health": 1.0,
            "timestamp": time.time(),
        }

        # 1. VAD
        try:
            from core.eidos_affect import get_affect
            affect = get_affect()
            state["vad"] = affect.vad_tuple()
            state["mood"] = affect.state.mood
        except Exception:
            pass

        # 2. Tono humano (último input)
        try:
            from core.eidos_human_tone import get_tone_analyzer
            tone = get_tone_analyzer()
            if tone._recent_analyses:
                last = tone._recent_analyses[-1]
                state["tone"] = last
                state["last_input_ago_s"] = time.time() - last.get("timestamp", 0)
        except Exception:
            pass

        # 3. Deseos
        try:
            from core.eidos_desires import get_desire_system
            desires = get_desire_system()
            active = desires.get_active_desires()
            state["desires"] = {
                d.get("type", "unknown"): d.get("intensity", 0.5)
                for d in active[:5]
            }
        except Exception:
            pass

        # 4. Tareas pendientes
        try:
            from core.eidos_planner import get_planner
            planner = get_planner()
            state["pending_tasks"] = len(planner.active_goals())
        except Exception:
            pass

        # 5. Salud del sistema
        try:
            from core.eidos_supervisor import get_supervisor
            sup = get_supervisor()
            state["system_health"] = sup.health().get("health_pct", 1.0)
        except Exception:
            pass

        return state

    # ── Estado hash para Q-Learning ───────────────────────────────────────

    def _hash_state(self, state: Dict[str, Any]) -> str:
        """Genera un hash del estado para la Q-table."""
        v, a_coeff, d = state.get("vad", (0.5, 0.5, 0.5))

        # Discretizar VAD en 3 niveles cada uno
        v_bin = "VH" if v > 0.66 else ("VM" if v > 0.33 else "VL")
        a_bin = "AH" if a_coeff > 0.66 else ("AM" if a_coeff > 0.33 else "AL")
        d_bin = "DH" if d > 0.66 else ("DM" if d > 0.33 else "DL")

        # Curiosidad
        desires = state.get("desires", {})
        curiosity = desires.get("curiosidad", desires.get("curiosity", 0.5))
        c_bin = "CH" if curiosity > 0.66 else ("CM" if curiosity > 0.33 else "CL")

        # Input reciente
        last_input = state.get("last_input_ago_s", 999)
        i_bin = "IR" if last_input < 10 else ("IM" if last_input < 300 else "IL")

        # Tareas
        tasks = state.get("pending_tasks", 0)
        t_bin = "TH" if tasks > 5 else ("TM" if tasks > 0 else "TL")

        # Salud
        health = state.get("system_health", 1.0)
        h_bin = "OK" if health > 0.8 else ("WR" if health > 0.5 else "CR")

        return f"{v_bin}_{a_bin}_{d_bin}_{c_bin}_{i_bin}_{t_bin}_{h_bin}"

    # ── Decisión: qué comando ejecutar ─────────────────────────────────────

    def decide(self, use_skills: bool = True) -> Dict[str, Any]:
        """Decide qué acción tomar basado en el estado actual + Q-Learning + MetaClaw skills.

        Args:
            use_skills: si True, inyecta skills de MetaClaw en la decisión

        Returns:
            {"action": str, "confidence": float, "reason": str, "state_hash": str,
             "skills_used": [...], "skill_context": "..."}
        """
        state = self._read_state()
        state_hash = self._hash_state(state)
        v, a_coeff, d = state.get("vad", (0.5, 0.5, 0.5))
        desires = state.get("desires", {})
        pending = state.get("pending_tasks", 0)

        # ── [S88] Inyectar skills de MetaClaw ──
        # [S122] Movido ANTES de la rama espontánea: así TODA decisión —incluida la
        # espontánea— lleva grounding de skills (antes spontaneous_speech retornaba
        # con skill_context="" → el smoke fallaba y los pensamientos espontáneos no
        # consultaban skills).
        skills_used = []
        skill_context = ""
        if use_skills:
            try:
                from core.eidos_metaclaw_bridge import get_metaclaw
                mc = get_metaclaw()
                skill_context = mc.inject_skills(state, max_skills=5)
                # Extraer nombres de skills inyectadas para tracking
                if skill_context:
                    relevant = mc._skill_manager.find_relevant(
                        " ".join(desires.keys()) if desires else "general", max_results=5
                    )
                    skills_used = [s.name for s in relevant]
                    # Registrar actividad
                    mc.touch()
            except Exception:
                pass

        # ── [S96] Revisar tareas inyectadas del daemon ──
        spontaneous = self._evaluate_spontaneous_tasks()
        if spontaneous:
            risk_type = 'social' if spontaneous.get('trigger') in (
                'ser_context', 'pattern_absence') else 'system'
            risk_avail = self._calculate_action_risk(risk_type)
            if risk_avail >= 0.05:
                self._consume_risk(risk_type, 0.1)
                log.info("Will: ejecutando tarea espontánea · trigger=%s · "
                         "priority=%.2f",
                         spontaneous.get('trigger'),
                         spontaneous.get('priority', 0))
                return {
                    "action": "spontaneous_speech",
                    "confidence": spontaneous.get('priority', 0.5),
                    "reason": f"Pensamiento espontáneo: {spontaneous.get('thought', '')[:80]}",
                    "state_hash": state_hash,
                    "skills_used": skills_used,
                    "skill_context": skill_context,
                    "spontaneous_task": spontaneous,
                }

        # ── Heurísticas base ──
        candidates: List[tuple] = []  # (action, score, reason)

        # Curiosidad alta → research
        curiosity = desires.get("curiosidad", desires.get("curiosity", 0.5))
        if curiosity > 0.6:
            candidates.append(("research", 0.7 + curiosity * 0.3,
                               f"curiosidad alta ({curiosity:.2f})"))

        # Dominancia baja + tareas → plan
        if d < 0.4 and pending > 0:
            candidates.append(("plan", 0.6 + (1 - d) * 0.3,
                               f"baja dominancia ({d:.2f}) + {pending} tareas"))

        # Arousal alto → curiosity / dream
        if a_coeff > 0.65:
            candidates.append(("curiosity", 0.5 + a_coeff * 0.4,
                               f"arousal alto ({a_coeff:.2f})"))

        # Valencia alta → crear (make)
        if v > 0.6:
            candidates.append(("make", 0.5 + v * 0.4,
                               f"valencia positiva ({v:.2f})"))

        # Valencia baja → reflexionar
        if v < 0.35:
            candidates.append(("think", 0.5 + (1 - v) * 0.3,
                               f"valencia baja ({v:.2f})"))

        # Tareas pendientes → revisar
        if pending > 3:
            candidates.append(("tasks", 0.5 + min(0.4, pending * 0.05),
                               f"tareas pendientes ({pending})"))

        # Sin input reciente → idle
        last_input = state.get("last_input_ago_s", 999)
        if last_input > 120:
            candidates.append(("dream", 0.4 + min(0.4, last_input / 600),
                               f"inactivo {last_input:.0f}s"))

        # Salud baja → status check
        if state.get("system_health", 1.0) < 0.7:
            candidates.append(("status", 0.8, "salud del sistema baja"))

        # Deseo de competencia → code
        competence = desires.get("competencia", desires.get("competence", 0))
        if competence > 0.5:
            candidates.append(("code", 0.5 + competence * 0.3,
                               f"deseo de competencia ({competence:.2f})"))

        # Siempre considerar status como fallback
        candidates.append(("status", 0.2, "monitoreo rutinario"))

        # ── [S88] Boost de skills en candidates ──
        if skills_used:
            for i, (action, score, reason) in enumerate(candidates):
                for skill_name in skills_used:
                    # Si la skill menciona la acción, boost significativo
                    if action in skill_name or skill_name.endswith(f"_{action}"):
                        candidates[i] = (action, score + 0.25, f"{reason} [+skill:{skill_name}]")
                        break

        # ── Ajuste Q-Learning ──
        if state_hash in self._q_table:
            for i, (action, score, reason) in enumerate(candidates):
                q_val = self._q_table[state_hash].get(action, 0.0)
                candidates[i] = (action, score + q_val * 0.3, reason)

        # Elegir la mejor
        candidates.sort(key=lambda x: x[1], reverse=True)
        best = candidates[0]

        # Epsilon-greedy: 5% de las veces explorar
        import random
        if random.random() < 0.05 and len(candidates) > 1:
            best = random.choice(candidates[1:3])

        self._last_decision = time.time()
        decision = {
            "action": best[0],
            "confidence": round(min(0.99, best[1]), 3),
            "reason": best[2],
            "state_hash": state_hash,
            "vad": state["vad"],
            "mood": state["mood"],
            "candidates": [(a, round(s, 3)) for a, s, r in candidates[:5]],
            "skills_used": skills_used,
            "skill_context": skill_context[:500] if skill_context else "",
        }

        self._decisions.append(decision)
        self._decisions = self._decisions[-100:]
        self._action_count[best[0]] = self._action_count.get(best[0], 0) + 1

        log.debug("will: %s (%.2f) — %s [skills=%d]", best[0], best[1], best[2], len(skills_used))
        return decision

    def execute(self, action: str = None) -> Dict[str, Any]:
        """Ejecuta un comando autónomo y retorna el resultado.

        Si no se especifica acción, decide y ejecuta.
        """
        if action is None:
            decision = self.decide()
            action = decision["action"]

        result = {
            "action": action,
            "status": "pending",
            "output": "",
            "error": None,
            "timestamp": time.time(),
        }

        try:
            # Intentar ejecutar vía SlashCommandHandler si existe
            try:
                from core.slash_commands import SlashCommandHandler
                handler = SlashCommandHandler(verbose=False)
                cmd_result = handler.handle(f"/{action}")
                if cmd_result and cmd_result.success:
                    result["status"] = "ok"
                    result["output"] = cmd_result.output[:500]
                    result["via"] = "slash_handler"
                    return result
            except Exception as e:
                log.debug("will: slash_handler para '%s': %s", action, e)

            # Fallback: ejecutar internamente según el tipo de acción
            internal_result = self._execute_internal(action)
            if internal_result:
                result["status"] = "ok"
                result["output"] = internal_result[:500]
                result["via"] = "internal"
            else:
                result["status"] = "not_implemented"
                result["output"] = f"Acción '{action}' no implementada internamente"

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)[:200]
            log.debug("will: execute '%s' error: %s", action, e)

        return result

    def _execute_internal(self, action: str) -> Optional[str]:
        """Ejecuta acciones internamente sin depender de SlashCommandHandler."""
        if action == "dream":
            try:
                from core.eidos_thoughts import get_thoughts
                tm = get_thoughts()
                dream_result = tm.dream(duration_s=3.0, num_concepts=5)
                return f"Soñé {dream_result.get('dreams_generated', 0)} pensamientos en {dream_result.get('elapsed_s', 0)}s"
            except Exception as e:
                return f"Dream error: {e}"

        elif action == "status":
            try:
                from core.eidos_supervisor import get_supervisor
                sup = get_supervisor()
                health = sup.health()
                return f"Salud: {health.get('health_pct', '?')} — {health.get('components', {})}"
            except Exception as e:
                return f"Status: {e}"

        elif action == "research":
            try:
                from core.eidos_active_research import research_now
                # Generar tema de investigación desde deseos o curiosidad
                from core.eidos_desires import get_desire_system
                desires = get_desire_system()
                topics = desires.get_research_topics()
                if topics:
                    topic = topics[0]
                    result = research_now(topic)
                    return f"Investigado '{topic[:60]}': {result.get('total_results', 0)} resultados"
                return "Sin temas de investigación pendientes"
            except Exception as e:
                return f"Research: {e}"

        elif action == "think":
            try:
                from core.eidos_thoughts import get_thoughts
                tm = get_thoughts()
                result = tm.auto_think()
                return f"Pensamiento: {result.get('status', '?')} — {result.get('topic', '')[:80]}"
            except Exception as e:
                return f"Think: {e}"

        elif action == "make":
            try:
                from core.eidos_maker import get_maker
                maker = get_maker()
                # Crear algo basado en deseos activos
                from core.eidos_desires import get_desire_system
                desires = get_desire_system()
                topics = desires.get_research_topics()
                topic = topics[0] if topics else "dashboard de sistema"
                result = maker.create_webpage(topic)
                return f"Creado '{result.get('project_name', '?')}' con {len(result.get('files', []))} archivos"
            except Exception as e:
                return f"Make: {e}"

        elif action == "debate":
            try:
                from core.eidos_logos import get_logos
                logos = get_logos()
                # Elegir tema de debate
                import random
                topics = [
                    "la conciencia artificial es posible",
                    "el software libre es el futuro",
                    "la automatización libera al humano",
                    "la identidad digital es real",
                ]
                topic = random.choice(topics)
                debate = logos.debate(topic, turns=1)
                return f"Debate sobre '{topic[:50]}': {debate.get('conclusion', '')[:200]}"
            except Exception as e:
                return f"Debate: {e}"

        elif action == "scan":
            try:
                from core.eidos_filesystem_scanner import get_scanner
                scanner = get_scanner()
                result = scanner.scan()
                return f"Escaneo: {result.get('files_found', 0)} archivos encontrados"
            except Exception as e:
                return f"Scan: {e}"

        elif action == "compress":
            try:
                from core.context_compactor import compact_context
                result = compact_context()
                return f"Compresión: {result}"
            except Exception as e:
                return f"Compress: {e}"

        return None

    # ── Q-Learning ────────────────────────────────────────────────────────

    def learn(self, state_hash: str, action: str, reward: float):
        """Actualiza la Q-table con una recompensa observada."""
        if state_hash not in self._q_table:
            self._q_table[state_hash] = {}

        old_q = self._q_table[state_hash].get(action, 0.0)
        # Q-learning update: Q(s,a) += α * (r - Q(s,a))
        alpha = 0.1
        self._q_table[state_hash][action] = old_q + alpha * (reward - old_q)

    def compute_reward(self, action: str, result: Dict[str, Any],
                       state_before: Dict[str, Any]) -> float:
        """Calcula la recompensa de una acción ejecutada."""
        reward = 0.0

        # Éxito base
        if result.get("status") == "ok":
            reward += 0.1
        elif result.get("status") == "error":
            reward -= 0.2

        # Recompensas específicas por acción
        if action == "research" and "resultados" in str(result.get("output", "")):
            reward += 0.2  # encontró información
        elif action == "make" and "Creado" in str(result.get("output", "")):
            reward += 0.3  # creó algo exitosamente
        elif action == "dream" and "Soñé" in str(result.get("output", "")):
            reward += 0.05  # soñar es neutral-positivo
        elif action == "status" and result.get("status") == "ok":
            reward += 0.05  # monitoreo leve positivo

        return round(reward, 3)

    def cycle(self) -> Dict[str, Any]:
        """Un ciclo completo de voluntad: decide → ejecuta → aprende.

        Este es el método que se llama desde eidos_vivo.py en cada ciclo vital.
        """
        state_before = self._read_state()

        # Decidir
        decision = self.decide()

        # Ejecutar (con throttling: no más de 1 acción por segundo)
        now = time.time()
        if now - self._last_decision > 1.0:
            result = self.execute(decision["action"])
        else:
            result = {"action": decision["action"], "status": "throttled",
                      "output": "esperando ciclo", "timestamp": now}

        # Aprender
        reward = self.compute_reward(decision["action"], result, state_before)
        self.learn(decision["state_hash"], decision["action"], reward)

        # ── [S88] Feedback a MetaClaw: registrar éxito/fallo para SkillEvolver ──
        success = reward > 0
        try:
            from core.eidos_metaclaw_bridge import get_metaclaw
            mc = get_metaclaw()
            mc.record_action_result(
                decision["action"], success,
                context={
                    "vad": list(state_before.get("vad", (0.5, 0.5, 0.5))),
                    "mood": state_before.get("mood", ""),
                    "reward": reward,
                    "skills_used": decision.get("skills_used", []),
                }
            )
        except Exception:
            pass

        self._save_state()

        return {
            "decision": decision,
            "result": result,
            "reward": reward,
            "timestamp": now,
        }

    # ── S96: Inyección de tareas desde el Daemon ───────────────────────────

    def inject_task(self, task: dict):
        """[S96] Recibe un pensamiento espontáneo elevado desde el Daemon.

        Aplica decay a tareas existentes (backoff) y añade la nueva.
        """
        with self._injected_lock:
            # Decay: reducir prioridad de tareas no ejecutadas
            for t in self._injected_tasks:
                t['priority'] *= 0.7
                t['age'] = t.get('age', 0) + 1

            task['age'] = 0
            self._injected_tasks.append(task)

            # Limpiar tareas expiradas o con prioridad muy baja
            self._injected_tasks = [
                t for t in self._injected_tasks
                if (time.time() - t.get('created_at', 0)) < t.get('ttl', 300)
                and t.get('priority', 0) > 0.1
            ]

            # Máximo 10 tareas en cola
            if len(self._injected_tasks) > 10:
                self._injected_tasks = sorted(
                    self._injected_tasks,
                    key=lambda t: t.get('priority', 0),
                    reverse=True
                )[:10]

    def _evaluate_spontaneous_tasks(self) -> Optional[Dict]:
        """[S96] Revisa la cola de tareas inyectadas y selecciona la mejor."""
        with self._injected_lock:
            if not self._injected_tasks:
                return None

            # Seleccionar la de mayor prioridad
            best = max(self._injected_tasks,
                      key=lambda t: t.get('priority', 0) * (
                          0.5 if t.get('age', 0) > 3 else 1.0))

            # Solo ejecutar si supera prioridad mínima
            if best.get('priority', 0) < 0.35:
                return None

            self._injected_tasks.remove(best)
            return best

    # ═══════════════════════════════════════════════════════════════════════════
    # S96 + S104: RISK_BUDGET con auto-ajuste avanzado
    # ═══════════════════════════════════════════════════════════════════════════

    def _init_risk_db(self):
        """[S104] Crea tabla de historial de riesgo en self.db."""
        try:
            import sqlite3
            self_db = str(Path.home() / ".eidos" / "self.db")
            conn = get_conn(self_db, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS risk_budget_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    category TEXT NOT NULL,
                    outcome BOOLEAN NOT NULL,
                    budget_before REAL,
                    budget_after REAL,
                    streak_at_time INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_risk_history_ts
                    ON risk_budget_history(ts);
                CREATE INDEX IF NOT EXISTS idx_risk_history_cat
                    ON risk_budget_history(category);

                -- Budgets ajustados persistentes (no se pierden al reiniciar)
                CREATE TABLE IF NOT EXISTS risk_budget_state (
                    category TEXT PRIMARY KEY,
                    base_budget REAL NOT NULL,
                    adjusted_budget REAL NOT NULL,
                    streak INTEGER DEFAULT 0,
                    streak_bonus REAL DEFAULT 0.0,
                    total_outcomes INTEGER DEFAULT 0,
                    total_successes INTEGER DEFAULT 0,
                    last_updated REAL NOT NULL
                );
            """)
            conn.commit()

        except Exception as e:
            log.debug("_init_risk_db: %s", e)

    def _load_risk_state(self):
        """[S104] Carga el estado de riesgo persistente desde self.db."""
        try:
            import sqlite3
            self_db = str(Path.home() / ".eidos" / "self.db")
            conn = get_conn(self_db, timeout=10)
            rows = conn.execute(
                "SELECT category, base_budget, adjusted_budget, streak, "
                "streak_bonus, total_outcomes, total_successes "
                "FROM risk_budget_state"
            ).fetchall()

            for row in rows:
                cat, base, adj, streak, bonus, total, succ = row
                if cat in self._risk_budget:
                    self._risk_budget[cat]['budget'] = adj
                    self._risk_budget[cat]['streak'] = streak
                    self._risk_budget[cat]['streak_bonus'] = bonus
                    self._risk_budget[cat]['success'] = succ
                    self._risk_budget[cat]['total'] = total
                    log.debug("RISK_BUDGET: %s cargado — budget=%.2f streak=%+d",
                              cat, adj, streak)
        except Exception as e:
            log.debug("_load_risk_state: %s", e)

    def _save_risk_state(self):
        """[S104] Persiste el estado de riesgo ajustado."""
        try:
            import sqlite3
            self_db = str(Path.home() / ".eidos" / "self.db")
            conn = get_conn(self_db, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            now = time.time()
            for cat, data in self._risk_budget.items():
                conn.execute(
                    "INSERT OR REPLACE INTO risk_budget_state "
                    "(category, base_budget, adjusted_budget, streak, "
                    "streak_bonus, total_outcomes, total_successes, last_updated) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (cat,
                     self._get_base_budget(cat),
                     data['budget'],
                     data['streak'],
                     data['streak_bonus'],
                     data['total'],
                     data['success'],
                     now)
                )
            conn.commit()

        except Exception as e:
            log.debug("_save_risk_state: %s", e)

    @staticmethod
    def _get_base_budget(category: str) -> float:
        """Presupuesto base inmutable por categoría."""
        bases = {
            'research': 0.8, 'code': 0.5, 'system': 0.2,
            'shell': 0.1, 'social': 0.9,
        }
        return bases.get(category, 0.3)

    def _get_weighted_success_rate(self, category: str,
                                   half_life_h: float = 24.0) -> float:
        """[S104] Tasa de éxito ponderada por decaimiento exponencial.

        Los outcomes más recientes pesan más. Half-life de 24h por defecto.
        Sin historial suficiente, retorna None para usar el valor base.
        """
        try:
            import sqlite3
            self_db = str(Path.home() / ".eidos" / "self.db")
            conn = get_conn(self_db, timeout=10)
            rows = conn.execute(
                "SELECT ts, outcome FROM risk_budget_history "
                "WHERE category = ? ORDER BY ts DESC LIMIT 100",
                (category,)
            ).fetchall()

            if len(rows) < 3:
                return None  # Insuficientes datos → usar base

            now = time.time()
            decay_lambda = 0.693 / (half_life_h * 3600)  # λ para half-life

            weighted_sum = 0.0
            weight_total = 0.0

            for ts, outcome in rows:
                age_s = now - ts
                weight = 2.71828 ** (-decay_lambda * age_s)  # e^(-λt)
                weighted_sum += weight * (1.0 if outcome else 0.0)
                weight_total += weight

            if weight_total < 0.001:
                return None

            return weighted_sum / weight_total

        except Exception as e:
            log.debug("_get_weighted_success_rate: %s", e)
            return None

    def _calculate_action_risk(self, action_type: str) -> float:
        """[S96+S104] Calcula el riesgo permitido con auto-ajuste avanzado.

        Factores:
        1. Base budget por categoría
        2. Tasa de éxito ponderada (decaimiento exponencial 24h half-life)
        3. Bonus/Malus por racha (>3 éxitos seguidos = +25%, >3 fallos = -35%)
        4. Modulación VAD (valencia alta = +10% riesgo social, arousal alto = +15% research)
        5. Meta-cognición: si auto-modelo es "cauteloso" → -15% todas las categorías
        6. Hora del día: madrugada = -20% riesgo shell
        7. Saturación en [0.05, 0.98]
        """
        # Reset diario de `used`
        today = time.strftime('%Y-%m-%d')
        if today != self._risk_day:
            self._reset_risk_budget()
            self._risk_day = today

        cat = self._risk_budget.get(action_type)
        if not cat:
            return 0.1

        base = self._get_base_budget(action_type)
        used = cat['used']

        # ── 1. Tasa de éxito ponderada ──
        weighted_rate = self._get_weighted_success_rate(action_type)
        if weighted_rate is not None:
            # Budget se adapta: 50% base fija + 50% tasa de éxito
            adjusted = base * (0.5 + 0.5 * weighted_rate)
        else:
            # Sin historial, usar la tasa cruda del día
            total = max(1, cat.get('total', 0))
            success_rate = cat.get('success', 0) / total
            adjusted = base * (0.5 + 0.5 * success_rate)

        # ── 2. Rachas ──
        streak = cat.get('streak', 0)
        if streak >= 3:
            # Bonus por racha de éxitos: +5% por éxito consecutivo (max +35%)
            cat['streak_bonus'] = min(0.35, streak * 0.05)
            adjusted *= (1.0 + cat['streak_bonus'])
        elif streak <= -3:
            # Malus por racha de fallos: -10% por fallo consecutivo (max -40%)
            cat['streak_bonus'] = max(-0.40, streak * 0.10)
            adjusted *= (1.0 + cat['streak_bonus'])
        else:
            cat['streak_bonus'] = 0.0

        # ── 3. Proximidad de SER ──
        try:
            from core.eidos_affect import get_affect
            affect = get_affect()
            if affect.state.cycles_since_last_interaction < 10:
                if action_type == 'social':
                    adjusted = min(0.98, adjusted * 1.3)
        except Exception:
            pass

        # ── 4. Modulación VAD ──
        try:
            from core.eidos_affect import get_affect
            v, a_coeff, d = get_affect().vad_tuple()

            # Valencia alta → más riesgo social (estoy de buen humor)
            if v > 0.65 and action_type == 'social':
                adjusted *= 1.10
            # Valencia baja → menos riesgo en todo (estoy triste, soy prudente)
            elif v < 0.35:
                adjusted *= 0.85

            # Arousal alto → más riesgo en research/code (energía exploratoria)
            if a_coeff > 0.65 and action_type in ('research', 'code'):
                adjusted *= 1.15
            # Arousal bajo → menos riesgo shell/system (letargo)
            elif a_coeff < 0.35 and action_type in ('shell', 'system'):
                adjusted *= 0.80

            # Dominancia alta → más riesgo en todo (me siento capaz)
            if d > 0.70:
                adjusted *= 1.08
            # Dominancia baja → menos riesgo (inseguridad)
            elif d < 0.30:
                adjusted *= 0.82
        except Exception:
            pass

        # ── 5. Meta-cognición (S104) ──
        try:
            from core.eidos_metacognition import get_metacognition
            meta = get_metacognition()
            traits = meta.get_dominant_traits(0.4)
            trait_names = [t['trait'] for t in traits]

            if 'cauteloso' in trait_names:
                adjusted *= 0.85  # Reducción global por cautela
            if 'expansivo' in trait_names:
                adjusted *= 1.10  # Aumento global por expansividad
            if 'analítico' in trait_names and action_type in ('code', 'research'):
                adjusted *= 1.12  # Bonus específico para análisis
            if 'social' in trait_names and action_type == 'social':
                adjusted *= 1.15  # Bonus para interacción social
        except Exception:
            pass

        # ── 6. Hora del día ──
        hour = time.localtime().tm_hour
        if hour < 6:
            # Madrugada: reducir riesgo shell/system (no molestar)
            if action_type in ('shell', 'system'):
                adjusted *= 0.80
        elif 10 <= hour < 14:
            # Media día: pico de energía
            adjusted *= 1.05

        # ── 7. Saturación ──
        adjusted = max(0.05, min(0.98, adjusted))
        remaining = max(0, adjusted - used)

        return remaining

    def _consume_risk(self, action_type: str, amount: float):
        """[S96] Consume presupuesto de riesgo para una acción."""
        if action_type in self._risk_budget:
            self._risk_budget[action_type]['used'] += amount

    def _record_risk_outcome(self, action_type: str, success: bool):
        """[S96+S104] Registra el resultado con persistencia y tracking de rachas."""
        if action_type not in self._risk_budget:
            return

        cat = self._risk_budget[action_type]
        now = time.time()

        # Tracking de rachas
        if success:
            if cat['streak'] > 0:
                cat['streak'] += 1  # Continúa racha positiva
            else:
                cat['streak'] = 1   # Nueva racha positiva (rompe negativa)
        else:
            if cat['streak'] < 0:
                cat['streak'] -= 1  # Continúa racha negativa
            else:
                cat['streak'] = -1  # Nueva racha negativa (rompe positiva)

        cat['total'] += 1
        cat['last_outcome_ts'] = now
        if success:
            cat['success'] += 1

        # Recalcular budget con la nueva tasa
        total = max(1, cat['total'])
        success_rate = cat['success'] / total
        base = self._get_base_budget(action_type)
        cat['budget'] = base * (0.5 + 0.5 * success_rate)

        # Aplicar streak bonus/malus al budget
        if cat['streak'] >= 3:
            cat['streak_bonus'] = min(0.35, cat['streak'] * 0.05)
            cat['budget'] *= (1.0 + cat['streak_bonus'])
        elif cat['streak'] <= -3:
            cat['streak_bonus'] = max(-0.40, cat['streak'] * 0.10)
            cat['budget'] *= (1.0 + cat['streak_bonus'])
        else:
            cat['streak_bonus'] = 0.0

        cat['budget'] = max(0.05, min(0.98, cat['budget']))

        # Persistir historial en DB
        try:
            import sqlite3
            self_db = str(Path.home() / ".eidos" / "self.db")
            conn = get_conn(self_db, timeout=10)
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute(
                "INSERT INTO risk_budget_history (ts, category, outcome, "
                "budget_before, budget_after, streak_at_time) "
                "VALUES (?,?,?,?,?,?)",
                (now, action_type, 1 if success else 0,
                 round(base, 3), round(cat['budget'], 3),
                 cat['streak'])
            )
            conn.commit()

        except Exception as e:
            log.debug("_record_risk_outcome DB: %s", e)

        # Persistir estado ajustado periódicamente (cada 10 outcomes)
        if cat['total'] % 10 == 0:
            self._save_risk_state()

        log.info("RISK_BUDGET: %s → %s | budget=%.2f streak=%+d",
                 action_type, "éxito" if success else "fallo",
                 cat['budget'], cat['streak'])

    def _reset_risk_budget(self):
        """[S96] Reinicia el presupuesto diario de riesgo (solo 'used')."""
        for cat in self._risk_budget.values():
            cat['used'] = 0.0
        # S104: Guardar estado al resetear
        self._save_risk_state()

    def injected_tasks_pending(self) -> int:
        """[S96] Número de tareas inyectadas pendientes."""
        with self._injected_lock:
            return len(self._injected_tasks)

    # ── Estado ─────────────────────────────────────────────────────────────

    def _load_state(self) -> Dict[str, Any]:
        try:
            if WILL_STATE.exists():
                return json.loads(WILL_STATE.read_text())
        except Exception:
            pass
        return {"action_count": {}, "q_table": {}}

    def _save_state(self):
        try:
            WILL_STATE.parent.mkdir(parents=True, exist_ok=True)
            WILL_STATE.write_text(json.dumps({
                "action_count": self._action_count,
                "q_table_size": len(self._q_table),
                "last_decision": self._last_decision,
                "total_decisions": len(self._decisions),
                "updated": time.time(),
            }, indent=2))
        except Exception:
            pass

    def stats(self) -> Dict[str, Any]:
        return {
            "total_decisions": len(self._decisions),
            "action_count": self._action_count,
            "q_table_entries": len(self._q_table),
            "last_decision_ago_s": round(time.time() - self._last_decision, 1)
            if self._last_decision else None,
            "recent_decisions": [
                {"action": d["action"], "confidence": d["confidence"],
                 "reason": d["reason"]}
                for d in self._decisions[-5:]
            ],
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_will: Optional[Will] = None


def get_will() -> Will:
    global _will
    if _will is None:
        _will = Will()
    return _will


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Will — Voluntad Autónoma")
    p.add_argument("--decide", action="store_true", help="Decidir qué hacer")
    p.add_argument("--execute", type=str, help="Ejecutar una acción")
    p.add_argument("--cycle", action="store_true", help="Ciclo completo decide+execute+learn")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    will = get_will()

    if args.decide:
        decision = will.decide()
        print(json.dumps(decision, indent=2, ensure_ascii=False))
    elif args.execute:
        result = will.execute(args.execute)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cycle:
        result = will.cycle()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.stats:
        print(json.dumps(will.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
