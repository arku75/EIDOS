"""
core/eidos_register.py — EIDOS se registra/loguea SOLO en plataformas. S125.

SER: "deberá registrarse en labex.io y demás donde él más quiera; si tiene su correo
que lo utilice". EIDOS usa SU correo (EIDOS_LOGIN_EMAIL/PASSWORD de secrets.env) + el
WebAgent (Playwright, que HEREDA las cookies/sesión de SER, incl. Gmail) para:
  1. ir a la página de registro/login,
  2. rellenar email + contraseña (detección genérica de campos),
  3. enviar,
  4. si hay verificación por correo → buscarla en Gmail, sacar el enlace y abrirlo,
  5. comprobar y REPORTAR a SER (buzón). HONESTO: si no puede (captcha/formulario raro),
     se lo dice a SER (action_needed) en vez de fingir éxito.

Reusa: eidos_web_agent (navegador+Gmail), ser_inbox (avisar a SER). No reinventa.
Seguro por defecto headless; visible=True usa el navegador con la sesión de SER.

CLI:
    python3 -m core.eidos_register labex.io [--visible]
    python3 -m core.eidos_register https://sitio.com/signup [--visible]
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("eidos.register")

_SECRETS = Path.home() / ".eidos" / "secrets.env"

# Perfiles por sitio (best-effort; el genérico cubre el resto por tipo de campo)
PLATFORMS: Dict[str, Dict[str, str]] = {
    "labex.io": {
        "signup": "https://labex.io/register",
        "login":  "https://labex.io/login",
        "ok_hint": "dashboard",      # texto/URL que indica sesión iniciada
        "mail_q":  "from:labex OR subject:(verify OR confirm OR labex)",
    },
}


def _secret(name: str) -> str:
    """Lee una credencial de ~/.eidos/secrets.env (o del entorno)."""
    val = os.environ.get(name, "")
    if val:
        return val
    try:
        for line in _SECRETS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _profile(platform: str) -> Dict[str, str]:
    """Perfil del sitio. Si no hay uno conocido, lo deriva del dominio/URL."""
    key = platform.lower().replace("https://", "").replace("http://", "").split("/")[0]
    if key in PLATFORMS:
        return PLATFORMS[key]
    base = platform if platform.startswith("http") else f"https://{key}"
    return {"signup": base.rstrip("/") + "/register", "login": base.rstrip("/") + "/login",
            "ok_hint": "", "mail_q": f"subject:(verify OR confirm) OR from:{key}"}


def _fill_first(page, selectors: List[str], value: str) -> bool:
    for sel in selectors:
        try:
            page.locator(sel).first.fill(value, timeout=4000)
            return True
        except Exception:
            continue
    return False


_EMAIL_SEL = ["input[type=email]", "input[name*=email i]", "input[id*=email i]",
              "input[autocomplete=email]", "input[name*=user i]"]
_PASS_SEL = ["input[type=password]", "input[name*=pass i]", "input[id*=pass i]"]
_SUBMIT_TXT = ["Sign up", "Register", "Create account", "Crear cuenta", "Regístrate",
               "Registrarse", "Continue", "Continuar", "Sign in", "Log in", "Entrar"]


def _click_submit(agent) -> bool:
    for t in _SUBMIT_TXT:
        if agent.find_click(t, timeout=3000):
            return True
    for sel in ["button[type=submit]", "input[type=submit]", "button:has-text('')"]:
        try:
            agent.page.locator(sel).first.click(timeout=3000)
            return True
        except Exception:
            continue
    return False


def _grab_verify_link(agent, mail_q: str) -> Optional[str]:
    """Busca el correo de verificación en Gmail (sesión de SER) y extrae el enlace."""
    try:
        if not agent.gmail_search(mail_q):
            return None
        if not agent.gmail_open_first():
            return None
        body = agent.gmail_get_body() or ""
        m = re.search(r'https?://\S*(?:verify|confirm|activate|token|signup)\S*', body, re.I)
        if not m:
            m = re.search(r'https?://\S+', body)
        return m.group(0).rstrip(').,>"\'') if m else None
    except Exception as e:
        log.debug("_grab_verify_link: %s", e)
        return None


def _tell_ser(title: str, summary: str, need_help: bool) -> None:
    try:
        from core.ser_inbox import get_ser_inbox
        get_ser_inbox().add("alert_critical" if need_help else "achievement",
                            title[:120], summary[:400], action_needed=need_help)
    except Exception as e:
        log.debug("_tell_ser: %s", e)


# ── EL CONCEPTO (lo importante: EIDOS generaliza, no un script por sitio) ──────
def learn_registration_concept() -> None:
    """Delega en el mecanismo GENERAL de skills (core.eidos_skills): 'registrarse o entrar en un
    sitio web' es UN skill transferible más, no un parche de labex. Así generaliza a cualquier sitio."""
    try:
        from core.eidos_skills import learn_skill
        learn_skill(
            "registrarse o entrar en un sitio web",
            "Ir a /register o /login; localizar el correo por su TIPO (input[type=email]) y escribirlo; "
            "la contraseña (input[type=password]) y escribirla; confirmar contraseña si la piden; pulsar "
            "enviar (Sign up/Register/Crear cuenta/Entrar); si hay CAPTCHA pedir a SER (no se elude); si "
            "piden verificación, abrir el email en Gmail y pulsar el enlace; confirmar logout/mi cuenta. "
            "Igual en TODOS los sitios; solo cambian las URLs.", domain="web")
    except Exception as e:
        log.debug("learn_registration_concept: %s", e)


def _access_conn():
    from core.db import get_conn
    c = get_conn(Path.home() / ".eidos" / "evolution_brain.db", timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS site_access(
        site TEXT, signup_url TEXT, login_url TEXT, email_used TEXT,
        method TEXT, success INTEGER, ts REAL)""")
    return c


def remember_site_access(site, signup_url, login_url, email, method, success=True) -> None:
    """Memoria por sitio (queryable): 'entré en <site> así'. Refuerza el concepto general."""
    try:
        c = _access_conn()
        c.execute("INSERT INTO site_access(site,signup_url,login_url,email_used,method,success,ts) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (str(site)[:80], str(signup_url)[:200], str(login_url)[:200],
                   str(email)[:120], str(method)[:40], 1 if success else 0, time.time()))
        c.commit()
        # registrar la INSTANCIA en el mecanismo general de skills (refuerza el concepto)
        try:
            from core.eidos_skills import note_instance
            note_instance("registrarse o entrar en un sitio web", site, bool(success),
                          detail=f"{method} via {signup_url}")
        except Exception:
            learn_registration_concept()
    except Exception as e:
        log.debug("remember_site_access: %s", e)


def recall_site_access(site: str):
    """¿Cómo entré antes en este sitio? (para ir más rápido)."""
    try:
        c = _access_conn()
        r = c.execute("SELECT signup_url,login_url,method FROM site_access WHERE site=? AND success=1 "
                      "ORDER BY ts DESC LIMIT 1", (str(site)[:80],)).fetchone()
        return {"signup_url": r[0], "login_url": r[1], "method": r[2]} if r else None
    except Exception:
        return None


def register(platform: str = "labex.io", visible: bool = False,
             do_login_first: bool = True) -> Dict[str, Any]:
    """EIDOS se da de alta (o entra) en `platform` con su propio correo. Best-effort + honesto."""
    email = _secret("EIDOS_LOGIN_EMAIL")
    password = _secret("EIDOS_LOGIN_PASSWORD")
    if not email or not password:
        msg = "no tengo correo/clave en secrets (EIDOS_LOGIN_EMAIL/PASSWORD) → no puedo registrarme"
        _tell_ser(f"Registro {platform}", msg, need_help=True)
        return {"ok": False, "reason": msg}

    prof = _profile(platform)
    try:
        from core.eidos_web_agent import get_singleton
        agent = get_singleton()
    except Exception as e:
        return {"ok": False, "reason": f"sin navegador: {e}"}

    out: Dict[str, Any] = {"platform": platform, "email": email}
    try:
        if not agent.connect(headless=not visible):
            return {"ok": False, "reason": "no pude abrir el navegador"}

        # 1) intentar LOGIN primero (quizá ya tiene cuenta / sesión heredada de SER)
        if do_login_first:
            agent.goto(prof["login"])
            _fill_first(agent.page, _EMAIL_SEL, email)
            _fill_first(agent.page, _PASS_SEL, password)
            _click_submit(agent)
            time.sleep(3)
            if prof.get("ok_hint") and prof["ok_hint"].lower() in (agent.url() + " " + agent.read_text(800)).lower():
                _tell_ser(f"Entré en {platform}", f"Ya tenía cuenta; sesión iniciada con {email}.", False)
                return {**out, "ok": True, "action": "login", "summary": f"sesión iniciada en {platform}"}

        # 2) REGISTRO
        agent.goto(prof["signup"])
        got_email = _fill_first(agent.page, _EMAIL_SEL, email)
        _fill_first(agent.page, _PASS_SEL, password)
        # algunos formularios piden confirmar la contraseña (segundo campo password)
        try:
            pws = agent.page.locator("input[type=password]")
            if pws.count() > 1:
                pws.nth(1).fill(password, timeout=3000)
        except Exception:
            pass
        if not got_email:
            msg = f"no encontré el formulario de registro en {prof['signup']} (¿captcha/JS?) → ayúdame, SER"
            _tell_ser(f"Registro {platform}", msg, need_help=True)
            return {**out, "ok": False, "reason": msg}
        _click_submit(agent)
        time.sleep(4)

        # 3) verificación por correo (Gmail con la sesión de SER)
        link = _grab_verify_link(agent, prof["mail_q"])
        if link:
            agent.goto(link)
            time.sleep(2)
            out["verified"] = True

        # 4) comprobar
        final = (agent.url() + " " + agent.read_text(800)).lower()
        ok = bool(prof.get("ok_hint")) and prof["ok_hint"].lower() in final
        ok = ok or "logout" in final or "sign out" in final or "cerrar sesión" in final
        if ok:
            _tell_ser(f"Me registré en {platform} 🎉",
                      f"Cuenta creada/activada con {email}. Listo para aprender ahí.", False)
            return {**out, "ok": True, "action": "register", "summary": f"registrado en {platform}"}
        msg = (f"intenté registrarme en {platform} con {email} pero no pude confirmar "
               f"(quizá captcha o verificación manual). ¿Me ayudas a terminarlo?")
        _tell_ser(f"Registro {platform} a medias", msg, need_help=True)
        return {**out, "ok": False, "reason": msg, "verify_link": link}
    except Exception as e:
        return {**out, "ok": False, "reason": f"error: {e}"}
    finally:
        try:
            agent.close()
        except Exception:
            pass


def _looks_logged_in(agent, prof) -> bool:
    blob = (agent.url() + " " + agent.read_text(1200)).lower()
    if prof.get("ok_hint") and prof["ok_hint"] in blob:
        return True
    return any(k in blob for k in ("logout", "sign out", "cerrar sesión", "log out",
                                   "/dashboard", "my account", "mi cuenta", "/labs", "profile"))


def register_interactive(platform: str = "labex.io", wait_secs: int = 300) -> Dict[str, Any]:
    """Camino LEGÍTIMO (usuario real): abre el navegador REAL VISIBLE con la sesión de SER,
    prerellena el correo de EIDOS, y ESPERA a que SER resuelva el captcha / complete el alta.
    Detecta el login y reporta. NO es evasión: es un navegador real con un humano resolviendo
    el captcha. No cierra hasta éxito o timeout (SER necesita tiempo)."""
    email = _secret("EIDOS_LOGIN_EMAIL")
    password = _secret("EIDOS_LOGIN_PASSWORD")
    prof = _profile(platform)
    try:
        from core.eidos_web_agent import get_singleton
        agent = get_singleton()
    except Exception as e:
        return {"ok": False, "reason": f"sin navegador: {e}"}
    try:
        if not agent.connect(headless=False):      # VISIBLE
            return {"ok": False, "reason": "no pude abrir el navegador visible"}
        # 1) ¿ya logueado por la cookie heredada de SER?
        agent.goto(prof["login"])
        if _looks_logged_in(agent, prof):
            _tell_ser(f"Ya estaba dentro de {platform}",
                      f"Heredé tu sesión; no hizo falta captcha. Listo para aprender ahí.", False)
            return {"ok": True, "action": "session-inherited", "platform": platform}
        # 2) prerellenar el correo/clave de EIDOS donde pueda (SER completa el resto)
        agent.goto(prof["signup"])
        _fill_first(agent.page, _EMAIL_SEL, email)
        _fill_first(agent.page, _PASS_SEL, password)
        log.info("🧑‍💻 labex abierto y precargado con %s — SER, resuelve el captcha/regístrate; espero %ds",
                 email, wait_secs)
        _tell_ser(f"Te dejé {platform} abierto",
                  f"Prerellené tu correo {email}. Resuelve el captcha y dale a registrarte/entrar; "
                  f"yo detecto cuando estés dentro y sigo desde ahí.", True)
        # 3) ESPERAR a que SER lo complete (poll, sin cerrar)
        deadline = time.time() + wait_secs
        while time.time() < deadline:
            time.sleep(5)
            try:
                if _looks_logged_in(agent, prof):
                    _tell_ser(f"¡Dentro de {platform}! 🎉",
                              "SER completó el alta/login; ya tengo sesión para aprender ahí.", False)
                    return {"ok": True, "action": "registered-with-ser", "platform": platform}
            except Exception:
                continue
        return {"ok": False, "reason": "no detecté el login dentro del tiempo (¿quedó a medias?)",
                "platform": platform}
    except Exception as e:
        return {"ok": False, "reason": f"error: {e}", "platform": platform}
    finally:
        try:
            agent.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys, json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = sys.argv[1:]
    platform = next((a for a in args if not a.startswith("--")), "labex.io")
    if "--interactive" in args or "--ser" in args:
        wait = 300
        if "--wait" in args:
            try: wait = int(args[args.index("--wait") + 1])
            except Exception: pass
        print(json.dumps(register_interactive(platform, wait_secs=wait), ensure_ascii=False, indent=2))
    else:
        visible = "--visible" in args
        print(json.dumps(register(platform, visible=visible), ensure_ascii=False, indent=2))
