#!/usr/bin/env python3
"""
EIDOS Web Panel - Server Backend
=================================

WebSocket + REST API para dashboard en tiempo real
Muestra:
- Procesos background activos
- Logs detallados (estilo Claude)
- Knowledge DB stats
- Learning progress
- System resources
"""

import sys
import json
import asyncio
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Set
from aiohttp import web
import aiohttp_cors

# Add EIDOS to path
sys.path.insert(0, str(Path.home() / "EIDOS"))

from core.process_manager import get_process_manager, ProcessStatus
from core.detailed_logger import get_detailed_logger
from core.knowledge_integration import get_knowledge_db, is_using_rust
from core.continuous_learner import get_continuous_learner

# ── Autonomía ──────────────────────────────────────────────────────────────────
_autonomy_cache: dict = {}
_autonomy_cache_ts: float = 0.0
_AUTONOMY_CACHE_TTL = 30.0  # segundos (S122-I: era 5s → 30s para reducir presión de memoria)

logger = logging.getLogger(__name__)

# WebSocket connections
ws_connections: Set[web.WebSocketResponse] = set()


async def broadcast_update(data: dict):
    """Broadcast update a todos los clientes WebSocket"""
    if not ws_connections:
        return

    message = json.dumps(data)
    disconnected = set()

    for ws in ws_connections:
        try:
            await ws.send_str(message)
        except Exception:
            disconnected.add(ws)

    # Remove disconnected clients
    ws_connections.difference_update(disconnected)


async def websocket_handler(request):
    """WebSocket endpoint para updates en tiempo real"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    ws_connections.add(ws)
    logger.info(f"✅ WebSocket client connected (total: {len(ws_connections)})")

    try:
        # Send initial state
        initial_data = await get_full_state()
        await ws.send_json(initial_data)

        # Listen for messages (mostly ping/pong)
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                if msg.data == 'ping':
                    await ws.send_str('pong')
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f'WebSocket error: {ws.exception()}')

    finally:
        ws_connections.discard(ws)
        logger.info(f"❌ WebSocket client disconnected (total: {len(ws_connections)})")

    return ws


async def get_full_state():
    """Obtener estado completo del sistema"""
    pm = get_process_manager()
    dl = get_detailed_logger()
    kb = get_knowledge_db(verbose=False)
    learner = get_continuous_learner()

    # Process Manager stats
    pm_stats = pm.get_stats()

    # Processes list
    processes = [
        {
            "id": p.id,
            "name": p.name,
            "status": p.status.value,
            "priority": p.priority.name,
            "cpu_percent": p.cpu_percent,
            "memory_mb": p.memory_mb,
            "started_at": p.started_at,
            "changes_count": len(p.changes)
        }
        for p in pm.list_processes()
    ]

    # Recent logs
    recent_logs = [
        {
            "timestamp": log.timestamp,
            "type": log.change_type.value,
            "description": log.description,
            "before": log.before,
            "after": log.after,
            "file_link": log.get_file_link(),
            "metadata": log.metadata
        }
        for log in dl.logs[-50:]  # Last 50 logs
    ]

    # Knowledge DB stats
    kb_using_rust = is_using_rust()
    if kb and kb_using_rust:
        kb_stats_json = kb.get_stats()
        kb_stats = json.loads(kb_stats_json)
    elif kb:
        kb_stats = kb.get_stats()
    else:
        kb_stats = {}

    # Learning stats
    learned_content = learner.learned_content
    learning_stats = {
        "total_learned": len(learned_content),
        "queue_size": learner.learning_queue.qsize(),
        "recent_items": [
            {
                "title": item.get("title", "N/A"),
                "source_type": item.get("source_type", "N/A"),
                "status": item.get("status", "N/A"),
                "completed_at": item.get("completed_at")
            }
            for item in list(learned_content.values())[-10:]
        ]
    }

    return {
        "timestamp": datetime.now().isoformat(),
        "process_manager": {
            "stats": pm_stats,
            "processes": processes
        },
        "logs": recent_logs,
        "knowledge_db": {
            "using_rust": kb_using_rust,
            "stats": kb_stats
        },
        "learning": learning_stats,
        # S119 #241: Autonomía integrada en broadcast WebSocket
        "autonomy": await get_autonomy_data(),
    }


async def get_autonomy_data() -> dict:
    """Agrega todas las métricas de autonomía para el dashboard."""
    global _autonomy_cache, _autonomy_cache_ts
    now = time.time()
    if _autonomy_cache and (now - _autonomy_cache_ts) < _AUTONOMY_CACHE_TTL:
        return _autonomy_cache

    import json as _json
    from pathlib import Path as _Path

    data: dict = {
        "timestamp": datetime.now().isoformat(),
        "identity": {},
        "health_score": 0.0,
        "components": [],
        "capabilities": [],
        "runtime_probes": [],
        "graph_metrics": {},
        "anomalies": [],
        "traits": {},
        "services": {},
        "summary": "",
        "logic_engine": {},
        "semantic_router": {},
        "research_loop": {},
        "curiosity": {},
    }

    # ── 1. Identity (eidos_identity) ─────────────────────────────────────────
    try:
        from core.eidos_identity import EidosIdentity, identity_hash, IDENTITY_VERSION
        eid = EidosIdentity()
        data["identity"] = eid.independence()
    except Exception as e:
        data["identity"] = {"error": str(e), "version": "?", "hash": "?"}

    # ── 2. SelfInspect (ligero) ──────────────────────────────────────────────
    try:
        from core.eidos_self_inspect import self_inspect
        report = self_inspect(deep=False)
        d = report.to_dict()
        data["health_score"] = d.get("health_score", 0.0)
        data["components"] = d.get("components", [])
        data["capabilities"] = d.get("capabilities", [])
        data["runtime_probes"] = d.get("runtime_probes", [])
        data["graph_metrics"] = d.get("graph_metrics", {})
        data["anomalies"] = d.get("anomalies", [])
        data["traits"] = d.get("traits", {})
        data["services"] = d.get("services", {})
        data["summary"] = d.get("summary", "")
    except Exception as e:
        data["summary"] = f"self_inspect error: {e}"

    # ── 3. Motor lógico (eidos_logic) ────────────────────────────────────────
    try:
        from core.eidos_logic import get_logic_reasoner
        logic = get_logic_reasoner()
        # Disparar carga lazy para obtener conteos reales
        try:
            logic.load_seed_facts()
        except Exception:
            pass
        try:
            logic._load_learned_facts()
        except Exception:
            pass
        data["logic_engine"] = {
            "learned_facts": len(logic.facts) if hasattr(logic, 'facts') else 0,
            "seed_facts": logic._seed_fact_count if hasattr(logic, '_seed_fact_count') else len(logic.facts),
            "rules": len(logic.rules) if hasattr(logic, 'rules') else 0,
            "autonomous_mode": True,
            "db_path": str(getattr(logic, '_db_path', '~/.eidos/evolution_brain.db')),
        }
    except Exception as e:
        data["logic_engine"] = {"error": str(e)}

    # ── 4. SemanticRouter ────────────────────────────────────────────────────
    try:
        from core.semantic_router import get_semantic_router
        router = get_semantic_router()
        data["semantic_router"] = router.get_stats()
    except Exception as e:
        data["semantic_router"] = {"error": str(e)}

    # ── 5. Autonomous Research Loop ──────────────────────────────────────────
    try:
        stats_file = _Path.home() / ".eidos" / "autoresearch_stats.json"
        if stats_file.exists():
            with open(stats_file) as f:
                rs = _json.load(f)
            data["research_loop"] = {
                "today_count": rs.get("today_count", 0),
                "max_per_day": rs.get("max_per_day", 30),
                "interval_secs": rs.get("interval_secs", 300),
                "last_topic": rs.get("last_topic", ""),
                "total_facts_extracted": rs.get("total_facts_extracted", 0),
                "total_research": rs.get("total_research", 0),
            }
        else:
            data["research_loop"] = {"today_count": 0, "max_per_day": 30, "interval_secs": 300, "last_topic": "", "total_facts_extracted": 0, "total_research": 0, "note": "stats file not found"}
    except Exception as e:
        data["research_loop"] = {"error": str(e)}

    # ── 6. Curiosidad ────────────────────────────────────────────────────────
    try:
        from core.eidos_curiosity import get_curiosity
        ce = get_curiosity()
        data["curiosity"] = {
            "ticks": ce._stats.get("ticks", 0),
            "questions_generated": ce._stats.get("questions_generated", 0),
            "answers_learned": ce._stats.get("answers_learned", 0),
            "started_at": ce._stats.get("started_at"),
            "queue_size": len(ce._topic_queue) if hasattr(ce, '_topic_queue') else 0,
        }
    except Exception as e:
        data["curiosity"] = {"error": str(e)}

    # Cache
    _autonomy_cache = data
    _autonomy_cache_ts = now
    return data


async def api_autonomy(request):
    """GET /api/autonomy - Métricas completas de autonomía"""
    data = await get_autonomy_data()
    return web.json_response(data)


async def api_status(request):
    """GET /api/status - Estado completo"""
    state = await get_full_state()
    return web.json_response(state)


async def api_processes(request):
    """GET /api/processes - Lista de procesos"""
    pm = get_process_manager()

    processes = [p.to_dict() for p in pm.list_processes()]

    return web.json_response({
        "processes": processes,
        "stats": pm.get_stats()
    })


async def api_process_detail(request):
    """GET /api/processes/{id} - Detalle de proceso"""
    process_id = request.match_info['id']
    pm = get_process_manager()

    proc = pm.get_process(process_id)
    if not proc:
        return web.json_response({"error": "Process not found"}, status=404)

    return web.json_response(proc.to_dict())


async def api_kill_process(request):
    """POST /api/processes/{id}/kill - Terminar proceso"""
    process_id = request.match_info['id']
    pm = get_process_manager()

    success = pm.kill_process(process_id)

    if success:
        await broadcast_update({
            "type": "process_killed",
            "process_id": process_id
        })
        return web.json_response({"success": True})
    else:
        return web.json_response({"error": "Failed to kill process"}, status=400)


async def api_logs(request):
    """GET /api/logs - Logs detallados"""
    dl = get_detailed_logger()

    # Query params
    limit = int(request.query.get('limit', 50))
    change_type = request.query.get('type')

    logs = dl.logs
    if change_type:
        logs = [l for l in logs if l.change_type.value == change_type]

    logs = logs[-limit:]

    return web.json_response({
        "logs": [
            {
                "timestamp": log.timestamp,
                "type": log.change_type.value,
                "description": log.description,
                "before": log.before,
                "after": log.after,
                "file_link": log.get_file_link(),
                "code_before": log.code_before,
                "code_after": log.code_after,
                "metadata": log.metadata
            }
            for log in logs
        ],
        "total": len(dl.logs)
    })


async def api_knowledge(request):
    """GET /api/knowledge - Knowledge DB stats"""
    kb = get_knowledge_db(verbose=False)

    if not kb:
        return web.json_response({"error": "Knowledge DB not available"}, status=503)

    using_rust = is_using_rust()

    if using_rust:
        stats = json.loads(kb.get_stats())
    else:
        stats = kb.get_stats()

    return web.json_response({
        "using_rust": using_rust,
        "stats": stats
    })


async def api_learning(request):
    """GET /api/learning - Learning stats"""
    learner = get_continuous_learner()

    learned_content = learner.learned_content

    return web.json_response({
        "total_learned": len(learned_content),
        "queue_size": learner.learning_queue.qsize(),
        "items": [
            {
                "id": item_id,
                "title": item.get("title", "N/A"),
                "source_type": item.get("source_type", "N/A"),
                "status": item.get("status", "N/A"),
                "created_at": item.get("created_at"),
                "completed_at": item.get("completed_at")
            }
            for item_id, item in list(learned_content.items())[-50:]
        ]
    })


async def periodic_broadcast(app):
    """Task periódica para broadcast de updates"""
    import gc
    _bc_count = 0
    while True:
        try:
            if ws_connections:
                state = await get_full_state()
                await broadcast_update({
                    "type": "full_update",
                    "data": state
                })
                _bc_count += 1
                # [S122-I] GC explícito cada 10 broadcasts (~50s) para evitar
                # que el web-panel crezca a 791MB (leak gradual en
                # self_inspect + logic_reasoner + singletons)
                if _bc_count % 10 == 0:
                    gc.collect()
        except Exception as e:
            logger.error(f"Error in periodic broadcast: {e}")

        await asyncio.sleep(5)  # [S122-I] Broadcast cada 5s (era 2s)


async def start_background_tasks(app):
    """Iniciar tasks de background"""
    app['periodic_broadcast'] = asyncio.create_task(periodic_broadcast(app))


async def cleanup_background_tasks(app):
    """Limpiar tasks de background"""
    app['periodic_broadcast'].cancel()
    await app['periodic_broadcast']


async def api_claw_status(request):
    """Estado completo de OpenClaw gateway y agente Lumen."""
    import subprocess, json as _json, urllib.request
    result = {
        "gateway": {"running": False, "port": 19001, "url": "http://localhost:19001"},
        "agent": {},
        "auth": {"ok": False, "error": ""},
        "systemd": {"active": False},
    }
    # Systemd
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "eidos-openclaw.service"],
                           capture_output=True, text=True, timeout=3)
        result["systemd"]["active"] = r.stdout.strip() == "active"
    except Exception:
        pass
    # Gateway port
    try:
        req = urllib.request.Request("http://localhost:19001/", method="GET")
        urllib.request.urlopen(req, timeout=2)
        result["gateway"]["running"] = True
    except Exception:
        pass
    # Config del agente
    cfg_path = Path.home() / ".openclaw-dev" / "openclaw.json"
    if cfg_path.exists():
        try:
            cfg = _json.loads(cfg_path.read_text())
            agents = cfg.get("agents", {}).get("list", [])
            if agents:
                a = agents[0]
                result["agent"] = {
                    "id": a.get("id"),
                    "name": a.get("identity", {}).get("name", "?"),
                    "emoji": a.get("identity", {}).get("emoji", ""),
                    "theme": a.get("identity", {}).get("theme", ""),
                    "workspace": a.get("workspace", ""),
                }
        except Exception:
            pass
    # Auth check
    auth_path = Path.home() / ".openclaw-dev" / "agents" / "dev" / "agent" / "auth-profiles.json"
    if auth_path.exists():
        try:
            auth = _json.loads(auth_path.read_text())
            has_key = any(v for v in auth.values() if v)
            result["auth"]["ok"] = has_key
            result["auth"]["error"] = "" if has_key else "Sin API key de Anthropic configurada"
        except Exception:
            result["auth"]["error"] = "Error leyendo auth-profiles.json"
    else:
        result["auth"]["error"] = "auth-profiles.json no existe"
    return web.json_response(result)


async def api_claw_logs(request):
    """Últimas líneas del log de OpenClaw gateway."""
    log_path = Path.home() / ".eidos" / "logs" / "openclaw_gateway.log"
    lines = []
    if log_path.exists():
        try:
            text = log_path.read_text(errors="replace")
            lines = text.splitlines()[-80:]
        except Exception:
            lines = ["Error leyendo log"]
    return web.json_response({"lines": lines, "path": str(log_path)})


async def api_claw_restart(request):
    """Reinicia el servicio eidos-openclaw.service."""
    import subprocess
    try:
        r = subprocess.run(
            ["systemctl", "--user", "restart", "eidos-openclaw.service"],
            capture_output=True, text=True, timeout=10
        )
        ok = r.returncode == 0
        return web.json_response({"success": ok, "output": r.stdout + r.stderr})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


# ── Control de pantalla ──────────────────────────────────────────────────────

# ════════════════════════════════════════════════════════════════════════════
# Observación / STOP (botones del panel /screen)
# ════════════════════════════════════════════════════════════════════════════
import time as _time
import uuid as _uuid

_observation_sessions: dict = {}   # session_id -> {events:[], started_at, last_event}
_global_stop_state: dict = {"active": False, "since": 0.0}


async def api_observation_start(request):
    sid = _uuid.uuid4().hex[:12]
    _observation_sessions[sid] = {
        "events": [], "started_at": _time.time(), "last_event": _time.time(),
    }
    return web.json_response({"success": True, "session_id": sid})


async def api_observation_event(request):
    try:
        body = await request.json()
        sid = body.get("session_id")
        sess = _observation_sessions.get(sid)
        if not sess:
            return web.json_response({"success": False, "error": "session no encontrada"}, status=404)
        if len(sess["events"]) >= 5000:
            return web.json_response({"success": False, "error": "demasiados eventos (5000+)"})
        sess["events"].append({
            "kind": body.get("kind"),
            "payload": body.get("payload"),
            "ts": body.get("ts", _time.time()),
        })
        sess["last_event"] = _time.time()
        return web.json_response({"success": True, "n": len(sess["events"])})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


async def api_observation_stop(request):
    try:
        body = await request.json()
        sid = body.get("session_id")
        ser_message = (body.get("ser_message") or "").strip()
        sess = _observation_sessions.pop(sid, None)
        if not sess:
            return web.json_response({"success": False, "error": "session no encontrada"}, status=404)
        events = sess["events"]
        node_id = None
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _sys.path.insert(0, str(_Path.home() / "EIDOS"))
            from core.brain_memory import BrainMemory  # type: ignore
            brain = BrainMemory()
            obs_summary = _summarize_observation(events, sess["started_at"])
            # S63c: si el SER aportó explicación, la mezcla con los eventos como
            # contexto razonado. Tags trazables para recall futuro.
            if ser_message and ser_message != "[descartado]":
                content = (
                    "TEACH-BY-DEMO (PAUSE→OBSERVA→CONTINUE)\n"
                    f"--- SER explicación ---\n{ser_message[:2000]}\n"
                    f"--- eventos observados ---\n{obs_summary}"
                )
                tags = ["teach_by_demo", "user_taught", "with_explanation",
                        "screen_panel", "lesson_example"]
                importance = 0.85
            elif ser_message == "[descartado]":
                return web.json_response({
                    "success": True, "n_events": len(events),
                    "node_id": None, "discarded": True,
                })
            else:
                content = obs_summary
                tags = ["user_observation", "screen_panel", "lesson_example"]
                importance = 0.7
            node_id = brain.remember(
                content=content, tags=tags,
                importance=importance, category="lesson_example",
            )
        except Exception as e:
            logger.warning(f"observation stop: no se pudo guardar en brain: {e}")
        return web.json_response({
            "success": True, "n_events": len(events), "node_id": node_id,
            "with_explanation": bool(ser_message and ser_message != "[descartado]"),
        })
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


def _summarize_observation(events: list, started_at: float) -> str:
    n = len(events)
    dur = _time.time() - started_at
    by_kind: dict = {}
    seq = []
    for e in events[:60]:
        k = e.get("kind")
        by_kind[k] = by_kind.get(k, 0) + 1
        p = e.get("payload") or {}
        if k == "click":
            seq.append(f"click({p.get('x')},{p.get('y')})")
        elif k == "type":
            t = str(p.get("text", ""))[:30]
            seq.append(f'type("{t}")')
        elif k == "key":
            seq.append(f"key({p.get('key')})")
    summary = (
        f"Lesson (user-taught example via /screen).\n"
        f"Duración: {dur:.1f}s — {n} eventos: "
        f"{', '.join(f'{k}={v}' for k,v in by_kind.items())}\n"
        f"Secuencia: {' → '.join(seq[:60])}"
    )
    return summary


async def api_screen_stop(request):
    _global_stop_state["active"] = True
    _global_stop_state["since"] = _time.time()
    return web.json_response({"success": True, "stop_active": True})


async def api_screen_hide_window_briefly(request):
    """Minimiza la ventana del browser que muestra /screen para evitar bucle recursivo en la próxima captura."""
    import subprocess, os
    try:
        display = os.environ.get("DISPLAY", ":0")
        env = {**os.environ, "DISPLAY": display}
        wid = subprocess.run(
            ["xdotool", "search", "--name", "EIDOS — Vista en Vivo"],
            capture_output=True, timeout=3, env=env, text=True,
        ).stdout.strip().splitlines()
        for w in wid:
            if w.strip():
                subprocess.run(["xdotool", "windowminimize", w.strip()],
                               capture_output=True, timeout=3, env=env)
        return web.json_response({"success": True, "minimized_windows": len(wid)})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


def _window_offset(wid: str) -> tuple[int, int]:
    """S64b: devuelve (x, y) posición de la ventana en el display X11.
    Necesario para mapear clicks relativos a la ventana → coords absolutas."""
    if not wid:
        return (0, 0)
    import subprocess, os, re
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    try:
        r = subprocess.run(
            ["xdotool", "getwindowgeometry", wid],
            capture_output=True, text=True, timeout=3, env=env,
        )
        m = re.search(r"Position:\s*(-?\d+),(-?\d+)", r.stdout)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    except Exception:
        pass
    return (0, 0)


def _find_firefox_wid() -> str:
    """S125-L: devuelve WID del NAVEGADOR target (Firefox/Chromium/Chrome)."""
    import subprocess, os
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    EXCLUDED_HINTS = ("EIDOS — Vista en Vivo", "EIDOS — Screen Viewer")
    BROWSER_CLASSES = ("firefox", "chromium", "chrome", "google-chrome", "navigator", "eidos-target")
    try:
        r = subprocess.run(
            ["wmctrl", "-lx"], capture_output=True, text=True, timeout=3, env=env,
        )
        for line in r.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            cls, title = parts[2].lower(), parts[4]
            if not any(bc in cls for bc in BROWSER_CLASSES):
                continue
            if any(h in title for h in EXCLUDED_HINTS):
                continue
            return parts[0]
    except Exception:
        pass
    return ""


def _excluded_window_rects(env) -> list:
    """S124: geometría (x,y,w,h) de las ventanas del PROPIO viewer EIDOS, para
    taparlas en la captura root y evitar la recursión visual que reportó SER."""
    import subprocess
    EXCLUDED_HINTS = ("EIDOS — Vista en Vivo", "EIDOS — Screen Viewer")
    rects = []
    try:
        r = subprocess.run(["wmctrl", "-lG"], capture_output=True, text=True, timeout=3, env=env)
        for line in r.stdout.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            title = parts[7]
            if any(h in title for h in EXCLUDED_HINTS):
                try:
                    rects.append((int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])))
                except ValueError:
                    pass
    except Exception:
        pass
    return rects


def _blackout_rects(png_bytes: bytes, rects: list) -> bytes:
    """Tapa con negro las regiones dadas (anti-recursión). Si PIL falla, devuelve
    la imagen original — NUNCA rompe la captura."""
    if not rects:
        return png_bytes
    try:
        import io
        from PIL import Image, ImageDraw
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        d = ImageDraw.Draw(img)
        W, H = img.size
        for (x, y, w, h) in rects:
            x0, y0 = max(0, x - 4), max(0, y - 30)   # margen por barra de título
            x1, y1 = min(W, x + w + 4), min(H, y + h + 4)
            if x1 > x0 and y1 > y0:
                d.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))  # negro puro S125-L
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return png_bytes


def _active_nonviewer_wid(env) -> str:
    """S124: WID de la ventana ACTIVA (la app en primer plano, normalmente a pantalla
    completa donde EIDOS trabaja), excluyendo el propio viewer. Si la activa ES el
    viewer, devuelve la ventana no-viewer más alta del apilado. Captura rápida y sin recursión."""
    import subprocess
    EXCLUDED = ("EIDOS — Vista en Vivo", "EIDOS — Screen Viewer")
    try:
        aw = subprocess.run(["xdotool", "getactivewindow"],
                            capture_output=True, text=True, timeout=2, env=env).stdout.strip()
        if aw:
            nm = subprocess.run(["xdotool", "getwindowname", aw],
                                capture_output=True, text=True, timeout=2, env=env).stdout
            if not any(h in nm for h in EXCLUDED):
                return aw
        st = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"],
                            capture_output=True, text=True, timeout=2, env=env).stdout
        if "#" in st:
            for wid in reversed([w.strip() for w in st.split("#", 1)[1].split(",") if w.strip()]):
                nm = subprocess.run(["xprop", "-id", wid, "WM_NAME"],
                                    capture_output=True, text=True, timeout=2, env=env).stdout
                if not any(h in nm for h in EXCLUDED):
                    return wid
    except Exception:
        pass
    return ""


def _capture_desktop_no_viewer(env, display):
    """S124: captura el ESCRITORIO COMPLETO excluyendo la ventana del propio viewer
    EIDOS (estilo AnyDesk, sin recursión). Compone cada ventana en orden de apilado
    vía `import -window` (con compositor KWin, coge el pixmap real aunque esté tapada).
    Devuelve PNG o None si falla (el caller cae a scrot)."""
    import subprocess, io, tempfile, os as _os
    EXCLUDED = ("EIDOS — Vista en Vivo", "EIDOS — Screen Viewer")
    try:
        from PIL import Image
        geo = subprocess.run(["xdotool", "getdisplaygeometry"],
                             capture_output=True, text=True, timeout=3, env=env)
        sw, sh = (int(v) for v in geo.stdout.split())
        st = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"],
                            capture_output=True, text=True, timeout=3, env=env)
        if "#" not in st.stdout:
            return None
        wids = [w.strip() for w in st.stdout.split("#", 1)[1].split(",") if w.strip()]
        if not wids:
            return None
        canvas = Image.new("RGB", (sw, sh), (8, 10, 14))
        painted = 0
        for wid in wids:                                  # abajo→arriba
            try:
                nm = subprocess.run(["xprop", "-id", wid, "WM_NAME"],
                                    capture_output=True, text=True, timeout=2, env=env).stdout
                if any(h in nm for h in EXCLUDED):
                    continue                              # ← excluye el viewer
                g = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid],
                                   capture_output=True, text=True, timeout=2, env=env).stdout
                gd = dict(l.split("=", 1) for l in g.strip().splitlines() if "=" in l)
                x, y, w, h = int(gd["X"]), int(gd["Y"]), int(gd["WIDTH"]), int(gd["HEIGHT"])
                if w <= 1 or h <= 1:
                    continue
                fd, tmp = tempfile.mkstemp(suffix=".png", prefix="eidos_win_"); _os.close(fd)
                subprocess.run(["import", "-window", wid, "-display", display, tmp],
                               capture_output=True, timeout=6, env=env)
                if _os.path.exists(tmp) and _os.path.getsize(tmp) > 0:
                    canvas.paste(Image.open(tmp).convert("RGB"), (x, y))
                    painted += 1
                _os.path.exists(tmp) and _os.unlink(tmp)
            except Exception:
                continue
        if painted == 0:
            return None
        out = io.BytesIO(); canvas.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None


def _capture_screen(target: str = "root") -> bytes:
    """Captura pantalla y devuelve bytes PNG.

    S63: si target=='firefox', captura SOLO la ventana de Firefox ESR (xdotool
    + import -window WID). Si no existe Firefox, fallback al root X11 entero.
    target=='root' (default) preserva el comportamiento previo (X11 entero).
    """
    import subprocess, os, tempfile
    display = os.environ.get("DISPLAY", ":0")
    env = {**os.environ, "DISPLAY": display}
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="eidos_scr_")
    os.close(fd)
    os.unlink(tmp)  # S125-L: borrar archivo vacío para que scrot escriba aquí

    if target == "firefox":
        # S124: firefox si existe; si no, la ventana ACTIVA (app a pantalla completa),
        # nunca el viewer ni scrot root → rápido y sin recursión.
        wid = _find_firefox_wid() or _active_nonviewer_wid(env)
        if wid:
            r = subprocess.run(
                ["import", "-window", wid, "-display", display, tmp],
                capture_output=True, timeout=8, env=env,
            )
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                with open(tmp, "rb") as f:
                    data = f.read()
                os.unlink(tmp)
                return data
        # fallthrough → captura root si no se pudo capturar firefox

    r = subprocess.run(["scrot", "--silent", tmp], capture_output=True, timeout=8, env=env)
    if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        subprocess.run(
            ["import", "-window", "root", "-display", display, tmp],
            capture_output=True, timeout=8, env=env
        )
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        raise RuntimeError(f"scrot rc={r.returncode}: {r.stderr.decode()[:100]}")
    with open(tmp, "rb") as f:
        data = f.read()
    os.unlink(tmp)
    # S124: AnyDesk-style — escritorio COMPLETO sin la ventana del viewer (compone
    # ventanas vía compositor). Si falla, scrot normal con blackout del viewer.
    composed = _capture_desktop_no_viewer(env, display)
    if composed:
        return composed
    return _blackout_rects(data, _excluded_window_rects(env))


async def api_screen_live_png(request):
    """GET /api/screen/live.png[?target=firefox] — pantalla PNG directa.
    Compatible con <img src='/api/screen/live.png'>. Cache-busting vía timestamp.
    S63: target=firefox limita la captura a la ventana de Firefox ESR."""
    try:
        target = request.query.get("target", "root")
        data = _capture_screen(target=target)
        return web.Response(
            body=data,
            content_type="image/png",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
            }
        )
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def api_screen_screenshot(request):
    """Captura pantalla y devuelve JSON con base64. Acepta ?target=firefox."""
    import base64
    try:
        target = request.query.get("target", "root")
        data = _capture_screen(target=target)
        return web.json_response({"success": True, "image": base64.b64encode(data).decode(), "format": "png", "target": target})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


async def api_screen_click(request):
    """Hace click en coordenadas x,y.
    S64b: si body.target=='firefox', x/y se interpretan relativos a la
    ventana Firefox-Target → se suma el offset de la ventana en X11 + se
    activa esa ventana primero. Evita 'mouse extraño' por offset mal mapeado."""
    import subprocess, os
    try:
        body = await request.json()
        x, y = int(body.get("x", 0)), int(body.get("y", 0))
        button = int(body.get("button", 1))
        target = str(body.get("target", "root"))
        display = os.environ.get("DISPLAY", ":0")
        env = {**os.environ, "DISPLAY": display}
        abs_x, abs_y = x, y
        if target == "firefox":
            wid = _find_firefox_wid()
            if wid:
                ox, oy = _window_offset(wid)
                abs_x, abs_y = x + ox, y + oy
                subprocess.run(["xdotool", "windowactivate", "--sync", wid],
                               capture_output=True, timeout=3, env=env)
        r = subprocess.run(
            ["xdotool", "mousemove", str(abs_x), str(abs_y), "click", str(button)],
            capture_output=True, timeout=5, env=env,
        )
        return web.json_response({"success": r.returncode == 0,
                                  "x": x, "y": y, "abs_x": abs_x, "abs_y": abs_y,
                                  "target": target})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


async def api_screen_type(request):
    """Escribe texto. S64b: si target=='firefox', activa la ventana target
    antes del type para que las teclas vayan ahí, no a la ventana activa."""
    import subprocess, os
    try:
        body = await request.json()
        text = str(body.get("text", ""))
        target = str(body.get("target", "root"))
        display = os.environ.get("DISPLAY", ":0")
        env = {**os.environ, "DISPLAY": display}
        if target == "firefox":
            wid = _find_firefox_wid()
            if wid:
                subprocess.run(["xdotool", "windowactivate", "--sync", wid],
                               capture_output=True, timeout=3, env=env)
        r = subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--", text],
            capture_output=True, timeout=10, env=env,
        )
        return web.json_response({"success": r.returncode == 0, "target": target})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


async def api_screen_key(request):
    """Envía una tecla. S64b: target=='firefox' activa la ventana primero."""
    import subprocess, os
    try:
        body = await request.json()
        key = str(body.get("key", "Return"))
        target = str(body.get("target", "root"))
        display = os.environ.get("DISPLAY", ":0")
        env = {**os.environ, "DISPLAY": display}
        if target == "firefox":
            wid = _find_firefox_wid()
            if wid:
                subprocess.run(["xdotool", "windowactivate", "--sync", wid],
                               capture_output=True, timeout=3, env=env)
        r = subprocess.run(
            ["xdotool", "key", "--clearmodifiers", key],
            capture_output=True, timeout=5, env=env,
        )
        return web.json_response({"success": r.returncode == 0, "target": target})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


async def api_screen_open(request):
    """Abre una aplicación o URL."""
    import subprocess, os
    try:
        body = await request.json()
        target = str(body.get("target", ""))
        if not target or target == "__topic__":
            return web.json_response({"success": False, "error": "target requerido"})
        display = os.environ.get("DISPLAY", ":0")
        subprocess.Popen(
            ["xdg-open", target],
            env={**os.environ, "DISPLAY": display},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return web.json_response({"success": True, "opened": target})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


async def api_screen_instruct(request):
    """POST /api/screen/instruct — S63 Bloque B.
    Body: {instruction: "...", max_steps?: int, dry_run?: bool}
    Ejecuta la instrucción vía UI-TARS scaffold SIN VLM (OCR backend) sobre
    la ventana Firefox ESR target. Persiste el método en BrainMemory."""
    try:
        body = await request.json()
        instruction = str(body.get("instruction", "")).strip()
        max_steps = int(body.get("max_steps", 8))
        dry_run = bool(body.get("dry_run", False))
        if not instruction:
            return web.json_response({"success": False, "error": "instruction requerida"}, status=400)
        if max_steps < 1 or max_steps > 20:
            return web.json_response({"success": False, "error": "max_steps fuera de rango [1,20]"}, status=400)

        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path.home() / "EIDOS"))
        from core.screen_trainer import train  # type: ignore

        res = await asyncio.to_thread(train, instruction, max_steps, True, dry_run)
        return web.json_response({"success": res.get("status") == "ok", **res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_screen_repeat_last(request):
    """POST /api/screen/repeat-last — re-ejecuta el último método entrenado."""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path.home() / "EIDOS"))
        from core.screen_trainer import repeat_last  # type: ignore
        res = await asyncio.to_thread(repeat_last)
        return web.json_response({"success": res.get("status") == "ok", **res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_launch_viewer(request):
    """POST /api/launch-viewer — S124: lanza la app nativa bin/eidos-viewer
    (reemplaza la vieja página /screen, que causaba recursión). Por defecto en
    modo lectura (--no-input) para no arriesgar grab de ratón; ?input=1 para control."""
    import subprocess as _sp, os as _os
    from pathlib import Path as _P
    try:
        eidos_root = _P(__file__).parent.parent
        args = ["python3", "bin/eidos-viewer"]
        if request.query.get("input") != "1":
            args.append("--no-input")
        _sp.Popen(args, cwd=str(eidos_root),
                  env={**_os.environ, "DISPLAY": _os.environ.get("DISPLAY", ":0")},
                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                  start_new_session=True)
        return web.json_response({"success": True, "msg": "EIDOS viewer (app nativa) lanzado"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def api_eidos_topic(request):
    """POST /api/eidos/topic — envía un tema a EIDOS libre para que lo investigue."""
    from pathlib import Path
    try:
        body = await request.json()
        topic = str(body.get("topic", "")).strip()
        if not topic:
            return web.json_response({"success": False, "error": "topic requerido"})
        topic_file = Path.home() / ".eidos" / "libre_topic.txt"
        topic_file.parent.mkdir(parents=True, exist_ok=True)
        topic_file.write_text(topic, encoding="utf-8")
        return web.json_response({"success": True, "topic": topic})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)})


def create_app():
    """Crear aplicación aiohttp"""
    app = web.Application()

    # CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })

    # Routes
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/api/status', api_status)
    app.router.add_get('/api/processes', api_processes)
    app.router.add_get('/api/processes/{id}', api_process_detail)
    app.router.add_post('/api/processes/{id}/kill', api_kill_process)
    app.router.add_get('/api/logs', api_logs)
    app.router.add_get('/api/knowledge', api_knowledge)
    app.router.add_get('/api/learning', api_learning)
    # Claw panel
    app.router.add_get('/api/autonomy', api_autonomy)
    app.router.add_get('/autonomia', lambda r: web.FileResponse(Path(__file__).parent / 'static' / 'autonomia.html'))
    app.router.add_get('/api/claw/status', api_claw_status)
    app.router.add_get('/api/claw/logs', api_claw_logs)
    app.router.add_post('/api/claw/restart', api_claw_restart)
    app.router.add_get('/claw', lambda r: web.FileResponse(Path(__file__).parent / 'static' / 'claw.html'))
    # Screen control
    app.router.add_get('/api/screen/live.png', api_screen_live_png)
    app.router.add_get('/api/screen/screenshot', api_screen_screenshot)
    app.router.add_post('/api/screen/click', api_screen_click)
    app.router.add_post('/api/screen/type', api_screen_type)
    app.router.add_post('/api/screen/key', api_screen_key)
    app.router.add_post('/api/screen/open', api_screen_open)
    app.router.add_post('/api/screen/stop', api_screen_stop)
    app.router.add_post('/api/screen/hide-window-briefly', api_screen_hide_window_briefly)
    app.router.add_post('/api/observation/start', api_observation_start)
    app.router.add_post('/api/observation/event', api_observation_event)
    app.router.add_post('/api/observation/stop', api_observation_stop)
    app.router.add_post('/api/screen/instruct', api_screen_instruct)
    app.router.add_post('/api/screen/repeat-last', api_screen_repeat_last)
    app.router.add_post('/api/eidos/topic', api_eidos_topic)
    app.router.add_post('/api/launch-viewer', api_launch_viewer)  # S124: lanza app nativa
    # S124: página HTML /screen ELIMINADA (causaba recursión visual en el navegador).
    # Reemplazada por la app nativa bin/eidos-viewer (sin recursión, auto-pausa,
    # panel de pensamiento). Los endpoints /api/screen/* siguen activos para ella.

    # Static files (HTML panel)
    app.router.add_static('/static/', path=Path(__file__).parent / 'static', name='static')

    # [S122] Servir CUALQUIER dashboard por /<nombre>.html (y /<nombre>). Antes
    # solo /static/<x>.html o rutas sueltas sin .html → SER abría /neural_dashboard.html
    # y /monitor.html y daban 404. Manejador genérico (sin path traversal).
    _STATIC_DIR = Path(__file__).parent / 'static'
    async def _serve_dashboard(request):
        name = request.match_info.get('page', '')
        # Si no viene por regex {page} (rutas explícitas como /neural_dashboard),
        # derivar el nombre del path de la request (sin la barra inicial)
        if not name:
            name = request.path.lstrip('/').replace('.html', '')
        name = name.replace('/', '').replace('..', '').replace('\\', '')
        f = _STATIC_DIR / f"{name}.html"
        if f.exists():
            return web.FileResponse(f)
        return web.Response(status=404, text=f"No existe el panel '{name}.html'")
    app.router.add_get('/{page:[A-Za-z0-9_-]+\\.html}', _serve_dashboard)
    app.router.add_get('/neural_dashboard', _serve_dashboard)
    app.router.add_get('/monitor', _serve_dashboard)
    app.router.add_get('/index', _serve_dashboard)

    app.router.add_get('/', lambda r: web.FileResponse(Path(__file__).parent / 'static' / 'index.html'))

    # Configure CORS
    for route in list(app.router.routes()):
        cors.add(route)

    # Background tasks
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)

    return app


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [WebPanel] %(levelname)s - %(message)s'
    )

    print("╔════════════════════════════════════════════════════════════════╗")
    print("║           🌐 EIDOS Web Panel Server                           ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print("")
    print("🚀 Starting server on http://localhost:8080")
    print("📊 Dashboard: http://localhost:8080")
    print("🔌 WebSocket: ws://localhost:8080/ws")
    print("")
    print("📡 API Endpoints:")
    print("   GET  /api/status       - Full system status")
    print("   GET  /api/autonomy     - Autonomy dashboard data")
    print("   GET  /api/processes    - Background processes")
    print("   GET  /api/logs         - Detailed logs")
    print("   GET  /api/knowledge    - Knowledge DB stats")
    print("   GET  /api/learning     - Learning progress")
    print("   GET  /autonomia        - Dashboard de Autonomía (HTML)")
    print("")

    app = create_app()
    web.run_app(app, host='127.0.0.1', port=8080)  # solo local - seguridad
