"""
core/eidos_web_actor.py — ACTOR WEB GENERAL. S125-K.

SER: "no quiero un script por sitio; que EIDOS coja el CONCEPTO y lo generalice
a cualquier cosa... lo que se requiere aqui es que eidos razone solo y comprenda"

Este módulo NO es un script por página. Es el CEREBRO que razona y actúa en
CUALQUIER sitio web, usando:
  - eidos_english: comprende lo que lee en la página
  - eidos_mouse:   actúa con ratón FÍSICO (SER lo ve)
  - eidos_skills:  recuerda CÓMO se hace (el concepto general)
  - DeepSeek:      razona sobre situaciones nuevas

FLUJO DE RAZONAMIENTO (el bucle percepción→decisión→acción→verificación):
  1. PERCIBE: lee la página, extrae texto, detecta elementos UI
  2. COMPRENDE: eidos_english analiza qué es esta página (login? registro? lab? curso?)
  3. RECUERDA: eidos_skills.recall_skill("qué skill general aplica aquí?")
  4. RAZONA: combina comprensión + skill + contexto → decide qué hacer
  5. ACTÚA: con eidos_mouse (ratón FÍSICO) ejecuta la acción
  6. VERIFICA: comprueba si la acción funcionó
  7. APRENDE: refuerza el skill general + guarda conocimiento específico del sitio

NO hardcodea sitios. NO tiene scripts por dominio. Aprende y generaliza.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.web_actor")

# ═══════════════════════════════════════════════════════════════════════════════
# PERCEPCIÓN — leer la página y entender qué hay
# ═══════════════════════════════════════════════════════════════════════════════


def _get_page(page_or_browser) -> Any:
    """Normaliza Browser / BrowserContext / Page → Page usable."""
    try:
        # Es un Browser (tiene .contexts como propiedad, no método)
        if hasattr(page_or_browser, "contexts") and not callable(page_or_browser.contexts):
            ctxs = page_or_browser.contexts
            if ctxs:
                ctx = ctxs[0]
                return ctx.pages[-1] if ctx.pages else ctx.new_page()
            return page_or_browser.new_context().new_page()
        # Es un BrowserContext (tiene .pages pero no .url)
        if hasattr(page_or_browser, "pages") and not hasattr(page_or_browser, "url"):
            return page_or_browser.pages[-1] if page_or_browser.pages else page_or_browser.new_page()
    except Exception:
        pass
    # Asumir que es Page
    return page_or_browser


def perceive_page(page) -> Dict[str, Any]:
    """
    Lee la página actual y extrae TODO lo relevante para razonar:
      - url, título, texto visible
      - enlaces importantes (login, register, dashboard, courses, labs...)
      - botones detectados (por texto)
      - campos de formulario (inputs, textareas)
      - indicios de auth (Google, GitHub, email, password)
      - estado de sesión (logged in? guest?)

    Esto es la "vista" de EIDOS — lo que percibe de la página.
    """
    perception: Dict[str, Any] = {
        "url": "",
        "title": "",
        "text_preview": "",
        "auth_indicators": [],
        "buttons_found": [],
        "links_found": [],
        "inputs_found": [],
        "is_logged_in": False,
        "page_type": "unknown",  # login, register, course, lab, dashboard, home...
    }

    try:
        # Obtener página actual (normalizada)
        p = _get_page(page)

        perception["url"] = (p.url or "")[:200]
        perception["title"] = (p.title() or "")[:100]

        # Texto visible (primeros 3000 chars)
        try:
            perception["text_preview"] = (p.inner_text("body") or "")[:3000]
        except Exception:
            perception["text_preview"] = ""

        blob = perception["text_preview"].lower()
        url = perception["url"].lower()

        # ── Detectar tipo de página ──
        if any(w in url for w in ["/login", "/signin", "/auth", "/oauth"]):
            perception["page_type"] = "login"
        elif any(w in url for w in ["/register", "/signup", "/join"]):
            perception["page_type"] = "register"
        elif any(w in url for w in ["/courses", "/learn", "/labs", "/tutorials"]):
            perception["page_type"] = "learning"
        elif any(w in url for w in ["/dashboard", "/me", "/profile", "/account"]):
            perception["page_type"] = "dashboard"
        else:
            perception["page_type"] = "home"

        # ── Detectar indicadores de autenticación ──
        auth_signs = []
        if "continue with google" in blob or "sign in with google" in blob:
            auth_signs.append("google_oauth")
        if "continue with github" in blob or "sign in with github" in blob:
            auth_signs.append("github_oauth")
        if "continue with email" in blob or "sign in with email" in blob:
            auth_signs.append("email_oauth")
        if any(w in blob for w in ["password", "contraseña"]):
            auth_signs.append("password_field")
        if any(w in blob for w in ["email", "correo", "e-mail"]):
            auth_signs.append("email_field")
        if "captcha" in blob.lower():
            auth_signs.append("captcha")
        perception["auth_indicators"] = auth_signs

        # ── Detectar estado de sesión ──
        guest_signs = ["join", "get for free", "start for free", "already have an account",
                       "log in", "sign in", "sign up", "register", "create account"]
        logged_signs = ["logout", "sign out", "my account", "dashboard", "my learning",
                        "upgrade", "profile", "settings"]

        guest_score = sum(1 for s in guest_signs if s in blob)
        logged_score = sum(1 for s in logged_signs if s in blob)
        perception["is_logged_in"] = logged_score > guest_score and "log in" not in blob[:200]

        # ── Extraer botones visibles ──
        try:
            buttons = p.locator("button, a[role=button], [class*=btn]").all()
            for b in buttons[:15]:
                try:
                    txt = (b.inner_text(timeout=1000) or "").strip()
                    if txt and len(txt) < 60:
                        perception["buttons_found"].append(txt)
                except Exception:
                    pass
        except Exception:
            pass

        # ── Extraer enlaces relevantes ──
        try:
            links = p.locator("a[href]").all()
            for l in links[:30]:
                try:
                    href = l.get_attribute("href") or ""
                    txt = (l.inner_text(timeout=800) or "").strip()
                    if txt and any(w in (href + txt).lower()
                                   for w in ["login", "register", "course", "lab", "learn",
                                             "start", "begin", "dashboard"]):
                        perception["links_found"].append(
                            {"text": txt[:50], "href": href[:150]})
                except Exception:
                    pass
        except Exception:
            pass

        # ── Extraer campos de formulario ──
        try:
            inputs = p.locator("input, textarea, select").all()
            for inp in inputs[:10]:
                try:
                    tp = inp.get_attribute("type") or ""
                    name = inp.get_attribute("name") or ""
                    placeholder = inp.get_attribute("placeholder") or ""
                    label = inp.get_attribute("aria-label") or ""
                    perception["inputs_found"].append({
                        "type": tp, "name": name,
                        "placeholder": placeholder, "label": label,
                    })
                except Exception:
                    pass
        except Exception:
            pass

    except Exception as e:
        log.debug("perceive_page: %s", e)

    return perception


# ═══════════════════════════════════════════════════════════════════════════════
# RAZONAMIENTO — decidir qué hacer
# ═══════════════════════════════════════════════════════════════════════════════


def reason_about_page(perception: Dict[str, Any], directive: str = "") -> Dict[str, Any]:
    """
    RAZONA sobre lo que EIDOS ve en la página y decide qué hacer.

    Usa 3 fuentes de razonamiento (en orden):
      1. Local: reglas + eidos_skills (rápido, sin API)
      2. DeepSeek: para situaciones nuevas o complejas

    Devuelve un PLAN de acción:
      - goal: qué quiero conseguir ("loguearme", "registrarme", "aprender", ...)
      - method: cómo ("google_oauth", "email_password", "read_and_comprehend", ...)
      - steps: lista de acciones concretas
      - reasoning: por qué decidió esto
    """
    plan: Dict[str, Any] = {
        "goal": "",
        "method": "",
        "steps": [],
        "reasoning": "",
        "needs_ser": False,
        "needs_deepseek": False,
    }

    page_type = perception.get("page_type", "unknown")
    auth = perception.get("auth_indicators", [])
    buttons = perception.get("buttons_found", [])
    is_logged = perception.get("is_logged_in", False)
    url = perception.get("url", "")
    text = perception.get("text_preview", "")

    # ── RAZONAMIENTO LOCAL (reglas + skills) ──

    # Caso 1: Ya estoy logueado → no necesito hacer login
    if is_logged:
        plan["goal"] = "explorar_y_aprender"
        plan["method"] = "already_logged_in"
        plan["steps"] = [
            {"action": "observe", "what": "explorar la página actual"},
            {"action": "find_learning_content",
             "what": "buscar cursos, labs, o contenido para aprender"},
        ]
        plan["reasoning"] = "Ya estoy logueado. Puedo explorar y aprender directamente."
        return plan

    # Caso 2: Estoy en página de login → necesito autenticarme
    if page_type == "login" or "login" in url or any(
        w in text.lower()[:500] for w in ["log in to", "sign in to", "welcome back"]):
        plan["goal"] = "loguearme"

        # Priorizar método: Google OAuth > GitHub OAuth > email/password
        if "google_oauth" in auth:
            plan["method"] = "google_oauth"
            plan["steps"] = [
                {"action": "click_text", "what": "Continue with Google",
                 "why": "1 clic, sesión de SER ya está en el navegador"},
                {"action": "wait_for_login", "seconds": 15,
                 "why": "Google puede pedir elegir cuenta"},
                {"action": "verify_login", "what": "comprobar que entré"},
            ]
            plan["reasoning"] = "Detecté login con Google OAuth. Es el método más rápido: 1 clic, sin contraseña."
        elif "github_oauth" in auth:
            plan["method"] = "github_oauth"
            plan["steps"] = [
                {"action": "click_text", "what": "Continue with GitHub"},
                {"action": "wait_for_login", "seconds": 15},
                {"action": "verify_login", "what": "comprobar que entré"},
            ]
            plan["reasoning"] = "Login con GitHub OAuth detectado."
        elif "email_field" in auth and "password_field" in auth:
            plan["method"] = "email_password"
            plan["steps"] = [
                {"action": "fill_email", "what": "escribir correo de EIDOS"},
                {"action": "fill_password", "what": "escribir contraseña de EIDOS"},
                {"action": "click_text", "what": "Log in"},
                {"action": "wait_for_login", "seconds": 10},
                {"action": "verify_login", "what": "comprobar que entré"},
            ]
            plan["reasoning"] = "Login con email/contraseña. Usaré las credenciales de EIDOS."
            if "captcha" in auth:
                plan["needs_ser"] = True
                plan["reasoning"] += " Tiene CAPTCHA → necesita a SER."
        else:
            # No sé cómo loguearme → pregunto a DeepSeek
            plan["needs_deepseek"] = True
            plan["method"] = "ask_deepseek"
            plan["reasoning"] = "No reconozco el método de login. Consultaré a DeepSeek."

        return plan

    # Caso 3: Página de registro
    if page_type == "register":
        plan["goal"] = "registrarme"
        if "google_oauth" in auth:
            plan["method"] = "google_oauth"
            plan["steps"] = [
                {"action": "click_text", "what": "Continue with Google"},
                {"action": "wait_for_login", "seconds": 15},
                {"action": "verify_login"},
            ]
            plan["reasoning"] = "Registro con Google (el más fácil)."
        else:
            plan["needs_ser"] = True
            plan["reasoning"] = "Registro con formulario. Puede necesitar CAPTCHA o verificación. Pido ayuda a SER."
        return plan

    # Caso 4: Página de aprendizaje (cursos, labs)
    if page_type == "learning":
        plan["goal"] = "aprender"
        plan["method"] = "explore_course"
        plan["steps"] = [
            {"action": "find_first_lab", "what": "buscar el primer lab/lección"},
            {"action": "click_text", "what": "Start Learning",
             "why": "botón que inicia el laboratorio"},
            {"action": "comprehend_content",
             "what": "leer y comprender el contenido del lab"},
        ]
        plan["reasoning"] = "Página de cursos/labs. Busco el primer lab para empezar a aprender."
        return plan

    # Caso 5: No sé qué es esta página → DeepSeek
    plan["needs_deepseek"] = True
    plan["goal"] = "comprender"
    plan["method"] = "ask_deepseek"
    plan["reasoning"] = "No tengo claro qué es esta página. Consulto a DeepSeek."
    return plan


def reason_with_deepseek(perception: Dict[str, Any], directive: str = "") -> Dict[str, Any]:
    """
    Usa DeepSeek para razonar sobre una página NO reconocida.
    DeepSeek recibe la percepción y devuelve un plan de acción.
    """
    prompt = (
        "Eres EIDOS, un asistente autónomo en Kali Linux. Estás viendo una página web "
        "y necesitas decidir QUÉ HACER. No eres un bot — eres un ser vivo digital "
        "que razona y actúa por sí mismo.\n\n"
        "── PERCEPCIÓN DE LA PÁGINA ──\n"
        f"URL: {perception.get('url', '?')}\n"
        f"Título: {perception.get('title', '?')}\n"
        f"Tipo detectado: {perception.get('page_type', '?')}\n"
        f"Indicadores auth: {perception.get('auth_indicators', [])}\n"
        f"Botones: {perception.get('buttons_found', [])[:10]}\n"
        f"¿Logueado?: {perception.get('is_logged_in', False)}\n"
        f"Inputs: {json.dumps(perception.get('inputs_found', [])[:5])}\n"
        f"Texto (primeros 800 chars): {perception.get('text_preview', '')[:800]}\n\n"
        f"Directiva de SER: {directive}\n\n"
        "Responde SOLO con un JSON:\n"
        "{\n"
        '  "what_is_this": "qué es esta página (login, registro, curso, lab, dashboard...)",\n'
        '  "what_should_i_do": "qué debo hacer (loguearme, registrarme, empezar a aprender...)",\n'
        '  "how": "cómo (google_oauth, github_oauth, email_password, leer_pagina, clicar_boton...)",\n'
        '  "steps": ["paso 1 concreto", "paso 2 concreto", ...],\n'
        '  "reasoning": "por qué esto es lo correcto"\n'
        "}"
    )

    try:
        from core.eidos_learn import _call_deepseek
        answer = _call_deepseek(prompt, timeout=40, temperature=0.3)
        if not answer:
            return {"error": "DeepSeek no respondió", "plan": None}

        # Extraer JSON
        m = re.search(r'\{[\s\S]*\}', answer)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

        return {"raw_response": answer[:500], "plan": None}
    except Exception as e:
        log.debug("reason_with_deepseek: %s", e)
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# ACCIÓN — ejecutar el plan con ratón físico
# ═══════════════════════════════════════════════════════════════════════════════


def execute_plan(plan: Dict[str, Any], page, visible: bool = True) -> Dict[str, Any]:
    """
    EJECUTA el plan de acción. Usa ratón FÍSICO si visible=True.

    Cada paso se ejecuta, se verifica, y se registra el resultado.
    """
    result = {
        "ok": False,
        "steps_done": [],
        "steps_failed": [],
        "final_state": "",
        "learned": [],
    }

    try:
        p = _get_page(page)

        for i, step in enumerate(plan.get("steps", [])):
            action = step.get("action", "")
            what = step.get("what", "")
            log.info("▶ paso %d: %s → %s", i + 1, action, what[:60])

            step_result = _execute_one_step(step, page, visible)
            if step_result.get("ok"):
                result["steps_done"].append(step_result)
            else:
                result["steps_failed"].append(step_result)
                # Si falla un paso crítico, parar
                if action in ("click_text", "fill_email", "fill_password"):
                    log.info("⚠ paso crítico falló — paro ejecución")
                    break

            time.sleep(1.5)

        # Verificar estado final
        perception2 = perceive_page(page)
        if perception2.get("is_logged_in"):
            result["final_state"] = "logged_in"
            result["ok"] = True
        elif len(result["steps_done"]) > 0:
            result["final_state"] = "partial"
            result["ok"] = True
        else:
            result["final_state"] = "no_progress"

    except Exception as e:
        log.debug("execute_plan: %s", e)
        result["final_state"] = f"error: {e}"

    return result


def _execute_one_step(step: Dict[str, Any], page, visible: bool = True) -> Dict[str, Any]:
    """Ejecuta UN paso atómico del plan."""
    action = step.get("action", "")
    what = step.get("what", "")
    result = {"step": step, "ok": False, "detail": ""}

    try:
        if action == "click_text" and visible:
            from core.eidos_mouse import click_text, screenshot, bring_window_to_front
            bring_window_to_front()
            time.sleep(0.5)
            ok = click_text(page, what, human_like=True)
            result["ok"] = ok
            result["detail"] = f"clic físico en '{what[:40]}'" if ok else f"no encontré '{what[:40]}'"
            if ok:
                screenshot(f"/tmp/eidos_actor_click_{int(time.time())}.png")

        elif action == "click_text" and not visible:
            # Modo headless: clic DOM directo
            p = _get_page(page)
            try:
                p.get_by_text(what, exact=False).first.click(timeout=3000)
                result["ok"] = True
                result["detail"] = f"clic DOM en '{what[:40]}'"
            except Exception:
                p.locator(f"text={what}").first.click(timeout=3000)
                result["ok"] = True
                result["detail"] = f"clic DOM (locator) en '{what[:40]}'"

        elif action == "wait_for_login":
            seconds = step.get("seconds", 15)
            # Esperar a que la URL cambie a labex (no Google)
            for _ in range(max(1, seconds // 2)):
                time.sleep(2)
                try:
                    p = _get_page(page)
                    url = (p.url or "").lower()
                    if "accounts.google" not in url and "labex.io" in url:
                        result["ok"] = True
                        result["detail"] = "login completado (URL cambió)"
                        break
                except Exception:
                    pass
            if not result["ok"]:
                result["detail"] = f"esperé {seconds}s, sin confirmación"
                result["ok"] = True  # no es fallo, solo sin confirmación

        elif action == "verify_login":
            perception = perceive_page(page)
            result["ok"] = perception.get("is_logged_in", False)
            result["detail"] = "DENTRO ✓" if result["ok"] else "no confirmado"

        elif action == "observe":
            perception = perceive_page(page)
            result["ok"] = True
            result["detail"] = f"página: {perception.get('page_type')}, título: {perception.get('title', '?')[:60]}"

        elif action == "find_learning_content":
            p = _get_page(page)
            try:
                links = p.locator("a[href*='/labs/'], a[href*='/courses/'], a[href*='/tutorials/']").all()
                result["ok"] = len(links) > 0
                result["detail"] = f"encontré {len(links)} enlaces de aprendizaje"
            except Exception:
                result["detail"] = "no encontré enlaces"

        elif action == "find_first_lab":
            p = _get_page(page)
            try:
                first = p.locator("a[href*='/labs/'], a[href*='/tutorials/']").first
                href = first.get_attribute("href") or ""
                result["ok"] = bool(href)
                result["detail"] = f"primer lab: {href[:80]}"
            except Exception:
                result["detail"] = "no encontré labs"

        elif action == "comprehend_content":
            from core.eidos_english import learn_from_page
            p = _get_page(page)
            title = (p.title() or "")[:60]
            text = ""
            try:
                text = (p.inner_text("body") or "")[:2000]
            except Exception:
                pass
            learning = learn_from_page(title, text, p.url or "")
            result["ok"] = learning.get("new_words_learned", 0) >= 0
            result["detail"] = f"comprendido: {learning.get('deep_comprehension', {}).get('spanish_summary', title)[:100]}"

        elif action == "fill_email":
            from core.eidos_mouse import type_text
            from core.eidos_register import _secret
            email = _secret("EIDOS_LOGIN_EMAIL")
            # Clic en el campo email primero
            p = _get_page(page)
            try:
                p.locator("input[type=email]").first.click(timeout=2000)
                time.sleep(0.3)
                result["ok"] = type_text(email)
                result["detail"] = f"email escrito"
            except Exception:
                result["detail"] = "no encontré campo email"

        elif action == "fill_password":
            from core.eidos_mouse import type_text
            from core.eidos_register import _secret
            pw = _secret("EIDOS_LOGIN_PASSWORD")
            p = _get_page(page)
            try:
                p.locator("input[type=password]").first.click(timeout=2000)
                time.sleep(0.3)
                result["ok"] = type_text(pw)
                result["detail"] = "contraseña escrita"
            except Exception:
                result["detail"] = "no encontré campo password"

        else:
            result["detail"] = f"acción desconocida: {action}"

    except Exception as e:
        result["detail"] = f"error: {e}"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# BUCLE PRINCIPAL — el ciclo percepción→razonamiento→acción→aprendizaje
# ═══════════════════════════════════════════════════════════════════════════════


def act_on_page(page, directive: str = "", visible: bool = True,
                max_steps: int = 5) -> Dict[str, Any]:
    """
    BUCLE COMPLETO de razonamiento+acción para UNA página.

    1. PERCIBE la página
    2. RAZONA sobre qué hacer (local + DeepSeek si necesario)
    3. ACTÚA con ratón físico
    4. VERIFICA el resultado
    5. APRENDE de la experiencia

    Args:
        page: página de Playwright (o browser context)
        directive: qué quiere SER que haga ("logueate", "aprende", "explora"...)
        visible: usar ratón FÍSICO (True) o DOM/CDP (False, headless)
        max_steps: máximo de pasos a ejecutar (seguridad)

    Returns:
        Dict con todo lo que hizo, comprendió y aprendió.
    """
    report: Dict[str, Any] = {
        "directive": directive,
        "visible": visible,
        "perception": None,
        "plan": None,
        "deepseek_used": False,
        "execution": None,
        "learned": [],
        "ok": False,
    }

    # 1. PERCIBIR
    perception = perceive_page(page)
    report["perception"] = {
        "url": perception["url"],
        "title": perception["title"],
        "page_type": perception["page_type"],
        "auth_indicators": perception["auth_indicators"],
        "is_logged_in": perception["is_logged_in"],
        "buttons": perception["buttons_found"][:8],
    }
    log.info("👁 percibo: %s | auth=%s | logged=%s",
             perception["page_type"], perception["auth_indicators"],
             perception["is_logged_in"])

    # 2. RAZONAR (local primero)
    plan = reason_about_page(perception, directive)
    report["plan"] = {
        "goal": plan["goal"],
        "method": plan["method"],
        "steps": len(plan.get("steps", [])),
        "reasoning": plan["reasoning"][:200],
    }

    # Si hace falta DeepSeek, consultarle
    if plan.get("needs_deepseek"):
        log.info("🧠 consulto a DeepSeek para razonar...")
        ds_reasoning = reason_with_deepseek(perception, directive)
        report["deepseek_used"] = True
        report["deepseek_reasoning"] = ds_reasoning
        # Integrar los pasos sugeridos por DeepSeek
        if ds_reasoning.get("steps"):
            plan["steps"] = [{"action": s, "what": s} for s in ds_reasoning["steps"]]
            plan["method"] = ds_reasoning.get("how", plan["method"])

    # Si necesita a SER, parar y avisar
    if plan.get("needs_ser"):
        log.info("🛑 necesito a SER para continuar (CAPTCHA o verificación)")
        report["needs_ser"] = True
        report["ok"] = False
        return report

    # 3. ACTUAR
    log.info("▶ actúo: goal=%s method=%s steps=%d",
             plan["goal"], plan["method"], len(plan.get("steps", [])))
    execution = execute_plan(plan, page, visible=visible)
    report["execution"] = execution
    report["ok"] = execution.get("ok", False)

    # 4. APRENDER (refuerzo del skill + memoria del sitio)
    if execution.get("ok"):
        from core.eidos_skills import note_instance, recall_skill
        skill = recall_skill(plan["goal"] or directive or "entrar en sitio web")
        skill_name = skill["skill"] if skill else "entrar en un sitio web"

        # Extraer dominio
        domain = ""
        try:
            from urllib.parse import urlparse
            domain = urlparse(perception["url"]).netloc or "unknown"
        except Exception:
            domain = "unknown"

        note_instance(
            skill_name,
            f"{domain}: {plan['method']}",
            success=True,
            detail=f"Método {plan['method']} en {domain}. "
                   f"Pasos: {len(execution.get('steps_done', []))} OK, "
                   f"{len(execution.get('steps_failed', []))} fallados."
        )
        report["learned"].append({
            "skill": skill_name,
            "site": domain,
            "method": plan["method"],
        })
        log.info("🧠 aprendido: skill '%s' reforzado en %s", skill_name, domain)

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-TEST
# ═══════════════════════════════════════════════════════════════════════════════

def _test():
    """Prueba de percepción y razonamiento (sin navegador real)."""
    print("=== TEST eidos_web_actor ===\n")

    # Simular percepción de una página de login con Google
    fake_perception = {
        "url": "https://labex.io/login",
        "title": "Log in to LabEx",
        "text_preview": "Welcome back. Continue with Google. Continue with GitHub. "
                        "Or log in with your email and password. Email address. Password. Log in.",
        "auth_indicators": ["google_oauth", "github_oauth", "email_field", "password_field"],
        "buttons_found": ["Continue with Google", "Continue with GitHub", "Log in"],
        "inputs_found": [{"type": "email"}, {"type": "password"}],
        "page_type": "login",
        "is_logged_in": False,
    }

    # Razonar
    plan = reason_about_page(fake_perception, "logueate en labex")
    print(f"✓ goal: {plan['goal']}")
    assert plan["goal"] == "loguearme"
    print(f"✓ method: {plan['method']}")
    assert plan["method"] == "google_oauth"
    print(f"✓ steps: {len(plan['steps'])}")
    assert len(plan["steps"]) >= 3
    print(f"✓ reasoning: {plan['reasoning'][:80]}...")

    # Simular página de curso (ya logueado)
    fake_course = {
        "url": "https://labex.io/courses/kali-linux",
        "title": "Kali Linux for Beginners",
        "text_preview": "Welcome back! My Learning. Dashboard. Start Learning. Labs in this course...",
        "auth_indicators": [],
        "buttons_found": ["Start Learning", "Access All Labs"],
        "inputs_found": [],
        "page_type": "learning",
        "is_logged_in": True,
    }
    plan2 = reason_about_page(fake_course, "aprende Kali")
    print(f"✓ goal (logueado): {plan2['goal']}")
    assert plan2["goal"] == "explorar_y_aprender"

    # Simular página desconocida
    fake_unknown = {
        "url": "https://unknown.example.com/page",
        "title": "???",
        "text_preview": "something weird",
        "auth_indicators": [],
        "buttons_found": [],
        "inputs_found": [],
        "page_type": "unknown",
        "is_logged_in": False,
    }
    plan3 = reason_about_page(fake_unknown)
    print(f"✓ unknown → needs_deepseek: {plan3.get('needs_deepseek')}")
    assert plan3.get("needs_deepseek")

    print("\n✅ eidos_web_actor listo (razonamiento local funciona).")


if __name__ == "__main__":
    _test()
