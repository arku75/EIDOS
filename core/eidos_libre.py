"""
core/eidos_libre.py — Modo libre: bucle ver→razonar→actuar→aprender

Activado por `eidos libre`. Para con `eidos quieto` o Ctrl+C.

Bucle continuo:
  1. Capturar pantalla (realtime_vision)
  2. Razonar: ¿qué veo? ¿qué puedo aprender o hacer?
  3. Decidir acción segura (Constitution + lista whitelist)
  4. Ejecutar (lectura, click verificado, etc)
  5. Aprender del resultado

Limitado por:
  - Constitution (no rm -rf, no comandos peligrosos)
  - Whitelist de acciones permitidas
  - Modo dry-run por defecto (sólo observa+aprende, no actúa)

Para activar acciones reales:
    EIDOS_LIBRE_REAL_ACTIONS=1 eidos libre
"""
from __future__ import annotations

import os
import sys
import sqlite3
import time
import threading
import logging
import signal
from pathlib import Path
from typing import Optional, Dict, Any, List
from core.db import get_conn

log = logging.getLogger("eidos.libre")

LEARNING_LOG = Path.home() / ".eidos" / "learning_log.md"

# Acciones permitidas (whitelist) — todas READ-ONLY
SAFE_ACTIONS = {
    "observe_screen":    "Capturar y analizar pantalla actual",
    "read_file":         "Leer un archivo (ya en knowledge)",
    "ask_ollama":        "Hacer pregunta interna a Ollama",
    "research_concept":  "Investigar un concepto via knowledge_nodes + man pages",
    "spawn_thought":     "Generar un pensamiento sobre lo que ve",
}


class EidosLibre:
    """Modo libre — EIDOS observa, razona y aprende sin parar."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._curriculum_step = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        # Dedup: temas ya estudiados en esta sesión (evita repetir n8n/docker 4 veces)
        self._studied_topics: set = set()
        self._studied_timestamps: dict = {}
        self._stats: Dict[str, Any] = {
            "iterations": 0,
            "screens_observed": 0,
            "thoughts_generated": 0,
            "actions_attempted": 0,
            "actions_succeeded": 0,
            "started_at": None,
        }
        self._real_actions = os.environ.get(
            "EIDOS_LIBRE_REAL_ACTIONS", "0"
        ).strip() == "1"
        # Rotación forzada: garantiza que todos los personajes actúen.
        # Excluye colony_coder — el contexto Python ya lo activa en las otras iters.
        self._rotation_others = [
            "colony_operator", "colony_analyst", "colony_vision",
            "colony_ser", "colony_lumen", "colony_general",
            "colony_centinela",
        ]
        self._rotation_index = 0   # apunta al próximo personaje no-coder

    def start(self, interval_seconds: float = 30.0) -> None:
        """Activa el bucle libre."""
        if self._thread and self._thread.is_alive():
            log.info("Modo libre ya activo")
            return
        self._stop.clear()
        self._stats["started_at"] = time.time()
        mode = "REAL_ACTIONS" if self._real_actions else "OBSERVE_ONLY"
        log.info("🦅 EIDOS LIBRE iniciado — modo %s, intervalo %ds",
                 mode, interval_seconds)
        self._log_diary_header()
        self._thread = threading.Thread(
            target=self._loop, args=(interval_seconds,),
            daemon=True, name="eidos-libre"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        log.info("EIDOS LIBRE detenido — %d iteraciones", self._stats["iterations"])

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict[str, Any]:
        s = dict(self._stats)
        s["running"]      = self.is_running()
        s["mode"]         = "REAL_ACTIONS" if self._real_actions else "OBSERVE_ONLY"
        s["safe_actions"] = list(SAFE_ACTIONS.keys())
        if s["started_at"]:
            s["uptime_minutes"] = round((time.time() - s["started_at"]) / 60, 1)
        return s

    def _log_diary_header(self) -> None:
        try:
            LEARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LEARNING_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n## 🦅 Sesión EIDOS LIBRE — {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        except Exception:
            pass  # error no crítico, continuar
    def _loop(self, interval_seconds: float) -> None:
        while not self._stop.is_set():
            try:
                # Fase 2: pedir slot al conductor antes de actuar
                try:
                    from core.colony_conductor import get_conductor, NORMAL
                    if not get_conductor().request_slot("libre", priority=1):
                        self._stop.wait(timeout=15)
                        continue
                except Exception:
                    pass  # error no crítico, continuar
                self._iteration()
            except Exception as e:
                log.exception("Iteración libre falló: %s", e)
                # Fase 5: broadcastear el error para que el healer lo recoja
                try:
                    from core.colony_broadcast import get_broadcast
                    get_broadcast().broadcast(
                        "colony_libre", f"Error en iteración: {e}", msg_type="alert"
                    )
                except Exception:
                    pass  # error no crítico, continuar
            finally:
                try:
                    from core.colony_conductor import get_conductor
                    get_conductor().release_slot("libre")
                except Exception:
                    pass  # error no crítico, continuar
            self._stop.wait(timeout=interval_seconds)

    def _iteration(self) -> None:
        """Una iteración: ver → razonar → experimentar → aprender → broadcastear."""
        self._stats["iterations"] += 1

        # S124: fase BOM opcional — bucle causal pulido (percibir AT-SPI2 → razonar con
        # el grafo + CURIOSIDAD hacia lo desconocido → actuar(frenos) → verificar → aprender/
        # reforzar). GATED por EIDOS_BOM=1 (off por defecto). dry_run atado a _real_actions:
        # solo actúa de verdad con EIDOS_LIBRE_REAL_ACTIONS=1. try/except: nunca rompe el loop.
        if os.environ.get("EIDOS_BOM") == "1":
            try:
                from core.causal_loop import step as _bom_step
                _bom = _bom_step(
                    goal=os.environ.get("EIDOS_BOM_GOAL", "explora y aprende la app activa"),
                    dry_run=not self._real_actions)
                log.info("🦅 BOM: %s", _bom)
            except Exception as e:
                log.debug("BOM phase: %s", e)

        # S125: "DEJARLO CORRER SOLO" — si SER dejó tareas en la cola de estudio, EIDOS
        # trabaja UNA por iteración: navega la web SOLO (headless), consulta sus IAs,
        # se registra donde haga falta, investiga lo que no sabe y lo deja en el informe
        # / lo pregunta a SER (si está). Una por vuelta = ritmo sano, no martillea.
        try:
            from core.study_queue import list_items, run_pending
            if list_items("pending"):
                _sq = run_pending(max_items=1, dry_run=not self._real_actions)
                log.info("📚 cola de estudio: aprendidas=%s falladas=%s → %s",
                         _sq.get("learned"), _sq.get("failed"), _sq.get("report"))
                return  # iteración dedicada a la tarea de SER
        except Exception as e:
            log.debug("study_queue phase: %s", e)

        # 0. TEMA DEL USUARIO — si hay uno pendiente, investigar inmediatamente
        user_topic = self._get_user_topic()
        if user_topic:
            topic_key = user_topic.lower().strip()
            # Dedup: saltarse si ya se estudió en los últimos 60 min
            last_ts = self._studied_timestamps.get(topic_key, 0)
            if time.time() - last_ts < 3600:
                log.info("⏭ Tema ya estudiado recientemente, eligiendo otro: %s", user_topic[:40])
                # Limpiar el archivo para que elija uno nuevo
                (Path.home() / ".eidos" / "libre_topic.txt").unlink(missing_ok=True)
            else:
                log.info("📌 Investigando tema de SER: %s", user_topic[:80])
                self._studied_topics.add(topic_key)
                self._studied_timestamps[topic_key] = time.time()
                try:
                    from core.colony_studier import get_studier
                    studier = get_studier()
                    # Buscar URLs con dork mejorado → leer TODAS exhaustivamente
                    dork_query = self._build_dork_query(user_topic)
                    log.info("🔍 Dork query: %s", dork_query[:80])
                    study_result = studier.study_topic(dork_query)
                    pages_read = study_result.get("pages_read", [])
                    nodes_added = study_result.get("nodes_added", 0)
                    log.info("📚 Web study exhaustivo '%s': %d páginas, +%d nodos",
                             user_topic[:40], len(pages_read), nodes_added)
                    # Para cada URL encontrada: BFS exhaustivo siguiendo TODOS los sublinks
                    for url in pages_read[:3]:
                        if url.startswith("http"):
                            log.info("🕷 BFS exhaustivo en: %s", url[:80])
                            studier.study_url(url, depth=4, visible=False)
                    # Consolidar con deep research
                    result = self._deep_research(user_topic)
                    self._stats["actions_attempted"] += 1
                    self._stats["actions_succeeded"] += 1
                    self._stats["thoughts_generated"] += 1
                except Exception as e:
                    log.warning("Deep research falló: %s", e)
            return  # Iteración dedicada al tema del usuario

        # 0b. GAPS DE CONOCIMIENTO — investigar en web si hay gaps pendientes
        try:
            _cli_active = Path.home() / ".eidos" / "cli_active"
            if not _cli_active.exists():  # Solo si CLI no está activo
                from core.eidos_curiosity import get_curiosity
                gaps = get_curiosity().get_high_priority_gaps(limit=1)
                if gaps:
                    gap_topic = gaps[0].replace("estudio:", "").strip()
                    log.info("🔍 Gap detectado, estudiando: %s", gap_topic[:40])
                    from core.colony_studier import get_studier
                    get_studier().study_topic(gap_topic, max_pages=2)
        except Exception:
            pass  # error no crítico, continuar

        # 1. OBSERVAR — capturar pantalla + archivo activo en VSCode
        screen_context = self._observe_screen()
        if not screen_context:
            return
        screen_context = self._enrich_with_vscode(screen_context)

        # 2. ENRUTAR — elegir personaje según el contexto
        character = self._route_to_character(screen_context)

        # 3. EXPLORAR — el personaje escanea su dominio del PC
        pc_context = self._explore_pc_domain(character)
        if pc_context:
            screen_context["pc_exploration"] = pc_context

        # 4. ESCUCHAR — leer lo que otros personajes aprendieron
        peer_knowledge = self._read_peer_broadcasts(character)
        if peer_knowledge:
            screen_context["peer_knowledge"] = peer_knowledge

        # 5. RAZONAR + EXPERIMENTAR — el personaje genera pensamiento y ejecuta
        thought = self._reason_with_character(character, screen_context)
        if not thought:
            return
        self._stats["thoughts_generated"] += 1

        # 6. APRENDER — guardar + aprendizaje específico del personaje
        self._learn_and_investigate(thought, screen_context, character)

        # 7. SÍNTESIS — cada 5 iteraciones, Lumen sintetiza lo aprendido
        if self._stats["iterations"] % 5 == 0:
            self._lumen_synthesis()

    def _observe_screen(self) -> Optional[Dict[str, Any]]:
        """Captura el estado actual de pantalla + contexto del observer.
        Usa EidosObserver si está activo, o captura puntual si no."""
        # Intentar obtener datos del observer en tiempo real (más rico)
        try:
            from core.eidos_observer import get_observer
            obs = get_observer()
            if obs._running and obs._last_screen_text:
                self._stats["screens_observed"] += 1
                return {
                    "timestamp": time.time(),
                    "type": "observer",
                    "active_window": obs._last_window,
                    "screen_text":   obs._last_screen_text[:400],
                    "mouse_pos":     obs._mouse_pos,
                    "audio":         obs._audio_transcript[:200],
                    "data": f"Ventana: {obs._last_window}\n{obs._last_screen_text[:300]}",
                }
        except Exception:
            pass
        # Fallback: lista de ventanas con wmctrl (ligero, sin screenshot ni tesseract)
        try:
            from core.screen_scanner import scan_windows
            result = scan_windows(use_vision=False)
            windows = result.get("windows", []) if isinstance(result, dict) else []
            win_list = ", ".join(w.get("name", "?") for w in windows[:8]) if windows else "ninguna"
            self._stats["screens_observed"] += 1
            return {
                "timestamp": time.time(),
                "type": "window_list",
                "active_window": windows[0].get("name", "?") if windows else "?",
                "data": f"Ventanas abiertas: {win_list}",
            }
        except Exception as e:
            log.debug("observe_screen: %s", e)
        # Fallback: usar GUI observer si vision no responde
        try:
            from core.gui_observer import get_observer
            obs = get_observer()
            if hasattr(obs, "current_state"):
                state = obs.current_state
                self._stats["screens_observed"] += 1
                return {"timestamp": time.time(), "type": "gui_state",
                        "context": getattr(state, "context", "?"),
                        "window": getattr(state, "active_window", "?")}
        except Exception:
            pass  # error no crítico, continuar
        # Fallback garantizado — siempre devuelve contexto básico del sistema
        import datetime
        import subprocess
        try:
            cwd = subprocess.check_output(["pwd"], text=True).strip()
        except Exception:
            cwd = str(Path.home())
        self._stats["screens_observed"] += 1
        return {
            "timestamp": time.time(),
            "type": "system_context",
            "context": f"Kali Linux — directorio {cwd}",
            "window": datetime.datetime.now().strftime("%H:%M"),
        }

    def _enrich_with_vscode(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Añade el archivo activo de VSCode al contexto si está disponible."""
        try:
            import urllib.request, json as _json
            req = urllib.request.urlopen("http://localhost:8765/api/active-file", timeout=1)
            data = _json.loads(req.read())
            if data.get("path"):
                context["vscode_file"]     = data["path"]
                context["vscode_language"] = data.get("language", "")
                context["vscode_content"]  = (data.get("content") or "")[:500]
        except Exception:
            pass  # error no crítico, continuar
        return context

    def _route_to_character(self, context: Dict[str, Any]) -> str:
        """
        Elige el personaje. Cada 3 iteraciones usa rotación forzada para que
        todos los personajes aprendan, sin importar qué archivo hay abierto.
        Las otras 2 de cada 3 usan afinidad de contexto.
        """
        iter_count = self._stats.get("iterations", 0)

        # Cada 3ª iteración: un personaje distinto de coder (rotación forzada)
        if iter_count % 3 == 0:
            char = self._rotation_others[self._rotation_index % len(self._rotation_others)]
            self._rotation_index += 1
            log.info("🔄 Rotación → %s (iter %d)", char.split('_')[1].upper(), iter_count)
            return char

        # Las otras 2: afinidad de contexto
        lang  = context.get("vscode_language", "").lower()
        ctx   = context.get("context", "").lower()
        ctype = context.get("type", "")

        if lang in ("python", "javascript", "typescript", "go", "rust", "c", "cpp", "java"):
            return "colony_coder"
        if lang in ("shellscript", "bash", "zsh") or "terminal" in ctx:
            return "colony_operator"
        if ctype == "screenshot" or "imagen" in ctx or "visual" in ctx:
            return "colony_vision"
        if lang in ("markdown", "plaintext") or "docs" in ctx or "readme" in ctx:
            return "colony_analyst"
        import random
        return random.choice(["colony_ser", "colony_general", "colony_lumen"])

    def _explore_pc_domain(self, character: str) -> str:
        """El personaje explora su dominio del PC (read-only, con timeout)."""
        try:
            from core.colony_pc_explorer import get_explorer
            result = get_explorer().explore(character)
            if result and result.summary and len(result.summary) > 20:
                return result.summary[:600]
        except Exception as e:
            log.debug("pc_explore %s: %s", character, e)
        return ""

    def _read_peer_broadcasts(self, character: str) -> str:
        """Lee lo que los otros personajes aprendieron recientemente."""
        try:
            from core.colony_broadcast import get_broadcast
            return get_broadcast().get_peer_knowledge(character, limit=3)
        except Exception:
            return ""

    def _get_own_knowledge(self, character: str, limit: int = 3) -> str:
        """Recupera el conocimiento previo acumulado por este personaje."""
        try:
            brain_db = Path.home() / ".eidos" / "evolution_brain.db"
            if not brain_db.exists():
                return ""
            conn = get_conn(brain_db, timeout=3)
            conn.execute("PRAGMA journal_mode=WAL")
            rows = conn.execute(
                "SELECT definition FROM knowledge_nodes "
                "WHERE source LIKE ? ORDER BY last_used DESC LIMIT ?",
                (f"%{character}%", limit)
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            if not rows:
                return ""
            items = [r[0][:120] for r in rows]
            return "[Lo que ya sé]\n" + "\n".join(f"• {x}" for x in items)
        except Exception:
            return ""

    def _reason_with_character(self, character: str, context: Dict[str, Any]) -> Optional[str]:
        """El personaje razona sobre lo que ve, experimenta en sandbox y genera pensamiento."""
        ctx_summary  = context.get("context", context.get("type", "pantalla"))
        peer_info    = context.get("peer_knowledge", "")
        vscode_file  = context.get("vscode_file", "")
        vscode_lang  = context.get("vscode_language", "")
        vscode_info  = f" (VSCode: {vscode_file.split('/')[-1]} — {vscode_lang})" if vscode_file else ""

        # ── EXPERIMENTO EN SANDBOX ──────────────────────────────────────────
        sandbox_result = ""
        try:
            from core.character_sandbox import get_sandbox
            sb = get_sandbox(character)

            if character == "colony_coder":
                # Coder: prueba librerías útiles del host o analiza estructura
                import random
                libs = ["pathlib", "json", "hashlib", "sqlite3", "threading", "dataclasses",
                        "typing", "functools", "itertools", "collections", "asyncio"]
                topic = random.choice(libs)
                r = sb.run_experiment(topic, timeout=20)
                if r.success and r.output.strip():
                    sandbox_result = f"[Python:{topic}]\n{r.output.strip()[:200]}"

            elif character == "colony_operator":
                # Operator: herramientas seguras del sistema
                import random
                tool = random.choice(["git", "ss", "lsof", "df", "ps"])
                r = sb.run_experiment(tool, timeout=12)
                if r.success and r.output.strip():
                    sandbox_result = f"[Sistema:{tool}]\n{r.output.strip()[:200]}"

            elif character == "colony_analyst":
                # Analyst: analiza un documento de EIDOS
                import random
                docs = list(Path("/home/ser/EIDOS").glob("*.md")) + \
                       list(Path("/home/ser/EIDOS").glob("core/*.py"))
                if docs:
                    doc = random.choice(docs[:20])
                    content = doc.read_text(encoding="utf-8", errors="ignore")[:400]
                    r = sb.run(
                        f"text={repr(content)}\nwords=len(text.split())\n"
                        f"lines=len(text.splitlines())\n"
                        f"print(f'{doc.name}: {{words}} palabras, {{lines}} líneas')",
                        language="python", timeout=8, learn=False
                    )
                    if r.success:
                        sandbox_result = f"[Análisis:{doc.name}] {r.output.strip()}"

            elif character == "colony_vision":
                # Vision: lista recursos visuales del sistema
                r = sb.run(
                    "import subprocess\n"
                    "r=subprocess.run(['find','/home/ser','-name','*.png','-o','-name','*.jpg','-o','-name','*.svg'],"
                    "capture_output=True,text=True,timeout=3)\n"
                    "lines=r.stdout.strip().splitlines()[:8]\n"
                    "print(f'{len(lines)} archivos visuales encontrados')\n"
                    "[print(l.split('/')[-1]) for l in lines]",
                    language="python", timeout=10, learn=False
                )
                if r.success and r.output.strip():
                    sandbox_result = f"[Visual]\n{r.output.strip()[:200]}"

            elif character == "colony_ser":
                # SER: lee su propio cerebro y reflexiona
                brain_path = Path.home() / "SER_BRAIN.md"
                if brain_path.exists():
                    content = brain_path.read_text(errors="ignore")[:500]
                    r = sb.run(
                        f"brain={repr(content)}\n"
                        f"print('SER_BRAIN secciones:', brain.count('#'))\n"
                        f"print('Primera sección:', brain.split('#')[1][:100] if '#' in brain else 'N/A')",
                        language="python", timeout=8, learn=False
                    )
                    if r.success:
                        sandbox_result = f"[SER_BRAIN]\n{r.output.strip()[:200]}"

            elif character == "colony_lumen":
                # Lumen: estadísticas del cerebro de conocimiento
                r = sb.run(
                    "import sqlite3\nfrom pathlib import Path\n"
                    "db=Path.home()/'.eidos'/'evolution_brain.db'\n"
                    "if db.exists():\n"
                    "    c=sqlite3.connect(str(db))\n"
                    "    total=c.execute('SELECT COUNT(*) FROM knowledge_nodes').fetchone()[0]\n"
                    "    high=c.execute('SELECT COUNT(*) FROM knowledge_nodes WHERE confidence>0.8').fetchone()[0]\n"
                    "    print(f'Nodos: {total} total, {high} alta confianza')\n"
                    "    top=c.execute('SELECT concept FROM knowledge_nodes ORDER BY usage_count DESC LIMIT 3').fetchall()\n"
                    "    [print('Top:', r[0][:60]) for r in top]\n"
                    "    c.close()",
                    language="python", timeout=10, learn=False
                )
                if r.success and r.output.strip():
                    sandbox_result = f"[Brain stats]\n{r.output.strip()[:200]}"

            elif character == "colony_general":
                # General: experimento aleatorio cruzado
                import random
                topic = random.choice(["os", "sys", "time", "random", "math", "re"])
                r = sb.run_experiment(topic, timeout=10)
                if r.success and r.output.strip():
                    sandbox_result = f"[General:{topic}]\n{r.output.strip()[:200]}"

        except Exception as e:
            log.debug("sandbox_experiment en libre: %s", e)

        # ── CONOCIMIENTO PROPIO DEL PERSONAJE ───────────────────────────────
        own_knowledge = self._get_own_knowledge(character, limit=3)

        # ── PROMPT CON CONTEXTO COMPLETO ────────────────────────────────────
        char_name = character.split("_")[1].upper()
        char_roles = {
            "colony_coder":    "programador experto en Python/EIDOS",
            "colony_operator": "operador de sistemas Linux",
            "colony_vision":   "experto en recursos visuales y multimedia",
            "colony_analyst":  "analista de datos y documentación",
            "colony_ser":      "SER, el creador y supervisor de EIDOS",
            "colony_lumen":    "sintetizador de conocimiento de Colony",
            "colony_general":  "coordinador general de Colony",
        }
        role = char_roles.get(character, "personaje de Colony")
        base = f"Soy {role}. Contexto actual: {ctx_summary}{vscode_info}."

        # Contexto del PC (exploración del dominio propio)
        pc_info = context.get("pc_exploration", "")

        # Construir prompt completo — conocimiento propio + PC + sandbox + peers
        ollama_prompt_parts = [base]
        if own_knowledge:
            ollama_prompt_parts.append(own_knowledge)
        if pc_info:
            ollama_prompt_parts.append(f"[Mi dominio del PC]\n{pc_info[:350]}")
        if sandbox_result:
            ollama_prompt_parts.append(sandbox_result)
        if peer_info:
            ollama_prompt_parts.append(peer_info)
        ollama_prompt_parts.append(
            "Basándome en todo lo anterior, ¿qué nuevo insight obtengo? "
            "Responde en 1 frase concisa que añada valor a lo que ya sé."
        )
        full_prompt = "\n".join(ollama_prompt_parts)

        # ── OLLAMA vía cola serializada — timeout corto para no bloquear Colony ─
        thought = sandbox_result or base  # fallback si Ollama no responde
        try:
            from core.ollama_queue import get_ollama_queue, BACKGROUND
            ollama_resp = get_ollama_queue().ask(
                prompt=full_prompt,
                model="lfm2.5-thinking:1.2b",
                priority=BACKGROUND,
                timeout=50,
                options={"num_predict": 60}
            )
            if ollama_resp and len(ollama_resp) > 10:
                thought = f"[{char_name}] {ollama_resp}"
                if sandbox_result:
                    thought += f" | sandbox: {sandbox_result[:80]}"
        except Exception:
            if sandbox_result:
                thought = f"[{char_name}] {sandbox_result}"

        return thought

    def _learn_and_investigate(self, thought: str, context: Dict[str, Any],
                               character: str = "colony_general") -> None:
        """Guarda el pensamiento del personaje en la chronicle y en knowledge_nodes."""
        char_tag = character.split("_")[1].upper() if "_" in character else character.upper()
        log.info("💭 [%s] %s", char_tag, thought[:120])
        try:
            from core.colony_chronicle import get_chronicle
            get_chronicle().record(
                character, "libre_thought",
                thought[:300],
                metadata={"context_type": context.get("type"),
                          "vscode_file": context.get("vscode_file"),
                          "character": character},
                importance=0.5,
            )
        except Exception:
            pass  # error no crítico, continuar
        # Guardar como knowledge_node
        try:
            import sqlite3, hashlib, time as _t
            brain_db = Path.home() / ".eidos" / "evolution_brain.db"
            if brain_db.exists():
                conn = get_conn(brain_db, timeout=5)
                conn.execute("PRAGMA journal_mode=WAL")
                now = _t.time()
                concept = f"libre:{character}:{thought[:60]}"
                node_id = hashlib.md5(concept.encode()).hexdigest()[:16]
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_nodes "
                    "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                    "VALUES (?,?,?,?,?,?,?,1)",
                    (node_id, concept[:120], thought[:500], f"eidos_libre:{character}", 0.6, now, now)
                )
                conn.commit()
                pass  # S109: get_conn no necesita close()
        except Exception:
            pass  # error no crítico, continuar
        # Modo REAL_ACTIONS: EIDOS toma acciones reales (browser, lectura, etc)
        if self._real_actions:
            self._stats["actions_attempted"] += 1
            try:
                action_taken = self._take_action(context)
                if action_taken:
                    self._stats["actions_succeeded"] += 1
                    log.info("Acción ejecutada: %s", action_taken)
            except Exception as e:
                log.debug("Acción falló: %s", e)

        # Broadcastear a Colony para que los otros personajes lo lean
        try:
            from core.colony_broadcast import get_broadcast
            get_broadcast().broadcast(character, thought[:300], msg_type="learning")
        except Exception:
            pass  # error no crítico, continuar
        # Log al diario
        try:
            with open(LEARNING_LOG, "a", encoding="utf-8") as f:
                ts = time.strftime("%H:%M:%S")
                f.write(f"- [{ts}] {thought[:200]}\n")
        except Exception:
            pass  # error no crítico, continuar
    def _lumen_synthesis(self) -> None:
        """Cada 5 iteraciones, Lumen sintetiza lo aprendido por todos los personajes."""
        try:
            import sqlite3
            brain_db = Path.home() / ".eidos" / "evolution_brain.db"
            if not brain_db.exists():
                return
            conn = get_conn(brain_db, timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            rows = conn.execute(
                "SELECT concept, definition FROM knowledge_nodes "
                "WHERE source LIKE 'eidos_libre:%' "
                "ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            pass  # S109: get_conn no necesita close()
            if not rows:
                return
            resumen = "\n".join(f"- {r[1][:100]}" for r in rows)
            prompt = (
                f"Soy Lumen, el razonador de Colony. Los personajes han observado:\n{resumen}\n\n"
                f"¿Qué patrón común emerge? Dame UNA conclusión en 1 frase."
            )
            # Fase 1: usar cola con prioridad BACKGROUND
            from core.ollama_queue import get_ollama_queue, BACKGROUND
            synthesis = get_ollama_queue().ask(
                prompt=prompt, model="lfm2.5-thinking:1.2b",
                priority=BACKGROUND, timeout=90, options={"num_predict": 60}
            )
            if synthesis and len(synthesis) > 10:
                log.info("💡 Lumen sintetiza: %s", synthesis[:150])
                # Guardar síntesis de Lumen como knowledge de alta importancia
                import hashlib, time as _t
                conn2 = get_conn(brain_db, timeout=5)
                conn2.execute("PRAGMA journal_mode=WAL")
                concept = f"lumen_synthesis:{synthesis[:60]}"
                node_id = hashlib.md5(concept.encode()).hexdigest()[:16]
                now = _t.time()
                conn2.execute(
                    "INSERT OR IGNORE INTO knowledge_nodes "
                    "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                    "VALUES (?,?,?,?,?,?,?,1)",
                    (node_id, concept[:120], synthesis[:500], "lumen_synthesis", 0.85, now, now)
                )
                conn2.commit()
                pass  # S109: get_conn no necesita close()
                # Log al diario
                with open(LEARNING_LOG, "a", encoding="utf-8") as f:
                    f.write(f"- [{_t.strftime('%H:%M:%S')}] 💡 LUMEN: {synthesis[:200]}\n")
        except Exception as e:
            log.debug("lumen_synthesis falló: %s", e)

    # ── TEMA DIRIGIDO POR EL USUARIO ───────────────────────────────────────────

    def _get_user_topic(self) -> Optional[str]:
        """Lee temas de SER desde libre_topic.txt, libre_interest.txt y TaskList."""
        # 1. libre_topic.txt (tema inmediato — prioridad máxima)
        topic_file = Path.home() / ".eidos" / "libre_topic.txt"
        try:
            if topic_file.exists():
                topic = topic_file.read_text(encoding="utf-8").strip()
                topic_file.unlink()
                if topic:
                    log.info("📌 Tema recibido de SER (libre_topic): %s", topic[:80])
                    return topic
        except Exception:
            pass

        # 2. libre_interest.txt — temas añadidos vía /curiosity
        interest_file = Path.home() / ".eidos" / "libre_interest.txt"
        try:
            if interest_file.exists():
                lines = [l.strip() for l in
                         interest_file.read_text(encoding="utf-8").splitlines()
                         if l.strip()]
                if lines:
                    topic = lines[0]
                    # Quitar la primera línea consumida
                    remaining = "\n".join(lines[1:])
                    if remaining:
                        interest_file.write_text(remaining + "\n")
                    else:
                        interest_file.unlink(missing_ok=True)
                    log.info("📌 Tema de /curiosity: %s", topic[:80])
                    return topic
        except Exception:
            pass

        # 3. TaskList — tareas pendientes de tipo curiosity/learning
        try:
            from core.task_list import TaskList
            tl = TaskList()
            tasks = [t for t in tl.list_pending(5)
                     if "curiosity" in (t.tags or "") and t.agent_id == "eidos_libre"]
            if tasks:
                t = tasks[0]
                tl.update(t.id, status="in_progress")
                topic = t.subject.replace("Investigar: ", "").strip()
                log.info("📌 Tarea de TaskList [%s]: %s", t.id, topic[:80])
                return topic
        except Exception:
            pass

        return None

    def set_topic(self, topic: str) -> None:
        """Escribe un tema para que EIDOS lo investigue en la próxima iteración."""
        topic_file = Path.home() / ".eidos" / "libre_topic.txt"
        topic_file.parent.mkdir(parents=True, exist_ok=True)
        topic_file.write_text(topic.strip(), encoding="utf-8")
        log.info("📌 Tema guardado: %s", topic[:80])

    # ── INVESTIGACIÓN PROFUNDA ──────────────────────────────────────────────────

    def _fetch_text(self, url: str, timeout: int = 8) -> str:
        """Fetch URL y extrae texto plano básico (strip HTML)."""
        import urllib.request, re, html as _html
        headers = {"User-Agent": "EIDOS-Libre/1.0 (research bot)"}
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(80_000).decode("utf-8", errors="replace")
            # Strip HTML
            raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
            raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.DOTALL | re.IGNORECASE)
            raw = re.sub(r"<[^>]+>", " ", raw)
            raw = _html.unescape(raw)
            raw = re.sub(r"\s+", " ", raw).strip()
            return raw[:3000]
        except Exception as e:
            return f"[fetch_error: {e}]"

    def _search_wikipedia(self, topic: str) -> str:
        """Busca en Wikipedia via API y devuelve el extracto."""
        import urllib.request, urllib.parse, json as _json
        try:
            query = urllib.parse.quote(topic)
            url = (f"https://en.wikipedia.org/api/rest_v1/page/summary/{query}")
            req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Libre/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                d = _json.loads(r.read())
            extract = d.get("extract", "")
            if extract:
                return f"[Wikipedia/{topic}]\n{extract[:1500]}"
            # Fallback: búsqueda
            url2 = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json&srlimit=3"
            req2 = urllib.request.Request(url2, headers={"User-Agent": "EIDOS-Libre/1.0"})
            with urllib.request.urlopen(req2, timeout=8) as r2:
                d2 = _json.loads(r2.read())
            results = d2.get("query", {}).get("search", [])
            if results:
                texts = [f"• {r['title']}: {re.sub('<.*?>', '', r.get('snippet', ''))}" for r in results[:3]]
                import re
                texts = [re.sub(r"<[^>]+>", "", t) for t in texts]
                return f"[Wikipedia/{topic}]\n" + "\n".join(texts)
        except Exception as e:
            return f"[Wikipedia error: {e}]"
        return ""

    def _search_duckduckgo(self, query: str) -> str:
        """Búsqueda DDG Instant Answer API."""
        import urllib.request, urllib.parse, json as _json
        try:
            q = urllib.parse.quote(query)
            url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(url, headers={"User-Agent": "EIDOS-Libre/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                d = _json.loads(r.read())
            parts = []
            if d.get("AbstractText"):
                parts.append(d["AbstractText"][:800])
            for item in d.get("RelatedTopics", [])[:3]:
                if isinstance(item, dict) and item.get("Text"):
                    parts.append("• " + item["Text"][:200])
            if parts:
                return f"[DuckDuckGo/{query}]\n" + "\n".join(parts)
        except Exception as e:
            return f"[DDG error: {e}]"
        return ""

    def _build_dork_query(self, topic: str) -> str:
        """Construye query con Google Dorks para encontrar recursos de calidad."""
        topic_lower = topic.lower()
        # Temas de seguridad → buscar en GitHub y kali.org
        if any(k in topic_lower for k in ("exploit", "cve", "pentest", "hack", "vuln", "payload", "bypass", "inject")):
            return f'{topic} site:github.com OR site:exploit-db.com OR site:kali.org'
        # Temas de herramientas → buscar documentación oficial
        if any(k in topic_lower for k in ("nmap", "burp", "metasploit", "sqlmap", "hydra", "nikto", "gobuster")):
            return f'{topic} tutorial site:github.com OR site:kali.org OR site:hackingarticles.in'
        # Temas de código/arquitectura
        if any(k in topic_lower for k in ("python", "flask", "sqlite", "chromadb", "agent", "llm", "ai")):
            return f'{topic} site:github.com OR site:docs.python.org OR "how to"'
        # Temas de criptografia/blockchain
        if any(k in topic_lower for k in ("solana", "rust", "blockchain", "crypto", "defi")):
            return f'{topic} site:github.com OR site:docs.rs OR site:solana.com'
        # Query genérico mejorado — excluir resultados de baja calidad
        return f'{topic} tutorial OR guide OR documentation -site:pinterest.com -site:quora.com'

    def _read_eidos_local(self, topic: str) -> str:
        """Lee archivos locales de EIDOS para auto-estudio — sin necesitar internet."""
        eidos_root = Path("/home/ser/EIDOS")
        topic_lower = topic.lower()
        file_map = {
            "colony_dashboard": "core/colony_dashboard.py",
            "colony_community": "core/colony_community.py",
            "eidos_natural":    "core/eidos_natural.py",
            "ollama_queue":     "core/ollama_queue.py",
            "eidos_libre":      "core/eidos_libre.py",
            "claude_bridge":    "core/claude_bridge.py",
            "colony_studier":   "core/colony_studier.py",
            "brain.db":         None,
        }
        results = []
        for key, filepath in file_map.items():
            if key in topic_lower and filepath:
                full = eidos_root / filepath
                if full.exists():
                    content = full.read_text(errors="ignore")
                    # Extraer docstrings y definiciones de funciones (primeras 3000 chars)
                    lines = [l for l in content.splitlines()[:80]
                             if l.strip() and not l.strip().startswith("#")]
                    results.append(f"[Archivo local: {filepath}]\n" + "\n".join(lines[:50]))
        # Si el tema es genérico sobre EIDOS, leer el README o TASK.md
        if not results:
            for fname in ["TASK.md", "README.md", "SER_IDENTITY.md"]:
                f = eidos_root / fname
                if f.exists():
                    results.append(f"[{fname}]\n" + f.read_text(errors="ignore")[:1500])
                    break
        return "\n\n".join(results[:2]) if results else ""

    def _ollama_deep(self, prompt: str, num_predict: int = 100) -> str:
        """Pregunta a Ollama con tokens limitados para no bloquear ciclos."""
        try:
            from core.ollama_queue import get_ollama_queue, BACKGROUND
            resp = get_ollama_queue().ask(
                prompt=prompt, model="lfm2.5-thinking:1.2b",
                priority=BACKGROUND, timeout=50,
                options={"num_predict": min(num_predict, 60), "temperature": 0.7}
            )
            return resp or ""
        except Exception as e:
            log.debug("ollama_deep falló: %s", e)
            return ""

    def _deep_research(self, topic: str) -> str:
        """
        Investigación profunda multi-paso sobre un tema:
        1. Recopila información de Wikipedia + DDG
        2. Ollama analiza y genera 3 preguntas de seguimiento
        3. Investiga cada follow-up con búsqueda + Ollama
        4. Ollama sintetiza todo en un análisis coherente
        5. Guarda la síntesis como conocimiento
        """
        import hashlib, time as _t, re
        log.info("🔬 Investigación profunda: %s", topic)

        # ── PASO 1: Recopilar fuentes ────────────────────────────────────────
        # Si el tema es sobre EIDOS mismo, leer archivos locales en lugar de DDG
        local_src = ""
        eidos_keywords = ("eidos", "colony", "ollama_queue", "eidos_libre", "eidos_natural",
                          "brain.db", "colony_community", "colony_dashboard", "claude_bridge")
        topic_lower = topic.lower()
        if any(kw in topic_lower for kw in eidos_keywords):
            local_src = self._read_eidos_local(topic)

        wiki = self._search_wikipedia(topic) if not local_src else ""
        ddg  = self._search_duckduckgo(topic) if not local_src else ""
        sources = "\n\n".join(filter(None, [local_src, wiki, ddg]))
        if not sources or len(sources) < 100:
            sources = f"Tema: {topic} — investiga desde tu conocimiento interno."

        # ── PASO 2: Análisis + follow-ups ───────────────────────────────────
        analysis_prompt = (
            f"Investiga en profundidad el tema: **{topic}**\n\n"
            f"Información recopilada:\n{sources[:2000]}\n\n"
            f"Responde en español con:\n"
            f"1) ANÁLISIS: 3-5 puntos clave sobre este tema\n"
            f"2) FOLLOW-UPS: 3 preguntas de investigación que vale la pena explorar "
            f"(una por línea, empieza con 'FQ:')\n"
            f"3) APLICACIÓN: cómo esto es relevante para ciberseguridad o sistemas Linux"
        )
        analysis = self._ollama_deep(analysis_prompt, num_predict=400)
        if not analysis:
            analysis = f"Análisis de {topic}: {sources[:500]}"
        log.info("📊 Análisis inicial OK (%d chars)", len(analysis))

        # ── PASO 3: Investigar follow-ups — máximo 1 para no bloquear ciclo ────
        fq_pattern = re.compile(r"FQ:\s*(.+)", re.IGNORECASE)
        follow_ups = fq_pattern.findall(analysis)[:1]
        follow_results = []
        for fq in follow_ups:
            fq = fq.strip()
            if not fq:
                continue
            log.info("  🔍 Follow-up: %s", fq[:60])
            fq_wiki = self._search_wikipedia(fq)
            fq_ddg  = self._search_duckduckgo(fq)
            fq_src  = "\n".join(filter(None, [fq_wiki, fq_ddg]))[:1000] or fq
            fq_prompt = (
                f"Responde brevemente (2-3 oraciones) sobre:\n{fq}\n\n"
                f"Contexto:\n{fq_src[:800]}"
            )
            fq_ans = self._ollama_deep(fq_prompt, num_predict=150)
            if fq_ans:
                follow_results.append(f"• {fq}\n  → {fq_ans.strip()}")

        # ── PASO 4: Síntesis final ───────────────────────────────────────────
        synthesis_parts = [f"Investigación: {topic}", analysis[:1000]]
        if follow_results:
            synthesis_parts.append("\n[Follow-ups investigados]")
            synthesis_parts.extend(follow_results)
        synthesis_text = "\n\n".join(synthesis_parts)

        synth_prompt = (
            f"Has investigado el tema: {topic}\n\n"
            f"Hallazgos:\n{synthesis_text[:2500]}\n\n"
            f"Escribe UNA conclusión síntesis (2-3 oraciones) que capture "
            f"el conocimiento más valioso obtenido."
        )
        final_synthesis = self._ollama_deep(synth_prompt, num_predict=200)
        if not final_synthesis:
            final_synthesis = synthesis_text[:300]

        # ── PASO 5: Guardar como conocimiento de alta confianza ──────────────
        full_result = (
            f"[DEEP RESEARCH: {topic}]\n"
            f"{analysis[:800]}\n\n"
            f"[Síntesis]\n{final_synthesis}"
        )
        try:
            brain_db = Path.home() / ".eidos" / "evolution_brain.db"
            if brain_db.exists():
                import sqlite3
                conn = get_conn(brain_db, timeout=10)
                conn.execute("PRAGMA journal_mode=WAL")
                now = _t.time()
                concept = f"deep_research:{topic[:60]}"
                node_id = hashlib.md5(concept.encode()).hexdigest()[:16]
                conn.execute(
                    "INSERT OR REPLACE INTO knowledge_nodes "
                    "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                    "VALUES (?,?,?,?,?,?,?,2)",
                    (node_id, concept[:120], full_result[:1500], "eidos_libre:deep_research", 0.9, now, now)
                )
                conn.commit()
                pass  # S109: get_conn no necesita close()
                log.info("💾 Deep research guardado en brain.db")
        except Exception as e:
            log.debug("Guardar deep research: %s", e)

        # Broadcast a Colony
        try:
            from core.colony_broadcast import get_broadcast
            get_broadcast().broadcast(
                "colony_lumen",
                f"[DEEP RESEARCH] {topic}: {final_synthesis[:300]}",
                msg_type="learning"
            )
        except Exception:
            pass  # error no crítico, continuar
        # Log al diario
        try:
            with open(LEARNING_LOG, "a", encoding="utf-8") as f:
                f.write(f"\n### 🔬 Deep Research: {topic}\n{full_result[:600]}\n\n")
        except Exception:
            pass  # error no crítico, continuar
        log.info("✅ Deep research completado: %s", topic)
        return full_result[:200]

    # ── ACCIONES ───────────────────────────────────────────────────────────────

    def _take_action(self, context: Dict[str, Any]) -> Optional[str]:
        """Toma una acción basada en el curriculum estructurado."""
        topic = self._get_user_topic()
        if not topic:
            import random
            topics = ["Arquitectura interna de EIDOS y código fuente", "nmap", "n8n", "docker", "metasploit", "wireshark"]
            topic = random.choice(topics)
            self.set_topic(topic)
            self._curriculum_step = 0
            log.info("🎯 Nuevo tema auto-asignado: %s", topic)
            
        step = getattr(self, "_curriculum_step", 0)
        
        try:
            if step == 0:
                log.info("📚 Curriculum [%s] Paso 0: Investigación Base", topic)
                self._deep_research(topic)
                self._curriculum_step = 1
                return f"curriculum:step0:{topic[:20]}"
                
            elif step == 1:
                log.info("🌐 Curriculum [%s] Paso 1: Exploración Activa", topic)
                try:
                    if "arquitectura interna" in topic.lower():
                        import glob
                        py_files = glob.glob("/home/ser/EIDOS/core/*.py")[:5]
                        text = "Código fuente local:\n"
                        for f in py_files:
                            with open(f, "r") as src:
                                text += f"--- {f} ---\n{src.read()[:1000]}\n"
                        
                        from core.eidos_evolution_engine import get_evolution_engine
                        get_evolution_engine().learn_from_ollama(
                            f"libre:self_study:{int(time.time())}",
                            "Estudio de mi propia arquitectura interna",
                            f"Análisis del código:\n{text}"
                        )
                    else:
                        from core.eidos_playwright import get_playwright_agent
                        agent = get_playwright_agent(headless=False)
                        search_url = f"https://html.duckduckgo.com/html/?q={topic.replace(' ', '+')}+tutorial+documentation"
                        agent.goto(search_url)
                        agent.scroll(800)
                        agent.click("a.result__url")
                        agent.scroll(1000)
                        text = agent.extract_text(max_chars=3000)
                        if text:
                            from core.eidos_evolution_engine import get_evolution_engine
                            get_evolution_engine().learn_from_ollama(
                                f"libre:playwright:{int(time.time())}",
                                f"Estudio web interactivo sobre {topic}",
                                f"Contenido web extraído:\n{text}"
                            )
                except Exception as e:
                    log.error("Playwright falló: %s", e)
                
                self._curriculum_step = 2
                return f"curriculum:step1:{topic[:20]}"
                
            elif step == 2:
                log.info("💻 Curriculum [%s] Paso 2: Práctica Terminal", topic)
                import subprocess
                cmd = ["man", "-P", "cat", topic.split()[0]] if " " not in topic else ["echo", f"Practicando {topic}"]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env={"PATH": "/usr/bin:/bin"})
                    out = (r.stdout + r.stderr)[:1500]
                    if not out:
                        r = subprocess.run(["which", topic.split()[0]], capture_output=True, text=True)
                        out = r.stdout
                    
                    from core.eidos_evolution_engine import get_evolution_engine
                    get_evolution_engine().learn_from_ollama(
                        f"libre:terminal:{int(time.time())}",
                        f"Práctica terminal: {topic}",
                        f"Output:\n{out}" if out else "Comando no encontrado o sin output"
                    )
                except Exception:
                    pass
                self._curriculum_step = 3
                return f"curriculum:step2:{topic[:20]}"
                
            elif step == 3:
                log.info("🎓 Curriculum [%s] Paso 3: Maestría", topic)
                prompt = f"Resume todo lo que has aprendido sobre '{topic}' en 3 puntos clave para tu memoria permanente."
                analysis = self._ollama_deep(prompt, num_predict=200)
                if analysis:
                    from core.eidos_evolution_engine import get_evolution_engine
                    get_evolution_engine().learn_from_ollama(
                        f"libre:mastery:{topic}",
                        f"Maestría completada: {topic}",
                        analysis
                    )
                self.set_topic("")
                self._curriculum_step = 0
                return f"curriculum:step3_done:{topic[:20]}"

        except Exception as e:
            log.error("Error en curriculum step %s: %s", step, e)
            
        return None

    def _action_deep_research_random(self) -> Optional[str]:
        """Investiga profundamente un tema de seguridad aleatorio."""
        import random
        topics = [
            "SQL injection attack techniques",
            "Linux privilege escalation methods",
            "MITRE ATT&CK lateral movement",
            "buffer overflow exploitation",
            "command and control C2 frameworks",
            "network reconnaissance nmap",
            "web application firewall bypass",
            "cryptography asymmetric encryption",
            "container security Docker escape",
            "supply chain attack software",
            "zero-day vulnerability research",
            "memory forensics analysis",
            "OSINT open source intelligence",
            "malware reverse engineering",
            "network packet analysis Wireshark",
        ]
        topic = random.choice(topics)
        try:
            self._deep_research(topic)
            return f"deep_research:{topic[:40]}"
        except Exception:
            return None

    def _action_read_random_doc(self) -> Optional[str]:
        """Lee un .md aleatorio del proyecto y lo aprende."""
        import random
        from pathlib import Path
        candidates = list(Path("/home/ser/EIDOS").glob("*.md"))[:30]
        if not candidates:
            return None
        doc = random.choice(candidates)
        try:
            content = doc.read_text(encoding="utf-8")[:2000]
            from core.eidos_evolution_engine import get_evolution_engine
            get_evolution_engine().learn_from_ollama(
                f"libre:doc:{doc.name}",
                f"Lectura libre de {doc.name}",
                content,
            )
            return f"read_doc:{doc.name}"
        except Exception:
            return None

    def _action_man_page(self) -> Optional[str]:
        """Lee man de un comando random y aprende."""
        import random
        import subprocess
        cmds = ["nmap", "curl", "git", "docker", "systemctl", "ss", "tcpdump",
                "find", "grep", "awk", "jq", "lsof", "strace", "dig"]
        cmd = random.choice(cmds)
        try:
            r = subprocess.run(
                ["man", "-P", "cat", cmd],
                capture_output=True, text=True, timeout=4,
                env={"MANPAGER": "cat", "PAGER": "cat", "PATH": "/usr/bin:/bin"}
            )
            if r.returncode == 0 and len(r.stdout) > 200:
                from core.eidos_evolution_engine import get_evolution_engine
                get_evolution_engine().learn_from_ollama(
                    f"libre:man:{cmd}",
                    f"Estudio man page de {cmd}",
                    r.stdout[:2000],
                )
                return f"man:{cmd}"
        except Exception:
            pass  # error no crítico, continuar
        return None

    def _action_command_help(self) -> Optional[str]:
        """Ejecuta comando --help (read-only) y aprende."""
        import random
        import subprocess
        cmds = ["nmap", "curl", "git", "docker", "ollama", "python3",
                "node", "ssh", "openssl"]
        cmd = random.choice(cmds)
        try:
            r = subprocess.run(
                [cmd, "--help"],
                capture_output=True, text=True, timeout=3
            )
            if (r.returncode in (0, 1)) and len(r.stdout + r.stderr) > 100:
                content = (r.stdout + r.stderr)[:2000]
                from core.eidos_evolution_engine import get_evolution_engine
                get_evolution_engine().learn_from_ollama(
                    f"libre:help:{cmd}",
                    f"Estudio --help de {cmd}",
                    content,
                )
                return f"help:{cmd}"
        except Exception:
            pass  # error no crítico, continuar
        return None

    def _action_screenshot_describe(self) -> Optional[str]:
        """Captura pantalla y la describe via vision_lightweight."""
        try:
            from core.eidos_realtime_vision import get_realtime_vision
            vision = get_realtime_vision()
            if hasattr(vision, "take_screenshot") and hasattr(vision, "analyze_screen"):
                snap = vision.take_screenshot()
                if snap:
                    desc = vision.analyze_screen()
                    if desc:
                        from core.eidos_evolution_engine import get_evolution_engine
                        get_evolution_engine().learn_from_ollama(
                            f"libre:screen:{int(time.time())}",
                            "Análisis libre de pantalla",
                            str(desc)[:1500],
                        )
                        return "screenshot:described"
        except Exception:
            pass  # error no crítico, continuar
        return None


    def _action_open_browser(self) -> Optional[str]:
        """Fetch real de URL educativa, analiza el contenido con Ollama en profundidad."""
        import random, subprocess
        url_topics = [
            # Seguridad y hacking
            ("https://gtfobins.github.io/", "GTFOBins Linux privilege escalation"),
            ("https://lolbas-project.github.io/", "LOLBAS Windows Living off the Land binaries"),
            ("https://owasp.org/www-project-top-ten/", "OWASP Top 10 web vulnerabilities"),
            ("https://kali.org/tools/", "Kali Linux security tools"),
            ("https://www.exploit-db.com/", "Exploit-DB exploits públicos y shellcodes"),
            ("https://attack.mitre.org/", "MITRE ATT&CK framework tácticas y técnicas"),
            ("https://tryhackme.com/", "TryHackMe plataforma práctica de ciberseguridad"),
            ("https://labex.io/", "LabEx laboratorios prácticos de programación y Linux"),
            ("https://www.vulnhub.com/", "VulnHub máquinas vulnerables para practicar"),
            ("https://pentesterlab.com/exercises", "PentesterLab ejercicios de pentesting"),
            ("https://portswigger.net/web-security", "PortSwigger Web Security Academy"),
            # Programación y código
            ("https://github.com/trending/python", "GitHub trending repos Python"),
            ("https://github.com/trending/rust", "GitHub trending repos Rust"),
            ("https://github.com/trending/go", "GitHub trending repos Go"),
            ("https://docs.python.org/3/library/", "Python standard library documentation"),
            ("https://doc.rust-lang.org/book/", "The Rust Programming Language book"),
            ("https://go.dev/doc/effective_go", "Effective Go documentation"),
            ("https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide", "MDN JavaScript Guide"),
            # Linux y sistemas
            ("https://wiki.archlinux.org/", "Arch Wiki documentación Linux exhaustiva"),
            ("https://www.kernel.org/doc/html/latest/", "Linux Kernel documentation"),
            ("https://linuxcommand.org/", "Linux Command Line learning"),
            ("https://overthewire.org/wargames/", "OverTheWire wargames Linux y seguridad"),
            # IA y Machine Learning
            ("https://huggingface.co/docs", "HuggingFace documentación modelos IA"),
            ("https://arxiv.org/list/cs.AI/recent", "ArXiv papers recientes de IA"),
            ("https://paperswithcode.com/", "Papers With Code benchmarks y métodos ML"),
            # Libros técnicos
            ("https://z-library.ec/", "Z-Library libros técnicos de programación, seguridad y Linux"),
            ("https://z-library.ec/s/python", "Z-Library libros Python"),
            ("https://z-library.ec/s/cybersecurity", "Z-Library libros ciberseguridad"),
            ("https://z-library.ec/s/linux", "Z-Library libros Linux y sistemas"),
        ]
        url, topic = random.choice(url_topics)
        try:
            content = self._fetch_text(url, timeout=10)
            if len(content) > 200:
                log.info("🌐 Analizando: %s (%d chars)", url, len(content))
                # Abrir Firefox visualmente en el display activo (HDMI)
                display = os.environ.get("DISPLAY", ":0")
                env = {**os.environ, "DISPLAY": display}
                try:
                    subprocess.Popen(
                        ["firefox-esr", "--new-tab", url],
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    import time as _t; _t.sleep(2)
                    # Traer Firefox al frente
                    subprocess.run(
                        ["wmctrl", "-a", "Firefox"],
                        env=env, capture_output=True, timeout=3
                    )
                    log.info("🦊 Firefox abierto: %s", url)
                except Exception as fe:
                    log.debug("Firefox open error: %s", fe)
                    subprocess.Popen(["xdg-open", url], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # Análisis profundo del contenido
                prompt = (
                    f"Analiza este contenido de {url} sobre {topic}:\n\n"
                    f"{content[:1500]}\n\n"
                    f"Dame 3 insights clave de seguridad que EIDOS debería recordar."
                )
                analysis = self._ollama_deep(prompt, num_predict=250)
                if analysis:
                    from core.eidos_evolution_engine import get_evolution_engine
                    get_evolution_engine().learn_from_ollama(
                        f"libre:browser:{int(time.time())}",
                        f"Análisis profundo de {url}",
                        f"Fuente: {url}\nTema: {topic}\nAnálisis:\n{analysis}",
                    )
                    log.info("📚 Análisis de %s guardado", topic[:40])
                return f"browser+analysis:{url}"
        except Exception as e:
            log.debug("_action_open_browser: %s", e)
        return None

    def _action_open_terminal(self) -> Optional[str]:
        """Abre una terminal y ejecuta un comando de investigación seguro."""
        import random, subprocess
        cmds = [
            ["ss", "-tulnp"],
            ["netstat", "-rn"],
            ["ps", "aux", "--sort=-%mem"],
            ["lsof", "-i", "-n", "-P"],
            ["df", "-h"],
            ["free", "-h"],
            ["uname", "-a"],
            ["ip", "addr"],
        ]
        cmd = random.choice(cmds)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            output = (r.stdout + r.stderr)[:1500]
            if output:
                from core.eidos_evolution_engine import get_evolution_engine
                get_evolution_engine().learn_from_ollama(
                    f"libre:terminal:{int(time.time())}",
                    f"EIDOS ejecutó: {' '.join(cmd)}",
                    output,
                )
                log.info("💻 Terminal: %s → %d chars", ' '.join(cmd), len(output))
                return f"terminal:{' '.join(cmd)}"
        except Exception:
            pass  # error no crítico, continuar
        return None

    def _action_study_own_code(self) -> Optional[str]:
        """EIDOS estudia su propio código fuente para mejorar."""
        import random
        core_dir = Path("/home/ser/EIDOS/core")
        py_files = list(core_dir.glob("*.py"))
        if not py_files:
            return None
        target = random.choice(py_files)
        try:
            code = target.read_text(errors="ignore")
            lines = len(code.splitlines())
            # Analizar con Ollama
            prompt = (
                f"Analiza este módulo de EIDOS ({target.name}, {lines} líneas):\n\n"
                f"{code[:2000]}\n\n"
                f"1) ¿Qué hace este módulo?\n"
                f"2) ¿Tiene algún bug o mejora obvia?\n"
                f"3) ¿Qué patrón de diseño usa?\n"
                f"Responde en español, 3 frases máximo."
            )
            analysis = self._ollama_deep(prompt, num_predict=200)
            if analysis and len(analysis) > 20:
                from core.eidos_evolution_engine import get_evolution_engine
                get_evolution_engine().learn_from_ollama(
                    f"libre:self_study:{target.name}",
                    f"Auto-estudio de {target.name}",
                    f"Módulo: {target.name} ({lines} líneas)\nAnálisis:\n{analysis}",
                )
                log.info("📖 Auto-estudio: %s → %s", target.name, analysis[:80])
                return f"self_study:{target.name}"
        except Exception as e:
            log.debug("_action_study_own_code: %s", e)
        return None


_instance: Optional[EidosLibre] = None
_inst_lock = threading.Lock()


def get_libre() -> EidosLibre:
    global _instance
    if _instance is None:
        with _inst_lock:
            if _instance is None:
                _instance = EidosLibre()
    return _instance


def main() -> None:
    """Entry point: arranca modo libre interactivo o demonio."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    # --help / -h: mostrar uso y salir ANTES de procesar como tópico
    if '--help' in sys.argv or '-h' in sys.argv:
        print("Uso: eidos libre [tópico]")
        print("      eidos libre --daemon   (modo servicio, sin input interactivo)")
        sys.exit(0)

    libre = get_libre()

    # Flag --daemon: sin input interactivo, loop permanente como servicio
    daemon_mode = "--daemon" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--daemon"]

    # Tema desde CLI: `eidos libre "tema a investigar"`
    initial_topic = None
    if args:
        initial_topic = " ".join(args)

    libre.start(interval_seconds=30.0)
    print("\n🦅 EIDOS LIBRE activo. Ctrl+C para detener.\n")
    print(f"Modo: {libre.get_stats()['mode']}")
    print(f"Diario: {LEARNING_LOG}")
    print("\nEscribe un tema para investigar en profundidad (Enter = EIDOS elige):")
    print("Ejemplo: SQL injection bypass WAF\n")

    if initial_topic:
        libre.set_topic(initial_topic)
        print(f"📌 Investigando: {initial_topic}\n")

    def _sig_handler(signum, frame):
        print("\n\nDeteniendo EIDOS LIBRE...")
        libre.stop()
        stats = libre.get_stats()
        print(f"\n📊 Stats finales:")
        print(f"  Iteraciones:     {stats['iterations']}")
        print(f"  Pantallas vistas: {stats['screens_observed']}")
        print(f"  Pensamientos:    {stats['thoughts_generated']}")
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # Hilo de lectura interactiva (solo en modo no-daemon)
    if not daemon_mode:
        def _input_reader():
            while True:
                try:
                    line = input()
                    if line.strip():
                        libre.set_topic(line.strip())
                        print(f"  📌 Tema guardado — EIDOS lo investigará en la próxima iteración")
                except (EOFError, KeyboardInterrupt):
                    break

        input_thread = threading.Thread(target=_input_reader, daemon=True, name="libre-input")
        input_thread.start()

    # Mantener vivo el proceso principal
    try:
        while libre.is_running():
            time.sleep(5)
    except KeyboardInterrupt:
        _sig_handler(None, None)


if __name__ == "__main__":
    main()
