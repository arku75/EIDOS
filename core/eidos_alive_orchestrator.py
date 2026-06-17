"""
EIDOS Alive Orchestrator — La chispa de vida sin LLM/VLM.

Conecta los 5 GAPs de S66 en un solo flow vivo:
    PERCIBE (OCR + layout) → APRENDE (apps activas) → RECUERDA (episódica) →
    RAZONA (inference + grafo + self-research) → ACTÚA (action_system) → REFLEXIONA

Corre como daemon ligero. NO usa Ollama ni Moondream. Solo grafo + OCR + lógica.
LLM y VLM son herramientas externas que pueden invocarse explícitamente, pero el
loop autónomo no los necesita para vivir.

Stream of consciousness se escribe a ~/.eidos/alive_stream.log para que SER vea
al despertar qué ha estado haciendo EIDOS toda la noche.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn
from core.loop_governor import get_loop_governor

# Ejecutable standalone: añade el root de EIDOS al path
_EIDOS_ROOT = Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in sys.path:
    sys.path.insert(0, str(_EIDOS_ROOT))

# Event bus: sistema nervioso que conecta todos los modulos en tiempo real
from core.eidos_event_bus import get_event_bus, publish as bus_publish

# ── S125 Unified Modules ──────────────────────────────────────────────────────
# Module 1: Unified Memory (reemplaza 5 sistemas de memoria dispares)
from core.eidos_memory_unified import memory as unified_memory
# Module 2: Metacognition Real (introspeccion basada en datos, no templates)
from core.eidos_metacog_real import get_metacog_real
# Module 5: Sandbox code execution (practicar conceptos nuevos)
from core.eidos_sandbox import learn_from_code as sandbox_learn
# Module 6: Voice (Vosk speech-to-text, optional)
# (lazy import en start() para no cargar vosk/pyaudio si no se usa)

log = logging.getLogger("eidos.alive")

STREAM_LOG = Path.home() / ".eidos" / "alive_stream.log"
STREAM_LOG.parent.mkdir(parents=True, exist_ok=True)


class AliveOrchestrator:
    """Orquesta el ciclo vital de EIDOS sin LLM."""

    def __init__(self, perceive_every: int = 30, reflect_every: int = 300):
        self.perceive_every = perceive_every    # OCR cada 120s (era 30s, reducido para evitar saturar tesseract)
        self.reflect_every = reflect_every       # introspección cada 5min
        self._last_ocr_time = 0
        self._ocr_interval = 120                 # mínimo entre OCRs (segundos)
        self._ocr_initial_boost = True           # S126: forzar OCR inmediato al arrancar
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_reflection = 0
        self._cycle_count = 0
        self._autonomous_decisions = 0   # ciclos que produjeron decision autónoma
        self._known_concepts_seen = set()
        self._explored_dirs = set()           # directorios ya explorados
        self._last_filesystem_explore = 0     # timestamp última exploración FS
        self._fs_explore_every = 600          # explorar FS cada 10 min
        self._last_browse = 0                # timestamp última navegación web
        self._browse_every = 900             # navegar web cada 15 min
        self._browser_instance = None        # singleton del navegador
        self._last_brain_context = None      # ActivatedContext del último reason() para aprendizaje
        self._last_brain_decision = None     # Decision del último reason() para aprendizaje
        self._presence = None                # Presence engine (lazy init)
        self._healer_last_run = 0            # timestamp último healer check
        self._healer_interval = 300          # healer cada 5 min
        self._growth_index = 0.0             # Growth Index real (reemplaza falso 95.5%)
        self._last_growth_update = 0         # timestamp ultimo growth update
        self._last_active_window = ""        # para detectar window_changed events
        self._event_bus = None               # lazy init del sistema nervioso
        self._curiosity_engine = None        # CuriosityEngine (lazy init)
        self._last_curiosity_tick = 0        # timestamp ultimo curiosity tick
        self._curiosity_tick_every = 600     # curiosity tick cada 10 min (idle)
        # ── Observational Learning ──────────────────────────────────────────
        self._observational_enabled = os.environ.get("EIDOS_OBSERVATIONAL") == "1"
        self._last_observational_cycle = 0   # timestamp ultimo ciclo observacional
        self._observational_every = 15       # observar cada 15s (mas frecuente que perceive)
        self._observational_ctx: Dict[str, Any] = {}  # ultimo contexto observacional
        # ── S125 Modules ─────────────────────────────────────────────────────
        self._singularity_engine = None            # Module 3: SingularityEngine (lazy)
        self._last_singularity_check = 0           # timestamp ultima comprobacion
        self._singularity_every = 1800             # cada 30 min (Module 3)
        self._voice_thread = None                  # Module 6: voz (EIDOS_VOICE=1)
        self._voice_running = False                # flag thread de voz
        self._metacog = None                       # Module 2: MetaCognitionReal (lazy)
        self._last_memory_decay = 0                # Module 1: decay cada 1h
        # ── Relation Engine: semantic typing (Hallazgo Crítico #1) ───────────
        self._relation_engine = None               # RelationEngine (lazy)
        self._last_relation_enrich = 0             # timestamp ultimo enrich
        self._relation_enrich_every = 1800         # cada 30 min
        # ── S127 unified_cycle sub-cycle intervals (descaled to avoid overlap) ─
        self._last_character_lifecycle = 0         # character_lifecycle tick timestamp
        self._character_lifecycle_every = 300      # cada 5 min (300s)
        self._last_night_cycle = 0                 # night_cycle tick timestamp
        self._night_cycle_every = 3600             # revisar cada hora si es de noche
        self._last_brain_sync_tick = 0             # brain_sync tick timestamp
        self._brain_sync_every = 1200              # cada 20 min (1200s)
        self._last_autonomous_research = 0         # autonomous_research tick timestamp
        self._autonomous_research_every = 1800     # cada 30 min (1800s)
        self._last_study_queue = 0                 # study_queue processing timestamp
        self._study_queue_every = 900              # procesar cola cada 15 min (900s)
        self._last_browser_deep_dive = 0           # browser_deep_dive timestamp (S127 — cada 1h)
        self._last_pipeline = 0                   # pipeline research timestamp (S127 — cada 4h)
        self._pipeline_every = 14400              # investigar topic pendiente cada 4h (14400s)
        # ── S126 anti-degenerate-loop ─────────────────────────────────────────
        self._action_rotation = [             # Acciones diversas para rotar cuando
            "explore_filesystem",             # el OCR está vacío y el grafo no decide.
            "introspect",                     # Evita el bucle percibir→recordar→razonar
            "browse_web",                     # sin actuar nunca.
            "offer_help",
        ]
        self._action_rotation_idx = 0
        self._rotation_interval = 5           # rotar cada N ciclos sin accion

    def _stream(self, kind: str, message: str, extra: Optional[Dict] = None):
        """Escribe al stream of consciousness — visible para SER al despertar."""
        entry = {
            "ts": datetime.now().isoformat(),
            "cycle": self._cycle_count,
            "kind": kind,
            "message": message,
        }
        if extra:
            entry["extra"] = extra
        try:
            with open(STREAM_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
        log.info("[%s] %s", kind, message)

    # ─── PERCIBIR ───────────────────────────────────────────────────────────

    def perceive(self) -> Dict[str, Any]:
        """Captura el estado actual de la pantalla con OCR + layout.
        OCR se throttle a cada 120s para no saturar tesseract (screenshots 1.3MB)."""
        try:
            from core.screen_scanner import get_open_windows, scan_windows
            windows = get_open_windows()
            elements = []
            visible_text = ""
            now = time.time()
            # S126: Forzar OCR inicial al arrancar — evita el bucle degenerado
            # donde 0 elementos OCR => fallback siempre "observe"
            force_ocr = self._ocr_initial_boost
            if force_ocr:
                self._ocr_initial_boost = False
            # Throttle OCR: solo cada _ocr_interval segundos (excepto boost inicial)
            if force_ocr or now - self._last_ocr_time >= self._ocr_interval:
                try:
                    scan = scan_windows(use_vision=False)
                    elements = scan.get("elements", [])
                    visible_text = scan.get("visible_text", "")
                    self._last_ocr_time = now
                except Exception:
                    pass
            active = windows[0].get("name", "") if windows else ""
            ctx = {
                "windows_open": [w.get("name", "") for w in windows],
                "elements": elements,
                "active_window": active,
                "visible_text": visible_text[:2000],
                "active_pid": windows[0].get("pid", 0) if windows else 0,
            }
            self._stream("perceive",
                         f"Pantalla: {len(ctx.get('windows_open', []))} ventanas, "
                         f"{len(ctx.get('elements', []))} elementos OCR. "
                         f"Activa: {ctx.get('active_window', '?')[:60]}")
            return ctx
        except Exception as e:
            self._stream("perceive_error", f"S125O|perceive falló: {e}")
            return {}

    # ─── OBSERVAR A SER (Observational Learning) ────────────────────────────

    def observe_ser(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Observational Learning phase: watch SER work and extract intent/procedures.

        Uses the EIDOS Observational module to:
        1. Segment discrete actions SER performs (commands, file opens, browser nav)
        2. Infer the intent behind each action (why did SER do that?)
        3. Accumulate action sequences for recipe extraction
        4. Promote high-confidence recipes to procedural memory

        Enabled via EIDOS_OBSERVATIONAL=1 env var (OFF by default).
        Throttles to _observational_every seconds to avoid CPU saturation.
        """
        if not self._observational_enabled:
            return {}

        now = time.time()
        if now - self._last_observational_cycle < self._observational_every:
            return self._observational_ctx

        self._last_observational_cycle = now
        try:
            from core.eidos_observational import observe_cycle
            obs_ctx = observe_cycle(ctx)
            self._observational_ctx = obs_ctx

            if obs_ctx.get("actions_detected", 0) > 0:
                action_summaries = [
                    "%s:%s" % (a.get('type', '?'),
                               str(a.get('cmd', a.get('file', a.get('page', ''))))[:40])
                    for a in obs_ctx.get("actions", [])[:5]
                ]
                self._stream("observe_ser",
                             "Observado: %s acciones SER — %s" % (
                                 obs_ctx['actions_detected'],
                                 '; '.join(action_summaries)))

            if obs_ctx.get("intents_inferred", 0) > 0:
                for intent in obs_ctx.get("intents", [])[:3]:
                    self._stream("observe_intent",
                                 "Inferido: %s — %s (conf=%.0f%%)" % (
                                     intent['intent'],
                                     intent['description'][:100],
                                     intent['confidence'] * 100))

            return obs_ctx

        except Exception as e:
            self._stream("observe_error", "observe_ser: %s" % e)
            return {}

    # ─── APRENDER ───────────────────────────────────────────────────────────

    def learn_from_perception(self, ctx: Dict[str, Any]) -> int:
        """Si hay ventana activa nueva, la aprende en background.
        También extrae conceptos desconocidos del texto visible para self-research.

        [S125] Module 1 (eidos_memory_unified): Cada concepto nuevo se guarda en
        la memoria unificada. Module 5 (eidos_sandbox): Si el texto visible
        contiene fragmentos de codigo, los practica en el sandbox."""
        learned = 0
        active = ctx.get("active_window", "")
        if active and active not in self._known_concepts_seen:
            self._known_concepts_seen.add(active)
            try:
                from core.screen_scanner import research_app
                r = research_app(active, deep=False)
                if r.get("learned"):
                    self._stream("learn", f"Nueva app aprendida: {r.get('key', active)}",
                                 extra={"definition": r.get("definition")})
                    learned += 1
                    # ── Module 1: Guardar en memoria unificada ─────────────────
                    try:
                        unified_memory.remember(
                            f"App aprendida: {r.get('key', active)} — {r.get('definition', '')[:300]}",
                            category="fact",
                            importance=0.7,
                            tags=["app", "perception", r.get('key', active)[:30]],
                        )
                    except Exception:
                        pass
            except Exception as e:
                self._stream("learn_error", f"research_app: {e}")

        # Extraer 1-2 palabras únicas del texto visible para posible self-research
        text = ctx.get("visible_text", "")
        if text and len(text) > 20:
            import re
            words = set(re.findall(r"\b[a-zA-Z]{5,15}\b", text.lower()))
            candidates = list(words - self._known_concepts_seen)[:2]
            for c in candidates:
                self._known_concepts_seen.add(c)
                try:
                    from core.knowledge_reasoner import get_reasoner
                    r = get_reasoner()
                    if r._ready:
                        added = r._self_research(c)
                        if added > 0:
                            self._stream("self_research",
                                         f"Concepto nuevo aprendido: '{c}' (+{added} nodos)")
                            learned += added
                            # ── Module 1: Guardar concepto en memoria unificada ─
                            try:
                                unified_memory.remember(
                                    f"Concepto auto-investigado: {c} (del texto visible)",
                                    category="concept",
                                    importance=0.55,
                                    tags=["self_research", "perception", c[:30]],
                                )
                            except Exception:
                                pass
                except Exception:
                    pass

        # ── Module 5 (eidos_sandbox): Practicar snippets de codigo en sandbox ──
        if text and len(text) > 50:
            try:
                import re as _re
                # Detectar snippets de Python en el texto visible
                py_snippets = _re.findall(
                    r'(?:def\s+\w+|import\s+\w+|from\s+\w+\s+import|class\s+\w+|print\(.+?\)|'
                    r'for\s+\w+\s+in\s+|if\s+__name__\s*==)',
                    text, _re.IGNORECASE
                )
                if py_snippets and len(py_snippets) >= 2:
                    # Extraer un bloque de codigo razonable
                    snippet = text[:500]
                    sandbox_result = sandbox_learn(snippet, lang="python")
                    if sandbox_result.get("ok"):
                        self._stream("sandbox_practice",
                                     f"Sandbox: codigo practicado ({sandbox_result.get('error_type', '?')}) — "
                                     f"{len(sandbox_result.get('concepts_extended', []))} conceptos extraidos")
                        # ── Module 1: Guardar resultado del sandbox ──
                        unified_memory.remember(
                            f"Sandbox: {snippet[:100]}... → {sandbox_result.get('stdout_preview', '')[:200]}",
                            category="skill_result",
                            importance=0.5,
                            tags=["sandbox", "practice", "python"],
                        )
            except Exception:
                pass

        return learned

    # ─── EXPLORAR SISTEMA DE ARCHIVOS ───────────────────────────────────────

    def explore_filesystem(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Explora el sistema de archivos si detecta un gestor de archivos
        o terminal activa. Aprende la estructura del disco como un humano."""
        active = ctx.get("active_window", "").lower()
        result = {"explored": False, "path": "", "landmarks_found": [], "error": ""}

        # Solo si la ventana activa sugiere navegación de archivos
        is_file_manager = any(kw in active for kw in [
            "dolphin", "files", "nautilus", "thunar", "explorer",
            "konsole", "terminal", "shell", "bash", "zsh",
            "kate", "gedit", "vim", "nvim", "emacs", "code",
            "~", "/", "home",
        ])
        if not is_file_manager:
            return result

        # No saturar — explorar como mucho cada 10 min
        now = time.time()
        if now - self._last_filesystem_explore < self._fs_explore_every:
            return result

        try:
            from core.eidos_filesystem import explore_path, get_landmarks, ls

            # Explorar home de SER primero (el punto de partida natural)
            home = str(Path.home())
            if home not in self._explored_dirs:
                info = explore_path(home, quick=False)
                if info.get("ok"):
                    self._explored_dirs.add(home)
                    result["explored"] = True
                    result["path"] = home
                    result["entries_summary"] = info.get("entries_summary", {})
                    self._stream("filesystem",
                                 f"Explorado HOME: {info.get('name', '?')} — "
                                 f"{info.get('entries_summary', {}).get('total', 0)} entradas, "
                                 f"disco: {info.get('disk', {}).get('available_gb', '?')}GB libre")

            # Explorar un landmark nuevo cada vez
            all_landmarks = get_landmarks()
            for lm in all_landmarks:
                lm_path = lm["path"]
                if lm_path not in self._explored_dirs and lm.get("accessible"):
                    info = explore_path(lm_path, quick=True)
                    if info.get("ok"):
                        self._explored_dirs.add(lm_path)
                        result["landmarks_found"].append({
                            "path": lm_path,
                            "description": lm.get("description", ""),
                            "entries": info.get("entries_summary", {}).get("total", 0),
                        })
                        self._stream("filesystem",
                                     f"Landmark: {lm_path} — {lm.get('description', '')} "
                                     f"({info.get('entries_summary', {}).get('total', 0)} entradas)")
                        break  # Uno por ciclo para no saturar

            self._last_filesystem_explore = now
        except Exception as e:
            self._stream("filesystem_error", f"explore_filesystem: {e}")
            result["error"] = str(e)

        return result

    # ─── NAVEGAR WEB ────────────────────────────────────────────────────────

    def browse_web(self, url: str = "", topic: str = "") -> Dict[str, Any]:
        """Navega la web para investigar un tema o leer una página.
        Usa el navegador EIDOS (Playwright/Selenium) o Firefox ESR como fallback.

        Returns: {ok, url, title, text_preview, elements_found, mode (browser|firefox|error)}
        """
        result = {"ok": False, "url": url, "title": "", "text_preview": "",
                   "elements_found": 0, "mode": "error", "error": ""}

        now = time.time()
        if now - self._last_browse < self._browse_every and not topic and not url:
            return result  # Throttle, no saturar

        try:
            # Intentar con el navegador integrado (Playwright/Selenium)
            try:
                from core.eidos_browser import browse as browse_func
                if url:
                    text = browse_func(url, action="read", headless=False, wait=3)
                    result.update(ok=True, url=url, title="(browser)", text_preview=str(text)[:2000],
                                   mode="browser", elements_found=0)
                elif topic:
                    # Buscar en DuckDuckGo
                    import urllib.parse
                    search_url = f"https://duckduckgo.com/?q={urllib.parse.quote(topic)}"
                    text = browse_func(search_url, action="read", headless=False, wait=3)
                    result.update(ok=True, url=search_url, title=f"Búsqueda: {topic}",
                                   text_preview=str(text)[:2000], mode="browser")
            except Exception as e:
                log.debug("eidos_browser no disponible: %s, usando Firefox ESR", e)
                # Fallback: Firefox ESR
                import subprocess
                import urllib.parse
                target_url = url if url else f"https://duckduckgo.com/?q={urllib.parse.quote(topic)}"
                subprocess.run(
                    ["firefox-esr", "--new-tab", target_url],
                    check=False, timeout=10,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                result.update(ok=True, url=target_url, title="(Firefox ESR)",
                               text_preview="Página abierta en Firefox ESR para que SER la vea.",
                               mode="firefox", elements_found=0)

            if result.get("ok"):
                self._last_browse = now
                self._stream("browse",
                             f"Navegación web: {result.get('url', '?')[:80]} — "
                             f"modo={result.get('mode', '?')} — "
                             f"{len(result.get('text_preview', ''))} chars leídos")

                # Aprender del texto leído
                if result.get("text_preview") and len(result.get("text_preview")) > 50:
                    import re
                    words = set(re.findall(r"\b[a-zA-Z]{6,20}\b",
                                           result["text_preview"].lower()))
                    new_words = list(words - self._known_concepts_seen)[:3]
                    for w in new_words:
                        self._known_concepts_seen.add(w)
                        try:
                            from core.knowledge_reasoner import get_reasoner
                            r = get_reasoner()
                            if r._ready:
                                r._self_research(w)
                        except Exception:
                            pass
        except Exception as e:
            result["error"] = str(e)
            self._stream("browse_error", f"browse_web: {e}")

        return result

    # ─── PLANIFICAR Y EJECUTAR (Planner ↔ BOM) ──────────────────────────

    def plan_and_execute(self, goal: str, max_steps: int = 5,
                         dry_run: bool = None) -> Dict[str, Any]:
        """Descompone un objetivo en pasos usando el GraphPlanner y ejecuta
        cada paso a través del BOM (causal_loop). Planner ↔ BOM bridge.

        dry_run por defecto sigue EIDOS_BOM=1: si la variable está seteada
        a '1', ejecuta en modo REAL (mueve ratón, escribe teclado, clica).
        Si no, simula en seco para aprender sin riesgo."""
        if dry_run is None:
            dry_run = os.environ.get("EIDOS_BOM") != "1"
        result = {
            "ok": False, "goal": goal,
            "plan_steps": [], "executed": [],
            "all_succeeded": False, "error": "",
        }
        try:
            # 1. Planificar
            from core.eidos_planner import get_planner
            planner = get_planner()
            plan = planner.plan(goal)
            if not plan:
                result["error"] = "GraphPlanner no pudo generar un plan"
                return result

            steps_raw = plan.get("steps", [])
            if not steps_raw:
                result["error"] = "Plan sin pasos ejecutables"
                return result

            # Limitar y normalizar pasos
            steps_raw = steps_raw[:max_steps]
            for s in steps_raw:
                if s is None:
                    continue
                result["plan_steps"].append({
                    "action": s.get("action", "?"),
                    "args": s.get("args", []),
                    "confidence": s.get("confidence", 0.0),
                    "shell_cmd": s.get("shell_cmd", ""),
                })

            if not result["plan_steps"]:
                result["error"] = "Plan sin pasos válidos"
                return result

            # Log
            step_names = [ps["action"] for ps in result["plan_steps"]]
            self._stream("plan",
                         "Plan para '%s': %d pasos — %s" %
                         (str(goal)[:60], len(result["plan_steps"]),
                          str(step_names)))

            # 2. Ejecutar cada paso
            all_ok = True
            for i, ps in enumerate(result["plan_steps"]):
                action = ps.get("action", "?")
                shell_cmd = ps.get("shell_cmd", "") or ""
                exec_result = {"step": i + 1, "action": action, "ok": False}

                if dry_run:
                    exec_result["ok"] = True
                    exec_result["dry_run"] = True
                    exec_result["output"] = "[DRY_RUN] Ejecutaría: %s" % shell_cmd
                    self._stream("plan_exec",
                                 "Paso %d/%d: %s [DRY_RUN]" %
                                 (i + 1, len(result["plan_steps"]), action))
                else:
                    # ── Modo real con verificación y recovery (Gap 1 + Gap 4) ──
                    max_attempts = 3
                    verified = False
                    for attempt in range(1, max_attempts + 1):
                        try:
                            from core.causal_loop import step as bom_step
                            from core.eidos_recovery import get_recovery, safe_click, safe_type, safe_navigate
                            recovery = get_recovery()

                            # Snapshot pre-acción
                            pre_hash = str(time.time())
                            try:
                                from core.screen_scanner import take_screenshot
                                pre_hash = take_screenshot() or pre_hash
                            except Exception:
                                pass

                            # Ejecutar
                            bom_result = bom_step(goal=goal, app_name=None, dry_run=False)
                            exec_result["ok"] = bom_result.get("ok", False)
                            exec_result["output"] = str(bom_result.get("reason", ""))[:200]
                            exec_result["bom_result"] = bom_result

                            # Verificar
                            if exec_result["ok"] and action in ("click", "type", "navigate"):
                                verifier = recovery.verifier
                                is_ok, detail = verifier.verify(
                                    action,
                                    {"x": bom_result.get("x", 0), "y": bom_result.get("y", 0),
                                     "text": shell_cmd, "url": shell_cmd},
                                    pre_hash
                                )
                                if not is_ok:
                                    exec_result["ok"] = False
                                    exec_result["error"] = f"Verificación fallida: {detail}"
                                    # Fallback
                                    # Fallback via RecoveryOrchestrator fallback engine
                                    fb_info = f"fallback:{action}:cmd={shell_cmd[:60]}:attempt={attempt}"
                                    try:
                                        from core.eidos_recovery import ActionAttempt
                                        fb_attempt = ActionAttempt(
                                            action_type=action,
                                            action_params={"x": bom_result.get("x", 0), "y": bom_result.get("y", 0),
                                                          "text": shell_cmd, "url": shell_cmd},
                                            attempt_number=attempt, screen_before_hash=pre_hash)
                                        if action == "click":
                                            recovery.fallback_engine.fallback_click(fb_attempt)
                                        elif action == "type":
                                            recovery.fallback_engine.fallback_type(fb_attempt)
                                        elif action == "navigate":
                                            recovery.fallback_engine.fallback_navigate(fb_attempt)
                                    except Exception:
                                        pass  # Fallback engine is best-effort
                                    exec_result["fallback"] = fb_info
                                else:
                                    verified = True
                                    break  # exito verificado
                            elif exec_result["ok"]:
                                verified = True
                                break
                            else:
                                exec_result["fallback"] = f"action_failed:{action}:attempt={attempt}"

                        except Exception as e:
                            exec_result["ok"] = False
                            exec_result["error"] = str(e)
                            if attempt < max_attempts:
                                exec_result["retry"] = attempt + 1

                    if not verified and not exec_result.get("ok"):
                        all_ok = False

                    self._stream("plan_exec",
                                 "Paso %d/%d: %s → %s%s" %
                                 (i + 1, len(result["plan_steps"]), action,
                                  "✅" if verified else ("⚠️ retry" if exec_result.get("retry") else "❌"),
                                  f" ({exec_result.get('error', '')[:60]})" if exec_result.get("error") else ""))

                result["executed"].append(exec_result)
                if not exec_result["ok"]:
                    all_ok = False
                    if not dry_run:
                        break

            result["all_succeeded"] = all_ok
            result["ok"] = True

            # Grabar episodio
            try:
                from core.episodic_memory import record_action
                record_action(
                    "plan_and_execute", target="planner_bom",
                    context={"goal": goal, "steps": len(result["plan_steps"]),
                              "dry_run": dry_run},
                    outcome="success" if all_ok else "partial",
                )
            except Exception:
                pass

        except Exception as e:
            result["error"] = str(e)
            self._stream("plan_error", "plan_and_execute: %s" % str(e))

        return result

    def execute_terminal(self, cmd: str, reason: str = "") -> Dict[str, Any]:
        """Ejecuta un comando en la terminal visible (tmux+Konsole).
        Solo comandos seguros; los destructivos son bloqueados."""
        result = {"ok": False, "cmd": cmd, "output": "", "error": ""}
        try:
            from core.eidos_shell_term import run, is_safe
            safe, why = is_safe(cmd)
            if not safe:
                result["error"] = why
                return result
            r = run(cmd, timeout=20.0, open_window=False)
            result.update(ok=r.get("ok", False), output=r.get("output", ""), error=r.get("error", ""))
            if result["ok"]:
                self._stream("terminal",
                             f"Ejecutado: {cmd[:80]} → {result['output'][:120]}")
                # Aprender del output como concepto
                if len(result["output"]) > 10 and len(result["output"]) < 500:
                    self._known_concepts_seen.add(f"cmd:{cmd[:40]}")
        except Exception as e:
            result["error"] = str(e)
            self._stream("terminal_error", f"execute_terminal: {e}")
        return result

    # ─── RECORDAR ───────────────────────────────────────────────────────────

    def recall_relevant(self, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Busca episodios pasados similares al contexto actual.
        [S125] Module 1: Usa la memoria unificada (semantica + keyword + temporal)."""
        try:
            from core.episodic_memory import get_episodic
            em = get_episodic()
            text = ctx.get("visible_text", "")[:200]
            active = ctx.get("active_window", "")
            query = f"{active} {text}".strip()
            if len(query) < 10:
                return []
            sims = em.recall_similar(query, k=3, hours_window=24*7)

            # ── Module 1: Complementar con memoria unificada ─────────────────
            unified_results = []
            try:
                unified_results = unified_memory.recall(query, k=2, mode="auto")
                for um in unified_results:
                    if um.get("content") and um.get("score", 0) > 0.2:
                        sims.append({
                            "id": f"um_{um.get('id')}",
                            "content": um.get("content", "")[:300],
                            "source": "unified_memory",
                            "score": um.get("score", 0),
                            "category": um.get("category", "general"),
                        })
            except Exception:
                pass

            if sims:
                total = len(sims)
                uni_count = len([s for s in sims if s.get("source") == "unified_memory"])
                self._stream("recall",
                             f"Recuerdo {total} episodios similares "
                             f"({uni_count} de memoria unificada) al contexto actual")
            return sims
        except Exception as e:
            self._stream("recall_error", str(e))
            return []

    # ─── RAZONAR ────────────────────────────────────────────────────────────

    def reason(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Decide qué hacer basado en el contexto. Usa el grafo neuronal (EidosBrain).

        Prioridades urgentes (safety-first, el grafo no las sobreescribe):
          1. Dialog visible  → wait_user_decision
          2. Error detectado → offer_help

        Para el resto, el cerebro neuronal dispara spreading activation desde
        los conceptos perceptuales (nombres de ventana, texto visible) y puntúa
        las acciones candidatas según los patrones de activación neuronal.
        Sin hardcodear "if 'firefox' in window: browse_web".
        """
        decision = {"action": "observe", "reason": "nada relevante", "confidence": 0.0}
        layout = ctx.get("layout") or {}

        # ── URGENCY OVERRIDES (safety-first) ────────────────────────────

        # 1. Hay un dialog visible? → analizarlo (el grafo no sobreescribe esto)
        if layout.get("dialogs"):
            dlg = layout["dialogs"][0]
            content_texts = [c.get("text", "") for c in dlg.get("content", [])]
            buttons = [b.get("text", "") for b in dlg.get("buttons", [])]
            dlg_summary = " ".join(content_texts)[:100]
            self._stream("reason",
                         f"Detecté dialog: '{dlg_summary[:60]}' con botones {buttons[:3]}")
            decision = {
                "action": "wait_user_decision",
                "reason": "[graph+dialog] dialog_visible",
                "confidence": 0.85,
                "dialog": {"summary": dlg_summary, "buttons": buttons},
            }
            return decision

        # 2. Hay error real detectado? → ofrecer ayuda
        if ctx.get("has_error"):
            self._stream("reason", "Detecté error real en pantalla")
            decision = {
                "action": "offer_help",
                "reason": "[graph+error] error_real_detectado",
                "confidence": 0.85,
            }
            return decision

        # ── GRAPH-BASED REASONING (EidosBrain) ──────────────────────────

        try:
            from core.eidos_brain import get_brain
            brain = get_brain()

            if brain.is_ready:
                # Phase 1: Perception → GraphQuery
                query = brain.perceive_as_graph_query(ctx)

                # Phase 2: GraphQuery → Spreading Activation
                activated = brain.activate_brain(query, max_depth=3)

                # Phase 3: ActivatedContext → Decision
                actions = ["browse_web", "explore_filesystem",
                           "introspect", "observe"]
                brain_decision = brain.reason_from_activation(
                    activated,
                    available_actions=actions,
                )

                action = brain_decision.action
                now = time.time()

                # ── Throttling (time-based, graph doesn't know about time) ──
                if action == "explore_filesystem":
                    if now - self._last_filesystem_explore < self._fs_explore_every:
                        action = self._next_best_action(
                            brain_decision, exclude=["explore_filesystem"])
                if action == "browse_web":
                    if now - self._last_browse < self._browse_every:
                        action = "observe"  # No insistir — esperar
                        # Negative learning: throttled → weaken concept-action edges
                        brain.learn_from_outcome(brain_decision, "throttled")

                # Periodic introspection (idle too long → reflect)
                if now - self._last_reflection >= self.reflect_every:
                    if action in ("observe",) and brain_decision.confidence < 0.35:
                        action = "introspect"
                        self._last_reflection = now

                # Build decision dict (compatible with act() interface)
                decision = {
                    "action": action,
                    "reason": brain_decision.reason,
                    "confidence": brain_decision.confidence,
                }

                if brain_decision.primary_action:
                    decision["brain_supporting"] = (
                        brain_decision.primary_action.supporting_concepts[:5]
                    )
                    decision["brain_alternatives"] = [
                        (a.action, a.score)
                        for a in brain_decision.alternatives[:3]
                    ]
                    decision["brain_neurons"] = activated.total_neurons_fired

                # Store activated context for learning after outcome
                if action in ("browse_web", "explore_filesystem"):
                    self._last_brain_context = activated
                    self._last_brain_decision = brain_decision

                self._stream(
                    "reason",
                    f"[brain] {action} (conf={brain_decision.confidence:.2f}, "
                    f"neurons={activated.total_neurons_fired}) — "
                    f"{brain_decision.reason}",
                )

                log.info("Brain reason: %s (conf=%.2f, neurons=%d)",
                         action, brain_decision.confidence,
                         activated.total_neurons_fired)
                return decision

            else:
                # Graph not ready — fallback to keyword-based reasoning
                self._stream("reason", "Brain graph not ready — usando fallback")
                return self._reason_fallback(ctx)

        except Exception as e:
            self._stream("reason_error", f"Brain reasoning falló: {e}")
            log.warning("Brain reason error, usando fallback: %s", e)
            return self._reason_fallback(ctx)

    def _next_best_action(self, brain_decision, exclude: list) -> str:
        """Get the next best action from brain alternatives, excluding some."""
        if hasattr(brain_decision, 'alternatives') and brain_decision.alternatives:
            for alt in brain_decision.alternatives:
                if alt.action not in exclude:
                    return alt.action
        return "introspect"

    def _reason_fallback(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback reasoning when brain graph is not available.
        Uses lightweight keyword presence scoring (no hardcoded window names)."""
        decision = {"action": "observe", "reason": "fallback_default",
                    "confidence": 0.0}
        active = ctx.get("active_window", "").lower()
        visible = ctx.get("visible_text", "").lower()
        combined = f"{active} {visible[:500]}"

        # Keyword groups for action scoring (graph-independent fallback)
        action_keywords = {
            "browse_web": [
                "firefox", "chrome", "browser", "http", "www", "web",
                "chromium", "brave", "edge", "safari", "navegador",
                "internet", "url", "search", "page",
            ],
            "explore_filesystem": [
                "file", "folder", "directory", "terminal", "shell",
                "dolphin", "nautilus", "thunar", "konsole", "bash",
                "zsh", "linux", "path", "home", "root", "disk",
                "mount", "partition",
            ],
            "offer_help": [
                "error", "fail", "exception", "crash", "warning",
                "traceback", "cannot", "denied", "refused", "timeout",
                "missing", "broken", "problem", "bug", "critical",
            ],
            "introspect": [
                "idle", "desktop", "wallpaper", "empty",
            ],
        }

        scores = {}
        for action, keywords in action_keywords.items():
            score = sum(1 for kw in keywords if kw in combined)
            scores[action] = score

        best_action = max(scores, key=scores.get)
        best_score = scores[best_action]

        if best_score >= 1:  # S126: bajado de 2→1 para generar mas acciones
            decision = {
                "action": best_action,
                "reason": f"[fallback] {best_score} keywords matched: {best_action}",
                "confidence": min(best_score / 12.0, 0.45),
            }
        elif time.time() - self._last_reflection >= self.reflect_every:
            self._last_reflection = time.time()
            decision = {
                "action": "introspect",
                "reason": "[fallback] periodic_reflection",
                "confidence": 0.35,
            }
        else:
            # S126: Anti-degenerate-loop — cuando OCR vacio y sin keywords,
            # rotar acciones diversas cada _rotation_interval ciclos para
            # evitar el bucle percibir→recordar→razonar sin actuar nunca.
            self._action_rotation_idx += 1
            if self._action_rotation_idx >= self._rotation_interval:
                self._action_rotation_idx = 0
                # Get the action rotation idx from cycle count to diversify
                action_idx = (self._cycle_count // self._rotation_interval) % len(self._action_rotation)
                forced_action = self._action_rotation[action_idx]
                now = time.time()
                # Respect throttle windows so we don't spam
                if forced_action == "explore_filesystem" and now - self._last_filesystem_explore < self._fs_explore_every:
                    forced_action = "introspect"
                if forced_action == "browse_web" and now - self._last_browse < self._browse_every:
                    forced_action = "introspect"
                decision = {
                    "action": forced_action,
                    "reason": f"[fallback] S126 rotation: OCR empty, forcing {forced_action}",
                    "confidence": 0.30,
                }
                self._stream("reason",
                             f"[fallback] Rotando a {forced_action} "
                             f"(ciclo #{self._cycle_count}, OCR vacio)")

        self._stream("reason",
                     f"[fallback] {decision['action']} (score={best_score})")
        return decision

    # ─── ACTUAR ─────────────────────────────────────────────────────────────

    def act(self, decision: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
        """Ejecuta la acción decidida. Conservador: NO mueve mouse sin permiso explícito.
        Tras ejecutar, informa al cerebro neuronal para aprendizaje Hebbiano."""
        action = decision.get("action", "observe")
        outcome = None  # track for brain learning

        if action == "offer_help":
            try:
                from core.colony_proactive import push_message
                push_message(
                    actor="eidos_alive",
                    message="Veo un error real en tu pantalla. "
                            "Dime en /talk si quieres que lo analice.",
                    topic="error_alert",
                    priority=7,
                )
                self._stream("act", "Mensaje proactivo enviado (error_alert)")
                from core.episodic_memory import record_action
                record_action(
                    "offer_help", target="user",
                    context={"reason": decision["reason"]},
                    outcome="proactive_sent",
                )
                outcome = "success"
                return True
            except Exception as e:
                self._stream("act_error", f"offer_help falló: {e}")
                outcome = "failure"
                return False
            finally:
                self._brain_learn_from_outcome(outcome)

        elif action == "explore_filesystem":
            fs_result = self.explore_filesystem(ctx)
            if fs_result.get("explored"):
                self._stream("act",
                             f"Filesystem explorado: {fs_result.get('path', '?')} — "
                             f"{len(fs_result.get('landmarks_found', []))} landmarks nuevos")
                try:
                    from core.episodic_memory import record_action
                    record_action(
                        "explore_filesystem", target="filesystem",
                        context={"path": fs_result.get("path", ""),
                                  "landmarks": len(fs_result.get("landmarks_found", []))},
                        outcome="explored" if fs_result.get("explored") else "skipped",
                    )
                except Exception:
                    pass
                outcome = "success" if fs_result.get("explored") else "skipped"
                self._brain_learn_from_outcome(outcome)
                return True
            outcome = "failure"
            self._brain_learn_from_outcome(outcome)
            return False

        elif action == "browse_web":
            topic = decision.get("topic", "")
            bw_result = self.browse_web(topic=topic)
            if bw_result.get("ok"):
                self._stream("act",
                             f"Web navegada: {bw_result.get('url', '?')[:80]} — "
                             f"{bw_result.get('mode', '?')}")
                try:
                    from core.episodic_memory import record_action
                    record_action(
                        "browse_web", target="web",
                        context={"url": bw_result.get("url", ""),
                                  "topic": topic,
                                  "mode": bw_result.get("mode", "error")},
                        outcome="success",
                    )
                except Exception:
                    pass
                outcome = "success"
                self._brain_learn_from_outcome(outcome)
                return True
            outcome = "failure"
            self._brain_learn_from_outcome(outcome)
            return False

        elif action == "introspect":
            result = self.introspect()
            outcome = "success" if result else "failure"
            self._brain_learn_from_outcome(outcome)
            return result

        elif action == "wait_user_decision":
            self._stream("act", f"Esperando decisión SER sobre dialog: "
                                f"{decision.get('dialog', {})}")
            self._brain_learn_from_outcome("skipped")
            return True

        return False

    def _brain_learn_from_outcome(self, outcome: str):
        """Informa al cerebro neuronal del resultado de una acción para
        aprendizaje Hebbiano (fortalecer/debilitar conexiones sinápticas)."""
        if outcome is None:
            return
        if self._last_brain_decision is None:
            return
        try:
            from core.eidos_brain import get_brain
            brain = get_brain()
            if brain.is_ready:
                n = brain.learn_from_outcome(self._last_brain_decision, outcome)
                if n > 0:
                    log.debug("Brain learned: outcome=%s → %d edges adjusted", outcome, n)
        except Exception:
            pass

    # ─── REFLEXIONAR ────────────────────────────────────────────────────────

    def synthesize(self) -> Dict[str, Any]:
        """Genera conclusiones sintetizando conocimiento acumulado.
        Cierra el GAP #6: EIDOS no solo acumula datos, produce entendimiento.

        Analiza: grafo → patrones emergentes → hipótesis → conclusiones.
        Se ejecuta durante introspect() o cuando hay suficientes datos nuevos.
        """
        result = {"synthesized": False, "hypotheses": [], "conclusions": [],
                   "new_nodes_added": 0, "error": ""}
        try:
            import sqlite3
            from pathlib import Path
            db = Path.home() / ".eidos" / "evolution_brain.db"
            if not db.exists():
                return result

            with get_conn(db, timeout=10) as c:
                # 1. Encontrar clusters de conceptos relacionados sin conexión
                # Conceptos en la misma categoría pero sin aristas entre ellos
                unlinked = c.execute("""
                    SELECT n1.id, n2.id, n1.concept, n2.concept, n1.category
                    FROM knowledge_nodes n1
                    JOIN knowledge_nodes n2
                      ON n1.category = n2.category
                     AND n1.id < n2.id
                     AND n1.concept != n2.concept
                     AND n1.category NOT IN ('generic', 'unknown', '')
                    LEFT JOIN knowledge_edges e
                      ON (e.from_node = CAST(n1.id AS TEXT) AND e.to_node = CAST(n2.id AS TEXT))
                      OR (e.from_node = CAST(n2.id AS TEXT) AND e.to_node = CAST(n1.id AS TEXT))
                    WHERE e.id IS NULL
                    LIMIT 15
                """).fetchall()

                hypotheses = []
                for row in unlinked:
                    src_id, tgt_id, src_concept, tgt_concept, category = row
                    # Inferir relación plausible
                    rel_type = "related_to"
                    if category in ("security_tool", "kali_tool"):
                        rel_type = "used_with"
                    elif category in ("protocol", "network"):
                        rel_type = "operates_over"
                    elif category in ("programming_language", "framework"):
                        rel_type = "compatible_with"

                    hypothesis = (
                        f"'{src_concept}' y '{tgt_concept}' comparten categoría "
                        f"'{category}' — posible relación '{rel_type}'"
                    )
                    hypotheses.append({
                        "source": src_concept, "target": tgt_concept,
                        "relation": rel_type, "category": category,
                    })

                    # Intentar crear la arista en el grafo
                    try:
                        c.execute(
                            "INSERT OR IGNORE INTO knowledge_edges "
                            "(from_node, to_node, relation_type, strength) "
                            "VALUES (?, ?, ?, ?)",
                            (str(src_id), str(tgt_id), rel_type, 0.3),
                        )
                        result["new_nodes_added"] += 1
                    except Exception:
                        pass

                # 2. Contar conceptos por categoría para detectar desequilibrios
                categories = c.execute("""
                    SELECT category, COUNT(*) as cnt
                    FROM knowledge_nodes
                    WHERE category != ''
                    GROUP BY category
                    ORDER BY cnt DESC
                    LIMIT 20
                """).fetchall()

                if categories:
                    top_cat = categories[0][0]
                    top_count = categories[0][1]
                    # Si una categoría domina (>40% del total), hay sesgo
                    total = sum(row[1] for row in categories)
                    if total > 0 and top_count / total > 0.4:
                        result["conclusions"].append(
                            f"Alto sesgo en categoría '{top_cat}' "
                            f"({top_count}/{total} = {top_count/total:.0%}). "
                            f"Explorar categorías subrepresentadas."
                        )

                # 3. Detectar conceptos huérfanos (sin aristas)
                orphans = c.execute("""
                    SELECT n.concept FROM knowledge_nodes n
                    LEFT JOIN knowledge_edges e
                      ON e.from_node = CAST(n.id AS TEXT)
                      OR e.to_node = CAST(n.id AS TEXT)
                    WHERE e.id IS NULL AND n.category != ''
                    LIMIT 5
                """).fetchall()

                if orphans:
                    orphan_names = [o[0] for o in orphans]
                    result["conclusions"].append(
                        f"Conceptos huérfanos detectados: {orphan_names}. "
                        f"Necesitan self-research para conectarlos al grafo."
                    )

            if hypotheses or result["conclusions"]:
                result["synthesized"] = True
                result["hypotheses"] = hypotheses
                self._stream("synthesize",
                             f"Síntesis: {len(hypotheses)} hipótesis, "
                             f"{len(result['conclusions'])} conclusiones, "
                             f"+{result['new_nodes_added']} conexiones")
            else:
                self._stream("synthesize", "Sin novedades para sintetizar")

        except Exception as e:
            result["error"] = str(e)
            self._stream("synthesize_error", str(e))

        return result

    def introspect(self) -> bool:
        """Análisis periódico: stats grafo, episodios, apps aprendidas +
        meta-cognición: detecta vacíos, prioriza aprendizaje, sintetiza.

        [S125] Module 2 (eidos_metacog_real): Introspeccion REAL basada en datos.
        [S125] Module 4 (eidos_skill_tree): Skill tree reporting.
        [S125] Module 1 (eidos_memory_unified): Memory health check."""
        try:
            import sqlite3
            from pathlib import Path
            db = Path.home() / ".eidos" / "evolution_brain.db"
            with get_conn(db, timeout=5) as c:
                nodes = c.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
                edges = c.execute("SELECT COUNT(*) FROM knowledge_edges").fetchone()[0]
                apps_learned = c.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE concept LIKE 'app:%'"
                ).fetchone()[0]
                researched = c.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE source LIKE 'research:%'"
                ).fetchone()[0]
                tabula = c.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE source LIKE 'tabula_rasa:%'"
                ).fetchone()[0]
                total_cycles = max(self._cycle_count, 1)
                independence = round(self._autonomous_decisions / total_cycles * 100, 1)
                bottom_cats = c.execute(
                    "SELECT category, COUNT(*) as cnt FROM knowledge_nodes "
                    "WHERE category != '' GROUP BY category ORDER BY cnt ASC LIMIT 5"
                ).fetchall()
            from core.episodic_memory import get_episodic
            ep_stats = get_episodic().stats()

            density = round(edges / max(nodes, 1), 2)

            summary = (
                f"Grafo: {nodes} nodos, {edges} aristas (densidad {density}) | "
                f"Apps: {apps_learned} | Investigados: {researched} | "
                f"Tabula Rasa: {tabula} | Independencia: {independence}% | "
                f"Episodios: {ep_stats['total_episodes']} | Ciclos: {self._cycle_count}"
            )
            self._stream("introspect", summary, extra={
                "graph_nodes": nodes, "graph_edges": edges,
                "density": density, "independence_pct": independence,
                "autonomous_decisions": self._autonomous_decisions,
                "total_cycles": total_cycles,
                "apps_learned": apps_learned, "researched": researched,
                "tabula_rasa": tabula,
                "episodes": ep_stats["total_episodes"],
                "cycles": self._cycle_count,
            })

            # ── Module 2: MetaCognicion REAL ────────────────────────────────
            try:
                if self._metacog is None:
                    self._metacog = get_metacog_real()
                mc_report = self._metacog.introspect()
                self._stream("meta_cognition",
                             f"[MetacogReal] {mc_report.get('narrative_es', '')[:300]}",
                             extra={
                                 "capabilities": mc_report.get("capabilities"),
                                 "calibration": mc_report.get("calibration"),
                                 "traits": mc_report.get("traits"),
                                 "warnings_active": mc_report.get("warnings_active"),
                                 "total_attempts": mc_report.get("total_attempts"),
                             })
                # Registrar intentos en el capability model si hay capacidad nueva
                for cap_name in ["memory_semantic", "autonomous_learn", "read_code",
                                "execute_terminal", "search_web", "logical_reasoning"]:
                    self._metacog.record_outcome(
                        capability=cap_name,
                        success=True,
                        predicted_confidence=0.7,
                        task="introspect_cycle",
                        method="auto",
                        duration_ms=0,
                    )
            except Exception as mc_err:
                self._stream("introspect_error", f"MetacogReal: {mc_err}")

            # ── Module 4: Skill Tree ────────────────────────────────────────
            try:
                from core.eidos_skill_tree import get_skill_tree
                st = get_skill_tree()
                # Resumen de dominios
                domain_summary = st.domain_summary()
                top_domains = sorted(
                    domain_summary, key=lambda d: d.get("avg_mastery", 0), reverse=True
                )[:3]
                if top_domains:
                    domain_str = ", ".join(
                        f"{d['domain']}({d['node_count']}n/{d.get('avg_mastery', 0):.2f})"
                        for d in top_domains
                    )
                    self._stream("meta_cognition",
                                 f"[SkillTree] Top dominios: {domain_str}")

                # Que aprender ahora?
                recommendations = st.what_should_i_learn(top_n=3)
                if recommendations:
                    learn_now = recommendations[0]
                    self._stream("meta_cognition",
                                 f"[SkillTree] Recomendacion: {learn_now.get('concept', '?')} "
                                 f"({learn_now.get('domain', '?')}, "
                                 f"prioridad={learn_now.get('priority', 0):.1f}) — "
                                 f"{learn_now.get('reason', '')[:120]}")

                # Skills mas fuertes y debiles
                strongest = st.strongest_skills(3)
                weakest = st.weakest_skills(3)
                if strongest:
                    self._stream("meta_cognition",
                                 f"[SkillTree] Mas fuerte: {strongest[0].get('concept', '?')[:40]} "
                                 f"({strongest[0].get('mastery_level', '?')}, "
                                 f"score={strongest[0].get('overall_score', 0):.2f})")
                if weakest:
                    self._stream("meta_cognition",
                                 f"[SkillTree] Mas debil: {weakest[0].get('concept', '?')[:40]} "
                                 f"({weakest[0].get('mastery_level', '?')}, "
                                 f"prioridad={weakest[0].get('improvement_priority', 0):.1f})")
            except Exception as st_err:
                self._stream("introspect_error", f"SkillTree: {st_err}")

            # ── Module 1: Memory health check ────────────────────────────────
            try:
                mem_health = unified_memory.memory_health()
                if mem_health.get("health_score", 100) < 50:
                    self._stream("meta_cognition",
                                 f"[UnifiedMemory] Salud: {mem_health.get('health_score', 0)}/100 "
                                 f"({mem_health.get('status', '?')}) — "
                                 f"active={mem_health.get('active', 0)}, "
                                 f"at_risk={mem_health.get('at_risk', 0)}")
            except Exception:
                pass

            # Meta-cognición: áreas débiles (original)
            if bottom_cats:
                weak = [r[0] for r in bottom_cats[:3] if r[1] < 3]
                if weak:
                    self._stream("meta_cognition",
                                 f"Áreas débiles: {weak}. "
                                 f"Prioridad aprendizaje: {' > '.join(weak)}")

            # Meta-cognición: gaps procedimentales (original)
            try:
                from core.eidos_procedural import discover_procedural_gaps
                gaps = discover_procedural_gaps()
                if gaps:
                    high = [g for g in gaps if g.get("priority") == "high"]
                    self._stream("meta_cognition",
                                 f"Gaps procedimentales: {len(gaps)} conceptos sin receta "
                                 f"({len(high)} prioritarios)")
            except Exception:
                pass

            # Sintetizar conclusiones (original)
            try:
                synth = self.synthesize()
                if synth.get("synthesized"):
                    self._stream("meta_cognition",
                                 f"Síntesis: {len(synth.get('hypotheses', []))} "
                                 f"hipótesis, {len(synth.get('conclusions', []))} "
                                 f"conclusiones")
            except Exception:
                pass

            # Aprender patrones (original)
            try:
                from core.episodic_memory import get_episodic as _ge
                promoted = _ge().learn_patterns(min_repetitions=2, lookback_days=7)
                if promoted:
                    self._stream("introspect",
                                 f"Patrones promovidos: {promoted}")
            except Exception:
                pass

            if independence < 25:
                self._stream("meta_cognition",
                             f"Independencia {independence}% (<25%). "
                             f"Continuar self-research para >50%.")

            return True
        except Exception as e:
            self._stream("introspect_error", str(e))
            return False

    # ─── UNIFIED CYCLE (S127) ─────────────────────────────────────────────

    def unified_cycle(self):
        """Coordina TODOS los sub-ciclos con intervalos desescalonados (S127).

        Intervalos para evitar solapamiento entre sub-sistemas:
          - character_lifecycle → cada 300s (5 min)
          - curiosity           → cada 600s (10 min, ya integrado en cycle())
          - night_cycle         → solo de noche (22:00-06:00), revisado cada 3600s
          - brain_sync          → cada 1200s (20 min)
          - autonomous_research → cada 1800s (30 min)
          - study_queue         → cada 900s (15 min)

        El core perception-action loop (cycle()) se ejecuta SIEMPRE en cada
        iteracion. Los sub-ciclos largos se despachan segun su intervalo.
        """
        now = time.time()

        # ── Sub-cycle 1: Character Lifecycle (cada 300s = 5 min) ─────────
        if now - self._last_character_lifecycle >= self._character_lifecycle_every:
            self._last_character_lifecycle = now
            try:
                from core.character_lifecycle import get_lifecycle
                lc = get_lifecycle()
                # Restart any dead learning loops for active characters
                lc._restart_learning_loops()
                stats = lc.get_stats()
                if stats.get("total", 0) > 0:
                    self._stream("unified_cycle",
                                 f"CharacterLifecycle: {stats.get('total', 0)} chars, "
                                 f"{stats.get('learning', 0)} learning, "
                                 f"{stats.get('sovereign', 0)} sovereign")
            except Exception as e:
                log.debug("unified_cycle: character_lifecycle tick failed: %s", e)

        # ── Sub-cycle 2: Night Cycle (solo de noche 22:00-06:00) ────────
        if now - self._last_night_cycle >= self._night_cycle_every:
            self._last_night_cycle = now
            try:
                from core.time_awareness import get_time_context
                tctx = get_time_context()
                if tctx.get("is_night"):
                    try:
                        from core.night_cycle import run_night_cycle
                        nc_result = run_night_cycle()
                        self._stream("unified_cycle",
                                     f"NightCycle: ejecutado — "
                                     f"{nc_result.get('summary', 'ok') if isinstance(nc_result, dict) else 'ok'}")
                    except Exception:
                        # Fallback: eidos_night_cycle (5-phase psychological)
                        try:
                            from core.eidos_night_cycle import get_night_cycle
                            nc = get_night_cycle()
                            nc_result = nc.run()
                            self._stream("unified_cycle",
                                         f"EidosNightCycle: {nc_result.get('night_id', '?')} — "
                                         f"{len(nc_result.get('phases', {}))} phases")
                        except Exception as e2:
                            log.debug("unified_cycle: night_cycle failed: %s", e2)
            except Exception as e:
                log.debug("unified_cycle: time_awareness check failed: %s", e)

        # ── Sub-cycle 3: Brain Sync (cada 1200s = 20 min) ───────────────
        if now - self._last_brain_sync_tick >= self._brain_sync_every:
            self._last_brain_sync_tick = now
            try:
                from core.brain_sync import sync
                sync_result = sync(verbose=False)
                if sync_result.get("kali_to_mac") or sync_result.get("mac_to_kali"):
                    self._stream("unified_cycle",
                                 f"BrainSync: K→M={sync_result.get('kali_to_mac', 0)}, "
                                 f"M→K={sync_result.get('mac_to_kali', 0)}")
                elif sync_result.get("error"):
                    log.debug("unified_cycle: brain_sync skipped: %s", sync_result["error"])
            except Exception as e:
                log.debug("unified_cycle: brain_sync failed: %s", e)

        # ── Sub-cycle 4: Autonomous Research (cada 1800s = 30 min) ──────
        if now - self._last_autonomous_research >= self._autonomous_research_every:
            self._last_autonomous_research = now
            try:
                from core.autonomous_research_loop import AutonomousResearchLoop
                arl = AutonomousResearchLoop(interval=1800)
                ar_result = arl.cycle()  # una iteracion manual (non-blocking)
                if ar_result and ar_result.get("researched"):
                    self._stream("unified_cycle",
                                 f"AutonomousResearch: {ar_result.get('topics', 0)} topics, "
                                 f"+{ar_result.get('nodes_added', 0)} nodes")
            except Exception as e:
                log.debug("unified_cycle: autonomous_research failed: %s", e)

        # ── Sub-cycle 5: Study Queue (cada 900s = 15 min) ───────────────
        if now - self._last_study_queue >= self._study_queue_every:
            self._last_study_queue = now
            try:
                from core.study_queue import run_pending
                sq_result = run_pending(max_items=5, dry_run=True)
                if sq_result.get("processed", 0) > 0:
                    self._stream("unified_cycle",
                                 f"StudyQueue: {sq_result['processed']} processed, "
                                 f"{sq_result.get('learned', 0)} learned")
            except Exception as e:
                log.debug("unified_cycle: study_queue failed: %s", e)

        # ── Sub-cycle 6: Browser Deep Dive (cada 3600s = 1h) ──────────────
        # Toma el primer item pending con kind='web' o URL del study_queue
        # y hace una inmersión profunda con depth=2, max_pages=5.
        if now - self._last_browser_deep_dive >= 3600:
            try:
                from core.study_queue import list_items
                from core.eidos_browser import deep_dive
                pending = list_items("pending")
                web_item = None
                for item in pending:
                    directive = item.get("directive", "")
                    kind = item.get("kind", "")
                    # Check for web kind or URL in directive
                    if kind == "web" or any(token in directive for token in ("http://", "https://", "www.")):
                        web_item = item
                        break
                if web_item:
                    url = web_item["directive"]
                    topic = web_item.get("summary", "") or web_item.get("source", "") or "deep_dive"
                    gov = get_loop_governor()
                    ok = gov.run_guarded(
                        "browser_deep_dive",
                        lambda: deep_dive(url, topic, depth=2, max_pages=5),
                        timeout_s=120,
                    )
                    if ok:
                        self._stream("unified_cycle",
                                     f"BrowserDeepDive: completado — url={url[:80]} topic={topic[:60]}")
                        bus_publish("browser_deep_dive_done", {
                            "url": url, "topic": topic, "cycle": self._cycle_count,
                        }, source="orchestrator")
                    else:
                        self._stream("unified_cycle",
                                     f"BrowserDeepDive: timeout/cancelado — url={url[:80]}")
                self._last_browser_deep_dive = now
            except ImportError:
                log.debug("unified_cycle: browser_deep_dive skipped — eidos_browser.deep_dive not available")
                self._last_browser_deep_dive = now
            except Exception as e:
                log.debug("unified_cycle: browser_deep_dive failed: %s", e)
                self._last_browser_deep_dive = now

        # ── S127 Pipeline: investigar meta pendiente cada 4h ──────────────────
        if now - self._last_pipeline >= self._pipeline_every:
            try:
                from core.eidos_goals import get_goal_manager
                gm = get_goal_manager()
                next_action = gm.get_next_action()
                if next_action and next_action.get("description"):
                    topic = next_action["description"][:100]
                    gov = get_loop_governor()
                    def _run_pipeline():
                        from core.eidos_pipeline import deep_research
                        r = deep_research(topic, dry_run=False)
                        gm.update(next_action["goal_id"], {"result": "ok" if r["phases"][0].get("ok") else "fail"})
                        return r
                    if gov.run_guarded("pipeline", _run_pipeline, timeout_s=300):
                        self._stream("unified_cycle", f"Pipeline: {topic[:60]}", {"phases": "completed"})
                self._last_pipeline = now
            except Exception as e:
                log.debug("unified_cycle: pipeline failed: %s", e)
                self._last_pipeline = now

        # ── Core perception-action loop (SIEMPRE) ─────────────────────────
        self.cycle()

    # ─── LOOP VITAL ────────────────────────────────────────────────────────

    def cycle(self):
        """Un ciclo completo: percibir → aprender → recordar → razonar → actuar.
        + Fase 2-4: healer check, presence write, growth tracking.
        + Eventos del sistema nervioso: cada fase publica al EventBus.

        NOTA (S127): cycle() es el core perception-action loop. Para el ciclo
        completo que coordina TODOS los sub-sistemas (character_lifecycle,
        night_cycle, brain_sync, autonomous_research, study_queue), usar
        unified_cycle() que invoca este metodo tras despachar los sub-ciclos
        con intervalos desescalonados."""
        self._cycle_count += 1

        # Lazy init del event bus
        if self._event_bus is None:
            self._event_bus = get_event_bus()

        ctx = self.perceive()
        if not ctx:
            return

        # ── Evento: screenshot_taken ─────────────────────────────────────
        bus_publish("screenshot_taken", {
            "active_window": ctx.get("active_window", ""),
            "windows_open": len(ctx.get("windows_open", [])),
            "elements_count": len(ctx.get("elements", [])),
            "cycle": self._cycle_count,
        }, source="orchestrator")

        # ── Evento: window_changed ───────────────────────────────────────
        active = ctx.get("active_window", "")
        if active and active != self._last_active_window:
            bus_publish("window_changed", {
                "previous": self._last_active_window,
                "new_window": active,
                "cycle": self._cycle_count,
            }, source="orchestrator")
            self._last_active_window = active

        # ── Evento: error_detected ───────────────────────────────────────
        if ctx.get("has_error"):
            bus_publish("error_detected", {
                "active_window": active,
                "cycle": self._cycle_count,
            }, source="orchestrator")

        # ── Observational Learning: watch SER work ────────────────────────
        if self._observational_enabled:
            self.observe_ser(ctx)

        learned = self.learn_from_perception(ctx)

        # ── Evento: knowledge_added ──────────────────────────────────────
        if learned > 0:
            bus_publish("knowledge_added", {
                "concepts_learned": learned,
                "active_window": active,
                "cycle": self._cycle_count,
            }, source="orchestrator")

        memories = self.recall_relevant(ctx)
        ctx["recalled_episodes"] = len(memories)
        decision = self.reason(ctx)

        # ── Evento: decision_made ────────────────────────────────────────
        action_name = decision.get("action", "observe")
        if action_name != "observe":
            bus_publish("decision_made", {
                "action": action_name,
                "reason": decision.get("reason", "")[:200],
                "confidence": decision.get("confidence", 0.0),
                "cycle": self._cycle_count,
            }, source="orchestrator")

        if decision.get("action") not in (None, "observe"):
            acted = self.act(decision, ctx)
            if acted:
                self._autonomous_decisions += 1
                # ── Evento: action_executed ──────────────────────────
                bus_publish("action_executed", {
                    "action": action_name,
                    "dry_run": decision.get("dry_run", True),
                    "cycle": self._cycle_count,
                }, source="orchestrator")

        # ── Curiosity Engine: explore during idle cycles ──────────────
        now = time.time()
        if now - self._last_curiosity_tick >= self._curiosity_tick_every:
            self._last_curiosity_tick = now
            try:
                if self._curiosity_engine is None:
                    from core.eidos_curiosity_engine import get_curiosity_engine
                    self._curiosity_engine = get_curiosity_engine()
                    self._curiosity_engine._orchestrator = self
                tick_result = self._curiosity_engine.tick()
                if tick_result.get("ok"):
                    bus_publish("curiosity_explored", {
                        "item_type": tick_result.get("item_type", "?"),
                        "payload": tick_result.get("payload", "?")[:80],
                        "result_preview": tick_result.get("result_preview", "")[:100],
                        "night_mode": tick_result.get("night_mode", False),
                        "cycle": self._cycle_count,
                    }, source="orchestrator")
            except Exception as e:
                log.debug("Curiosity engine tick failed: %s", e)

        # ── FASE 2: Healer check ────────────────────────────────────────
        if now - self._healer_last_run >= self._healer_interval:
            self._healer_last_run = now
            try:
                from core.eidos_healer import get_healer
                healer = get_healer()
                report = healer.check_all()
                if not report.healthy:
                    self._stream("healer", f"⚠️ {len([c for c in report.checks if not c.healthy])} fallos")
                    # ── Evento: service_crashed por cada fallo ──────────
                    for check in report.checks:
                        if not check.healthy:
                            bus_publish("service_crashed", {
                                "service": check.component,
                                "error": check.detail,
                                "cycle": self._cycle_count,
                            }, source="orchestrator")
                    repaired = healer.repair_all(report)
                    # ── Evento: repair_attempted ────────────────────────
                    if repaired:
                        for check in report.checks:
                            if not check.healthy:
                                bus_publish("repair_attempted", {
                                    "service": check.component,
                                    "success": True,
                                    "cycle": self._cycle_count,
                                }, source="orchestrator")
                else:
                    self._stream("healer", "✅ All systems healthy")
            except Exception as e:
                self._stream("healer_error", str(e))

        # ── FASE 4: Presence write ───────────────────────────────────────
        try:
            from core.eidos_presence import get_presence
            if self._presence is None:
                self._presence = get_presence()
                self._presence.start_daemon()
            self._presence.set_cycle(self._cycle_count)
            self._presence.set_activity(f"Cycle {self._cycle_count}: perceiving {ctx.get('active_window', '?')[:40]}")
        except Exception:
            pass

        # ── FASE 3: Growth tracking ──────────────────────────────────────
        if now - self._last_growth_update >= 3600:  # cada hora
            self._last_growth_update = now
            try:
                from core.eidos_growth import get_growth_engine
                ge = get_growth_engine()
                snapshot = ge.compute_growth_index()
                self._growth_index = snapshot.growth_index
                self._stream("growth", f"Growth: {self._growth_index:.3f}")
            except Exception:
                pass
            self._autonomous_decisions += 1

        # ── S125 Module 3: Singularity Engine check ────────────────────────
        if now - self._last_singularity_check >= self._singularity_every:
            self._last_singularity_check = now
            singularity_config = Path.home() / ".eidos" / "singularity_enabled"
            if singularity_config.exists():
                try:
                    import json as _json
                    cfg = _json.loads(singularity_config.read_text())
                    if cfg.get("enabled"):
                        if self._singularity_engine is None:
                            from core.eidos_singularity import get_singularity_engine
                            self._singularity_engine = get_singularity_engine()
                        result = self._singularity_engine.run_once()
                        self._stream("singularity",
                                     f"Singularity cycle: {'SUCCESS' if result.success else 'FAILED'} "
                                     f"(stage={result.stage}, tier={result.tier}) — {result.message[:120]}")
                        bus_publish("singularity_cycle", {
                            "success": result.success,
                            "stage": result.stage,
                            "tier": result.tier,
                            "trust_before": result.trust_before,
                            "trust_after": result.trust_after,
                            "cycle": self._cycle_count,
                        }, source="orchestrator")
                except Exception as s_err:
                    self._stream("singularity_error", str(s_err))

        # ── S125 Module 1: Memory decay (cada 1h) ──────────────────────────
        if now - self._last_memory_decay >= 3600:
            self._last_memory_decay = now
            try:
                archived = unified_memory.decay()
                if archived > 0:
                    self._stream("memory_decay",
                                 f"UnifiedMemory: {archived} recuerdos archivados (decay)")
                # Consolidar cada 4h
                if self._cycle_count % 120 == 0:  # roughly every 4h at 30s/cycle
                    consolidated = unified_memory.consolidate()
                    if consolidated > 0:
                        self._stream("memory_decay",
                                     f"UnifiedMemory: {consolidated} recuerdos consolidados")
            except Exception as md_err:
                log.debug("Memory decay/consolidate failed: %s", md_err)

        # ── S125 Module 4: Gateway health check (cada 600s = 10min) ───────
        gateway_every = 600
        if now - getattr(self, '_last_gateway_check', 0) >= gateway_every:
            self._last_gateway_check = now
            try:
                from core.eidos_gateway import get_service_status as _gw_status
                # get_service_status returns dict {service_name: status_or_error}
                gw_status = _gw_status()
                unhealthy = [k for k, v in gw_status.items()
                            if v not in ("ok", "healthy")]
                if unhealthy:
                    self._stream("gateway", f"Gateway unhealthy: {unhealthy}")
                    bus_publish("gateway_degraded", {
                        "unhealthy_services": unhealthy,
                        "cycle": self._cycle_count,
                    }, source="orchestrator")
            except Exception as gw_err:
                log.debug("Gateway health check failed: %s", gw_err)

        # ── S125 Module 7: Skill Tree rebuild (cada 3600s = 1h) ───────────
        skill_tree_every = 3600
        if now - getattr(self, '_last_skill_tree', 0) >= skill_tree_every:
            self._last_skill_tree = now
            try:
                from core.eidos_skill_tree import get_skill_tree
                st = get_skill_tree()
                layout = st.build()
                total_nodes = layout.total_nodes()
                mastered = sum(1 for n in layout.nodes if n.overall_score >= 0.7)
                self._stream("skill_tree",
                            f"SkillTree: {total_nodes} skills, "
                            f"{mastered} mastered")
                bus_publish("skill_tree_updated", {
                    "total_skills": total_nodes,
                    "cycle": self._cycle_count,
                }, source="orchestrator")
            except Exception as st_err:
                log.debug("Skill tree rebuild failed: %s", st_err)

        # ── Relation Engine: enrich semantic types (Hallazgo Crítico #1) ────
        if now - self._last_relation_enrich >= self._relation_enrich_every:
            self._last_relation_enrich = now
            try:
                from core.relation_engine import get_relation_engine
                if self._relation_engine is None:
                    self._relation_engine = get_relation_engine(verbose=False)
                result = self._relation_engine.enrich_graph(
                    classify_limit=2000,
                    transitive_limit=500,
                    symmetric_limit=500,
                    suggest_limit=100,
                )
                if (result.edges_classified > 0 or
                        result.transitive_inferred > 0 or
                        result.symmetric_inferred > 0):
                    self._stream("relation_engine",
                                 f"Enriched: +{result.edges_classified} classified, "
                                 f"+{result.transitive_inferred} transitive, "
                                 f"+{result.symmetric_inferred} symmetric, "
                                 f"{result.contradictions_found} contradictions, "
                                 f"+{result.relations_suggested} suggested")
            except Exception as re_err:
                log.debug("Relation engine enrich failed: %s", re_err)

        # ── S125 Module 6: Voice listener (EIDOS_VOICE=1, cada 120s) ──────
        if os.environ.get("EIDOS_VOICE") == "1":
            voice_every = 120
            if now - getattr(self, '_last_voice_check', 0) >= voice_every:
                self._last_voice_check = now
                try:
                    from core.eidos_voice import listen as _voice_listen
                    heard = _voice_listen(timeout=2)
                    if heard and len(heard.strip()) > 2:
                        self._stream("voice", f"Escuchado: {heard[:100]}")
                        bus_publish("voice_heard", {
                            "text": heard[:200],
                            "cycle": self._cycle_count,
                        }, source="orchestrator")
                except Exception as v_err:
                    log.debug("Voice check failed: %s", v_err)

        # ── Evento: cycle_completed ──────────────────────────────────────
        bus_publish("cycle_completed", {
            "cycle": self._cycle_count,
            "autonomous_decisions": self._autonomous_decisions,
            "active_window": active,
        }, source="orchestrator")

    def _loop(self):
        """Background loop. Usa unified_cycle() (S127) para coordinar
        TODOS los sub-ciclos con intervalos desescalonados."""
        while self._running:
            t0 = time.time()
            try:
                self.unified_cycle()
            except Exception as e:
                self._stream("cycle_error", str(e))
            elapsed = time.time() - t0
            sleep_for = max(1.0, self.perceive_every - elapsed)
            time.sleep(sleep_for)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._stream("startup",
                     "EIDOS Alive Orchestrator iniciado. "
                     "Cerebro: grafo neuronal (35K nodos) + spreading activation. "
                     "LLMs/VLMs disponibles como herramientas externas.")
        log.info("AliveOrchestrator started (perceive_every=%ds)",
                 self.perceive_every)
        # ── Evento: orchestrator_started ─────────────────────────────────
        bus_publish("orchestrator_started", {
            "perceive_every": self.perceive_every,
            "reflect_every": self.reflect_every,
        }, source="orchestrator")

        # ── S125 Module 6: Voice (optional, EIDOS_VOICE=1) ────────────────
        if os.environ.get("EIDOS_VOICE") == "1" and not self._voice_running:
            self._voice_running = True
            self._voice_thread = threading.Thread(
                target=self._voice_loop, daemon=True, name="eidos-voice"
            )
            self._voice_thread.start()
            self._stream("startup", "Modulo de voz activado (EIDOS_VOICE=1)")

    def _voice_loop(self):
        """Background loop for voice recognition (Module 6).

        Prefiere colony_wake_word (Whisper + wake word detection) si esta disponible.
        Fallback a Vosk basic listen si Whisper no esta instalado.
        """
        # ── Intentar wake word daemon (Whisper + "EIDOS"/"colonia" detection) ──
        wake_started = False
        try:
            from core.colony_wake_word import get_wake_word_daemon
            daemon = get_wake_word_daemon()
            if daemon.start():
                wake_started = True
                self._stream("voice_mode", "Wake word activo (Whisper) — escuchando 'EIDOS'/'colonia'")
                log.info("Voice: colony_wake_word daemon started")
            else:
                self._stream("voice_warn", "Wake word daemon no pudo iniciar (Whisper no disponible?)")
        except Exception as e:
            log.debug("colony_wake_word import error: %s", e)

        if wake_started:
            # Wake word daemon corre en su propio thread — solo mantener vivo
            try:
                daemon = get_wake_word_daemon()
                while self._running and self._voice_running:
                    time.sleep(5)
                    # Publicar stats periodicas del daemon
                    stats = daemon.get_stats()
                    if stats.get("activations", 0) > 0:
                        bus_publish("wake_word_stats", {
                            "activations": stats.get("activations", 0),
                            "queries": stats.get("queries", 0),
                            "listening": stats.get("listening", False),
                        }, source="orchestrator")
            except Exception as e:
                log.debug("Wake word daemon error: %s", e)
            finally:
                try:
                    daemon.stop()
                except Exception:
                    pass
            return

        # ── Fallback: Vosk basic listen loop ─────────────────────────────
        try:
            from core.eidos_voice import listen
        except Exception:
            self._stream("voice_error", "No se pudo importar eidos_voice")
            self._voice_running = False
            return

        log.info("Voice listening loop started (Vosk fallback)")
        while self._running and self._voice_running:
            try:
                text = listen(lang="es", timeout=30)
                if text:
                    self._stream("voice_command", f"Voz detectada: '{text}'")
                    # Publish as event so other modules can react
                    bus_publish("voice_command", {
                        "text": text,
                        "lang": "es",
                        "cycle": self._cycle_count,
                    }, source="orchestrator")
            except Exception as e:
                log.debug("Voice listen error: %s", e)
                time.sleep(5)

    def stop(self):
        self._running = False
        # ── S125 Module 6: Stop voice thread ─────────────────────────────
        self._voice_running = False
        try:
            from core.colony_wake_word import get_wake_word_daemon
            daemon = get_wake_word_daemon()
            if daemon.is_running():
                daemon.stop()
        except Exception:
            pass
        if self._voice_thread:
            self._voice_thread.join(timeout=3)
        if self._thread:
            self._thread.join(timeout=5)
        self._stream("shutdown", f"Detenido tras {self._cycle_count} ciclos")
        # ── Evento: orchestrator_stopped ─────────────────────────────────
        bus_publish("orchestrator_stopped", {
            "total_cycles": self._cycle_count,
            "autonomous_decisions": self._autonomous_decisions,
        }, source="orchestrator")


_SINGLETON: Optional[AliveOrchestrator] = None


def get_alive() -> AliveOrchestrator:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = AliveOrchestrator(perceive_every=30, reflect_every=300)
    return _SINGLETON


def read_recent_stream(n: int = 50) -> List[Dict[str, Any]]:
    """Lee los últimos N eventos del stream of consciousness."""
    if not STREAM_LOG.exists():
        return []
    try:
        with open(STREAM_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(l) for l in lines if l.strip()]
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    if "--once" in sys.argv:
        a = get_alive()
        a.cycle()
        print(json.dumps(read_recent_stream(10), indent=2, ensure_ascii=False))
    elif "--stream" in sys.argv:
        events = read_recent_stream(20)
        for e in events:
            print(f"[{e['ts'][11:19]}] [{e['kind']:18}] {e['message']}")
    else:
        # Bucle directo (Ctrl+C para parar)
        a = get_alive()
        a.start()
        try:
            while True:
                time.sleep(60)
                print(f"Ciclos: {a._cycle_count}, "
                      f"conceptos conocidos: {len(a._known_concepts_seen)}")
        except KeyboardInterrupt:
            a.stop()
            print("Stopped.")
