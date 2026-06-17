#!/usr/bin/env python3
"""
bin/labex_step.py — Control PASO A PASO del navegador para labex (SER ve cada paso). S125.

SER quiere ir por pasos y VER todos. Este stepper lanza UN chromium VISIBLE y persistente
(con la sesión de Google de SER copiada, para login con 1 clic) y ejecuta UNA acción por
invocación, SIN cerrar el navegador entre pasos. Cada paso deja /tmp/labex_now.png.

Uso:
  python3 bin/labex_step.py open          # abre chromium visible en labex/login
  python3 bin/labex_step.py google        # clic en "Continue with Google"
  python3 bin/labex_step.py check         # ¿logueado? (url + estado) + captura
  python3 bin/labex_step.py courses       # ir a cursos y abrir uno de principiante
  python3 bin/labex_step.py startlab      # arrancar el lab
  python3 bin/labex_step.py read          # leer y aprender la página actual
  python3 bin/labex_step.py type 'CMD'    # escribir CMD en el terminal del lab
  python3 bin/labex_step.py next          # pulsar Siguiente/Continue
  python3 bin/labex_step.py shot          # solo captura + título/url
  python3 bin/labex_step.py close         # cerrar el navegador
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, "/home/ser/EIDOS")

PORT = 9333
PROFILE = os.path.expanduser("~/.eidos/labex_profile")
SHOT = "/tmp/labex_now.png"
CHROME = shutil.which("chromium") or shutil.which("chromium-browser") or "/usr/bin/chromium"
ENV = {**os.environ, "DISPLAY": ":0"}


def _cdp_up():
    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/json/version", timeout=2)
        return True
    except Exception:
        return False


def _seed_profile():
    """Copia la sesión de SER (cookies/login de Google) a un perfil persistente, 1 vez."""
    if Path(PROFILE, "Default", "Cookies").exists():
        return
    src = Path(os.path.expanduser("~/.config/chromium"))
    (Path(PROFILE) / "Default").mkdir(parents=True, exist_ok=True)
    for item in ["Cookies", "Cookies-journal", "Login Data", "Preferences",
                 "Secure Preferences", "Network", "Local Storage", "Session Storage"]:
        s = src / "Default" / item
        d = Path(PROFILE) / "Default" / item
        try:
            if s.is_dir():
                shutil.copytree(s, d, dirs_exist_ok=True)
            elif s.exists():
                shutil.copy2(s, d)
        except Exception:
            pass
    try:
        shutil.copy2(src / "Local State", Path(PROFILE) / "Local State")
    except Exception:
        pass


def _launch():
    _seed_profile()
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check", "--start-maximized",
         "--disable-session-crashed-bubble", "--restore-last-session=false"],
        env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    for _ in range(30):
        if _cdp_up():
            return True
        time.sleep(1)
    return False


def _front():
    for arg in ("chromium.Chromium", "Chromium-browser.Chromium-browser"):
        try:
            subprocess.run(["wmctrl", "-x", "-R", arg], env=ENV, capture_output=True, timeout=3)
        except Exception:
            pass


def _page():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = pw.chromium.connect_over_cdp(f"http://localhost:{PORT}")
    ctx = b.contexts[0] if b.contexts else b.new_context()
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    return pw, b, pg


def _logged_in(pg):
    url = (pg.url or "").lower()
    if "labex.io" not in url:
        return False
    try:
        blob = (pg.inner_text("body") or "").lower()[:2500]
    except Exception:
        blob = ""
    if "get for free" in blob or "start for free" in blob:
        return False
    return "logout" in blob or "my learning" in blob or "dashboard" in url or "log in" not in blob


def _shot(pg):
    try:
        pg.screenshot(path=SHOT)
    except Exception:
        pass


def _learn(title, text):
    try:
        from core.db import get_conn
        c = get_conn(os.path.expanduser("~/.eidos/evolution_brain.db"), timeout=20)
        c.execute("INSERT INTO knowledge_nodes (id,concept,definition,category,confidence,source,created_at) "
                  "VALUES (?,?,?,?,?,?,?)",
                  ("lx_" + uuid.uuid4().hex[:12], title[:120], text[:1500], "labex_lab", 0.8,
                   "labex_step", time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
    except Exception:
        pass


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "shot"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "open":
        if not _cdp_up():
            if not _launch():
                print("ERROR: no pude lanzar chromium"); return
        pw, b, pg = _page()
        pg.goto("https://labex.io/login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3); _front(); _shot(pg)
        print(f"OK open | url={pg.url[:70]} | logueado={_logged_in(pg)}")
        pw.stop(); return

    if not _cdp_up():
        print("ERROR: el navegador no está abierto. Ejecuta 'open' primero."); return
    pw, b, pg = _page()
    try:
        if action == "google":
            ok = False
            for t in ("Continue with Google", "Sign in with Google", "Log in with Google",
                      "Continuar con Google", "Iniciar sesión con Google", "Google"):
                try:
                    pg.get_by_text(t, exact=False).first.click(timeout=2500); ok = True; break
                except Exception:
                    continue
            if not ok:
                for sel in ["[aria-label*=google i]", "[class*=google i]", "img[alt*=google i]",
                            "button:has(img)"]:
                    try:
                        pg.locator(sel).first.click(timeout=2000); ok = True; break
                    except Exception:
                        continue
            time.sleep(3); _front(); _shot(pg)
            print(f"OK google | click={ok} | url={pg.url[:70]}")

        elif action == "check":
            time.sleep(1); _front(); _shot(pg)
            print(f"OK check | url={pg.url[:70]} | LOGUEADO={_logged_in(pg)}")

        elif action == "courses":
            pg.goto("https://labex.io/courses", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            opened = ""
            for t in ("Kali Linux for Beginners", "Linux for Beginners", "Linux Basics", "Beginner"):
                try:
                    pg.get_by_text(t, exact=False).first.click(timeout=2500); opened = t; break
                except Exception:
                    continue
            time.sleep(3); _front(); _shot(pg)
            print(f"OK courses | abierto={opened or '(primer enlace)'} | url={pg.url[:70]}")

        elif action == "startlab":
            ok = ""
            for t in ("Start Lab", "Start for Free", "Start", "Launch", "Comenzar", "Empezar", "Continue"):
                try:
                    pg.get_by_text(t, exact=False).first.click(timeout=2500); ok = t; break
                except Exception:
                    continue
            time.sleep(6); _front(); _shot(pg)
            print(f"OK startlab | pulsado={ok} | url={pg.url[:70]}")

        elif action == "read":
            txt = (pg.inner_text("body") or "")[:2200]
            _learn(f"labex: {pg.title()[:60]}", txt)
            _shot(pg)
            print(f"OK read | titulo={pg.title()[:50]} | chars={len(txt)}\n--- extracto ---\n{txt[:600]}")

        elif action == "type":
            typed = False
            for sel in [".xterm-helper-textarea", ".xterm", ".terminal", "[class*=terminal]", "canvas"]:
                try:
                    pg.locator(sel).first.click(timeout=2500)
                    pg.keyboard.type(arg, delay=40); pg.keyboard.press("Enter"); typed = True; break
                except Exception:
                    continue
            time.sleep(3); _front(); _shot(pg)
            print(f"OK type | cmd={arg!r} | escrito={typed}")

        elif action == "next":
            ok = ""
            for t in ("Next", "Continue", "Siguiente", "Continuar", "Next Step", "Check"):
                try:
                    pg.get_by_text(t, exact=False).first.click(timeout=2500); ok = t; break
                except Exception:
                    continue
            time.sleep(3); _front(); _shot(pg)
            print(f"OK next | pulsado={ok or 'no hallado'}")

        elif action == "shot":
            _front(); _shot(pg)
            print(f"OK shot | titulo={pg.title()[:50]} | url={pg.url[:70]} | logueado={_logged_in(pg)}")

        elif action == "close":
            for p in subprocess.run(["pgrep", "-f", f"remote-debugging-port={PORT}"],
                                    capture_output=True, text=True).stdout.split():
                try: os.kill(int(p), 9)
                except Exception: pass
            print("OK close")
    finally:
        try: pw.stop()
        except Exception: pass


if __name__ == "__main__":
    main()
