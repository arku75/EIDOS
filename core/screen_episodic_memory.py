"""
core/screen_episodic_memory.py — Memoria episódica persistente para pantalla [S89.2]

"El que no recuerda, repite. El que repite, no evoluciona." — DeepSeek

Almacena experiencias de navegación para reutilización entre sesiones:
  1. Episodios: scene_hash → action → scene_after_hash → result → reward
  2. Planes exitosos: goal → plan_steps[] → success_rate → times_reused
  3. Patrones UI: widget_type → typical_position → confidence
  4. Recuperación por similitud textual (FTS5) y por similitud de escena

Integración con ScreenController:
  - Antes de crear plan nuevo, buscar planes similares anteriores
  - Después de cada acción, almacenar scene→action→result
  - Después de misión exitosa, guardar plan completo
  - Patrones recurrentes de UI se promueven a "creencias"

Uso:
    sem = get_screen_episodic_memory()
    similar_plan = sem.find_similar_plan("investigar n8n en github")
    sem.record_action(scene_hash, action, result, reward)
    sem.record_mission_plan(goal, steps, success)
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from core.db import get_conn

log = logging.getLogger("eidos.screen_episodic")

MEMORY_DB = Path.home() / ".eidos" / "screen_episodic.db"


# ── Tipos ───────────────────────────────────────────────────────────────────────


@dataclass
class EpisodeRecord:
    """Un episodio de pantalla: escena → acción → resultado."""
    id: str
    scene_hash: str
    scene_text_preview: str
    window_title: str
    action_json: str
    result_success: bool
    reward: float
    scene_after_hash: str
    scene_after_text_preview: str
    tags: str
    timestamp: float


@dataclass
class PlanRecord:
    """Un plan de misión exitoso almacenado."""
    id: str
    goal: str
    goal_hash: str
    steps_json: str
    success_rate: float
    times_reused: int
    last_used: float
    created_at: float


@dataclass
class UIPattern:
    """Patrón de UI aprendido de la experiencia."""
    id: str
    element_text: str
    element_type: str
    typical_x_norm: float  # coordenada normalizada (0-1)
    typical_y_norm: float
    screen_context: str     # dónde suele aparecer
    occurrences: int
    confidence: float
    last_seen: float


# ── ScreenEpisodicMemory ───────────────────────────────────────────────────────


class ScreenEpisodicMemory:
    """Memoria episódica persistente para experiencias de pantalla.

    Usa SQLite + FTS5 para búsqueda textual rápida.
    Patrón: content-sync con triggers (como episodic_memory.py).
    """

    def __init__(self, db_path: Path = MEMORY_DB):
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = get_conn(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Inicializa las tablas y FTS5."""
        with self._lock:
            conn = self._conn()
            try:
                # Episodios: cada acción de pantalla
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS screen_episodes (
                        id TEXT PRIMARY KEY,
                        scene_hash TEXT NOT NULL,
                        scene_text_preview TEXT DEFAULT '',
                        window_title TEXT DEFAULT '',
                        action_json TEXT DEFAULT '{}',
                        result_success INTEGER DEFAULT 1,
                        reward REAL DEFAULT 0.0,
                        scene_after_hash TEXT DEFAULT '',
                        scene_after_text_preview TEXT DEFAULT '',
                        tags TEXT DEFAULT '',
                        timestamp REAL NOT NULL
                    )
                """)

                # FTS5 para búsqueda textual de escenas
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS screen_episodes_fts USING fts5(
                        scene_text_preview, window_title, tags,
                        content='screen_episodes', content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 2'
                    )
                """)

                # Triggers FTS5
                conn.executescript("""
                    CREATE TRIGGER IF NOT EXISTS se_ai AFTER INSERT ON screen_episodes BEGIN
                        INSERT INTO screen_episodes_fts(rowid, scene_text_preview, window_title, tags)
                        VALUES (new.rowid, new.scene_text_preview, new.window_title, new.tags);
                    END;
                    CREATE TRIGGER IF NOT EXISTS se_ad AFTER DELETE ON screen_episodes BEGIN
                        INSERT INTO screen_episodes_fts(screen_episodes_fts, rowid, scene_text_preview, window_title, tags)
                        VALUES ('delete', old.rowid, old.scene_text_preview, old.window_title, old.tags);
                    END;
                    CREATE TRIGGER IF NOT EXISTS se_au AFTER UPDATE ON screen_episodes BEGIN
                        INSERT INTO screen_episodes_fts(screen_episodes_fts, rowid, scene_text_preview, window_title, tags)
                        VALUES ('delete', old.rowid, old.scene_text_preview, old.window_title, old.tags);
                        INSERT INTO screen_episodes_fts(rowid, scene_text_preview, window_title, tags)
                        VALUES (new.rowid, new.scene_text_preview, new.window_title, new.tags);
                    END;
                """)

                # Planes de misión exitosos
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS screen_plans (
                        id TEXT PRIMARY KEY,
                        goal TEXT NOT NULL,
                        goal_hash TEXT NOT NULL,
                        steps_json TEXT DEFAULT '[]',
                        success_rate REAL DEFAULT 0.5,
                        times_reused INTEGER DEFAULT 0,
                        last_used REAL,
                        created_at REAL NOT NULL
                    )
                """)

                # FTS5 para búsqueda de planes por goal
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS screen_plans_fts USING fts5(
                        goal,
                        content='screen_plans', content_rowid='rowid',
                        tokenize='unicode61 remove_diacritics 2'
                    )
                """)

                conn.executescript("""
                    CREATE TRIGGER IF NOT EXISTS sp_ai AFTER INSERT ON screen_plans BEGIN
                        INSERT INTO screen_plans_fts(rowid, goal)
                        VALUES (new.rowid, new.goal);
                    END;
                    CREATE TRIGGER IF NOT EXISTS sp_ad AFTER DELETE ON screen_plans BEGIN
                        INSERT INTO screen_plans_fts(screen_plans_fts, rowid, goal)
                        VALUES ('delete', old.rowid, old.goal);
                    END;
                    CREATE TRIGGER IF NOT EXISTS sp_au AFTER UPDATE ON screen_plans BEGIN
                        INSERT INTO screen_plans_fts(screen_plans_fts, rowid, goal)
                        VALUES ('delete', old.rowid, old.goal);
                        INSERT INTO screen_plans_fts(rowid, goal)
                        VALUES (new.rowid, new.goal);
                    END;
                """)

                # Patrones de UI aprendidos
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS screen_ui_patterns (
                        id TEXT PRIMARY KEY,
                        element_text TEXT NOT NULL,
                        element_type TEXT DEFAULT 'unknown',
                        typical_x_norm REAL DEFAULT 0.5,
                        typical_y_norm REAL DEFAULT 0.5,
                        screen_context TEXT DEFAULT '',
                        occurrences INTEGER DEFAULT 1,
                        confidence REAL DEFAULT 0.3,
                        last_seen REAL
                    )
                """)

                conn.commit()
            finally:
                conn.close()

    # ── Grabación de episodios ─────────────────────────────────────────────────

    def record_action(self, scene_before: Any, action: Dict[str, Any],
                      result: Any, reward: float = 0.0) -> str:
        """Registra una acción de pantalla como episodio.

        Args:
            scene_before: escena antes de la acción
            action: dict con la acción ejecutada
            result: ActionResult o dict con resultado
            reward: recompensa (-1.0 a 1.0)

        Returns:
            episode_id
        """
        scene_text = getattr(scene_before, 'ocr_full_text', '') or ''
        scene_hash = hashlib.md5(scene_text[:500].encode()).hexdigest()[:16]
        window_title = getattr(scene_before, 'window_title', '') or ''

        success = getattr(result, 'success', True)
        scene_after_text = (getattr(result, 'scene_after', None) or
                           getattr(result, 'description', ''))
        if hasattr(scene_after_text, 'ocr_full_text'):
            scene_after_text = scene_after_text.ocr_full_text or ''
        scene_after_hash = hashlib.md5(
            str(scene_after_text)[:500].encode()
        ).hexdigest()[:16]

        ep_id = hashlib.md5(
            f"{scene_hash}:{action.get('action', '')}:{time.time()}".encode()
        ).hexdigest()[:16]

        tags = []
        action_type = action.get('action', '')
        if action_type:
            tags.append(action_type)
        if success:
            tags.append('success')
        else:
            tags.append('failure')
        if reward > 0:
            tags.append('positive')
        elif reward < 0:
            tags.append('negative')

        with self._lock:
            conn = self._conn()
            try:
                conn.execute("""
                    INSERT INTO screen_episodes
                    (id, scene_hash, scene_text_preview, window_title,
                     action_json, result_success, reward,
                     scene_after_hash, scene_after_text_preview,
                     tags, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ep_id,
                    scene_hash,
                    scene_text[:300],
                    window_title[:200],
                    json.dumps(action, ensure_ascii=False)[:500],
                    1 if success else 0,
                    reward,
                    scene_after_hash,
                    str(scene_after_text)[:300],
                    ','.join(tags),
                    time.time(),
                ))
                conn.commit()
                return ep_id
            except Exception as e:
                log.warning("Error grabando episodio: %s", e)
                return ""
            finally:
                conn.close()

    # ── Búsqueda de episodios ──────────────────────────────────────────────────

    def find_similar_episodes(self, scene_text: str, action_type: str = "",
                              limit: int = 10) -> List[EpisodeRecord]:
        """Busca episodios similares a la escena actual.

        Usa FTS5 para búsqueda textual rápida.
        """
        if not scene_text or len(scene_text.strip()) < 5:
            return []

        # Sanitizar para FTS5
        safe_query = self._sanitize_fts5(scene_text[:200])

        with self._lock:
            conn = self._conn()
            try:
                query = """
                    SELECT e.*, bm25(screen_episodes_fts, 0.0) as rank
                    FROM screen_episodes e
                    JOIN screen_episodes_fts fts ON e.rowid = fts.rowid
                    WHERE screen_episodes_fts MATCH ?
                """
                params = [safe_query]
                if action_type:
                    query += " AND e.tags LIKE ?"
                    params.append(f"%{action_type}%")

                query += " ORDER BY rank LIMIT ?"
                params.append(limit)

                rows = conn.execute(query, params).fetchall()
                return [EpisodeRecord(
                    id=r['id'], scene_hash=r['scene_hash'],
                    scene_text_preview=r['scene_text_preview'],
                    window_title=r['window_title'],
                    action_json=r['action_json'],
                    result_success=bool(r['result_success']),
                    reward=float(r['reward']),
                    scene_after_hash=r['scene_after_hash'] or '',
                    scene_after_text_preview=r['scene_after_text_preview'] or '',
                    tags=r['tags'] or '',
                    timestamp=float(r['timestamp']),
                ) for r in rows]
            except Exception as e:
                log.debug("Búsqueda FTS5 falló: %s. Usando LIKE fallback.", e)
                # Fallback LIKE
                try:
                    like_q = '%' + scene_text[:50].replace(' ', '%') + '%'
                    rows = conn.execute(
                        "SELECT * FROM screen_episodes WHERE scene_text_preview LIKE ? LIMIT ?",
                        (like_q, limit)
                    ).fetchall()
                    return [EpisodeRecord(
                        id=r['id'], scene_hash=r['scene_hash'],
                        scene_text_preview=r['scene_text_preview'],
                        window_title=r['window_title'],
                        action_json=r['action_json'],
                        result_success=bool(r['result_success']),
                        reward=float(r['reward']),
                        scene_after_hash=r['scene_after_hash'] or '',
                        scene_after_text_preview=r['scene_after_text_preview'] or '',
                        tags=r['tags'] or '',
                        timestamp=float(r['timestamp']),
                    ) for r in rows]
                except Exception:
                    return []
            finally:
                conn.close()

    def find_best_action_for_scene(self, scene_text: str,
                                   action_type: str = "click") -> Optional[Dict[str, Any]]:
        """Encuentra la mejor acción pasada para una escena similar.

        Retorna la acción con mayor reward para escenas textualmente similares.
        """
        episodes = self.find_similar_episodes(scene_text, action_type, limit=20)
        if not episodes:
            return None

        # Filtrar solo éxitos con reward positivo
        successful = [e for e in episodes if e.result_success and e.reward > 0]
        if not successful:
            return None

        # Ordenar por reward descendente
        successful.sort(key=lambda e: e.reward, reverse=True)

        try:
            return json.loads(successful[0].action_json)
        except Exception:
            return None

    # ── Planes de misión ────────────────────────────────────────────────────────

    def record_mission_plan(self, goal: str, steps: List[Any],
                            success: bool, result_data: Dict[str, Any] = None):
        """Guarda un plan de misión para futura reutilización.

        Args:
            goal: meta original
            steps: lista de PlanStep o dicts
            success: si la misión fue exitosa
            result_data: métricas adicionales
        """
        goal_hash = hashlib.md5(goal.lower().encode()).hexdigest()[:16]

        # Serializar steps
        steps_json = json.dumps([
            {
                "order": s.order if hasattr(s, 'order') else i,
                "action": s.action if hasattr(s, 'action') else s.get('action', ''),
                "target_description": s.target_description if hasattr(s, 'target_description') else s.get('target_description', ''),
                "target_text": s.target_text if hasattr(s, 'target_text') else s.get('target_text', ''),
                "expected_result": s.expected_result if hasattr(s, 'expected_result') else s.get('expected_result', ''),
            }
            for i, s in enumerate(steps)
        ], ensure_ascii=False)

        # Calcular success_rate (media móvil si el plan ya existe)
        existing = self._get_plan_by_hash(goal_hash)
        if existing:
            new_rate = (existing.success_rate * existing.times_reused + (1.0 if success else 0.0)) / (existing.times_reused + 1)
            plan_id = existing.id
            times_reused = existing.times_reused + 1
        else:
            new_rate = 0.8 if success else 0.3
            plan_id = hashlib.md5(
                f"{goal_hash}:{time.time()}".encode()
            ).hexdigest()[:16]
            times_reused = 0

        with self._lock:
            conn = self._conn()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO screen_plans
                    (id, goal, goal_hash, steps_json, success_rate,
                     times_reused, last_used, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                        (SELECT created_at FROM screen_plans WHERE goal_hash = ?),
                        ?
                    ))
                """, (
                    plan_id, goal, goal_hash, steps_json, new_rate,
                    times_reused, time.time(), goal_hash, time.time(),
                ))
                conn.commit()
                log.debug("Plan guardado: %s (rate=%.2f, reused=%d)",
                         goal[:60], new_rate, times_reused)
            except Exception as e:
                log.warning("Error guardando plan: %s", e)
            finally:
                conn.close()

    def find_similar_plan(self, goal: str, min_confidence: float = 0.5) -> Optional[Dict[str, Any]]:
        """Busca un plan exitoso para una meta similar.

        Args:
            goal: meta en lenguaje natural
            min_confidence: confianza mínima para considerar reutilizable

        Returns:
            Dict con {goal, steps, success_rate, times_reused} o None
        """
        goal_hash = hashlib.md5(goal.lower().encode()).hexdigest()[:16]

        # 1. Búsqueda exacta por hash
        exact = self._get_plan_by_hash(goal_hash)
        if exact and exact.success_rate >= min_confidence:
            self._bump_plan_usage(goal_hash)
            return self._plan_to_dict(exact)

        # 2. Búsqueda FTS5 por similitud textual
        safe_query = self._sanitize_fts5(goal[:200])

        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("""
                    SELECT sp.* FROM screen_plans sp
                    JOIN screen_plans_fts fts ON sp.rowid = fts.rowid
                    WHERE screen_plans_fts MATCH ?
                    AND sp.success_rate >= ?
                    ORDER BY sp.success_rate * sp.times_reused DESC
                    LIMIT 5
                """, (safe_query, min_confidence)).fetchall()

                if rows:
                    best = rows[0]
                    self._bump_plan_usage(goal_hash)
                    return self._plan_to_dict(dict(best))

            except Exception as e:
                log.debug("Búsqueda FTS5 de planes falló: %s", e)
            finally:
                conn.close()

        return None

    def _get_plan_by_hash(self, goal_hash: str) -> Optional[PlanRecord]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM screen_plans WHERE goal_hash = ?",
                    (goal_hash,)
                ).fetchone()
                if row:
                    return PlanRecord(
                        id=row['id'], goal=row['goal'],
                        goal_hash=row['goal_hash'],
                        steps_json=row['steps_json'],
                        success_rate=float(row['success_rate']),
                        times_reused=int(row['times_reused']),
                        last_used=float(row['last_used'] or 0),
                        created_at=float(row['created_at']),
                    )
            finally:
                conn.close()
        return None

    def _bump_plan_usage(self, goal_hash: str):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE screen_plans SET times_reused = times_reused + 1, "
                    "last_used = ? WHERE goal_hash = ?",
                    (time.time(), goal_hash)
                )
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

    def _plan_to_dict(self, record: PlanRecord) -> Dict[str, Any]:
        try:
            steps = json.loads(record.steps_json)
        except Exception:
            steps = []
        return {
            "goal": record.goal,
            "steps": steps,
            "success_rate": record.success_rate,
            "times_reused": record.times_reused,
            "source": "episodic_memory",
        }

    # ── Patrones de UI ──────────────────────────────────────────────────────────

    def learn_ui_pattern(self, element_text: str, element_type: str,
                         x: int, y: int, screen_width: int = 1920,
                         screen_height: int = 1080,
                         context: str = ""):
        """Aprende dónde suele aparecer un elemento de UI.

        Normaliza coordenadas para que el patrón sea reutilizable
        independientemente de la resolución.
        """
        x_norm = x / max(1, screen_width)
        y_norm = y / max(1, screen_height)

        pattern_id = hashlib.md5(
            f"{element_text.lower()}:{element_type}:{context[:50]}".encode()
        ).hexdigest()[:16]

        with self._lock:
            conn = self._conn()
            try:
                existing = conn.execute(
                    "SELECT occurrences, typical_x_norm, typical_y_norm, confidence "
                    "FROM screen_ui_patterns WHERE id = ?",
                    (pattern_id,)
                ).fetchone()

                if existing:
                    occ = existing['occurrences'] + 1
                    # Media móvil para coordenadas
                    new_x = (existing['typical_x_norm'] * (occ - 1) + x_norm) / occ
                    new_y = (existing['typical_y_norm'] * (occ - 1) + y_norm) / occ
                    new_conf = min(0.95, existing['confidence'] + 0.05)

                    conn.execute(
                        "UPDATE screen_ui_patterns SET "
                        "typical_x_norm=?, typical_y_norm=?, occurrences=?, "
                        "confidence=?, last_seen=? WHERE id=?",
                        (new_x, new_y, occ, new_conf, time.time(), pattern_id)
                    )
                else:
                    conn.execute(
                        "INSERT INTO screen_ui_patterns "
                        "(id, element_text, element_type, typical_x_norm, "
                        "typical_y_norm, screen_context, occurrences, confidence, last_seen) "
                        "VALUES (?, ?, ?, ?, ?, ?, 1, 0.3, ?)",
                        (pattern_id, element_text[:200], element_type,
                         x_norm, y_norm, context[:300], time.time())
                    )
                conn.commit()
            except Exception as e:
                log.debug("Error aprendiendo patrón UI: %s", e)
            finally:
                conn.close()

    def predict_element_position(self, element_text: str,
                                 context: str = "") -> Optional[Tuple[float, float, float]]:
        """Predice dónde debería estar un elemento de UI.

        Returns:
            (x_norm, y_norm, confidence) o None si no hay datos
        """
        with self._lock:
            conn = self._conn()
            try:
                # Búsqueda por texto similar
                rows = conn.execute(
                    "SELECT typical_x_norm, typical_y_norm, confidence, occurrences "
                    "FROM screen_ui_patterns "
                    "WHERE element_text LIKE ? OR screen_context LIKE ? "
                    "ORDER BY confidence * occurrences DESC LIMIT 1",
                    (f"%{element_text[:50]}%", f"%{context[:50]}%")
                ).fetchall()

                if rows:
                    r = rows[0]
                    return (r['typical_x_norm'], r['typical_y_norm'],
                            r['confidence'])
            except Exception:
                pass
            finally:
                conn.close()
        return None

    # ── Utilidades ──────────────────────────────────────────────────────────────

    def _sanitize_fts5(self, query: str) -> str:
        """Sanitiza query para FTS5 (escapa caracteres especiales)."""
        special = '*"()+:^~.-'
        safe = []
        for c in query:
            if c in special:
                safe.append(' ')
            else:
                safe.append(c)
        result = ''.join(safe).strip()
        if not result:
            return '"empty"'
        # Si hay múltiples palabras, unirlas con AND implícito
        words = result.split()
        if len(words) > 1:
            return ' AND '.join(f'"{w}"' if len(w) > 2 else w for w in words[:5])
        return result[:100]

    # ── Mantenimiento ───────────────────────────────────────────────────────────

    def prune_old_episodes(self, max_age_days: int = 90):
        """Limpia episodios antiguos de bajo valor."""
        cutoff = time.time() - (max_age_days * 86400)
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "DELETE FROM screen_episodes WHERE timestamp < ? AND reward <= 0",
                    (cutoff,)
                )
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                episodes = conn.execute(
                    "SELECT COUNT(*) as c FROM screen_episodes"
                ).fetchone()['c']
                plans = conn.execute(
                    "SELECT COUNT(*) as c FROM screen_plans"
                ).fetchone()['c']
                patterns = conn.execute(
                    "SELECT COUNT(*) as c FROM screen_ui_patterns"
                ).fetchone()['c']
                success_rate = conn.execute(
                    "SELECT AVG(CASE WHEN result_success THEN 1.0 ELSE 0.0 END) as avg "
                    "FROM screen_episodes"
                ).fetchone()['avg'] or 0
                return {
                    "total_episodes": episodes,
                    "total_plans": plans,
                    "total_ui_patterns": patterns,
                    "success_rate": round(success_rate, 3),
                    "db_path": str(self._db_path),
                    "db_size_mb": round(
                        self._db_path.stat().st_size / 1024 / 1024, 2
                    ) if self._db_path.exists() else 0,
                }
            except Exception as e:
                return {"error": str(e)}
            finally:
                conn.close()


# ── Singleton ─────────────────────────────────────────────────────────────────
_memory: Optional[ScreenEpisodicMemory] = None


def get_screen_episodic_memory() -> ScreenEpisodicMemory:
    global _memory
    if _memory is None:
        _memory = ScreenEpisodicMemory()
    return _memory


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="ScreenEpisodicMemory — memoria episódica para pantalla"
    )
    p.add_argument("--record-test", action="store_true",
                   help="Grabar episodio de prueba")
    p.add_argument("--search", type=str, help="Buscar episodios similares")
    p.add_argument("--find-plan", type=str, help="Buscar plan similar")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    p.add_argument("--prune", action="store_true", help="Limpiar episodios antiguos")
    args = p.parse_args()

    sem = get_screen_episodic_memory()

    if args.record_test:
        from core.screen_controller import Scene
        scene = Scene(
            timestamp=time.time(),
            window_title="Firefox — GitHub",
            ocr_full_text="n8n-io/n8n workflow automation Star 50k",
        )
        action = {"action": "click", "x": 400, "y": 300, "reason": "buscar repo"}
        ep_id = sem.record_action(scene, action, type('R', (), {'success': True, 'description': 'OK'})(), 0.8)
        print(f"Episodio grabado: {ep_id}")

        # Grabar plan
        sem.record_mission_plan(
            "investigar n8n en github",
            [
                {"order": 0, "action": "navigate", "target_description": "abrir github"},
                {"order": 1, "action": "type", "target_description": "buscar n8n"},
                {"order": 2, "action": "click", "target_description": "primer resultado"},
            ],
            success=True,
        )
        print("Plan grabado")

    elif args.search:
        results = sem.find_similar_episodes(args.search)
        print(f"Resultados para '{args.search}': {len(results)}")
        for r in results[:5]:
            print(f"  {r.id}: {r.window_title} | {r.tags} | reward={r.reward}")

    elif args.find_plan:
        plan = sem.find_similar_plan(args.find_plan, min_confidence=0.0)
        if plan:
            print(f"Plan encontrado: {plan['goal']}")
            print(f"  Success rate: {plan['success_rate']}")
            print(f"  Reused: {plan['times_reused']}")
            print(f"  Steps: {len(plan['steps'])}")
        else:
            print(f"No se encontró plan para: '{args.find_plan}'")

    elif args.prune:
        sem.prune_old_episodes(30)
        print("Limpieza completada")

    elif args.stats:
        import json
        print(json.dumps(sem.stats(), indent=2))

    else:
        p.print_help()
