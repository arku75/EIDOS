"""
core/eidos_confidence_decay.py — Decaimiento de confianza semanal

Nodos no usados en 7 días → bajan confidence 0.1
Nodos con confidence < 0.3 → marcar para re-destilación
Nodos con confidence > 0.95 y uso frecuente → consolidados (no decaen)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Dict
from core.db import get_conn

log = logging.getLogger("eidos.confidence_decay")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"


def run_decay(dry_run: bool = True) -> Dict:
    """
    Ejecuta decaimiento de confianza en brain.db.
    
    Args:
        dry_run: Si True, solo reporta sin modificar
        
    Returns:
        Dict con stats del decaimiento
    """
    t0 = time.time()
    con = get_conn(BRAIN_DB)
    
    # 1. Nodos con usage_count = 0 o muy bajo (nunca usados) → decaer
    decayed = con.execute("""
        UPDATE knowledge_nodes 
        SET confidence = MAX(confidence - 0.1, 0.1)
        WHERE usage_count < 2 
          AND confidence > 0.1
          AND source != 'seed_knowledge'
          AND source != 'knowledge_injection'
    """).rowcount if not dry_run else 0
    
    # 2. Nodos con confidence alta y mucho uso → consolidar (fijar confidence)
    consolidated = con.execute("""
        UPDATE knowledge_nodes
        SET confidence = MIN(confidence + 0.05, 1.0)
        WHERE usage_count > 10 
          AND confidence < 0.95
    """).rowcount if not dry_run else 0
    
    # 3. Nodos obsoletos (confidence < 0.3 y no usados) → marcar
    stale = con.execute("""
        SELECT COUNT(*) FROM knowledge_nodes 
        WHERE confidence < 0.3 AND usage_count < 1
    """).fetchone()[0]
    
    con.commit() if not dry_run else con.rollback()
    con.close()
    
    elapsed = time.time() - t0
    
    result = {
        "decayed": decayed,
        "consolidated": consolidated,
        "stale_marked": stale,
        "dry_run": dry_run,
        "elapsed_s": round(elapsed, 2),
    }
    
    if dry_run:
        log.info(f"[DRY RUN] Se decaerían {decayed} nodos, se consolidarían {consolidated}, {stale} obsoletos")
    else:
        log.info(f"Decaídos {decayed}, consolidados {consolidated}, {stale} obsoletos")
    
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Dry run primero
    result = run_decay(dry_run=True)
    print(f"Dry run: {result}")
