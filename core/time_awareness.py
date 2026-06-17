"""
core/time_awareness.py — EIDOS tiene conciencia del tiempo real.

EIDOS sabe qué hora es, qué día es, si es de noche o de día,
y planifica sus tareas en consecuencia:
  - Día (8-22h): Colony activa, aprende, responde a SER
  - Noche (22-8h): ciclo nocturno, limpieza brain, búsqueda libros
  - Fines de semana: business intelligence, análisis de tendencias

También lleva un calendario de metas temporales.
"""
from datetime import datetime, timedelta
from pathlib import Path
import json, time, logging

log = logging.getLogger("time_awareness")

EIDOS_HOME   = Path.home() / ".eidos"
SCHEDULE_FILE = EIDOS_HOME / "schedule.json"


def get_now() -> datetime:
    return datetime.now()


def get_time_context() -> dict:
    """Retorna contexto temporal completo para que EIDOS sepa dónde está."""
    now = get_now()
    hour = now.hour
    weekday = now.weekday()  # 0=lunes, 6=domingo

    is_night   = hour < 8 or hour >= 22
    is_morning = 8 <= hour < 12
    is_evening = 17 <= hour < 22
    is_weekend = weekday >= 5

    if is_night:
        period = "noche"
        suggestion = "Ciclo nocturno — limpiar brain, buscar libros, analizar clon"
    elif is_morning:
        period = "mañana"
        suggestion = "Momento ideal para aprender — Colony activa, SER disponible"
    elif is_evening:
        period = "tarde-noche"
        suggestion = "Business intelligence — buscar tendencias, preparar ideas para SER"
    else:
        period = "tarde"
        suggestion = "Trabajo técnico — experimentar en sandbox, leer libros"

    return {
        "datetime": now.isoformat(),
        "hour": hour,
        "weekday": now.strftime("%A"),
        "weekday_es": ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"][weekday],
        "period": period,
        "is_night": is_night,
        "is_weekend": is_weekend,
        "suggestion": suggestion,
        "unix": int(time.time()),
    }


def should_run_night_cycle() -> bool:
    """¿Debería ejecutarse el ciclo nocturno ahora?"""
    now = get_now()
    # Entre las 3:00 y las 4:00 de la madrugada
    if now.hour == 3:
        # Verificar si ya corrió hoy
        schedule = _load_schedule()
        last_run = schedule.get("last_night_cycle", "")
        if last_run != now.strftime("%Y-%m-%d"):
            return True
    return False


def mark_night_cycle_done():
    """Marca el ciclo nocturno como completado hoy."""
    schedule = _load_schedule()
    schedule["last_night_cycle"] = get_now().strftime("%Y-%m-%d")
    _save_schedule(schedule)


def get_next_tasks() -> list:
    """
    EIDOS decide qué hacer ahora basándose en la hora y las metas activas.
    Retorna lista de tareas sugeridas para la sesión actual.
    """
    ctx = get_time_context()
    tasks = []

    if ctx["is_night"]:
        tasks.append({"priority": 10, "task": "Ciclo nocturno — clean brain + sync clon + buscar libros"})
        tasks.append({"priority": 8,  "task": "Analizar vulnerabilidades del clon con bandit"})
        tasks.append({"priority": 7,  "task": "Buscar libros sobre SQL injection y Python 2024"})
    elif ctx["is_weekend"]:
        tasks.append({"priority": 9,  "task": "Business intelligence — buscar ideas de negocio con IA"})
        tasks.append({"priority": 8,  "task": "Analizar tendencias tech esta semana"})
        tasks.append({"priority": 7,  "task": "Estudiar un capítulo de Linux Basics for Hackers"})
    else:
        tasks.append({"priority": 9,  "task": "Responder a SER con plena atención"})
        tasks.append({"priority": 8,  "task": "Continuar curriculum SQL injection"})
        tasks.append({"priority": 7,  "task": "Verificar estado de servicios y watchdog"})

    # Añadir tarea de la hora si hay pendientes urgentes
    try:
        from core.eidos_goals import get_active_goals
        goals = get_active_goals()
        high_prio = [g for g in goals if g.get("priority", 0) >= 9]
        for g in high_prio[:2]:
            tasks.append({"priority": g["priority"], "task": f"Meta activa: {g['title']}"})
    except Exception:
        pass

    tasks.sort(key=lambda t: -t["priority"])
    return tasks


def get_weekly_plan() -> str:
    """Genera el plan de la semana basado en metas activas y tiempo disponible."""
    now = get_now()
    plan_lines = [
        f"📅 PLAN SEMANAL — semana del {now.strftime('%d/%m/%Y')}",
        "",
        "🌅 Lunes-Viernes (trabajo técnico):",
        "  • Mañana: Colony activa, aprender con SER",
        "  • Tarde: Sandbox — experimentar con clon",
        "  • Noche: Ciclo autónomo — limpieza + libros",
        "",
        "🏖️ Sábado-Domingo (business intelligence):",
        "  • Buscar ideas de negocio con IA",
        "  • Analizar tendencias tech (web scraping)",
        "  • Preparar propuestas para SER",
        "",
        "🎯 Metas activas esta semana:",
    ]
    try:
        from core.eidos_goals import get_active_goals
        goals = get_active_goals()
        for g in goals[:5]:
            plan_lines.append(f"  [{g.get('priority','?')}] {g['title']}")
    except Exception:
        plan_lines.append("  (no se pudieron cargar las metas)")

    return "\n".join(plan_lines)


def _load_schedule() -> dict:
    try:
        if SCHEDULE_FILE.exists():
            return json.loads(SCHEDULE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_schedule(data: dict):
    EIDOS_HOME.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(data, indent=2))
