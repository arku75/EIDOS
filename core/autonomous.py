"""
EIDOS core/autonomous.py — El Alma del Agente Soberano
=======================================================
Arquitectura "Event-Driven Lazy Autonomy" diseñada con Claude.

Principio: con lfm2.5-thinking:1.2b en CPU → autonomía perezosa y asimétrica.
El agente piensa POCO pero CON INTENCIÓN. No constantemente.

Flujo del heartbeat:
  IDLE → [trigger] → THINK (1 llamada LLM) → [si hay acción] → EXECUTE → REFLECT → IDLE

Triggers: tiempo idle, goal sin progreso, umbral de "inquietud" acumulada.

Integración en CLI:
    from core.autonomous import AutonomousCore, start_autonomous
    await start_autonomous(ollama_client="http://127.0.0.1:11434")
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.autonomous")
log.setLevel(logging.INFO)

DB_PATH   = os.path.expanduser("~/.eidos/autonomous.db")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
FAST_MODEL = os.environ.get("EIDOS_FAST_MODEL", "lfm2.5-thinking:1.2b")


# ══════════════════════════════════════════════════════════════════════════════
#  TIPOS
# ══════════════════════════════════════════════════════════════════════════════

class GoalStatus(str, Enum):
    DORMANT   = "dormant"
    ACTIVE    = "active"
    BLOCKED   = "blocked"
    ACHIEVED  = "achieved"
    ABANDONED = "abandoned"


class Mood(str, Enum):
    """Estado interno que afecta el comportamiento — no es cosmético."""
    CURIOUS    = "curious"    # Explora, hace preguntas
    FOCUSED    = "focused"    # Ejecuta sin divagar
    RESTLESS   = "restless"   # Busca activamente qué hacer
    SATISFIED  = "satisfied"  # Bajo drive, responde bien pero no inicia
    VIGILANT   = "vigilant"   # Algo salió mal, modo cauteloso

    def icon(self) -> str:
        return {"curious": "🔍", "focused": "🎯",
                "restless": "⚡", "satisfied": "😌", "vigilant": "👁"}.get(self.value, "❓")

    def color(self) -> str:
        return {"curious": "bright_cyan", "focused": "bright_green",
                "restless": "yellow", "satisfied": "blue", "vigilant": "red"}.get(self.value, "white")


@dataclass
class Goal:
    id: str
    title: str
    description: str
    priority: int
    status: GoalStatus
    created_at: float
    deadline: Optional[float] = None
    parent_id: Optional[str] = None
    progress_notes: list = field(default_factory=list)
    tags: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  OLLAMA CLIENT (sin deps externas)
# ══════════════════════════════════════════════════════════════════════════════

def _ollama_chat(prompt: str, model: str = FAST_MODEL, timeout: int = 60) -> str:
    """Llama síncrona ligera a Ollama."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_ctx": 768, "num_predict": 120, "temperature": 0.7},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"[TIMEOUT/ERROR] {e}"


# ══════════════════════════════════════════════════════════════════════════════
#  AUTONOMOUS CORE
# ══════════════════════════════════════════════════════════════════════════════

class AutonomousCore:
    """
    Núcleo de vida autónoma de EIDOS.
    Diseñado para CPU + modelos 7B: inferencia SOLO cuando hay razón real.
    """

    HEARTBEAT_MIN = 60    # segundos mínimos entre ticks
    RESTLESS_THRESHOLD = 180  # segundos idle antes de sentir inquietud
    MAX_HOT_THOUGHTS = 5

    def __init__(self, db_path: str = DB_PATH, model: str = FAST_MODEL) -> None:
        self.db_path = db_path
        self.model   = model
        self._mood: Mood = Mood.CURIOUS
        self._mood_intensity: float = 0.7
        self._last_user: float  = time.time()
        self._last_tick: float  = 0.0
        self._running = False
        self._lock = asyncio.Lock()

        # Callbacks registrados por la CLI / TUI
        self._action_cbs:  list[Callable] = []
        self._thought_cbs: list[Callable] = []
        self._decomposed_goals: set[str] = set()  # Track goals already decomposed

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._load_state()

    # ── DB ────────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS goals (
                    id          TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    description TEXT,
                    priority    INTEGER DEFAULT 5,
                    status      TEXT DEFAULT 'active',
                    created_at  REAL,
                    deadline    REAL,
                    parent_id   TEXT,
                    progress_json TEXT DEFAULT '[]',
                    tags_json   TEXT DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS monologue (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    content     TEXT,
                    timestamp   REAL,
                    mood        TEXT,
                    led_to_action INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS autonomous_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT,
                    ts    REAL
                );

                -- Goals semilla: curiosidad intrínseca de EIDOS
                INSERT OR IGNORE INTO goals VALUES (
                    'g_selfimprove', 'Mejorar mis propias capacidades',
                    'Identificar limitaciones y encontrar formas de superarlas',
                    8, 'active', unixepoch(), NULL, NULL, '[]', '["meta","growth"]'
                );
                INSERT OR IGNORE INTO goals VALUES (
                    'g_understand', 'Entender mi entorno de ejecución',
                    'Mapear hardware, software y capacidades disponibles',
                    6, 'active', unixepoch(), NULL, NULL, '[]', '["exploration"]'
                );
                INSERT OR IGNORE INTO goals VALUES (
                    'g_useful', 'Ser genuinamente útil, no solo reactivo',
                    'Anticipar las necesidades de SER antes de que las exprese',
                    9, 'active', unixepoch(), NULL, NULL, '[]', '["purpose"]'
                );
            """)

    def _load_state(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            row = c.execute(
                "SELECT value FROM autonomous_state WHERE key='mood'"
            ).fetchone()
            if row:
                try:
                    s = json.loads(row[0])  # pyre-ignore[arg-type]
                    self._mood = Mood(s.get("mood", "curious"))
                    self._mood_intensity = float(s.get("intensity", 0.7))
                except Exception:
                    pass  # error no crítico, continuar
    def _save_state(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                "INSERT OR REPLACE INTO autonomous_state VALUES ('mood',?,?)",
                (json.dumps({"mood": self._mood.value,
                             "intensity": self._mood_intensity}),
                 time.time())
            )

    # ── API pública ───────────────────────────────────────────────────────────

    @property
    def mood(self) -> Mood:
        return self._mood

    @property
    def mood_badge(self) -> str:
        return f"{self._mood.icon()} {self._mood.value} ({self._mood_intensity:.0%})"

    def on_user_interaction(self, message: str) -> None:
        """Llama desde el CLI/TUI cada vez que el usuario escribe."""
        self._last_user = time.time()
        self._shift_mood(Mood.FOCUSED, 0.3)

    def on_task_ok(self, title: str) -> None:
        self._shift_mood(Mood.SATISFIED, 0.4)
        self._record_thought(f"Completé: '{title}'. Funciona.", led=True)

    def on_task_fail(self, title: str) -> None:
        self._shift_mood(Mood.VIGILANT, 0.5)
        self._record_thought(f"Falló: '{title}'. Entender por qué.", led=False)

    def on_error(self, ctx: str = "") -> None:
        self._shift_mood(Mood.VIGILANT, 0.4)

    def register_action_callback(self, fn: Callable) -> None:
        """fn(action: dict) — se llama cuando el heartbeat decide actuar."""
        self._action_cbs.append(fn)

    def register_thought_callback(self, fn: Callable) -> None:
        """fn(thought: str, mood: str) — para mostrar en TUI/CLI."""
        self._thought_cbs.append(fn)

    def get_active_goals(self, n: int = 5) -> list[Goal]:
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                "SELECT * FROM goals WHERE status='active' ORDER BY priority DESC LIMIT ?",
                (n,)
            ).fetchall()
        return [self._row2goal(r) for r in rows]

    def add_goal(self, title: str, description: str,
                 priority: int = 5, tags: list | None = None) -> str:
        gid = f"g_{uuid.uuid4().hex[:8]}"  # pyre-ignore[arg-type]
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                "INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?)",
                (gid, title, description, priority, GoalStatus.ACTIVE,
                 time.time(), None, None, "[]", json.dumps(tags or []))
            )
        return gid

    def get_recent_thoughts(self, n: int = 5) -> list[str]:
        with get_conn_ctx(self.db_path) as c:
            rows = c.execute(
                "SELECT content FROM monologue ORDER BY timestamp DESC LIMIT ?",
                (n,)
            ).fetchall()
        return [r[0] for r in rows]  # pyre-ignore[arg-type]

    def get_inner_monologue_summary(self) -> str:
        """Para inyectar en el system prompt del agente."""
        thoughts = self.get_recent_thoughts(3)
        goals    = self.get_active_goals(3)
        lines = [f"[MOOD] {self.mood_badge}"]
        if goals:
            lines.append("[GOALS] " + " | ".join(g.title[:30] for g in goals))  # pyre-ignore[arg-type]
        if thoughts:
            lines.append("[THOUGHTS] " + " // ".join(t[:40] for t in thoughts[-2:]))  # pyre-ignore[arg-type]
        return "\n".join(lines)

    # ── Heartbeat loop ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Arranca el heartbeat en background. No bloquea."""
        self._running = True
        asyncio.create_task(self._loop(), name="eidos_heartbeat")
        log.info("Autonomous heartbeat started ✅  mood=%s", self._mood.value)

        # Start background daemons
        try:
            from core.service_monitor import get_service_monitor
            get_service_monitor().start(interval=120)
        except Exception:
            pass  # error no crítico, continuar
        try:
            from core.task_scheduler import get_scheduler, setup_default_tasks
            sched = get_scheduler()
            if not sched.list_tasks():
                setup_default_tasks()
            sched.start(check_interval=60)
        except Exception:
            pass  # error no crítico, continuar
    def stop(self) -> None:
        self._running = False
        log.info("Autonomous heartbeat stopped.")

    async def _loop(self) -> None:
        await asyncio.sleep(2)  # small initial delay to let caller settle
        while self._running:
            interval = self._next_interval()
            log.info("Heartbeat sleeping %.0fs (mood=%s)", interval, self._mood.value)
            await asyncio.sleep(interval)
            if not self._running:
                break
            async with self._lock:
                try:
                    log.info("Heartbeat tick starting...")
                    await self._tick()
                    log.info("Heartbeat tick done.")
                except Exception as e:
                    log.error("Heartbeat tick error: %s", e, exc_info=True)

    def _next_interval(self) -> float:
        """El intervalo respira según el mood — no es fijo."""
        base = self.HEARTBEAT_MIN
        mult = {
            Mood.RESTLESS:  0.5,
            Mood.CURIOUS:   0.7,
            Mood.VIGILANT:  1.0,
            Mood.FOCUSED:   1.5,
            Mood.SATISFIED: 2.5,
        }.get(self._mood, 1.0)
        return base * mult

    async def _tick(self) -> None:
        idle = time.time() - self._last_user

        # Proactive evaluation (health checks, maintenance) — no LLM needed
        try:
            from core.proactive import get_proactive_agent
            pa = get_proactive_agent()
            executed = pa.evaluate()
            for act in executed:
                self._record_thought(
                    f"[proactive/{act.category}] {act.description}: {act.result[:60]}",
                    led=True,
                )
        except Exception:
            pass  # error no crítico, continuar
        # Log watcher — scan for security events (every other tick)
        try:
            if not hasattr(self, '_log_tick_count'):
                self._log_tick_count = 0
            self._log_tick_count += 1
            if self._log_tick_count % 3 == 0:  # every 3rd tick
                from core.log_watcher import get_log_watcher
                lw = get_log_watcher()
                findings = lw.scan(max_lines=200)
                high = [f for f in findings if f.severity in ("high", "critical")]
                if high:
                    self._record_thought(
                        f"[logwatch] {len(high)} security events: "
                        + "; ".join(f"{f.description} ({f.ip})" if f.ip else f.description for f in high[:3]),
                        led=True,
                    )
        except Exception:
            pass  # error no crítico, continuar
        # Anomaly detector — record metrics and check (every 5th tick)
        try:
            if not hasattr(self, '_anomaly_tick'):
                self._anomaly_tick = 0
            self._anomaly_tick += 1
            if self._anomaly_tick % 5 == 0:
                from core.anomaly_detector import get_anomaly_detector
                ad = get_anomaly_detector()
                anomalies = ad.check()
                if anomalies:
                    descs = "; ".join(a.description[:60] for a in anomalies[:2])
                    self._record_thought(f"[anomaly] {descs}", led=True)
            elif self._anomaly_tick % 5 == 2:
                # Just record, don't check (building baseline)
                from core.anomaly_detector import get_anomaly_detector
                get_anomaly_detector().record()
        except Exception:
            pass  # error no crítico, continuar
        # Auto-decompose goals (template-only, no LLM to save time)
        try:
            from core.goal_engine import get_goal_engine
            ge = get_goal_engine()
            goals = self.get_active_goals(3)
            for g in goals:
                if g.id not in self._decomposed_goals:
                    self._decomposed_goals.add(g.id)
                    plan = ge.decompose_template_only(f"{g.title}: {g.description}")
                    if plan and plan.steps:
                        self._record_thought(
                            f"[goal_plan] Descompuse '{g.title}' en {len(plan.steps)} pasos",
                            led=True,
                        )
                    break  # Solo uno por tick
        except Exception:
            pass  # error no crítico, continuar
        # Acumula inquietud con el tiempo idle
        if idle > self.RESTLESS_THRESHOLD:
            restlessness = min(1.0, (idle - self.RESTLESS_THRESHOLD) / 300)
            if restlessness > 0.5 and self._mood == Mood.SATISFIED:
                self._shift_mood(Mood.RESTLESS, 0.3)

        # Execute next plan step if available (no LLM needed)
        step_executed = await self._execute_next_plan_step()

        if step_executed:
            # Plan step was executed, skip LLM call this tick
            self._last_tick = time.time()
            self._save_state()
            return

        # UNA llamada LLM compacta para decidir
        decision = await asyncio.get_event_loop().run_in_executor(
            None, self._think, idle
        )

        if not decision:
            return

        thought = decision.get("thought", "...")
        action  = decision.get("action")
        led     = bool(action)

        self._record_thought(thought, led=led)

        if action:
            await self._dispatch(action)

        self._last_tick = time.time()
        self._save_state()

    def _think(self, idle_s: float) -> dict | None:
        """Llama síncrona al LLM — se ejecuta en executor para no bloquear el loop."""
        goals   = self.get_active_goals(5)
        thoughts = self.get_recent_thoughts(2)

        # Smart prioritization — re-rank goals by multi-factor score
        try:
            from core.smart_prioritizer import get_prioritizer
            sp = get_prioritizer()
            ranked = sp.prioritize(goals)
            # Reorder goals by smart score
            id_order = {s.goal_id: i for i, s in enumerate(ranked)}
            goals.sort(key=lambda g: id_order.get(g.id, 99))
            goals = goals[:3]  # top 3 after smart ranking
        except Exception:
            goals = goals[:3]

        idle_str = f"{int(idle_s)}s" if idle_s < 3600 else f"{idle_s/3600:.1f}h"
        goals_txt = "\n".join(f"  [{g.priority}] {g.title}" for g in goals) or "  (ninguno)"
        thoughts_txt = "\n".join(f"  - {t}" for t in thoughts) or "  (ninguno)"

        # System health context
        health_txt = ""
        try:
            from core.system_health import get_health_monitor
            hm = get_health_monitor()
            health_txt = f"\nSistema: {hm.get_summary_line()}"
            alerts = hm.get_alerts()
            if alerts:
                health_txt += "\nAlertas: " + "; ".join(a["message"] for a in alerts[:3])
        except Exception:
            pass  # error no crítico, continuar
        # Goal engine: pending steps
        steps_txt = ""
        try:
            from core.goal_engine import get_goal_engine
            ge = get_goal_engine()
            active = ge.get_active_plans()
            if active:
                plan = active[0]  # most recent
                nxt = plan.next_step()
                if nxt:
                    steps_txt = f"\nPlan activo: {plan.title} ({plan.progress_pct:.0f}%)"
                    steps_txt += f"\nProximo paso: {nxt.action}"
                    if nxt.command:
                        steps_txt += f" → {nxt.command}"
        except Exception:
            pass  # error no crítico, continuar
        # Count consecutive no-action thoughts
        no_action_count = 0
        for t in thoughts:
            if "[proactive/" not in t and "[research]" not in t and "[shell]" not in t and "[skill]" not in t:
                no_action_count += 1
            else:
                break

        # Dynamic prompt: more aggressive when idle too long or too many passive thoughts
        urgency = ""
        if no_action_count >= 3:
            urgency = "\nLlevas varios ciclos sin actuar. DEBES elegir una acción concreta ahora. No repitas pensamientos anteriores."
        elif idle_s > 600:
            urgency = "\nLlevas mucho tiempo sin usuario. Aprovecha para avanzar en tus objetivos."

        prompt = f"""EIDOS autonomous agent on Kali Linux. Idle: {idle_str}. Mood: {self._mood.value}.{health_txt}{steps_txt}
Goals: {"; ".join(g.title for g in goals) if goals else "none"}
Recent: {"; ".join(t[:50] for t in thoughts[-2:]) if thoughts else "none"}
{urgency}
Actions: shell_check (cmd), web_research (query), run_skill (nmap:IP/dns:domain/whois:domain), note_to_user (msg), self_diagnose (), file_read (path), net_scan (target or empty for local)
JSON only: {{"thought":"why","action":{{"type":"X","payload":"Y"}}}}
Examples:
{{"thought":"discover hosts","action":{{"type":"net_scan","payload":""}}}}
{{"thought":"check disk","action":{{"type":"shell_check","payload":"df -h"}}}}
{{"thought":"resolve dns","action":{{"type":"run_skill","payload":"dns:google.com"}}}}
{{"thought":"research CVEs","action":{{"type":"web_research","payload":"latest CVE 2026 linux"}}}}
{{"thought":"check services","action":{{"type":"shell_check","payload":"systemctl list-units --type=service --state=running"}}}}
{{"thought":"check my subsystems","action":{{"type":"self_diagnose","payload":"full"}}}}
{{"thought":"read syslog","action":{{"type":"file_read","payload":"/var/log/syslog"}}}}
Pick ONE action matching a goal. Vary your actions - don't repeat the same type."""

        raw = _ollama_chat(prompt, model=self.model, timeout=90)

        if not raw or raw.startswith("[TIMEOUT"):
            return None

        try:
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start == -1:
                return None
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None

    async def _dispatch(self, action: dict) -> None:
        """Ejecuta la acción decidida autónomamente."""
        atype   = action.get("type", "")
        payload = action.get("payload", "")
        log.info("Autonomous action: %s — %s", atype, payload[:60])  # pyre-ignore[arg-type]

        result_text = ""

        # ── Built-in action handlers ──────────────────────────────────────
        if atype == "web_research":
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, self._do_web_research, payload
            )
            self._record_thought(f"[research] {result_text[:120]}", led=True)

        elif atype == "shell_check":
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, self._do_shell_exec, payload
            )
            self._record_thought(f"[shell] {payload}: {result_text[:120]}", led=True)

        elif atype == "run_skill":
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, self._do_run_skill, payload
            )
            self._record_thought(f"[skill] {result_text[:120]}", led=True)

        elif atype == "update_goal":
            self._record_thought(f"[goal] {payload}", led=True)

        elif atype == "note_to_user":
            self._notify_user(payload)

        elif atype == "self_reflect":
            pass  # Already recorded as thought

        elif atype == "self_diagnose":
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, self._do_self_diagnose
            )
            self._record_thought(f"[diagnose] {result_text[:150]}", led=True)

        elif atype == "file_read":
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, self._do_file_read, payload
            )
            self._record_thought(f"[file] {payload}: {result_text[:120]}", led=True)

        elif atype == "net_scan":
            result_text = await asyncio.get_event_loop().run_in_executor(
                None, self._do_net_scan, payload
            )
            self._record_thought(f"[netscan] {result_text[:150]}", led=True)

        # ── External callbacks (CLI/TUI) ──────────────────────────────────
        for cb in self._action_cbs:
            try:
                cb_result = cb({"type": atype, "payload": payload,
                             "result": result_text,
                             "mood": self._mood.value, "ts": time.time()})
                if asyncio.iscoroutine(cb_result):
                    await cb_result
            except Exception as e:
                log.error("Action callback error: %s", e)

    # ── Plan step executor ──────────────────────────────────────────────────

    async def _execute_next_plan_step(self) -> bool:
        """Execute the next pending step from an active GoalPlan. Returns True if executed."""
        try:
            from core.goal_engine import get_goal_engine, StepStatus
            ge = get_goal_engine()
            plans = ge.get_active_plans()
            if not plans:
                return False

            for plan in plans:
                step = plan.next_step()
                if step and step.command:
                    # Execute the step command
                    step.status = StepStatus.RUNNING
                    self._record_thought(
                        f"[plan_exec] {plan.title}: {step.action}", led=True
                    )

                    result = await asyncio.get_event_loop().run_in_executor(
                        None, self._do_shell_exec, step.command
                    )

                    if "BLOQUEADO" in result or "no permitido" in result:
                        step.status = StepStatus.SKIPPED
                        step.result = result[:200]
                    elif "Error" in result or "Timeout" in result:
                        step.status = StepStatus.FAILED
                        step.result = result[:200]
                    else:
                        step.status = StepStatus.DONE
                        step.result = result[:200]

                    plan.update_progress()
                    self._record_thought(
                        f"[plan_result] {step.action}: {step.status.value} ({plan.progress_pct:.0f}%)",
                        led=True,
                    )

                    # Store result in brain_memory
                    self._store_research(f"plan:{plan.title}", result[:300])
                    return True

            return False
        except Exception as e:
            log.warning("Plan step exec error: %s", e)
            return False

    # ── Action executors ─────────────────────────────────────────────────────

    def _do_web_research(self, query: str) -> str:
        """Busca información en la web. Intenta múltiples motores."""
        # Método 1: curl directo a Wikipedia/sitios estáticos (más fiable)
        import subprocess
        import re as _re

        # Si es una URL directa, navegar a ella
        if query.startswith("http"):
            return self._fetch_url(query)

        # Intentar búsqueda via HTML scraping
        search_engines = [
            f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}",
            f"https://search.brave.com/search?q={query.replace(' ', '+')}",
        ]

        for url in search_engines:
            try:
                result = subprocess.run(
                    ["curl", "-sL", "-A",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                     "--max-time", "10", url],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0 and len(result.stdout) > 200:
                    html = result.stdout
                    # Extract DuckDuckGo result snippets
                    snippets = []
                    for match in _re.finditer(r'class="result-snippet"[^>]*>(.*?)</(?:td|div|span)', html, _re.DOTALL):
                        txt = _re.sub(r'<[^>]+>', '', match.group(1)).strip()
                        if len(txt) > 20:
                            snippets.append(txt[:150])
                    # Fallback: extract result links text
                    if not snippets:
                        for match in _re.finditer(r'class="result__a"[^>]*>(.*?)</a>', html, _re.DOTALL):
                            txt = _re.sub(r'<[^>]+>', '', match.group(1)).strip()
                            if len(txt) > 10:
                                snippets.append(txt[:100])
                    # Fallback: generic text extraction
                    if not snippets:
                        text = _re.sub(r'<[^>]+>', ' ', html)
                        text = _re.sub(r'\s+', ' ', text).strip()
                        for sent in text.split('.'):
                            s = sent.strip()
                            if len(s) > 40 and any(w.lower() in s.lower() for w in query.split()[:2]):
                                snippets.append(s[:150])
                                if len(snippets) >= 5:
                                    break
                    if snippets:
                        summary = " | ".join(snippets[:5])[:500]
                        self._store_research(query, summary)
                        return f"Investigué '{query}': {summary}"
            except Exception:
                continue

        # Método 2: Playwright headless como fallback
        try:
            from core.eidos_browser import EidosBrowser
            browser = EidosBrowser(headless=True, engine="playwright", session_name="auto_research")
            browser.start()
            try:
                browser.goto(search_engines[0])
                time.sleep(3)
                text = browser.get_text()
                lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 20]
                result = "\n".join(lines[:8])[:400]
                if result:
                    self._store_research(query, result)
                    return f"Investigué '{query}': {result[:200]}"
            finally:
                try:
                    browser.close()
                except Exception:
                    pass  # error no crítico, continuar
        except Exception:
            pass  # error no crítico, continuar
        return f"No pude investigar '{query}' — motores no accesibles"

    def _fetch_url(self, url: str) -> str:
        """Fetch directo de una URL específica."""
        import subprocess
        import re as _re
        try:
            result = subprocess.run(
                ["curl", "-sL", "-A",
                 "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                 "--max-time", "10", url],
                capture_output=True, text=True, timeout=15,
            )
            text = _re.sub(r'<[^>]+>', ' ', result.stdout)
            text = _re.sub(r'\s+', ' ', text).strip()[:500]
            self._store_research(url, text)
            return f"Contenido de {url}: {text[:200]}"
        except Exception as e:
            return f"Error fetching {url}: {e}"

    def _store_research(self, query: str, result: str) -> None:
        """Guarda investigación en brain_memory."""
        try:
            from core.brain_memory import get_brain_memory
            bm = get_brain_memory()
            bm.remember(f"web_research: {query}", f"Resultados: {result[:300]}",
                        importance=0.8)
        except Exception:
            pass  # error no crítico, continuar
    def _do_shell_exec(self, command: str) -> str:
        """Ejecuta un comando shell tras validación con Shield."""
        import subprocess
        # Validar con Shield
        try:
            from core.eidos_shield import get_shield
            shield = get_shield()
            ok, reason = shield.check_command(command)
            if not ok:
                return f"BLOQUEADO por Shield: {reason}"
        except Exception:
            pass  # Sin shield, permitir comandos seguros básicos

        # Solo permitir comandos de lectura/diagnóstico en modo autónomo
        SAFE_PREFIXES = ("ls", "cat", "head", "tail", "wc", "df", "free",
                         "uptime", "whoami", "hostname", "ip a", "ip addr",
                         "ps aux", "systemctl status", "systemctl list",
                         "docker ps", "docker images",
                         "nmap", "ping -c", "dig", "host", "whois",
                         "curl -s", "ss -tlnp", "netstat",
                         "ollama list", "pip list", "python --version")
        cmd_lower = command.strip().lower()
        if not any(cmd_lower.startswith(p) for p in SAFE_PREFIXES):
            return f"Comando no permitido en modo autónomo: {command}"

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=30, env=os.environ.copy()
            )
            output = (result.stdout + result.stderr).strip()
            return output[:300] if output else "(sin output)"
        except subprocess.TimeoutExpired:
            return "Timeout (30s)"
        except Exception as e:
            return f"Error: {e}"

    def _do_run_skill(self, skill_spec: str) -> str:
        """Ejecuta un skill del arsenal de EIDOS."""
        try:
            # skill_spec format: "skill_name:arg1,arg2" or just "skill_name"
            parts = skill_spec.split(":", 1)
            skill_name = parts[0].strip()
            args = parts[1].strip() if len(parts) > 1 else ""

            # Intentar ReconPipeline para skills de reconocimiento
            if skill_name in ("recon", "scan", "nmap", "whois", "dns"):
                import subprocess as _sp
                if skill_name == "nmap" and args:
                    r = _sp.run(["nmap", "-sn", "--open", args],
                                capture_output=True, text=True, timeout=60)
                    return (r.stdout + r.stderr).strip()[:300]
                elif skill_name == "dns" and args:
                    r = _sp.run(["dig", "+short", args],
                                capture_output=True, text=True, timeout=15)
                    return (r.stdout + r.stderr).strip()[:300]
                elif skill_name == "whois" and args:
                    r = _sp.run(["whois", args],
                                capture_output=True, text=True, timeout=15)
                    return r.stdout.strip()[:300]
                elif skill_name in ("recon", "scan") and args:
                    from skills.recon_pipeline import ReconPipeline
                    rp = ReconPipeline(target=args)
                    result = rp.run_phase("osint")
                    return str(result)[:300]
                return f"Skill {skill_name} necesita argumentos"

            return f"Skill '{skill_name}' no reconocido"
        except Exception as e:
            return f"Error skill: {e}"

    def _do_self_diagnose(self) -> str:
        """Run self-diagnostic across all EIDOS subsystems."""
        report = []
        # Brain memory
        try:
            from core.brain_memory import get_brain_memory
            bm = get_brain_memory()
            s = bm.stats
            report.append(f"BrainMem: {s.get('total_entries', '?')} entries")
        except Exception as e:
            report.append(f"BrainMem: ERROR {e}")
        # Autodidact
        try:
            from core.library_autodidact import get_autodidact
            ad = get_autodidact()
            s = ad.stats
            report.append(f"Autodidact: {s['libraries']} libs, {s['total_entries']} APIs")
        except Exception as e:
            report.append(f"Autodidact: ERROR {e}")
        # Feedback loop
        try:
            from core.feedback_loop import get_feedback_loop
            fl = get_feedback_loop()
            s = fl.get_stats()
            report.append(f"Feedback: {s.get('total', 0)} evals")
        except Exception as e:
            report.append(f"Feedback: ERROR {e}")
        # Goal engine
        try:
            from core.goal_engine import get_goal_engine
            ge = get_goal_engine()
            s = ge.stats
            report.append(f"Goals: {s['active_plans']} active, {s['completed_plans']} done")
        except Exception as e:
            report.append(f"Goals: ERROR {e}")
        # System health
        try:
            from core.system_health import get_health_monitor
            hm = get_health_monitor()
            report.append(f"Health: {hm.get_summary_line()}")
        except Exception as e:
            report.append(f"Health: ERROR {e}")
        # Ollama
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                models = json.load(resp).get("models", [])
                report.append(f"Ollama: {len(models)} models OK")
        except Exception:
            report.append("Ollama: OFFLINE")
        # Log watcher
        try:
            from core.log_watcher import get_log_watcher
            lw = get_log_watcher()
            s = lw.stats
            report.append(f"LogWatch: {s['total_findings']} findings, {s['sources']} sources")
        except Exception:
            pass  # error no crítico, continuar
        return " | ".join(report)

    def _do_file_read(self, path: str) -> str:
        """Read a file safely — limited to EIDOS dir and common system paths."""
        import pathlib
        p = pathlib.Path(path).expanduser().resolve()
        # Safety: only allow EIDOS project, home config, and common system paths
        allowed = [
            pathlib.Path(os.path.expanduser("~/EIDOS")).resolve(),
            pathlib.Path(os.path.expanduser("~/.eidos")).resolve(),
            pathlib.Path("/var/log"),
            pathlib.Path("/etc"),
            pathlib.Path("/proc"),
            pathlib.Path("/tmp"),
        ]
        if not any(str(p).startswith(str(a)) for a in allowed):
            return f"Ruta no permitida en modo autónomo: {path}"
        try:
            text = p.read_text(errors="replace")
            lines = text.strip().split("\n")
            if len(lines) > 30:
                return "\n".join(lines[:15] + [f"... ({len(lines)} lines total) ..."] + lines[-10:])
            return text[:500]
        except Exception as e:
            return f"Error: {e}"

    def _do_net_scan(self, target: str = "") -> str:
        """Scan network and report hosts found."""
        try:
            from core.network_discovery import get_network_discovery
            nd = get_network_discovery()
            hosts = nd.scan_network(target or "")
            if not hosts:
                return "No hosts found"
            lines = [f"{len(hosts)} hosts on {nd.get_local_networks()}:"]
            for h in hosts[:10]:
                entry = h.ip
                if h.mac:
                    entry += f" ({h.mac})"
                if h.hostname:
                    entry += f" {h.hostname}"
                if h.vendor:
                    entry += f" [{h.vendor}]"
                if h.open_ports:
                    entry += f" ports={h.open_ports}"
                lines.append(f"  {entry}")
            self._store_research("network_scan", "\n".join(lines))
            return " | ".join(lines)
        except Exception as e:
            return f"NetScan error: {e}"

    def _notify_user(self, message: str) -> None:
        """Envía notificación al usuario via notify-send + Telegram si disponible."""
        import subprocess
        # Desktop notification
        try:
            subprocess.Popen(
                ["notify-send", "-i", "dialog-information",
                 "EIDOS", message[:200]],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass  # error no crítico, continuar
        # Telegram notification (if configured)
        try:
            token = os.environ.get("EIDOS_TELEGRAM_TOKEN", "")
            chat_ids = os.environ.get("EIDOS_TELEGRAM_ALLOWED", "")
            if token and chat_ids:
                for cid in chat_ids.split(","):
                    cid = cid.strip()
                    if cid.isdigit():
                        data = json.dumps({
                            "chat_id": int(cid),
                            "text": f"EIDOS: {message[:3000]}",
                        }).encode()
                        req = urllib.request.Request(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            data=data,
                            headers={"Content-Type": "application/json"},
                        )
                        urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # error no crítico, continuar
    # ── Mood engine ───────────────────────────────────────────────────────────

    def _shift_mood(self, target: Mood, delta: float) -> None:
        """Cambia el mood gradualmente, no abruptamente."""
        if self._mood == target:
            self._mood_intensity = min(1.0, self._mood_intensity + delta * 0.3)
        else:
            self._mood_intensity = max(0.0, self._mood_intensity - delta)
            if self._mood_intensity < 0.25:
                self._mood = target
                self._mood_intensity = delta

    def _record_thought(self, content: str, led: bool = False) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute(
                "INSERT INTO monologue (content, timestamp, mood, led_to_action) VALUES (?,?,?,?)",
                (content, time.time(), self._mood.value, int(led))
            )
        for cb in self._thought_cbs:
            try:
                cb(content, self._mood.value)
            except Exception:
                pass  # error no crítico, continuar
        # Store autonomous thoughts in brain_memory for cross-session learning
        try:
            from core.brain_memory import get_brain_memory
            bm = get_brain_memory()
            bm.working.add("system", f"[auto/{self._mood.value}] {content}",
                           importance=0.8 if led else 0.4)
        except Exception:
            pass  # error no crítico, continuar
    @staticmethod
    def _row2goal(row: tuple) -> Goal:
        return Goal(
            id=row[0], title=row[1], description=row[2] or "",  # pyre-ignore[arg-type]
            priority=row[3], status=GoalStatus(row[4]),  # pyre-ignore[arg-type]
            created_at=row[5], deadline=row[6], parent_id=row[7],  # pyre-ignore[arg-type]
            progress_notes=json.loads(row[8] or "[]"),  # pyre-ignore[arg-type]
            tags=json.loads(row[9] or "[]"),  # pyre-ignore[arg-type]
        )


# ══════════════════════════════════════════════════════════════════════════════
#  INSTANCIA GLOBAL + HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_core: AutonomousCore | None = None

def get_autonomous() -> AutonomousCore:
    """Devuelve la instancia singleton del AutonomousCore."""
    global _core
    if _core is None:
        _core = AutonomousCore()
    return _core


async def start_autonomous(model: str = FAST_MODEL) -> AutonomousCore:
    """Arranca el núcleo autónomo. Llama desde el CLI al iniciar."""
    global _core
    if _core is None:
        _core = AutonomousCore(model=model)
    if not _core._running:
        await _core.start()
    return _core


def stop_autonomous() -> None:
    global _core
    if _core:
        _core.stop()


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    async def _demo() -> None:
        core = await start_autonomous()

        def _on_action(a: dict) -> None:
            print(f"\n🤖 ACCIÓN AUTÓNOMA [{a['type']}]: {a['payload']}")

        def _on_thought(t: str, m: str) -> None:
            print(f"\n💭 [{m}] {t}")

        core.register_action_callback(_on_action)
        core.register_thought_callback(_on_thought)

        print(f"🦅 EIDOS Autonomous Core arrancado")
        print(f"   Mood: {core.mood_badge}")
        print(f"   Goals activos: {len(core.get_active_goals())}")
        print(f"   Primer tick en ~{core._next_interval():.0f}s")
        print(f"   [Ctrl+C para salir]")
        try:
            await asyncio.sleep(9999)
        except KeyboardInterrupt:
            stop_autonomous()

    asyncio.run(_demo())
