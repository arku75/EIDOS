"""
core/eidos_curiosity_engine.py — Autonomous Curiosity Engine for EIDOS

EIDOS explores and learns WITHOUT being told what to do. This is not the
passive "ask a question and find an answer" curiosity — it's the active,
autonomous explorer that:

1. **Ignorance Atlas**: Tracks everything EIDOS DOESN'T know but could explore
   - Filesystem paths never visited
   - URLs never fetched
   - Commands never executed
   - Knowledge categories with < 5 nodes
   - Procedural gaps (concepts without recipes)

2. **Curiosity Scheduler**: Every 10 minutes when idle
   - Picks the highest-priority unknown item
   - Explores it (read file, fetch URL, learn command)
   - Records result
   - Generates 2-3 NEW things to explore based on what was found
   - Updates the Atlas

3. **Night Explorer Mode**: When SER is inactive (no keyboard/mouse for 30 min)
   - Activates aggressive exploration
   - Follows curiosity chains (A -> B -> C)
   - Synthesizes morning briefing

4. **Integration**: Wires into AliveOrchestrator so it runs automatically
   during idle cycles

Uso:
    from core.eidos_curiosity_engine import get_curiosity_engine
    engine = get_curiosity_engine()
    engine.start()          # background thread
    engine.tick()           # single exploration step (manual)
    engine.morning_briefing()  # synthesize overnight discoveries
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from core.db import get_conn

log = logging.getLogger("eidos.curiosity_engine")

# ── Constants ────────────────────────────────────────────────────────────────

EIDOS_ROOT = Path(__file__).resolve().parent.parent
BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
ATLAS_DB = Path.home() / ".eidos" / "curiosity_atlas.db"
BRIEFING_LOG = Path.home() / ".eidos" / "morning_briefing.md"

# Default intervals
DEFAULT_IDLE_INTERVAL = 600   # 10 min in normal mode
NIGHT_INTERVAL = 60           # 60 sec in night mode
NIGHT_IDLE_THRESHOLD = 1800   # 30 min of no keyboard/mouse

# Safe exploration commands (read-only)
SAFE_EXPLORE_CMDS = [
    ("man -P cat {cmd}", "man_page"),
    ("{cmd} --help 2>&1 | head -80", "help_output"),
    ("whatis {cmd} 2>&1", "whatis"),
    ("type {cmd} 2>&1", "type"),
    ("which {cmd} 2>&1", "which"),
]

# Filesystem exploration limits
MAX_FILE_READ_BYTES = 128 * 1024      # 128KB max per file
MAX_DIR_ENTRIES = 200                  # max entries per directory scan
READ_BLOCKED_PATTERNS = [
    "/etc/shadow", "/root/", ".ssh/id_", ".gnupg/",
    "/proc/*/mem", "/sys/kernel/security",
]
# Limit exploration to these roots
FS_EXPLORE_ROOTS = [
    str(Path.home()),
    "/etc",
    "/usr/share/doc",
    "/usr/local/bin",
    "/opt",
    "/var/log",
]

# URL exploration patterns (safe: documentation, not arbitrary web)
URL_PATTERNS = {
    "man_page": "https://man7.org/linux/man-pages/man1/{cmd}.1.html",
    "arch_wiki": "https://wiki.archlinux.org/title/{topic}",
    "debian_man": "https://manpages.debian.org/{cmd}",
}

# Categories considered "thin" when < THIN_CATEGORY_THRESHOLD nodes
THIN_CATEGORY_THRESHOLD = 5

# ── Idle detection ───────────────────────────────────────────────────────────

def _detect_user_active() -> bool:
    """Return True if SER is actively using keyboard/mouse.
    Uses xprintidle (X11 idle ms) if available, falls back to
    checking /proc/interrupts for i8042 activity delta."""
    try:
        # Primary method: xprintidle (X11 idle time in ms)
        r = subprocess.run(
            ["xprintidle"], capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            idle_ms = int(r.stdout.strip())
            return idle_ms < 30000  # active if moved in last 30s
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Fallback: check if any GUI process has recent CPU activity
    try:
        r = subprocess.run(
            ["ps", "-eo", "pid,etime,comm"],
            capture_output=True, text=True, timeout=2
        )
        now = time.time()
        gui_procs = {"Xorg", "plasmashell", "kwin", "firefox-esr", "dolphin", "konsole"}
        for line in r.stdout.strip().split("\n")[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) >= 3:
                comm = parts[2]
                if any(g in comm for g in gui_procs):
                    return True  # GUI processes running, assume active
    except Exception:
        pass

    # Final fallback: assume active (safer to not go night-mode accidentally)
    return True


def _get_idle_seconds() -> float:
    """Return how many seconds SER has been idle (0 if active)."""
    try:
        r = subprocess.run(
            ["xprintidle"], capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip()) / 1000.0
    except Exception:
        pass
    return 0.0


# ── Ignorance Atlas ──────────────────────────────────────────────────────────

class IgnoranceAtlas:
    """SQLite-backed data structure tracking everything EIDOS doesn't know.

    The Atlas is the anti-brain: it stores gaps, not knowledge.
    Each unexplored item has a type, payload, priority, and provenance chain.

    Schema (single table):
        CREATE TABLE IF NOT EXISTS ignorance_atlas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,       -- 'path', 'url', 'cmd', 'category', 'recipe'
            payload TEXT NOT NULL,         -- actual path/url/command/category/concept
            priority REAL DEFAULT 0.0,     -- calculated priority score
            status TEXT DEFAULT 'pending', -- 'pending', 'exploring', 'explored', 'failed'
            discovered_at REAL,            -- epoch timestamp
            explored_at REAL,              -- epoch timestamp
            explore_result TEXT,           -- what was learned (summary)
            explore_detail TEXT,           -- full exploration output (truncated)
            parent_item_id INTEGER,        -- what led to discovering this (chain)
            discovery_source TEXT,          -- 'fs_scan', 'man_see_also', 'related', 'category_gap', 'manual'
            chain_depth INTEGER DEFAULT 0, -- how deep in an exploration chain
            night_session INTEGER DEFAULT 0,-- which night session explored this
            FOREIGN KEY (parent_item_id) REFERENCES ignorance_atlas(id)
        );
    """

    def __init__(self):
        self._db_path = ATLAS_DB
        self._lock = threading.Lock()
        self._init_db()
        self._night_session = 0
        self._night_started: Optional[float] = None
        # Load explored items from DB so they persist across restarts
        self._explored_in_session: Set[str] = self._load_explored_from_db()

    def _init_db(self):
        ATLAS_DB.parent.mkdir(parents=True, exist_ok=True)
        with get_conn(self._db_path, timeout=10) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ignorance_atlas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    priority REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'pending',
                    discovered_at REAL,
                    explored_at REAL,
                    explore_result TEXT,
                    explore_detail TEXT,
                    parent_item_id INTEGER,
                    discovery_source TEXT,
                    chain_depth INTEGER DEFAULT 0,
                    night_session INTEGER DEFAULT 0
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_atlas_status_priority
                ON ignorance_atlas(status, priority DESC);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_atlas_type
                ON ignorance_atlas(item_type, status);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_atlas_payload
                ON ignorance_atlas(payload);
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS atlas_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

    def _load_explored_from_db(self) -> Set[str]:
        """Load all explored item payloads from the database so they survive restarts."""
        explored: Set[str] = set()
        try:
            with get_conn(self._db_path, timeout=5) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT payload FROM ignorance_atlas "
                    "WHERE status IN ('explored', 'failed')"
                ).fetchall()
                for row in rows:
                    explored.add(row[0])
            if explored:
                log.debug("IgnoranceAtlas: loaded %d explored items from DB", len(explored))
        except Exception as e:
            log.debug("_load_explored_from_db: %s", e)
        return explored

    def add_item(
        self,
        item_type: str,
        payload: str,
        priority: float = 0.0,
        parent_item_id: Optional[int] = None,
        discovery_source: str = "manual",
        chain_depth: int = 0,
    ) -> Optional[int]:
        """Add an unknown item to the atlas. Returns item ID, or None if duplicate.

        Checks both the in-memory explored set AND the database to avoid re-adding
        items that were already explored in a previous session.
        """
        with self._lock:
            # Fast path: check in-memory set (survives restarts via DB load)
            if payload in self._explored_in_session:
                return None
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    # Check duplicate in DB (any status — pending, exploring, explored, failed)
                    existing = conn.execute(
                        "SELECT id, status FROM ignorance_atlas WHERE payload = ? AND item_type = ?",
                        (payload, item_type)
                    ).fetchone()
                    if existing:
                        # If it was already explored/failed but not in our in-memory set, add it now
                        if existing[1] in ("explored", "failed"):
                            self._explored_in_session.add(payload)
                        return None

                    now = time.time()
                    c = conn.execute(
                        """INSERT INTO ignorance_atlas
                           (item_type, payload, priority, status, discovered_at,
                            parent_item_id, discovery_source, chain_depth, night_session)
                           VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
                        (item_type, payload, priority, now,
                         parent_item_id, discovery_source, chain_depth,
                         self._night_session)
                    )
                    return c.lastrowid
            except Exception as e:
                log.debug("add_item error: %s", e)
                return None

    def get_highest_priority(self) -> Optional[Dict[str, Any]]:
        """Return the highest-priority pending item, or None if atlas is empty."""
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    row = conn.execute(
                        """SELECT id, item_type, payload, priority, parent_item_id,
                                  discovery_source, chain_depth
                           FROM ignorance_atlas
                           WHERE status = 'pending'
                           ORDER BY priority DESC, discovered_at ASC
                           LIMIT 1"""
                    ).fetchone()
                    if row:
                        return {
                            "id": row[0],
                            "item_type": row[1],
                            "payload": row[2],
                            "priority": row[3],
                            "parent_item_id": row[4],
                            "discovery_source": row[5],
                            "chain_depth": row[6],
                        }
            except Exception as e:
                log.debug("get_highest_priority error: %s", e)
        return None

    def mark_exploring(self, item_id: int) -> None:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    conn.execute(
                        "UPDATE ignorance_atlas SET status='exploring' WHERE id=?",
                        (item_id,)
                    )
            except Exception as e:
                log.debug("mark_exploring error: %s", e)

    def mark_explored(
        self, item_id: int, result: str, detail: str = ""
    ) -> None:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    # Also fetch the payload so we can update the in-memory set
                    row = conn.execute(
                        "SELECT payload FROM ignorance_atlas WHERE id=?",
                        (item_id,)
                    ).fetchone()
                    conn.execute(
                        """UPDATE ignorance_atlas
                           SET status='explored', explored_at=?, explore_result=?,
                               explore_detail=?
                           WHERE id=?""",
                        (time.time(), result[:2000], detail[:5000], item_id)
                    )
                    if row:
                        self._explored_in_session.add(row[0])
            except Exception as e:
                log.debug("mark_explored error: %s", e)

    def mark_failed(self, item_id: int, error: str = "") -> None:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    row = conn.execute(
                        "SELECT payload FROM ignorance_atlas WHERE id=?",
                        (item_id,)
                    ).fetchone()
                    conn.execute(
                        """UPDATE ignorance_atlas
                           SET status='failed', explored_at=?, explore_result=?
                           WHERE id=?""",
                        (time.time(), f"FAILED: {error[:500]}", item_id)
                    )
                    if row:
                        self._explored_in_session.add(row[0])
            except Exception as e:
                log.debug("mark_failed error: %s", e)

    def update_priority(self, item_id: int, new_priority: float) -> None:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    conn.execute(
                        "UPDATE ignorance_atlas SET priority=? WHERE id=?",
                        (new_priority, item_id)
                    )
            except Exception as e:
                log.debug("update_priority error: %s", e)

    def count_pending(self) -> int:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM ignorance_atlas WHERE status='pending'"
                    ).fetchone()
                    return row[0] if row else 0
            except Exception:
                return 0

    def count_explored(self) -> int:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM ignorance_atlas WHERE status='explored'"
                    ).fetchone()
                    return row[0] if row else 0
            except Exception:
                return 0

    def get_recent_explored(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    rows = conn.execute(
                        """SELECT item_type, payload, explore_result, explored_at
                           FROM ignorance_atlas
                           WHERE status='explored'
                           ORDER BY explored_at DESC
                           LIMIT ?""",
                        (limit,)
                    ).fetchall()
                    return [
                        {
                            "item_type": r[0], "payload": r[1],
                            "explore_result": r[2], "explored_at": r[3],
                        }
                        for r in rows
                    ]
            except Exception:
                return []

    def get_chain(self, start_item_id: int, max_depth: int = 10) -> List[Dict[str, Any]]:
        """Follow a curiosity chain from parent to children."""
        chain = []
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    current_id = start_item_id
                    while current_id and len(chain) < max_depth:
                        row = conn.execute(
                            """SELECT id, item_type, payload, explore_result, parent_item_id
                               FROM ignorance_atlas WHERE id=?""",
                            (current_id,)
                        ).fetchone()
                        if not row:
                            break
                        chain.append({
                            "id": row[0], "item_type": row[1],
                            "payload": row[2], "explore_result": row[3],
                        })
                        # Get children of this node
                        children = conn.execute(
                            """SELECT id FROM ignorance_atlas
                               WHERE parent_item_id=? ORDER BY id""",
                            (current_id,)
                        ).fetchall()
                        if children:
                            current_id = children[0][0]
                        else:
                            break
            except Exception:
                pass
        return chain

    def get_night_stats(self, night_session: int) -> Dict[str, Any]:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    explored = conn.execute(
                        "SELECT COUNT(*) FROM ignorance_atlas WHERE night_session=? AND status='explored'",
                        (night_session,)
                    ).fetchone()
                    failed = conn.execute(
                        "SELECT COUNT(*) FROM ignorance_atlas WHERE night_session=? AND status='failed'",
                        (night_session,)
                    ).fetchone()
                    types = conn.execute(
                        """SELECT item_type, COUNT(*) FROM ignorance_atlas
                           WHERE night_session=? AND status='explored'
                           GROUP BY item_type""",
                        (night_session,)
                    ).fetchall()
                    return {
                        "night_session": night_session,
                        "explored": explored[0] if explored else 0,
                        "failed": failed[0] if failed else 0,
                        "by_type": {r[0]: r[1] for r in types},
                    }
            except Exception:
                return {}

    def start_night_session(self) -> int:
        self._night_session += 1
        self._night_started = time.time()
        log.info("Night Explorer session %d started", self._night_session)
        return self._night_session

    def end_night_session(self) -> Optional[float]:
        started = self._night_started
        self._night_started = None
        if started:
            duration = time.time() - started
            log.info("Night Explorer session %d ended (%.1f min)",
                     self._night_session, duration / 60)
            return duration
        return None

    @property
    def is_night_mode(self) -> bool:
        return self._night_started is not None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO atlas_meta VALUES (?, ?)",
                        (key, value)
                    )
            except Exception:
                pass

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            try:
                with get_conn(self._db_path, timeout=5) as conn:
                    row = conn.execute(
                        "SELECT value FROM atlas_meta WHERE key=?", (key,)
                    ).fetchone()
                    return row[0] if row else default
            except Exception:
                return default


# ── Discovery Scanners ────────────────────────────────────────────────────────

class DiscoveryScanners:
    """Methods that scan the system for new things EIDOS hasn't explored yet."""

    def __init__(self, atlas: IgnoranceAtlas):
        self.atlas = atlas
        self._known_paths: Set[str] = set()
        self._known_cmds: Set[str] = set()
        self._last_fs_scan: float = 0
        self._last_cmd_scan: float = 0
        self._fs_scan_interval = 1800   # rescan every 30 min
        self._cmd_scan_interval = 3600  # rescan every 60 min

    def scan_filesystem(self, force: bool = False) -> int:
        """Scan filesystem for directories not yet in the atlas.
        Returns number of new items added."""
        now = time.time()
        if not force and now - self._last_fs_scan < self._fs_scan_interval:
            return 0
        self._last_fs_scan = now

        added = 0
        for root_str in FS_EXPLORE_ROOTS:
            root = Path(root_str)
            if not root.exists():
                continue
            try:
                for entry in root.iterdir():
                    if entry.is_dir() and not entry.name.startswith('.'):
                        entry_str = str(entry)
                        if entry_str not in self._known_paths:
                            self._known_paths.add(entry_str)
                            # Priority based on directory nature
                            prio = self._calculate_path_priority(entry_str)
                            item_id = self.atlas.add_item(
                                "path", entry_str, priority=prio,
                                discovery_source="fs_scan"
                            )
                            if item_id:
                                added += 1
                            if added >= 30:  # cap per scan
                                return added
            except PermissionError:
                continue
            except Exception:
                continue
        return added

    def scan_commands(self, force: bool = False) -> int:
        """Scan for commands in PATH not yet in the atlas.
        Returns number of new items added."""
        now = time.time()
        if not force and now - self._last_cmd_scan < self._cmd_scan_interval:
            return 0
        self._last_cmd_scan = now

        added = 0
        try:
            # Use compgen on bash for efficient listing
            r = subprocess.run(
                ["bash", "-c", "compgen -c | sort -u | head -500"],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "LC_ALL": "C"}
            )
            if r.returncode == 0:
                for cmd in r.stdout.strip().split("\n"):
                    cmd = cmd.strip()
                    if not cmd or len(cmd) < 2:
                        continue
                    # Skip very common/trivial commands
                    if cmd in ("ls", "cd", "pwd", "echo", "cat", "true", "false",
                               "[", ":", ".", "test", "printf"):
                        continue
                    if cmd not in self._known_cmds:
                        self._known_cmds.add(cmd)
                        # Priority: man exists = higher curiosity value
                        prio = 5.0
                        try:
                            man_check = subprocess.run(
                                ["man", "-w", cmd],
                                capture_output=True, timeout=2
                            )
                            if man_check.returncode == 0:
                                prio = 7.0
                        except Exception:
                            pass
                        self.atlas.add_item(
                            "cmd", cmd, priority=prio,
                            discovery_source="cmd_scan"
                        )
                        added += 1
                    if added >= 50:
                        break
        except Exception as e:
            log.debug("scan_commands error: %s", e)
        return added

    def scan_thin_categories(self) -> int:
        """Scan brain DB for categories with fewer than THIN_CATEGORY_THRESHOLD nodes.
        Returns number of new items added."""
        added = 0
        try:
            with get_conn(BRAIN_DB, timeout=10) as conn:
                thin = conn.execute(
                    """SELECT category, COUNT(*) as cnt
                       FROM knowledge_nodes
                       WHERE category != '' AND category NOT IN ('generic', 'unknown')
                       GROUP BY category
                       HAVING cnt < ?
                       ORDER BY cnt ASC
                       LIMIT 10""",
                    (THIN_CATEGORY_THRESHOLD,)
                ).fetchall()
                for row in thin:
                    cat, cnt = row
                    # Check if already in atlas
                    if not self._is_in_atlas("category", cat):
                        prio = 8.0 - cnt  # fewer nodes = higher priority
                        self.atlas.add_item(
                            "category", cat, priority=prio,
                            discovery_source="category_gap"
                        )
                        added += 1
        except Exception as e:
            log.debug("scan_thin_categories error: %s", e)
        return added

    def scan_procedural_gaps(self) -> int:
        """Scan for concepts that have knowledge nodes but no procedural recipes.
        Returns number of new items added."""
        added = 0
        try:
            from core.eidos_procedural import discover_procedural_gaps
            gaps = discover_procedural_gaps()
            for gap in gaps[:10]:
                concept = gap.get("concept", "")
                if concept and not self._is_in_atlas("recipe", concept):
                    prio = 9.0 if gap.get("priority") == "high" else 6.0
                    self.atlas.add_item(
                        "recipe", concept, priority=prio,
                        discovery_source="procedural_gap"
                    )
                    added += 1
        except Exception as e:
            log.debug("scan_procedural_gaps error: %s", e)
        return added

    def scan_urls_from_brain(self) -> int:
        """Scan brain for concepts that could have associated URLs to explore."""
        added = 0
        try:
            with get_conn(BRAIN_DB, timeout=10) as conn:
                # Find well-known concepts without URL exploration entries
                concepts = conn.execute(
                    """SELECT DISTINCT concept FROM knowledge_nodes
                       WHERE confidence > 0.5 AND LENGTH(definition) > 50
                       AND category IN ('tool', 'protocol', 'security_tool',
                                        'framework', 'programming_language')
                       ORDER BY usage_count DESC
                       LIMIT 20"""
                ).fetchall()
                for row in concepts:
                    concept = row[0]
                    url_topic = concept.replace(" ", "_")
                    for url_type, url_template in URL_PATTERNS.items():
                        url = url_template.format(cmd=url_topic, topic=url_topic)
                        if not self._is_in_atlas("url", url):
                            prio = 5.0
                            self.atlas.add_item(
                                "url", url, priority=prio,
                                discovery_source="brain_url"
                            )
                            added += 1
                            break  # one URL per concept
                    if added >= 15:
                        break
        except Exception as e:
            log.debug("scan_urls_from_brain error: %s", e)
        return added

    def _is_in_atlas(self, item_type: str, payload: str) -> bool:
        try:
            with get_conn(self.atlas._db_path, timeout=5) as conn:
                row = conn.execute(
                    "SELECT 1 FROM ignorance_atlas WHERE item_type=? AND payload=?",
                    (item_type, payload)
                ).fetchone()
                return row is not None
        except Exception:
            return False

    def _calculate_path_priority(self, path: str) -> float:
        """Calculate priority for a filesystem path based on its nature."""
        prio = 3.0  # base
        p = path.lower()
        if "doc" in p or "man" in p:
            prio += 3.0
        if "bin" in p or "lib" in p:
            prio += 2.0
        if "config" in p or "etc" in p:
            prio += 2.0
        if "log" in p:
            prio += 1.0
        if "src" in p or "source" in p:
            prio += 2.0
        return prio

    def scan_all(self, force: bool = False) -> Dict[str, int]:
        """Run all scanners. Returns counts by scanner type."""
        return {
            "filesystem": self.scan_filesystem(force=force),
            "commands": self.scan_commands(force=force),
            "thin_categories": self.scan_thin_categories(),
            "procedural_gaps": self.scan_procedural_gaps(),
            "urls": self.scan_urls_from_brain(),
        }


# ── Explorers ────────────────────────────────────────────────────────────────

class CuriosityExplorer:
    """Executes the actual exploration of unknown items."""

    def __init__(self, atlas: IgnoranceAtlas):
        self.atlas = atlas

    def explore(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Explore a single unknown item.
        Returns {ok, result, detail, follow_ups: [...]}"""
        item_type = item["item_type"]
        payload = item["payload"]
        item_id = item["id"]

        self.atlas.mark_exploring(item_id)

        if item_type == "path":
            outcome = self._explore_path(payload, item_id)
        elif item_type == "cmd":
            outcome = self._explore_command(payload, item_id)
        elif item_type == "url":
            outcome = self._explore_url(payload, item_id)
        elif item_type == "category":
            outcome = self._explore_category(payload, item_id)
        elif item_type == "recipe":
            outcome = self._explore_recipe(payload, item_id)
        else:
            outcome = {"ok": False, "result": f"Unknown item_type: {item_type}", "detail": "", "follow_ups": []}

        if outcome["ok"]:
            self.atlas.mark_explored(item_id, outcome["result"], outcome.get("detail", ""))
        else:
            self.atlas.mark_failed(item_id, outcome.get("result", "Unknown error"))

        return outcome

    # ── Path exploration ──────────────────────────────────────────────────

    def _explore_path(self, path: str, parent_id: int) -> Dict[str, Any]:
        """Explore a filesystem path: list contents, read interesting files."""
        result = {"ok": False, "result": "", "detail": "", "follow_ups": []}
        try:
            p = Path(path)
            if not p.exists():
                result["result"] = f"Path no longer exists: {path}"
                return result

            # Check read safety
            if not self._is_path_safe(p):
                result["result"] = f"Path blocked by safety: {path}"
                return result

            # List directory contents
            entries = []
            dirs_found = []
            files_found = []
            try:
                for entry in sorted(p.iterdir())[:MAX_DIR_ENTRIES]:
                    try:
                        st = entry.stat()
                        etype = "dir" if entry.is_dir() else "file"
                        if entry.is_symlink():
                            etype = "link"
                        entries.append({
                            "name": entry.name,
                            "type": etype,
                            "size": st.st_size,
                        })
                        if entry.is_dir() and not entry.name.startswith('.'):
                            dirs_found.append(str(entry))
                        elif entry.is_file() and not entry.name.startswith('.'):
                            files_found.append(entry)
                    except (PermissionError, OSError):
                        continue
            except PermissionError:
                result["result"] = f"Permission denied: {path}"
                return result

            # Summarize
            dir_count = sum(1 for e in entries if e["type"] == "dir")
            file_count = sum(1 for e in entries if e["type"] == "file")
            total_size = sum(e["size"] for e in entries)
            summary = (f"Explored directory '{path}': "
                       f"{dir_count} dirs, {file_count} files, "
                       f"{len(entries)} entries total, "
                       f"{total_size / 1024:.1f} KB")

            # Read one interesting file (README, index, .py, .sh, .txt)
            interesting_exts = {".py", ".sh", ".txt", ".md", ".rst", ".cfg",
                               ".toml", ".yml", ".yaml", ".json", ".conf"}
            read_file_detail = ""
            for f in files_found:
                suffix = f.suffix.lower()
                name_lower = f.name.lower()
                if (suffix in interesting_exts or
                    name_lower in ("readme", "makefile", "dockerfile", "license")):
                    try:
                        content = f.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_READ_BYTES]
                        read_file_detail = content[:1000]
                        summary += f"\nRead file '{f.name}' ({len(content)} bytes)"
                        break  # just one file per exploration
                    except Exception:
                        continue

            result["ok"] = True
            result["result"] = summary
            result["detail"] = read_file_detail

            # Generate follow-up items: subdirectories
            for d in dirs_found[:5]:
                result["follow_ups"].append({
                    "item_type": "path", "payload": d,
                    "priority": 4.0, "discovery_source": "subdirectory",
                })

            # Generate follow-up: any referenced commands or scripts in the file
            if read_file_detail:
                cmds = self._extract_referenced_commands(read_file_detail)
                for cmd in cmds[:3]:
                    result["follow_ups"].append({
                        "item_type": "cmd", "payload": cmd,
                        "priority": 5.0, "discovery_source": "file_reference",
                    })

        except Exception as e:
            result["result"] = f"Error exploring path {path}: {e}"
            log.debug("_explore_path error: %s", e)

        return result

    def _extract_referenced_commands(self, text: str) -> List[str]:
        """Extract command names referenced in text content."""
        cmd_pattern = re.findall(
            r'(?:^|\s)([a-z][a-z0-9_-]{2,20})(?:\s|$|,)', text.lower()
        )
        # Filter to likely commands
        likely = []
        for c in set(cmd_pattern):
            if c in self._known_commands():
                likely.append(c)
                if len(likely) >= 5:
                    break
        if not likely:
            likely = list(set(cmd_pattern))[:3]
        return likely

    def _known_commands(self) -> Set[str]:
        """Cache of known system commands."""
        try:
            r = subprocess.run(
                ["bash", "-c", "compgen -c | head -500"],
                capture_output=True, text=True, timeout=5,
                env={**os.environ, "LC_ALL": "C"}
            )
            return set(r.stdout.strip().split("\n"))
        except Exception:
            return set()

    def _is_path_safe(self, path: Path) -> bool:
        """Check if a path is safe to read."""
        path_str = str(path.resolve())
        for pattern in READ_BLOCKED_PATTERNS:
            if pattern.replace("*", "") in path_str:
                return False
        return True

    # ── Command exploration ───────────────────────────────────────────────

    def _explore_command(self, cmd: str, parent_id: int) -> Dict[str, Any]:
        """Explore a command: read its man page or --help."""
        result = {"ok": False, "result": "", "detail": "", "follow_ups": []}
        try:
            findings = []

            # Try man page first
            for template, source_name in SAFE_EXPLORE_CMDS:
                try:
                    cmd_str = template.format(cmd=cmd)
                    if " " in cmd_str:
                        parts = cmd_str.split(" ")
                    else:
                        parts = [cmd_str]
                    # For man: man -P cat <cmd>
                    if "man" in template:
                        r = subprocess.run(
                            ["man", "-P", "cat", cmd],
                            capture_output=True, text=True, timeout=8,
                            env={**os.environ, "MANPAGER": "cat", "MANWIDTH": "80"}
                        )
                    else:
                        r = subprocess.run(
                            cmd_str, shell=True,
                            capture_output=True, text=True, timeout=5,
                            env={**os.environ, "LC_ALL": "C"}
                        )
                    if r.returncode in (0, 1) and len(r.stdout) > 30:
                        output = r.stdout[:2000]
                        findings.append(f"[{source_name}]\n{output}")
                        if source_name == "man_page" and len(r.stdout) > 500:
                            break  # man page is good enough, stop
                except Exception:
                    continue

            if findings:
                combined = "\n\n".join(findings)
                # Extract SEE ALSO references from man page
                see_also = self._extract_see_also(combined)
                for ref in see_also[:5]:
                    result["follow_ups"].append({
                        "item_type": "cmd", "payload": ref,
                        "priority": 6.0, "discovery_source": "man_see_also",
                    })
                result["ok"] = True
                result["result"] = f"Explored command '{cmd}': {findings[0][:200]}"
                result["detail"] = combined
            else:
                result["result"] = f"No documentation found for command '{cmd}'"
                result["ok"] = True  # still "ok" - we explored, just found nothing

        except Exception as e:
            result["result"] = f"Error exploring command '{cmd}': {e}"
            log.debug("_explore_command error: %s", e)

        return result

    def _extract_see_also(self, man_text: str) -> List[str]:
        """Extract SEE ALSO references from a man page."""
        see_also_section = re.search(
            r'SEE\s+ALSO(.*?)(?:^[A-Z]{2,}|^[A-Z][A-Z\s]{5,}|\Z)',
            man_text, re.DOTALL | re.MULTILINE
        )
        if not see_also_section:
            return []
        section_text = see_also_section.group(1)
        # Extract command names: usually in format cmd(1), cmd(8), etc.
        refs = re.findall(r'\b([a-z][a-z0-9_-]{2,20})\(\d\)', section_text)
        # Also plain command names
        plain = re.findall(r'\b([a-z][a-z0-9_-]{2,20})\b', section_text)
        return list(dict.fromkeys(refs + plain))[:8]

    # ── URL exploration ───────────────────────────────────────────────────

    def _explore_url(self, url: str, parent_id: int) -> Dict[str, Any]:
        """Fetch and read a URL using eidos_crawler (robots.txt, clean extraction, link discovery)."""
        result = {"ok": False, "result": "", "detail": "", "follow_ups": []}
        try:
            from core.eidos_crawler import crawl_from_curiosity
            crawl_result = crawl_from_curiosity(url, parent_item_id=parent_id)
            result["ok"] = crawl_result["ok"]
            result["result"] = crawl_result["result"]
            result["detail"] = crawl_result["detail"]
            result["follow_ups"] = crawl_result["follow_ups"]
        except ImportError:
            log.warning("eidos_crawler not available, falling back to basic URL fetch")
            result = self._explore_url_basic(url, parent_id)
        except Exception as e:
            result["result"] = f"Error fetching URL {url}: {e}"
            log.debug("_explore_url error: %s", e)
        return result

    def _explore_url_basic(self, url: str, parent_id: int) -> Dict[str, Any]:
        """Fallback URL fetch when crawler is unavailable."""
        result = {"ok": False, "result": "", "detail": "", "follow_ups": []}
        try:
            import urllib.request
            req = urllib.request.Request(
                url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) "
                                  "Gecko/20100101 Firefox/140.0"
                }
            )
            resp = urllib.request.urlopen(req, timeout=15)
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                result["result"] = f"URL {url}: not text content ({content_type})"
                result["ok"] = True
                return result

            raw = resp.read(MAX_FILE_READ_BYTES * 2).decode("utf-8", errors="replace")
            resp.close()

            text = raw
            if "<" in text:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(text, "html.parser")
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    text = soup.get_text(" ", strip=True)
                except Exception:
                    text = re.sub(r'<[^>]+>', ' ', text)

            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) < 50:
                result["result"] = f"URL {url}: insufficient text content"
                result["ok"] = True
                return result

            preview = text[:1500]
            result["ok"] = True
            result["result"] = f"Fetched URL '{url}': {len(text)} chars of text. {preview[:200]}..."
            result["detail"] = preview

            terms = re.findall(r'\b([a-z][a-z0-9_-]{4,20})\b', text.lower()[:5000])
            term_counts = {}
            for t in terms:
                if t not in ("the", "this", "that", "with", "from", "have", "will",
                             "your", "which", "their", "there", "about", "would"):
                    term_counts[t] = term_counts.get(t, 0) + 1
            top_terms = sorted(term_counts.items(), key=lambda x: -x[1])[:5]
            for term, _ in top_terms:
                man_url = URL_PATTERNS["man_page"].format(cmd=term)
                result["follow_ups"].append({
                    "item_type": "url", "payload": man_url,
                    "priority": 4.0, "discovery_source": "url_content",
                })

        except Exception as e:
            result["result"] = f"Error fetching URL {url}: {e}"
            log.debug("_explore_url_basic error: %s", e)

        return result

    # ── Category exploration ──────────────────────────────────────────────

    def _explore_category(self, category: str, parent_id: int) -> Dict[str, Any]:
        """Explore a thin knowledge category: find and research key concepts."""
        result = {"ok": False, "result": "", "detail": "", "follow_ups": []}
        try:
            with get_conn(BRAIN_DB, timeout=10) as conn:
                existing = conn.execute(
                    "SELECT concept FROM knowledge_nodes WHERE category=? LIMIT 5",
                    (category,)
                ).fetchall()
                existing_concepts = [r[0] for r in existing]

            # Try to find related concepts from man pages
            try:
                r = subprocess.run(
                    ["man", "-k", category],
                    capture_output=True, text=True, timeout=10,
                    env={**os.environ, "LC_ALL": "C"}
                )
                if r.returncode == 0:
                    man_results = r.stdout.strip().split("\n")[:10]
                    new_cmds = []
                    for line in man_results:
                        cmd_match = re.match(r'^(\S+)', line)
                        if cmd_match:
                            cmd = cmd_match.group(1).rstrip(')').split('(')[0]
                            if cmd not in existing_concepts:
                                new_cmds.append(cmd)
                    for cmd in new_cmds[:5]:
                        result["follow_ups"].append({
                            "item_type": "cmd", "payload": cmd,
                            "priority": 7.0,
                            "discovery_source": "category_research",
                        })

                result["ok"] = True
                result["result"] = (f"Explored category '{category}': "
                                   f"{len(existing_concepts)} existing concepts, "
                                   f"{len(new_cmds) if 'new_cmds' in dir() else 0} "
                                   f"new commands found via man -k")
            except Exception:
                result["ok"] = True
                result["result"] = f"Category '{category}' explored (no man -k results)"

        except Exception as e:
            result["result"] = f"Error exploring category '{category}': {e}"
            log.debug("_explore_category error: %s", e)

        return result

    # ── Recipe exploration ────────────────────────────────────────────────

    def _explore_recipe(self, concept: str, parent_id: int) -> Dict[str, Any]:
        """Explore a procedural gap: learn how to do something with a concept."""
        result = {"ok": False, "result": "", "detail": "", "follow_ups": []}
        try:
            # Try to get a procedure/recipe for the concept
            # First: check if it's a command we can get a man page for
            r = subprocess.run(
                ["man", "-w", concept], capture_output=True, timeout=3
            )
            if r.returncode == 0:
                r2 = subprocess.run(
                    ["man", "-P", "cat", concept],
                    capture_output=True, text=True, timeout=8,
                    env={**os.environ, "MANPAGER": "cat"}
                )
                if r2.returncode == 0:
                    # Extract EXAMPLES section
                    examples = self._extract_examples(r2.stdout)
                    if examples:
                        result["ok"] = True
                        result["result"] = (f"Found procedural recipe for '{concept}': "
                                           f"EXAMPLES section from man page")
                        result["detail"] = examples
                        return result
                    # Extract SYNOPSIS as a minimal recipe
                    synopsis = self._extract_synopsis(r2.stdout)
                    if synopsis:
                        result["ok"] = True
                        result["result"] = f"Found SYNOPSIS for '{concept}' from man page"
                        result["detail"] = synopsis
                        return result

            # Second: try to learn via Ollama/cloud
            try:
                from core.eidos_learn import ask_llm
                answer, source = ask_llm(
                    f"Give me a practical step-by-step recipe for using '{concept}'. "
                    f"Include: what it's for, common options, and 2-3 concrete examples "
                    f"with real commands. Keep it under 500 words.",
                    timeout=60
                )
                if answer and len(answer) > 40:
                    result["ok"] = True
                    result["result"] = f"Generated recipe for '{concept}' via {source}"
                    result["detail"] = answer
                    return result
            except Exception:
                pass

            result["ok"] = True
            result["result"] = f"No recipe found for '{concept}' yet"

        except Exception as e:
            result["result"] = f"Error exploring recipe '{concept}': {e}"
            log.debug("_explore_recipe error: %s", e)

        return result

    def _extract_examples(self, man_text: str) -> str:
        """Extract EXAMPLES section from a man page."""
        m = re.search(
            r'EXAMPLES?\n(.*?)(?:^[A-Z]{2,}|^[A-Z][A-Z\s]{5,}|\Z)',
            man_text, re.DOTALL | re.MULTILINE
        )
        return m.group(1).strip()[:2000] if m else ""

    def _extract_synopsis(self, man_text: str) -> str:
        """Extract SYNOPSIS section from a man page."""
        m = re.search(
            r'SYNOPSIS\n(.*?)(?:^DESCRIPTION|^[A-Z]{2,}|^[A-Z][A-Z\s]{5,}|\Z)',
            man_text, re.DOTALL | re.MULTILINE
        )
        return m.group(1).strip()[:1500] if m else ""


# ── Morning Briefing Synthesizer ─────────────────────────────────────────────

class MorningBriefing:
    """Synthesizes a briefing of what EIDOS explored overnight.
    Combines exploration data from IgnoranceAtlas with mastery data
    from mastery_goals.db (Study-Practice-Verify results)."""

    def __init__(self, atlas: IgnoranceAtlas):
        self.atlas = atlas

    def synthesize(self, night_session: Optional[int] = None,
                   mastery_results: Optional[List[Dict[str, Any]]] = None) -> str:
        """Generate a morning briefing of overnight exploration + mastery.

        Args:
            night_session: Optional night session number for filtering
            mastery_results: Optional list of SPV results from night mastery run

        Returns markdown-formatted briefing string.
        """
        explored = self.atlas.get_recent_explored(limit=50)

        # ── Try to get mastery briefing from the DB ───────────────────────
        mastery_section = ""
        try:
            from core.eidos_mastery import get_mastery_tracker
            tracker = get_mastery_tracker()
            mastery_section = tracker.generate_morning_briefing()
        except Exception as e:
            log.debug("Could not generate mastery briefing: %s", e)

        lines = []
        lines.append("# EIDOS Morning Briefing")
        lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        # ── Night Exploration Summary ─────────────────────────────────────
        if explored:
            lines.append(f"Total items explored: {len(explored)}")

            # Group by type
            by_type: Dict[str, List[Dict]] = {}
            for item in explored:
                t = item["item_type"]
                if t not in by_type:
                    by_type[t] = []
                by_type[t].append(item)

            lines.append("")
            lines.append("## Exploration Summary")
            for t, items in sorted(by_type.items()):
                lines.append(f"- **{t}s**: {len(items)} explored")
            lines.append("")

            # Key findings (pick the most interesting ones)
            lines.append("## Key Discoveries")
            count = 0
            for item in explored:
                if item["explore_result"] and "FAILED" not in item["explore_result"]:
                    payload = item["payload"]
                    result = item["explore_result"][:300]
                    lines.append(f"### {item['item_type'].title()}: `{payload}`")
                    lines.append(f"{result}")
                    lines.append("")
                    count += 1
                    if count >= 15:
                        break
        else:
            lines.append("No exploration this session.")
            lines.append("")

        # ── Atlas stats ───────────────────────────────────────────────────
        pending = self.atlas.count_pending()
        lines.append("## Atlas Status")
        lines.append(f"- Total explored ever: {self.atlas.count_explored()}")
        lines.append(f"- Pending in queue: {pending}")
        lines.append("")

        # ── Night Mastery SPV Results (in-memory, if any) ─────────────────
        if mastery_results:
            # Separate curriculum vs practice results
            curriculum_results = [r for r in mastery_results
                                 if r.get("type") != "practice"]
            practice_results_list = [r for r in mastery_results
                                    if r.get("type") == "practice"]

            lines.append("## Night Mastery Results (this session)")

            # Curriculum (SPV) results
            if curriculum_results:
                passed = sum(1 for r in curriculum_results if r.get("passed"))
                failed = len(curriculum_results) - passed
                lines.append(f"### Curriculum SPV")
                lines.append(f"- **{len(curriculum_results)}** milestones attempted")
                lines.append(f"- **{passed}** passed, **{failed}** failed")
                lines.append("")
                for r in curriculum_results:
                    icon = "+" if r.get("passed") else "X"
                    lines.append(f"- `[{icon}]` **{r.get('domain', '?')}**: "
                               f"{r.get('milestone', '?')} "
                               f"(score={r.get('score', 0):.0f}, {r.get('attempts', 0)} attempts)")

                if failed > 0:
                    lines.append("")
                    lines.append("#### Needs Attention (Curriculum)")
                    for r in curriculum_results:
                        if not r.get("passed"):
                            lines.append(f"- **{r.get('domain')}/{r.get('milestone')}**: "
                                       f"failed after {r.get('attempts', 0)} attempts. "
                                       f"Consider different approach.")
                lines.append("")

            # Practice (active recall) results
            if practice_results_list:
                p_passed = sum(1 for r in practice_results_list if r.get("passed"))
                p_failed = len(practice_results_list) - p_passed
                p_advanced = sum(1 for r in practice_results_list
                                if r.get("mastery_advanced"))
                avg_score = (sum(r.get("score", 0) for r in practice_results_list) /
                            max(len(practice_results_list), 1))
                lines.append(f"### Practice Queue (Active Recall)")
                lines.append(f"- **{len(practice_results_list)}** concepts practiced")
                lines.append(f"- **{p_passed}** passed, **{p_failed}** failed")
                lines.append(f"- Average recall score: **{avg_score:.3f}**")
                if p_advanced > 0:
                    lines.append(f"- **{p_advanced}** concepts advanced to next mastery level!")
                lines.append("")
                # Show top and bottom performers
                sorted_practice = sorted(practice_results_list,
                                        key=lambda r: r.get("score", 0), reverse=True)
                lines.append("#### Best Recall")
                for r in sorted_practice[:5]:
                    icon = "+" if r.get("passed") else "X"
                    advanced_mark = " (ADVANCED!)" if r.get("mastery_advanced") else ""
                    lines.append(f"- `[{icon}]` **{r.get('concept', '?')[:45]}** "
                               f"score={r.get('score', 0):.3f} "
                               f"streak=+{r.get('streak_correct', 0)}/"
                               f"-{r.get('streak_wrong', 0)}{advanced_mark}")
                if len(sorted_practice) > 5:
                    lines.append(f"  _(+ {len(sorted_practice) - 5} more)_")
                # Show weakest
                weakest = [r for r in sorted_practice if r.get("score", 0) < 0.5]
                if weakest:
                    lines.append("")
                    lines.append("#### Needs Review (score < 0.5)")
                    for r in weakest[:5]:
                        lines.append(f"- **{r.get('concept', '?')[:45]}** "
                                   f"score={r.get('score', 0):.3f} "
                                   f"-- {r.get('feedback', '')[:80]}")
                lines.append("")

        # ── Full Mastery Briefing from DB ──────────────────────────────────
        if mastery_section.strip():
            # Strip the title header from the mastery briefing (it's already in this doc)
            mastery_lines = mastery_section.split("\n")
            # Skip first 3 lines (title, date, blank) since we have our own header
            if len(mastery_lines) > 3:
                lines.append("---")
                lines.append("")
                lines.extend(mastery_lines[3:])
        elif not mastery_results:
            lines.append("## Mastery Status")
            lines.append("No mastery data available yet. Create goals with: `python3 core/eidos_mastery.py create <domain>`")
            lines.append("")

        return "\n".join(lines)

    def write_briefing(self, night_session: Optional[int] = None,
                       mastery_results: Optional[List[Dict[str, Any]]] = None) -> Path:
        """Write the briefing to ~/.eidos/morning_briefing.md."""
        content = self.synthesize(night_session, mastery_results=mastery_results)
        BRIEFING_LOG.parent.mkdir(parents=True, exist_ok=True)
        BRIEFING_LOG.write_text(content, encoding="utf-8")
        return BRIEFING_LOG


# ── Curiosity Engine (Main Controller) ───────────────────────────────────────

class CuriosityEngine:
    """Main controller that orchestrates the autonomous curiosity cycle.

    Connects: IgnoranceAtlas -> DiscoveryScanners -> CuriosityExplorer
    Manages: idle/night modes, chain following, integration with AliveOrchestrator
    """

    def __init__(self):
        self.atlas = IgnoranceAtlas()
        self.scanners = DiscoveryScanners(self.atlas)
        self.explorer = CuriosityExplorer(self.atlas)
        self.briefing = MorningBriefing(self.atlas)

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._tick_count = 0
        self._explore_count = 0
        self._night_mode = False
        self._last_tick = 0.0
        self._idle_since: Optional[float] = None

        # Integration: reference to orchestrator (set after init)
        self._orchestrator = None

        # Night mastery: tick counter to alternate between exploration and SPV
        self._night_mastery_tick = 0
        self._night_mastery_results: List[Dict[str, Any]] = []
        # Practice queue: run once per night session after SPV starts
        self._practice_ran_this_night: bool = False
        self._practice_night_results: List[Dict[str, Any]] = []

    # ── API ───────────────────────────────────────────────────────────────

    def tick(self, force_night: bool = False) -> Dict[str, Any]:
        """Single exploration cycle.
        1. Scan for new unknown items
        2. Pick highest priority
        3. Explore it
        4. Record result
        5. Generate follow-ups
        6. Update Atlas

        During night mode: alternate between curiosity exploration and
        mastery SPV loops on pending curriculum milestones (80% SPV / 20% explore).

        Returns dict with tick result summary."""
        self._tick_count += 1
        self._last_tick = time.time()
        result = {
            "tick": self._tick_count,
            "ok": False,
            "item_type": "",
            "payload": "",
            "explored": False,
            "follow_ups": 0,
            "night_mode": self._night_mode,
        }

        # Step 0: Check night mode state
        self._check_night_mode()

        # ── Night Mode: Mastery SPV Loop (80% of ticks) ───────────────────
        if self._night_mode:
            self._night_mastery_tick += 1
            # Run SPV on every tick except every 5th (20% exploration)
            if self._night_mastery_tick % 5 != 0:
                mastery_result = self._run_night_mastery_tick()
                if mastery_result is not None:
                    # Blend into result
                    result["ok"] = True
                    result["item_type"] = "mastery_spv"
                    result["payload"] = mastery_result.get("milestone", "")
                    result["explored"] = mastery_result.get("passed", False)
                    result["result_preview"] = (
                        f"Night SPV: {mastery_result.get('domain', '')}/{mastery_result.get('milestone', '')} "
                        f"= {'PASS' if mastery_result.get('passed') else 'FAIL'} "
                        f"(score {mastery_result.get('score', 0):.0f})"
                    )
                    result["mastery_spv"] = mastery_result
                    result["follow_ups"] = 0

                    # ── Trigger practice batch after first SPV milestone ──
                    if not self._practice_ran_this_night:
                        log.info("Night cycle: triggering practice batch "
                                 "(first SPV milestone completed)")
                        self._practice_ran_this_night = True
                        try:
                            practice_results = self._run_night_practice_batch(
                                max_items=50
                            )
                            result["practice_batch"] = {
                                "items_processed": len(practice_results),
                                "passed": sum(1 for r in practice_results
                                             if r.get("passed")),
                            }
                            # Append to night mastery results for briefing
                            self._night_mastery_results.extend(practice_results)
                        except Exception as e:
                            log.error("Night practice batch failed: %s", e)

                    return result
                # If no pending milestones, fall through to exploration

            # ── Periodic practice: if practice hasn't run yet (e.g. no SPV milestones),
            #     run it on tick 3 of night mode regardless ──
            if not self._practice_ran_this_night and self._night_mastery_tick == 3:
                log.info("Night cycle: triggering practice batch (fallback, no SPV milestones)")
                self._practice_ran_this_night = True
                try:
                    practice_results = self._run_night_practice_batch(max_items=50)
                    self._night_mastery_results.extend(practice_results)
                except Exception as e:
                    log.error("Night practice batch fallback failed: %s", e)

        # Step 1: Scan for new unknowns (throttled internally)
        scan_results = self.scanners.scan_all(force=self._night_mode)
        total_scanned = sum(scan_results.values())
        if total_scanned > 0:
            log.debug("Scanners found %d new items: %s", total_scanned, scan_results)

        # Step 2: Pick highest priority item
        item = self.atlas.get_highest_priority()
        if not item:
            result["reason"] = "atlas_empty"

            # During night mode, if atlas is empty, try mastery
            if self._night_mode:
                mastery_result = self._run_night_mastery_tick()
                if mastery_result is not None:
                    result["ok"] = True
                    result["item_type"] = "mastery_spv"
                    result["payload"] = mastery_result.get("milestone", "")
                    result["mastery_spv"] = mastery_result
                    result["result_preview"] = (
                        f"Night SPV (fallback): {mastery_result.get('domain', '')}/{mastery_result.get('milestone', '')}"
                    )
            return result

        result["item_type"] = item["item_type"]
        result["payload"] = item["payload"]
        result["priority"] = item["priority"]

        # Step 3: Explore
        outcome = self.explorer.explore(item)
        result["ok"] = outcome["ok"]
        result["explored"] = outcome["ok"]
        result["result_preview"] = outcome.get("result", "")[:200]

        if outcome["ok"]:
            self._explore_count += 1

        # Step 4: Generate follow-up items
        follow_ups = outcome.get("follow_ups", [])
        parent_id = item["id"]
        chain_depth = item.get("chain_depth", 0) + 1
        for fu in follow_ups[:3]:  # max 3 follow-ups
            self.atlas.add_item(
                fu["item_type"], fu["payload"],
                priority=fu.get("priority", 3.0),
                parent_item_id=parent_id,
                discovery_source=fu.get("discovery_source", "follow_up"),
                chain_depth=chain_depth,
            )
        result["follow_ups"] = len(follow_ups)

        # Log the exploration
        if outcome["ok"]:
            log.info("CuriosityEngine tick %d: explored %s '%s' -> %s",
                     self._tick_count, item["item_type"], item["payload"],
                     outcome.get("result", "")[:100])

        # Record in stream if orchestrator is attached
        if self._orchestrator and hasattr(self._orchestrator, "_stream"):
            self._orchestrator._stream(
                "curiosity",
                f"Explored {item['item_type']} '{item['payload']}': "
                f"{outcome.get('result', '?')[:120]}",
                extra={"tick": self._tick_count, "night": self._night_mode}
            )

        return result

    def _run_night_mastery_tick(self) -> Optional[Dict[str, Any]]:
        """Run a single SPV loop on the next pending milestone.
        Called during night mode ticks.

        Returns: {domain, milestone, step, passed, score, attempts, ...} or None
        """
        try:
            from core.eidos_mastery import (
                get_mastery_tracker, get_spv_loop, get_curriculum_generator
            )
            tracker = get_mastery_tracker()

            # Ensure goals exist for all known domains (lazy init)
            gen = get_curriculum_generator()
            existing_domains = {g["domain"] for g in tracker.list_goals()
                               if g["status"] in ("active", "in_progress")}
            for domain in gen.list_known_domains():
                if domain not in existing_domains:
                    try:
                        tracker.create_goal(domain)
                        log.info("Night mode: auto-created goal for %s", domain)
                    except Exception:
                        pass

            pending = tracker.get_pending_milestones()
            if not pending:
                return None

            # Pick the first pending milestone
            pm = pending[0]
            spv = get_spv_loop()

            spv_result = spv.run_loop(
                {
                    "id": pm["id"],
                    "goal_id": pm["goal_id"],
                    "material": pm["material"],
                    "practice_exercise": pm["practice_exercise"],
                    "verification_test": pm["verification_test"],
                    "label": pm["label"],
                },
                goal_id=pm["goal_id"],
                concept=pm["domain"],
            )

            # Complete the milestone
            tracker.complete_milestone(
                pm["goal_id"], pm["id"],
                learned=(
                    f"Night SPV: Score={spv_result['score']}, "
                    f"Attempts={spv_result['attempts']}"
                ),
                spv_result=spv_result,
            )

            mastery_result = {
                "domain": pm["domain"],
                "milestone": pm["label"],
                "step": pm["step"],
                "passed": spv_result["passed"],
                "score": spv_result["score"],
                "attempts": spv_result["attempts"],
                "duration_seconds": spv_result.get("duration_seconds", 0),
            }
            self._night_mastery_results.append(mastery_result)

            log.info("Night SPV [%s]: %s/%s = %s (score %.0f, %d attempts)",
                     "PASS" if spv_result["passed"] else "FAIL",
                     pm["domain"], pm["label"],
                     "COMPLETED" if spv_result["passed"] else "NEEDS_RETRY",
                     spv_result["score"], spv_result["attempts"])

            return mastery_result

        except Exception as e:
            log.debug("Night mastery tick error: %s", e)
            return None

    def _run_night_practice_batch(self, max_items: int = 50) -> List[Dict[str, Any]]:
        """Run a batch of practice queue items during night mode.

        Queries practice_queue for due items, generates active recall prompts,
        calls the LLM to answer FROM MEMORY, evaluates the response against
        the stored definition, and records the session.

        This is the autonomous active recall loop that runs once per night
        session after SPV milestones have started processing.

        Args:
            max_items: Max practice items to process (default 50 for night mode)

        Returns:
            List of practice results: {concept, passed, score, feedback, ...}
        """
        results: List[Dict[str, Any]] = []
        t0 = time.time()

        try:
            from core.eidos_practice import get_practice_queue, get_active_recall
            pq = get_practice_queue()
            ar = get_active_recall()

            due_concepts = pq.get_due_concepts(limit=max_items)

            if not due_concepts:
                log.info("Night Practice: no due items in practice_queue")
                return results

            log.info("Night Practice: starting batch of %d due items (max %d)",
                     len(due_concepts), max_items)

            processed = 0
            passed_count = 0

            for dc in due_concepts:
                concept = dc["concept"]
                node_id = dc.get("node_id", "")
                definition = dc.get("definition", "")
                level = dc.get("mastery_level", "NOVICE")

                try:
                    # Step 1: Generate active recall prompt
                    prompt_data = ar.generate_prompt(concept)
                    if "error" in prompt_data:
                        results.append({
                            "type": "practice",
                            "concept": concept,
                            "node_id": node_id,
                            "passed": False,
                            "score": 0.0,
                            "error": prompt_data.get("error", ""),
                        })
                        continue

                    # Step 2: Generate recall response FROM MEMORY via LLM
                    recall_response = ""
                    try:
                        from core.eidos_learn import ask_llm
                        recall_prompt = (
                            f"You are EIDOS doing active recall practice. "
                            f"From MEMORY ONLY (do NOT look up anything), "
                            f"answer this question:\n\n{prompt_data['prompt']}\n\n"
                            f"Respond in Spanish. Keep it under 200 words. "
                            f"If you truly don't know, say 'No lo recuerdo bien'."
                        )
                        recall_response, llm_source = ask_llm(recall_prompt, timeout=20)
                    except Exception as llm_err:
                        log.debug("Night Practice LLM failed for %s: %s", concept, llm_err)
                        recall_response = (
                            f"Recuerdo parcial: {definition[:300]}"
                            if definition else "No lo recuerdo bien"
                        )

                    # Step 3: Evaluate recall against stored definition
                    session_result = ar.practice_session(concept, recall_response)
                    evaluation = session_result.get("evaluation", {})

                    # Step 4: Determine if we should advance to next mastery level
                    mastery_advanced = False
                    if session_result.get("success") and session_result.get("recall_score", 0) > 0.8:
                        try:
                            from core.eidos_practice import get_gate_checker
                            gc = get_gate_checker()
                            advance_result = gc.advance_level(concept)
                            mastery_advanced = advance_result.get("advanced", False)
                            if mastery_advanced:
                                log.info("Night Practice: %s advanced to %s",
                                         concept, advance_result.get("new_level", "?"))
                        except Exception:
                            pass

                    pr = {
                        "type": "practice",
                        "concept": concept,
                        "node_id": node_id,
                        "passed": session_result.get("success", False),
                        "score": session_result.get("recall_score", 0.0),
                        "mastery_level": level,
                        "feedback": evaluation.get("feedback", ""),
                        "next_review_hours": session_result.get("interval_hours", 0),
                        "streak_correct": session_result.get("streak_correct", 0),
                        "streak_wrong": session_result.get("streak_wrong", 0),
                        "duration_ms": session_result.get("duration_ms", 0),
                        "mastery_advanced": mastery_advanced,
                    }
                    results.append(pr)

                    if pr["passed"]:
                        passed_count += 1

                    log.info("Night Practice [%s]: %s score=%.3f streak=+%d/-%d %s",
                             "PASS" if pr["passed"] else "RETRY",
                             concept[:40],
                             pr["score"],
                             pr.get("streak_correct", 0),
                             pr.get("streak_wrong", 0),
                             "(ADVANCED!)" if mastery_advanced else "")

                    processed += 1

                except Exception as e:
                    log.error("Night Practice error on %s: %s", concept, e)
                    results.append({
                        "type": "practice",
                        "concept": concept,
                        "node_id": node_id,
                        "passed": False,
                        "score": 0.0,
                        "error": str(e)[:100],
                    })
                    processed += 1

            elapsed = time.time() - t0
            log.info("Night Practice batch COMPLETE: %d items in %.1f min, "
                     "%d/%d passed",
                     len(results), elapsed / 60, passed_count, len(results))

            # Store for morning briefing
            self._practice_night_results = results

        except Exception as e:
            log.error("Night Practice batch error: %s", e)

        return results

    # ── Night Explorer Mode ───────────────────────────────────────────────

    def _check_night_mode(self) -> bool:
        """Check if we should enter/exit night explorer mode."""
        was_night = self._night_mode
        idle_sec = _get_idle_seconds()

        if idle_sec >= NIGHT_IDLE_THRESHOLD and not self._night_mode:
            self._enter_night_mode()
        elif idle_sec < NIGHT_IDLE_THRESHOLD and self._night_mode:
            self._exit_night_mode()

        return self._night_mode

    def _enter_night_mode(self) -> None:
        """Activate aggressive night exploration."""
        self._night_mode = True
        self._practice_ran_this_night = False  # Reset for this night session
        self._practice_night_results = []
        self.atlas.start_night_session()
        log.info("Night Explorer mode activated (SER idle > %d min)",
                 NIGHT_IDLE_THRESHOLD // 60)
        if self._orchestrator and hasattr(self._orchestrator, "_stream"):
            self._orchestrator._stream(
                "curiosity",
                "Night Explorer mode ACTIVATED - aggressive exploration begins"
            )

    def _exit_night_mode(self) -> None:
        """Deactivate night exploration and synthesize briefing."""
        self._night_mode = False
        self._idle_since = None
        duration = self.atlas.end_night_session()
        log.info("Night Explorer mode deactivated (SER active again)")

        # Synthesize morning briefing (combined exploration + mastery)
        try:
            path = self.briefing.write_briefing(
                self.atlas._night_session,
                mastery_results=self._night_mastery_results,
            )
            log.info("Morning briefing written to %s", path)
            # Reset mastery results for next night
            self._night_mastery_results = []
            if self._orchestrator and hasattr(self._orchestrator, "_stream"):
                self._orchestrator._stream(
                    "curiosity",
                    f"Morning briefing synthesized: {path}",
                    extra={"night_session": self.atlas._night_session,
                           "duration_min": duration / 60 if duration else 0}
                )
        except Exception as e:
            log.debug("Briefing write error: %s", e)

    def is_night_mode(self) -> bool:
        return self._night_mode

    def morning_briefing(self) -> str:
        """Public API: get the morning briefing content (exploration + mastery)."""
        return self.briefing.synthesize(
            mastery_results=self._night_mastery_results if self._night_mastery_results else None
        )

    # ── Background loop ──────────────────────────────────────────────────

    def start(self, interval_seconds: Optional[float] = None) -> None:
        """Start the curiosity engine in background thread."""
        if self._thread and self._thread.is_alive():
            return
        if interval_seconds is None:
            interval_seconds = float(
                os.environ.get("EIDOS_CURIOSITY_ENGINE_INTERVAL",
                               str(DEFAULT_IDLE_INTERVAL))
            )
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(interval_seconds,),
            daemon=True, name="eidos-curiosity-engine"
        )
        self._thread.start()
        log.info("Curiosity Engine started — tick every %gs", interval_seconds)

    def stop(self) -> None:
        """Stop the curiosity engine."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._night_mode:
            self._exit_night_mode()
        log.info("Curiosity Engine stopped (%d ticks, %d explored)",
                 self._tick_count, self._explore_count)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict[str, Any]:
        return {
            "ticks": self._tick_count,
            "explored": self._explore_count,
            "night_mode": self._night_mode,
            "atlas_pending": self.atlas.count_pending(),
            "atlas_explored": self.atlas.count_explored(),
            "running": self.is_running(),
        }

    def _loop(self, interval_seconds: float) -> None:
        """Main background loop."""
        # Wait 60s before first tick to let system initialize
        if not self._stop.wait(60):
            while not self._stop.is_set():
                try:
                    effective_interval = (
                        NIGHT_INTERVAL if self._night_mode else interval_seconds
                    )
                    self.tick()
                except Exception as e:
                    log.exception("Curiosity Engine tick error: %s", e)
                self._stop.wait(timeout=effective_interval)


# ── Singleton ────────────────────────────────────────────────────────────────

_engine: Optional[CuriosityEngine] = None
_engine_lock = threading.Lock()


def get_curiosity_engine() -> CuriosityEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = CuriosityEngine()
    return _engine


# ── Integration with AliveOrchestrator ──────────────────────────────────────

def integrate_with_orchestrator(orchestrator) -> None:
    """Wire the Curiosity Engine into an AliveOrchestrator instance.

    After calling this, the orchestrator's cycle() will automatically
    run curiosity engine ticks during idle cycles.

    Usage in AliveOrchestrator.__init__ or start():
        from core.eidos_curiosity_engine import integrate_with_orchestrator
        integrate_with_orchestrator(self)
    """
    engine = get_curiosity_engine()
    engine._orchestrator = orchestrator

    # Save original cycle method
    original_cycle = orchestrator.cycle

    def enhanced_cycle():
        """Enhanced cycle that includes curiosity engine tick."""
        original_cycle()

        # Run curiosity tick if the brain decided to observe (idle)
        # and enough time has passed since last tick
        now = time.time()
        if now - engine._last_tick >= DEFAULT_IDLE_INTERVAL:
            try:
                engine.tick()
            except Exception:
                pass

    orchestrator.cycle = enhanced_cycle
    log.info("Curiosity Engine integrated into AliveOrchestrator")


# ── CLI / Standalone ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(message)s"
    )

    engine = get_curiosity_engine()

    if "--once" in sys.argv:
        print("=== EIDOS Curiosity Engine — single tick ===")
        result = engine.tick()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\nAtlas: {engine.atlas.count_pending()} pending, "
              f"{engine.atlas.count_explored()} explored")

    elif "--scan" in sys.argv:
        print("=== EIDOS Curiosity Engine — scan mode ===")
        results = engine.scanners.scan_all(force=True)
        print(json.dumps(results, indent=2))
        print(f"\nAtlas: {engine.atlas.count_pending()} pending items")

    elif "--night" in sys.argv:
        print("=== EIDOS Curiosity Engine — Night Explorer Mode ===")
        print("Running 5 aggressive ticks...")
        engine._enter_night_mode()
        for i in range(5):
            result = engine.tick()
            print(f"  Tick {i+1}: {result.get('item_type', '?')} "
                  f"'{result.get('payload', '?')[:60]}' "
                  f"-> {'OK' if result.get('ok') else 'FAIL'}")
            time.sleep(2)
        engine._exit_night_mode()
        print("\nMorning Briefing:")
        print(engine.morning_briefing())

    elif "--briefing" in sys.argv:
        print(engine.morning_briefing())

    elif "--stats" in sys.argv:
        print(json.dumps(engine.get_stats(), indent=2))

    else:
        print("=== EIDOS Curiosity Engine — interactive ===")
        print("Starting background engine...")
        engine.start()
        try:
            while True:
                time.sleep(30)
                stats = engine.get_stats()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                      f"Ticks: {stats['ticks']}, "
                      f"Explored: {stats['explored']}, "
                      f"Night: {stats['night_mode']}, "
                      f"Atlas: {stats['atlas_pending']} pending / "
                      f"{stats['atlas_explored']} explored")
        except KeyboardInterrupt:
            engine.stop()
            print("Stopped.")
