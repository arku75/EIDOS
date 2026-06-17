"""
EIDOS Autonomous Loop — Bucle de autonomía real

Cada X segundos:
1. Percibe: escanea pantalla (ventanas + OCR)
2. Razona: analiza contexto con inference engine
3. Actúa: si detecta oportunidad, envía mensaje proactivo o ejecuta acción
4. Aprende: registra lo que hizo y el resultado

Ejemplo:
- Detecta "error" en pantalla → ofrece ayuda
- Detecta "cómo instalar Python" en Firefox → abre tutorial
- Detecta ventana de terminal con comando fallido → sugiere corrección
"""

import logging
import threading
import time
from typing import Dict, Optional

try:
    from core.screen_scanner import get_screen_context
except ImportError:
    # Fallback: usar get_open_windows + scan_windows
    def get_screen_context():
        from core.screen_scanner import get_open_windows, scan_windows
        windows = get_open_windows()
        active = windows[0].get("name", "") if windows else ""
        try:
            scan = scan_windows(use_vision=False)
            return {
                "windows_open": [w.get("name", "") for w in windows],
                "elements": scan.get("elements", []),
                "active_window": active,
                "visible_text": scan.get("visible_text", "")[:2000],
                "active_pid": windows[0].get("pid", 0) if windows else 0,
            }
        except Exception:
            return {
                "windows_open": [w.get("name", "") for w in windows],
                "elements": [],
                "active_window": active,
                "visible_text": "",
                "active_pid": windows[0].get("pid", 0) if windows else 0,
            }
from core.inference import get_inference
from core.colony_proactive import push_message
from core.action_system import (
    is_available as action_available,
    move_to, click, write, press, 
    locate_button, control_window, execute_sequence
)

log = logging.getLogger("eidos.autonomous_loop")

class EidosAutonomousLoop:
    """Bucle autónomo de EIDOS: percibir → razonar → actuar → aprender."""
    
    def __init__(self, interval: int = 10):
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_context = ""
        self._last_ocr_time = 0
        self._cached_elements = []
        
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("Bucle autónomo iniciado (intervalo: %d segundos)", self.interval)
        
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Bucle autónomo detenido")
        
    def _loop(self):
        while self._running:
            try:
                self._cycle()
            except Exception as e:
                log.error("Error en ciclo autónomo: %s", e)
            time.sleep(self.interval)
    
    def _cycle(self):
        # OCR cada 30s (no cada 10s) para no saturar
        now = time.time()
        if now - self._last_ocr_time >= 30:
            context = get_screen_context()
            self._last_ocr_time = now
            self._cached_elements = context.get("elements", [])
        else:
            context = {
                "windows_open": [],
                "visible_text": "",
                "elements": self._cached_elements,
                "buttons": [e for e in self._cached_elements if e.get("type") == "button"],
                "titles": [e for e in self._cached_elements if e.get("type") == "title"],
                "has_error": False,
                "needs_help": False,
                "wants_install": False,
            }
        
        text = context.get("visible_text", "").strip()
        
        # Sin OCR nuevo, ver solo ventanas (rápido)
        if not text:
            from core.screen_scanner import get_open_windows
            wins = get_open_windows()
            if wins:
                text = " ".join(w.get("name", "") for w in wins)
                context["windows_open"] = [w.get("name", "?") for w in wins]
        
        # Evita repetir el mismo contexto
        if text == self._last_context or not text:
            return
        self._last_context = text
        
        log.debug("Ciclo autónomo: ventanas=%s, texto=%s",
                 context["windows_open"], text[:80])
        
        action = self._analyze_context(context)
        if not action:
            return
        
        self._execute_action(action, context)
    
    def _analyze_context(self, context: Dict) -> Optional[Dict]:
        import re as _re
        text = context.get("visible_text", "").lower()
        buttons = " ".join(b["text"].lower() for b in context.get("buttons", []))
        titles = " ".join(t["text"].lower() for t in context.get("titles", []))
        full = f"{text}\n{buttons}\n{titles}"

        # Error real: requiere indicador fuerte (traceback, exception, fatal,
        # códigos HTTP 4xx/5xx, "error:" o "error " seguido de palabra técnica)
        # NO basta con la subcadena "error" en cualquier sitio (OCR ruidoso).
        _err_pat = _re.compile(
            r"\b(traceback|exception|segmentation fault|fatal error|"
            r"error:\s|errno|failed to|cannot find|permission denied|"
            r"connection refused|404|500|502|503)\b",
            _re.IGNORECASE,
        )
        # O bien un diálogo con botón explícito Aceptar/Cerrar + título con "error"
        has_real_error = bool(_err_pat.search(full)) or (
            _re.search(r"\berror\b", titles) and any(
                w in buttons for w in ["aceptar", "ok", "cerrar", "close"]
            )
        )
        if context.get("has_error") or has_real_error:
            for btn in context.get("buttons", []):
                if any(w in btn["text"].lower() for w in ["aceptar", "ok", "cerrar", "close", "cancelar"]):
                    return {
                        "type": "click_button",
                        "reason": "error_con_boton",
                        "button_text": btn["text"],
                        "message": f"Veo un error en pantalla y un botón '{btn['text']}'. ¿Clic?",
                        "confidence": 0.9,
                    }
            return {
                "type": "offer_help",
                "reason": "error_en_pantalla",
                "message": "Veo un error en pantalla. ¿Quieres que lo revise? Responde en /talk",
                "confidence": 0.9,
            }

        # Petición de ayuda: requiere frase explícita, no solo palabra suelta
        _help_pat = _re.compile(
            r"\b(c[oó]mo (instalo|hago|puedo|configuro|arreglo)|"
            r"how (do|to) (i|install|fix|configure)|"
            r"ayuda con|help (me|with)|"
            r"necesito ayuda)\b",
            _re.IGNORECASE,
        )
        if context.get("needs_help") or _help_pat.search(full):
            return {
                "type": "offer_help",
                "reason": "solicitud_ayuda",
                "message": "Parece que buscas ayuda. Dime en /talk qué necesitas",
                "confidence": 0.85,
            }

        # Instalación detectada: requiere verbo + objeto
        _inst_pat = _re.compile(
            r"\b(instalar|install|setup|configurar) [a-z0-9_.+-]{2,}\b",
            _re.IGNORECASE,
        )
        m_inst = _inst_pat.search(full)
        if m_inst:
            q = m_inst.group(0)
            return {
                "type": "open_tutorial",
                "reason": "instalacion",
                "message": f"Detecté '{q}' — abro tutorial",
                "url": f"https://duckduckgo.com/?q={'+'.join(q.split())}+tutorial",
            }
        
        # Botón relevante en pantalla (sin contexto de error)
        for btn in context.get("buttons", []):
            btn_text = btn["text"].lower()
            if any(w in btn_text for w in ["siguiente", "next", "continuar", "continue", "enviar", "send"]):
                return {
                    "type": "click_button",
                    "reason": "boton_relevante",
                    "button_text": btn["text"],
                    "message": f"Veo un botón '{btn['text']}' en pantalla. ¿Hago clic?",
                    "confidence": 0.7
                }
        
        # Si inference engine resuelve (ej: "qué es docker" desde OCR)
        try:
            inf = get_inference().resolve(text)
            if inf.resolved and inf.confidence >= 0.4:
                push_message(
                    actor="eidos_autonomous",
                    message=f"Veo en tu pantalla: {text[:100]}... {inf.answer}",
                    topic="inference",
                    priority=3
                )
        except Exception:
            pass
        
        return None
    
    def _execute_action(self, action: Dict, context: Dict):
        log.info("Acción autónoma: %s (razón=%s)", action["type"], action.get("reason", "?"))
        push_message(
            actor="eidos_autonomous",
            message=action.get("message", "acción autónoma"),
            topic=action.get("reason", "autonomous"),
            priority=6
        )
        
        if action["type"] == "open_tutorial":
            self._open_url(action["url"])
    
    def _open_url(self, url: str):
        try:
            import subprocess
            subprocess.run(["firefox-esr", "--new-tab", url],
                          check=True, timeout=10,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.error("Error al abrir URL: %s", e)


def get_autonomous_loop() -> EidosAutonomousLoop:
    global _autonomous_loop
    if _autonomous_loop is None:
        _autonomous_loop = EidosAutonomousLoop(interval=10)
    return _autonomous_loop

_autonomous_loop: Optional[EidosAutonomousLoop] = None