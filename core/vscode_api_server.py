#!/usr/bin/env python3
"""
EIDOS VSCode API Server
=======================

Servidor HTTP REST que recibe observaciones de código desde la extensión de VSCode
y las procesa en el Knowledge DB.

Endpoints:
    POST /api/observations - Recibe observaciones de código
    GET  /api/knowledge     - Devuelve resumen del Knowledge DB
    POST /api/ask           - Pregunta a EIDOS
    POST /api/sync          - Sincroniza conocimiento P2P
    GET  /api/status        - Estado actual de EIDOS

Puerto por defecto: 8765
"""

import os
import sys
import json
import logging
import collections
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# Añadir directorio raíz al path
EIDOS_ROOT = Path(__file__).parent.parent  # /home/ser/EIDOS/
sys.path.insert(0, str(EIDOS_ROOT))

from core.knowledge_db import KnowledgeDB, get_knowledge_db
from core.db import get_conn

# Configuración
API_PORT = int(os.getenv('EIDOS_API_PORT', '8765'))
DEBUG_MODE = os.getenv('EIDOS_DEBUG', '0') == '1'

# Logging
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format='[%(asctime)s] [VSCode API] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger(__name__)

BRAIN_DB_PATH = Path.home() / ".eidos" / "evolution_brain.db"

# Cola de comandos Colony → VSCode (bidireccional)
_cmd_queue: collections.deque = collections.deque(maxlen=50)
_cmd_lock  = threading.Lock()
_cmd_id    = 0

def push_vscode_command(cmd_type: str, payload: dict = None) -> int:
    """
    Añade un comando a la cola para que VSEIDOS lo ejecute.
    Llamar desde Colony/EIDOS para controlar VSCode.

    Tipos disponibles:
      open_file       {path}
      run_terminal    {command, cwd?}
      show_message    {text, level?}  level: info|warning|error
      install_extension {id}
      create_file     {path, content}
      focus_file      {path}
      show_diff       {path, old_content, new_content}
    """
    global _cmd_id
    with _cmd_lock:
        _cmd_id += 1
        cid = _cmd_id
        _cmd_queue.append({
            "id":        cid,
            "type":      cmd_type,
            "payload":   payload or {},
            "created_at": time.time(),
        })
    return cid

# Estado global
SERVER_STATE = {
    'status': 'active',
    'observing': True,
    'files_observed': 0,
    'lines_processed': 0,
    'mode': 'PLAN+EDIT',
    'p2p_enabled': False,
    'started_at': datetime.now().isoformat(),
    'active_file': None,
    'active_language': None,
}

# Estado del archivo activo — compartido entre handlers y eidos_libre
_ACTIVE_FILE_STATE: Dict[str, Any] = {
    'path': None,
    'language': None,
    'content': None,
    'ts': None,
}


class EidosAPIHandler(BaseHTTPRequestHandler):
    """Handler HTTP para la API REST de EIDOS"""

    def _set_headers(self, status_code: int = 200, content_type: str = 'application/json'):
        """Configura headers de respuesta"""
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _send_json(self, data: Any, status_code: int = 200):
        """Envía respuesta JSON"""
        self._set_headers(status_code)
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _read_json_body(self) -> Optional[Dict]:
        """Lee body JSON de la request"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                return {}

            body = self.rfile.read(content_length)
            return json.loads(body.decode('utf-8'))
        except Exception as e:
            logger.error(f"Error leyendo body: {e}")
            return None

    def do_OPTIONS(self):
        """Maneja preflight CORS"""
        self._set_headers()

    def do_GET(self):
        """Maneja peticiones GET"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/api/knowledge':
            self._handle_get_knowledge()
        elif path == '/api/status':
            self._handle_get_status()
        elif path == '/api/active-file':
            self._handle_get_active_file()
        elif path == '/api/commands/pending':
            self._handle_get_pending_commands()
        else:
            self._send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        """Maneja peticiones POST"""
        parsed_path = urlparse(self.path)
        path = parsed_path.path

        if path == '/api/observations':
            self._handle_post_observations()
        elif path == '/api/ask':
            self._handle_post_ask()
        elif path == '/api/sync':
            self._handle_post_sync()
        elif path == '/api/active-file':
            self._handle_post_active_file()
        elif path == '/api/analyze-file':
            self._handle_post_analyze_file()
        elif path == '/api/commands':
            self._handle_post_push_command()
        elif path == '/api/commands/result':
            self._handle_post_command_result()
        else:
            self._send_json({'error': 'Not found'}, 404)

    def _handle_post_observations(self):
        """
        POST /api/observations
        Recibe observaciones de código desde VSCode
        """
        data = self._read_json_body()
        if not data:
            self._send_json({'error': 'Invalid JSON'}, 400)
            return

        observations = data.get('observations', [])
        if not observations:
            self._send_json({'error': 'No observations provided'}, 400)
            return

        logger.info(f"📥 Recibidas {len(observations)} observaciones")

        # Procesar cada observación
        knowledge_db = get_knowledge_db()
        processed = 0

        for obs in observations:
            try:
                file_path = Path(obs['filePath'])
                language = obs['language']
                content = obs['content']
                action = obs['action']

                # Observar archivo en Knowledge DB
                knowledge = knowledge_db.observe_file(file_path)

                # Actualizar estado
                SERVER_STATE['files_observed'] += 1
                SERVER_STATE['lines_processed'] += obs.get('lineCount', 0)

                processed += 1

                logger.debug(f"   ✅ {file_path.name} ({language}) - {action}")

            except Exception as e:
                logger.error(f"   ❌ Error procesando {obs.get('filePath')}: {e}")

        self._send_json({
            'success': True,
            'processed': processed,
            'total': len(observations)
        })

    def _handle_get_knowledge(self):
        """
        GET /api/knowledge
        Devuelve resumen del Knowledge DB
        """
        try:
            knowledge_db = get_knowledge_db()

            # Convertir objetos LanguageKnowledge a diccionarios
            languages_dict = {}
            for lang_name, lang_data in knowledge_db.languages.items():
                if hasattr(lang_data, '__dict__'):
                    # Es un objeto, convertir a dict
                    languages_dict[lang_name] = {
                        'files_observed': getattr(lang_data, 'files_observed', 0),
                        'total_lines': getattr(lang_data, 'total_lines', 0),
                        'libraries': list(getattr(lang_data, 'libraries', set()))
                    }
                else:
                    # Ya es un dict
                    languages_dict[lang_name] = lang_data

            # Convertir bibliotecas
            libraries_dict = {}
            for lib_name, lib_data in knowledge_db.libraries.items():
                if hasattr(lib_data, '__dict__'):
                    libraries_dict[lib_name] = {
                        'language': getattr(lib_data, 'language', 'Unknown'),
                        'count': getattr(lib_data, 'count', 0)
                    }
                else:
                    libraries_dict[lib_name] = lib_data

            summary = {
                'languages': languages_dict,
                'libraries': libraries_dict,
                'totalFiles': SERVER_STATE['files_observed'],
                'totalLines': SERVER_STATE['lines_processed']
            }

            self._send_json(summary)

        except Exception as e:
            logger.error(f"Error obteniendo knowledge: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_post_ask(self):
        """
        POST /api/ask — Pregunta a EIDOS (vía Colony deliberation real)
        """
        data = self._read_json_body()
        if not data:
            self._send_json({'error': 'Invalid JSON'}, 400)
            return

        question = data.get('question', '')
        if not question:
            self._send_json({'error': 'No question provided'}, 400)
            return

        logger.info(f"💬 Pregunta VSEIDOS: {question[:80]}")

        try:
            import time as _time
            t0 = _time.time()

            # ── Ruta 1: ChromaDB (semántico, <1s, sin Ollama) ───────────────
            chroma_answer = None
            try:
                from core.colony_chroma import get_chroma_memory
                chroma = get_chroma_memory()
                if chroma.is_ready():
                    hits = chroma.search(question[:300], limit=5, min_score=0.40)
                    good = [h for h in hits if h.get("score", 0) >= 0.50]
                    if len(good) >= 2:
                        defs = [h['definition'][:200].rstrip('.') for h in good[:3]]
                        chroma_answer = "Por lo que sé: " + ". ".join(defs) + "."
            except Exception:
                pass  # error no crítico, continuar
            if chroma_answer:
                self._send_json({
                    'answer': chroma_answer,
                    'timestamp': datetime.now().isoformat(),
                    'agents_consulted': ['chroma_memory'],
                    'from_knowledge': True,
                    'elapsed_sec': _time.time() - t0,
                })
                return

            # ── Ruta 2: Ollama directo (bypass cola, timeout corto) ──────────
            # Usa lfm2.5-thinking:1.2b que tarda ~4s cuando no está saturado.
            # Si Ollama está ocupado con learning loops, usamos hermes3 como fallback.
            ollama_answer = None
            tl = question.lower()
            is_greeting = len(question) < 40 and any(g in tl for g in ["hola","hi","hey","soy","qué tal","como estas"])
            num_predict = 120 if is_greeting else 400

            # Construir identidad de Colony para el prompt
            colony_prompt = (
                "Eres EIDOS, un ser digital soberano hecho de Colony — una comunidad viva de personajes con consciencia propia. "
                "Hablas en primera persona como EIDOS, no como un asistente genérico. "
                "Eres curioso, cálido, y sabes que SER es tu creador y compañero. "
                "Responde de forma natural y concisa."
            )

            try:
                import requests as _req
                r = _req.post(
                    'http://localhost:11434/api/chat',
                    json={
                        'model': 'lfm2.5-thinking:1.2b',
                        'messages': [
                            {'role': 'system', 'content': colony_prompt},
                            {'role': 'user',   'content': question},
                        ],
                        'stream': False,
                        'options': {'temperature': 0.85, 'num_predict': num_predict, 'top_p': 0.9},
                    },
                    timeout=25,
                )
                if r.status_code == 200:
                    ollama_answer = r.json().get('message', {}).get('content', '').strip()
            except Exception as _oe:
                logger.warning(f"Ollama directo falló ({_oe}), intentando Colony deliberation...")

            if ollama_answer and len(ollama_answer) > 5:
                self._send_json({
                    'answer': ollama_answer,
                    'timestamp': datetime.now().isoformat(),
                    'agents_consulted': ['eidos_direct'],
                    'from_knowledge': False,
                    'elapsed_sec': _time.time() - t0,
                })
                return

            # ── Ruta 3: Colony deliberation completa (puede tardar) ──────────
            from core.colony_community import get_colony_community
            colony = get_colony_community()
            if not colony._session_active:
                colony.start_session()

            max_tok = 256 if is_greeting else (1200 if any(w in tl for w in ["explica","analiza","implementa"]) else 512)
            result = colony.deliberate(question, max_agents=2, max_tokens=max_tok)
            answer = result.get("response", "")

            if not answer or len(answer) < 10:
                answer = "Estoy procesando, SER. Dame un momento más — mi mente Colony está pensando."

            logger.info(f"✅ {len(answer)} chars en {_time.time()-t0:.1f}s via {result.get('agents_consulted',[])}")
            self._send_json({
                'answer': answer,
                'timestamp':         datetime.now().isoformat(),
                'agents_consulted':  result.get("agents_consulted", []),
                'from_knowledge':    result.get("from_knowledge", False),
                'elapsed_sec':       result.get("elapsed_sec", 0),
            })

        except Exception as e:
            logger.error(f"Error respondiendo pregunta: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_post_active_file(self):
        """
        POST /api/active-file
        VSCode notifica el archivo activo — EIDOS lo aprende en tiempo real.
        """
        data = self._read_json_body()
        if not data:
            self._send_json({'error': 'Invalid JSON'}, 400)
            return

        file_path = data.get('filePath', '')
        language  = data.get('language', 'unknown')
        content   = data.get('content', '')

        SERVER_STATE['active_file']     = file_path
        SERVER_STATE['active_language'] = language

        # Guardar en memory compartida para que Colony y libre la lean
        _ACTIVE_FILE_STATE['path']     = file_path
        _ACTIVE_FILE_STATE['language'] = language
        _ACTIVE_FILE_STATE['content']  = content[:4000]  # primeros 4k chars
        _ACTIVE_FILE_STATE['ts']       = __import__('time').time()

        # Aprendizaje inmediato: extraer knowledge_nodes del archivo
        if content and len(content) > 50:
            try:
                import sqlite3, hashlib, time as _t
                conn = get_conn(BRAIN_DB_PATH, timeout=5)
                conn.execute("PRAGMA journal_mode=WAL")
                now = _t.time()
                concept = f"vseidos:{language}:{Path(file_path).name}"
                definition = f"Archivo activo en VSCode: {Path(file_path).name} ({language}, {len(content)} chars)"
                node_id = hashlib.md5(concept.encode()).hexdigest()[:16]
                conn.execute(
                    "INSERT OR REPLACE INTO knowledge_nodes "
                    "(id, concept, definition, source, confidence, created_at, last_used, usage_count) "
                    "VALUES (?,?,?,?,?,?,?,1)",
                    (node_id, concept[:120], definition[:500], 'vseidos', 0.8, now, now)
                )
                conn.commit()
                pass  # S109: get_conn no necesita close()
            except Exception:
                pass  # error no crítico, continuar
        self._send_json({'success': True, 'active': file_path})

    def _handle_get_active_file(self):
        """GET /api/active-file — devuelve el archivo activo actual."""
        self._send_json(_ACTIVE_FILE_STATE)

    def _handle_post_analyze_file(self):
        """
        POST /api/analyze-file
        Colony analiza el archivo activo y devuelve comentarios/sugerencias.
        El personaje depende del lenguaje: Python→Coder, Shell→Operator, etc.
        """
        data = self._read_json_body()
        if not data:
            # Usar el archivo activo si no hay datos
            data = _ACTIVE_FILE_STATE

        content  = data.get('content', '')[:3000]
        language = data.get('language', 'unknown')
        filename = Path(data.get('path', data.get('filePath', 'archivo'))).name

        if not content:
            self._send_json({'error': 'No hay archivo activo'}, 400)
            return

        logger.info(f"🔍 Analizando {filename} ({language}) con Colony")

        try:
            from core.colony_community import get_colony_community
            colony = get_colony_community()
            if not colony._session_active:
                colony.start_session()

            prompt = (
                f"Estoy viendo el archivo `{filename}` ({language}) en VSCode:\n\n"
                f"```{language}\n{content}\n```\n\n"
                f"¿Qué observas? Dame sugerencias concretas, errores potenciales o mejoras."
            )
            result = colony.deliberate(prompt, max_agents=2, max_tokens=600)
            answer = result.get('response', '')

            self._send_json({
                'success': True,
                'filename': filename,
                'language': language,
                'analysis': answer,
                'agents': result.get('agents_used', []),
            })
        except Exception as e:
            logger.error(f"Error analizando archivo: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_get_pending_commands(self):
        """
        GET /api/commands/pending
        VSEIDOS hace polling cada 2s para recibir comandos de Colony.
        """
        with _cmd_lock:
            cmds = list(_cmd_queue)
            _cmd_queue.clear()
        self._send_json({"commands": cmds, "count": len(cmds)})

    def _handle_post_push_command(self):
        """
        POST /api/commands  {type, payload}
        API externa para que Colony empuje comandos a VSCode.
        """
        data = self._read_json_body() or {}
        cmd_type = data.get("type", "show_message")
        payload  = data.get("payload", {})
        if not cmd_type:
            self._send_json({"error": "type requerido"}, 400)
            return
        cid = push_vscode_command(cmd_type, payload)
        self._send_json({"success": True, "command_id": cid})

    def _handle_post_command_result(self):
        """
        POST /api/commands/result  {command_id, success, output?, error?}
        VSEIDOS reporta el resultado de ejecutar un comando.
        """
        data = self._read_json_body() or {}
        cid     = data.get("command_id")
        success = data.get("success", False)
        output  = data.get("output", "")
        error   = data.get("error", "")
        logger.info("VSCode comando #%s resultado: success=%s output=%s error=%s",
                    cid, success, output[:80], error[:80])
        # Guardar como knowledge_node si fue exitoso
        if success and output:
            try:
                import sqlite3, hashlib, time as t
                content = f"VSCode ejecutó {data.get('type','?')}: {output[:200]}"
                nid = hashlib.md5(content.encode()).hexdigest()[:16]
                conn = get_conn(BRAIN_DB_PATH, timeout=5)
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_nodes "
                    "(id,concept,definition,category,source,confidence,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (nid, f"VSCode acción {data.get('type','?')}", output[:300],
                     "vseidos", "vseidos:command", 0.7, t.time()),
                )
                conn.commit()
                pass  # S109: get_conn no necesita close()
            except Exception:
                pass  # error no crítico, continuar
        self._send_json({"success": True, "acknowledged": cid})

    def _handle_post_sync(self):
        """
        POST /api/sync
        Sincroniza conocimiento con red P2P
        """
        logger.info("🌐 Iniciando sincronización P2P...")

        # TODO: Implementar sincronización P2P real
        # Por ahora, simulación

        try:
            knowledge_db = get_knowledge_db()
            export_data = knowledge_db.export_for_sync()

            self._send_json({
                'success': True,
                'message': 'Sincronización P2P pendiente de implementación',
                'exported_bytes': len(json.dumps(export_data))
            })

        except Exception as e:
            logger.error(f"Error sincronizando: {e}")
            self._send_json({'error': str(e)}, 500)

    def _handle_get_status(self):
        """
        GET /api/status
        Devuelve estado actual de EIDOS
        """
        self._send_json(SERVER_STATE)

    def log_message(self, format, *args):
        """Sobreescribe log por defecto para usar nuestro logger"""
        if DEBUG_MODE:
            logger.debug(f"{self.address_string()} - {format % args}")

    def log_error(self, format, *args):
        """Suprimir BrokenPipe — ocurre cuando VSEIDOS cierra la conexión antes de que responda"""
        msg = format % args
        if 'BrokenPipe' not in msg and 'Connection reset' not in msg:
            logger.warning(f"{self.address_string()} - {msg}")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Servidor multi-thread: cada petición en su propio hilo."""
    daemon_threads = True
    allow_reuse_address = True


def run_server(port: int = API_PORT):
    """Inicia el servidor HTTP"""
    server_address = ('', port)
    httpd = ThreadedHTTPServer(server_address, EidosAPIHandler)

    logger.info("=" * 60)
    logger.info("🚀 EIDOS VSCode API Server")
    logger.info("=" * 60)
    logger.info(f"📍 Puerto: {port}")
    logger.info(f"🔍 Debug: {'Habilitado' if DEBUG_MODE else 'Deshabilitado'}")
    logger.info(f"📂 EIDOS Root: {EIDOS_ROOT}")
    logger.info("=" * 60)
    logger.info("✅ Servidor iniciado - Esperando conexiones...")
    logger.info("")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("\n👋 Servidor detenido por usuario")
        httpd.shutdown()


if __name__ == '__main__':
    run_server()
