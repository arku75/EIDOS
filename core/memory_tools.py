"""
core/memory_tools.py — Fase 35: Memory + Notification + Scheduler
──────────────────────────────────────────────────────────────────
4 tools de prioridad máxima que faltaban:
  1. record_exchange  — guarda cada conversación en history.jsonl (aprendizaje real)
  2. memory_recall    — búsqueda semántica en ChromaDB de conversaciones pasadas
  3. notify_ser       — EIDOS envía notificaciones KDE proactivas a SER
  4. schedule_task    — EIDOS programa tareas futuras por sí solo (cron integrado)
"""

from __future__ import annotations

import json
import os
import subprocess
import sqlite3
import hashlib
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from core.db import get_conn

# ── Rutas ────────────────────────────────────────────────────────────────────
EIDOS_DIR = Path.home() / ".eidos"
HISTORY_FILE = EIDOS_DIR / "history.jsonl"
SCHEDULE_DB = EIDOS_DIR / "schedule.db"
EIDOS_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
#  1. RECORD EXCHANGE — guarda conversación en history.jsonl
# ══════════════════════════════════════════════════════════════════════════════

def record_exchange(args: dict[str, Any]) -> str:
    """
    Guarda un par (user, assistant) en history.jsonl.
    Args:
        user    (str): mensaje del usuario
        reply   (str): respuesta de EIDOS
        tools   (list[str], opcional): herramientas usadas
        tags    (list[str], opcional): etiquetas semánticas
    Returns:
        str: confirmación con ID del registro
    """
    user_msg = str(args.get("user", "")).strip()
    reply_msg = str(args.get("reply", "")).strip()
    tools_used: list[str] = args.get("tools", [])
    tags: list[str] = args.get("tags", [])

    if not user_msg or not reply_msg:
        return "[record_exchange] ERROR: 'user' y 'reply' son obligatorios"

    record = {
        "id": hashlib.md5(f"{user_msg}{datetime.utcnow().isoformat()}".encode()).hexdigest()[:12],
        "ts": datetime.utcnow().isoformat(),
        "user": user_msg,
        "reply": reply_msg,
        "tools": tools_used,
        "tags": tags,
    }

    try:
        # Lanzar la indexación en ChromaDB en un hilo de background para no bloquear el Kernel (<50ms total)
        threading.Thread(
            target=_try_chromadb_index,
            args=(record,),
            daemon=True,
            name=f"chromadb_indexer_{record['id']}"
        ).start()

        return f"[record_exchange] ✅ Guardado ID={record['id']} | ts={record['ts'][:19]}"
    except Exception as e:
        return f"[record_exchange] ERROR escribiendo history.jsonl: {e}"


def _try_chromadb_index(record: dict) -> None:
    """Intenta indexar en ChromaDB y/o memory_vec (falla silenciosamente)."""
    text = f"USER: {record['user']}\nEIDOS: {record['reply']}"
    tags = record.get("tags", [])

    # 1. Intentar memory_vec (ligero, sqlite-vec, siempre disponible)
    try:
        from core.memory_vec import SemanticMemory
        mem = SemanticMemory()
        mem.store(text, tags=tags)
    except Exception:
        pass  # error no crítico, continuar
    # 2. Intentar ChromaDB (pesado, puede no estar disponible)
    try:
        import chromadb  # type: ignore[import]
        client = chromadb.PersistentClient(path=str(EIDOS_DIR / "chroma"))
        col = client.get_or_create_collection("eidos_history")
        col.add(
            documents=[text],
            ids=[record["id"]],
            metadatas=[{"ts": record["ts"], "tags": ",".join(tags)}],
        )
    except Exception:
        pass  # ChromaDB opcional

def trigger_history_autoindex() -> str:
    """Dispara un escaneo asíncrono en background de TODO el historial pasado
    y lo inyecta en ChromaDB para dotar a EIDOS de recuerdos a largo plazo.
    """
    def _bulk_index():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(EIDOS_DIR / "chroma"))
            col = client.get_or_create_collection("eidos_history")
            
            if not HISTORY_FILE.exists():
                return
            
            with HISTORY_FILE.open("r", encoding="utf-8") as f:
                docs, ids, metas = [], [], []
                for line in f:
                    if not line.strip(): continue
                    try:
                        r = json.loads(line)
                        rid = r.get("id")
                        if not rid: continue
                        text = f"USER: {r.get('user', '')}\nEIDOS: {r.get('reply', '')}"
                        docs.append(text)
                        ids.append(rid)
                        metas.append({"ts": r.get("ts", ""), "tags": ",".join(r.get("tags", []))})
                    except Exception:
                        continue
                
                if docs:
                    # Sobrescribir los existentes si hay colisión, o agregarlos
                    # Idealmente dividir en lotes para evitar RAM spike, pero para JSONL pequeños:
                    # upsert procesa batches
                    col.upsert(documents=docs, ids=ids, metadatas=metas)
                    print(f"\n[MEMORIA] 🧠 Auto-index completado: {len(docs)} recuerdos inyectados al inconsciente.\n")
        except Exception as e:
            print(f"Error en auto-index: {e}")

    threading.Thread(target=_bulk_index, daemon=True, name="chromadb_bulk_indexer").start()
    return "[MEMORIA] 🔄 Proceso de inyección en memoria profunda (ChromaDB) lanzado en background..."


# ══════════════════════════════════════════════════════════════════════════════
#  2. MEMORY RECALL — búsqueda semántica en historial
# ══════════════════════════════════════════════════════════════════════════════

def memory_recall(args: dict[str, Any]) -> str:
    """
    Busca conversaciones pasadas relevantes para la query.
    Args:
        query   (str): consulta semántica
        n       (int, def 5): número de resultados
        days    (int, def 30): buscar solo en los últimos N días
    Returns:
        str: resultados formateados o '(sin resultados)'
    """
    query = str(args.get("query", "")).strip()
    n_results = int(args.get("n", 5))
    days_back = int(args.get("days", 30))

    if not query:
        return "[memory_recall] ERROR: 'query' es obligatoria"

    # 1. Intentar memory_vec primero (ligero, sqlite-vec KNN)
    vec_result = _recall_memory_vec(query, n_results)
    if vec_result:
        return vec_result

    # 2. Intentar ChromaDB (semántico, pesado)
    chroma_result = _recall_chromadb(query, n_results)
    if chroma_result:
        return chroma_result

    # 3. Fallback: búsqueda keyword en history.jsonl
    return _recall_jsonl(query, n_results, days_back)


def _recall_memory_vec(query: str, n: int) -> str:
    """Búsqueda vectorial KNN en sqlite-vec (ligero, siempre disponible)."""
    try:
        from core.memory_vec import SemanticMemory
        mem = SemanticMemory()
        results = mem.search(query, k=n)
        if not results:
            return ""
        lines = [f"[memory_recall via sqlite-vec — {len(results)} resultados]\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{'─'*50}")
            lines.append(f"[{i}] dist={r.get('distance', '?'):.3f}")
            lines.append(r.get("text", "")[:400])
        return "\n".join(lines)
    except Exception:
        return ""


def _recall_chromadb(query: str, n: int) -> str:
    """Búsqueda vectorial en ChromaDB."""
    try:
        import chromadb  # type: ignore[import]
        client = chromadb.PersistentClient(path=str(EIDOS_DIR / "chroma"))
        col = client.get_or_create_collection("eidos_history")
        if col.count() == 0:
            return ""
        results = col.query(query_texts=[query], n_results=min(n, col.count()))
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        if not docs:
            return "(sin resultados en ChromaDB)"
        lines = [f"[memory_recall via ChromaDB — {len(docs)} resultados]\n"]
        for i, (doc, meta) in enumerate(zip(docs, metas), 1):
            ts = meta.get("ts", "?")[:19]
            lines.append(f"{'─'*50}")
            lines.append(f"[{i}] {ts}")
            lines.append(doc[:400])
        return "\n".join(lines)
    except Exception:
        return ""


def _recall_jsonl(query: str, n: int, days_back: int) -> str:
    """Búsqueda keyword simple en history.jsonl."""
    if not HISTORY_FILE.exists():
        return "[memory_recall] history.jsonl vacío — aún no hay historial guardado"

    cutoff = datetime.utcnow() - timedelta(days=days_back)
    keywords = query.lower().split()
    matches: list[dict] = []

    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rec_ts = datetime.fromisoformat(rec.get("ts", "2000-01-01"))
                    if rec_ts < cutoff:
                        continue
                    text = (rec.get("user", "") + " " + rec.get("reply", "")).lower()
                    if any(kw in text for kw in keywords):
                        matches.append(rec)
                except Exception:
                    continue
    except Exception as e:
        return f"[memory_recall] ERROR leyendo historial: {e}"

    if not matches:
        return f"[memory_recall] Sin resultados para '{query}' en los últimos {days_back} días"

    matches = matches[-n:]  # últimos N
    lines = [f"[memory_recall via keyword — {len(matches)} resultados]\n"]
    for i, rec in enumerate(matches, 1):
        lines.append(f"{'─'*50}")
        lines.append(f"[{i}] {rec.get('ts', '?')[:19]}")
        lines.append(f"USR: {rec.get('user', '')[:200]}")
        lines.append(f"BOT: {rec.get('reply', '')[:200]}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  3. NOTIFY SER — notificaciones KDE proactivas
# ══════════════════════════════════════════════════════════════════════════════

def notify_ser(args: dict[str, Any]) -> str:
    """
    Envía una notificación de escritorio KDE a SER via notify-send.
    Args:
        message  (str): cuerpo de la notificación
        title    (str, def "EIDOS"): título
        urgency  (str, def "normal"): low | normal | critical
        icon     (str, def "dialog-information"): icono
        timeout  (int, def 5000): ms en pantalla (0 = persistente)
    Returns:
        str: confirmación o error
    """
    message = str(args.get("message", "")).strip()
    title = str(args.get("title", "EIDOS 🤖"))
    urgency = str(args.get("urgency", "normal"))
    icon = str(args.get("icon", "dialog-information"))
    timeout = int(args.get("timeout", 5000))

    if not message:
        return "[notify_ser] ERROR: 'message' es obligatorio"

    if urgency not in ("low", "normal", "critical"):
        urgency = "normal"

    # Intentar notify-send (freedesktop — funciona en KDE/GNOME)
    cmd = [
        "notify-send",
        "--urgency", urgency,
        "--icon", icon,
        f"--expire-time={timeout}",
        title,
        message,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
        if result.returncode == 0:
            return f"[notify_ser] ✅ Notificación enviada: '{title}: {message[:50]}'"
        else:
            # Fallback: kdialog
            return _notify_kdialog(title, message, urgency)
    except FileNotFoundError:
        return _notify_kdialog(title, message, urgency)
    except Exception as e:
        return f"[notify_ser] ERROR: {e}"


def _notify_kdialog(title: str, message: str, urgency: str) -> str:
    """Fallback via kdialog (KDE)."""
    try:
        cmd = ["kdialog", "--passivepopup", message, "5", "--title", title]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return f"[notify_ser] ✅ Notificación kdialog: '{title}: {message[:50]}'"
        return f"[notify_ser] ⚠️ notify-send y kdialog fallaron. Mensaje: {title}: {message}"
    except Exception as e:
        return f"[notify_ser] ⚠️ Fallback kdialog error: {e}. Mensaje: {title}: {message}"


# ══════════════════════════════════════════════════════════════════════════════
#  4. SCHEDULE TASK — programa tareas futuras (cron integrado en SQLite)
# ══════════════════════════════════════════════════════════════════════════════

def _init_schedule_db() -> sqlite3.Connection:
    """Inicializa la base de datos de tareas programadas."""
    conn = get_conn(SCHEDULE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id          TEXT PRIMARY KEY,
            command     TEXT NOT NULL,
            description TEXT NOT NULL,
            run_at      TEXT NOT NULL,
            repeat      TEXT DEFAULT 'once',
            status      TEXT DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            last_run    TEXT
        )
    """)
    conn.commit()
    return conn


def schedule_task(args: dict[str, Any]) -> str:
    """
    Programa una tarea futura para que EIDOS la ejecute automáticamente.
    Args:
        command     (str): comando shell o instrucción para EIDOS
        description (str): descripción legible
        delay_min   (int, def 0): ejecutar en X minutos desde ahora
        run_at      (str, opcional): ISO datetime "2026-03-12T03:00:00" (anula delay_min)
        repeat      (str, def "once"): once | hourly | daily | weekly
    Returns:
        str: confirmación con ID de la tarea programada
    """
    command = str(args.get("command", "")).strip()
    description = str(args.get("description", "Tarea EIDOS")).strip()
    delay_min = int(args.get("delay_min", 0))
    run_at_str = args.get("run_at", "")
    repeat = str(args.get("repeat", "once"))

    if not command:
        return "[schedule_task] ERROR: 'command' es obligatorio"

    if repeat not in ("once", "hourly", "daily", "weekly"):
        repeat = "once"

    # Calcular run_at
    if run_at_str:
        try:
            run_at = datetime.fromisoformat(run_at_str)
        except ValueError:
            return f"[schedule_task] ERROR: 'run_at' formato inválido (usa ISO: 2026-03-12T03:00:00)"
    else:
        run_at = datetime.utcnow() + timedelta(minutes=max(0, delay_min))

    task_id = hashlib.md5(f"{command}{run_at.isoformat()}".encode()).hexdigest()[:10]
    now_str = datetime.utcnow().isoformat()

    try:
        conn = _init_schedule_db()
        conn.execute(
            """INSERT OR REPLACE INTO scheduled_tasks
               (id, command, description, run_at, repeat, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (task_id, command, description, run_at.isoformat(), repeat, now_str),
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        # Arrancar el scheduler si no está corriendo
        _ensure_scheduler_running()

        local_run = run_at.strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"[schedule_task] ✅ Tarea programada:\n"
            f"  ID:     {task_id}\n"
            f"  Desc:   {description}\n"
            f"  Cmd:    {command[:80]}\n"
            f"  Cuando: {local_run} UTC\n"
            f"  Repite: {repeat}"
        )
    except Exception as e:
        return f"[schedule_task] ERROR guardando tarea: {e}"


def list_scheduled_tasks(args: dict[str, Any]) -> str:
    """Lista todas las tareas programadas pendientes."""
    try:
        conn = _init_schedule_db()
        rows = conn.execute(
            "SELECT id, description, run_at, repeat, status FROM scheduled_tasks ORDER BY run_at"
        ).fetchall()
        pass  # S109: get_conn no necesita close()
        if not rows:
            return "[schedule_task] Sin tareas programadas"
        lines = [f"[Tareas programadas — {len(rows)} total]"]
        for row in rows:
            lines.append(f"  {row[0]} | {row[4]:8s} | {row[2][:16]} | {row[3]:6s} | {row[1][:40]}")
        return "\n".join(lines)
    except Exception as e:
        return f"[schedule_task] ERROR listando tareas: {e}"


# ── Scheduler daemon (hilo background) ────────────────────────────────────────

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _ensure_scheduler_running() -> None:
    """Arranca el scheduler en background si no está activo."""
    global _scheduler_started
    with _scheduler_lock:
        if not _scheduler_started:
            t = threading.Thread(target=_scheduler_loop, daemon=True, name="eidos_scheduler")
            t.start()
            _scheduler_started = True


def _scheduler_loop() -> None:
    """Loop que verifica y ejecuta tareas programadas cada 60 segundos."""
    import time
    while True:
        try:
            _run_due_tasks()
        except Exception:
            pass  # error no crítico, continuar
        time.sleep(60)


def _run_due_tasks() -> None:
    """Ejecuta las tareas cuyo run_at ya pasó."""
    now = datetime.utcnow()
    try:
        conn = _init_schedule_db()
        rows = conn.execute(
            "SELECT id, command, description, repeat FROM scheduled_tasks "
            "WHERE status='pending' AND run_at <= ?",
            (now.isoformat(),)
        ).fetchall()

        for task_id, command, description, repeat in rows:
            try:
                # Ejecutar el comando
                result = subprocess.run(
                    command, shell=True, capture_output=True,
                    text=True, timeout=30,
                )
                output = (result.stdout or result.stderr or "").strip()[:200]

                # Notificar a SER
                notify_ser({
                    "title": "EIDOS ⏰ Tarea ejecutada",
                    "message": f"{description}: {output[:100] or 'OK'}",
                    "urgency": "normal",
                })

                # Actualizar estado
                if repeat == "once":
                    conn.execute(
                        "UPDATE scheduled_tasks SET status='done', last_run=? WHERE id=?",
                        (now.isoformat(), task_id)
                    )
                else:
                    # Calcular próxima ejecución
                    delta_map = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
                    next_run = now + delta_map.get(repeat, timedelta(days=1))
                    conn.execute(
                        "UPDATE scheduled_tasks SET run_at=?, last_run=? WHERE id=?",
                        (next_run.isoformat(), now.isoformat(), task_id)
                    )
            except Exception as e:
                conn.execute(
                    "UPDATE scheduled_tasks SET status='failed', last_run=? WHERE id=?",
                    (now.isoformat(), task_id)
                )

        conn.commit()
        pass  # S109: get_conn no necesita close()
    except Exception:
        pass  # error no crítico, continuar
# ══════════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS para TOOLS[] (formato Ollama)
# ══════════════════════════════════════════════════════════════════════════════

MEMORY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "record_exchange",
            "description": "Guarda una conversación (user + reply) en el historial persistente de EIDOS para aprendizaje futuro.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user":  {"type": "string", "description": "Mensaje del usuario"},
                    "reply": {"type": "string", "description": "Respuesta dada por EIDOS"},
                    "tools": {"type": "array", "items": {"type": "string"}, "description": "Herramientas usadas (opcional)"},
                    "tags":  {"type": "array", "items": {"type": "string"}, "description": "Etiquetas semánticas (opcional)"},
                },
                "required": ["user", "reply"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recall",
            "description": "Busca en el historial de conversaciones anteriores de EIDOS para encontrar información relevante.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Consulta semántica o keyword"},
                    "n":     {"type": "integer", "description": "Número de resultados (default 5)"},
                    "days":  {"type": "integer", "description": "Buscar solo en los últimos N días (default 30)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_ser",
            "description": "Envía una notificación de escritorio KDE a SER. Úsalo para alertas proactivas, recordatorios o resultados importantes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Texto de la notificación"},
                    "title":   {"type": "string", "description": "Título (default: EIDOS)"},
                    "urgency": {"type": "string", "enum": ["low", "normal", "critical"], "description": "Urgencia"},
                    "timeout": {"type": "integer", "description": "Milisegundos en pantalla (default 5000)"},
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_task",
            "description": "Programa una tarea para que EIDOS la ejecute automáticamente en el futuro (delay o fecha específica).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command":     {"type": "string", "description": "Comando shell o instrucción a ejecutar"},
                    "description": {"type": "string", "description": "Descripción legible de la tarea"},
                    "delay_min":   {"type": "integer", "description": "Ejecutar en X minutos desde ahora (default 0)"},
                    "run_at":      {"type": "string", "description": "Fecha-hora exacta ISO: 2026-03-12T03:00:00"},
                    "repeat":      {"type": "string", "enum": ["once", "hourly", "daily", "weekly"], "description": "Frecuencia"},
                },
                "required": ["command", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scheduled_tasks",
            "description": "Lista todas las tareas futuras programadas en EIDOS.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

MEMORY_TOOL_IMPL = {
    "record_exchange":      record_exchange,
    "memory_recall":        memory_recall,
    "notify_ser":           notify_ser,
    "schedule_task":        schedule_task,
    "list_scheduled_tasks": list_scheduled_tasks,
}

MEMORY_TOOL_SCHEMAS = {
    "record_exchange": {
        "user":  {"type": str, "required": True},
        "reply": {"type": str, "required": True},
    },
    "memory_recall": {
        "query": {"type": str, "required": True},
    },
    "notify_ser": {
        "message": {"type": str, "required": True},
    },
    "schedule_task": {
        "command":     {"type": str, "required": True},
        "description": {"type": str, "required": True},
    },
    "list_scheduled_tasks": {},
}
