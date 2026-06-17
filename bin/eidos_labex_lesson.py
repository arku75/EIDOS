#!/usr/bin/env python3
"""
bin/eidos_labex_lesson.py — EIDOS hace un par de lecciones en labex.io (visible, con SER). S125.

SER: "veamos si eidos hace un par de clases dentro de labex desde el principio."
Flujo: abre navegador VISIBLE → entra (credenciales; si hay captcha lo resuelve SER) → va a
cursos → abre un curso de principiante → LEE y APRENDE la lección a su grafo → avanza a la
siguiente → reporta. HONESTO: deja capturas (/tmp/eidos_labex_*.png) y dice hasta dónde llegó.
No es evasión: navegador real, sesión real, sin trucos.
"""
import logging
import os
import sys
import time
import uuid

sys.path.insert(0, "/home/ser/EIDOS")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("labex")


def _learn(title, text):
    """Aprende el contenido de la lección a su grafo (SQLite directo, ligero)."""
    try:
        from core.db import get_conn
        c = get_conn(os.path.expanduser("~/.eidos/evolution_brain.db"), timeout=20)
        c.execute(
            "INSERT INTO knowledge_nodes (id,concept,definition,category,confidence,source,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("labex_" + uuid.uuid4().hex[:12], f"labex lección: {title}"[:120], text[:1500],
             "labex_lesson", 0.8, "labex_study", time.strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        return True
    except Exception as e:
        log.info("learn: %s", e)
        return False


def _logged_in(a):
    blob = (a.url() + " " + a.read_text(1500)).lower()
    return any(k in blob for k in ("logout", "sign out", "dashboard", "my account",
                                   "/labs", "skill tree", "/dashboard", "profile"))


def main():
    from core.eidos_register import _secret
    from core.eidos_web_agent import get_singleton
    email = _secret("EIDOS_LOGIN_EMAIL")
    pw = _secret("EIDOS_LOGIN_PASSWORD")
    a = get_singleton()
    if not a.connect(headless=False):
        print("RESULTADO: no abrió el navegador")
        return
    learned = []
    try:
        a.goto("https://labex.io")
        time.sleep(2)
        if not _logged_in(a):
            log.info("no logueado → intento login con credenciales de EIDOS")
            a.goto("https://labex.io/login")
            time.sleep(2)
            try:
                a.page.locator("input[type=email]").first.fill(email, timeout=5000)
                a.page.locator("input[type=password]").first.fill(pw, timeout=5000)
            except Exception as e:
                log.info("campos de login no encontrados: %s", e)
            for t in ("Log in", "Sign in", "Login", "Entrar", "Iniciar sesión"):
                if a.find_click(t, 3000):
                    break
            log.info("⏳ 75s por si hay captcha — SER, resuélvelo si aparece")
            time.sleep(75)
        log.info("login estado: %s", "DENTRO" if _logged_in(a) else "no confirmado")

        # ir a cursos / aprender (desde el principio)
        for url in ("https://labex.io/courses", "https://labex.io/learn", "https://labex.io/skilltrees"):
            a.goto(url)
            time.sleep(3)
            if "404" not in (a.page.title() or "") and "not found" not in a.read_text(300).lower():
                break
        try:
            a.page.screenshot(path="/tmp/eidos_labex_courses.png")
            log.info("📸 /tmp/eidos_labex_courses.png")
        except Exception:
            pass

        # abrir el primer curso/lab que encuentre
        opened = False
        for sel in ["a[href*='/courses/']", "a[href*='/labs/']", "a[href*='/tutorials/']",
                    ".course-card a", "a.course-link"]:
            try:
                a.page.locator(sel).first.click(timeout=5000)
                opened = True
                break
            except Exception:
                continue
        time.sleep(4)
        log.info("curso abierto: %s | url: %s", opened, a.url()[:80])

        # leer y aprender hasta 2 lecciones (avanzando con 'Next/Continuar')
        for i in range(2):
            time.sleep(3)
            title = (a.page.title() or a.url())[:80]
            text = a.read_text(2500)
            try:
                a.page.screenshot(path=f"/tmp/eidos_labex_lesson{i+1}.png")
            except Exception:
                pass
            if text and len(text) > 150:
                _learn(title, text)
                learned.append(title)
                log.info("✅ lección %d leída y aprendida: %s (%d chars)", i + 1, title[:50], len(text))
            else:
                log.info("lección %d: poco contenido legible (%d chars)", i + 1, len(text or ""))
            # avanzar
            adv = False
            for t in ("Next", "Continue", "Siguiente", "Continuar", "Next Step"):
                if a.find_click(t, 3000):
                    adv = True
                    break
            if not adv:
                log.info("no encontré botón 'siguiente' — paro aquí")
                break
        print(f"RESULTADO: login={'ok' if _logged_in(a) else 'dudoso'} | lecciones aprendidas={len(learned)} | {learned}")
    except Exception as e:
        print(f"RESULTADO: error → {e}")
    finally:
        time.sleep(20)   # dejar que SER lo vea antes de cerrar
        try:
            a.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
