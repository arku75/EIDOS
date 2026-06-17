#!/usr/bin/env python3
"""
EIDOS Bootstrap Knowledge - Knowledge Base Seeding
===================================================

Siembra conocimiento base curado para que EIDOS tenga contexto factual
real desde el primer momento. Esto incluye:

- Información del sistema operativo y hardware
- Conceptos básicos de programación y lenguajes
- Estructura del propio EIDOS
- Conocimiento general útil

El objetivo es que EIDOS no empiece desde cero sino con una base
sólida de conocimiento verificado.
"""

from core.db import get_conn, get_conn_ctx
import os
import sys
import sqlite3
import platform
import psutil
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

# Add EIDOS to path
EIDOS_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(EIDOS_ROOT))

# Evolution Engine integration
try:
    from core.eidos_evolution_engine import get_evolution_engine
    EVOLUTION_AVAILABLE = True
except ImportError:
    EVOLUTION_AVAILABLE = False


def get_system_knowledge() -> List[Dict[str, Any]]:
    """Obtiene conocimiento factual del sistema actual"""
    knowledge = []
    
    # OS Information
    knowledge.append({
        "concept": "Sistema Operativo",
        "definition": f"EIDOS corre en {platform.system()} {platform.release()}",
        "category": "system",
        "confidence": 1.0,
        "source": "system_scan"
    })
    
    # Hardware
    cpu_info = f"{platform.machine()} con {psutil.cpu_count()} cores"
    memory_gb = psutil.virtual_memory().total / (1024**3)
    knowledge.append({
        "concept": "Hardware Actual",
        "definition": f"CPU: {cpu_info}, RAM: {memory_gb:.1f}GB",
        "category": "hardware",
        "confidence": 1.0,
        "source": "system_scan"
    })
    
    # Python version
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    knowledge.append({
        "concept": "Python Runtime",
        "definition": f"EIDOS usa Python {python_version}",
        "category": "runtime",
        "confidence": 1.0,
        "source": "system_scan"
    })
    
    return knowledge


def get_eidos_structure_knowledge() -> List[Dict[str, Any]]:
    """Conocimiento sobre la propia estructura de EIDOS"""
    knowledge = []
    
    # Core modules
    core_dir = EIDOS_ROOT / "core"
    if core_dir.exists():
        core_modules = len(list(core_dir.glob("*.py")))
        knowledge.append({
            "concept": "EIDOS Core",
            "definition": f"Núcleo de EIDOS con {core_modules} módulos Python",
            "category": "eidos_structure",
            "confidence": 1.0,
            "source": "code_analysis"
        })
    
    # Skills
    skills_dir = EIDOS_ROOT / "skills" / "plugins"
    if skills_dir.exists():
        skill_count = len(list(skills_dir.glob("*")))
        knowledge.append({
            "concept": "Skills EIDOS",
            "definition": f"Catálogo de {skill_count} habilidades disponibles",
            "category": "eidos_structure",
            "confidence": 1.0,
            "source": "code_analysis"
        })
    
    # Colony agents
    knowledge.append({
        "concept": "Colony Community",
        "definition": "Comunidad de 5 agentes especializados: coder, analyst, vision, operator, general",
        "category": "eidos_structure",
        "confidence": 1.0,
        "source": "code_analysis"
    })
    
    return knowledge


def get_programming_knowledge() -> List[Dict[str, Any]]:
    """Conceptos básicos de programación"""
    knowledge = [
        {
            "concept": "Python",
            "definition": "Lenguaje de programación interpretado, dinámico y multipropósito",
            "category": "programming",
            "confidence": 1.0,
            "source": "curated"
        },
        {
            "concept": "SQLite",
            "definition": "Base de datos SQL ligera, sin servidor, usada para persistencia local",
            "category": "database",
            "confidence": 1.0,
            "source": "curated"
        },
        {
            "concept": "API REST",
            "definition": "Interfaz de programación que usa métodos HTTP para operaciones CRUD",
            "category": "web",
            "confidence": 1.0,
            "source": "curated"
        },
        {
            "concept": "Ollama",
            "definition": "Plataforma local para ejecutar modelos de lenguaje como Llama y Qwen",
            "category": "ai",
            "confidence": 1.0,
            "source": "curated"
        },
        {
            "concept": "Autonomía",
            "definition": "Capacidad de operar sin intervención directa, aprendiendo y adaptándose",
            "category": "concept",
            "confidence": 1.0,
            "source": "curated"
        }
    ]
    
    return knowledge


def seed_bootstrap_knowledge() -> Dict[str, int]:
    """
    Siembra conocimiento base curado en la base de datos de EIDOS.
    
    Returns:
        Dict con estadísticas: inserted, updated, skipped
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0}
    
    if not EVOLUTION_AVAILABLE:
        print("⚠️ Evolution Engine no disponible - usando fallback")
        return stats
    
    try:
        evolution = get_evolution_engine()
        
        # Recolectar todo el conocimiento base
        all_knowledge = []
        all_knowledge.extend(get_system_knowledge())
        all_knowledge.extend(get_eidos_structure_knowledge())
        all_knowledge.extend(get_programming_knowledge())
        
        # Insertar en la base de datos
        with get_conn_ctx(evolution.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA busy_timeout=30000")
            for item in all_knowledge:
                concept = item["concept"]
                definition = item["definition"]
                source = item["source"]
                confidence = item["confidence"]
                
                # Verificar si ya existe
                cursor = conn.execute(
                    "SELECT id FROM knowledge_nodes WHERE concept = ?",
                    (concept,)
                )
                existing = cursor.fetchone()
                
                if existing:
                    # Actualizar si es necesario
                    conn.execute("""
                        UPDATE knowledge_nodes 
                        SET definition = ?, source = ?, confidence = ?
                        WHERE concept = ?
                    """, (definition, source, confidence, concept))
                    stats["updated"] += 1
                else:
                    # Insertar nuevo
                    conn.execute("""
                        INSERT INTO knowledge_nodes 
                        (concept, definition, category, source, confidence, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (concept, definition, item.get("category", "general"), source, 
                          confidence, datetime.now().isoformat()))
                    stats["inserted"] += 1
            
            conn.commit()
        
        # Actualizar score de independencia
        evolution._update_independence_score()
        
        print(f"✅ Bootstrap Knowledge completado:")
        print(f"   Insertados: {stats['inserted']}")
        print(f"   Actualizados: {stats['updated']}")
        print(f"   Total conceptos: {sum(stats.values())}")
        
    except Exception as e:
        print(f"❌ Error en bootstrap knowledge: {e}")
        import traceback
        traceback.print_exc()
    
    return stats


if __name__ == "__main__":
    print("🌱 EIDOS Bootstrap Knowledge")
    print("=" * 50)
    stats = seed_bootstrap_knowledge()
    print(f"\nResumen: {stats}")
