#!/usr/bin/env python3
"""
EIDOS Detailed Logger - Logging Estilo Claude
==============================================

Logging detallado que muestra:
- Qué se está haciendo (descripción clara)
- Estado ANTES de la operación
- Estado DESPUÉS de la operación
- Links clickeables a archivos/líneas
- Cambios específicos en código
- Visualización clara y profesional
"""

import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ChangeType(Enum):
    """Tipos de cambios"""
    FILE_CREATE = "file_create"
    FILE_EDIT = "file_edit"
    FILE_DELETE = "file_delete"
    FUNCTION_ADD = "function_add"
    FUNCTION_EDIT = "function_edit"
    CLASS_ADD = "class_add"
    IMPORT_ADD = "import_add"
    CONFIG_CHANGE = "config_change"
    PROCESS_START = "process_start"
    PROCESS_COMPLETE = "process_complete"
    LEARNING_START = "learning_start"
    LEARNING_COMPLETE = "learning_complete"
    ERROR = "error"
    INFO = "info"


@dataclass
class DetailedLog:
    """Log detallado con toda la información"""
    timestamp: str
    change_type: ChangeType
    description: str
    before: Optional[str] = None
    after: Optional[str] = None
    file_path: Optional[Path] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    code_before: Optional[str] = None
    code_after: Optional[str] = None
    metadata: Dict[str, Any] = None

    def get_file_link(self) -> Optional[str]:
        """Generar link clickeable a archivo"""
        if not self.file_path:
            return None

        link = f"{self.file_path}"
        if self.line_start:
            if self.line_end and self.line_end != self.line_start:
                link += f":{self.line_start}-{self.line_end}"
            else:
                link += f":{self.line_start}"

        return link

    def format_markdown(self) -> str:
        """Formatear como markdown para visualización"""
        lines = []

        # Timestamp y tipo
        emoji = self._get_emoji()
        lines.append(f"## {emoji} {self.description}")
        lines.append(f"**Time**: {self.timestamp}")
        lines.append(f"**Type**: {self.change_type.value}")
        lines.append("")

        # Link a archivo
        if self.file_path:
            link = self.get_file_link()
            lines.append(f"**File**: [{link}]({link})")
            lines.append("")

        # Antes/Después
        if self.before or self.after:
            lines.append("### Changes")
            if self.before:
                lines.append(f"**Before**: {self.before}")
            if self.after:
                lines.append(f"**After**: {self.after}")
            lines.append("")

        # Código antes/después
        if self.code_before or self.code_after:
            lines.append("### Code Changes")

            if self.code_before:
                lines.append("**Before**:")
                lines.append("```")
                lines.append(self.code_before.strip())
                lines.append("```")
                lines.append("")

            if self.code_after:
                lines.append("**After**:")
                lines.append("```")
                lines.append(self.code_after.strip())
                lines.append("```")
                lines.append("")

        # Metadata
        if self.metadata:
            lines.append("### Details")
            for key, value in self.metadata.items():
                lines.append(f"- **{key}**: {value}")
            lines.append("")

        return "\n".join(lines)

    def format_console(self) -> str:
        """Formatear para consola con colores"""
        lines = []

        emoji = self._get_emoji()
        lines.append(f"\n{emoji} {self.description}")

        # Link
        if self.file_path:
            link = self.get_file_link()
            lines.append(f"   📄 {link}")

        # Cambios
        if self.before:
            lines.append(f"   ⬅️  Before: {self.before}")
        if self.after:
            lines.append(f"   ➡️  After:  {self.after}")

        # Código
        if self.code_before or self.code_after:
            lines.append("")
            if self.code_before:
                lines.append("   Old code:")
                for line in self.code_before.strip().split('\n'):
                    lines.append(f"      - {line}")

            if self.code_after:
                lines.append("   New code:")
                for line in self.code_after.strip().split('\n'):
                    lines.append(f"      + {line}")

        return "\n".join(lines)

    def _get_emoji(self) -> str:
        """Obtener emoji según tipo de cambio"""
        emoji_map = {
            ChangeType.FILE_CREATE: "📝",
            ChangeType.FILE_EDIT: "✏️",
            ChangeType.FILE_DELETE: "🗑️",
            ChangeType.FUNCTION_ADD: "⚡",
            ChangeType.FUNCTION_EDIT: "🔧",
            ChangeType.CLASS_ADD: "🏗️",
            ChangeType.IMPORT_ADD: "📦",
            ChangeType.CONFIG_CHANGE: "⚙️",
            ChangeType.PROCESS_START: "🚀",
            ChangeType.PROCESS_COMPLETE: "✅",
            ChangeType.LEARNING_START: "📚",
            ChangeType.LEARNING_COMPLETE: "🎓",
            ChangeType.ERROR: "❌",
            ChangeType.INFO: "ℹ️",
        }
        return emoji_map.get(self.change_type, "📌")


class DetailedLogger:
    """
    Logger que mantiene historial detallado de cambios estilo Claude

    Features:
    - Logging estructurado con antes/después
    - Links clickeables a archivos
    - Visualización en consola y markdown
    - Historial completo de sesión
    - Export a diferentes formatos
    """

    def __init__(self, session_name: str = "eidos_session"):
        self.session_name = session_name
        self.session_start = datetime.now().isoformat()
        self.logs: List[DetailedLog] = []

        # Standard logger para consola
        self.logger = logging.getLogger(f"DetailedLogger.{session_name}")

    def log(
        self,
        description: str,
        change_type: ChangeType = ChangeType.INFO,
        before: Optional[str] = None,
        after: Optional[str] = None,
        file_path: Optional[str] = None,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
        code_before: Optional[str] = None,
        code_after: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Log un cambio detallado"""

        log_entry = DetailedLog(
            timestamp=datetime.now().isoformat(),
            change_type=change_type,
            description=description,
            before=before,
            after=after,
            file_path=Path(file_path) if file_path else None,
            line_start=line_start,
            line_end=line_end,
            code_before=code_before,
            code_after=code_after,
            metadata=metadata or {}
        )

        self.logs.append(log_entry)

        # Log a consola
        console_output = log_entry.format_console()
        self.logger.info(console_output)

    def log_file_create(self, file_path: str, content_preview: str = ""):
        """Log creación de archivo"""
        self.log(
            description=f"Created file: {Path(file_path).name}",
            change_type=ChangeType.FILE_CREATE,
            before="File did not exist",
            after="File created",
            file_path=file_path,
            code_after=content_preview[:200] if content_preview else None,
            metadata={"file_size": len(content_preview) if content_preview else 0}
        )

    def log_file_edit(
        self,
        file_path: str,
        line_start: int,
        line_end: int,
        old_code: str,
        new_code: str,
        description: Optional[str] = None
    ):
        """Log edición de archivo"""
        desc = description or f"Edited {Path(file_path).name}"

        self.log(
            description=desc,
            change_type=ChangeType.FILE_EDIT,
            before=f"Lines {line_start}-{line_end} (old code)",
            after=f"Lines {line_start}-{line_end} (new code)",
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            code_before=old_code,
            code_after=new_code
        )

    def log_function_add(
        self,
        file_path: str,
        function_name: str,
        line_start: int,
        code: str
    ):
        """Log adición de función"""
        self.log(
            description=f"Added function: {function_name}()",
            change_type=ChangeType.FUNCTION_ADD,
            before=f"Function {function_name} did not exist",
            after=f"Function {function_name} added at line {line_start}",
            file_path=file_path,
            line_start=line_start,
            code_after=code,
            metadata={"function_name": function_name}
        )

    def log_process_start(self, process_name: str, command: str, pid: int):
        """Log inicio de proceso"""
        self.log(
            description=f"Started process: {process_name}",
            change_type=ChangeType.PROCESS_START,
            before="Process not running",
            after=f"Process running (PID: {pid})",
            metadata={
                "process_name": process_name,
                "command": command,
                "pid": pid
            }
        )

    def log_process_complete(
        self,
        process_name: str,
        pid: int,
        exit_code: int,
        duration: float
    ):
        """Log finalización de proceso"""
        status = "Success" if exit_code == 0 else f"Failed (code {exit_code})"

        self.log(
            description=f"Completed process: {process_name}",
            change_type=ChangeType.PROCESS_COMPLETE,
            before=f"Process running (PID: {pid})",
            after=f"Process completed: {status}",
            metadata={
                "process_name": process_name,
                "pid": pid,
                "exit_code": exit_code,
                "duration_seconds": round(duration, 2)
            }
        )

    def log_learning_start(self, source: str, source_type: str):
        """Log inicio de aprendizaje"""
        self.log(
            description=f"Started learning from: {source}",
            change_type=ChangeType.LEARNING_START,
            before="Not learning",
            after=f"Learning from {source_type}",
            metadata={
                "source": source,
                "source_type": source_type
            }
        )

    def log_learning_complete(
        self,
        source: str,
        knowledge_extracted: Dict[str, Any],
        duration: float
    ):
        """Log finalización de aprendizaje"""
        items_count = len(knowledge_extracted.get('items', []))

        self.log(
            description=f"Completed learning from: {source}",
            change_type=ChangeType.LEARNING_COMPLETE,
            before=f"Learning in progress",
            after=f"Learned {items_count} items",
            metadata={
                "source": source,
                "items_learned": items_count,
                "duration_seconds": round(duration, 2),
                **knowledge_extracted
            }
        )

    def get_session_summary(self) -> str:
        """Obtener resumen de la sesión"""
        lines = []

        lines.append("=" * 70)
        lines.append(f"EIDOS Session Summary: {self.session_name}")
        lines.append(f"Started: {self.session_start}")
        lines.append(f"Logs: {len(self.logs)}")
        lines.append("=" * 70)
        lines.append("")

        # Contar por tipo
        by_type = {}
        for log in self.logs:
            type_name = log.change_type.name
            by_type[type_name] = by_type.get(type_name, 0) + 1

        lines.append("Activity Breakdown:")
        for type_name, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  - {type_name}: {count}")

        return "\n".join(lines)

    def export_markdown(self, output_path: Path):
        """Exportar todo el historial como markdown"""
        with open(output_path, 'w') as f:
            f.write(f"# EIDOS Session Log: {self.session_name}\n\n")
            f.write(f"**Started**: {self.session_start}\n")
            f.write(f"**Total Logs**: {len(self.logs)}\n\n")
            f.write("---\n\n")

            for log in self.logs:
                f.write(log.format_markdown())
                f.write("\n---\n\n")

    def export_json(self, output_path: Path):
        """Exportar como JSON"""
        import json

        data = {
            "session_name": self.session_name,
            "session_start": self.session_start,
            "total_logs": len(self.logs),
            "logs": [
                {
                    "timestamp": log.timestamp,
                    "type": log.change_type.value,
                    "description": log.description,
                    "before": log.before,
                    "after": log.after,
                    "file_path": str(log.file_path) if log.file_path else None,
                    "file_link": log.get_file_link(),
                    "line_start": log.line_start,
                    "line_end": log.line_end,
                    "metadata": log.metadata
                }
                for log in self.logs
            ]
        }

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)


# Singleton global
_detailed_logger: Optional[DetailedLogger] = None


def get_detailed_logger(session_name: str = "eidos") -> DetailedLogger:
    """Obtener instancia global del detailed logger"""
    global _detailed_logger

    if _detailed_logger is None:
        _detailed_logger = DetailedLogger(session_name)

    return _detailed_logger


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)

    logger = get_detailed_logger("test_session")

    # Simular algunos cambios
    logger.log_file_create(
        "/home/ser/test.py",
        "def hello():\n    print('Hello EIDOS!')"
    )

    logger.log_function_add(
        "/home/ser/test.py",
        "process_data",
        10,
        "def process_data(data):\n    return data.upper()"
    )

    logger.log_file_edit(
        "/home/ser/test.py",
        5,
        5,
        "print('Hello')",
        "print('Hello EIDOS!')",
        "Updated greeting message"
    )

    logger.log_process_start("test_process", "python test.py", 12345)
    logger.log_process_complete("test_process", 12345, 0, 2.5)

    logger.log_learning_start("https://youtube.com/watch?v=test", "video_youtube")
    logger.log_learning_complete(
        "https://youtube.com/watch?v=test",
        {"items": ["concept1", "concept2", "concept3"]},
        45.2
    )

    # Resumen
    print("\n" + logger.get_session_summary())
