#!/usr/bin/env python3
"""
EIDOS /btw Command Handler
===========================

Sistema de comandos slash estilo Claude/terminal que permite interacción
en tiempo real mientras EIDOS trabaja.

Comandos soportados:
- /btw <question>  - Pregunta en tiempo real, EIDOS responde mientras trabaja
- /status          - Estado actual de EIDOS
- /pause           - Pausa trabajo actual
- /continue        - Continúa trabajo pausado
- /help            - Ayuda de comandos

El sistema funciona en:
- VSCode (VSEIDOS chat)
- Terminal (CLI)
- Web Panel
- Cualquier IDE conectado
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from queue import Queue, Empty
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════════════════

class CommandType(Enum):
    """Tipos de comandos slash"""
    BTW = "btw"           # Pregunta en tiempo real
    STATUS = "status"     # Estado actual
    PAUSE = "pause"       # Pausar trabajo
    CONTINUE = "continue" # Continuar trabajo
    HELP = "help"         # Ayuda
    CONTEXT = "context"   # Mostrar contexto actual
    HISTORY = "history"   # Historial de comandos


@dataclass
class Command:
    """Comando slash parseado"""
    type: CommandType
    args: str
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = "unknown"  # 'vscode', 'terminal', 'web'


@dataclass
class CommandResponse:
    """Respuesta a un comando"""
    command: Command
    response: str
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class WorkContext:
    """Contexto de trabajo actual de EIDOS"""
    current_task: Optional[str] = None
    task_progress: float = 0.0  # 0.0 - 1.0
    task_description: str = ""
    files_being_edited: List[str] = field(default_factory=list)
    current_thoughts: str = ""
    paused: bool = False
    started_at: Optional[datetime] = None


# ══════════════════════════════════════════════════════════════════════════════
# BTW Command Handler
# ══════════════════════════════════════════════════════════════════════════════

class BTWCommandHandler:
    """
    Maneja comandos /btw en tiempo real mientras EIDOS trabaja.

    Features:
    - Non-blocking: EIDOS sigue trabajando mientras responde
    - Context-aware: Entiende qué está haciendo EIDOS
    - Multi-source: Funciona desde VSCode, terminal, web
    - Real-time updates: Stream de respuestas mientras trabaja
    """

    def __init__(self):
        self.work_context = WorkContext()
        self.command_queue = Queue()
        self.response_callbacks: Dict[str, Callable[[CommandResponse], None]] = {}

        # Thread para procesar comandos en background
        self.processing_thread = threading.Thread(target=self._process_commands_loop, daemon=True)
        self.processing_thread.start()

        # Historial
        self.command_history: List[Command] = []
        self.response_history: List[CommandResponse] = []

        logger.info("🎯 BTW Command Handler initialized")

    def parse_command(self, text: str, source: str = "unknown") -> Optional[Command]:
        """
        Parse comando slash.

        Examples:
            /btw ¿qué estás haciendo ahora?
            /status
            /pause
            /help
        """
        text = text.strip()

        if not text.startswith('/'):
            return None

        # Split comando y args
        parts = text[1:].split(None, 1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        # Map a CommandType
        try:
            cmd_type = CommandType(cmd_name)
            return Command(type=cmd_type, args=args, source=source)
        except ValueError:
            # Comando no reconocido
            return None

    def handle_command(self, command: Command, callback: Optional[Callable] = None) -> CommandResponse:
        """
        Maneja un comando de forma síncrona.

        Args:
            command: Comando a ejecutar
            callback: Función para streaming de respuesta (opcional)

        Returns:
            CommandResponse con la respuesta completa
        """
        self.command_history.append(command)

        # Register callback para esta fuente
        if callback:
            self.response_callbacks[command.source] = callback

        # Dispatch según tipo
        if command.type == CommandType.BTW:
            response = self._handle_btw(command)
        elif command.type == CommandType.STATUS:
            response = self._handle_status(command)
        elif command.type == CommandType.PAUSE:
            response = self._handle_pause(command)
        elif command.type == CommandType.CONTINUE:
            response = self._handle_continue(command)
        elif command.type == CommandType.HELP:
            response = self._handle_help(command)
        elif command.type == CommandType.CONTEXT:
            response = self._handle_context(command)
        elif command.type == CommandType.HISTORY:
            response = self._handle_history(command)
        else:
            response = CommandResponse(
                command=command,
                response=f"❌ Comando no implementado: {command.type}"
            )

        self.response_history.append(response)

        # Call callback if provided
        if callback:
            callback(response)

        return response

    def _handle_btw(self, command: Command) -> CommandResponse:
        """
        Maneja comando /btw - responde en tiempo real mientras trabaja.

        El usuario pregunta algo y EIDOS responde explicando qué está
        haciendo EN ESE MOMENTO, luego continúa trabajando.
        """
        question = command.args

        # Context actual
        ctx = self.work_context

        # Construir respuesta basada en contexto
        response_parts = []

        # Header
        response_parts.append(f"💬 **BTW Response** (mientras trabajo...)")
        response_parts.append("")

        # Tu pregunta
        response_parts.append(f"Tu pregunta: *{question}*")
        response_parts.append("")

        # Qué estoy haciendo AHORA
        if ctx.current_task:
            response_parts.append(f"🔧 **Ahora mismo estoy:**")
            response_parts.append(f"   {ctx.current_task}")

            if ctx.task_description:
                response_parts.append(f"   └─ {ctx.task_description}")

            if ctx.task_progress > 0:
                progress_bar = self._make_progress_bar(ctx.task_progress)
                response_parts.append(f"   Progress: {progress_bar} {ctx.task_progress*100:.0f}%")

            response_parts.append("")

        # Archivos que estoy editando
        if ctx.files_being_edited:
            response_parts.append(f"📝 **Editando:**")
            for file in ctx.files_being_edited[:5]:  # Max 5
                response_parts.append(f"   • {file}")
            response_parts.append("")

        # Pensamientos actuales
        if ctx.current_thoughts:
            response_parts.append(f"💭 **Pensando:**")
            response_parts.append(f"   {ctx.current_thoughts}")
            response_parts.append("")

        # Responder a la pregunta específica
        response_parts.append(f"💡 **Respuesta:**")
        answer = self._generate_contextual_answer(question, ctx)
        response_parts.append(f"   {answer}")
        response_parts.append("")

        # Footer
        response_parts.append("✅ *Continúo trabajando...*")

        return CommandResponse(
            command=command,
            response="\n".join(response_parts),
            context={
                'task': ctx.current_task,
                'progress': ctx.task_progress,
                'files': ctx.files_being_edited
            }
        )

    def _handle_status(self, command: Command) -> CommandResponse:
        """Maneja /status - muestra estado actual completo"""
        ctx = self.work_context

        status_lines = []
        status_lines.append("📊 **EIDOS Status**")
        status_lines.append("=" * 50)
        status_lines.append("")

        # Estado general
        if ctx.paused:
            status_lines.append("⏸️  **Estado**: PAUSADO")
        else:
            status_lines.append("▶️  **Estado**: TRABAJANDO")

        # Tarea actual
        if ctx.current_task:
            status_lines.append(f"🔧 **Tarea**: {ctx.current_task}")

            if ctx.task_progress > 0:
                progress_bar = self._make_progress_bar(ctx.task_progress)
                status_lines.append(f"📈 **Progreso**: {progress_bar} {ctx.task_progress*100:.0f}%")

        # Tiempo trabajando
        if ctx.started_at:
            elapsed = datetime.now() - ctx.started_at
            status_lines.append(f"⏱️  **Tiempo**: {elapsed.total_seconds():.1f}s")

        # Archivos
        if ctx.files_being_edited:
            status_lines.append(f"📝 **Archivos** ({len(ctx.files_being_edited)}):")
            for file in ctx.files_being_edited[:10]:
                status_lines.append(f"   • {Path(file).name}")

        status_lines.append("")
        status_lines.append("💡 Tip: Usa `/btw <pregunta>` para preguntar mientras trabajo")

        return CommandResponse(
            command=command,
            response="\n".join(status_lines),
            context=self._context_to_dict()
        )

    def _handle_pause(self, command: Command) -> CommandResponse:
        """Maneja /pause - pausa trabajo actual"""
        self.work_context.paused = True

        return CommandResponse(
            command=command,
            response="⏸️  **PAUSADO** - Trabajo actual en pausa\n\nUsa `/continue` para reanudar"
        )

    def _handle_continue(self, command: Command) -> CommandResponse:
        """Maneja /continue - reanuda trabajo"""
        was_paused = self.work_context.paused
        self.work_context.paused = False

        if was_paused:
            return CommandResponse(
                command=command,
                response="▶️  **REANUDADO** - Continuando trabajo..."
            )
        else:
            return CommandResponse(
                command=command,
                response="ℹ️  Ya estaba trabajando (no estaba pausado)"
            )

    def _handle_help(self, command: Command) -> CommandResponse:
        """Maneja /help - muestra ayuda de comandos"""
        help_text = """
🎯 **EIDOS Slash Commands**

**Comandos disponibles:**

`/btw <pregunta>`
   Pregunta algo EN TIEMPO REAL mientras EIDOS trabaja.
   EIDOS te responderá sin parar lo que está haciendo.

   Ejemplos:
   • `/btw ¿qué estás haciendo?`
   • `/btw ¿en qué archivo trabajas?`
   • `/btw ¿cuánto falta?`

`/status`
   Muestra el estado actual completo de EIDOS.
   (Tarea, progreso, archivos, tiempo)

`/pause`
   Pausa el trabajo actual de EIDOS.

`/continue`
   Reanuda el trabajo pausado.

`/context`
   Muestra el contexto completo actual (JSON)

`/history`
   Historial de comandos ejecutados

`/help`
   Muestra esta ayuda

**Dónde funcionan:**
✓ VSCode (VSEIDOS chat panel)
✓ Terminal (EIDOS CLI)
✓ Web Dashboard
✓ Cualquier IDE conectado

**Tip**: EIDOS entiende tu pregunta en contexto y sigue trabajando! 🚀
"""
        return CommandResponse(
            command=command,
            response=help_text
        )

    def _handle_context(self, command: Command) -> CommandResponse:
        """Maneja /context - muestra contexto completo"""
        context_dict = self._context_to_dict()
        context_json = json.dumps(context_dict, indent=2, default=str)

        return CommandResponse(
            command=command,
            response=f"```json\n{context_json}\n```",
            context=context_dict
        )

    def _handle_history(self, command: Command) -> CommandResponse:
        """Maneja /history - muestra historial de comandos"""
        history_lines = []
        history_lines.append("📜 **Command History**")
        history_lines.append("")

        for i, cmd in enumerate(self.command_history[-10:], 1):  # Last 10
            history_lines.append(f"{i}. `/{cmd.type.value}` {cmd.args}")
            history_lines.append(f"   └─ {cmd.timestamp.strftime('%H:%M:%S')} from {cmd.source}")

        if not self.command_history:
            history_lines.append("(No commands yet)")

        return CommandResponse(
            command=command,
            response="\n".join(history_lines)
        )

    def _generate_contextual_answer(self, question: str, ctx: WorkContext) -> str:
        """
        Genera respuesta contextual basada en la pregunta y contexto actual.

        Esto es simplificado - en producción usaría el Chat Brain.
        """
        q_lower = question.lower()

        # Patterns comunes
        if any(word in q_lower for word in ['qué', 'que', 'what', 'doing']):
            if ctx.current_task:
                return f"Estoy {ctx.current_task}. {ctx.task_description}"
            return "Procesando tareas en background."

        elif any(word in q_lower for word in ['archivo', 'file', 'editing']):
            if ctx.files_being_edited:
                return f"Trabajando en: {', '.join([Path(f).name for f in ctx.files_being_edited[:3]])}"
            return "No estoy editando archivos ahora mismo."

        elif any(word in q_lower for word in ['cuánto', 'quanto', 'progress', 'falta']):
            if ctx.task_progress > 0:
                remaining = (1.0 - ctx.task_progress) * 100
                return f"Progreso: {ctx.task_progress*100:.0f}%. Falta aproximadamente {remaining:.0f}%."
            return "No tengo información de progreso para la tarea actual."

        elif any(word in q_lower for word in ['por qué', 'porque', 'why']):
            return f"Porque estoy siguiendo el plan: {ctx.task_description or 'completar tareas pendientes'}"

        # Fallback genérico
        return "Entiendo tu pregunta. Ahora mismo me enfoco en completar la tarea actual, pero puedo responderte mejor con más contexto."

    def _make_progress_bar(self, progress: float, width: int = 20) -> str:
        """Genera barra de progreso ASCII"""
        filled = int(progress * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"

    def _context_to_dict(self) -> Dict:
        """Convierte contexto a dict serializable"""
        ctx = self.work_context
        return {
            'current_task': ctx.current_task,
            'task_progress': ctx.task_progress,
            'task_description': ctx.task_description,
            'files_being_edited': ctx.files_being_edited,
            'current_thoughts': ctx.current_thoughts,
            'paused': ctx.paused,
            'started_at': ctx.started_at.isoformat() if ctx.started_at else None
        }

    def _process_commands_loop(self):
        """Background loop para procesar comandos de forma asíncrona"""
        while True:
            try:
                # TODO: Procesar comandos de la queue si necesario
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error in command processing loop: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    # Public API para actualizar contexto
    # ══════════════════════════════════════════════════════════════════════════

    def update_context(self,
                       task: Optional[str] = None,
                       progress: Optional[float] = None,
                       description: Optional[str] = None,
                       files: Optional[List[str]] = None,
                       thoughts: Optional[str] = None):
        """
        Actualiza el contexto de trabajo actual.

        Llamar desde cualquier parte de EIDOS cuando:
        - Empiezas una nueva tarea
        - Cambias el progreso
        - Editas un archivo
        - Tienes un nuevo pensamiento
        """
        if task is not None:
            self.work_context.current_task = task
            self.work_context.started_at = datetime.now()

        if progress is not None:
            self.work_context.task_progress = min(1.0, max(0.0, progress))

        if description is not None:
            self.work_context.task_description = description

        if files is not None:
            self.work_context.files_being_edited = files

        if thoughts is not None:
            self.work_context.current_thoughts = thoughts


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_btw_handler = None

def get_btw_handler() -> BTWCommandHandler:
    """Get singleton BTW handler"""
    global _btw_handler
    if _btw_handler is None:
        _btw_handler = BTWCommandHandler()
    return _btw_handler


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    handler = get_btw_handler()

    # Simular contexto de trabajo
    handler.update_context(
        task="Implementando Rust Knowledge DB",
        progress=0.65,
        description="Optimizando lock-free concurrency con DashMap",
        files=["rust-core/src/knowledge.rs", "tests/benchmark.py"],
        thoughts="El uso de DashMap mejora significativamente el rendimiento"
    )

    # Test commands
    test_commands = [
        "/btw ¿qué estás haciendo?",
        "/btw ¿en qué archivo trabajas?",
        "/status",
        "/help",
    ]

    for cmd_text in test_commands:
        print(f"\n{'='*70}")
        print(f"USER: {cmd_text}")
        print(f"{'='*70}")

        cmd = handler.parse_command(cmd_text, source="terminal")
        if cmd:
            response = handler.handle_command(cmd)
            print(response.response)
        else:
            print("❌ Invalid command")
