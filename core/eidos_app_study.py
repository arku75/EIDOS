"""
core/eidos_app_study.py — EIDOS estudia apps reales (S65c)
============================================================

Habilita a EIDOS para estudiar aplicaciones de escritorio Y servicios web
locales POR SÍ MISMO, no via Claude. Usa el scaffold uitars (mode=gui,
vision_backend=ocr — SIN VLM, sin GPU) para razonar sobre la UI capturada
y persiste el aprendizaje en BrainMemory con category=app_study.

Pipeline:
  1. Resolver la app (catálogo de aliases conocidos + autodetección).
  2. Lanzar binario (o focalizar ventana existente).
  3. Capturar OCR de la ventana target.
  4. Invocar uitars_scaffold con prompt de estudio dirigido.
  5. Persistir resumen + elementos UI detectados en BrainMemory.
  6. Devolver informe estructurado.

Uso programático:
    from core.eidos_app_study import study
    result = study("telegram")

CLI:
    python3 -m core.eidos_app_study --app telegram
    python3 -m core.eidos_app_study --app n8n --url http://localhost:5678
    python3 -m core.eidos_app_study --self-test
"""
from __future__ import annotations

import os
import sys
import time
import json
import subprocess
import logging
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

log = logging.getLogger("eidos.app_study")

# ── Catálogo de apps conocidas ───────────────────────────────────────────────

KNOWN_APPS: dict = {
    "telegram": {
        "binary": "telegram-desktop",
        "wm_class_re": r"telegram-desktop|TelegramDesktop",
        "purpose": "Cliente de mensajería Telegram (chats, canales, bots, llamadas)",
        "alternative_method": (
            "Bot API directa via @eidos_aibot (token en ~/.eidos/.env): "
            "https://api.telegram.org/bot<TOKEN>/getMe — más rápido que GUI."
        ),
    },
    "n8n": {
        "binary": None,  # web app
        "url": "http://localhost:5678",
        "wm_class_re": r"chromium|firefox",
        "purpose": "Workflow automation low-code (HTTP, schedule, integrations)",
        "alternative_method": (
            "REST API directa: GET http://localhost:5678/rest/workflows. "
            "Browse workflows en $EIDOS_SOURCE_ROOT/docs/n8n_workflows/workflows/."
        ),
    },
    "vscode": {
        "binary": "code",
        "wm_class_re": r"code\.Code|VSCode",
        "purpose": "Editor con extensiones (EIDOS extension instalada)",
        "alternative_method": "code --help; code --list-extensions",
    },
    "chromium": {
        "binary": "chromium",
        "wm_class_re": r"chromium\.Chromium",
        "purpose": "Browser Chromium — DEFAULT de EIDOS para webs (S65d). Mejor que Firefox-ESR por no tener extensiones EIDOS que activen anti-bot.",
        "alternative_method": "chromium --headless --dump-dom URL; o curl con User-Agent realista",
    },
    "github_openclaw": {
        "binary": "chromium",
        "url": "https://github.com/openclaw",
        "wm_class_re": r"chromium\.Chromium",
        "purpose": "Organización OpenClaw en GitHub (ClawColony, ClawRouter, ClawHub, etc.) — fuente de los Claws integrados en EIDOS",
        "alternative_method": "gh repo list openclaw; curl https://api.github.com/orgs/openclaw/repos",
    },
    "n8n_docs": {
        "binary": "chromium",
        "url": "https://docs.n8n.io/",
        "wm_class_re": r"chromium\.Chromium",
        "purpose": "Documentación n8n — workflow automation. Tu n8n local está en :5678 (eidos-n8n Docker)",
        "alternative_method": "curl https://docs.n8n.io/llms.txt; ver $EIDOS_SOURCE_ROOT/docs/n8n_workflows/",
    },
    "firefox": {
        "binary": "firefox-esr",
        "wm_class_re": r"firefox-esr|Navigator",
        "purpose": "Browser Firefox-ESR (perfil principal con cookies)",
        "alternative_method": "curl con --user-agent del SER o firefox_session.py",
    },
    "konsole": {
        "binary": "konsole",
        "wm_class_re": r"konsole|Konsole|yakuake",
        "purpose": "Terminal Konsole — SER navega por Kali Linux, ejecuta comandos, scripts, y opencode en esta terminal",
        "alternative_method": "whoami; pwd; ls; ps aux — patrones de terminal observados por eidos_observer",
    },
    "thunar": {
        "binary": "thunar",
        "wm_class_re": r"thunar|Thunar",
        "purpose": "Gestor de archivos Thunar — SER navega archivos, scripts, y proyectos visualmente",
        "alternative_method": "ls -la ~/EIDOS; tree ~/EIDOS --dirsfirst -L 2",
    },
    "kali_terminal": {
        "binary": None,
        "wm_class_re": r"konsole|Konsole|yakuake|terminator|gnome-terminal",
        "purpose": "Terminal Kali Linux — entorno principal de SER para comandos, desarrollo, y gestion EIDOS",
        "alternative_method": "SER usa principalmente: ls, cd, cat, grep, python3, nmap, curl, ps, find, nohup",
    },
    "blender": {
        "binary": os.environ.get("EIDOS_BLENDER_BIN", "blender"),
        "wm_class_re": r"blender|Blender",
        "purpose": "Suite de modelado 3D, animación, renderizado (Cycles/Eevee), compositing, VFX, scripting Python. Versión 5.1.2 instalada standalone",
        "alternative_method": (
            "Scripting headless: $EIDOS_BLENDER_BIN --background --python script.py "
            "--render-frame 1 --render-output /tmp/render.png. "
            "API: bpy (context, data, ops). bpy.ops.mesh.primitive_cube_add() para crear objetos. "
            "Más en the Blender installation templates_py directory"
        ),
    },
}


# ── Helpers de ventana ──────────────────────────────────────────────────────

def _find_window_by_class(class_re: str) -> Optional[tuple[str, str]]:
    """Devuelve (wid, titulo) de la primera ventana cuyo cls match regex."""
    import re
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    try:
        r = subprocess.run(
            ["wmctrl", "-lx"], capture_output=True, text=True, timeout=3, env=env,
        )
        pat = re.compile(class_re, re.IGNORECASE)
        for line in r.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            wid, cls, title = parts[0], parts[2], parts[4]
            if pat.search(cls):
                return (wid, title)
    except Exception as e:
        log.warning("find_window: %s", e)
    return None


def _launch_binary(binary: str, timeout_s: float = 8.0,
                   wm_class_re: str = "") -> Optional[str]:
    """Lanza el binario y espera a que aparezca su ventana. Devuelve WID."""
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    try:
        subprocess.Popen(
            [binary], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.warning("binario no encontrado: %s", binary)
        return None
    if not wm_class_re:
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.6)
        found = _find_window_by_class(wm_class_re)
        if found:
            return found[0]
    return None


# ── Estudio principal ───────────────────────────────────────────────────────

STUDY_PROMPT_TEMPLATE = (
    "App: {name}. {purpose}. {task}\n"
    "INSTRUCCIONES (sigue al pie de la letra):\n"
    "- Lee los elementos UI listados (refs e0, e1, ...).\n"
    "- Cuenta y resume en ESPAÑOL.\n"
    "- En el paso 1, ya escribe Thought + Action = finished(content='...') "
    "con tu informe DIRECTO. NO esperes más pasos.\n"
    "Formato del finished('...') OBLIGATORIO:\n"
    "  CONTACTOS_DIRECTOS: N (lista de los 5 primeros nombres)\n"
    "  GRUPOS: N (lista nombres)\n"
    "  CANALES: N (lista nombres con icono megáfono o badge especial)\n"
    "  OBSERVACIONES: (1-2 frases sobre lo que ves)\n"
    "Si no puedes contar, da tu mejor estimación."
)


def study(app_name: str, custom_purpose: Optional[str] = None,
          max_steps: int = 4, dry_run: bool = False,
          task: str = "Identifica los elementos UI principales y resume.") -> dict:
    """EIDOS estudia una app. Devuelve dict con resumen + node_id en brain."""
    t0 = time.time()
    res: dict = {
        "app": app_name,
        "status": "pending",
        "node_id": None,
        "elapsed_s": 0.0,
    }

    cfg = KNOWN_APPS.get(app_name.lower())
    if not cfg:
        res["status"] = "unknown_app"
        res["error"] = (
            f"App '{app_name}' no en catálogo. Conocidas: {list(KNOWN_APPS.keys())}. "
            "Edita core/eidos_app_study.py:KNOWN_APPS para añadirla."
        )
        return res

    purpose = custom_purpose or cfg.get("purpose", "(propósito no documentado)")
    res["purpose"] = purpose
    res["alternative_method"] = cfg.get("alternative_method", "")

    # 1. Localizar o lanzar
    wid_title = _find_window_by_class(cfg["wm_class_re"])
    if not wid_title and cfg.get("binary"):
        log.info("ventana no existe → lanzando %s", cfg["binary"])
        wid = _launch_binary(cfg["binary"], timeout_s=8, wm_class_re=cfg["wm_class_re"])
        if wid:
            wid_title = _find_window_by_class(cfg["wm_class_re"])
    if not wid_title and cfg.get("url"):
        # app web → abrir en Chromium si no hay ventana
        url = cfg["url"]
        log.info("app web → abriendo URL %s en chromium", url)
        try:
            subprocess.Popen(
                ["chromium", "--new-window", url],
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass
        time.sleep(5)
        wid_title = _find_window_by_class(cfg["wm_class_re"])

    if not wid_title:
        res["status"] = "no_window"
        res["error"] = f"no se pudo localizar/lanzar ventana de {app_name}"
        res["elapsed_s"] = round(time.time() - t0, 2)
        return res

    wid, title = wid_title
    res["wid"] = wid
    res["title"] = title

    # 2. Activar la ventana
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    subprocess.run(["wmctrl", "-ia", wid], capture_output=True, timeout=3, env=env)
    time.sleep(1)

    # 3. Invocar scaffold uitars (OCR) — EIDOS razona, NO Claude
    try:
        from core.uitars_scaffold import UITarsScaffold, Status
    except Exception as e:
        res["status"] = "scaffold_import_error"
        res["error"] = str(e)
        res["elapsed_s"] = round(time.time() - t0, 2)
        return res

    instruction = STUDY_PROMPT_TEMPLATE.format(
        name=app_name, purpose=purpose, task=task,
    )
    try:
        scaff = UITarsScaffold(mode="gui", vision_backend="ocr", dry_run=dry_run)
        run_res = scaff.run(instruction, max_steps=max_steps)
        res["scaffold_status"] = run_res.status.value if hasattr(run_res, "status") else "?"
        res["steps"] = [
            {
                "n": getattr(s, "n", i + 1),
                "thought": (getattr(s, "thought", "") or "")[:200],
                "action_type": getattr(s, "action_type", ""),
                "obs": (getattr(s, "observation", "") or "")[:200],
            }
            for i, s in enumerate(getattr(run_res, "steps", [])[:max_steps])
        ]
        # El scaffold devuelve summary y a veces answer (de finished())
        res["summary"] = (
            getattr(run_res, "summary", lambda: "")()[:3000]
            if callable(getattr(run_res, "summary", None)) else ""
        )
        res["answer"] = (getattr(run_res, "answer", "") or "")[:2000]
    except Exception as e:
        res["status"] = "scaffold_run_error"
        res["error"] = str(e)
        res["elapsed_s"] = round(time.time() - t0, 2)
        return res

    # 4. Persistir en BrainMemory
    try:
        from core.brain_memory import BrainMemory
        b = BrainMemory()
        content_lines = [
            f"app_study: {app_name} (estudiado por EIDOS via scaffold OCR sin VLM, {time.strftime('%Y-%m-%d %H:%M')})",
            f"Propósito: {purpose}",
            f"WID/Title: {wid} / {title}",
            f"Método alternativo recomendado: {res['alternative_method']}",
            f"Scaffold status: {res.get('scaffold_status')}",
            "",
            "=== Razonamiento de EIDOS ===",
        ]
        for s in res.get("steps", []):
            content_lines.append(f"[{s['n']}] {s['thought'][:300]}")
            content_lines.append(f"     → {s['action_type']} | obs: {s['obs'][:120]}")
        if res.get("answer"):
            content_lines.append("")
            content_lines.append("=== Informe final (finished) ===")
            content_lines.append(res["answer"])

        nid = b.remember(
            content="\n".join(content_lines),
            tags=["app_study", f"app={app_name}", "studied_by_eidos", "uitars_ocr"],
            importance=0.85,
            category="app_study",
        )
        res["node_id"] = nid
        res["status"] = "ok"
    except Exception as e:
        res["status"] = "brain_persist_error"
        res["error"] = str(e)

    res["elapsed_s"] = round(time.time() - t0, 2)
    return res


# ── CLI / Self-test ─────────────────────────────────────────────────────────

def _self_test() -> dict:
    """Smoke: import + lookup catálogo + ventana search SIN ejecutar scaffold."""
    checks = []
    checks.append(("catalog has telegram", "telegram" in KNOWN_APPS))
    checks.append(("catalog has n8n", "n8n" in KNOWN_APPS))
    found = _find_window_by_class(r"konsole")
    checks.append(("find_window konsole returns something",
                   found is not None and len(found) == 2))
    try:
        from core.uitars_scaffold import UITarsScaffold
        checks.append(("uitars_scaffold importa", True))
    except Exception as e:
        checks.append((f"uitars_scaffold import error: {e}", False))
    try:
        from core.brain_memory import BrainMemory
        b = BrainMemory()
        checks.append(("BrainMemory init", True))
    except Exception as e:
        checks.append((f"BrainMemory error: {e}", False))

    passed = sum(1 for _, ok in checks if ok)
    return {
        "self_test": "PASS" if passed == len(checks) else "FAIL",
        "passed": passed,
        "total": len(checks),
        "checks": [{"check": c, "ok": ok} for c, ok in checks],
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [app_study] %(message)s")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", help="Nombre de app en el catálogo (telegram, n8n, ...)")
    ap.add_argument("--purpose", help="Custom purpose override")
    ap.add_argument("--max-steps", type=int, default=4)
    ap.add_argument("--task", default="Identifica los elementos UI principales y resume.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(_self_test(), ensure_ascii=False, indent=2))
        sys.exit(0)
    if not args.app:
        ap.print_help()
        sys.exit(2)
    res = study(args.app, custom_purpose=args.purpose, task=args.task,
                max_steps=args.max_steps, dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
