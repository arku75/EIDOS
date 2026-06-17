#!/usr/bin/env python3
"""
EIDOS core/memory_decay.py — Memoria con Decay Temporal
========================================================
Sistema de memoria que simula el olvido natural: las memorias
menos accedidas pierden importancia con el tiempo.

Backend: SQLite en ~/.eidos/decaying_memory.db

Cada memoria tiene:
- content: texto almacenado
- importance: 0-1 (importancia base)
- created_at: timestamp de creacion
- last_accessed: ultimo acceso
- access_count: veces accedida
- decay_rate: velocidad de olvido

Formula de decay:
  effective_importance = importance * exp(-decay_rate * hours_since_access)

Acceder a una memoria la refuerza (aumenta importance).
Memorias con effective_importance < 0.05 se archivan (no se borran).

Integrable con memory_vec.py existente para busqueda semantica.

Inspirado en AutoResearchClaw: memoria que aprende que es relevante.

Uso:
    from core.memory_decay import DecayingMemory

    mem = DecayingMemory()
    mem_id = mem.store("nmap encontro puertos abiertos", importance=0.8)
    results = mem.recall("puertos abiertos", min_importance=0.1)
    mem.reinforce(mem_id)
    health = mem.memory_health()
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from core.db import get_conn

# ══════════════════════════════════════════════════════════════════════════════
# Configuracion
# ══════════════════════════════════════════════════════════════════════════════

DB_PATH = Path.home() / ".eidos" / "decaying_memory.db"

DEFAULT_DECAY_RATE = 0.01    # Decay lento por defecto (por hora)
REINFORCE_BOOST = 0.05       # Cuanto sube la importancia al reforzar
ARCHIVE_THRESHOLD = 0.05     # Por debajo de esto se archiva
MAX_IMPORTANCE = 1.0         # Importancia maxima posible


# ══════════════════════════════════════════════════════════════════════════════
# Decaying Memory
# ══════════════════════════════════════════════════════════════════════════════

class DecayingMemory:
    """
    Memoria con decay temporal que simula el olvido natural.

    Las memorias pierden importancia con el tiempo, pero accederlas
    las refuerza. Memorias muy debiles se archivan automaticamente.
    """

    def __init__(self, db_path: str = None, verbose: bool = True):
        self.db_path = db_path or str(DB_PATH)
        self.verbose = verbose
        self._lock = threading.Lock()

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = get_conn(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")

        self._init_db()

        active, archived = self._count_by_status()
        self._log(f"Inicializado: {active} activas, {archived} archivadas")

    def _log(self, msg: str):
        if self.verbose:
            print(f"🧠 [DecayingMemory] {msg}")

    def _init_db(self):
        """Crea tablas si no existen."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                importance REAL NOT NULL DEFAULT 0.5,
                decay_rate REAL NOT NULL DEFAULT 0.01,
                created_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                archived INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_decay_importance ON memories(importance);
            CREATE INDEX IF NOT EXISTS idx_decay_archived ON memories(archived);
            CREATE INDEX IF NOT EXISTS idx_decay_last_accessed ON memories(last_accessed);
        """)
        self.conn.commit()

    def _count_by_status(self) -> Tuple[int, int]:
        """Cuenta memorias activas y archivadas."""
        active = self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE archived = 0"
        ).fetchone()[0]
        archived = self.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE archived = 1"
        ).fetchone()[0]
        return active, archived

    def effective_importance(self, importance: float, decay_rate: float,
                              last_accessed: float) -> float:
        """
        Calcula la importancia efectiva con decay temporal.

        Formula: importance * exp(-decay_rate * hours_since_access)
        """
        hours_since = (time.time() - last_accessed) / 3600.0
        return importance * math.exp(-decay_rate * hours_since)

    # ──────────────────────────────────────────────────────────────────────
    # CRUD
    # ──────────────────────────────────────────────────────────────────────

    def store(self, content: str, importance: float = 0.5,
              decay_rate: float = None, tags: List[str] = None,
              metadata: Dict[str, Any] = None) -> int:
        """
        Almacena una nueva memoria.

        Args:
            content: Texto a recordar
            importance: Importancia inicial (0-1)
            decay_rate: Velocidad de olvido (por hora). Menor = recuerda mas
            tags: Etiquetas para filtrado
            metadata: Datos adicionales

        Returns:
            ID de la memoria creada
        """
        importance = max(0.0, min(MAX_IMPORTANCE, importance))
        decay_rate = decay_rate if decay_rate is not None else DEFAULT_DECAY_RATE
        tags = tags or []
        metadata = metadata or {}
        now = time.time()

        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO memories
                   (content, importance, decay_rate, created_at, last_accessed, access_count, tags, metadata, archived)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, 0)""",
                (content, importance, decay_rate, now, now,
                 json.dumps(tags), json.dumps(metadata))
            )
            self.conn.commit()
            mem_id = cur.lastrowid

        self._log(f"💾 Memoria #{mem_id} almacenada (importance={importance:.2f})")
        return mem_id

    def recall(self, query: str, min_importance: float = 0.1,
               limit: int = 10, include_archived: bool = False) -> List[Dict[str, Any]]:
        """
        Busca memorias relevantes, filtrando por importancia efectiva.

        Acceder a una memoria la refuerza automaticamente.

        Args:
            query: Texto de busqueda
            min_importance: Importancia minima efectiva
            limit: Maximo de resultados
            include_archived: Si True, incluye memorias archivadas

        Returns:
            Lista de memorias con su importancia efectiva
        """
        # Buscar por texto (LIKE con palabras clave — parametrizado)
        words = query.lower().split()[:5]
        like_conditions = []
        like_params = []
        for w in words:
            like_conditions.append("LOWER(content) LIKE ?")
            like_params.append(f"%{w}%")

        conditions = " AND ".join(like_conditions) if like_conditions else "1=1"

        if not include_archived:
            conditions += " AND archived = 0"

        sql = f"""SELECT id, content, importance, decay_rate, created_at,
                         last_accessed, access_count, tags, metadata, archived
                  FROM memories WHERE {conditions}
                  ORDER BY last_accessed DESC LIMIT ?"""

        with self._lock:
            rows = self.conn.execute(sql, (*like_params, limit * 3)).fetchall()  # extra para filtrar

        results = []
        for row in rows:
            eff_imp = self.effective_importance(row[2], row[3], row[5])

            if eff_imp < min_importance:
                continue

            results.append({
                "id": row[0],
                "content": row[1],
                "importance": row[2],
                "effective_importance": round(eff_imp, 4),
                "decay_rate": row[3],
                "created_at": row[4],
                "last_accessed": row[5],
                "access_count": row[6],
                "tags": json.loads(row[7]),
                "metadata": json.loads(row[8]),
                "archived": bool(row[9]),
            })

        # Ordenar por importancia efectiva
        results.sort(key=lambda x: x["effective_importance"], reverse=True)
        results = results[:limit]

        # Reforzar memorias accedidas
        for r in results:
            self._touch(r["id"])

        return results

    def reinforce(self, memory_id: int, boost: float = None) -> bool:
        """
        Refuerza una memoria: aumenta importancia y actualiza last_accessed.

        Args:
            memory_id: ID de la memoria
            boost: Cuanto aumentar importancia (por defecto REINFORCE_BOOST)

        Returns:
            True si se reforzo
        """
        boost = boost if boost is not None else REINFORCE_BOOST

        with self._lock:
            row = self.conn.execute(
                "SELECT importance, archived FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()

            if not row:
                return False

            new_importance = min(MAX_IMPORTANCE, row[0] + boost)
            now = time.time()

            self.conn.execute(
                """UPDATE memories
                   SET importance = ?, last_accessed = ?, access_count = access_count + 1, archived = 0
                   WHERE id = ?""",
                (new_importance, now, memory_id)
            )
            self.conn.commit()

        self._log(f"💪 Memoria #{memory_id} reforzada: {row[0]:.2f} → {new_importance:.2f}")
        return True

    def _touch(self, memory_id: int):
        """Actualiza last_accessed y access_count sin cambiar importance."""
        with self._lock:
            self.conn.execute(
                "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (time.time(), memory_id)
            )
            self.conn.commit()

    def get_memory(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """Obtiene una memoria por ID."""
        row = self.conn.execute(
            """SELECT id, content, importance, decay_rate, created_at,
                      last_accessed, access_count, tags, metadata, archived
               FROM memories WHERE id = ?""",
            (memory_id,)
        ).fetchone()

        if not row:
            return None

        eff_imp = self.effective_importance(row[2], row[3], row[5])
        return {
            "id": row[0],
            "content": row[1],
            "importance": row[2],
            "effective_importance": round(eff_imp, 4),
            "decay_rate": row[3],
            "created_at": row[4],
            "last_accessed": row[5],
            "access_count": row[6],
            "tags": json.loads(row[7]),
            "metadata": json.loads(row[8]),
            "archived": bool(row[9]),
        }

    # ──────────────────────────────────────────────────────────────────────
    # Cleanup / Archivado
    # ──────────────────────────────────────────────────────────────────────

    def cleanup(self, threshold: float = None) -> int:
        """
        Archiva memorias con importancia efectiva por debajo del threshold.
        No las borra, solo las marca como archivadas.

        Returns:
            Numero de memorias archivadas
        """
        threshold = threshold if threshold is not None else ARCHIVE_THRESHOLD
        archived_count = 0

        with self._lock:
            rows = self.conn.execute(
                "SELECT id, importance, decay_rate, last_accessed FROM memories WHERE archived = 0"
            ).fetchall()

            to_archive = []
            for row in rows:
                eff_imp = self.effective_importance(row[1], row[2], row[3])
                if eff_imp < threshold:
                    to_archive.append(row[0])

            if to_archive:
                placeholders = ",".join("?" * len(to_archive))
                self.conn.execute(
                    f"UPDATE memories SET archived = 1 WHERE id IN ({placeholders})",
                    to_archive
                )
                self.conn.commit()
                archived_count = len(to_archive)

        if archived_count > 0:
            self._log(f"📦 Archivadas {archived_count} memorias (threshold={threshold})")

        return archived_count

    def unarchive(self, memory_id: int) -> bool:
        """Desarchivar una memoria y reforzarla."""
        return self.reinforce(memory_id, boost=0.3)

    def purge_archived(self, older_than_days: int = 30) -> int:
        """
        Elimina permanentemente memorias archivadas mas antiguas que N dias.
        Usar con precaucion.
        """
        cutoff = time.time() - (older_than_days * 86400)

        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM memories WHERE archived = 1 AND last_accessed < ?",
                (cutoff,)
            )
            self.conn.commit()
            count = cur.rowcount

        if count > 0:
            self._log(f"🗑️ Purgadas {count} memorias archivadas (>{older_than_days} dias)")

        return count

    # ──────────────────────────────────────────────────────────────────────
    # Estadisticas
    # ──────────────────────────────────────────────────────────────────────

    def memory_health(self) -> Dict[str, Any]:
        """
        Diagnostico de salud de la memoria.

        Returns:
            Distribucion de importancias, memorias activas vs archivadas,
            memorias en riesgo de archivo, etc.
        """
        active, archived = self._count_by_status()

        # Distribucion de importancias efectivas
        rows = self.conn.execute(
            "SELECT importance, decay_rate, last_accessed FROM memories WHERE archived = 0"
        ).fetchall()

        if not rows:
            return {
                "active": 0,
                "archived": archived,
                "total": archived,
                "distribution": {},
                "at_risk": 0,
                "avg_importance": 0.0,
                "health_score": 0.0,
            }

        eff_importances = [
            self.effective_importance(r[0], r[1], r[2])
            for r in rows
        ]

        # Distribucion por rangos
        distribution = {
            "critical (< 0.1)": sum(1 for e in eff_importances if e < 0.1),
            "low (0.1 - 0.3)": sum(1 for e in eff_importances if 0.1 <= e < 0.3),
            "medium (0.3 - 0.6)": sum(1 for e in eff_importances if 0.3 <= e < 0.6),
            "high (0.6 - 0.8)": sum(1 for e in eff_importances if 0.6 <= e < 0.8),
            "vital (>= 0.8)": sum(1 for e in eff_importances if e >= 0.8),
        }

        at_risk = sum(1 for e in eff_importances if e < ARCHIVE_THRESHOLD * 2)
        avg_imp = sum(eff_importances) / len(eff_importances) if eff_importances else 0

        # Health score: 0-100
        health_score = min(100, int(
            (avg_imp * 40) +
            (min(active / max(active + archived, 1), 1.0) * 30) +
            (max(0, 1.0 - at_risk / max(active, 1)) * 30)
        ))

        return {
            "active": active,
            "archived": archived,
            "total": active + archived,
            "distribution": distribution,
            "at_risk": at_risk,
            "avg_effective_importance": round(avg_imp, 4),
            "health_score": health_score,
            "db_path": self.db_path,
        }

    def get_strongest(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Obtiene las memorias mas fuertes (mayor importancia efectiva)."""
        rows = self.conn.execute(
            """SELECT id, content, importance, decay_rate, last_accessed, access_count, tags
               FROM memories WHERE archived = 0"""
        ).fetchall()

        scored = []
        for row in rows:
            eff = self.effective_importance(row[2], row[3], row[4])
            scored.append({
                "id": row[0],
                "content": row[1][:100],
                "importance": row[2],
                "effective_importance": round(eff, 4),
                "access_count": row[5],
                "tags": json.loads(row[6]),
            })

        scored.sort(key=lambda x: x["effective_importance"], reverse=True)
        return scored[:limit]

    def get_weakest(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Obtiene las memorias mas debiles (proximas a archivarse)."""
        rows = self.conn.execute(
            """SELECT id, content, importance, decay_rate, last_accessed, access_count, tags
               FROM memories WHERE archived = 0"""
        ).fetchall()

        scored = []
        for row in rows:
            eff = self.effective_importance(row[2], row[3], row[4])
            scored.append({
                "id": row[0],
                "content": row[1][:100],
                "importance": row[2],
                "effective_importance": round(eff, 4),
                "access_count": row[5],
                "tags": json.loads(row[6]),
            })

        scored.sort(key=lambda x: x["effective_importance"])
        return scored[:limit]

    def close(self):
        """Cierra la conexion."""
        self.conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_dm: Optional[DecayingMemory] = None

def get_decaying_memory() -> DecayingMemory:
    """Obtiene la instancia singleton de DecayingMemory."""
    global _dm
    if _dm is None:
        _dm = DecayingMemory()
    return _dm


# ══════════════════════════════════════════════════════════════════════════════
# CLI Testing
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import os
    import tempfile

    print(f"\n{'=' * 70}")
    print(f"EIDOS DECAYING MEMORY — Test Suite")
    print(f"{'=' * 70}\n")

    # Usar DB temporal para tests
    test_db = tempfile.mktemp(suffix=".db")
    dm = DecayingMemory(db_path=test_db, verbose=True)

    passed = 0
    failed = 0

    def test(name, condition):
        global passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            failed += 1

    # ── Test 1: Almacenar memorias ──
    print("\n📌 Test 1: Almacenar memorias")
    id1 = dm.store("nmap encontro puertos 22, 80, 443 abiertos", importance=0.9, tags=["scan", "nmap"])
    id2 = dm.store("La clave SSH del servidor es RSA 1024 debil", importance=0.7, tags=["vuln", "ssh"])
    id3 = dm.store("Configuracion de nginx actualizada", importance=0.3, tags=["config"])
    id4 = dm.store("Log de debug temporal", importance=0.1, decay_rate=0.1, tags=["debug"])
    test("store retorna ID", id1 > 0 and id2 > 0)
    test("4 memorias creadas", dm._count_by_status()[0] == 4)

    # ── Test 2: Recall ──
    print("\n📌 Test 2: Recall (busqueda)")
    results = dm.recall("puertos abiertos")
    test("recall encuentra memoria", len(results) >= 1)
    test("resultado correcto", results[0]["content"].startswith("nmap"))

    results2 = dm.recall("SSH debil")
    test("recall SSH", len(results2) >= 1)

    # ── Test 3: Importancia efectiva ──
    print("\n📌 Test 3: Importancia efectiva")
    # Recien creada, effective ~= importance
    mem = dm.get_memory(id1)
    test("effective ~= importance (reciente)", abs(mem["effective_importance"] - mem["importance"]) < 0.01)

    # Simular paso del tiempo
    eff_future = dm.effective_importance(0.5, 0.01, time.time() - 3600 * 24)  # 24h atras
    test("decay reduce importancia", eff_future < 0.5)
    print(f"      importance=0.5, 24h atras: effective={eff_future:.4f}")

    eff_far_future = dm.effective_importance(0.5, 0.01, time.time() - 3600 * 168)  # 1 semana
    test("decay fuerte tras 1 semana", eff_far_future < eff_future)
    print(f"      importance=0.5, 1 semana: effective={eff_far_future:.4f}")

    # ── Test 4: Reinforce ──
    print("\n📌 Test 4: Reinforce")
    mem_before = dm.get_memory(id3)
    dm.reinforce(id3)
    mem_after = dm.get_memory(id3)
    test("reinforce aumenta importance", mem_after["importance"] > mem_before["importance"])
    test("reinforce incrementa access_count", mem_after["access_count"] > mem_before["access_count"])

    # ── Test 5: Cleanup / Archivado ──
    print("\n📌 Test 5: Cleanup / Archivado")
    # Forzar decay del log temporal manipulando last_accessed
    dm.conn.execute(
        "UPDATE memories SET last_accessed = ? WHERE id = ?",
        (time.time() - 3600 * 200, id4)  # 200 horas atras con decay_rate 0.1
    )
    dm.conn.commit()

    archived = dm.cleanup()
    test("cleanup archiva memorias debiles", archived >= 1)
    mem4 = dm.get_memory(id4)
    test("memoria debil archivada", mem4["archived"])

    # ── Test 6: Unarchive ──
    print("\n📌 Test 6: Unarchive")
    dm.unarchive(id4)
    mem4_after = dm.get_memory(id4)
    test("unarchive reactiva memoria", not mem4_after["archived"])
    test("unarchive reforzo importancia", mem4_after["importance"] > 0.1)

    # ── Test 7: Health ──
    print("\n📌 Test 7: Memory Health")
    health = dm.memory_health()
    test("health tiene active", health["active"] >= 3)
    test("health tiene distribution", "critical (< 0.1)" in health["distribution"])
    test("health score 0-100", 0 <= health["health_score"] <= 100)
    print(f"      Health score: {health['health_score']}/100")
    print(f"      Active: {health['active']}, Archived: {health['archived']}")

    # ── Test 8: Strongest/Weakest ──
    print("\n📌 Test 8: Strongest / Weakest")
    strongest = dm.get_strongest(limit=3)
    test("get_strongest retorna resultados", len(strongest) >= 1)
    test("strongest ordenado", strongest[0]["effective_importance"] >= strongest[-1]["effective_importance"])

    weakest = dm.get_weakest(limit=3)
    test("get_weakest retorna resultados", len(weakest) >= 1)

    print(f"      Strongest: {strongest[0]['content'][:50]}... (eff={strongest[0]['effective_importance']})")
    print(f"      Weakest:   {weakest[0]['content'][:50]}... (eff={weakest[0]['effective_importance']})")

    # ── Cleanup ──
    dm.close()
    os.unlink(test_db)

    print(f"\n{'=' * 70}")
    print(f"RESULTADOS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'=' * 70}\n")

    if failed > 0:
        sys.exit(1)
    print("✅ Decaying Memory funcional\n")
