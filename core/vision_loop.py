"""
core/vision_loop.py — EIDOS ve la pantalla y entiende lo que hay antes de actuar.

Ciclo: screenshot → llama3.2-vision → descripción → Colony decide → [GUI:] → repetir
"""
from core.db import get_conn
import base64
import json
import time
import logging
import subprocess
import os
from pathlib import Path
from typing import Optional, Dict, Any

log = logging.getLogger("vision_loop")
DISPLAY = os.environ.get("DISPLAY", ":0")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VISION_MODEL = "moondream:latest"
FAST_VISION_MODEL = "moondream:latest"


def _screenshot_b64(wid: Optional[int] = None) -> Optional[str]:
    """Toma un screenshot y lo devuelve como base64."""
    ts = int(time.time())
    path = f"/tmp/eidos_vision_{ts}.png"
    env = {**os.environ, "DISPLAY": DISPLAY}
    if wid:
        cmd = f"scrot -z {path} --window {wid}"
    else:
        cmd = f"scrot -z {path}"
    ret = subprocess.run(cmd, shell=True, env=env, capture_output=True, timeout=5)
    if ret.returncode != 0 or not Path(path).exists():
        subprocess.run(f"scrot -z {path}", shell=True, env=env, capture_output=True, timeout=5)
    if Path(path).exists():
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return None


def see_screen(question: str, wid: Optional[int] = None,
               fast: bool = False) -> Dict[str, Any]:
    """
    EIDOS mira la pantalla y responde una pregunta sobre lo que ve.

    Args:
        question: qué quiere saber EIDOS (ej: "¿qué controles hay? ¿dónde está el botón Enviar?")
        wid: WID de ventana específica. None = pantalla completa
        fast: usar moondream (rápido pero menos preciso) en vez de llama3.2-vision

    Returns:
        {"ok": bool, "description": str, "model": str, "screenshot": path}
    """
    import requests

    model = FAST_VISION_MODEL if fast else VISION_MODEL
    ts = int(time.time())
    path = f"/tmp/eidos_vision_{ts}.png"

    # Screenshot
    env = {**os.environ, "DISPLAY": DISPLAY}
    cmd = f"scrot -z {path} --window {wid}" if wid else f"scrot -z {path}"
    subprocess.run(cmd, shell=True, env=env, capture_output=True, timeout=5)
    if not Path(path).exists():
        subprocess.run(f"scrot -z {path}", shell=True, env=env, capture_output=True, timeout=5)

    if not Path(path).exists():
        return {"ok": False, "error": "No se pudo capturar la pantalla"}

    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    prompt = (
        f"Eres el sistema de visión de EIDOS, una IA autónoma. "
        f"Mira esta captura de pantalla y responde en español:\n\n"
        f"PREGUNTA: {question}\n\n"
        f"Sé específico sobre posiciones (arriba/centro/abajo, izquierda/centro/derecha), "
        f"texto visible, botones, campos de texto, menús, ventanas abiertas. "
        f"Si la pregunta es sobre dónde clickar, da estimaciones en porcentaje (X%, Y%) del área visible."
    )

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 400}
            },
            timeout=600  # vision en CPU sin GPU: puede tardar 5-10 min
        )
        resp.raise_for_status()
        description = resp.json().get("response", "").strip()
        log.info("vision_loop: %s → %d chars", model, len(description))

        # Guardar en brain
        _save_vision_to_brain(question, description, path)

        return {
            "ok": True,
            "description": description,
            "model": model,
            "screenshot": path
        }
    except Exception as e:
        log.error("vision_loop error: %s", e)
        return {"ok": False, "error": str(e), "screenshot": path}


def react_loop(goal: str, wid: Optional[int] = None,
               max_steps: int = 8) -> Dict[str, Any]:
    """
    Bucle ver-reaccionar: EIDOS ve la pantalla, decide la próxima acción, la ejecuta, repite.

    Args:
        goal: qué quiere conseguir EIDOS (ej: "buscar 'python asyncio' en Firefox")
        wid: ventana donde trabajar
        max_steps: máximo de iteraciones para evitar bucles infinitos

    Returns:
        {"ok": bool, "steps": list, "final_description": str}
    """
    import requests
    from core.gui_automator import execute_gui_command, _focused_wid

    steps = []
    log.info("react_loop: iniciando para '%s' (max %d pasos)", goal, max_steps)

    for step_n in range(1, max_steps + 1):
        log.info("react_loop: paso %d/%d", step_n, max_steps)

        # 1. Ver pantalla
        vision = see_screen(
            f"Estoy intentando: '{goal}'. ¿Qué veo ahora? ¿Qué debería hacer a continuación? "
            f"¿El objetivo está conseguido? Indica exactamente qué botón o campo clickar.",
            wid=wid
        )

        if not vision["ok"]:
            steps.append({"step": step_n, "error": vision.get("error")})
            break

        description = vision["description"]
        steps.append({"step": step_n, "vision": description[:300]})

        # 2. Decidir acción basándose en lo visto
        decision_prompt = (
            f"Eres EIDOS. Tu objetivo: '{goal}'\n"
            f"Lo que ves ahora en pantalla: {description}\n\n"
            f"¿Está el objetivo conseguido? Responde SOLO con:\n"
            f"- 'DONE' si el objetivo está logrado\n"
            f"- Un comando [GUI: acción] para continuar (solo uno)\n"
            f"Comandos válidos: click X% Y%, type texto, key tecla, scroll down N, screenshot\n"
            f"Ejemplo: [GUI: click 50% 30%]"
        )

        try:
            dec_resp = requests.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": "lfm2.5-thinking:1.2b",
                    "prompt": decision_prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 100}
                },
                timeout=30
            )
            decision = dec_resp.json().get("response", "").strip()
        except Exception as e:
            decision = ""
            log.error("react_loop decisión error: %s", e)

        steps[-1]["decision"] = decision

        if "DONE" in decision.upper():
            log.info("react_loop: objetivo conseguido en %d pasos", step_n)
            return {"ok": True, "steps": steps, "final_description": description,
                    "completed_in": step_n}

        # 3. Ejecutar acción decidida
        import re
        gui_match = re.search(r'\[GUI:\s*([^\]]+)\]', decision, re.IGNORECASE)
        if gui_match:
            cmd = gui_match.group(1).strip()
            result = execute_gui_command(cmd)
            steps[-1]["executed"] = cmd
            steps[-1]["result"] = result.get("ok")
            log.info("react_loop: ejecuté [GUI: %s] → %s", cmd, result.get("ok"))
            if not result.get("ok"):
                log.warning("react_loop: falló [GUI: %s]", cmd)
            time.sleep(0.8)
        else:
            log.warning("react_loop: no se encontró comando GUI en decisión: %s", decision[:100])
            steps.append({"step": step_n, "warning": "sin comando GUI válido"})
            break

    return {"ok": False, "steps": steps,
            "error": f"Objetivo no conseguido en {max_steps} pasos"}


def _save_vision_to_brain(question: str, description: str, screenshot_path: str):
    """Guarda lo visto en evolution_brain.db para que Colony aprenda del entorno."""
    try:
        import sqlite3, uuid, hashlib
        db = Path.home() / ".eidos" / "evolution_brain.db"
        c = get_conn(db, timeout=3)
        node_id = hashlib.md5(f"vision:{question}".encode()).hexdigest()[:16]
        now = time.time()
        c.execute(
            "INSERT OR REPLACE INTO knowledge_nodes "
            "(id,concept,definition,category,confidence,source,created_at,last_used,usage_count) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            (node_id, f"vision:obs:{question[:60]}",
             f"Pregunta: {question}\nVisto: {description}\nCaptura: {screenshot_path}",
             "vision", 0.8, "vision_loop", now, now)
        )
        c.commit(); c.close()
    except Exception:
        pass
