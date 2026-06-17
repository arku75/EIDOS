"""
core/eidos_observational.py — Observational Learning (S125+)
=============================================================
EIDOS learns by WATCHING SER work. Not just perceiving the screen,
but extracting INTENT and PROCEDURES from what it observes.

Five components:
  1. ActionSegmenter  — detect discrete actions SER performs
  2. IntentInferrer   — infer WHY SER did something
  3. RecipeExtractor  — convert observed sequences into procedural recipes
  4. PassiveMode      — background monitoring during SER's active hours
  5. AliveOrchestratorIntegration — wiring into the life cycle

Stores observations in ~/.eidos/observations.jsonl (fast append)
and extracts recipes into eidos_procedural.py (DB-backed).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import sys as _sys
from pathlib import Path as _Path

_EIDOS_ROOT = _Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_EIDOS_ROOT))

log = logging.getLogger("eidos.observational")

# ── Paths ────────────────────────────────────────────────────────────────────

OBS_DIR = Path.home() / ".eidos"
OBS_FILE = OBS_DIR / "observations.jsonl"
OBS_DB = OBS_DIR / "observational.db"
OBS_DIR.mkdir(parents=True, exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE — Store inferred intents and recipe candidates
# ══════════════════════════════════════════════════════════════════════════════

def _ensure_obs_db() -> None:
    from core.db import get_conn
    with get_conn(OBS_DB, timeout=5) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS action_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                action_type TEXT NOT NULL,  -- terminal_cmd, file_open, browser_nav, window_focus, window_change
                raw_data TEXT NOT NULL,      -- JSON with action-specific fields
                window_before TEXT,
                window_after TEXT,
                visible_text_before TEXT,
                visible_text_after TEXT
            );
            CREATE TABLE IF NOT EXISTS inferred_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                action_id INTEGER,
                intent TEXT NOT NULL,        -- "fix_service", "edit_code", "browse_docs", ...
                description TEXT,
                confidence REAL DEFAULT 0.5,
                evidence TEXT,               -- JSON: what led to this inference
                related_action_ids TEXT,     -- JSON list of action IDs in causal chain
                FOREIGN KEY (action_id) REFERENCES action_segments(id)
            );
            CREATE TABLE IF NOT EXISTS recipe_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                steps_json TEXT NOT NULL,
                trigger_words_json TEXT DEFAULT '[]',
                confidence REAL DEFAULT 0.0,
                observation_count INTEGER DEFAULT 0,
                first_seen REAL,
                last_seen REAL,
                status TEXT DEFAULT 'candidate',  -- candidate, extracted, promoted
                promoted_to_recipe_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_segments_ts ON action_segments(ts);
            CREATE INDEX IF NOT EXISTS idx_segments_type ON action_segments(action_type);
            CREATE INDEX IF NOT EXISTS idx_intents_action ON inferred_intents(action_id);
            CREATE INDEX IF NOT EXISTS idx_candidates_status ON recipe_candidates(status);
        """)


_ensure_obs_db()

# ══════════════════════════════════════════════════════════════════════════════
#  1. ACTION SEGMENTER — Detect discrete actions SER performs
# ══════════════════════════════════════════════════════════════════════════════

class ActionSegmenter:
    """Detects discrete actions from screen state changes.

    Watches window titles, terminal output, file paths, and browser URLs
    to segment SER's continuous activity into individual actions.
    """

    def __init__(self):
        self._last_windows: List[Dict] = []
        self._last_focus: str = ""
        self._last_terminal_title: str = ""
        self._last_browser_title: str = ""
        self._last_known_actions: Set[str] = set()  # dedup
        self._shell_log_last_id: int = 0

    # ── Window change detection ────────────────────────────────────────────

    def _get_windows(self) -> List[Dict]:
        """Get current window list via wmctrl."""
        try:
            out = subprocess.check_output(
                ["wmctrl", "-lp"], text=True, timeout=3
            )
        except Exception:
            return []
        windows = []
        for line in out.strip().splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            win_id, desktop, pid, _host, name = parts
            if int(desktop) < 0:
                continue
            windows.append({
                "id": win_id, "desktop": int(desktop),
                "pid": pid, "name": name.strip(),
            })
        return windows

    def _get_active_window(self) -> Optional[Dict]:
        """Get the currently focused window."""
        wins = self._get_windows()
        if not wins:
            return None
        # The first window from wmctrl is often the active one,
        # but let's use xdotool for accuracy
        try:
            wid = subprocess.check_output(
                ["xdotool", "getactivewindow"], text=True, timeout=2
            ).strip()
            for w in wins:
                if w["id"] == wid:
                    return w
        except Exception:
            pass
        return wins[0] if wins else None

    # ── Terminal command detection ──────────────────────────────────────────

    def _detect_terminal_commands(self) -> List[Dict]:
        """Detect recent terminal commands from shell_log if available,
        or infer from terminal window title changes."""
        actions = []

        # Method 1: Shell logger DB (most accurate, if enabled)
        try:
            from core.db import get_conn
            shell_db = Path.home() / ".eidos" / "ser_shell_log.db"
            if shell_db.exists():
                with get_conn(shell_db, timeout=3) as c:
                    rows = c.execute(
                        "SELECT id, ts, cwd, cmd, exit_code, duration_ms "
                        "FROM shell_log WHERE id > ? ORDER BY ts DESC LIMIT 20",
                        (self._shell_log_last_id,)
                    ).fetchall()
                for row in rows:
                    cmd_id, ts, cwd, cmd, exit_code, duration_ms = row
                    if cmd_id > self._shell_log_last_id:
                        self._shell_log_last_id = cmd_id
                    actions.append({
                        "type": "terminal_cmd",
                        "ts": ts,
                        "cmd": cmd,
                        "cwd": cwd,
                        "exit_code": exit_code,
                        "duration_ms": duration_ms,
                        "source": "shell_log",
                    })
                if rows:
                    return actions
        except Exception:
            pass

        # Method 2: Infer from terminal window title changes
        active = self._get_active_window()
        if not active:
            return actions

        name = active["name"]
        # Terminal window titles often show: "user@host: ~/path" or "cmd — Konsole"
        is_terminal = any(kw in name.lower() for kw in
                          ["konsole", "terminal", "tmux", "zsh", "bash"])
        if not is_terminal:
            return actions

        if name != self._last_terminal_title:
            # Extract possible command from title
            # Konsole format: "cmd — Konsole" or "user@host:path — Konsole"
            for sep in [" — ", " - "]:
                if sep in name:
                    cmd_part = name.split(sep)[0].strip()
                    if cmd_part and cmd_part != self._last_terminal_title.split(sep)[0].strip() if self._last_terminal_title else True:
                        actions.append({
                            "type": "terminal_cmd",
                            "ts": time.time(),
                            "cmd": cmd_part,
                            "cwd": "",
                            "source": "title_inference",
                        })
                        self._last_terminal_title = name
                        break

        return actions

    # ── File open detection ─────────────────────────────────────────────────

    def _detect_file_opens(self) -> List[Dict]:
        """Detect files opened in editors from window titles.
        Patterns: 'filename.py — Visual Studio Code', 'file.txt (~/path) — Kate'"""
        actions = []
        active = self._get_active_window()
        if not active:
            return actions

        name = active["name"]
        # Editor keywords in window title
        editor_kws = ["code", "kate", "gedit", "vim", "nvim", "emacs",
                       "visual studio code", "vscode", "sublime", "nano"]
        is_editor = any(kw in name.lower() for kw in editor_kws)
        if not is_editor:
            return actions

        # Extract file path from title
        # Format: "filename.ext — Editor" or "filename.ext (~/path) — Editor"
        for sep in [" — ", " - "]:
            if sep in name:
                file_part = name.split(sep)[0].strip()
                # Check if it looks like a filename (has extension or path)
                if "." in file_part or "/" in file_part or "~" in file_part:
                    # Expand ~
                    expanded = file_part
                    if expanded.startswith("~"):
                        expanded = os.path.expanduser(expanded)
                    # Dedup
                    dedup_key = f"file:{expanded}"
                    if dedup_key not in self._last_known_actions:
                        self._last_known_actions.add(dedup_key)
                        # Determine file type
                        ext = Path(expanded).suffix.lower()
                        actions.append({
                            "type": "file_open",
                            "ts": time.time(),
                            "file_path": expanded,
                            "file_name": Path(expanded).name,
                            "extension": ext,
                            "editor": name.split(sep)[-1].strip() if sep in name else "unknown",
                            "source": "title_parse",
                        })
                break

        return actions

    # ── Browser navigation detection ─────────────────────────────────────────

    def _detect_browser_nav(self) -> List[Dict]:
        """Detect browser page navigations from window title changes.
        Firefox/Chrome windows show page title (or URL in some cases)."""
        actions = []
        active = self._get_active_window()
        if not active:
            return actions

        name = active["name"]
        browser_kws = ["firefox", "chrome", "chromium", "brave", "edge",
                        "mozilla", "browser"]
        is_browser = any(kw in name.lower() for kw in browser_kws)
        if not is_browser:
            return actions

        if name != self._last_browser_title:
            # Extract title (browser format: "Page Title — Mozilla Firefox")
            page_title = name
            for sep in [" — Mozilla", " - Mozilla", " — Google", " - Google",
                         " — Chromium", " - Chromium", " — Firefox", " - Firefox",
                         " — Brave", " - Brave"]:
                if sep in name:
                    page_title = name.split(sep)[0].strip()
                    break

            self._last_browser_title = name
            if page_title:
                # Try to get URL via AT-SPI2 or Playwright if available
                url = ""
                try:
                    from core.perception import HAS_GUI_OBS
                    if HAS_GUI_OBS:
                        from core.gui_observer import get_screen_state
                        state = get_screen_state()
                        if state and state.browser_url:
                            url = state.browser_url
                except Exception:
                    pass

                actions.append({
                    "type": "browser_nav",
                    "ts": time.time(),
                    "page_title": page_title,
                    "url": url,
                    "browser": active.get("name", ""),
                    "source": "title_change",
                })

        return actions

    # ── Window focus change detection ────────────────────────────────────────

    def _detect_focus_changes(self) -> List[Dict]:
        """Detect when SER switches between applications."""
        actions = []
        active = self._get_active_window()
        if not active:
            return actions

        app_name = active["name"]
        if app_name and app_name != self._last_focus:
            old = self._last_focus
            self._last_focus = app_name
            # Classify the app
            app_type = self._classify_app(app_name)
            actions.append({
                "type": "window_focus",
                "ts": time.time(),
                "app_name": app_name,
                "app_type": app_type,
                "previous_app": old,
                "source": "focus_change",
            })

        return actions

    def _classify_app(self, window_title: str) -> str:
        """Classify application type from window title."""
        t = window_title.lower()
        if any(kw in t for kw in ["konsole", "terminal", "tmux", "zsh", "bash"]):
            return "terminal"
        if any(kw in t for kw in ["code", "kate", "gedit", "vim", "nvim",
                                   "emacs", "sublime", "nano", "editor"]):
            return "editor"
        if any(kw in t for kw in ["firefox", "chrome", "chromium", "brave",
                                   "edge", "browser"]):
            return "browser"
        if any(kw in t for kw in ["dolphin", "nautilus", "thunar", "files"]):
            return "file_manager"
        if any(kw in t for kw in ["telegram", "discord", "slack", "signal"]):
            return "messaging"
        if any(kw in t for kw in ["libreoffice", "writer", "calc", "word"]):
            return "office"
        if any(kw in t for kw in ["gimp", "inkscape", "krita", "blender"]):
            return "creative"
        return "other"

    # ── Main segmenter ───────────────────────────────────────────────────────

    def segment(self) -> List[Dict[str, Any]]:
        """Run all segmenters and return detected actions.
        Each action: {type, ts, ...type-specific fields}"""
        all_actions: List[Dict] = []

        # Run segmenters, collecting errors but not stopping
        for detector, label in [
            (self._detect_terminal_commands, "terminal"),
            (self._detect_file_opens, "file_open"),
            (self._detect_browser_nav, "browser"),
            (self._detect_focus_changes, "focus"),
        ]:
            try:
                results = detector()
                all_actions.extend(results)
            except Exception as e:
                log.debug("Segmenter %s error: %s", label, e)

        # Store in DB for persistence
        if all_actions:
            self._store_segments(all_actions)

        return all_actions

    def _store_segments(self, actions: List[Dict]) -> None:
        """Persist segmented actions to DB."""
        try:
            from core.db import get_conn
            with get_conn(OBS_DB, timeout=5) as c:
                for a in actions:
                    c.execute(
                        """INSERT INTO action_segments
                           (ts, action_type, raw_data, window_before, window_after)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            a.get("ts", time.time()),
                            a.get("type", "unknown"),
                            json.dumps(a, ensure_ascii=False),
                            a.get("previous_app", ""),
                            a.get("app_name", a.get("page_title", "")),
                        ),
                    )
        except Exception as e:
            log.debug("_store_segments error: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
#  2. INTENT INFERRER — Understand WHY SER did something
# ══════════════════════════════════════════════════════════════════════════════

class IntentInferrer:
    """Infers the intent behind SER's observed actions.

    Uses heuristic pattern matching on action sequences:
    - Error text -> fix command = "fix problem"
    - Open file -> work on module
    - Browser URL -> research topic
    - Sequential actions -> task context
    """

    def __init__(self):
        self._recent_actions: deque = deque(maxlen=50)
        self._intent_patterns: Dict[str, List[Dict]] = defaultdict(list)
        # Patterns: (precondition, action) -> intent
        self._rules = self._build_rules()

    def _build_rules(self) -> List[Dict]:
        """Build intent inference rules. Each rule has:
        {name, precondition (regex on screen text), action_pattern (regex on cmd),
         intent, description_template, confidence}"""
        return [
            # ── System administration ──────────────────────────────────────
            {
                "name": "fix_service",
                "precondition": r"(connection refused|failed to connect|timeout|service unavailable|502|503|500|refused)",
                "action_pattern": r"(systemctl\s+(re)?start|service\s+\w+\s+(re)?start|sudo\s+systemctl)",
                "intent": "fix_service",
                "template": "SER restarted {service} after seeing '{error_hint}' — connection refused -> restart service",
                "confidence": 0.75,
            },
            {
                "name": "check_status",
                "precondition": r"(not responding|slow|lag|congelado|stuck|freeze)",
                "action_pattern": r"(systemctl\s+status|ps\s+aux|htop|top|free\s+-h|df\s+-h)",
                "intent": "diagnose_system",
                "template": "SER checked system status after noticing '{error_hint}'",
                "confidence": 0.70,
            },
            {
                "name": "install_tool",
                "action_pattern": r"(apt\s+(get\s+)?install|pip\s+(install|3\s+install)|npm\s+install|pnpm\s+install|cargo\s+install|gem\s+install)",
                "intent": "install_tool",
                "template": "SER installed a package: {cmd_hint}",
                "confidence": 0.85,
            },
            {
                "name": "update_system",
                "action_pattern": r"(apt\s+(get\s+)?(update|upgrade|dist-upgrade)|pacman\s+-Syu|snap\s+refresh|flatpak\s+update)",
                "intent": "update_system",
                "template": "SER updated the system",
                "confidence": 0.80,
            },
            # ── Development ────────────────────────────────────────────────
            {
                "name": "dev_python",
                "precondition": r"(\.py)[\"\s]",
                "action_pattern": r"(python[3]?\s+\S+\.py|python[3]?\s+-m\s+\S+)",
                "intent": "run_python",
                "template": "SER ran Python script: {cmd_hint}",
                "confidence": 0.80,
            },
            {
                "name": "dev_git",
                "action_pattern": r"(git\s+(commit|push|pull|merge|rebase|checkout|branch|status|diff|log|add|stash))",
                "intent": "version_control",
                "template": "SER used git: {cmd_hint}",
                "confidence": 0.80,
            },
            # ── File/Code work ─────────────────────────────────────────────
            {
                "name": "edit_code",
                "action_pattern": r".*",  # matches any file_open with code extension
                "intent": "edit_code",
                "template": "SER is working on file: {file}",
                "confidence": 0.60,
                "requires_action_type": "file_open",
                "extensions": [".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp",
                              ".h", ".java", ".rb", ".sh", ".zsh", ".toml",
                              ".yaml", ".yml", ".json", ".html", ".css", ".vue",
                              ".svelte"],
            },
            # ── Research/Browsing ──────────────────────────────────────────
            {
                "name": "browse_docs",
                "action_pattern": r"(docs\.|documentation|readthedocs|man\s+pages|wiki|arch\s+wiki)",
                "intent": "research_docs",
                "template": "SER is reading documentation: {page_title}",
                "confidence": 0.65,
                "requires_action_type": "browser_nav",
            },
            {
                "name": "browse_search",
                "action_pattern": r".*",
                "intent": "web_search",
                "template": "SER is browsing: {page_title}",
                "confidence": 0.50,
                "requires_action_type": "browser_nav",
            },
            # ── Project work detection ─────────────────────────────────────
            {
                "name": "working_on_eidos",
                "action_pattern": r"(EIDOS|eidos|/home/ser/EIDOS)",
                "intent": "work_eidos",
                "template": "SER is working on EIDOS project",
                "confidence": 0.70,
            },
        ]

    def infer(self, action: Dict[str, Any],
              context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Infer intent for a single action.

        Args:
            action: {type, ts, ...} from ActionSegmenter
            context: {windows_open, visible_text, active_window, ...}

        Returns: {intent, description, confidence, evidence} or None
        """
        action_type = action.get("type", "")
        self._recent_actions.append(action)

        for rule in self._rules:
            # Check action type filter
            if "requires_action_type" in rule:
                if action_type != rule["requires_action_type"]:
                    continue

            # Check file extension filter
            if "extensions" in rule and action_type == "file_open":
                ext = action.get("extension", "").lower()
                if ext not in rule["extensions"]:
                    continue

            # Check action pattern
            action_text = self._action_to_text(action)
            if not re.search(rule["action_pattern"], action_text, re.IGNORECASE):
                continue

            # Check precondition (screen context) if rule has one
            matched_precond = ""
            if "precondition" in rule and context:
                visible = context.get("visible_text", "")
                m = re.search(rule["precondition"], visible, re.IGNORECASE)
                if m:
                    matched_precond = m.group(1) if m.lastindex else m.group(0)
                elif action_type != "file_open":
                    # For non-file actions, precondition in screen text is required
                    continue

            # Build the result
            error_hint = matched_precond[:60] if matched_precond else ""
            cmd_hint = self._action_to_text(action)[:80]

            # Extract service name if applicable
            service = ""
            if rule["intent"] == "fix_service" and action_type == "terminal_cmd":
                svc_match = re.search(r"(systemctl\s+(re)?start|service)\s+(\S+)",
                                     action.get("cmd", ""))
                if svc_match:
                    service = svc_match.group(3)

            description = rule["template"].format(
                service=service,
                error_hint=error_hint,
                cmd_hint=cmd_hint,
                file=action.get("file_path", action.get("file_name", "")),
                page_title=action.get("page_title", ""),
            )

            result = {
                "intent": rule["intent"],
                "description": description,
                "confidence": rule["confidence"],
                "evidence": {
                    "rule": rule["name"],
                    "action_type": action_type,
                    "action_summary": cmd_hint,
                    "context_match": error_hint,
                },
                "ts": action.get("ts", time.time()),
            }

            # Track pattern for learning
            pattern_key = rule["intent"]
            self._intent_patterns[pattern_key].append({
                "ts": result["ts"],
                "action": action,
                "intent": result,
            })

            # Store in DB
            self._store_intent(action, result)

            return result

        return None

    def _action_to_text(self, action: Dict) -> str:
        """Convert an action to searchable text."""
        parts = []
        if action.get("type") == "terminal_cmd":
            parts.append(action.get("cmd", ""))
            parts.append(action.get("cwd", ""))
        elif action.get("type") == "file_open":
            parts.append(action.get("file_path", ""))
            parts.append(action.get("file_name", ""))
        elif action.get("type") == "browser_nav":
            parts.append(action.get("page_title", ""))
            parts.append(action.get("url", ""))
        elif action.get("type") == "window_focus":
            parts.append(action.get("app_name", ""))
        return " ".join(parts)

    def _store_intent(self, action: Dict, intent: Dict) -> None:
        """Persist inferred intent to DB."""
        try:
            from core.db import get_conn
            with get_conn(OBS_DB, timeout=5) as c:
                c.execute(
                    """INSERT INTO inferred_intents
                       (ts, intent, description, confidence, evidence)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        intent.get("ts", time.time()),
                        intent["intent"],
                        intent["description"],
                        intent["confidence"],
                        json.dumps(intent.get("evidence", {}), ensure_ascii=False),
                    ),
                )
        except Exception as e:
            log.debug("_store_intent error: %s", e)

    def get_recent_intents(self, n: int = 10) -> List[Dict]:
        """Get the most recently inferred intents."""
        try:
            from core.db import get_conn
            with get_conn(OBS_DB, timeout=5) as c:
                rows = c.execute(
                    "SELECT ts, intent, description, confidence "
                    "FROM inferred_intents ORDER BY ts DESC LIMIT ?",
                    (n,)
                ).fetchall()
            return [
                {"ts": r[0], "intent": r[1], "description": r[2],
                 "confidence": r[3]}
                for r in rows
            ]
        except Exception:
            return []

    def intent_summary(self) -> str:
        """Human-readable summary of recent intents inferred."""
        intents = self.get_recent_intents(5)
        if not intents:
            return "No intents inferred yet."
        lines = []
        for i in intents:
            lines.append(
                f"[{datetime.fromtimestamp(i['ts']).strftime('%H:%M:%S')}] "
                f"{i['intent']}: {i['description'][:100]} "
                f"(confidence: {i['confidence']:.0%})"
            )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  3. RECIPE EXTRACTOR — Convert observed sequences into procedural recipes
# ══════════════════════════════════════════════════════════════════════════════

class RecipeExtractor:
    """Watches SER repeat a task 2-3 times and extracts a reusable recipe.

    Uses sequence alignment across multiple observations of the same task
    to identify the invariant steps vs variable parameters.
    """

    def __init__(self):
        self._task_sequences: Dict[str, List[List[Dict]]] = defaultdict(list)
        # Keyed by intent/action group, value = list of observation sequences
        self._min_observations = 2    # need at least 2 observations
        self._confidence_threshold = 0.65  # 0.65 = 2 obs for simple patterns, 0.7+ for complex
        self._min_step_length = 2     # recipes need at least 2 steps

    def observe_sequence(self, actions: List[Dict],
                         intent: Optional[str] = None) -> Optional[Dict]:
        """Feed an action sequence to the extractor.

        Args:
            actions: List of [{type, ts, cmd, file_path, ...}] from segmenter
            intent: Inferred intent label for grouping

        Returns: Extracted recipe if confidence > threshold, else None
        """
        if len(actions) < self._min_step_length:
            return None

        # Group by task type
        task_key = intent or self._derive_task_key(actions)
        self._task_sequences[task_key].append(actions)

        # Keep only recent observations (last 20 per task)
        if len(self._task_sequences[task_key]) > 20:
            self._task_sequences[task_key] = \
                self._task_sequences[task_key][-20:]

        # Need at least 2 sequences to extract pattern
        seqs = self._task_sequences[task_key]
        if len(seqs) < self._min_observations:
            return None

        return self._extract_recipe(task_key, seqs)

    def _derive_task_key(self, actions: List[Dict]) -> str:
        """Derive a task grouping key from action types and commands."""
        types = [a.get("type", "?") for a in actions]
        # Include first command as fingerprint
        first_cmd = ""
        for a in actions:
            if a.get("cmd"):
                # Normalize: remove specific args/values
                first_cmd = re.sub(r'\b\d+\b', 'N', a["cmd"])
                first_cmd = re.sub(r'\b[\w.-]+@[\w.-]+\b', 'HOST', first_cmd)
                first_cmd = re.sub(r'(/[^\s]+)', '/PATH', first_cmd)
                first_cmd = first_cmd[:40]
                break
        return f"{'->'.join(types)}|{first_cmd}"

    def _extract_recipe(self, task_key: str,
                        sequences: List[List[Dict]]) -> Optional[Dict]:
        """Extract a recipe from multiple observation sequences.

        Uses simplified sequence alignment:
        1. Align steps across sequences by action type
        2. Find invariant parts (keep as literal steps)
        3. Find variable parts (replace with variables like {target}, {file})
        """
        # Align sequences by action type
        aligned = self._align_sequences(sequences)
        if not aligned or len(aligned) < self._min_step_length:
            return None

        # Generalize: find what varies across sequences
        generalized_steps = []
        confidence_scores = []
        for i, step_group in enumerate(aligned):
            step_template, variability = self._generalize_step(step_group)
            if step_template is None:
                # Step type inconsistent across sequences - skip
                break
            generalized_steps.append(step_template)
            confidence_scores.append(1.0 - variability)

        if len(generalized_steps) < self._min_step_length:
            return None

        # Overall confidence
        confidence = sum(confidence_scores) / len(confidence_scores)

        if confidence < self._confidence_threshold:
            return None

        # Build recipe name
        name = self._generate_recipe_name(task_key, generalized_steps)

        # Build trigger words
        trigger_words = self._generate_triggers(task_key, generalized_steps)

        recipe = {
            "name": name,
            "steps": generalized_steps,
            "trigger_words": trigger_words,
            "confidence": round(confidence, 2),
            "observation_count": len(sequences),
            "learned_from": "observation",
            "task_key": task_key,
        }

        # Store candidate
        self._store_candidate(recipe)
        return recipe

    def _align_sequences(self, sequences: List[List[Dict]]) -> List[List[List[Dict]]]:
        """Align steps across sequences by position and action type.
        Returns list of step groups, each group = same step across observations."""
        max_len = max(len(s) for s in sequences)
        aligned = []
        for i in range(max_len):
            group = []
            for seq in sequences:
                if i < len(seq):
                    group.append(seq[i])
            if group:
                # Only include if action types are consistent
                types = set(a.get("type", "") for a in group)
                if len(types) == 1:
                    aligned.append(group)
                elif len(group) >= len(sequences) * 0.6:
                    # Majority agree on type
                    dominant_type = max(set(t for a in group
                                           if (t := a.get("type", ""))),
                                       key=lambda t: sum(1 for a in group
                                                        if a.get("type") == t))
                    aligned.append([a for a in group
                                   if a.get("type") == dominant_type])
        return aligned

    def _generalize_step(self, step_group: List[Dict]) -> Tuple[Optional[Dict], float]:
        """Generalize a step group: find template and variability score.

        Returns (step_template_dict, variability 0-1 where 0 = all identical)."""
        if not step_group:
            return None, 1.0

        action_type = step_group[0].get("type", "")

        if action_type == "terminal_cmd":
            return self._generalize_terminal_cmd(step_group)
        elif action_type == "file_open":
            return self._generalize_file_open(step_group)
        elif action_type == "browser_nav":
            return self._generalize_browser_nav(step_group)
        elif action_type == "window_focus":
            return self._generalize_window_focus(step_group)
        else:
            return None, 1.0

    def _generalize_terminal_cmd(self, group: List[Dict]) -> Tuple[Dict, float]:
        """Generalize terminal commands: replace specific values with variables."""
        cmds = [a.get("cmd", "") for a in group]
        if not cmds or not all(cmds):
            return None, 1.0

        # Find common prefix (the command itself, e.g., "systemctl restart")
        words_sets = [c.split() for c in cmds]
        min_words = min(len(ws) for ws in words_sets)
        common_prefix = []
        for i in range(min_words):
            words_at_pos = set(ws[i] for ws in words_sets if i < len(ws))
            if len(words_at_pos) == 1:
                common_prefix.append(words_sets[0][i])
            else:
                break

        # Build generalized command with variables
        if common_prefix:
            cmd_base = " ".join(common_prefix)
            # Replace varying parts with variables
            var_positions = len(common_prefix)
            variables = []
            for i, ws in enumerate(words_sets):
                if len(ws) > var_positions:
                    var_part = " ".join(ws[var_positions:])
                    variables.append(var_part)

            if variables:
                # Check if they look like service names, file paths, etc.
                var_name = self._infer_variable_name(variables)
                cmd_template = f"{cmd_base} {{{var_name}}}"
            else:
                cmd_template = cmd_base
                var_name = ""

            variability = 1.0 - (len(common_prefix) / max(len(ws) for ws in words_sets))
        else:
            # No common structure at all
            cmd_template = cmds[0]
            variability = 0.5

        return {
            "action": "terminal",
            "cmd": cmd_template,
            "description": f"Ejecutar: {cmd_template}",
        }, variability

    def _generalize_file_open(self, group: List[Dict]) -> Tuple[Dict, float]:
        """Generalize file opens: find common directory or file pattern."""
        paths = [a.get("file_path", "") for a in group]
        extensions = set(a.get("extension", "") for a in group)

        # Find common ancestor directory
        common_dir = os.path.commonpath(paths) if all(paths) else ""
        variability = 0.0 if len(set(paths)) == 1 else 0.5

        if common_dir and common_dir != "/":
            return {
                "action": "filesystem",
                "cmd": "open",
                "target": f"{{{common_dir}/<file>}}",
                "description": f"Abrir archivo en {common_dir}",
            }, variability
        elif extensions and len(extensions) == 1:
            ext = list(extensions)[0]
            return {
                "action": "filesystem",
                "cmd": "open",
                "target": "{file_path}",
                "description": f"Abrir archivo {ext} en editor",
            }, 0.3
        else:
            return {
                "action": "filesystem",
                "cmd": "open",
                "target": "{file_path}",
                "description": "Abrir archivo en editor",
            }, 0.7

    def _generalize_browser_nav(self, group: List[Dict]) -> Tuple[Dict, float]:
        """Generalize browser navigations."""
        urls = [a.get("url", "") for a in group if a.get("url")]
        titles = [a.get("page_title", "") for a in group]
        variability = 0.3 if len(set(titles)) <= 2 else 0.8

        if urls:
            # Find common URL prefix
            common = os.path.commonprefix(urls)
            if common and len(common) > 10:
                return {
                    "action": "browse",
                    "target": f"{common}{{path}}",
                    "description": f"Navegar a {common}...",
                }, variability

        return {
            "action": "browse",
            "target": "{url_or_search}",
            "description": "Navegar a página web",
        }, 0.9

    def _generalize_window_focus(self, group: List[Dict]) -> Tuple[Dict, float]:
        """Generalize window focus changes."""
        apps = [a.get("app_name", "") for a in group]
        variability = 0.0 if len(set(apps)) == 1 else 0.7
        return {
            "action": "focus",
            "target": apps[0] if apps else "{app}",
            "description": f"Cambiar a {apps[0]}" if apps else "Cambiar aplicación",
        }, variability

    def _infer_variable_name(self, values: List[str]) -> str:
        """Infer a variable name from example values."""
        if not values:
            return "arg"
        # Check if they look like hostnames/IPs
        if all(re.match(r'^[\d.]+$', v) or re.match(r'^[\w.-]+\.[a-z]{2,}$', v)
               for v in values):
            return "target"
        # Check if they look like file paths
        if all('/' in v or '~' in v or v.endswith(('.py', '.sh', '.txt'))
               for v in values):
            return "file"
        # Check if they look like service names
        if all(re.match(r'^[a-z][a-z0-9_-]*$', v) for v in values):
            return "service"
        # Check if they look like package names
        if all(re.match(r'^[a-z][a-z0-9_.-]*$', v) for v in values):
            return "package"
        return "arg"

    def _generate_recipe_name(self, task_key: str,
                              steps: List[Dict]) -> str:
        """Generate a human-readable recipe name."""
        action_types = [s.get("action", "?") for s in steps]
        if "terminal" in action_types:
            for s in steps:
                if s.get("action") == "terminal":
                    cmd = s.get("cmd", "")
                    # Clean up the command for display
                    cmd_clean = re.sub(r'\{[^}]+\}', '...', cmd)
                    return f"Ejecutar: {cmd_clean[:60]}"
        if "browse" in action_types:
            return f"Navegar a sitio web"
        if "filesystem" in action_types:
            return f"Abrir y editar archivos"
        return f"Tarea: {task_key[:50]}"

    def _generate_triggers(self, task_key: str,
                           steps: List[Dict]) -> List[str]:
        """Generate trigger words for recipe recall."""
        triggers = set()
        for s in steps:
            cmd = s.get("cmd", "")
            target = s.get("target", "")
            desc = s.get("description", "")
            # Extract meaningful words
            for text in [cmd, target, desc]:
                words = re.findall(r'[a-zA-Z]{3,}', text)
                triggers.update(w.lower() for w in words[:3])
        return list(triggers)[:8]

    def _store_candidate(self, recipe: Dict) -> None:
        """Store recipe candidate in DB for later promotion."""
        try:
            from core.db import get_conn
            with get_conn(OBS_DB, timeout=5) as c:
                c.execute(
                    """INSERT INTO recipe_candidates
                       (name, steps_json, trigger_words_json, confidence,
                        observation_count, first_seen, last_seen, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate')""",
                    (
                        recipe["name"],
                        json.dumps(recipe["steps"], ensure_ascii=False),
                        json.dumps(recipe.get("trigger_words", []), ensure_ascii=False),
                        recipe["confidence"],
                        recipe.get("observation_count", 0),
                        time.time(),
                        time.time(),
                    ),
                )
        except Exception as e:
            log.debug("_store_candidate error: %s", e)

    def promote_candidates(self, min_confidence: float = 0.7) -> List[Dict]:
        """Promote recipe candidates to full recipes in eidos_procedural."""
        promoted = []
        try:
            from core.db import get_conn
            with get_conn(OBS_DB, timeout=5) as c:
                candidates = c.execute(
                    """SELECT id, name, steps_json, trigger_words_json, confidence,
                       observation_count FROM recipe_candidates
                       WHERE status='candidate' AND confidence >= ?
                       ORDER BY confidence DESC""",
                    (min_confidence,)
                ).fetchall()

            from core.eidos_procedural import record_recipe

            for cand in candidates:
                cand_id, name, steps_json, triggers_json, conf, obs_count = cand
                steps = json.loads(steps_json)
                triggers = json.loads(triggers_json)

                result = record_recipe(
                    name=name,
                    steps=steps,
                    description=f"Aprendido observando a SER ({obs_count} observaciones)",
                    trigger_words=triggers,
                    learned_from="observation",
                    tags=["observed", "auto_extracted"],
                )

                if result.get("ok"):
                    with get_conn(OBS_DB, timeout=5) as c2:
                        c2.execute(
                            """UPDATE recipe_candidates
                               SET status='promoted', promoted_to_recipe_id=?
                               WHERE id=?""",
                            (result.get("recipe_id", 0), cand_id),
                        )
                    promoted.append({
                        "candidate_id": cand_id,
                        "recipe_id": result.get("recipe_id"),
                        "name": name,
                        "confidence": conf,
                    })

        except Exception as e:
            log.error("promote_candidates error: %s", e)
        return promoted

    def get_candidate_stats(self) -> Dict:
        """Get statistics on recipe candidates."""
        try:
            from core.db import get_conn
            with get_conn(OBS_DB, timeout=5) as c:
                total = c.execute(
                    "SELECT COUNT(*) FROM recipe_candidates"
                ).fetchone()[0]
                ready = c.execute(
                    "SELECT COUNT(*) FROM recipe_candidates WHERE confidence >= 0.7 AND status='candidate'"
                ).fetchone()[0]
                promoted_count = c.execute(
                    "SELECT COUNT(*) FROM recipe_candidates WHERE status='promoted'"
                ).fetchone()[0]
                return {"total_candidates": total, "ready": ready,
                        "promoted": promoted_count}
        except Exception:
            return {"total_candidates": 0, "ready": 0, "promoted": 0}


# ══════════════════════════════════════════════════════════════════════════════
#  4. PASSIVE MODE — Background monitoring during SER's active hours
# ══════════════════════════════════════════════════════════════════════════════

class PassiveObserver:
    """Runs in background during SER's active hours.

    Monitors screen changes, logs observations to JSONL, and
    periodically attempts recipe extraction when confidence is high enough.
    Auto-promotes high-confidence candidates (>= 0.7) to procedural memory
    without waiting for manual intervention.
    """

    def __init__(self, poll_interval: float = 5.0,
                 recipe_extract_interval: float = 300.0):
        self.poll_interval = poll_interval           # seconds between screen checks
        self.recipe_extract_interval = recipe_extract_interval  # seconds between extraction attempts
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._segmenter = ActionSegmenter()
        self._inferrer = IntentInferrer()
        self._extractor = RecipeExtractor()
        self._last_extract_time: float = 0
        self._observation_count: int = 0
        self._session_start: float = 0
        self._current_sequence: List[Dict] = []  # actions being accumulated
        self._sequence_timeout: float = 30.0      # seconds before sequence is "done"
        self._last_action_time: float = 0
        self._last_promote_time: float = 0        # last time promote_candidates was called
        self._promote_interval: float = 60.0      # auto-promote check every 60s

    def _log_observation(self, obs: Dict) -> None:
        """Append an observation to the JSONL file."""
        try:
            obs["session_id"] = self._session_start
            obs["seq_num"] = self._observation_count
            with open(OBS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(obs, ensure_ascii=False) + "\n")
            self._observation_count += 1
        except Exception as e:
            log.debug("Failed to write observation: %s", e)

    def _get_screen_context(self) -> Dict[str, Any]:
        """Get current screen context for observation."""
        try:
            from core.screen_scanner import get_screen_context
            return get_screen_context()
        except Exception:
            return {}

    def _tick(self) -> Dict[str, Any]:
        """One observation tick: segment, infer, log, accumulate.

        Sequence boundary detection and recipe extraction run even when
        no actions are detected this tick (timeout-based finalization).
        Auto-promotion runs periodically regardless of action activity.
        """
        actions = self._segmenter.segment()
        context = self._get_screen_context()
        tick_result = {
            "ts": time.time(),
            "actions_detected": len(actions),
            "actions": actions,
            "intents_inferred": [],
            "recipe_extracted": None,
            "promoted_recipes": [],
        }

        if actions:
            self._last_action_time = time.time()

            # Infer intents
            for action in actions:
                intent = self._inferrer.infer(action, context)
                if intent:
                    tick_result["intents_inferred"].append(intent)
                    self._log_observation({
                        "type": "intent",
                        "ts": intent["ts"],
                        "intent": intent["intent"],
                        "description": intent["description"],
                        "confidence": intent["confidence"],
                    })

                # Log raw action
                self._log_observation({
                    "type": "action",
                    "ts": action.get("ts", time.time()),
                    "action_type": action.get("type", ""),
                    "details": action,
                })

            # Accumulate into current sequence
            self._current_sequence.extend(actions)

        # ── Sequence boundary detection (runs even without new actions) ──
        # If no actions have been detected for _sequence_timeout seconds
        # and we have enough accumulated actions, finalize the sequence.
        time_since_last = time.time() - self._last_action_time
        if (self._last_action_time > 0
                and time_since_last >= self._sequence_timeout
                and len(self._current_sequence) >= 2):
            self._log_observation({
                "type": "sequence_boundary",
                "ts": time.time(),
                "sequence_length": len(self._current_sequence),
            })
            # Try recipe extraction
            now = time.time()
            if now - self._last_extract_time >= self.recipe_extract_interval:
                self._last_extract_time = now
                recent_intent = tick_result["intents_inferred"][0]["intent"] \
                    if tick_result["intents_inferred"] else None
                recipe = self._extractor.observe_sequence(
                    list(self._current_sequence), intent=recent_intent
                )
                if recipe and recipe["confidence"] >= 0.7:
                    tick_result["recipe_extracted"] = recipe
                    self._log_observation({
                        "type": "recipe_extracted",
                        "ts": time.time(),
                        "recipe_name": recipe["name"],
                        "confidence": recipe["confidence"],
                        "steps": recipe["steps"],
                    })
                    # Auto-promote immediately when a high-confidence recipe is extracted
                    promoted = self._extractor.promote_candidates(min_confidence=0.7)
                    for p in promoted:
                        log.info("Auto-promoted recipe: %s (confidence=%.2f, recipe_id=%s)",
                                 p['name'], p['confidence'], p.get('recipe_id'))
                self._current_sequence = []  # reset for next sequence

        # ── Periodic auto-promotion (runs every tick, cheap DB check) ─────
        now = time.time()
        if now - self._last_promote_time >= self._promote_interval:
            self._last_promote_time = now
            try:
                promoted = self._extractor.promote_candidates(min_confidence=0.7)
                if promoted:
                    for p in promoted:
                        log.info("Auto-promoted recipe: %s (confidence=%.2f, recipe_id=%s)",
                                 p['name'], p['confidence'], p.get('recipe_id'))
                        tick_result["promoted_recipes"].append(p)
            except Exception as e:
                log.debug("auto-promote check error: %s", e)

        return tick_result

    def _passive_loop(self) -> None:
        """Main passive observation loop."""
        while self._running:
            try:
                self._tick()
            except Exception as e:
                log.error("PassiveObserver tick error: %s", e)
            time.sleep(self.poll_interval)

    def start(self) -> None:
        """Start passive observation."""
        if self._running:
            return
        self._running = True
        self._session_start = time.time()
        self._thread = threading.Thread(target=self._passive_loop, daemon=True)
        self._thread.start()
        log.info("PassiveObserver started (poll=%.1fs, extract=%.0fs)",
                 self.poll_interval, self.recipe_extract_interval)

    def stop(self) -> None:
        """Stop passive observation."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        log.info("PassiveObserver stopped (%d observations logged)",
                 self._observation_count)

    def is_running(self) -> bool:
        return self._running

    def stats(self) -> Dict[str, Any]:
        """Get current observation session stats."""
        return {
            "running": self._running,
            "observations_logged": self._observation_count,
            "session_duration_s": time.time() - self._session_start if self._session_start else 0,
            "current_sequence_len": len(self._current_sequence),
            "candidates": self._extractor.get_candidate_stats(),
            "recent_intents": self._inferrer.intent_summary(),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  5. ALIVE ORCHESTRATOR INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

def observe_cycle(ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One observation cycle for integration into AliveOrchestrator.

    This is designed to be called from AliveOrchestrator.perceive() or
    as a separate optional phase. It:
      1. Segments actions from current screen state
      2. Infers intent behind detected actions
      3. Accumulates for recipe extraction
      4. Returns observation context for the orchestrator
      5. Periodically auto-promotes mature recipe candidates (confidence >= 0.7)

    Args:
        ctx: Screen context from perceive() (optional, will get fresh if None)

    Returns:
        {actions_detected, intents_inferred, observations, recipe_candidate}
    """
    segmenter = _get_shared_segmenter()
    inferrer = _get_shared_inferrer()

    if ctx is None:
        try:
            from core.screen_scanner import get_screen_context
            ctx = get_screen_context()
        except Exception:
            ctx = {}

    actions = segmenter.segment()
    intents = []
    for action in actions:
        intent = inferrer.infer(action, ctx)
        if intent:
            intents.append(intent)

    # Log to JSONL if any activity detected
    if actions:
        _log_cycle_observation(actions, intents, ctx)

    # ── Periodic auto-promotion from observe_cycle (orchestrator path) ─────
    global _last_cycle_promote
    now = time.time()
    if now - _last_cycle_promote >= _CYCLE_PROMOTE_INTERVAL:
        _last_cycle_promote = now
        try:
            extractor = _get_shared_extractor()
            promoted = extractor.promote_candidates(min_confidence=0.7)
            if promoted:
                for p in promoted:
                    log.info("observe_cycle auto-promoted: %s (confidence=%.2f)", p['name'], p['confidence'])
        except Exception:
            pass

    return {
        "actions_detected": len(actions),
        "actions": [
            {"type": a.get("type"), "cmd": a.get("cmd", "")[:60],
             "file": a.get("file_path", "")[:60],
             "page": a.get("page_title", "")[:60]}
            for a in actions
        ],
        "intents_inferred": len(intents),
        "intents": [
            {"intent": i["intent"], "description": i["description"],
             "confidence": i["confidence"]}
            for i in intents
        ],
        "active_window": ctx.get("active_window", ""),
        "windows_open": len(ctx.get("windows_open", [])),
    }


# ── Shared singletons (avoid re-creating per cycle) ───────────────────────

_segmenter: Optional[ActionSegmenter] = None
_inferrer: Optional[IntentInferrer] = None
_passive: Optional[PassiveObserver] = None
_extractor: Optional[RecipeExtractor] = None
_last_cycle_promote: float = 0
_CYCLE_PROMOTE_INTERVAL: float = 120.0  # promote check every 2 min from observe_cycle()


def _get_shared_segmenter() -> ActionSegmenter:
    global _segmenter
    if _segmenter is None:
        _segmenter = ActionSegmenter()
    return _segmenter


def _get_shared_inferrer() -> IntentInferrer:
    global _inferrer
    if _inferrer is None:
        _inferrer = IntentInferrer()
    return _inferrer


def _get_shared_extractor() -> RecipeExtractor:
    global _extractor
    if _extractor is None:
        _extractor = RecipeExtractor()
    return _extractor


def _log_cycle_observation(actions: List[Dict],
                           intents: List[Dict],
                           ctx: Dict[str, Any]) -> None:
    """Log a cycle's observations to JSONL."""
    try:
        record = {
            "ts": time.time(),
            "iso": datetime.now().isoformat(),
            "actions_count": len(actions),
            "actions": [
                {"type": a.get("type"), "ts": a.get("ts"),
                 "summary": _action_summary(a)}
                for a in actions
            ],
            "intents": [
                {"intent": i["intent"], "confidence": i["confidence"]}
                for i in intents
            ],
            "active_window": ctx.get("active_window", ""),
        }
        with open(OBS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _action_summary(action: Dict) -> str:
    """Short human-readable summary of an action."""
    t = action.get("type", "")
    if t == "terminal_cmd":
        return action.get("cmd", "")[:100]
    elif t == "file_open":
        return f"open: {action.get('file_path', '')}"
    elif t == "browser_nav":
        return f"browse: {action.get('page_title', '')[:100]}"
    elif t == "window_focus":
        return f"focus: {action.get('app_name', '')}"
    return str(action)[:100]


# ── Public API ────────────────────────────────────────────────────────────────

def get_passive_observer(poll_interval: float = 5.0) -> PassiveObserver:
    """Get or create the singleton PassiveObserver."""
    global _passive
    if _passive is None:
        _passive = PassiveObserver(poll_interval=poll_interval)
    return _passive


def start_observing(poll_interval: float = 5.0) -> PassiveObserver:
    """Start passive observational learning. Convenience function."""
    obs = get_passive_observer(poll_interval)
    obs.start()
    return obs


def stop_observing() -> None:
    """Stop passive observational learning."""
    global _passive
    if _passive:
        _passive.stop()


def extract_recipes_from_history(lookback_hours: int = 24) -> List[Dict]:
    """Extract recipes from historical observations (batch mode).

    Reads ~/.eidos/observations.jsonl, groups actions into sequences,
    and runs the recipe extractor on them.
    """
    if not OBS_FILE.exists():
        log.info("No observations file found at %s", OBS_FILE)
        return []

    cutoff = time.time() - (lookback_hours * 3600)
    actions: List[Dict] = []
    try:
        with open(OBS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("ts", 0) >= cutoff:
                        if entry.get("type") == "action":
                            actions.append(entry.get("details", entry))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.error("Error reading observations: %s", e)
        return []

    if len(actions) < 4:
        return []

    # Group into sequences by time gaps (>60s gap = new sequence)
    sequences = []
    current_seq = []
    for a in sorted(actions, key=lambda x: x.get("ts", 0)):
        if current_seq and a.get("ts", 0) - current_seq[-1].get("ts", 0) > 60:
            if len(current_seq) >= 2:
                sequences.append(current_seq)
            current_seq = []
        current_seq.append(a)
    if len(current_seq) >= 2:
        sequences.append(current_seq)

    # Run extractor on each sequence group
    extractor = RecipeExtractor()
    recipes = []
    for seq in sequences:
        recipe = extractor.observe_sequence(seq)
        if recipe:
            recipes.append(recipe)

    # Promote high-confidence candidates
    promoted = extractor.promote_candidates(min_confidence=0.7)

    log.info("extract_recipes_from_history: %d sequences, %d recipes, "
             "%d promoted", len(sequences), len(recipes), len(promoted))
    return recipes


def observational_status() -> Dict[str, Any]:
    """Get comprehensive status of the observational learning system."""
    status = {
        "passive_running": False,
        "observations_file": str(OBS_FILE),
        "observations_file_exists": OBS_FILE.exists(),
        "observations_file_size_mb": 0,
    }
    if OBS_FILE.exists():
        status["observations_file_size_mb"] = round(
            OBS_FILE.stat().st_size / (1024 * 1024), 2
        )

    if _passive:
        status.update(_passive.stats())

    # DB stats
    try:
        from core.db import get_conn
        with get_conn(OBS_DB, timeout=5) as c:
            status["segments_stored"] = c.execute(
                "SELECT COUNT(*) FROM action_segments"
            ).fetchone()[0]
            status["intents_stored"] = c.execute(
                "SELECT COUNT(*) FROM inferred_intents"
            ).fetchone()[0]
            status["recipe_candidates"] = c.execute(
                "SELECT COUNT(*) FROM recipe_candidates"
            ).fetchone()[0]
            status["recipes_promoted"] = c.execute(
                "SELECT COUNT(*) FROM recipe_candidates WHERE status='promoted'"
            ).fetchone()[0]
    except Exception:
        status["db_error"] = True

    return status


# ══════════════════════════════════════════════════════════════════════════════
#  6. SER PATTERN TRACKER — Build personal knowledge base of SER's habits
# ══════════════════════════════════════════════════════════════════════════════

class SerPatternTracker:
    """Tracks SER's patterns over time to build a personal knowledge base.

    Analyzes observational data to identify:
      - Frequently used commands and their context
      - Preferred tools and editors for different tasks
      - Active hours and work session patterns
      - Common workflows (sequences of actions SER repeats)
      - File/language affinities (what SER works on most)

    Stores patterns as high-confidence knowledge nodes (source='ser_pattern')
    so the brain graph learns SER's behavior.
    """

    def __init__(self):
        self._patterns_db = Path.home() / ".eidos" / "ser_patterns.db"

    def _ensure_patterns_db(self):
        """Ensure the patterns database exists."""
        from core.db import get_conn
        with get_conn(self._patterns_db, timeout=5) as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS ser_commands (
                    cmd_base TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 1,
                    last_used REAL,
                    common_args TEXT,
                    context_category TEXT
                );
                CREATE TABLE IF NOT EXISTS ser_tools (
                    tool_name TEXT PRIMARY KEY,
                    tool_type TEXT,
                    sessions_used INTEGER DEFAULT 1,
                    last_used REAL,
                    avg_duration_minutes REAL
                );
                CREATE TABLE IF NOT EXISTS ser_hours (
                    hour INTEGER,
                    activity_count INTEGER DEFAULT 1,
                    avg_idle_seconds REAL,
                    PRIMARY KEY (hour)
                );
                CREATE TABLE IF NOT EXISTS ser_workflows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    steps_json TEXT,
                    frequency INTEGER DEFAULT 1,
                    last_observed REAL,
                    confidence REAL DEFAULT 0.5
                );
                CREATE INDEX IF NOT EXISTS idx_cmds_last ON ser_commands(last_used);
                CREATE INDEX IF NOT EXISTS idx_tools_last ON ser_tools(last_used);
            """)

    def track_from_observations(self, lookback_hours: int = 168) -> Dict[str, Any]:
        """Analyze observation data and extract SER patterns.

        Returns summary of patterns found.
        """
        self._ensure_patterns_db()
        summary = {
            "top_commands": [],
            "top_tools": [],
            "active_hours": [],
            "workflows_detected": 0,
            "patterns_persisted": 0,
        }

        # Read observations
        if not OBS_FILE.exists():
            return summary

        cutoff = time.time() - (lookback_hours * 3600)
        actions: List[Dict] = []
        try:
            with open(OBS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", 0) >= cutoff:
                            if entry.get("type") == "action":
                                actions.append(entry.get("details", entry))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return summary

        if len(actions) < 10:
            return summary

        # ── Command frequency analysis ──────────────────────────────────────
        cmd_counts: Dict[str, List[Dict]] = defaultdict(list)
        for a in actions:
            if a.get("type") == "terminal_cmd":
                cmd = a.get("cmd", "")
                if cmd:
                    # Normalize: extract base command
                    base = cmd.strip().split()[0] if cmd.strip() else ""
                    if base and len(base) > 1:
                        cmd_counts[base].append(a)

        from core.db import get_conn
        with get_conn(self._patterns_db, timeout=5) as c:
            for base, instances in sorted(cmd_counts.items(),
                                         key=lambda x: len(x[1]), reverse=True)[:10]:
                if len(instances) >= 2:
                    most_recent = max(a.get("ts", 0) for a in instances)
                    common_args = self._extract_common_args([a.get("cmd", "") for a in instances])
                    context = self._infer_command_context(base, instances)
                    c.execute(
                        """INSERT OR REPLACE INTO ser_commands
                           (cmd_base, count, last_used, common_args, context_category)
                           VALUES (?, ?, ?, ?, ?)""",
                        (base, len(instances), most_recent,
                         common_args, context)
                    )
                    summary["top_commands"].append({
                        "cmd": base,
                        "count": len(instances),
                        "context": context,
                    })

        # ── Tool/editor preference analysis ─────────────────────────────────
        tool_sessions: Dict[str, List[Dict]] = defaultdict(list)
        for a in actions:
            tool_name = ""
            tool_type = ""
            if a.get("type") == "file_open":
                tool_name = a.get("editor", "")
                tool_type = "editor"
            elif a.get("type") == "browser_nav":
                tool_name = a.get("browser", "")
                tool_type = "browser"
            elif a.get("type") == "window_focus":
                tool_name = a.get("app_name", "")
                tool_type = a.get("app_type", "other")
            if tool_name:
                tool_sessions[tool_name].append(a)

        with get_conn(self._patterns_db, timeout=5) as c:
            for tool, instances in sorted(tool_sessions.items(),
                                         key=lambda x: len(x[1]), reverse=True)[:8]:
                if len(instances) >= 3:
                    tool_type = instances[0].get("app_type",
                                                instances[0].get("editor", "unknown"))
                    c.execute(
                        """INSERT OR REPLACE INTO ser_tools
                           (tool_name, tool_type, sessions_used, last_used)
                           VALUES (?, ?, ?, ?)""",
                        (tool[:100], tool_type, len(instances),
                         max(a.get("ts", 0) for a in instances))
                    )
                    summary["top_tools"].append({
                        "tool": tool[:60],
                        "type": tool_type,
                        "sessions": len(instances),
                    })

        # ── Active hours analysis ───────────────────────────────────────────
        hour_activity: Dict[int, int] = defaultdict(int)
        for a in actions:
            ts = a.get("ts", 0)
            if ts > 0:
                hour = datetime.fromtimestamp(ts).hour
                hour_activity[hour] += 1

        with get_conn(self._patterns_db, timeout=5) as c:
            for hour, count in sorted(hour_activity.items()):
                c.execute(
                    """INSERT OR REPLACE INTO ser_hours
                       (hour, activity_count) VALUES (?, ?)""",
                    (hour, count)
                )
        summary["active_hours"] = sorted(hour_activity.items(),
                                        key=lambda x: x[1], reverse=True)[:5]

        # ── Persist patterns as knowledge nodes ─────────────────────────────
        summary["patterns_persisted"] = self._persist_patterns_to_brain(summary)

        return summary

    def _extract_common_args(self, commands: List[str]) -> str:
        """Extract commonly repeated arguments from command instances."""
        from collections import Counter
        all_args = []
        for cmd in commands:
            parts = cmd.strip().split()
            if len(parts) > 1:
                all_args.extend(parts[1:])
        if all_args:
            top = Counter(all_args).most_common(3)
            return ", ".join(f"{arg}({count}x)" for arg, count in top)
        return ""

    def _infer_command_context(self, base_cmd: str,
                               instances: List[Dict]) -> str:
        """Infer the context category for a command."""
        # Context from what was observed before/after
        sys_admin = {"systemctl", "service", "journalctl", "apt", "pacman",
                     "docker", "kubectl", "ps", "kill", "top", "free", "df"}
        dev = {"python", "python3", "node", "npm", "pnpm", "cargo", "go",
               "gcc", "g++", "make", "cmake", "rustc"}
        git_ops = {"git", "gh"}
        net = {"curl", "wget", "nc", "ssh", "scp", "ping", "nmap", "ss", "netstat"}
        files = {"ls", "cd", "find", "grep", "cat", "less", "tail", "head",
                "cp", "mv", "mkdir", "rm", "chmod", "chown"}

        if base_cmd in sys_admin:
            return "system_administration"
        elif base_cmd in dev:
            return "development"
        elif base_cmd in git_ops:
            return "version_control"
        elif base_cmd in net:
            return "networking"
        elif base_cmd in files:
            return "file_management"
        return "general"

    def _persist_patterns_to_brain(self, summary: Dict) -> int:
        """Persist SER patterns as high-confidence knowledge nodes."""
        persisted = 0
        try:
            brain_db = Path.home() / ".eidos" / "evolution_brain.db"
            if not brain_db.exists():
                return 0

            from core.db import get_conn

            with get_conn(brain_db, timeout=10) as c:
                # Top commands
                for cmd_info in summary.get("top_commands", [])[:5]:
                    concept = f"ser_pattern: uses '{cmd_info['cmd']}' frequently"
                    definition = (
                        f"SER frequently uses the command '{cmd_info['cmd']}' "
                        f"({cmd_info['count']}x observed). "
                        f"Context: {cmd_info.get('context', 'general')}. "
                        f"This is a core tool in SER's workflow."
                    )
                    import uuid
                    nid = "serpat_" + uuid.uuid4().hex[:12]
                    c.execute(
                        """INSERT OR REPLACE INTO knowledge_nodes
                           (id, concept, definition, category, confidence, source, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (nid, concept, definition, "ser_pattern",
                         0.85, "ser_pattern_tracker",
                         time.strftime("%Y-%m-%d %H:%M:%S"))
                    )
                    persisted += 1

                # Top tools
                for tool_info in summary.get("top_tools", [])[:5]:
                    concept = f"ser_pattern: prefers '{tool_info['tool']}' ({tool_info['type']})"
                    definition = (
                        f"SER frequently uses {tool_info['tool']} "
                        f"({tool_info['sessions']} sessions observed). "
                        f"Type: {tool_info['type']}. This is a preferred tool."
                    )
                    nid = "serpat_" + uuid.uuid4().hex[:12]
                    c.execute(
                        """INSERT OR REPLACE INTO knowledge_nodes
                           (id, concept, definition, category, confidence, source, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (nid, concept, definition, "ser_pattern",
                         0.80, "ser_pattern_tracker",
                         time.strftime("%Y-%m-%d %H:%M:%S"))
                    )
                    persisted += 1

                c.commit()

        except Exception as e:
            log.debug("persist_patterns_to_brain: %s", e)

        return persisted

    def get_ser_profile(self) -> Dict[str, Any]:
        """Get a comprehensive profile of SER's patterns."""
        self._ensure_patterns_db()
        profile = {
            "generated_at": datetime.now().isoformat(),
            "top_commands": [],
            "top_tools": [],
            "active_hours": [],
            "dominant_context": "unknown",
        }

        try:
            from core.db import get_conn
            with get_conn(self._patterns_db, timeout=5) as c:
                # Top commands
                rows = c.execute(
                    "SELECT cmd_base, count, context_category FROM ser_commands "
                    "ORDER BY count DESC LIMIT 10"
                ).fetchall()
                profile["top_commands"] = [
                    {"cmd": r[0], "count": r[1], "context": r[2]}
                    for r in rows
                ]

                # Top tools
                rows = c.execute(
                    "SELECT tool_name, tool_type, sessions_used FROM ser_tools "
                    "ORDER BY sessions_used DESC LIMIT 8"
                ).fetchall()
                profile["top_tools"] = [
                    {"tool": r[0], "type": r[1], "sessions": r[2]}
                    for r in rows
                ]

                # Active hours
                rows = c.execute(
                    "SELECT hour, activity_count FROM ser_hours "
                    "ORDER BY activity_count DESC LIMIT 6"
                ).fetchall()
                profile["active_hours"] = [
                    {"hour": r[0], "count": r[1]}
                    for r in rows
                ]

                # Dominant context
                row = c.execute(
                    "SELECT context_category, SUM(count) as total FROM ser_commands "
                    "GROUP BY context_category ORDER BY total DESC LIMIT 1"
                ).fetchone()
                if row:
                    profile["dominant_context"] = row[0]
        except Exception as e:
            log.debug("get_ser_profile: %s", e)

        return profile

    def get_ser_knowledge_summary(self) -> str:
        """Human-readable summary of what EIDOS knows about SER."""
        profile = self.get_ser_profile()
        lines = ["SER'S PATTERNS (learned by EIDOS)", "=" * 40]

        if profile["top_commands"]:
            lines.append("\nMost used commands:")
            for c in profile["top_commands"][:5]:
                lines.append(f"  {c['cmd']} ({c['count']}x) — {c['context']}")

        if profile["top_tools"]:
            lines.append("\nPreferred tools:")
            for t in profile["top_tools"][:5]:
                lines.append(f"  {t['tool']} ({t['type']}, {t['sessions']} sessions)")

        if profile["active_hours"]:
            hours_str = ", ".join(f"{h['hour']}h({h['count']})"
                                 for h in profile["active_hours"][:5])
            lines.append(f"\nActive hours: {hours_str}")

        if profile["dominant_context"] != "unknown":
            lines.append(f"\nDominant context: {profile['dominant_context']}")

        if len(lines) == 2:
            return "Not enough observational data yet to build SER's profile."

        return "\n".join(lines)


# Shared singleton
_pattern_tracker: Optional[SerPatternTracker] = None


def get_pattern_tracker() -> SerPatternTracker:
    global _pattern_tracker
    if _pattern_tracker is None:
        _pattern_tracker = SerPatternTracker()
    return _pattern_tracker


def analyze_ser_patterns(lookback_hours: int = 168) -> Dict[str, Any]:
    """Analyze SER patterns from recent observations. Convenience function."""
    return get_pattern_tracker().track_from_observations(lookback_hours)


def get_ser_profile() -> Dict[str, Any]:
    """Get SER's learned profile."""
    return get_pattern_tracker().get_ser_profile()


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-8s | %(message)s")

    if len(sys.argv) < 2:
        print("Uso: python eidos_observational.py <cmd> [args]")
        print("  segment    — segment actions from current screen")
        print("  infer      — infer intent from current context")
        print("  passive    — start passive observation mode (Ctrl+C to stop)")
        print("  extract    — extract recipes from history (last 24h)")
        print("  status     — show observational learning status")
        print("  promote    — promote high-confidence candidates to recipes")
        print("  ser_patterns — analyze SER patterns from observations")
        print("  ser_profile  — show learned SER profile")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "segment":
        seg = ActionSegmenter()
        actions = seg.segment()
        print(f"Detected {len(actions)} actions:")
        for a in actions:
            print(f"  [{a.get('type')}] {_action_summary(a)}")

    elif cmd == "infer":
        seg = ActionSegmenter()
        inf = IntentInferrer()
        from core.screen_scanner import get_screen_context
        ctx = get_screen_context()
        actions = seg.segment()
        print(f"Actions: {len(actions)}, Context: {ctx.get('active_window', '?')[:60]}")
        for a in actions:
            intent = inf.infer(a, ctx)
            if intent:
                print(f"  -> {intent['intent']}: {intent['description']} "
                      f"(confidence: {intent['confidence']:.0%})")
            else:
                print(f"  [{a.get('type')}] no intent inferred")
        print(inf.intent_summary())

    elif cmd == "passive":
        print("Starting passive observation mode...")
        obs = start_observing(poll_interval=5.0)
        try:
            while True:
                time.sleep(30)
                s = obs.stats()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                      f"Observations: {s['observations_logged']}, "
                      f"Candidates: {s['candidates']}")
        except KeyboardInterrupt:
            obs.stop()
            print("\nStopped.")

    elif cmd == "extract":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 24
        print(f"Extracting recipes from last {hours}h of observations...")
        recipes = extract_recipes_from_history(lookback_hours=hours)
        print(f"Found {len(recipes)} recipes:")
        for r in recipes:
            print(f"  - {r['name']} (confidence: {r['confidence']}, "
                  f"observations: {r.get('observation_count', '?'):})")
        # Also promote
        extractor = RecipeExtractor()
        promoted = extractor.promote_candidates()
        if promoted:
            print(f"Promoted {len(promoted)} candidates to procedural memory:")
            for p in promoted:
                print(f"  - {p['name']} -> recipe_id={p['recipe_id']}")

    elif cmd == "status":
        status = observational_status()
        print(json.dumps(status, indent=2, ensure_ascii=False))

    elif cmd == "promote":
        extractor = RecipeExtractor()
        promoted = extractor.promote_candidates()
        if promoted:
            print(f"Promoted {len(promoted)} candidates:")
            for p in promoted:
                print(f"  - {p['name']} -> id={p['recipe_id']} "
                      f"(confidence: {p['confidence']})")
        else:
            print("No candidates ready for promotion (need confidence >= 0.7)")

    elif cmd == "ser_patterns":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 168
        print(f"Analyzing SER patterns from last {hours}h of observations...")
        result = analyze_ser_patterns(lookback_hours=hours)
        print(f"Top commands: {json.dumps(result.get('top_commands', []), indent=2)}")
        print(f"Top tools: {json.dumps(result.get('top_tools', []), indent=2)}")
        print(f"Active hours: {result.get('active_hours', [])}")
        print(f"Patterns persisted to brain: {result.get('patterns_persisted', 0)}")

    elif cmd == "ser_profile":
        tracker = get_pattern_tracker()
        print(tracker.get_ser_knowledge_summary())

    else:
        print(f"Unknown command: {cmd}")
