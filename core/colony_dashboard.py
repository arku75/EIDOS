"""
EIDOS Colony Community Dashboard
=================================
Servidor web Flask para visualizar la comunidad de agentes en localhost.
Muestra edificios, agentes, chat en tiempo real, y sistema de recompensas.

Uso:
    python core/colony_dashboard.py
    # Abre http://localhost:7777 en tu navegador
"""

import os
import sys
import json
import time
import threading
import secrets
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

# Add EIDOS to path
EIDOS_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(EIDOS_ROOT))

from flask import Flask, render_template, jsonify, request
app = Flask(__name__, template_folder=str(EIDOS_ROOT / "core" / "templates"))
app.config['SECRET_KEY'] = os.environ.get('EIDOS_SECRET_KEY') or secrets.token_urlsafe(32)
if not os.environ.get('EIDOS_SECRET_KEY'):
    print('⚠️ Warning: EIDOS_SECRET_KEY is not set. Using a temporary random secret key.')

# Global community reference
_community = None


def get_community():
    """Obtiene la instancia de Colony Community"""
    global _community
    if _community is None:
        from core.colony_community import get_colony_community
        _community = get_colony_community()
    return _community


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    """Página principal del dashboard"""
    return render_template('colony_dashboard.html')


@app.route('/genealogy')
def genealogy_ui():
    """UI visual del árbol genealógico de Colony."""
    return render_template('colony_genealogy.html')


@app.route('/api/status')
def get_status():
    """API: Estado general del sistema"""
    try:
        community = get_community()
        agents = community.get_participants()

        total_tokens = sum(a.get('tokens_earned', 0) for a in agents)
        active_agents = sum(1 for a in agents if a.get('status') == 'online')

        # Independence score desde brain DB
        independence = 0.0
        brain_nodes  = 0
        try:
            import sqlite3 as _sq
            conn = _sq.connect(str(Path.home() / ".eidos" / "evolution_brain.db"), timeout=3)
            row = conn.execute("SELECT score FROM independence_state WHERE id=1").fetchone()
            if row:
                independence = float(row[0])
            row2 = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()
            if row2:
                brain_nodes = int(row2[0])
            conn.close()
        except Exception:
            pass  # error no crítico, continuar
        # HybridRouter stats
        routing = {}
        try:
            from core.octoclaw_bridge import get_hybrid_router
            routing = get_hybrid_router().get_stats()
        except Exception:
            pass  # error no crítico, continuar
        # Patrol status
        patrol = {}
        try:
            from core.patrol_loop import get_patrol_loop
            pl = get_patrol_loop()
            patrol = {"running": pl.is_running(), "last": pl.get_last_result()}
        except Exception:
            pass  # error no crítico, continuar
        # Ollama status
        ollama_ok = False
        try:
            from core.ollama_fallback import is_ollama_available
            ollama_ok = is_ollama_available()
        except Exception:
            pass  # error no crítico, continuar
        status = {
            "success": True,
            "online": True,
            "timestamp": time.time(),
            "version": "2.0",
            "agents": {
                "total": len(agents),
                "active": active_agents,
                "list": [a.get('name') for a in agents],
            },
            "tokens": {
                "total": total_tokens,
                "circulation": total_tokens,
            },
            "session": {
                "active": getattr(community, '_session_active', False),
                "start_time": getattr(community, '_session_start', None),
            },
            "brain": {
                "independence_score": f"{independence:.1%}",
                "knowledge_nodes": brain_nodes,
            },
            "routing": routing,
            "patrol": patrol,
            "ollama": {"available": ollama_ok},
        }
        return jsonify(status)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/agents')
def get_agents():
    """API: Lista de agentes con sus datos"""
    try:
        community = get_community()
        agents = community.get_participants()
        
        # Enriquecer con datos visuales
        for agent in agents:
            agent['building'] = get_agent_building(agent['agent_id'])
            agent['position'] = get_agent_position(agent['agent_id'])
        
        return jsonify({"success": True, "agents": agents})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/specialists')
def get_specialists():
    """API: Lista de los 205 especialistas OpenClaw por categoría"""
    try:
        from core.colony_openclaw_souls import OPENCLAW_SOULS, CATEGORIES
        by_cat = {}
        for oc_id, soul in OPENCLAW_SOULS.items():
            cat = soul.get("category", "other")
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append({
                "id": oc_id,
                "name": soul["name"],
                "emoji": soul.get("emoji", "🔧"),
                "specialty": soul["specialty"],
                "when_to_use": soul.get("when_to_use", ""),
                "category": cat,
            })
        return jsonify({
            "success": True,
            "total": len(OPENCLAW_SOULS),
            "categories": len(CATEGORIES),
            "specialists": by_cat,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/history')
def get_history():
    """API: Historial de mensajes"""
    try:
        community = get_community()
        limit = request.args.get('limit', 50, type=int)
        history = community.get_history(limit)
        return jsonify({"success": True, "messages": history})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/stats')
def get_stats():
    """API: Estadísticas de la comunidad"""
    try:
        community = get_community()
        stats = community.get_agent_stats()
        
        # Agregar info de sesión
        stats['timestamp'] = time.time()
        stats['active'] = getattr(community, '_session_active', False)
        
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/chat', methods=['POST'])
def post_chat():
    """API: Enviar mensaje al chat"""
    try:
        data = request.json
        message = data.get('message', '').strip()
        user   = data.get('user', 'SER')

        if not message:
            return jsonify({"success": False, "error": "Mensaje vacío"})

        # ── Slash-commands de EIDOS (/plan, /research, /react, /tools...) ──
        if message.startswith("/"):
            try:
                from core.slash_commands import get_command_handler
                handler = get_command_handler()
                if handler.is_slash_command(message):
                    result_obj = handler.handle(message)
                    resp_text  = result_obj.output if result_obj else "[sin respuesta]"
                    return jsonify({
                        "success": True,
                        "responses": [{
                            "msg_id": f"slash_{int(time.time()*1000)}",
                            "sender": "colony_general",
                            "sender_name": "EIDOS",
                            "sender_emoji": "⚡",
                            "content": resp_text,
                            "timestamp": time.time(),
                            "msg_type": "reply",
                        }]
                    })
            except Exception as _e:
                pass  # si falla el slash-handler, procesar como mensaje normal

        community = get_community()

        # ── Aprendizaje del lenguaje de SER ───────────────────────────────
        try:
            from core.ser_language_learner import get_ser_learner
            get_ser_learner().record_message(message, context=user)
        except Exception:
            pass  # opcional, no bloquea

        # ── Memoria episódica: cargar contexto de sesiones anteriores ──────
        episodic_context = ""
        try:
            from core.eidos_episodic_memory import EpisodicMemory
            if not hasattr(app, '_episodic_mem'):
                app._episodic_mem = EpisodicMemory()
                wake = app._episodic_mem.session_start()
                # Cargar qué se habló ayer/antes
                recent = app._episodic_mem.recall_recent(hours=48)
                if recent:
                    lines = []
                    for ep in recent[:5]:
                        t = ep.timestamp.strftime("%d/%m %H:%M") if hasattr(ep.timestamp, 'strftime') else str(ep.timestamp)[:16]
                        lines.append(f"  [{t}] {ep.title}: {ep.description[:80]}")
                    episodic_context = "\n\n[Memoria: últimas conversaciones con SER]\n" + "\n".join(lines)
            # Guardar este intercambio como episodio
            from core.eidos_episodic_memory import Episode
            ep = Episode(
                title=f"Conversación: {message[:50]}",
                description=f"SER: {message[:200]}",
                tags=["conversation", "ser"],
                importance=5
            )
            app._episodic_mem.db.save_episode(ep)
        except Exception:
            pass  # memoria episódica opcional

        # Asegurar sesión activa
        if not getattr(community, '_session_active', False):
            community.start_session()

        # Para whispers (@agente mensaje) usar say() directo
        if message.startswith("@"):
            responses = community.say(message)
            formatted = [{
                "msg_id": r.msg_id, "sender": r.sender,
                "sender_name": r.sender_name, "sender_emoji": r.sender_emoji,
                "content": r.content, "timestamp": r.timestamp, "msg_type": r.msg_type,
            } for r in responses]
        else:
            # Enriquecer el mensaje con contexto episódico para que EIDOS recuerde
            msg_with_context = message + episodic_context if episodic_context else message
            result = community.deliberate(msg_with_context, max_agents=3, skip_knowledge=False)
            resp_text = result.get("response", "")
            agents    = result.get("agents_consulted", ["EIDOS"])
            sender_id = agents[0] if agents else "colony_general"
            emoji_map = {
                "colony_potemtakem": "🐺", "colony_general": "🤖",
                "colony_coder": "💻", "colony_analyst": "🔍",
                "eidos_brain": "🧠", "self_knowledge": "🧠",
            }
            emoji = emoji_map.get(sender_id, "🤖")
            name_map = {
                "colony_potemtakem": "PotemTakem", "colony_general": "EIDOS",
                "colony_coder": "Coder", "colony_analyst": "Analyst",
                "eidos_brain": "EIDOS", "self_knowledge": "EIDOS",
            }
            sender_name = name_map.get(sender_id, "EIDOS")
            formatted = [{
                "msg_id": f"msg_{int(time.time()*1000)}",
                "sender": sender_id, "sender_name": sender_name,
                "sender_emoji": emoji, "content": resp_text,
                "timestamp": time.time(), "msg_type": "reply",
            }] if resp_text else []

        app.last_messages = {
            "user_message": {
                "sender": user, "sender_name": user, "sender_emoji": "👤",
                "content": message, "timestamp": time.time(),
            },
            "responses": formatted
        }

        return jsonify({"success": True, "responses": formatted})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/reward', methods=['POST'])
def post_reward():
    """API: Recompensar a un agente"""
    try:
        data = request.json
        agent_name = data.get('agent', '').lstrip('@')
        amount = float(data.get('amount', 0))
        reason = data.get('reason', '')
        
        if not agent_name or amount <= 0:
            return jsonify({"success": False, "error": "Agente o cantidad inválida"})
        
        community = get_community()
        result = community.reward_agent(agent_name, amount, reason)
        
        if result['success']:
            # Guardar evento para polling
            app.last_reward = {
                "agent": result['agent_name'],
                "amount": result['amount'],
                "reason": result.get('reason', ''),
            }
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/ask-all', methods=['POST'])
def ask_all():
    """API: Preguntar a los agentes persona principales.

    Asíncrono: lanza el broadcast en un thread y devuelve inmediatamente.
    El cliente recoge las respuestas vía /api/poll o /api/ask-all/result.
    Soporta ?sync=1 para esperar el resultado completo (puede tardar minutos).
    """
    import threading as _th
    try:
        data = request.json or {}
        question = data.get('question', '').strip()
        sync = bool(data.get('sync')) or request.args.get('sync') == '1'

        if not question:
            return jsonify({"success": False, "error": "Pregunta vacía"})

        community = get_community()
        if not getattr(community, '_session_active', False):
            community.start_session()

        def _run_broadcast():
            try:
                responses = community.ask_all(question)
                formatted = [{
                    "msg_id": r.msg_id, "sender": r.sender,
                    "sender_name": r.sender_name, "sender_emoji": r.sender_emoji,
                    "content": r.content, "timestamp": r.timestamp,
                } for r in responses]
                app.last_messages = {"question": question, "responses": formatted}
                app.ask_all_result = {
                    "success": True, "question": question,
                    "responses": formatted, "done": True,
                }
            except Exception as e:
                app.ask_all_result = {"success": False, "error": str(e), "done": True}

        if sync:
            _run_broadcast()
            return jsonify(getattr(app, 'ask_all_result',
                                   {"success": False, "error": "sin resultado"}))

        # Async: marcar en progreso y lanzar thread
        app.ask_all_result = {"success": True, "question": question,
                              "responses": [], "done": False}
        _th.Thread(target=_run_broadcast, daemon=True).start()
        return jsonify({"success": True, "async": True,
                        "message": "Broadcast lanzado. Consulta /api/ask-all/result"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/ask-all/result')
def ask_all_result():
    """Devuelve el resultado del último broadcast (para polling async)."""
    return jsonify(getattr(app, 'ask_all_result',
                           {"success": False, "error": "ningún broadcast lanzado",
                            "done": True}))


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_agent_building(agent_id: str) -> dict:
    """Retorna la estructura visual del edificio de cada agente"""
    buildings = {
        "colony_coder": {
            "name": "Torre de Código",
            "style": "modern-tech",
            "color": "#00d4aa",
            "height": 5,
            "icon": "💻",
            "description": "Centro de desarrollo y programación"
        },
        "colony_analyst": {
            "name": "Observatorio de Datos",
            "style": "glass-dome",
            "color": "#6b8cff",
            "height": 4,
            "icon": "🔍",
            "description": "Centro de análisis y estrategia"
        },
        "colony_vision": {
            "name": "Galería Visual",
            "style": "artistic",
            "color": "#ff6b9d",
            "height": 3,
            "icon": "👁️",
            "description": "Estudio de diseño y creatividad"
        },
        "colony_operator": {
            "name": "Núcleo de Operaciones",
            "style": "industrial",
            "color": "#ffd166",
            "height": 4,
            "icon": "⚡",
            "description": "Centro de control de sistemas"
        },
        "colony_general": {
            "name": "Plaza Central",
            "style": "plaza",
            "color": "#9b5de5",
            "height": 2,
            "icon": "🤖",
            "description": "Centro de coordinación comunitaria"
        },
    }
    return buildings.get(agent_id, buildings["colony_general"])


def get_agent_position(agent_id: str) -> dict:
    """Posición en el mapa 2D"""
    positions = {
        "colony_coder": {"x": 20, "y": 30},
        "colony_analyst": {"x": 60, "y": 20},
        "colony_vision": {"x": 80, "y": 50},
        "colony_operator": {"x": 40, "y": 70},
        "colony_general": {"x": 50, "y": 50},
    }
    return positions.get(agent_id, {"x": 50, "y": 50})


@app.route('/api/poll')
def poll_events():
    """Polling para obtener eventos nuevos (alternativa a WebSockets)"""
    events = []

    if hasattr(app, 'last_messages'):
        events.append({"type": "new_messages", "data": app.last_messages})
        delattr(app, 'last_messages')

    if hasattr(app, 'last_reward'):
        events.append({"type": "agent_rewarded", "data": app.last_reward})
        delattr(app, 'last_reward')

    return jsonify({"events": events})


@app.route('/api/routing')
def get_routing():
    """API: Estadísticas del HybridRouter y últimas decisiones de modelo."""
    try:
        from core.octoclaw_bridge import get_hybrid_router, is_available
        router = get_hybrid_router()
        stats  = router.get_stats()

        # Últimas 10 decisiones
        import sqlite3 as _sq
        from pathlib import Path as _P
        db = _P.home() / ".eidos" / "router.db"
        recent = []
        try:
            conn = _sq.connect(str(db), timeout=3)
            rows = conn.execute(
                "SELECT agent_id, default_model, final_model, decision_reason, "
                "smart_confidence, timestamp FROM routing_feedback "
                "ORDER BY timestamp DESC LIMIT 10"
            ).fetchall()
            conn.close()
            recent = [{"agent": r[0], "default": r[1], "chosen": r[2],
                       "reason": r[3], "conf": round(r[4], 2), "ts": r[5]}
                      for r in rows]
        except Exception:
            pass  # error no crítico, continuar
        return jsonify({
            "success": True,
            "octoclaw_available": is_available(),
            "stats": stats,
            "recent_decisions": recent,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/intercomm')
def get_intercomm():
    """API: Conversaciones autónomas recientes entre personajes."""
    try:
        from core.colony_intercomm import get_intercomm as _gi
        ic = _gi()
        return jsonify({
            "success": True,
            "running": ic.is_running(),
            "recent": ic.get_recent_conversations(10),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/genealogy')
def get_genealogy():
    """API: Árbol genealógico de Colony — personajes, nacimientos, reproducciones."""
    try:
        from core.character_lifecycle import get_lifecycle
        lc = get_lifecycle()
        data = lc.get_genealogy()
        stats = lc.get_stats()
        # Serializar para JSON
        chars = []
        for c in data.get("characters", []):
            chars.append({
                "name":            c.get("name"),
                "emoji":           c.get("emoji", "🌱"),
                "status":          c.get("status"),
                "connection_type": c.get("connection_type"),
                "absorption_pct":  round(c.get("absorption_pct", 0), 3),
                "knowledge_nodes": c.get("knowledge_nodes", 0),
                "target_nodes":    c.get("target_nodes", 0),
                "parent1":         c.get("parent1"),
                "parent2":         c.get("parent2"),
                "birth_date":      c.get("birth_date"),
            })
        return jsonify({
            "success":    True,
            "characters": chars,
            "genealogy":  data.get("genealogy", []),
            "stats":      stats,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/lifecycle/birth', methods=['POST'])
def birth_character():
    """API: Nace un personaje desde conexión externa."""
    try:
        data = request.get_json() or {}
        name = data.get("name", "").strip()
        conn_type = data.get("connection_type", "api_llm")
        emoji = data.get("emoji")
        target = int(data.get("target_nodes", 50))
        conn_data = data.get("connection_data", {})
        if not name:
            return jsonify({"success": False, "error": "name requerido"}), 400
        from core.character_lifecycle import get_lifecycle
        result = get_lifecycle().birth_from_connection(name, conn_type, conn_data, emoji, target)
        return jsonify({"success": True, "character": result, "already_existed": result is None})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/chroma/stats')
def get_chroma_stats():
    """API: Estado de ChromaDB (búsqueda semántica)."""
    try:
        from core.colony_chroma import get_chroma_memory
        chroma = get_chroma_memory()
        return jsonify({"success": True, **chroma.stats()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/chroma/search', methods=['POST'])
def chroma_search():
    """API: Búsqueda semántica en ChromaDB."""
    try:
        data = request.get_json() or {}
        query = data.get("query", "").strip()
        limit = int(data.get("limit", 5))
        if not query:
            return jsonify({"success": False, "error": "query requerida"}), 400
        from core.colony_chroma import get_chroma_memory
        results = get_chroma_memory().search(query, limit=limit)
        return jsonify({"success": True, "results": results, "count": len(results)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/remote-colony', methods=['GET', 'POST'])
def api_remote_colony():
    """Comunicación inter-EIDOS: hablar con la Colony remota (Mac↔Kali)"""
    import urllib.request as _ur
    try:
        data = request.get_json() or {}
        remote_url = data.get('remote_url', 'http://localhost:7778')
        action = data.get('action', 'status')

        if action == 'status':
            req = _ur.urlopen(f"{remote_url}/api/status", timeout=8)
            return jsonify({"success": True, "remote": json.loads(req.read())})

        elif action == 'agents':
            req = _ur.urlopen(f"{remote_url}/api/agents", timeout=8)
            return jsonify({"success": True, "remote_agents": json.loads(req.read())})

        elif action == 'chat':
            msg = data.get('message', '').strip()
            if not msg:
                return jsonify({"success": False, "error": "message required"})
            payload = json.dumps({"message": msg, "user": "EIDOS_RELAY"}).encode()
            req = _ur.Request(f"{remote_url}/api/chat", data=payload,
                              headers={"Content-Type": "application/json"})
            resp = _ur.urlopen(req, timeout=300)
            return jsonify({"success": True, "remote_response": json.loads(resp.read())})

        return jsonify({"success": False, "error": "action unknown"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:200]})


@app.route('/api/agent-direct', methods=['POST'])
def api_agent_direct():
    """Genera respuesta de un agente específico directamente (para relay inter-EIDOS)."""
    global _community
    try:
        data = request.get_json() or {}
        agent_id = data.get('agent_id', '').strip()
        message  = data.get('message', '').strip()
        if not agent_id or not message:
            return jsonify({"success": False, "error": "agent_id y message requeridos"})
        if _community is None:
            return jsonify({"success": False, "error": "Colony no inicializada"})
        result = _community._generate_agent_response(agent_id, message)
        return jsonify({"success": True, "agent_id": agent_id, "response": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:300]})


@app.route('/api/query', methods=['POST'])
def api_query():
    """API: Ejecuta una consulta a través del Colony Query Engine"""
    try:
        data = request.get_json() or {}
        text = data.get('text', '').strip()
        if not text:
            return jsonify({"success": False, "error": "Missing 'text' field"}), 400

        context = data.get('context', '')
        profile = data.get('profile', 'auto')
        max_tokens = data.get('max_tokens', 2048)

        try:
            from colony_query_engine import ColonyQueryEngine
        except ImportError:
            from core.colony_query_engine import ColonyQueryEngine

        if not hasattr(app, '_query_engine'):
            app._query_engine = ColonyQueryEngine()

        result = app._query_engine.query(
            text=text,
            context=context,
            profile=profile,
            max_tokens=max_tokens,
        )

        return jsonify({
            "success": result.success,
            "response": result.response,
            "agent": result.agent_used,
            "model": result.model_used,
            "query_type": result.query_type.value if hasattr(result.query_type, 'value') else str(result.query_type),
            "tokens": result.tokens_spent,
            "elapsed": result.elapsed_s,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Inicia el servidor del dashboard"""
    print("=" * 60)
    print("  🏛️  EIDOS COLONY COMMUNITY DASHBOARD")
    print("=" * 60)
    print()
    print("  Iniciando servidor...")
    print()
    
    # Verificar dependencias
    try:
        from core.colony_community import get_colony_community
        community = get_colony_community()
        print(f"  ✅ Colony Community cargada")
        print(f"  🤖 Agentes: {len(community.get_participants())}")
        
        # Auto-iniciar sesión
        if not getattr(community, '_session_active', False):
            session_id = community.start_session()
            print(f"  🆕 Sesión iniciada: {session_id}")
        else:
            print(f"  ✅ Sesión ya activa")
    except Exception as e:
        print(f"  ❌ Error cargando Colony Community: {e}")
        return
    
    # Crear directorio templates si no existe
    template_dir = EIDOS_ROOT / "core" / "templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    
    # Generar HTML si no existe
    html_path = template_dir / "colony_dashboard.html"
    if not html_path.exists():
        generate_dashboard_html(html_path)
        print(f"  ✅ Dashboard HTML generado")
    
    print()
    print("  🌐 Abre tu navegador en:")
    print("     http://localhost:7777")
    print()
    print("  Presiona Ctrl+C para detener")
    print("=" * 60)
    
    # Iniciar servidor
    print("  🚀 Dashboard iniciado en http://localhost:7777")
    print()
    app.run(host='0.0.0.0', port=7777, debug=False, threaded=True)


def generate_dashboard_html(path: Path):
    """Genera el archivo HTML del dashboard"""
    html_content = '''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🏛️ EIDOS Colony Community</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            min-height: 100vh;
            color: #fff;
            overflow-x: hidden;
        }
        
        .header {
            background: rgba(0,0,0,0.3);
            backdrop-filter: blur(10px);
            padding: 1rem 2rem;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .header h1 {
            font-size: 1.5rem;
            background: linear-gradient(90deg, #00d4aa, #6b8cff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .header .subtitle {
            font-size: 0.85rem;
            color: #888;
        }
        
        .main-container {
            display: grid;
            grid-template-columns: 1fr 350px;
            gap: 1rem;
            padding: 1rem;
            height: calc(100vh - 80px);
        }
        
        /* MAPA DEL MUNDO */
        .world-panel {
            background: rgba(0,0,0,0.2);
            border-radius: 16px;
            padding: 1rem;
            position: relative;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.1);
        }
        
        .world-title {
            font-size: 1rem;
            margin-bottom: 1rem;
            color: #00d4aa;
        }
        
        .city-map {
            position: relative;
            width: 100%;
            height: calc(100% - 40px);
            background: radial-gradient(circle at 50% 50%, rgba(0,212,170,0.1) 0%, transparent 70%);
            border-radius: 12px;
        }
        
        .building {
            position: absolute;
            transform: translate(-50%, -50%);
            cursor: pointer;
            transition: all 0.3s ease;
        }
        
        .building:hover {
            transform: translate(-50%, -50%) scale(1.1);
            z-index: 100;
        }
        
        .building-base {
            width: 80px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .building-structure {
            width: 60px;
            border-radius: 8px 8px 0 0;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            position: relative;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }
        
        .building.modern-tech .building-structure {
            background: linear-gradient(180deg, #00d4aa 0%, #00a884 100%);
        }
        
        .building.glass-dome .building-structure {
            background: linear-gradient(180deg, #6b8cff 0%, #4a6de5 100%);
            border-radius: 50% 50% 8px 8px;
        }
        
        .building.artistic .building-structure {
            background: linear-gradient(180deg, #ff6b9d 0%, #e84a8c 100%);
            border-radius: 20px;
        }
        
        .building.industrial .building-structure {
            background: linear-gradient(180deg, #ffd166 0%, #e6b74a 100%);
        }
        
        .building.plaza .building-structure {
            background: linear-gradient(180deg, #9b5de5 0%, #7c3dd1 100%);
            border-radius: 50%;
        }
        
        .building-name {
            margin-top: 8px;
            font-size: 0.75rem;
            text-align: center;
            background: rgba(0,0,0,0.6);
            padding: 4px 8px;
            border-radius: 12px;
            white-space: nowrap;
        }
        
        .agent-avatar {
            position: absolute;
            bottom: -10px;
            right: -10px;
            font-size: 1.2rem;
            background: #fff;
            border-radius: 50%;
            width: 28px;
            height: 28px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        
        .token-badge {
            position: absolute;
            top: -8px;
            left: 50%;
            transform: translateX(-50%);
            background: linear-gradient(90deg, #ffd700, #ffaa00);
            color: #000;
            font-size: 0.7rem;
            font-weight: bold;
            padding: 2px 8px;
            border-radius: 10px;
            white-space: nowrap;
        }
        
        /* SIDEBAR */
        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }
        
        .panel {
            background: rgba(0,0,0,0.2);
            border-radius: 16px;
            padding: 1rem;
            border: 1px solid rgba(255,255,255,0.1);
        }
        
        .panel-title {
            font-size: 0.9rem;
            color: #00d4aa;
            margin-bottom: 0.75rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        /* CHAT */
        .chat-messages {
            height: 300px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        
        .message {
            background: rgba(255,255,255,0.05);
            padding: 0.75rem;
            border-radius: 12px;
            border-left: 3px solid;
        }
        
        .message.ser {
            border-color: #00d4aa;
            background: rgba(0,212,170,0.1);
        }
        
        .message.agent {
            border-color: #6b8cff;
        }
        
        .message-header {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.8rem;
            margin-bottom: 0.25rem;
        }
        
        .message-sender {
            font-weight: bold;
        }
        
        .message-time {
            color: #888;
            font-size: 0.7rem;
        }
        
        .message-content {
            font-size: 0.85rem;
            line-height: 1.4;
        }
        
        .chat-input-area {
            margin-top: 0.75rem;
            display: flex;
            gap: 0.5rem;
        }
        
        .chat-input {
            flex: 1;
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 8px;
            padding: 0.75rem;
            color: #fff;
            font-size: 0.9rem;
        }
        
        .chat-input:focus {
            outline: none;
            border-color: #00d4aa;
        }
        
        .btn {
            background: linear-gradient(90deg, #00d4aa, #00a884);
            border: none;
            border-radius: 8px;
            padding: 0.75rem 1rem;
            color: #fff;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,212,170,0.4);
        }
        
        .btn-secondary {
            background: linear-gradient(90deg, #6b8cff, #4a6de5);
        }
        
        /* AGENT LIST */
        .agent-list {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        
        .agent-card {
            background: rgba(255,255,255,0.05);
            padding: 0.75rem;
            border-radius: 12px;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .agent-card:hover {
            background: rgba(255,255,255,0.1);
        }
        
        .agent-emoji {
            font-size: 1.5rem;
        }
        
        .agent-info {
            flex: 1;
        }
        
        .agent-name {
            font-weight: bold;
            font-size: 0.9rem;
        }
        
        .agent-traits {
            font-size: 0.75rem;
            color: #888;
        }
        
        .agent-tokens {
            background: linear-gradient(90deg, #ffd700, #ffaa00);
            color: #000;
            padding: 0.25rem 0.5rem;
            border-radius: 12px;
            font-size: 0.8rem;
            font-weight: bold;
        }
        
        /* STATS */
        .stats-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.5rem;
        }
        
        .stat-item {
            background: rgba(255,255,255,0.05);
            padding: 0.75rem;
            border-radius: 8px;
            text-align: center;
        }
        
        .stat-value {
            font-size: 1.25rem;
            font-weight: bold;
            color: #00d4aa;
        }
        
        .stat-label {
            font-size: 0.75rem;
            color: #888;
        }
        
        /* REWARD MODAL */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0,0,0,0.8);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        
        .modal-overlay.active {
            display: flex;
        }
        
        .modal {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            border-radius: 16px;
            padding: 1.5rem;
            width: 90%;
            max-width: 400px;
            border: 1px solid rgba(255,255,255,0.2);
        }
        
        .modal-title {
            font-size: 1.2rem;
            margin-bottom: 1rem;
        }
        
        .form-group {
            margin-bottom: 1rem;
        }
        
        .form-label {
            display: block;
            margin-bottom: 0.5rem;
            font-size: 0.85rem;
            color: #888;
        }
        
        .form-input {
            width: 100%;
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 8px;
            padding: 0.75rem;
            color: #fff;
        }
        
        .modal-buttons {
            display: flex;
            gap: 0.5rem;
            margin-top: 1.5rem;
        }
        
        .modal-buttons .btn {
            flex: 1;
        }
        
        .btn-cancel {
            background: rgba(255,255,255,0.1);
        }
        
        /* ANIMATIONS */
        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.05); }
        }
        
        .building.active .building-structure {
            animation: pulse 2s infinite;
            box-shadow: 0 0 30px currentColor;
        }
        
        /* SCROLLBAR */
        ::-webkit-scrollbar {
            width: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: rgba(0,0,0,0.1);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.2);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255,255,255,0.3);
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🏛️ EIDOS Colony Community</h1>
            <div class="subtitle">Tu mini-mundo de agentes inteligentes • Interactúa, recompensa, observa</div>
        </div>
        <div style="text-align: right;">
            <div style="font-size: 1.5rem; font-weight: bold; color: #00d4aa;">👤 SER</div>
            <div style="font-size: 0.8rem; color: #888;">Administrador de la Colonia</div>
        </div>
    </div>
    
    <div class="main-container">
        <!-- MAPA DEL MUNDO -->
        <div class="world-panel">
            <div class="world-title">🌆 Ciudad de la Colonia</div>
            <div class="city-map" id="cityMap">
                <!-- Los edificios se generan dinámicamente -->
            </div>
        </div>
        
        <!-- SIDEBAR -->
        <div class="sidebar">
            <!-- CHAT -->
            <div class="panel" style="flex: 1;">
                <div class="panel-title">💬 Chat Comunitario</div>
                <div class="chat-messages" id="chatMessages">
                    <div class="message system">
                        <div class="message-content">
                            Bienvenido a tu mini-mundo. Habla con tus agentes usando el chat de abajo.
                            Usa @nombre para mensajes privados.
                        </div>
                    </div>
                </div>
                <div class="chat-input-area">
                    <input type="text" class="chat-input" id="chatInput" 
                           placeholder="Escribe un mensaje... (@agente para whisper)"
                           onkeypress="if(event.key==='Enter') sendMessage()">
                    <button class="btn" onclick="sendMessage()">Enviar</button>
                </div>
                <div style="display: flex; gap: 0.5rem; margin-top: 0.5rem;">
                    <button class="btn btn-secondary" style="font-size: 0.8rem; padding: 0.5rem;" 
                            onclick="askAll()">📢 Preguntar a todos</button>
                </div>
            </div>
            
            <!-- AGENTES -->
            <div class="panel">
                <div class="panel-title">🤖 Agentes</div>
                <div class="agent-list" id="agentList">
                    <!-- Lista de agentes -->
                </div>
            </div>
            
            <!-- STATS -->
            <div class="panel">
                <div class="panel-title">📊 Estadísticas</div>
                <div class="stats-grid" id="statsGrid">
                    <div class="stat-item">
                        <div class="stat-value" id="totalAgents">5</div>
                        <div class="stat-label">Agentes</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="totalMessages">0</div>
                        <div class="stat-label">Mensajes</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="totalTokens">0</div>
                        <div class="stat-label">Tokens</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value" id="sessionStatus">🟢</div>
                        <div class="stat-label">Sesión</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- MODAL DE RECOMPENSA -->
    <div class="modal-overlay" id="rewardModal">
        <div class="modal">
            <div class="modal-title">🎁 Recompensar Agente</div>
            <div class="form-group">
                <label class="form-label">Agente</label>
                <input type="text" class="form-input" id="rewardAgent" readonly>
            </div>
            <div class="form-group">
                <label class="form-label">Cantidad de Tokens</label>
                <input type="number" class="form-input" id="rewardAmount" min="1" value="10">
            </div>
            <div class="form-group">
                <label class="form-label">Razón (opcional)</label>
                <input type="text" class="form-input" id="rewardReason" placeholder="Ej: Excelente código">
            </div>
            <div class="modal-buttons">
                <button class="btn btn-cancel" onclick="closeRewardModal()">Cancelar</button>
                <button class="btn" onclick="sendReward()">💰 Recompensar</button>
            </div>
        </div>
    </div>
    
    <script>
        let agents = [];
        let currentRewardAgent = null;
        let lastPoll = 0;
        
        // Inicializar
        async function init() {
            console.log('Iniciando dashboard...');
            await loadAgents();
            await loadHistory();
            await loadStats();
            startPolling();
        }
        
        // Polling para actualizaciones
        function startPolling() {
            setInterval(async () => {
                await pollEvents();
                await loadAgents(); // Recargar agentes para actualizar tokens
            }, 2000);
        }
        
        async function pollEvents() {
            try {
                const res = await fetch('/api/poll');
                const data = await res.json();
                
                data.events.forEach(event => {
                    if (event.type === 'new_messages') {
                        event.data.responses.forEach(resp => addMessageToChat(resp));
                    } else if (event.type === 'agent_rewarded') {
                        addMessageToChat({
                            sender: 'SYSTEM',
                            sender_name: 'Sistema',
                            sender_emoji: '🔔',
                            content: `🎁 SER recompensó a ${event.data.agent} con ${event.data.amount} tokens${event.data.reason ? ': ' + event.data.reason : ''}`,
                            timestamp: Date.now() / 1000,
                        });
                    }
                });
            } catch (e) {
                // Silenciar errores de polling
            }
        }
        
        // Cargar agentes
        async function loadAgents() {
            try {
                const res = await fetch('/api/agents');
                const data = await res.json();
                if (data.success) {
                    agents = data.agents;
                    renderBuildings();
                    renderAgentList();
                }
            } catch (e) {
                console.error('Error cargando agentes:', e);
            }
        }
        
        // Renderizar edificios en el mapa
        function renderBuildings() {
            const map = document.getElementById('cityMap');
            map.innerHTML = '';
            
            agents.forEach(agent => {
                const building = document.createElement('div');
                building.className = `building ${agent.building.style}`;
                building.style.left = agent.position.x + '%';
                building.style.top = agent.position.y + '%';
                
                const tokens = agent.tokens_earned || 0;
                
                building.innerHTML = `
                    <div class="building-base">
                        ${tokens > 0 ? `<div class="token-badge">${tokens.toFixed(0)} 💰</div>` : ''}
                        <div class="building-structure" style="height: ${agent.building.height * 20}px;">
                            ${agent.building.icon}
                        </div>
                        <div class="agent-avatar">${agent.emoji}</div>
                        <div class="building-name">${agent.building.name}</div>
                    </div>
                `;
                
                building.onclick = () => openRewardModal(agent);
                map.appendChild(building);
            });
        }
        
        // Renderizar lista de agentes
        function renderAgentList() {
            const list = document.getElementById('agentList');
            list.innerHTML = agents.map(agent => `
                <div class="agent-card" onclick="openRewardModal(${JSON.stringify(agent).replace(/"/g, '&quot;')})">
                    <div class="agent-emoji">${agent.emoji}</div>
                    <div class="agent-info">
                        <div class="agent-name">${agent.name}</div>
                        <div class="agent-traits">${agent.traits.join(', ')}</div>
                    </div>
                    <div class="agent-tokens">${(agent.tokens_earned || 0).toFixed(0)}</div>
                </div>
            `).join('');
        }
        
        // Cargar historial
        async function loadHistory() {
            try {
                const res = await fetch('/api/history?limit=20');
                const data = await res.json();
                if (data.success) {
                    data.messages.forEach(msg => addMessageToChat(msg));
                }
            } catch (e) {
                console.error('Error cargando historial:', e);
            }
        }
        
        // Cargar stats
        async function loadStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                if (data.success) {
                    document.getElementById('totalTokens').textContent = data.stats.total_rewards?.toFixed(0) || 0;
                }
            } catch (e) {
                console.error('Error cargando stats:', e);
            }
        }
        
        // Enviar mensaje
        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const message = input.value.trim();
            if (!message) return;
            
            input.value = '';
            
            // Agregar mensaje de usuario inmediatamente
            addMessageToChat({
                sender: 'SER',
                sender_name: 'SER',
                sender_emoji: '👤',
                content: message,
                timestamp: Date.now() / 1000,
            });
            
            try {
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message })
                });
            } catch (e) {
                console.error('Error enviando mensaje:', e);
            }
        }
        
        // Preguntar a todos
        async function askAll() {
            const question = prompt('¿Qué quieres preguntar a TODOS los agentes?');
            if (!question) return;
            
            addMessageToChat({
                sender: 'SER',
                sender_name: 'SER',
                sender_emoji: '👤',
                content: '📢 A TODOS: ' + question,
                timestamp: Date.now() / 1000,
            });
            
            try {
                const res = await fetch('/api/ask-all', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question })
                });
            } catch (e) {
                console.error('Error preguntando:', e);
            }
        }
        
        // Agregar mensaje al chat
        function addMessageToChat(msg) {
            const container = document.getElementById('chatMessages');
            const div = document.createElement('div');
            div.className = `message ${msg.sender === 'SER' ? 'ser' : 'agent'}`;
            
            const time = new Date(msg.timestamp * 1000).toLocaleTimeString('es-ES', { 
                hour: '2-digit', minute: '2-digit' 
            });
            
            div.innerHTML = `
                <div class="message-header">
                    <span>${msg.sender_emoji || '🤖'}</span>
                    <span class="message-sender">${msg.sender_name || msg.sender}</span>
                    <span class="message-time">${time}</span>
                </div>
                <div class="message-content">${escapeHtml(msg.content)}</div>
            `;
            
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
            
            // Actualizar contador
            const current = parseInt(document.getElementById('totalMessages').textContent);
            document.getElementById('totalMessages').textContent = current + 1;
        }
        
        // Polling: reemplaza WebSocket - consulta /api/poll cada 2s
        setInterval(async () => {
            try {
                const resp = await fetch('/api/poll');
                const data = await resp.json();
                for (const event of (data.events || [])) {
                    if (event.type === 'new_messages' || event.type === 'ask_all_responses') {
                        (event.data.responses || []).forEach(r => addMessageToChat(r));
                    }
                    if (event.type === 'agent_rewarded') {
                        addMessageToChat({
                            sender: 'SYSTEM',
                            sender_name: 'Sistema',
                            sender_emoji: '🔔',
                            content: `🎁 SER recompensó a ${event.data.agent} con ${event.data.amount} tokens${event.data.reason ? ': ' + event.data.reason : ''}`,
                            timestamp: Date.now() / 1000,
                        });
                        loadAgents();
                    }
                }
            } catch(e) { /* silent poll failure */ }
        }, 2000);
        
        // Modal de recompensa
        function openRewardModal(agent) {
            if (typeof agent === 'string') agent = JSON.parse(agent);
            currentRewardAgent = agent;
            document.getElementById('rewardAgent').value = agent.name + ' ' + agent.emoji;
            document.getElementById('rewardModal').classList.add('active');
        }
        
        function closeRewardModal() {
            document.getElementById('rewardModal').classList.remove('active');
            currentRewardAgent = null;
        }
        
        async function sendReward() {
            if (!currentRewardAgent) return;
            
            const amount = parseFloat(document.getElementById('rewardAmount').value);
            const reason = document.getElementById('rewardReason').value;
            
            try {
                const res = await fetch('/api/reward', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        agent: currentRewardAgent.name.toLowerCase(),
                        amount,
                        reason
                    })
                });
                
                const data = await res.json();
                if (data.success) {
                    closeRewardModal();
                    loadAgents();
                }
            } catch (e) {
                console.error('Error enviando recompensa:', e);
            }
        }
        
        // Helper
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Cerrar modal al hacer click fuera
        document.getElementById('rewardModal').onclick = (e) => {
            if (e.target.id === 'rewardModal') closeRewardModal();
        };
        
        // Iniciar aplicación
        init();
    </script>
</body>
</html>
'''
    path.write_text(html_content, encoding='utf-8')


if __name__ == "__main__":
    main()
