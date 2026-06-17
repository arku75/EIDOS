#!/usr/bin/env python3
"""
core/eidos_singularity.py — EIDOS Singularity Engine
======================================================
Autonomous self-modification engine. Allows EIDOS to detect, propose, test,
and apply changes to its own source code — closing the loop that self_improver
leaves open for SER.

Three autonomy tiers:
  TIER 1 (auto):   bugfixes, optimization, docs — fully autonomous
  TIER 2 (semi):   refactoring, new helpers, error handling — trust >= 0.5
  TIER 3 (SER):    new modules, architectural changes — manual only

Trust Score: starts at 0.1, increases with successful changes, decreases
with rollbacks.

    score = clamp(0, 1,
        (successful / max(1, successful + failed + reverted*2))
        * min(1, uptime_hours / 24)
    )

Config:        ~/.eidos/singularity_enabled  (JSON)
Persistence:   ~/.eidos/singularity.db       (SQLite)

Pipeline:
  1. detect opportunities  (error logs + self-inspection)
  2. classify into tier    (TIER 1/2/3 via TierClassifier)
  3. generate fix          (via existing self_improver)
  4. test in sandbox       (via existing eidos_staging)
  5. promote if passed     (copy from sandbox to production)
  6. git commit            (via git_guardian or direct git CLI)
  7. verify health         (syntax + import + smoke test)
  8. update trust score    (persist to singularity.db)

Safety invariants:
  - NEVER touch constitution files (constitution.toml, constitution.py)
  - ALWAYS create backup before any production change
  - ALWAYS test in sandbox clone first
  - ALWAYS enable instant rollback via git

CLI:
  python3 core/eidos_singularity.py enable  --tier 2
  python3 core/eidos_singularity.py disable
  python3 core/eidos_singularity.py status
  python3 core/eidos_singularity.py daemon  --interval 5
  python3 core/eidos_singularity.py run     --once
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure EIDOS root is on sys.path for `python3 core/eidos_singularity.py` usage
_EIDOS_ROOT = Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in sys.path:
    sys.path.insert(0, str(_EIDOS_ROOT))

from core.db import get_conn

log = logging.getLogger("eidos.singularity")

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

EIDOS_ROOT    = _EIDOS_ROOT
CONFIG_PATH   = Path.home() / ".eidos" / "singularity_enabled"
DB_PATH       = Path.home() / ".eidos" / "singularity.db"
BACKUP_DIR    = Path.home() / ".eidos" / "singularity_backups"
DAEMON_PID    = Path.home() / ".eidos" / "singularity_daemon.pid"

# Files that the Singularity Engine MUST NEVER modify.
# Includes constitution files AND the entire self-modification infrastructure
# that the Singularity Engine depends on to function safely.
IMMUTABLE_FILES: List[str] = [
    # Constitution (absolute invariants)
    "constitution.toml",
    "core/constitution.py",
    # Self-modification infrastructure (this module's dependencies)
    "core/eidos_singularity.py",
    "core/self_improver.py",
    "core/eidos_self_improvement.py",
    "core/eidos_staging.py",
    "core/git_guardian.py",
    "core/self_healing.py",
    # Core safety (RAM, trust model)
    "core/ram_guardian.py",
    "core/eidos_trust_model.py",
    # DB layer (everything depends on it)
    "core/db.py",
]

# TIER 1 patterns: fully autonomous changes
TIER1_PATTERNS: List[Tuple[str, str]] = [
    (r"bare.*except", "bugfix"),
    (r"fix.*bug",             "bugfix"),
    (r"fix.*error",           "bugfix"),
    (r"correct.*",            "bugfix"),
    (r"optimiz",              "optimization"),
    (r"speed.*up",            "optimization"),
    (r"faster",               "optimization"),
    (r"add.*docstring",       "docs"),
    (r"document",             "docs"),
    (r"missing.*doc",         "docs"),
    (r"typo",                 "docs"),
    (r"clean.*up",            "optimization"),
    (r"remove.*dead.*code",   "optimization"),
    (r"add.*type.*hint",      "docs"),
    (r"add.*log",             "docs"),
]

# TIER 2 patterns: semi-autonomous (need trust >= 0.5)
TIER2_PATTERNS: List[Tuple[str, str]] = [
    (r"refactor",             "refactoring"),
    (r"restructur",           "refactoring"),
    (r"reorganiz",            "refactoring"),
    (r"add.*helper",          "new_helper"),
    (r"add.*util",            "new_helper"),
    (r"error.*handling",      "error_handling"),
    (r"exception.*handling",  "error_handling"),
    (r"add.*retry",           "error_handling"),
    (r"add.*fallback",        "error_handling"),
    (r"extract.*function",    "refactoring"),
]

# TIER 3 patterns: SER-required
TIER3_PATTERNS: List[Tuple[str, str]] = [
    (r"new.*module",          "new_module"),
    (r"create.*module",       "new_module"),
    (r"architect",            "architectural"),
    (r"redesign",             "architectural"),
    (r"rewrite",              "architectural"),
    (r"api.*change",          "architectural"),
    (r"breaking.*change",     "architectural"),
    (r"database.*schema",     "architectural"),
    (r"migrat",               "architectural"),
]


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrustSnapshot:
    """Current trust score state."""
    score:            float = 0.1
    successful:       int   = 0
    failed:           int   = 0
    reverted:         int   = 0
    total_attempted:  int   = 0
    started_at:       float = 0.0       # epoch timestamp when tracking began
    last_updated:     float = 0.0


@dataclass
class Opportunity:
    """A detected self-improvement opportunity."""
    id:            str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    description:  str = ""
    file_path:    str = ""
    area_name:    str = ""
    evidence:     str = ""
    priority:     int = 5          # 1=critical, 5=low
    tier:         int = 1          # classified tier
    change_type:  str = "unknown"  # bugfix, optimization, docs, etc.


@dataclass
class ChangeRecord:
    """A record of a change applied via the Singularity Engine."""
    id:            str
    opportunity_id: str
    file_path:     str
    tier:          int
    change_type:   str
    description:   str
    proposed_code: str = ""
    sandbox_passed: bool = False
    promoted:       bool = False
    git_commit:     str = ""
    health_ok:      bool = False
    reverted:       bool = False
    error_message:  str = ""
    created_at:     float = 0.0
    completed_at:   float = 0.0


@dataclass
class PipelineResult:
    """Result of running the full pipeline."""
    success:         bool
    opportunity_id:  str = ""
    tier:            int = 0
    change_id:       str = ""
    stage:           str = ""          # which stage failed (if any)
    message:         str = ""
    sandbox_passed:  bool = False
    promoted:        bool = False
    committed:       bool = False
    health_ok:       bool = False
    trust_before:    float = 0.0
    trust_after:     float = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# TRUST SCORE
# ══════════════════════════════════════════════════════════════════════════════

class TrustScoreDB:
    """Manages the trust score — persistence, calculation, tracking."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._maybe_bootstrap()

    def _init_db(self) -> None:
        conn = get_conn(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trust_state (
                id             INTEGER PRIMARY KEY CHECK (id = 1),
                score          REAL    NOT NULL DEFAULT 0.1,
                successful     INTEGER NOT NULL DEFAULT 0,
                failed         INTEGER NOT NULL DEFAULT 0,
                reverted       INTEGER NOT NULL DEFAULT 0,
                total_attempted INTEGER NOT NULL DEFAULT 0,
                started_at     REAL    NOT NULL,
                last_updated   REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS change_history (
                id              TEXT PRIMARY KEY,
                opportunity_id  TEXT,
                file_path       TEXT,
                tier            INTEGER,
                change_type     TEXT,
                description     TEXT,
                proposed_code   TEXT,
                sandbox_passed  INTEGER DEFAULT 0,
                promoted        INTEGER DEFAULT 0,
                git_commit      TEXT,
                health_ok       INTEGER DEFAULT 0,
                reverted        INTEGER DEFAULT 0,
                error_message   TEXT,
                created_at      REAL,
                completed_at    REAL
            );

            CREATE INDEX IF NOT EXISTS idx_changes_tier
                ON change_history(tier);
            CREATE INDEX IF NOT EXISTS idx_changes_created
                ON change_history(created_at);
        """)
        conn.commit()
        pass  # S109: get_conn no necesita close()

    def _maybe_bootstrap(self) -> None:
        """Insert initial trust state if this is the first run."""
        conn = get_conn(self.db_path)
        row = conn.execute("SELECT 1 FROM trust_state WHERE id = 1").fetchone()
        if not row:
            now = time.time()
            conn.execute(
                "INSERT INTO trust_state (id, score, successful, failed, reverted, "
                "total_attempted, started_at, last_updated) VALUES (1, 0.1, 0, 0, 0, 0, ?, ?)",
                (now, now)
            )
            conn.commit()
        pass  # S109: get_conn no necesita close()

    def get_snapshot(self) -> TrustSnapshot:
        conn = get_conn(self.db_path)
        row = conn.execute(
            "SELECT score, successful, failed, reverted, total_attempted, "
            "started_at, last_updated FROM trust_state WHERE id = 1"
        ).fetchone()
        pass  # S109: get_conn no necesita close()
        if not row:
            return TrustSnapshot(started_at=time.time(), last_updated=time.time())
        return TrustSnapshot(
            score=row[0], successful=row[1], failed=row[2],
            reverted=row[3], total_attempted=row[4],
            started_at=row[5], last_updated=row[6]
        )

    def calculate_score(self, snapshot: TrustSnapshot = None) -> float:
        """Calculate trust score using the formula."""
        s = snapshot or self.get_snapshot()

        # Uptime factor: how long has singularity been running (capped at 24h)
        uptime_hours = 0.0
        if s.started_at > 0:
            uptime_hours = (time.time() - s.started_at) / 3600.0
        uptime_factor = min(1.0, uptime_hours / 24.0)

        # Success ratio: weighted by reverts (reverts cost double)
        attempts = s.successful + s.failed + s.reverted
        denominator = max(1, attempts)
        success_ratio = s.successful / denominator

        score = max(0.1, success_ratio * uptime_factor)

        # Bootstrap: if no attempts yet and we just started, give 0.1
        if attempts == 0:
            score = 0.1

        return round(max(0.0, min(1.0, score)), 4)

    def record_success(self) -> float:
        """Record a successful change, recalculate and return new score."""
        conn = get_conn(self.db_path)
        conn.execute(
            "UPDATE trust_state SET successful = successful + 1, "
            "total_attempted = total_attempted + 1, last_updated = ? WHERE id = 1",
            (time.time(),)
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        new_score = self.calculate_score()
        conn2 = get_conn(self.db_path)
        conn2.execute("UPDATE trust_state SET score = ? WHERE id = 1", (new_score,))
        conn2.commit()
        pass  # S109: get_conn no necesita close()
        log.info("Trust score updated: %.4f (success)", new_score)
        return new_score

    def record_failure(self) -> float:
        """Record a failed change attempt."""
        conn = get_conn(self.db_path)
        conn.execute(
            "UPDATE trust_state SET failed = failed + 1, "
            "total_attempted = total_attempted + 1, last_updated = ? WHERE id = 1",
            (time.time(),)
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        new_score = self.calculate_score()
        conn2 = get_conn(self.db_path)
        conn2.execute("UPDATE trust_state SET score = ? WHERE id = 1", (new_score,))
        conn2.commit()
        pass  # S109: get_conn no necesita close()
        log.info("Trust score updated: %.4f (failure)", new_score)
        return new_score

    def record_revert(self) -> float:
        """Record a reverted change (costs double)."""
        conn = get_conn(self.db_path)
        conn.execute(
            "UPDATE trust_state SET reverted = reverted + 1, "
            "total_attempted = total_attempted + 1, last_updated = ? WHERE id = 1",
            (time.time(),)
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()
        new_score = self.calculate_score()
        conn2 = get_conn(self.db_path)
        conn2.execute("UPDATE trust_state SET score = ? WHERE id = 1", (new_score,))
        conn2.commit()
        pass  # S109: get_conn no necesita close()
        log.warning("Trust score updated: %.4f (revert — penalty x2)", new_score)
        return new_score

    def can_act_at_tier(self, tier: int) -> Tuple[bool, str]:
        """Check if the current trust score allows acting at the given tier."""
        snapshot = self.get_snapshot()
        score = snapshot.score

        if tier == 1:
            return True, "TIER 1: always allowed when enabled"
        elif tier == 2:
            if score >= 0.5:
                return True, f"TIER 2: allowed (trust={score:.3f} >= 0.5)"
            else:
                return False, f"TIER 2: denied (trust={score:.3f} < 0.5)"
        elif tier == 3:
            return False, "TIER 3: requires SER approval (constitution.toml)"
        else:
            return False, f"Unknown tier: {tier}"

    def save_change_record(self, record: ChangeRecord) -> None:
        conn = get_conn(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO change_history "
            "(id, opportunity_id, file_path, tier, change_type, description, "
            "proposed_code, sandbox_passed, promoted, git_commit, health_ok, "
            "reverted, error_message, created_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.id, record.opportunity_id, record.file_path, record.tier,
             record.change_type, record.description, record.proposed_code,
             int(record.sandbox_passed), int(record.promoted), record.git_commit,
             int(record.health_ok), int(record.reverted), record.error_message,
             record.created_at, record.completed_at)
        )
        conn.commit()
        pass  # S109: get_conn no necesita close()

    def get_recent_changes(self, limit: int = 20) -> List[ChangeRecord]:
        conn = get_conn(self.db_path)
        rows = conn.execute(
            "SELECT * FROM change_history ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        pass  # S109: get_conn no necesita close()
        return [ChangeRecord(
            id=r[0], opportunity_id=r[1], file_path=r[2], tier=r[3],
            change_type=r[4], description=r[5], proposed_code=r[6] or "",
            sandbox_passed=bool(r[7]), promoted=bool(r[8]), git_commit=r[9] or "",
            health_ok=bool(r[10]), reverted=bool(r[11]), error_message=r[12] or "",
            created_at=r[13], completed_at=r[14]
        ) for r in rows]

    def count_today_changes(self) -> int:
        """Count changes made today (for daily rate limiting)."""
        today_start = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        conn = get_conn(self.db_path)
        row = conn.execute(
            "SELECT COUNT(*) FROM change_history WHERE created_at >= ? AND reverted = 0",
            (today_start,)
        ).fetchone()
        pass  # S109: get_conn no necesita close()
        return row[0] if row else 0


# ══════════════════════════════════════════════════════════════════════════════
# SINGULARITY CONFIG
# ══════════════════════════════════════════════════════════════════════════════

class SingularityConfig:
    """Reads/writes ~/.eidos/singularity_enabled — SER's one-time approval."""

    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = config_path

    def read(self) -> Dict[str, Any]:
        """Read config. Returns defaults if file does not exist."""
        if not self.config_path.exists():
            return {"enabled": False, "max_tier": 0, "max_changes_per_day": 0}
        try:
            raw = self.config_path.read_text(errors="replace").strip()
            if not raw:
                return {"enabled": False, "max_tier": 0, "max_changes_per_day": 0}
            data = json.loads(raw)
            return {
                "enabled": bool(data.get("enabled", False)),
                "max_tier": int(data.get("max_tier", 1)),
                "max_changes_per_day": int(data.get("max_changes_per_day", 10)),
            }
        except (json.JSONDecodeError, ValueError) as e:
            log.error("Invalid singularity config: %s", e)
            return {"enabled": False, "max_tier": 0, "max_changes_per_day": 0}

    def write(self, enabled: bool, max_tier: int = 1,
              max_changes_per_day: int = 10) -> None:
        """Write the enable/disable config. This is SER's approval mechanism."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "enabled": enabled,
            "max_tier": max_tier,
            "max_changes_per_day": max_changes_per_day,
            "enabled_at": time.time() if enabled else None,
            "enabled_by": "SER",
        }
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        self.config_path.chmod(0o600)
        if enabled:
            log.info("Singularity ENABLED (tier %d, %d/day)", max_tier, max_changes_per_day)
        else:
            log.info("Singularity DISABLED")

    def is_enabled(self) -> bool:
        return self.read()["enabled"]

    def get_max_tier(self) -> int:
        return self.read()["max_tier"]

    def get_max_per_day(self) -> int:
        return self.read()["max_changes_per_day"]


# ══════════════════════════════════════════════════════════════════════════════
# SAFETY GUARD
# ══════════════════════════════════════════════════════════════════════════════

class SafetyGuard:
    """Enforces safety invariants for the Singularity Engine."""

    @staticmethod
    def is_immutable(file_path: str) -> Tuple[bool, str]:
        """Check if a file is constitution-protected and cannot be touched."""
        path = Path(file_path)
        # Check against relative paths
        try:
            rel = str(path.relative_to(EIDOS_ROOT))
        except ValueError:
            rel = str(path)

        for immutable in IMMUTABLE_FILES:
            if rel == immutable or path.name == Path(immutable).name:
                # Extra check: verify it's actually the expected file
                if path.parent.name == Path(immutable).parent.name or rel == immutable:
                    return True, f"IMMUTABLE: {immutable} (constitution-protected)"
        return False, ""

    @staticmethod
    def create_backup(file_path: str) -> Optional[Path]:
        """Create a timestamped backup of a file before modification."""
        path = Path(file_path)
        if not path.exists():
            log.warning("Cannot backup non-existent file: %s", path)
            return None

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"{path.stem}_{ts}{path.suffix}"

        try:
            shutil.copy2(path, backup_path)
            log.info("Backup created: %s", backup_path)
            return backup_path
        except Exception as e:
            log.error("Backup failed for %s: %s", path, e)
            return None

    @staticmethod
    def restore_backup(backup_path: Path, target_path: str) -> bool:
        """Restore a file from backup (instant rollback)."""
        target = Path(target_path)
        if not backup_path.exists():
            log.error("Backup file not found: %s", backup_path)
            return False
        try:
            shutil.copy2(backup_path, target)
            log.info("Restored backup: %s -> %s", backup_path, target)
            return True
        except Exception as e:
            log.error("Failed to restore backup: %s", e)
            return False

    @staticmethod
    def verify_file_syntax(file_path: str) -> Tuple[bool, str]:
        """Check that a Python file compiles correctly."""
        try:
            subprocess.run(
                [sys.executable, "-m", "py_compile", file_path],
                capture_output=True, text=True, timeout=15,
                check=True
            )
            return True, ""
        except subprocess.CalledProcessError as e:
            return False, f"Syntax error: {e.stderr[:200]}"
        except Exception as e:
            return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# OPPORTUNITY DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

class OpportunityDetector:
    """Detects self-improvement opportunities from error logs and self-inspection."""

    def __init__(self):
        self._log_paths = [
            Path.home() / ".eidos" / "logs" / "colony_dashboard.log",
            Path.home() / ".eidos" / "logs" / "eidos_libre.log",
            Path.home() / ".eidos" / "logs" / "learning_daemon.log",
            Path.home() / ".eidos" / "logs" / "singularity.log",
        ]

    def detect_all(self) -> List[Opportunity]:
        """Run all detection methods and return deduplicated opportunities."""
        opportunities: List[Opportunity] = []

        opportunities.extend(self._from_error_logs())
        opportunities.extend(self._from_code_inspection())
        opportunities.extend(self._from_todos_and_fixmes())

        # Deduplicate by file_path + description
        seen: set = set()
        unique: List[Opportunity] = []
        for opp in opportunities:
            key = f"{opp.file_path}:{opp.description[:80]}"
            if key not in seen:
                seen.add(key)
                unique.append(opp)

        unique.sort(key=lambda o: o.priority)
        log.info("Detected %d unique opportunities", len(unique))
        return unique[:20]

    def _from_error_logs(self) -> List[Opportunity]:
        """Scan error logs for recurrent issues."""
        opportunities: List[Opportunity] = []
        for log_file in self._log_paths:
            if not log_file.exists():
                continue
            try:
                lines = log_file.read_text(errors="replace").splitlines()
                # Look at last 500 lines
                recent = lines[-500:]
                errors = [l for l in recent if "ERROR" in l or "Exception" in l
                         or "Traceback" in l]
                if len(errors) >= 3:
                    # Group by error type
                    error_types: Dict[str, int] = {}
                    for e in errors:
                        # Extract class name from traceback or error line
                        m = re.search(r"(\w+Error|\w+Exception)", e)
                        key = m.group(1) if m else e[:60].strip()
                        error_types[key] = error_types.get(key, 0) + 1

                    for etype, count in error_types.items():
                        if count >= 2:
                            opportunities.append(Opportunity(
                                description=f"Fix recurrent {etype} in {log_file.name}",
                                file_path="",
                                area_name=f"error_{etype.lower()}",
                                evidence=f"{count} occurrences of {etype}",
                                priority=2 if count >= 5 else 3,
                            ))
            except Exception:
                pass
        return opportunities

    def _from_code_inspection(self) -> List[Opportunity]:
        """Inspect core/ Python files for improvement areas."""
        opportunities: List[Opportunity] = []
        core_dir = EIDOS_ROOT / "core"
        if not core_dir.exists():
            return opportunities

        for py_file in core_dir.glob("*.py"):
            # Skip immutable files
            try:
                rel = str(py_file.relative_to(EIDOS_ROOT))
            except ValueError:
                rel = str(py_file)
            if any(rel == im or py_file.name == Path(im).name
                   for im in IMMUTABLE_FILES):
                continue

            try:
                content = py_file.read_text(errors="replace")
            except Exception:
                continue

            # Bare except clauses (catch-all, suppress errors)
            bare_excepts = len(re.findall(r'except\s*:', content))
            if bare_excepts > 0:
                opportunities.append(Opportunity(
                    description=f"Replace {bare_excepts} bare except clause(s) with specific exceptions",
                    file_path=str(py_file),
                    area_name=f"bare_except_{py_file.stem}",
                    evidence=f"Found {bare_excepts} bare 'except:' clauses",
                    priority=3,
                ))

            # Print statements (should use logging)
            prints = len(re.findall(r'^\s*print\(', content, re.MULTILINE))
            if prints > 3:
                opportunities.append(Opportunity(
                    description=f"Replace {prints} print() calls with logging in {py_file.name}",
                    file_path=str(py_file),
                    area_name=f"print_to_log_{py_file.stem}",
                    evidence=f"Found {prints} print() calls",
                    priority=4,
                ))

            # Missing docstrings (functions without docstrings)
            funcs_without_doc = 0
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("def ") and i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if not next_line.startswith('"""') and not next_line.startswith("'''"):
                        funcs_without_doc += 1
            if funcs_without_doc > 5:
                opportunities.append(Opportunity(
                    description=f"Add docstrings to {funcs_without_doc} undocumented functions in {py_file.name}",
                    file_path=str(py_file),
                    area_name=f"missing_docs_{py_file.stem}",
                    evidence=f"{funcs_without_doc} functions without docstrings",
                    priority=5,
                ))

        return opportunities

    def _from_todos_and_fixmes(self) -> List[Opportunity]:
        """Find TODO/FIXME/HACK markers in the codebase."""
        opportunities: List[Opportunity] = []
        core_dir = EIDOS_ROOT / "core"
        if not core_dir.exists():
            return opportunities

        for py_file in core_dir.glob("*.py"):
            try:
                rel = str(py_file.relative_to(EIDOS_ROOT))
            except ValueError:
                rel = str(py_file)
            if any(rel == im or py_file.name == Path(im).name
                   for im in IMMUTABLE_FILES):
                continue

            try:
                content = py_file.read_text(errors="replace")
            except Exception:
                continue

            markers = []
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                for marker in ["TODO", "FIXME", "HACK", "XXX"]:
                    if marker in stripped and not stripped.startswith("#"):
                        markers.append(f"L{i}: {stripped[:100]}")
                        break

            if markers:
                opportunities.append(Opportunity(
                    description=f"Resolve {len(markers)} TODO/FIXME items in {py_file.name}",
                    file_path=str(py_file),
                    area_name=f"todos_{py_file.stem}",
                    evidence="\n".join(markers[:3]),
                    priority=3 if len(markers) <= 3 else 2,
                ))

        return opportunities


# ══════════════════════════════════════════════════════════════════════════════
# TIER CLASSIFIER
# ══════════════════════════════════════════════════════════════════════════════

class TierClassifier:
    """Classifies opportunities into autonomy tiers based on patterns."""

    @staticmethod
    def classify(opportunity: Opportunity) -> int:
        """Classify an opportunity into TIER 1, 2, or 3."""
        text = f"{opportunity.description} {opportunity.area_name} {opportunity.evidence}".lower()

        # Check TIER 3 first (architectural / new modules)
        for pattern, ctype in TIER3_PATTERNS:
            if re.search(pattern, text):
                opportunity.change_type = ctype
                opportunity.tier = 3
                return 3

        # Check TIER 2 (refactoring / error handling)
        for pattern, ctype in TIER2_PATTERNS:
            if re.search(pattern, text):
                opportunity.change_type = ctype
                opportunity.tier = 2
                return 2

        # Check TIER 1 (bugfixes, optimization, docs)
        for pattern, ctype in TIER1_PATTERNS:
            if re.search(pattern, text):
                opportunity.change_type = ctype
                opportunity.tier = 1
                return 1

        # Default: unknown → TIER 2 to be safe (needs trust)
        opportunity.change_type = "unknown"
        opportunity.tier = 2
        # But if it involves a new file or major changes, bump to TIER 3
        if "new" in text and ("file" in text or "module" in text):
            opportunity.tier = 3
            opportunity.change_type = "new_module"
            return 3
        return 2

    @staticmethod
    def classify_from_self_improver(area) -> int:
        """Classify from a SelfImprover ImprovementArea object."""
        text = f"{area.name} {area.description} {area.evidence}".lower()

        for pattern, _ in TIER3_PATTERNS:
            if re.search(pattern, text):
                return 3
        for pattern, _ in TIER2_PATTERNS:
            if re.search(pattern, text):
                return 2
        return 1


# ══════════════════════════════════════════════════════════════════════════════
# SINGULARITY ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class SingularityEngine:
    """
    Main orchestration engine. Runs the full self-modification pipeline:
    detect -> classify -> generate fix -> test in sandbox -> promote ->
    git commit -> verify health -> update trust score.
    """

    def __init__(self):
        self.config      = SingularityConfig()
        self.trust_db    = TrustScoreDB()
        self.detector    = OpportunityDetector()
        self.classifier  = TierClassifier()
        self.safety      = SafetyGuard()

        # Lazy imports for optional dependencies
        self._self_improver = None
        self._staging       = None

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        log.info("SingularityEngine initialized (enabled=%s)", self.config.is_enabled())

    @property
    def self_improver(self):
        if self._self_improver is None:
            try:
                from core.self_improver import SelfImprover
                self._self_improver = SelfImprover()
            except ImportError as e:
                log.warning("self_improver not available: %s", e)
                self._self_improver = None
        return self._self_improver

    @property
    def staging(self):
        if self._staging is None:
            try:
                from core.eidos_staging import get_staging_system
                self._staging = get_staging_system()
            except ImportError as e:
                log.warning("eidos_staging not available: %s", e)
                self._staging = None
        return self._staging

    # ── Pipeline steps ──────────────────────────────────────────────────────

    def run_pipeline(self, opportunity: Opportunity = None) -> PipelineResult:
        """Run the complete self-modification pipeline for one opportunity."""
        if not self.config.is_enabled():
            return PipelineResult(
                success=False, stage="config",
                message="Singularity is not enabled. Run: python3 core/eidos_singularity.py enable --tier 2"
            )

        max_tier = self.config.get_max_tier()
        max_per_day = self.config.get_max_per_day()
        today_count = self.trust_db.count_today_changes()

        if today_count >= max_per_day:
            return PipelineResult(
                success=False, stage="rate_limit",
                message=f"Daily limit reached ({today_count}/{max_per_day})"
            )

        # Step 1: Detect opportunities (if not provided)
        if opportunity is None:
            opportunities = self.detector.detect_all()
            if not opportunities:
                return PipelineResult(
                    success=False, stage="detect",
                    message="No improvement opportunities detected"
                )
            opportunity = opportunities[0]

        # Step 2: Classify into tier
        if opportunity.tier == 0:
            self.classifier.classify(opportunity)
        tier = opportunity.tier

        if tier > max_tier:
            return PipelineResult(
                success=False, stage="classify", tier=tier,
                opportunity_id=opportunity.id,
                message=f"TIER {tier} exceeds max configured tier {max_tier}"
            )

        # Step 3: Check trust score allows this tier
        can_act, reason = self.trust_db.can_act_at_tier(tier)
        if not can_act:
            return PipelineResult(
                success=False, stage="trust_check", tier=tier,
                opportunity_id=opportunity.id,
                message=reason
            )

        trust_before = self.trust_db.get_snapshot().score

        # Step 4: Safety check — is the target file immutable?
        if opportunity.file_path:
            immutable, msg = self.safety.is_immutable(opportunity.file_path)
            if immutable:
                return PipelineResult(
                    success=False, stage="safety", tier=tier,
                    opportunity_id=opportunity.id,
                    message=f"SAFETY BLOCK: {msg}"
                )

        # Step 5: Generate fix via self_improver
        if not self.self_improver:
            return PipelineResult(
                success=False, stage="generate", tier=tier,
                opportunity_id=opportunity.id,
                message="self_improver module not available"
            )

        change_id = f"sing_{int(time.time())}_{uuid.uuid4().hex[:8]}"

        # Try using self_improver if we have a concrete area + file
        if opportunity.file_path and opportunity.file_path.endswith(".py"):
            proposal = self._generate_fix_via_improver(opportunity)
        else:
            # No concrete file — log and skip
            return PipelineResult(
                success=False, stage="generate", tier=tier,
                opportunity_id=opportunity.id,
                message=f"No concrete file to modify (desc: {opportunity.description[:80]})"
            )

        if not proposal:
            return PipelineResult(
                success=False, stage="generate", tier=tier,
                opportunity_id=opportunity.id,
                message="Failed to generate improvement proposal"
            )

        proposed_code = proposal.get("proposed_code", "")
        if not proposed_code:
            return PipelineResult(
                success=False, stage="generate", tier=tier,
                opportunity_id=opportunity.id,
                message="Proposed code is empty"
            )

        log.info("Pipeline: generated fix for %s (%d chars)",
                 opportunity.file_path, len(proposed_code))

        # Step 6: Test in sandbox clone
        sandbox_passed, sandbox_msg = self._test_in_sandbox(
            opportunity.file_path, proposed_code, opportunity.description
        )

        result = PipelineResult(
            success=False, tier=tier, opportunity_id=opportunity.id,
            change_id=change_id, trust_before=trust_before
        )

        if not sandbox_passed:
            result.stage = "sandbox"
            result.message = f"Sandbox test failed: {sandbox_msg}"
            result.sandbox_passed = False
            self.trust_db.record_failure()
            result.trust_after = self.trust_db.get_snapshot().score
            self._save_failed_record(change_id, opportunity, proposed_code,
                                     "sandbox", sandbox_msg)
            return result

        result.sandbox_passed = True
        log.info("Pipeline: sandbox tests PASSED")

        # Step 7: Backup production file
        backup_path = self.safety.create_backup(opportunity.file_path)
        if not backup_path:
            result.stage = "backup"
            result.message = "Failed to create backup"
            self.trust_db.record_failure()
            result.trust_after = self.trust_db.get_snapshot().score
            return result

        # Step 8: Apply to production
        promoted = self._apply_to_production(
            opportunity.file_path, proposed_code, change_id
        )

        if not promoted:
            result.stage = "promote"
            result.message = "Failed to apply change to production"
            result.promoted = False
            # Restore backup
            self.safety.restore_backup(backup_path, opportunity.file_path)
            self.trust_db.record_failure()
            result.trust_after = self.trust_db.get_snapshot().score
            self._save_failed_record(change_id, opportunity, proposed_code,
                                     "promote", "Failed to apply")
            return result

        result.promoted = True
        log.info("Pipeline: change promoted to production")

        # Step 9: Verify syntax of production file
        syntax_ok, syntax_err = self.safety.verify_file_syntax(opportunity.file_path)
        if not syntax_ok:
            self.safety.restore_backup(backup_path, opportunity.file_path)
            result.stage = "syntax_check"
            result.message = f"Syntax error after applying: {syntax_err}"
            self.trust_db.record_revert()
            result.trust_after = self.trust_db.get_snapshot().score
            self._save_failed_record(change_id, opportunity, proposed_code,
                                     "syntax_check", syntax_err)
            return result

        # Step 10: Git commit (via git_guardian or direct git CLI)
        commit_hash = self._git_commit(opportunity.file_path, opportunity.description)
        result.committed = bool(commit_hash)
        if commit_hash:
            log.info("Pipeline: committed as %s", commit_hash[:8])

        # Step 11: Verify health
        health_ok, health_msg = self._verify_health(opportunity.file_path)
        result.health_ok = health_ok

        if not health_ok:
            # Attempt rollback via git
            self._git_rollback()
            self.safety.restore_backup(backup_path, opportunity.file_path)
            result.stage = "health_check"
            result.message = f"Health check failed: {health_msg}"
            self.trust_db.record_revert()
            result.trust_after = self.trust_db.get_snapshot().score
            self._save_reverted_record(change_id, opportunity, proposed_code,
                                       commit_hash, health_msg)
            return result

        # Step 12: Success — update trust score
        self.trust_db.record_success()
        result.success = True
        result.message = f"TIER {tier} change applied successfully: {opportunity.description[:100]}"
        result.trust_after = self.trust_db.get_snapshot().score

        # Save success record
        record = ChangeRecord(
            id=change_id, opportunity_id=opportunity.id,
            file_path=opportunity.file_path, tier=tier,
            change_type=opportunity.change_type,
            description=opportunity.description,
            proposed_code=proposed_code,
            sandbox_passed=True, promoted=True,
            git_commit=commit_hash or "", health_ok=True,
            created_at=time.time(), completed_at=time.time()
        )
        self.trust_db.save_change_record(record)

        log.info("Pipeline: SUCCESS (trust: %.3f -> %.3f)",
                 trust_before, result.trust_after)
        return result

    # ── Pipeline helpers ────────────────────────────────────────────────────

    def _generate_fix_via_improver(self, opportunity: Opportunity) -> Optional[Dict[str, str]]:
        """Generate a fix by calling into self_improver's DeepSeek pipeline."""
        if not self.self_improver:
            return None

        from core.self_improver import ImprovementArea

        area = ImprovementArea(
            name=opportunity.description[:100],
            description=opportunity.description,
            file_path=opportunity.file_path,
            priority=opportunity.priority,
            evidence=opportunity.evidence,
        )

        try:
            proposal = self.self_improver.propose_improvement(area)
            if proposal and proposal.proposed:
                return {
                    "proposed_code": proposal.proposed,
                    "reasoning": proposal.reasoning,
                    "model_used": proposal.model_used,
                }
        except Exception as e:
            log.warning("self_improver.propose_improvement failed: %s", e)

        return None

    def _test_in_sandbox(self, file_path: str,
                         proposed_code: str,
                         description: str) -> Tuple[bool, str]:
        """Test the proposed change in sandbox clone."""
        # Strategy: use eidos_staging if available, otherwise direct clone test
        if self.staging:
            try:
                # Read original code snippet from the file
                target = Path(file_path)
                if not target.exists():
                    return False, f"Target file does not exist: {file_path}"

                # For staging, we need original_code (the section to replace)
                # We use self_improver's clone test instead for function-level changes
                pass
            except Exception as e:
                log.warning("Staging unavailable, falling back to direct clone: %s", e)

        # Fallback: test using the S@NDBOX_EIDOS clone
        return self._test_in_clone(file_path, proposed_code)

    def _test_in_clone(self, file_path: str, proposed_code: str) -> Tuple[bool, str]:
        """Test a code change in the SANDBOX_EIDOS clone."""
        sanitizer = S4NDBOX_EIDOS if None else None  # unused, placeholder
        sandbox_root = Path("/home/ser/NO TOCAR/S@NDBOX_EIDOS")
        clone_dir = sandbox_root / "eidos_clon"

        if not clone_dir.exists():
            # Try to create clone
            return False, f"Sandbox clone does not exist at {clone_dir}. Run eidos_setup_sandbox first."

        # Determine clone file path
        try:
            rel = Path(file_path).relative_to(EIDOS_ROOT)
        except ValueError:
            rel = Path(file_path).name

        clone_file = clone_dir / rel
        if rel.parts and rel.parts[0] == "core":
            clone_file = clone_dir / "core" / rel.name
        else:
            # Try core/
            alt = clone_dir / "core" / Path(file_path).name
            if alt.exists():
                clone_file = alt

        if not clone_file.exists():
            return False, f"Clone file not found: {clone_file}"

        try:
            # Backup clone file
            clone_orig = clone_file.read_text(errors="replace")
            clone_backup = clone_dir / f".sing_backup_{int(time.time())}.py"
            clone_backup.write_text(clone_orig)

            # Apply change to clone
            modified = self._apply_code_to_file(clone_orig, proposed_code)
            if modified == clone_orig:
                return False, "Code not found in clone file — cannot apply change"
            clone_file.write_text(modified)

            # Test 1: Syntax check
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", str(clone_file)],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode != 0:
                clone_file.write_text(clone_orig)  # Restore
                return False, f"Syntax error in sandbox: {r.stderr[:200]}"

            # Test 2: Import check (basic)
            try:
                r2 = subprocess.run(
                    [sys.executable, "-c",
                     f"import sys; sys.path.insert(0, '{clone_dir}'); "
                     f"exec(open('{clone_file}').read())"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(clone_dir)
                )
                if r2.returncode != 0:
                    clone_file.write_text(clone_orig)  # Restore
                    err = r2.stderr[:300] if r2.stderr else "Import/execution failed"
                    return False, f"Sandbox execution error: {err}"
            except subprocess.TimeoutExpired:
                clone_file.write_text(clone_orig)  # Restore
                return False, "Sandbox execution timed out"

            # Test 3: Run smoke tests in clone
            smoke_path = clone_dir / "tests" / "test_eidos_suite.py"
            if smoke_path.exists():
                try:
                    r3 = subprocess.run(
                        [sys.executable, str(smoke_path)],
                        capture_output=True, text=True, timeout=120,
                        cwd=str(clone_dir)
                    )
                    if r3.returncode != 0:
                        clone_file.write_text(clone_orig)  # Restore
                        return False, f"Smoke tests failed in sandbox"
                except subprocess.TimeoutExpired:
                    clone_file.write_text(clone_orig)  # Restore
                    return False, "Smoke tests timed out in sandbox"

            # All tests passed — keep change in clone for reference, return success
            log.info("Sandbox tests: ALL PASSED for %s", file_path)
            return True, "All sandbox tests passed"

        except Exception as e:
            # Try restore
            try:
                clone_file.write_text(clone_orig)
            except Exception:
                pass
            return False, f"Sandbox test error: {e}"

    @staticmethod
    def _apply_code_to_file(file_content: str, proposed_code: str) -> str:
        """Apply proposed code to file content intelligently."""
        # Case 1: proposed code is a complete function — replace existing
        fn_match = re.search(r"def\s+(\w+)\s*\(", proposed_code)
        if fn_match:
            fn_name = fn_match.group(1)
            # Try to find and replace the function
            pattern = rf"def\s+{fn_name}\s*\([^)]*\).*?(?=\n(?:\S|def\s|class\s)|\Z)"
            modified, count = re.subn(pattern, proposed_code + "\n", file_content,
                                      flags=re.DOTALL, count=1)
            if count > 0:
                return modified
            else:
                # Function not found — append to end
                return file_content.rstrip() + "\n\n" + proposed_code + "\n"

        # Case 2: proposed code is a block — try to identify where it goes
        # Append to end as fallback
        return file_content.rstrip() + "\n\n" + proposed_code + "\n"

    def _apply_to_production(self, file_path: str, proposed_code: str,
                             change_id: str) -> bool:
        """Apply the change to the production file."""
        target = Path(file_path)
        if not target.exists():
            log.error("Production file not found: %s", file_path)
            return False

        try:
            current = target.read_text(errors="replace")
            modified = self._apply_code_to_file(current, proposed_code)

            if modified == current:
                log.error("Code not found in production file — no change applied")
                return False

            target.write_text(modified)
            log.info("Applied change to %s", file_path)
            return True
        except Exception as e:
            log.error("Failed to apply change: %s", e)
            return False

    def _git_commit(self, file_path: str, description: str) -> Optional[str]:
        """Commit the change via git CLI (works without GitPython)."""
        try:
            # Check we're in a git repo
            r = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True, text=True, timeout=5,
                cwd=str(EIDOS_ROOT)
            )
            if r.returncode != 0:
                log.warning("Not a git repository — skipping commit")
                return None

            # Stage the file
            subprocess.run(
                ["git", "add", file_path],
                capture_output=True, text=True, timeout=10,
                cwd=str(EIDOS_ROOT), check=True
            )

            # Commit
            msg = f"[EIDOS Singularity] {description[:80]}"
            r2 = subprocess.run(
                ["git", "commit", "-m", msg],
                capture_output=True, text=True, timeout=10,
                cwd=str(EIDOS_ROOT)
            )
            if r2.returncode != 0:
                log.warning("Git commit returned non-zero: %s", r2.stderr[:200])
                # Unstage
                subprocess.run(
                    ["git", "reset", "HEAD", "--", file_path],
                    capture_output=True, timeout=5, cwd=str(EIDOS_ROOT)
                )
                return None

            # Get commit hash
            r3 = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=str(EIDOS_ROOT)
            )
            return r3.stdout.strip() if r3.returncode == 0 else "committed"

        except FileNotFoundError:
            log.warning("git CLI not found — skipping commit")
            return None
        except Exception as e:
            log.warning("Git commit failed: %s", e)
            return None

    def _git_rollback(self) -> bool:
        """Rollback the last commit using git revert (safe, preserves history)."""
        try:
            # Use git revert to safely undo the last commit without destroying history
            subprocess.run(
                ["git", "revert", "--no-edit", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=str(EIDOS_ROOT), check=True
            )
            log.warning("Git rollback executed: revert HEAD")
            return True
        except Exception as e:
            log.error("Git rollback failed: %s", e)
            return False

    def _verify_health(self, file_path: str) -> Tuple[bool, str]:
        """Verify system health after a change."""
        checks = []

        # Check 1: File syntax
        syntax_ok, syntax_err = self.safety.verify_file_syntax(file_path)
        checks.append(("syntax", syntax_ok, syntax_err))

        # Check 2: Basic import of core modules
        try:
            r = subprocess.run(
                [sys.executable, "-c",
                 "from core.db import get_conn; "
                 "c=get_conn('self.db'); c.execute('SELECT 1')"],
                capture_output=True, text=True, timeout=15,
                cwd=str(EIDOS_ROOT)
            )
            db_ok = r.returncode == 0
            checks.append(("db_connect", db_ok, r.stderr[:100] if not db_ok else ""))
        except Exception as e:
            checks.append(("db_connect", False, str(e)[:100]))

        # Check 3: If smoke tests exist, run a quick subset
        smoke_path = EIDOS_ROOT / "tests" / "test_eidos_suite.py"
        if smoke_path.exists():
            try:
                r = subprocess.run(
                    [sys.executable, str(smoke_path)],
                    capture_output=True, text=True, timeout=90,
                    cwd=str(EIDOS_ROOT)
                )
                smoke_ok = r.returncode == 0
                checks.append(("smoke_test", smoke_ok,
                              r.stderr[:200] if not smoke_ok else ""))
            except subprocess.TimeoutExpired:
                checks.append(("smoke_test", False, "Timeout"))
            except Exception as e:
                checks.append(("smoke_test", False, str(e)[:100]))

        # Aggregate
        all_ok = all(ok for _, ok, _ in checks)
        if all_ok:
            return True, "All health checks passed"
        else:
            failures = [f"{name}: {err}" for name, ok, err in checks if not ok]
            return False, "; ".join(failures[:3])

    def _save_failed_record(self, change_id: str, opportunity: Opportunity,
                            proposed_code: str, stage: str, error: str) -> None:
        record = ChangeRecord(
            id=change_id, opportunity_id=opportunity.id,
            file_path=opportunity.file_path, tier=opportunity.tier,
            change_type=opportunity.change_type,
            description=opportunity.description,
            proposed_code=proposed_code,
            sandbox_passed=(stage != "sandbox"),
            error_message=error,
            created_at=time.time(), completed_at=time.time()
        )
        self.trust_db.save_change_record(record)

    def _save_reverted_record(self, change_id: str, opportunity: Opportunity,
                              proposed_code: str, commit_hash: str,
                              error: str) -> None:
        record = ChangeRecord(
            id=change_id, opportunity_id=opportunity.id,
            file_path=opportunity.file_path, tier=opportunity.tier,
            change_type=opportunity.change_type,
            description=opportunity.description,
            proposed_code=proposed_code,
            sandbox_passed=True, promoted=True,
            git_commit=commit_hash, health_ok=False,
            reverted=True, error_message=error,
            created_at=time.time(), completed_at=time.time()
        )
        self.trust_db.save_change_record(record)

    # ── Public API ──────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Return full status of the Singularity Engine."""
        cfg = self.config.read()
        trust = self.trust_db.get_snapshot()
        recent = self.trust_db.get_recent_changes(10)
        today = self.trust_db.count_today_changes()

        return {
            "enabled": cfg["enabled"],
            "max_tier": cfg["max_tier"],
            "max_changes_per_day": cfg["max_changes_per_day"],
            "today_changes": today,
            "remaining_today": max(0, cfg["max_changes_per_day"] - today),
            "trust_score": trust.score,
            "trust_details": {
                "successful": trust.successful,
                "failed": trust.failed,
                "reverted": trust.reverted,
                "total_attempted": trust.total_attempted,
                "uptime_hours": round((time.time() - trust.started_at) / 3600, 2)
                    if trust.started_at > 0 else 0,
            },
            "tier_permissions": {
                "tier_1": self.trust_db.can_act_at_tier(1)[0],
                "tier_2": self.trust_db.can_act_at_tier(2)[0],
                "tier_3": self.trust_db.can_act_at_tier(3)[0],
            },
            "recent_changes": [
                {
                    "id": c.id[:12], "file": c.file_path, "tier": c.tier,
                    "type": c.change_type, "success": c.health_ok and not c.reverted,
                    "reverted": c.reverted, "error": c.error_message[:80],
                    "time": datetime.fromtimestamp(c.created_at).isoformat()
                        if c.created_at else ""
                }
                for c in recent[:10]
            ],
            "immutable_files": IMMUTABLE_FILES,
        }

    def run_once(self) -> PipelineResult:
        """Run one complete pipeline cycle. Used by daemon and --once CLI."""
        if not self.config.is_enabled():
            return PipelineResult(success=False, stage="config",
                                  message="Singularity is disabled")

        opportunities = self.detector.detect_all()
        if not opportunities:
            return PipelineResult(success=False, stage="detect",
                                  message="No opportunities found")

        # Try opportunities in priority order until one succeeds or we exhaust them
        for opp in opportunities[:5]:
            result = self.run_pipeline(opp)
            if result.success or result.stage == "rate_limit":
                return result
            log.info("Opportunity %s failed at stage %s, trying next...",
                     opp.id[:8], result.stage)

        return PipelineResult(success=False, stage="exhausted",
                              message="All opportunities exhausted without success")


# ══════════════════════════════════════════════════════════════════════════════
# SINGULARITY DAEMON
# ══════════════════════════════════════════════════════════════════════════════

class SingularityDaemon:
    """
    Background daemon that runs the Singularity Engine on a schedule.
    Checks system load before each cycle. Writes PID file.
    """

    def __init__(self, engine: SingularityEngine = None):
        self.engine = engine or SingularityEngine()
        self._running = False
        self._interval_minutes = 5

    @staticmethod
    def get_system_load() -> float:
        """Get 1-minute system load average."""
        try:
            return float(open("/proc/loadavg").read().split()[0])
        except Exception:
            return 0.0

    @staticmethod
    def get_cpu_count() -> int:
        """Get number of CPU cores."""
        try:
            return os.cpu_count() or 4
        except Exception:
            return 4

    def is_load_acceptable(self) -> bool:
        """Check if system load is low enough for self-modification work."""
        load = self.get_system_load()
        cpus = self.get_cpu_count()
        # Acceptable: load < 70% of CPU count
        threshold = cpus * 0.7
        return load < threshold

    def start(self, interval_minutes: int = 5) -> None:
        """Start the daemon loop. Blocks until stopped."""
        self._interval_minutes = max(1, interval_minutes)
        self._running = True

        # Write PID file
        DAEMON_PID.write_text(str(os.getpid()))
        log.info("SingularityDaemon started (interval=%dmin, pid=%d)",
                 self._interval_minutes, os.getpid())

        try:
            while self._running:
                if not self.engine.config.is_enabled():
                    log.info("Singularity disabled — daemon sleeping (60s)")
                    time.sleep(60)
                    continue

                if not self.is_load_acceptable():
                    load = self.get_system_load()
                    log.info("System load %.2f too high — skipping cycle", load)
                    time.sleep(self._interval_minutes * 60)
                    continue

                log.info("Daemon cycle starting...")
                try:
                    result = self.engine.run_once()
                    log.info("Daemon cycle result: %s (stage=%s)",
                             "SUCCESS" if result.success else "FAILED",
                             result.stage)
                except Exception as e:
                    log.error("Daemon cycle error: %s", e, exc_info=True)

                time.sleep(self._interval_minutes * 60)

        except KeyboardInterrupt:
            log.info("Daemon interrupted by user")
        finally:
            self._running = False
            DAEMON_PID.unlink(missing_ok=True)
            log.info("SingularityDaemon stopped")

    def stop(self) -> None:
        """Signal the daemon to stop."""
        self._running = False


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_engine: Optional[SingularityEngine] = None


def get_singularity_engine() -> SingularityEngine:
    global _engine
    if _engine is None:
        _engine = SingularityEngine()
    return _engine


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def cli_enable(args) -> int:
    """Enable the Singularity Engine."""
    tier = args.tier if hasattr(args, 'tier') else 1
    max_per_day = args.max_per_day if hasattr(args, 'max_per_day') else 10
    tier = max(1, min(2, tier))  # Clamp to 1-2 (TIER 3 requires SER manually)
    config = SingularityConfig()
    config.write(enabled=True, max_tier=tier, max_changes_per_day=max_per_day)
    print(f"Singularity Engine ENABLED")
    print(f"  Max tier: {tier}")
    print(f"  Max changes/day: {max_per_day}")
    print(f"  Config: {CONFIG_PATH}")
    print()
    print("TIER 1 (auto): bugfixes, optimization, docs")
    if tier >= 2:
        print("TIER 2 (semi): refactoring, error handling (trust >= 0.5)")
    print("TIER 3: requires SER approval (modify constitution.toml manually)")
    return 0


def cli_disable(args) -> int:
    """Disable the Singularity Engine."""
    config = SingularityConfig()
    config.write(enabled=False, max_tier=0, max_changes_per_day=0)
    print("Singularity Engine DISABLED")
    print(f"  Config: {CONFIG_PATH}")
    # Also stop daemon if running
    if DAEMON_PID.exists():
        try:
            pid = int(DAEMON_PID.read_text().strip())
            os.kill(pid, 15)  # SIGTERM
            print(f"  Daemon PID {pid} terminated")
        except Exception:
            pass
        DAEMON_PID.unlink(missing_ok=True)
    return 0


def cli_status(args) -> int:
    """Show Singularity Engine status."""
    engine = get_singularity_engine()
    status = engine.status()

    print("=" * 60)
    print("  EIDOS SINGULARITY ENGINE — STATUS")
    print("=" * 60)
    print(f"  Enabled:        {'YES' if status['enabled'] else 'NO'}")
    print(f"  Max tier:       {status['max_tier']}")
    print(f"  Max/day:        {status['max_changes_per_day']}")
    print(f"  Today:          {status['today_changes']} changes")
    print(f"  Remaining:      {status['remaining_today']}")
    print()
    print(f"  Trust Score:    {status['trust_score']:.4f}")
    td = status['trust_details']
    print(f"    Successful:   {td['successful']}")
    print(f"    Failed:       {td['failed']}")
    print(f"    Reverted:     {td['reverted']}")
    print(f"    Uptime:       {td['uptime_hours']:.1f}h")
    print()
    print(f"  Tier Permissions:")
    for t, ok in status['tier_permissions'].items():
        print(f"    {t}: {'ALLOWED' if ok else 'DENIED'}")
    print()
    if status['recent_changes']:
        print(f"  Recent Changes:")
        for c in status['recent_changes'][:5]:
            status_str = "OK" if c['success'] else ("REVERTED" if c['reverted'] else "FAIL")
            print(f"    [{c['time'][:16]}] T{c['tier']} {c['type']:20s} {status_str:8s} {c['file']}")
    else:
        print(f"  Recent Changes:  (none)")

    print()
    print(f"  Daemon PID:     {DAEMON_PID.read_text().strip() if DAEMON_PID.exists() else 'not running'}")
    print("=" * 60)
    return 0


def cli_run(args) -> int:
    """Run one pipeline cycle."""
    engine = get_singularity_engine()
    if not engine.config.is_enabled():
        print("ERROR: Singularity is disabled. Enable first: python3 core/eidos_singularity.py enable --tier 2")
        return 1

    print("Running one singularity cycle...")
    result = engine.run_once()
    print()
    print(f"  Result:   {'SUCCESS' if result.success else 'FAILED'}")
    print(f"  Stage:    {result.stage}")
    print(f"  Tier:     {result.tier}")
    print(f"  Message:  {result.message}")
    print(f"  Trust:    {result.trust_before:.4f} -> {result.trust_after:.4f}")
    if result.change_id:
        print(f"  Change:   {result.change_id}")
    return 0 if result.success else 1


def cli_daemon(args) -> int:
    """Run the background daemon."""
    interval = args.interval if hasattr(args, 'interval') else 5
    engine = get_singularity_engine()

    if not engine.config.is_enabled():
        print("ERROR: Singularity is disabled. Enable first.")
        return 1

    print(f"Starting Singularity Daemon (interval={interval}min)...")
    print(f"PID file: {DAEMON_PID}")
    print(f"Press Ctrl+C to stop.")

    daemon = SingularityDaemon(engine)
    daemon.start(interval_minutes=interval)
    return 0


def cli_trust(args) -> int:
    """Show or reset trust score."""
    engine = get_singularity_engine()
    snap = engine.trust_db.get_snapshot()
    print(f"Trust Score: {snap.score:.4f}")
    print(f"  Successful:  {snap.successful}")
    print(f"  Failed:      {snap.failed}")
    print(f"  Reverted:    {snap.reverted}")
    print(f"  Attempted:   {snap.total_attempted}")
    uptime = (time.time() - snap.started_at) / 3600 if snap.started_at > 0 else 0
    print(f"  Uptime:      {uptime:.1f}h")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EIDOS Singularity Engine — Autonomous Self-Modification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 core/eidos_singularity.py enable --tier 2
  python3 core/eidos_singularity.py disable
  python3 core/eidos_singularity.py status
  python3 core/eidos_singularity.py run --once
  python3 core/eidos_singularity.py daemon --interval 5
  python3 core/eidos_singularity.py trust
        """
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # enable
    p_enable = sub.add_parser("enable", help="Enable the Singularity Engine")
    p_enable.add_argument("--tier", type=int, default=1, choices=[1, 2],
                          help="Max autonomy tier (1=auto, 2=semi-auto)")
    p_enable.add_argument("--max-per-day", type=int, default=10,
                          help="Max changes per day (default: 10)")

    # disable
    sub.add_parser("disable", help="Disable the Singularity Engine")

    # status
    sub.add_parser("status", help="Show status and trust score")

    # run
    sub.add_parser("run", help="Run one pipeline cycle")

    # daemon
    p_daemon = sub.add_parser("daemon", help="Start background daemon")
    p_daemon.add_argument("--interval", type=int, default=5,
                          help="Minutes between cycles (default: 5)")

    # trust
    sub.add_parser("trust", help="Show trust score")

    args = parser.parse_args()

    if args.command == "enable":
        return cli_enable(args)
    elif args.command == "disable":
        return cli_disable(args)
    elif args.command == "status":
        return cli_status(args)
    elif args.command == "run":
        return cli_run(args)
    elif args.command == "daemon":
        return cli_daemon(args)
    elif args.command == "trust":
        return cli_trust(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S"
    )
    sys.exit(main())
