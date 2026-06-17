"""
core/business_intel.py — EIDOS analiza tendencias y propone ideas de negocio a SER.

Ciclo semanal (sábado/domingo):
  1. Busca tendencias en Open Library (libros más buscados sobre IA/automatización)
  2. Analiza su brain para ver qué habilidades tiene EIDOS
  3. Cruza habilidades con tendencias → identifica oportunidades
  4. Prepara propuesta concreta: "SER, tengo una idea: ___. Creo que podría funcionar porque ___."
  5. Guarda en brain y opcionalmente envía a SER via Telegram

EIDOS es socio de SER — propone, SER decide.
"""
import logging, time, sqlite3, uuid
from pathlib import Path
from typing import Dict, Any, List
from core.db import get_conn

log = logging.getLogger("business_intel")

BRAIN_DB  = Path.home() / ".eidos" / "evolution_brain.db"
IDEAS_FILE = Path.home() / ".eidos" / "business_ideas.json"


def get_current_trends() -> List[Dict]:
    """Busca tendencias actuales en tecnología/IA via Open Library."""
    try:
        from core.firefox_session import search_openlibrary
        trending_topics = [
            "ai agents automation 2024",
            "python automation business 2024",
            "web scraping monetize 2024",
            "cybersecurity consulting 2024",
            "machine learning saas 2025",
        ]
        trends = []
        for topic in trending_topics[:3]:
            books = search_openlibrary(topic, min_year=2023, limit=3)
            for b in books:
                trends.append({
                    "topic": topic,
                    "book": b.get("title", ""),
                    "year": b.get("year", ""),
                    "author": b.get("author", ""),
                })
            time.sleep(1)
        return trends
    except Exception as e:
        log.error(f"get_trends: {e}")
        return []


def get_eidos_skills() -> List[str]:
    """Lee el brain de EIDOS y extrae sus habilidades actuales."""
    skills = []
    try:
        conn = get_conn(BRAIN_DB, timeout=5)
        rows = conn.execute(
            "SELECT concept FROM knowledge_nodes WHERE category IN ('skills','curriculum','book_knowledge','security') "
            "AND confidence >= 0.7 ORDER BY confidence DESC LIMIT 30"
        ).fetchall()
        pass  # S109: get_conn no necesita close()
        skills = [r[0] for r in rows]
    except Exception as e:
        log.error(f"get_skills: {e}")
    return skills


def generate_ideas(trends: List[Dict], skills: List[str]) -> List[Dict]:
    """
    Cruza tendencias con habilidades de EIDOS para generar ideas de negocio.
    EIDOS razona: ¿qué puedo hacer que la gente pague?
    """
    ideas = []

    # Ideas basadas en capacidades reales de EIDOS
    base_ideas = [
        {
            "title": "Agente IA de seguridad para PYMEs",
            "description": "EIDOS sabe detectar vulnerabilidades SQL injection, XSS, y configuración de servidores. "
                          "Las PYMEs necesitan esto pero no pueden pagar un consultor. "
                          "EIDOS podría hacer auditorías automáticas por suscripción mensual.",
            "revenue_model": "50-200€/mes por empresa",
            "eidos_can_do": ["bandit scanner", "SQL injection curriculum", "Linux server analysis"],
            "next_step": "Buscar 3 PYMEs interesadas en prueba gratuita",
        },
        {
            "title": "Automatización de tareas repetitivas para freelancers",
            "description": "EIDOS puede controlar Firefox, rellenar formularios, hacer web scraping, "
                          "y automatizar cualquier tarea que un freelancer hace manualmente. "
                          "Muchos pagan por ahorrar 2-3 horas diarias.",
            "revenue_model": "100-500€ por automatización personalizada",
            "eidos_can_do": ["browser_native.py", "xdotool", "curl_cffi", "Playwright"],
            "next_step": "Identificar 5 tareas repetitivas comunes en Reddit/Fiverr",
        },
        {
            "title": "Asistente IA local para DevOps/SysAdmins",
            "description": "EIDOS vive en Kali Linux, entiende de sistemas, logs, Docker, Kubernetes. "
                          "Un SysAdmin local que no necesita cloud ni datos en servidores ajenos. "
                          "Privacidad total + conocimiento técnico real.",
            "revenue_model": "Venta única 150€ + 20€/mes soporte",
            "eidos_can_do": ["Colony deliberation", "system analysis", "watchdog", "Docker knowledge"],
            "next_step": "Crear demo en 30min mostrando qué hace EIDOS con un problema real de servidor",
        },
    ]

    # Añadir ideas basadas en tendencias encontradas
    for trend in trends[:2]:
        ideas.append({
            "title": f"Servicio relacionado con: {trend['topic']}",
            "description": f"Basado en el libro '{trend['book']}' ({trend['year']}), hay demanda en: {trend['topic']}. "
                          "EIDOS podría ofrecer automatización o consultoría en este área.",
            "revenue_model": "Por definir según el nicho",
            "eidos_can_do": ["web research", "automation", "browser control"],
            "next_step": "Investigar más sobre este nicho específico",
        })

    ideas.extend(base_ideas)
    return ideas


def save_ideas(ideas: List[Dict]):
    """Guarda las ideas en brain y en archivo local."""
    import json
    IDEAS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Cargar ideas anteriores
    existing = []
    if IDEAS_FILE.exists():
        try:
            existing = json.loads(IDEAS_FILE.read_text())
        except Exception:
            pass

    # Añadir nuevas con timestamp
    from datetime import datetime
    for idea in ideas:
        idea["created_at"] = datetime.now().isoformat()
        existing.append(idea)

    IDEAS_FILE.write_text(json.dumps(existing[-20:], indent=2, ensure_ascii=False))

    # Guardar en brain
    try:
        conn = get_conn(BRAIN_DB)
        for idea in ideas:
            conn.execute(
                "INSERT OR REPLACE INTO knowledge_nodes (id,concept,definition,category,confidence,"
                "source,last_used,agent_id,character) VALUES (?,?,?,?,?,'business_intel',?,'eidos','EIDOS')",
                (str(uuid.uuid4()),
                 f"business_idea:{idea['title'][:40].replace(' ','_')}",
                 f"IDEA: {idea['title']}. {idea['description'][:200]} Revenue: {idea['revenue_model']}",
                 "business_ideas", 0.8, time.time())
            )
        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception as e:
        log.error(f"brain save: {e}")


def format_ideas_for_ser(ideas: List[Dict]) -> str:
    """Formatea las ideas para presentarlas a SER de forma clara."""
    lines = ["💡 EIDOS tiene ideas de negocio para ti, SER:\n"]
    for i, idea in enumerate(ideas[:3], 1):
        lines.append(f"━━━ IDEA {i} ━━━")
        lines.append(f"📌 {idea['title']}")
        lines.append(f"   {idea['description'][:200]}")
        lines.append(f"💰 Modelo: {idea['revenue_model']}")
        lines.append(f"✅ EIDOS puede hacer: {', '.join(idea['eidos_can_do'][:3])}")
        lines.append(f"🎯 Próximo paso: {idea['next_step']}")
        lines.append("")
    lines.append("¿Exploramos alguna? Dime cuál te llama más y analizo los pasos concretos.")
    return "\n".join(lines)


def run_business_intelligence() -> Dict[str, Any]:
    """Ciclo completo de business intelligence. Llama los sábados/domingos."""
    log.info("=== EIDOS BUSINESS INTELLIGENCE ===")

    # 1. Buscar tendencias
    log.info("Buscando tendencias en Open Library...")
    trends = get_current_trends()

    # 2. Leer habilidades propias
    skills = get_eidos_skills()
    log.info(f"Habilidades EIDOS: {len(skills)} nodos relevantes")

    # 3. Generar ideas
    ideas = generate_ideas(trends, skills)
    log.info(f"Ideas generadas: {len(ideas)}")

    # 4. Guardar ideas
    save_ideas(ideas)

    # 5. Formatear para SER
    report = format_ideas_for_ser(ideas)

    # 6. Enviar a SER via Telegram si hay token
    telegram_sent = False
    try:
        token_file = Path.home() / ".eidos" / "telegram_token.txt"
        if token_file.exists():
            from core.night_cycle import send_telegram_report
            telegram_sent = send_telegram_report(report)
    except Exception:
        pass

    return {
        "ok": True,
        "trends_found": len(trends),
        "ideas_generated": len(ideas),
        "telegram_sent": telegram_sent,
        "report": report,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run_business_intelligence()
    print(result["report"])
