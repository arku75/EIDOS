"""
Context Compressor para EIDOS
Reduce contexto cuando se acerca al límite del modelo
"""

import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class CompressionResult:
    """Resultado de compresión de contexto"""
    original_tokens: int
    compressed_tokens: int
    method: str
    summary: str
    preserved_messages: int
    removed_messages: int

class ContextCompressor:
    """
    Comprime el contexto de conversación cuando se acerca al límite.
    Adaptado de Hermes.
    """
    
    def __init__(
        self,
        threshold: float = 0.50,
        target_ratio: float = 0.30,
        model_context_limit: int = 128000
    ):
        self.threshold = threshold  # Comprimir al 50% del límite
        self.target_ratio = target_ratio  # Reducir al 30%
        self.model_context_limit = model_context_limit
        self.threshold_tokens = int(model_context_limit * threshold)
        self.target_tokens = int(model_context_limit * target_ratio)
        
    def should_compress(self, current_tokens: int) -> bool:
        """Determinar si se debe comprimir"""
        return current_tokens >= self.threshold_tokens
    
    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int
    ) -> CompressionResult:
        """
        Comprimir lista de mensajes.
        Estrategia: Resumir mensajes antiguos, preservar recientes.
        """
        if not self.should_compress(current_tokens):
            return CompressionResult(
                original_tokens=current_tokens,
                compressed_tokens=current_tokens,
                method="none",
                summary="No compression needed",
                preserved_messages=len(messages),
                removed_messages=0
            )
        
        # Calcular cuántos mensajes conservar (últimos 30%)
        total_messages = len(messages)
        preserve_count = max(4, int(total_messages * 0.3))
        compress_count = total_messages - preserve_count
        
        # Mensajes a comprimir (todos excepto los últimos N)
        to_compress = messages[:compress_count]
        preserved = messages[compress_count:]
        
        # Generar resumen de mensajes comprimidos
        summary = self._generate_summary(to_compress)
        
        # Nueva lista de mensajes
        system_msg = {
            "role": "system",
            "content": f"[Contexto previo resumido: {summary}]"
        }
        
        compressed_messages = [system_msg] + preserved
        
        # Estimar tokens (aproximado)
        estimated_tokens = len(str(compressed_messages)) // 4
        
        logger.info(f"Compressed {total_messages} messages -> {len(compressed_messages)} "
                   f"(removed {compress_count}, preserved {preserve_count})")
        
        return CompressionResult(
            original_tokens=current_tokens,
            compressed_tokens=estimated_tokens,
            method="summary",
            summary=summary,
            preserved_messages=preserve_count,
            removed_messages=compress_count
        )
    
    def _generate_summary(self, messages: List[Dict[str, Any]]) -> str:
        """Generar resumen de mensajes"""
        # Extraer temas principales
        topics = []
        for msg in messages:
            content = msg.get("content", "")
            if len(content) > 50:
                topics.append(content[:100] + "...")
        
        # Resumen simple
        if len(topics) > 3:
            return f"Conversación previa sobre: {', '.join(topics[:3])}... " \
                   f"({len(messages)} mensajes resumidos)"
        elif topics:
            return f"Conversación previa: {', '.join(topics)}"
        else:
            return f"{len(messages)} mensajes previos resumidos"
    
    def truncate_output(
        self,
        output: str,
        max_bytes: int = 50000,
        max_lines: int = 2000
    ) -> str:
        """Truncar output de herramientas"""
        lines = output.split('\n')
        
        if len(lines) > max_lines:
            # Mantener inicio y fin
            head = lines[:max_lines // 2]
            tail = lines[-max_lines // 2:]
            output = '\n'.join(head) + f"\n\n... [{len(lines) - max_lines} líneas truncadas] ...\n\n" + '\n'.join(tail)
        
        if len(output) > max_bytes:
            output = output[:max_bytes] + f"\n\n[... {len(output) - max_bytes} bytes truncados]"
        
        return output
