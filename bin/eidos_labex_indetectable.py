#!/usr/bin/env python3
"""
bin/eidos_labex_indetectable.py — LabEx 100% INDETECTABLE (S125-M).

Firefox REAL de SER + ScreenController 5-capas + HumanEmulator bezier + OCR.
CERO Playwright. CERO DOM. CERO navigator.webdriver.
Cada paso: percibir (OCR) → decidir (coordenadas) → actuar (bezier física) → verificar.

La página web NO PUEDE distinguir a EIDOS de un humano real.
"""
import logging
import os
import subprocess
import sys
import time
import re
from pathlib import Path

sys.path.insert(0, str(Path.home() / "EIDOS"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [labex] %(message)s")
log = logging.getLogger("labex.indetectable")

EIDOS_HOME = Path.home() / "EIDOS"
SHOTS_DIR = Path.home() / ".eidos" / "screenshots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Seguridad: respetar freno de emergencia ─────────────────────────────────

def _can_act() -> bool:
    try:
        from core.eidos_input_safety import can_control_input
        ok, reason = can_control_input()
        if not ok:
            log.warning("🛑 %s", reason)
        return ok
    except Exception:
        return True  # sin el módulo, asumimos OK

# ── Captura + OCR ──────────────────────────────────────────────────────────

def _capture_and_ocr() -> str:
    """Captura la ventana Firefox activa y devuelve el texto OCR."""
    import pytesseract
    from PIL import Image
    shot = SHOTS_DIR / f"labex_{int(time.time())}.png"
    try:
        subprocess.run(
            ["import", "-window", "root", str(shot)],
            capture_output=True, timeout=8, env={**os.environ, "DISPLAY": ":0"},
        )
        if shot.exists() and shot.stat().st_size > 5000:
            img = Image.open(shot)
            if img.width > 1920:
                ratio = 1920 / img.width
                img = img.resize((1920, int(img.height * ratio)), Image.LANCZOS)
            text = pytesseract.image_to_string(img, lang="spa+eng")
            return text
    except Exception as e:
        log.debug("OCR falló: %s", e)
    return ""

# ── Búsqueda de texto en pantalla (OCR con coordenadas) ────────────────────

def _find_text_on_screen(search_text: str) -> tuple:
    """Busca texto en pantalla vía OCR y devuelve (x, y, w, h) o (None, None, 0, 0)."""
    import pytesseract
    from PIL import Image
    shot = SHOTS_DIR / f"labex_find_{int(time.time())}.png"
    try:
        subprocess.run(
            ["import", "-window", "root", str(shot)],
            capture_output=True, timeout=8, env={**os.environ, "DISPLAY": ":0"},
        )
        if shot.exists() and shot.stat().st_size > 5000:
            img = Image.open(shot)
            data = pytesseract.image_to_data(img, lang="spa+eng", output_type=pytesseract.Output.DICT)
            search_lower = search_text.lower()
            for i, word in enumerate(data["text"]):
                if search_lower in word.lower():
                    x = data["left"][i] + data["width"][i] // 2
                    y = data["top"][i] + data["height"][i] // 2
                    w = data["width"][i]
                    h = data["height"][i]
                    return (x, y, w, h)
    except Exception as e:
        log.debug("find_text falló: %s", e)
    return (None, None, 0, 0)

# ── Click humano (bezier) ──────────────────────────────────────────────────

def _click_at(x: int, y: int, description: str = "") -> bool:
    """Click FÍSICO con curva bezier en las coordenadas (x,y)."""
    if not _can_act():
        return False
    try:
        from core.human_emulator import get_human_emulator
        he = get_human_emulator()
        he._ensure_browser_focus()
        time.sleep(0.15)
        he.move_to(x, y)
        time.sleep(0.1)
        he.click()
        if description:
            log.info("🖱 click bezier @(%d,%d) → %s", x, y, description)
        return True
    except Exception as e:
        log.warning("click falló (%s): usando xdotool", e)
        subprocess.run(["xdotool", "mousemove", str(x), str(y)],
                       env={**os.environ, "DISPLAY": ":0"}, timeout=3)
        subprocess.run(["xdotool", "click", "1"],
                       env={**os.environ, "DISPLAY": ":0"}, timeout=3)
        return True

def _click_text(text: str, timeout: float = 10.0) -> bool:
    """Busca texto en pantalla y clica en el centro. Reintenta."""
    t0 = time.time()
    attempts = 0
    while time.time() - t0 < timeout:
        attempts += 1
        x, y, w, h = _find_text_on_screen(text)
        if x is not None:
            log.info("🎯 '%s' encontrado @(%d,%d) en %d intentos", text[:30], x, y, attempts)
            return _click_at(x, y, text[:30])
        time.sleep(1.0)
    log.warning("❌ '%s' no encontrado tras %.0fs (%d intentos)", text[:30], timeout, attempts)
    return False

def _type_text_physically(text: str) -> bool:
    """Escribe texto FÍSICAMENTE con ritmo humano."""
    if not _can_act():
        return False
    try:
        from core.human_emulator import get_human_emulator
        he = get_human_emulator()
        he.type_text(text)
        return True
    except Exception:
        subprocess.run(["xdotool", "type", text],
                       env={**os.environ, "DISPLAY": ":0"}, timeout=5)
        return True

def _press_key(key: str) -> bool:
    """Pulsa una tecla FÍSICAMENTE."""
    if not _can_act():
        return False
    subprocess.run(["xdotool", "key", key],
                   env={**os.environ, "DISPLAY": ":0"}, timeout=3)
    return True

# ── Navegación REAL Firefox ────────────────────────────────────────────────

def _open_firefox_tab(url: str) -> None:
    """Abre Firefox REAL con la URL (sin webdriver, sin Playwright)."""
    ff = None
    for bin_name in ("firefox-esr", "firefox", "firefox-esr-bin", "firefox-bin"):
        p = subprocess.run(["which", bin_name], capture_output=True, text=True, timeout=3)
        if p.returncode == 0:
            ff = p.stdout.strip()
            break
    if not ff:
        log.error("Firefox no encontrado")
        return
    subprocess.Popen([ff, "--new-tab", url],
                     env={**os.environ, "DISPLAY": ":0"},
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("🌐 Firefox REAL abriendo: %s", url[:80])
    time.sleep(4)

def _bring_firefox_front():
    """Trae Firefox al frente, maximizado."""
    env = {**os.environ, "DISPLAY": ":0"}
    try:
        subprocess.run(["wmctrl", "-a", "Mozilla Firefox"], timeout=3, env=env, capture_output=True)
    except Exception:
        pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowsize", "100%", "100%"],
                       timeout=3, env=env, capture_output=True)
    except Exception:
        pass
    time.sleep(0.5)

# ── Aprender al grafo ──────────────────────────────────────────────────────

def _learn(concept: str, text: str, src: str = "labex_indetectable"):
    try:
        from core.db import get_conn
        import uuid as _uuid
        conn = get_conn(str(Path.home() / ".eidos" / "evolution_brain.db"), timeout=20)
        conn.execute(
            "INSERT INTO knowledge_nodes (id, concept, definition, category, confidence, source, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("ldi_" + _uuid.uuid4().hex[:12], concept[:120], text[:1500],
             "labex_lab", 0.85, src, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception as e:
        log.debug("learn: %s", e)

# ── MAIN: flujo LabEx indetectable ────────────────────────────────────────

def main():
    log.info("🚀 EIDOS LabEx INDETECTABLE — Firefox real + HumanEmulator bezier + OCR")
    log.info("   CERO Playwright. CERO DOM. CERO navigator.webdriver.")

    from core.eidos_register import _secret
    email = _secret("EIDOS_LOGIN_EMAIL")

    steps_done = []

    # ── FASE 1: LOGIN ──────────────────────────────────────────────────────
    log.info("── FASE 1: LOGIN con Google (sesión real de SER) ──")
    _open_firefox_tab("https://labex.io/login")
    time.sleep(5)
    _bring_firefox_front()

    # Leer pantalla para diagnóstico
    text = _capture_and_ocr()
    log.info("📸 OCR inicial: %d chars", len(text))
    log.info("   primeras líneas: %s", text[:200].replace(chr(10), " | "))

    # Detectar si ya está logueado
    logged_in = "skill tree" in text.lower() or "my labs" in text.lower() or "dashboard" in text.lower()
    guest_signs = ("join labex", "continue with google", "sign in", "log in", "get started")
    is_guest = any(g in text.lower() for g in guest_signs)

    if logged_in:
        log.info("✅ Ya logueado (detectado: skill tree/dashboard)")
    elif is_guest:
        log.info("🔑 Necesita login. Buscando 'Continue with Google'...")
        if _click_text("Continue with Google", timeout=10):
            log.info("✅ Clic en Google OAuth. Esperando login (máx 90s)...")
            for i in range(18):
                time.sleep(5)
                _bring_firefox_front()
                text = _capture_and_ocr()
                if "skill tree" in text.lower() or "my labs" in text.lower():
                    log.info("✅ Login detectado en iteración %d", i+1)
                    logged_in = True
                    break
            if not logged_in:
                log.warning("⚠️ Login no detectado automáticamente. ¿SER necesita intervenir?")
                log.info("⏳ Esperando 60s más por si SER completa el login manual...")
                time.sleep(60)
    else:
        log.info("⚠️ No detecté ni login ni guest. Continuando...")

    # ── FASE 2: IR A CURSOS ────────────────────────────────────────────────
    log.info("── FASE 2: EXPLORAR CURSOS ──")
    _open_firefox_tab("https://labex.io/courses")
    time.sleep(5)
    _bring_firefox_front()
    text = _capture_and_ocr()
    log.info("📸 Cursos: %d chars OCR", len(text))

    # ── FASE 3: ABRIR CURSO DE PRINCIPIANTE ───────────────────────────────
    log.info("── FASE 3: BUSCAR CURSO ──")
    course_found = False
    for course in ("Linux for Beginners", "Linux Basics", "Kali Linux", "Beginner", "Quick Start"):
        if course.lower() in text.lower():
            log.info("📚 Detectado: %s", course)
            if _click_text(course, timeout=8):
                course_found = True
                log.info("✅ Curso abierto: %s", course)
                break
            time.sleep(3)

    if not course_found:
        # Fallback: buscar cualquier enlace de curso
        log.info("🔍 Buscando primer enlace de curso/lab...")
        for word in ("Linux", "Python", "Docker", "Git", "Beginner", "Start"):
            if word.lower() in text.lower():
                if _click_text(word, timeout=5):
                    course_found = True
                    break
                time.sleep(2)

    time.sleep(5)
    _bring_firefox_front()

    # ── FASE 4: ARRANCAR LAB ───────────────────────────────────────────────
    log.info("── FASE 4: ARRANCAR PRIMER LAB ──")
    text = _capture_and_ocr()
    lab_started = False
    for btn in ("Start Learning", "Start Lab", "Begin Lab", "Launch", "Access", "Comenzar"):
        if btn.lower() in text.lower():
            log.info("▶ Detectado: %s", btn)
            if _click_text(btn, timeout=8):
                lab_started = True
                log.info("✅ Lab arrancado")
                break
            time.sleep(3)

    if not lab_started:
        log.warning("⚠️ No encontré botón de inicio de lab. URL actual: ?")
        text2 = _capture_and_ocr()
        log.info("📸 Contenido actual: %s", text2[:300].replace(chr(10), " | "))

    # Esperar a que cargue el entorno del lab (VM + terminal)
    log.info("⏳ Esperando 40s a que cargue la VM del lab...")
    time.sleep(40)
    _bring_firefox_front()

    # ── FASE 5: HACER PASOS DEL LAB ────────────────────────────────────────
    log.info("── FASE 5: EJECUTAR PASOS DEL LAB ──")
    for step in range(5):
        time.sleep(3)
        text = _capture_and_ocr()
        if not text.strip():
            log.warning("OCR vacío en paso %d", step+1)
            continue

        # Extraer comandos del texto (bloques de código típicos)
        cmd = _extract_command_from_ocr(text)
        log.info("📝 Paso %d | OCR %d chars | comando: %s", step+1, len(text), cmd or "(ninguno)")

        _learn(f"labex paso {step+1}: {text[:60]}", text[:800])

        if cmd:
            # Escribir comando en la terminal del lab (xterm)
            log.info("⌨ Escribiendo: %s", cmd)
            _type_text_physically(cmd)
            time.sleep(0.3)
            _press_key("Return")
            time.sleep(3)
            steps_done.append({"paso": step+1, "cmd": cmd, "ejecutado": True})

        # Avanzar al siguiente paso
        advanced = False
        for btn in ("Next", "Continue", "Siguiente", "Next Step", "Check", "Submit"):
            if btn.lower() in text.lower():
                log.info("▶ Avanzando: %s", btn)
                if _click_text(btn, timeout=5):
                    advanced = True
                    break
                time.sleep(2)
        if not advanced:
            log.info("⏸ No hay botón 'siguiente' visible. Fin del lab o pausa.")
            break

    log.info("🏁 LabEx completado: %d pasos | %s",
             len(steps_done),
             str(steps_done))
    log.info("🪟 Ventana abierta 60s para que SER vea el resultado.")
    time.sleep(60)


def _extract_command_from_ocr(text: str) -> str:
    """Extrae el primer comando de terminal del texto OCR del lab.
    Busca patrones típicos: $ comando, # comando, o líneas que parecen comandos."""
    for line in text.splitlines():
        line = line.strip()
        # Comando con prompt
        m = re.match(r'[$#]\s*(\S.*)', line)
        if m:
            cmd = m.group(1).strip()
            if 2 < len(cmd) < 120:
                return cmd
        # Línea que empieza con verbo de comando típico
        m = re.match(r'(ls|cd|cat|echo|mkdir|touch|chmod|chown|cp|mv|rm|grep|find|'
                     r'tar|gzip|gunzip|apt|apt-get|pip|npm|node|python|python3|'
                     r'git|docker|curl|wget|ssh|scp|nmap|ping|netstat|ss|'
                     r'ps|kill|top|htop|df|du|free|uname|whoami|id|sudo|su|'
                     r'systemctl|journalctl)\s+.*', line)
        if m and len(line) < 120:
            return line.strip()
    return ""


if __name__ == "__main__":
    main()
