#!/usr/bin/env python3
"""
Knowledge Integration Layer
===========================

Integra automáticamente entre Python y Rust Knowledge DB.
Fallback transparente si Rust no está disponible.
"""
import sys
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Try to import Rust KB first
RUST_KB_PATH = Path.home() / "EIDOS" / "rust-core" / "target" / "release"
USE_RUST = False
KnowledgeDB = None

try:
    sys.path.insert(0, str(RUST_KB_PATH))
    from eidos_core import KnowledgeDB as RustKnowledgeDB
    KnowledgeDB = RustKnowledgeDB
    USE_RUST = True
    logger.info("✅ Using Rust Knowledge DB (5x faster)")
except ImportError:
    try:
        from .knowledge_db import KnowledgeDB as PythonKnowledgeDB
        KnowledgeDB = PythonKnowledgeDB
        USE_RUST = False
        logger.info("⚠️  Using Python Knowledge DB (slower fallback)")
    except ImportError:
        logger.error("❌ No Knowledge DB available!")
        KnowledgeDB = None


def get_knowledge_db(verbose: bool = False) -> Optional[Any]:
    """
    Get Knowledge DB instance (Rust o Python automáticamente).

    Returns:
        KnowledgeDB instance or None
    """
    if KnowledgeDB is None:
        return None

    try:
        kb = KnowledgeDB(verbose=verbose)
        return kb
    except Exception as e:
        logger.error(f"Error creating Knowledge DB: {e}")
        return None


def is_using_rust() -> bool:
    """Check if using Rust KB"""
    return USE_RUST


def observe_file_with_fallback(file_path: str) -> Optional[Dict]:
    """
    Observa archivo con Rust KB o fallback a Python.

    Returns:
        Dict con observación o None si falla
    """
    kb = get_knowledge_db(verbose=False)
    if kb is None:
        return None

    try:
        if USE_RUST:
            # Rust retorna JSON string
            import json
            result = kb.observe_file(file_path)
            return json.loads(result) if isinstance(result, str) else result
        else:
            # Python retorna dict directamente
            return kb.observe_file(file_path)
    except Exception as e:
        logger.error(f"Error observing file {file_path}: {e}")
        return None
