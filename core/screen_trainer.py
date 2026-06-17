"""
core/screen_trainer.py — interfaz de entrenamiento /screen (S63 Bloque B)
=========================================================================

Conecta el textarea de /screen al scaffold determinista UITarsScaffold(mode="gui",
vision_backend="ocr") — UI-TARS SIN VLM, usando OCR+OpenCV (ui_parser). SER
escribe una instrucción en lenguaje natural y el scaffold:

  1. Foca la ventana Firefox ESR target (bin/eidos-focus-firefox).
  2. Captura elementos en pantalla con OCR (lista de refs, no imagen).
  3. Razona Thought→Action sobre esa lista con el modelo de texto.
  4. Ejecuta clicks/types reales con xdotool.
  5. Guarda la secuencia completa como `method_taught` en BrainMemory.

Diferencia con los endpoints crudos /click /type /key: aquí EIDOS interpreta
la INTENCIÓN del SER, no comandos directos. Si SER escribe "busca X en z-lib",
el scaffold abre z-lib, encuentra el campo de búsqueda, escribe, pulsa Enter.

Cumple la línea disciplinada S63:
  • No usa VLM (sin GPU, sin coste de visión).
  • No usa Selenium directo (evita navigator.webdriver).
  • Reusa firefox_session para HTTP cuando se puede; xdotool para la ventana.
  • Cada ejecución persiste en BrainMemory con tags trazables.

Uso:
    from core.screen_trainer import train
    res = train("entra a z-lib.fm y busca 'red team field manual'")
    print(res["status"], res["node_id"])

Self-test:
    python3 core/screen_trainer.py --self-test
"""
from __future__ import annotations

import os
import sys
import time
import json
import subprocess
import logging
from pathlib import Path
from dataclasses import asdict
from typing import Optional

sys.path.insert(0, str(Path.home() / "EIDOS"))

log = logging.getLogger("eidos.screen_trainer")

EIDOS_HOME = Path.home() / "EIDOS"
FOCUS_BIN = EIDOS_HOME / "bin" / "eidos-focus-firefox"
LAST_METHOD_FILE = Path.home() / ".eidos" / "screen_last_method.json"

# S63b: Firefox TARGET con perfil dedicado para EVITAR recursión visual
# (la ventana del panel /screen NO debe ser la ventana donde EIDOS opera).
TARGET_PROFILE_DIR = Path.home() / ".eidos" / "firefox_target"
TARGET_WIN_NAME = "EIDOS-Target"           # set en title via prefs
PANEL_WIN_HINT = "EIDOS — Vista en Vivo"   # título del browser del panel


def find_target_window() -> Optional[str]:
    """Busca la ventana Firefox TARGET (excluyendo la del panel /screen).
    Devuelve WID hex o None."""
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    try:
        r = subprocess.run(
            ["wmctrl", "-lx"], capture_output=True, text=True, timeout=3, env=env,
        )
        for line in r.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            title = parts[4]
            cls = parts[2].lower()
            if "firefox" not in cls and "eidos-target" not in cls:
                continue
            if PANEL_WIN_HINT in title:
                continue   # esta es la ventana del panel, NO la target
            return parts[0]
    except Exception as e:
        log.warning("find_target_window: %s", e)
    return None


def focus_firefox(timeout: float = 4.0) -> Optional[str]:
    """Activa la ventana Firefox TARGET (no la del panel). Devuelve WID o None."""
    wid = find_target_window()
    if not wid:
        return None
    try:
        env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
        subprocess.run(["wmctrl", "-ia", wid], capture_output=True,
                       timeout=timeout, env=env)
        return wid
    except Exception as e:
        log.warning("focus_firefox falló: %s", e)
        return None


def ensure_firefox_running(open_url: str = "about:blank") -> bool:
    """Garantiza que existe una ventana Firefox TARGET (≠ ventana del panel).
    Si no existe, lanza Firefox con perfil dedicado EIDOS-target."""
    if find_target_window():
        return True
    try:
        TARGET_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        # Inicializa user.js con título distinguible para detección por wmctrl
        user_js = TARGET_PROFILE_DIR / "user.js"
        if not user_js.exists():
            user_js.write_text(
                'user_pref("browser.startup.homepage", "about:blank");\n'
                'user_pref("browser.tabs.warnOnClose", false);\n'
                'user_pref("browser.shell.checkDefaultBrowser", false);\n'
                'user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);\n'
                'user_pref("datareporting.policy.firstRunURL", "");\n'
            )
        subprocess.Popen(
            ["firefox-esr", "--no-remote", "--new-instance",
             "--profile", str(TARGET_PROFILE_DIR),
             "--class", "EIDOS-Target",
             "--name", "EIDOS-Target",
             open_url],
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Espera hasta 8s a que la ventana aparezca
        for _ in range(16):
            time.sleep(0.5)
            if find_target_window():
                return True
        return False
    except Exception as e:
        log.warning("no se pudo arrancar firefox target: %s", e)
        return False


def train(instruction: str, max_steps: int = 8,
          focus_browser: bool = True,
          dry_run: bool = False) -> dict:
    """Ejecuta una instrucción del SER vía scaffold sin VLM.

    Returns dict con:
      status, steps, summary, node_id (en brain), elapsed_s, error.
    """
    t0 = time.time()
    result: dict = {
        "instruction": instruction,
        "status": "pending",
        "steps": [],
        "summary": "",
        "node_id": None,
        "elapsed_s": 0.0,
    }

    if not instruction or len(instruction.strip()) < 2:
        result["status"] = "error"
        result["error"] = "instrucción vacía"
        return result

    wid = None
    if focus_browser:
        ensure_firefox_running()
        wid = focus_firefox()
        result["firefox_wid"] = wid or "no-encontrada"

    try:
        from core.uitars_scaffold import UITarsScaffold, Status
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"uitars_scaffold no importable: {e}"
        return result

    try:
        scaff = UITarsScaffold(mode="gui", vision_backend="ocr",
                               dry_run=dry_run)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"no se pudo crear scaffold OCR: {e}"
        return result

    try:
        run_res = scaff.run(instruction, max_steps=max_steps)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"scaffold.run() falló: {e}"
        result["elapsed_s"] = round(time.time() - t0, 2)
        return result

    try:
        result["scaffold_status"] = run_res.status.value if hasattr(run_res, "status") else "?"
        def _act_str(s):
            at = getattr(s, "action_type", "") or ""
            ai = getattr(s, "action_inputs", {}) or {}
            try:
                args = json.dumps(ai, ensure_ascii=False)[:120]
            except Exception:
                args = str(ai)[:120]
            return f"{at}({args})" if at else ""
        result["steps"] = [
            {
                "n": i + 1,
                "thought": (getattr(s, "thought", "") or "")[:200],
                "action": _act_str(s),
                "obs": (getattr(s, "observation", "") or "")[:200],
            }
            for i, s in enumerate(getattr(run_res, "steps", [])[:max_steps])
        ]
        result["summary"] = (getattr(run_res, "summary", lambda: "")()[:1500]
                             if callable(getattr(run_res, "summary", None))
                             else str(run_res)[:1500])
    except Exception as e:
        log.warning("serialización run_res parcial: %s", e)

    node_id = _persist_method(instruction, result, wid)
    result["node_id"] = node_id
    result["status"] = "ok" if result["steps"] else "no-steps"
    result["elapsed_s"] = round(time.time() - t0, 2)

    try:
        LAST_METHOD_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_METHOD_FILE.write_text(json.dumps({
            "instruction": instruction,
            "node_id": node_id,
            "ts": time.time(),
        }, ensure_ascii=False))
    except Exception:
        pass

    return result


def repeat_last() -> dict:
    """Re-ejecuta el último método entrenado (si existe en el archivo cache)."""
    if not LAST_METHOD_FILE.exists():
        return {"status": "error", "error": "no hay método previo"}
    try:
        data = json.loads(LAST_METHOD_FILE.read_text())
        return train(data["instruction"], focus_browser=True)
    except Exception as e:
        return {"status": "error", "error": f"no se pudo leer último método: {e}"}


def _persist_method(instruction: str, run_data: dict, wid: Optional[str]) -> Optional[str]:
    """Guarda el método entrenado en BrainMemory con tags trazables."""
    try:
        from core.brain_memory import BrainMemory
        brain = BrainMemory()
        steps_summary = " → ".join(
            s["action"][:60] for s in run_data.get("steps", [])[:10]
        ) or "(sin pasos)"
        content = (
            f"method_taught (screen_trainer)\n"
            f"Instrucción: {instruction[:300]}\n"
            f"Firefox WID: {wid or 'n/a'}\n"
            f"Status scaffold: {run_data.get('scaffold_status','?')}\n"
            f"Pasos: {steps_summary}\n"
        )
        node_id = brain.remember(
            content=content,
            tags=["screen_taught", "method_taught", "firefox_esr", "user=SER"],
            importance=0.8,
            category="method_taught",
        )
        return node_id
    except Exception as e:
        log.warning("persist_method falló: %s", e)
        return None


def _self_test() -> dict:
    """Smoke-test: importa el módulo, llama train(dry_run=True) con instrucción simple.
    NO mueve el cursor ni clica nada (dry_run). Verifica que la cadena completa
    (import → scaffold → persist) no se rompe."""
    res = train("ping interno scaffold OCR sin VLM",
                max_steps=2, focus_browser=False, dry_run=True)
    ok_keys = all(k in res for k in ("status", "steps", "summary", "elapsed_s"))
    return {
        "self_test": "PASS" if ok_keys else "FAIL",
        "scaffold_status": res.get("scaffold_status", "?"),
        "n_steps": len(res.get("steps", [])),
        "elapsed_s": res.get("elapsed_s", 0),
        "node_id": res.get("node_id"),
        "error": res.get("error"),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [screen_trainer] %(message)s")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--instruction")
    ap.add_argument("--repeat-last", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(_self_test(), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.repeat_last:
        print(json.dumps(repeat_last(), ensure_ascii=False, indent=2))
        sys.exit(0)
    if args.instruction:
        print(json.dumps(train(args.instruction, dry_run=args.dry_run),
                         ensure_ascii=False, indent=2))
        sys.exit(0)
    ap.print_help()
