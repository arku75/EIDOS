"""
core/eidos_skills.py — SKILLS GENERALES: aprender un concepto UNA vez y generalizarlo. S125.

Visión de SER: "que EIDOS coja el CONCEPTO y lo generalice a CUALQUIER cosa, no un script por
sitio". Un SKILL = un procedimiento GENERAL y transferible que EIDOS sabe hacer (registrarse en
un sitio, abrir un programa, usar una herramienta de terminal, rellenar un formulario, estudiar
un tema en la web...). No es código por caso: es el MÉTODO, que se aplica igual en todos lados.

Cómo aprende (maestro→alumno→generalización):
  1. EIDOS conoce el skill GENERAL (procedimiento) — sembrado o enseñado por SER.
  2. Lo ejecuta en un CASO concreto (labex.io, GIMP, nmap...). Se guarda como INSTANCIA.
  3. Cada éxito REFUERZA el skill general (sube confianza) → generaliza con la experiencia.
  4. Ante algo NUEVO, EIDOS RECUERDA el skill que aplica y lo usa (no parte de cero).

Ligero: SQLite directo a knowledge_nodes (source='skill_general') + tabla skill_instances.
NO reconstruye el grafo (la carga importa). El bridge recoge los nodos en su ciclo normal.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.skills")
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"


def _conn():
    from core.db import get_conn
    c = get_conn(BRAIN_DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS skill_instances(
        skill TEXT, instance TEXT, success INTEGER, detail TEXT, ts REAL)""")
    return c


def learn_skill(name: str, steps: str, domain: str = "general",
                confidence: float = 0.85) -> None:
    """EIDOS aprende (o refuerza) un PROCEDIMIENTO GENERAL transferible. Idempotente:
    si ya existe, sube la confianza (repetir/lograr fortalece). SQLite directo (ligero)."""
    try:
        c = _conn()
        concept = f"skill general: {name}"
        row = c.execute("SELECT id, confidence FROM knowledge_nodes WHERE concept=?",
                        (concept,)).fetchone()
        if row:
            new_conf = min(0.99, float(row[1] or 0.5) + 0.03)
            c.execute("UPDATE knowledge_nodes SET confidence=?, definition=? WHERE id=?",
                      (new_conf, steps[:2000], row[0]))
        else:
            c.execute(
                "INSERT INTO knowledge_nodes "
                "(id, concept, definition, category, confidence, source, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("skillg_" + uuid.uuid4().hex[:12], concept, steps[:2000], f"skill:{domain}",
                 confidence, "skill_general", time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        log.info("🧠 skill general: %s", name)
    except Exception as e:
        log.debug("learn_skill: %s", e)


def note_instance(skill: str, instance: str, success: bool = True, detail: str = "") -> None:
    """Registra un CASO concreto donde EIDOS aplicó el skill, y si fue bien REFUERZA el general."""
    try:
        c = _conn()
        c.execute("INSERT INTO skill_instances(skill,instance,success,detail,ts) VALUES(?,?,?,?,?)",
                  (str(skill)[:80], str(instance)[:120], 1 if success else 0,
                   str(detail)[:200], time.time()))
        c.commit()
        if success:
            # reforzar el skill general (generaliza con la experiencia)
            row = c.execute("SELECT id, confidence, definition FROM knowledge_nodes "
                            "WHERE concept=?", (f"skill general: {skill}",)).fetchone()
            if row:
                c.execute("UPDATE knowledge_nodes SET confidence=? WHERE id=?",
                          (min(0.99, float(row[1] or 0.5) + 0.03), row[0]))
                c.commit()
    except Exception as e:
        log.debug("note_instance: %s", e)


def recall_skill(query: str) -> Optional[Dict[str, Any]]:
    """Ante una situación NUEVA, ¿qué skill general aplica? Busca por palabras clave."""
    try:
        c = _conn()
        rows = c.execute("SELECT concept, definition, confidence FROM knowledge_nodes "
                         "WHERE source='skill_general' ORDER BY confidence DESC").fetchall()
        q = set(w for w in (query or "").lower().split() if len(w) > 2)
        best, best_score = None, 0
        for concept, definition, conf in rows:
            name = concept.replace("skill general:", "").strip().lower()
            score = sum(1 for w in q if w in name or w in (definition or "").lower())
            if score > best_score:
                best_score = score
                best = {"skill": name, "steps": definition, "confidence": conf}
        return best if best_score >= 1 else None
    except Exception:
        return None


def list_skills() -> List[Dict[str, Any]]:
    try:
        c = _conn()
        rows = c.execute("SELECT concept, confidence FROM knowledge_nodes "
                         "WHERE source='skill_general' ORDER BY confidence DESC").fetchall()
        out = []
        for concept, conf in rows:
            n = concept.replace("skill general:", "").strip()
            inst = c.execute("SELECT COUNT(*) FROM skill_instances WHERE skill=? AND success=1",
                             (n,)).fetchone()[0]
            out.append({"skill": n, "confidence": conf, "exitos": inst})
        return out
    except Exception:
        return []


# ── SKILLS SEMILLA (los conceptos base; se refuerzan con la práctica) ─────────
_SEED = [
    ("registrarse o entrar en un sitio web",
     "Ir a /register o /login; localizar el correo por su tipo (input[type=email]) y escribirlo; "
     "la contraseña (input[type=password]) y escribirla; confirmar contraseña si la piden; pulsar "
     "enviar (Sign up/Register/Crear cuenta/Entrar); si hay CAPTCHA pedir a SER que lo resuelva (no "
     "se elude); si piden verificación por correo, abrir el email en Gmail y pulsar el enlace; "
     "confirmar que aparece logout/mi cuenta. Igual en TODOS los sitios; solo cambian las URLs."),
    ("abrir un programa en el escritorio",
     "Localizar su icono/entrada de menú o lanzarlo por nombre; esperar a que aparezca su ventana; "
     "confirmar mirando la ventana activa. Recordar dónde estaba para la próxima."),
    ("usar una herramienta de terminal",
     "Leer su 'man'/'--help' para conocer sintaxis y flags; probar el caso simple primero; observar "
     "la salida; ajustar flags según el objetivo. El método es igual para cualquier herramienta."),
    ("rellenar un formulario web",
     "Identificar cada campo por su etiqueta/tipo; escribir el dato correcto en cada uno; revisar "
     "antes de enviar; pulsar enviar; verificar el resultado en la página."),
    ("estudiar un tema en la web y sacar conclusiones",
     "Buscar fuentes; leer las páginas y sus sublinks relevantes; extraer los conceptos clave al "
     "grafo; y AL FINAL escribir MI propia conclusión conectándolo con lo que ya sé (no solo copiar)."),
    ("aprender a usar una app nueva observando su interfaz",
     "Percibir los controles (AT-SPI2/OCR); razonar qué hace cada uno con lo que ya sé; probar lo "
     "desconocido con cuidado; verificar el efecto; guardar causa→efecto. Si no sé algo, preguntar a SER."),
]


def seed_core_skills() -> int:
    n = 0
    for name, steps in _SEED:
        learn_skill(name, steps, domain="core")
        n += 1
    return n


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "seed":
        print("skills sembrados:", seed_core_skills())
    elif cmd == "list":
        print(json.dumps(list_skills(), ensure_ascii=False, indent=2))
    elif cmd == "recall" and len(sys.argv) > 2:
        print(json.dumps(recall_skill(" ".join(sys.argv[2:])), ensure_ascii=False, indent=2))
    else:
        print("uso: seed | list | recall <situación>")
