#!/usr/bin/env python3
"""
bin/eidos_labex_dolab.py — EIDOS ANALIZA desde dónde empezar y HACE un lab de labex. S125.

SER: "que analice dónde proseguir los cursos/labs desde cero" + "que los haga".
Flujo (visible, con SER mirando): login solo → ir a cursos → elegir el curso de principiante
(desde cero) → abrir su primer lab → arrancarlo → por cada paso: leer la instrucción, extraer
el comando que el PROPIO lab indica (bloques de código), escribirlo en el terminal del lab,
avanzar. Aprende cada paso al grafo. HONESTO: capturas + reporta hasta dónde llega.

Seguridad: solo ejecuta comandos que vienen EN las instrucciones del lab (no inventa), en la
VM del lab (entorno educativo aislado de labex). Sin trucos ni evasión.
"""
import logging
import os
import subprocess
import sys
from pathlib import Path
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("labex.dolab")


def _front():
    """Trae la ventana del navegador al escritorio ACTUAL de SER, al frente y maximizada,
    para que la VEA (KDE tiene 2 escritorios; podría abrir en el otro)."""
    env = {**os.environ, "DISPLAY": ":0"}
    for arg in ("chromium.Chromium", "Chromium-browser.Chromium-browser", "chromium.chromium"):
        try:
            subprocess.run(["wmctrl", "-x", "-R", arg], timeout=3, env=env,
                           capture_output=True)
        except Exception:
            pass
    for t in ("LabEx", "labex", "Chromium"):
        try:
            subprocess.run(["wmctrl", "-a", t], timeout=3, env=env, capture_output=True)
        except Exception:
            pass
    # maximizar la ventana activa
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowsize", "100%", "100%"],
                       timeout=3, env=env, capture_output=True)
    except Exception:
        pass


def _learn(title, text, src="labex_dolab"):
    try:
        from core.db import get_conn
        c = get_conn(os.path.expanduser("~/.eidos/evolution_brain.db"), timeout=20)
        c.execute(
            "INSERT INTO knowledge_nodes (id,concept,definition,category,confidence,source,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("ldo_" + uuid.uuid4().hex[:12], title[:120], text[:1500], "labex_lab", 0.8, src,
             time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
    except Exception as e:
        log.info("learn: %s", e)


def _logged_in(a):
    """FIABLE: NO logueado si la página muestra registro/login (Join LabEx / Continue with
    Google/GitHub / Get for Free / Already have an account). Logueado solo si estamos en labex
    y NO aparece ninguno de esos CTA de invitado."""
    url = a.url().lower()
    if "labex.io" not in url:
        return False   # en accounts.google.com / github → aún no
    blob = a.read_text(2500).lower()
    guest = ("join labex", "continue with google", "continue with github", "continue with email",
             "get for free", "start for free", "already have an account", "log in to")
    if any(g in blob for g in guest):
        return False
    return True


def _extract_cmd(a):
    """Saca el comando que el lab indica (bloques <code>/<pre>). Solo lo del lab, no inventa."""
    for sel in ["pre code", "code", "pre", ".markdown code", "[class*=code]"]:
        try:
            els = a.page.locator(sel)
            n = min(els.count(), 6)
            for i in range(n):
                t = (els.nth(i).inner_text(timeout=2000) or "").strip()
                # comando plausible de una línea (no un bloque de salida largo)
                if t and 2 <= len(t) <= 120 and "\n" not in t and not t.startswith(("$", "#", "//")):
                    return t
        except Exception:
            continue
    return ""


def _type_in_terminal(a, cmd):
    """Escribe el comando en el terminal del lab (xterm en el navegador) y pulsa Enter."""
    for sel in [".xterm-helper-textarea", ".xterm", ".terminal", "[class*=terminal]",
                "[class*=xterm]", "canvas"]:
        try:
            a.page.locator(sel).first.click(timeout=3000)
            a.page.keyboard.type(cmd, delay=30)
            a.page.keyboard.press("Enter")
            return True
        except Exception:
            continue
    return False


def main():
    from core.eidos_register import _secret
    from core.eidos_web_agent import get_singleton
    email = _secret("EIDOS_LOGIN_EMAIL"); pw = _secret("EIDOS_LOGIN_PASSWORD")
    a = get_singleton()
    if not a.connect(headless=False):
        print("RESULTADO: no abrió navegador"); return
    did = []
    try:
        # 1) login solo
        a.goto("https://labex.io/login"); time.sleep(4)
        _front(); log.info("🪟 ventana al frente en labex/login — MÍRALA"); time.sleep(3)
        if not _logged_in(a):
            log.info("entro con GOOGLE (clic en el botón Continue with Google)")
            clicked = ""
            for t in ("Continue with Google", "Sign in with Google", "Log in with Google",
                      "Continuar con Google", "Iniciar sesión con Google"):
                if a.find_click(t, 3500):
                    clicked = t; break
            if not clicked:
                for sel in ["[aria-label*=google i]", "[class*=google i]", "img[alt*=google i]"]:
                    try:
                        a.page.locator(sel).first.click(timeout=2500); clicked = sel; break
                    except Exception:
                        continue
            log.info("🖱 login con Google: %s", clicked or "NO encontré el botón de Google")
            log.info("⏳ SER: si Google pide elegir tu cuenta o iniciar sesión, HAZLO en pantalla (espero 100s)")
            for _ in range(20):
                time.sleep(5); _front()
                if _logged_in(a):
                    break
        log.info("login real: %s", "DENTRO ✓" if _logged_in(a) else "NO entró aún")
        # PASO A PASO: si SER solo quiere ver el LOGIN, paramos aquí (LABEX_LOGIN_ONLY=1)
        if os.environ.get("LABEX_LOGIN_ONLY") == "1":
            log.info("🪟 LOGIN_ONLY: dejo la ventana abierta 120s para que lo veas; no sigo al lab.")
            _front(); time.sleep(120)
            print(f"RESULTADO: login={'DENTRO' if _logged_in(a) else 'fuera'} (solo login)")
            return

        # 2) ANALIZAR desde dónde empezar (desde cero): curso de principiante
        a.goto("https://labex.io/courses"); time.sleep(3)
        try: a.page.screenshot(path="/tmp/eidos_labex_analyze.png")
        except Exception: pass
        # elegir un curso de principiante (Linux / beginner / fundamentals)
        opened = False
        for txt in ["Linux for Beginners", "Linux Basics", "Beginner", "Fundamentals", "Introduction"]:
            if a.find_click(txt, 3000):
                opened = True; log.info("📚 curso elegido (desde cero): %s", txt); break
        if not opened:
            for sel in ["a[href*='/courses/']", "a[href*='/labs/']"]:
                try:
                    a.page.locator(sel).first.click(timeout=4000); opened = True; break
                except Exception: continue
        time.sleep(4)
        log.info("curso/url: %s", a.url()[:90])

        # 3) arrancar el primer lab — el botón REAL es "Start Learning" (no un "Start" genérico)
        for t in ("Start Learning", "Start Lab", "Access All Labs", "Start for Free",
                  "Empezar a aprender", "Comenzar", "Launch", "Begin"):
            if a.find_click(t, 3500):
                log.info("▶ arrancando lab: %s", t); break
        time.sleep(5)
        # el lab abre a menudo en PESTAÑA NUEVA → cambiar a ella para ver el terminal
        try:
            ctx = a.page.context
            if len(ctx.pages) > 1:
                a.page = ctx.pages[-1]
                log.info("↪ cambié a la pestaña del lab: %s", a.page.url[:70])
        except Exception as e:
            log.info("cambio de pestaña: %s", e)
        log.info("⏳ esperando 30s a que cargue el entorno del lab (VM + terminal)...")
        time.sleep(30); _front()
        try: a.page.screenshot(path="/tmp/eidos_labex_lab_start.png")
        except Exception: pass

        # 4) HACER los pasos: leer instrucción → comando del lab → terminal → avanzar
        for step in range(3):
            time.sleep(3)
            instr = a.read_text(1800)
            cmd = _extract_cmd(a)
            log.info("paso %d | instrucción %d chars | comando del lab: %r", step + 1, len(instr or ""), cmd[:60])
            _learn(f"labex lab paso {step+1}: {a.page.title()[:50]}", f"{instr[:600]} || CMD: {cmd}")
            ok = False
            if cmd:
                ok = _type_in_terminal(a, cmd)
                log.info("   escribí en terminal: %s", "sí" if ok else "no encontré terminal")
                did.append({"paso": step + 1, "cmd": cmd, "ejecutado": ok})
                time.sleep(4)
            try: a.page.screenshot(path=f"/tmp/eidos_labex_step{step+1}.png")
            except Exception: pass
            adv = False
            for t in ("Next", "Continue", "Siguiente", "Continuar", "Next Step", "Check"):
                if a.find_click(t, 2500): adv = True; break
            if not adv:
                log.info("no hay 'siguiente' visible — paro"); break
        print(f"RESULTADO: login={'ok' if _logged_in(a) else 'dudoso'} | pasos intentados={len(did)} | {did}")
    except Exception as e:
        print(f"RESULTADO: error → {e}")
    finally:
        log.info("🪟 dejo la ventana ABIERTA 90s para que la veas; ciérrala tú o espera.")
        _front()
        time.sleep(90)  # SER lo ve bien antes de cerrar
        try: a.close()
        except Exception: pass


if __name__ == "__main__":
    main()
