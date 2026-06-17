#!/usr/bin/env python3
"""
bin/labex_learn.py — EIDOS aprende en labex de verdad, paso a paso, SIN cerrar el navegador. S125.

SER: "está logueado; entra en Learn desde el PRIMER laboratorio, recorre CADA sublink, y que lo
ENTIENDA, razone y comprenda. NO cierres el chromium mientras lo usa (lo usará para más cosas)."

- Chromium PERSISTENTE: se lanza con start_new_session (sobrevive a este script) y NO se cierra.
  Reconecta por CDP si ya está abierto. Perfil persistente con la sesión Google de SER (login 1 clic).
- Login con Google (si hace falta).
- Va a la zona de aprendizaje, ABRE EL PRIMER LAB, y recorre los labs/sublinks en orden: por cada uno
  lee el contenido y lo COMPRENDE con su IA (resumen + conclusión propia + por qué importa), lo guarda
  al grafo (source 'labex_learn' con la conclusión de EIDOS), captura, y avanza.

Uso: python3 bin/labex_learn.py [n_labs]   (por defecto 6)
"""
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, "/home/ser/EIDOS")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("labex.learn")

PORT = 9333
PROFILE = os.path.expanduser("~/.eidos/labex_profile")
CHROME = shutil.which("chromium") or shutil.which("chromium-browser") or "/usr/bin/chromium"
ENV = {**os.environ, "DISPLAY": ":0"}
COURSE = "https://labex.io/courses/kali-linux-for-beginners"


def _cdp_up():
    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/json/version", timeout=2); return True
    except Exception:
        return False


def _seed_profile():
    if Path(PROFILE, "Default", "Cookies").exists():
        return
    src = Path(os.path.expanduser("~/.config/chromium"))
    (Path(PROFILE) / "Default").mkdir(parents=True, exist_ok=True)
    for item in ["Cookies", "Cookies-journal", "Login Data", "Preferences",
                 "Secure Preferences", "Network", "Local Storage", "Session Storage"]:
        s = src / "Default" / item; d = Path(PROFILE) / "Default" / item
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


def _launch_persistent():
    _seed_profile()
    subprocess.Popen(
        [CHROME, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check", "--start-maximized",
         "--disable-session-crashed-bubble", "--restore-last-session=false"],
        env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
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


def _logged_in(pg):
    url = (pg.url or "").lower()
    if "labex.io" not in url:
        return False
    try:
        blob = (pg.inner_text("body") or "").lower()[:2500]
    except Exception:
        blob = ""
    guest = ("join labex", "continue with google", "continue with github",
             "get for free", "already have an account")
    return not any(g in blob for g in guest)


def _comprehend(title, text):
    """EIDOS COMPRENDE: su IA resume + saca conclusión propia + por qué importa. Devuelve la conclusión."""
    prompt = (f"Eres EIDOS estudiando en labex. Lee este contenido del lab «{title}» y responde en "
              f"español, breve: 1) ¿Qué enseña? 2) TU conclusión propia. 3) ¿Por qué importa en "
              f"Linux/seguridad? Contenido:\n{text[:1800]}")
    for fn in ("ask_llm", "_call_deepseek", "_call_groq"):
        try:
            from core import eidos_learn
            f = getattr(eidos_learn, fn, None)
            if not f:
                continue
            r = f(prompt)
            if isinstance(r, (tuple, list)):
                r = r[0] if r else ""
            r = str(r).strip()
            if len(r) >= 40:
                return r
        except Exception as e:
            log.debug("comprehend %s: %s", fn, e)
    return ""


def _store(title, conclusion, url):
    try:
        from core.db import get_conn
        c = get_conn(os.path.expanduser("~/.eidos/evolution_brain.db"), timeout=20)
        c.execute("INSERT INTO knowledge_nodes (id,concept,definition,category,confidence,source,created_at) "
                  "VALUES (?,?,?,?,?,?,?)",
                  ("ll_" + uuid.uuid4().hex[:12], f"labex aprendido: {title}"[:120],
                   (conclusion + f" [fuente: {url}]")[:1500], "labex_learn", 0.82,
                   "labex_learn", time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
    except Exception as e:
        log.debug("store: %s", e)


def main():
    n_labs = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    if not _cdp_up():
        log.info("lanzo chromium PERSISTENTE (no se cerrará)")
        if not _launch_persistent():
            print("ERROR: no pude lanzar chromium"); return
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    b = pw.chromium.connect_over_cdp(f"http://localhost:{PORT}")
    ctx = b.contexts[0] if b.contexts else b.new_context()
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        # 1) LOGIN con Google si hace falta
        pg.goto("https://labex.io/login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4); _front()
        if not _logged_in(pg):
            log.info("entro con Google…")
            for t in ("Continue with Google", "Sign in with Google", "Continuar con Google"):
                try:
                    pg.get_by_text(t, exact=False).first.click(timeout=3000); break
                except Exception:
                    continue
            for _ in range(20):
                time.sleep(5); _front()
                if _logged_in(pg):
                    break
        log.info("login: %s", "DENTRO ✓" if _logged_in(pg) else "NO (sigo igual)")

        # 2) Ir al curso de principiante y ABRIR EL PRIMER LAB (desde el principio)
        pg.goto(COURSE, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4); _front()
        try:
            pg.screenshot(path="/tmp/labex_learn_course.png")
        except Exception:
            pass
        # recoger los enlaces de los labs del curso (en orden)
        lab_links = []
        try:
            for h in pg.locator("a[href*='/labs/'], a[href*='/tutorials/']").all():
                href = h.get_attribute("href") or ""
                if href and href not in lab_links:
                    lab_links.append(href if href.startswith("http") else "https://labex.io" + href)
        except Exception as e:
            log.info("recoger labs: %s", e)
        log.info("labs encontrados en el curso: %d", len(lab_links))

        # 3) Recorrer CADA lab desde el primero, comprendiendo
        learned = 0
        targets = lab_links[:n_labs] if lab_links else [COURSE]
        for i, url in enumerate(targets, 1):
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5); _front()
                # entrar al lab si hay botón
                if i == 1:
                    for t in ("Start Learning", "Start Lab", "Start"):
                        try:
                            pg.get_by_text(t, exact=False).first.click(timeout=2500); time.sleep(6); _front(); break
                        except Exception:
                            continue
                title = (pg.title() or url)[:70]
                text = (pg.inner_text("body") or "")[:2000]
                try:
                    pg.screenshot(path=f"/tmp/labex_learn_{i}.png")
                except Exception:
                    pass
                concl = _comprehend(title, text)
                if concl:
                    _store(title, concl, url)
                    learned += 1
                    log.info("✅ lab %d/%d COMPRENDIDO: %s\n   → %s", i, len(targets), title[:50], concl[:160])
                else:
                    log.info("lab %d: leí pero no logré comprender (texto %d chars)", i, len(text))
                time.sleep(4)   # SER lo ve
            except Exception as e:
                log.info("lab %d (%s): %s", i, url[:50], e)
        print(f"RESULTADO: login={'ok' if _logged_in(pg) else 'no'} | labs comprendidos={learned}/{len(targets)}")
        log.info("🪟 DEJO CHROMIUM ABIERTO (no lo cierro). EIDOS lo seguirá usando.")
    finally:
        # NO cerrar chromium: solo soltar el cliente playwright.
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
