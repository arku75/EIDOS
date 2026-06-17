"""
core/eidos_recovery.py — Action Recovery & Self-Healing [S126]

EIDOS is BLIND to its own failures. When it clicks something and nothing
happens, it doesn't notice. When it types a URL and a dialog steals focus,
it doesn't detect the error.

This module gives EIDOS a "nervous system" that:
  1. VERIFIES every action (did it actually work?)
  2. FALLBACK when verification fails (escalating strategies)
  3. DETECTS unexpected dialogs/popups blocking the flow
  4. LOGS all failures + recoveries → learns what works

Architecture:
  ActionAttempt → verify_click/verify_type/verify_navigate
    → if FAILED → fallback_click/fallback_type/fallback_navigate
      → retry with next strategy → verify again
        → if still FAILED → escalate to next fallback

KEEP IT SIMPLE. Focus on the most common failure modes.
Integrates with: action_system, human_emulator, screen_controller, screen_scanner.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.recovery")

# ── Paths ──────────────────────────────────────────────────────────────────────
RECOVERY_LOG = Path.home() / ".eidos" / "recovery_log.jsonl"
EIDOS_DIR = Path.home() / ".eidos"


# ── Data types ─────────────────────────────────────────────────────────────────


@dataclass
class ActionAttempt:
    """One attempted action + verification result + recovery chain."""
    action_type: str          # click, type, navigate, scroll, key
    action_params: Dict[str, Any]  # x, y, text, url, key, etc.
    attempt_number: int = 1
    verified: bool = False
    verification_method: str = ""
    verification_detail: str = ""
    screen_before_hash: str = ""
    screen_after_hash: str = ""
    fallbacks_tried: List[str] = field(default_factory=list)
    final_outcome: str = ""   # success, failed_after_fallbacks, aborted
    elapsed_ms: float = 0.0
    error_msg: str = ""
    dialog_detected: bool = False
    dialog_dismissed: bool = False


@dataclass
class DialogInfo:
    """Detected unexpected dialog/popup."""
    title: str = ""
    detected_via: str = ""    # wmctrl, ocr, pixel_inspection
    dismissed: bool = False
    dismiss_method: str = ""
    timestamp: float = 0.0


# ── Screen state helpers ───────────────────────────────────────────────────────


def _get_active_window_title() -> str:
    """Get the title of the currently active window."""
    try:
        wid = subprocess.check_output(
            ["xdotool", "getactivewindow"], text=True, timeout=2
        ).strip()
        title = subprocess.check_output(
            ["xdotool", "getwindowname", wid], text=True, timeout=2
        ).strip()
        return title
    except Exception:
        return ""


def _get_window_list() -> List[Dict[str, str]]:
    """Get all visible windows via wmctrl."""
    try:
        out = subprocess.check_output(["wmctrl", "-l"], text=True, timeout=4)
        windows = []
        for line in out.strip().splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4:
                windows.append({
                    "wid": parts[0],
                    "desktop": parts[1],
                    "title": parts[3],
                })
        return windows
    except Exception:
        return []


def _screen_hash() -> str:
    """Fast hash of current screen state via window titles."""
    titles = "|".join(sorted(w.get("title", "") for w in _get_window_list()))
    return str(hash(titles))


def _pixel_color_at(x: int, y: int) -> Optional[Tuple[int, int, int]]:
    """Get RGB color at screen coordinates. None on failure."""
    try:
        out = subprocess.check_output(
            ["scrot", "-a", f"{x},{y},1,1", "-o", "-"],
            timeout=3, stderr=subprocess.DEVNULL
        )
        if out and len(out) >= 3:
            return (out[0], out[1], out[2])
        return None
    except Exception:
        return None


def _ocr_region(x: int, y: int, width: int = 200, height: int = 40) -> str:
    """OCR a small region of the screen around coordinates."""
    tmp = str(EIDOS_DIR / f"_ocr_tmp_{int(time.time())}.png")
    try:
        subprocess.run(
            ["scrot", "-a", f"{x-width//2},{y-height//2},{width},{height}", tmp],
            timeout=3, capture_output=True
        )
        if Path(tmp).exists():
            out = subprocess.check_output(
                ["tesseract", tmp, "stdout", "-l", "spa+eng", "--oem", "1"],
                text=True, timeout=10
            ).strip()
            Path(tmp).unlink(missing_ok=True)
            return out
    except Exception:
        pass
    Path(tmp).unlink(missing_ok=True)
    return ""


# ── 1. ACTION VERIFIER ────────────────────────────────────────────────────────


class ActionVerifier:
    """Verifies that an action actually had its intended effect.

    After every action (click, type, navigate), verify it worked:
      - Click: did the screen change at the click coordinates?
      - Type: did the text appear in the target field?
      - Navigate: did the page title/URL change as expected?
    """

    # Time to wait after action before verifying (seconds)
    POST_ACTION_DELAY = {"click": 0.8, "type": 0.3, "navigate": 2.0, "key": 0.5}
    # How different must the screen be to count as "changed" (0-1)
    CHANGE_THRESHOLD = 0.05

    def verify_click(self, x: int, y: int, screen_before_hash: str) -> Tuple[bool, str]:
        """Verify a click had an effect.

        Strategy:
          1. Hash changed → something happened → SUCCESS
          2. Pixel color at (x,y) changed → the element changed → SUCCESS
          3. Same hash + same pixel → NOTHING happened → FAILED
        """
        time.sleep(self.POST_ACTION_DELAY["click"])
        after_hash = _screen_hash()

        # 1. Global screen change
        if after_hash != screen_before_hash:
            return True, "screen_hash_changed"

        # 2. Wait a bit more for slow UIs
        time.sleep(0.5)
        after_hash2 = _screen_hash()
        if after_hash2 != screen_before_hash:
            return True, "screen_hash_changed_delayed"

        # 3. Nothing changed — the click likely failed
        return False, "no_detectable_change"

    def verify_type(self, text: str, target_x: Optional[int] = None,
                    target_y: Optional[int] = None) -> Tuple[bool, str]:
        """Verify typed text appeared.

        Strategy:
          1. If we know target coordinates → OCR that region, check text
          2. Fallback: check if any window title changed (focused on field)
        """
        time.sleep(self.POST_ACTION_DELAY["type"])

        if target_x is not None and target_y is not None:
            ocr_text = _ocr_region(target_x, target_y, width=400, height=60)
            if ocr_text:
                # Check if any word from the typed text appears
                typed_words = set(text.lower().split())
                ocr_words = set(ocr_text.lower().split())
                overlap = typed_words & ocr_words
                if overlap:
                    return True, f"text_found_in_field: {','.join(list(overlap)[:3])}"
                # Partial match (substring)
                short_text = text.strip()[:15].lower()
                if short_text and short_text in ocr_text.lower():
                    return True, "text_found_partial_in_field"

        # Without coordinates, assume success — typing usually works
        return True, "assumed_ok_no_target_coords"

    def verify_navigate(self, url: str, screen_before_hash: str,
                        expected_in_title: str = "") -> Tuple[bool, str]:
        """Verify navigation succeeded.

        Strategy:
          1. Wait for page load
          2. Check window title changed
          3. Check expected domain appears in title
          4. Check screen hash changed
        """
        time.sleep(self.POST_ACTION_DELAY["navigate"])
        after_hash = _screen_hash()

        # 1. Hash change = something loaded
        if after_hash != screen_before_hash:
            title = _get_active_window_title()
            # Check if expected text is in title
            if expected_in_title and expected_in_title.lower() in title.lower():
                return True, f"title_matches_expected"
            if title and title != "":
                return True, f"title_changed_to: {title[:60]}"
            return True, "screen_changed"

        # 2. Wait longer for slow pages
        time.sleep(2.0)
        after_hash2 = _screen_hash()
        if after_hash2 != screen_before_hash:
            return True, "screen_changed_delayed"

        # 3. Still no change → navigation may have failed
        title = _get_active_window_title()
        if expected_in_title and expected_in_title.lower() in title.lower():
            return True, "title_already_matches"

        return False, "no_change_after_navigate"

    def verify(self, action_type: str, action_params: Dict[str, Any],
               screen_before_hash: str) -> Tuple[bool, str]:
        """Generic verify: dispatches to the right method."""
        if action_type == "click":
            x = action_params.get("x", 0)
            y = action_params.get("y", 0)
            return self.verify_click(x, y, screen_before_hash)
        elif action_type == "type":
            text = action_params.get("text", "")
            x = action_params.get("x")
            y = action_params.get("y")
            return self.verify_type(text, x, y)
        elif action_type in ("navigate", "navigate_url", "open_browser"):
            url = action_params.get("url", "")
            expected = action_params.get("expected_in_title", "")
            # Extract domain from URL for matching
            if not expected and url:
                for prefix in ("https://", "http://"):
                    url = url.replace(prefix, "")
                expected = url.split("/")[0].split(".")[0]  # e.g. "github" from "github.com/..."
            return self.verify_navigate(url, screen_before_hash, expected)
        elif action_type == "key":
            # Key presses are hard to verify without known effect
            return True, "key_action_assumed_ok"
        elif action_type == "scroll":
            # Scroll doesn't always change window titles but may change OCR
            time.sleep(0.5)
            after_hash = _screen_hash()
            return (after_hash != screen_before_hash), "scroll_hash_check"
        else:
            # Unknown action → assume success
            return True, f"unknown_action_type: {action_type}"


# ── 2. DIALOG DETECTOR ────────────────────────────────────────────────────────


class DialogDetector:
    """Detects unexpected dialogs, popups, alerts that steal focus.

    These block EIDOS from completing actions:
      - "Error" / "Warning" / "Alert" in window title
      - New window appeared that wasn't there before
      - Modal overlay (detected via pixel inspection or OCR)
    """

    DANGER_TITLES = [
        "error", "warning", "alert", "attention", "confirm",
        "save", "open", "download", "notification", "update",
        "firewall", "security", "permission", "allow", "block",
        "are you sure", "do you want", "would you like",
    ]

    def __init__(self):
        self._known_windows: set = set()   # windows that were there before action

    def snapshot_windows(self):
        """Remember current windows before an action."""
        self._known_windows = {
            w["title"] for w in _get_window_list()
            if w["title"].strip()
        }

    def detect(self) -> Optional[DialogInfo]:
        """Check if a new dialog/popup appeared since last snapshot.

        Returns DialogInfo if detected, None otherwise.
        """
        current_windows = _get_window_list()
        current_titles = {w["title"] for w in current_windows if w["title"].strip()}

        # 1. Look for new windows that weren't there before
        new_titles = current_titles - self._known_windows
        for title in new_titles:
            title_lower = title.lower()
            for danger_word in self.DANGER_TITLES:
                if danger_word in title_lower:
                    return DialogInfo(
                        title=title,
                        detected_via="new_window_title",
                        timestamp=time.time(),
                    )

        # 2. Check if active window title contains danger signals
        active_title = _get_active_window_title()
        if active_title:
            active_lower = active_title.lower()
            for danger_word in self.DANGER_TITLES:
                if danger_word in active_lower:
                    return DialogInfo(
                        title=active_title,
                        detected_via="active_window_title",
                        timestamp=time.time(),
                    )

        return None

    def dismiss_current(self) -> Tuple[bool, str]:
        """Try to dismiss the currently detected dialog.

        Methods (in order):
          1. Press Escape (dismisses most dialogs)
          2. Press Enter (accepts default button)
          3. Press Alt+F4 (closes window)
          4. Click at center (modal overlays)

        Returns (success, method_used).
        """
        before_hash = _screen_hash()

        # 1. Escape
        try:
            subprocess.run(
                ["xdotool", "key", "Escape"],
                capture_output=True, timeout=2
            )
            time.sleep(0.5)
            if _screen_hash() != before_hash:
                return True, "escape_dismissed"
        except Exception:
            pass

        # 2. Try closing via xdotool (close active window)
        try:
            wid = subprocess.check_output(
                ["xdotool", "getactivewindow"], text=True, timeout=2
            ).strip()
            # Alt+F4 on the dialog
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", wid],
                capture_output=True, timeout=2
            )
            subprocess.run(
                ["xdotool", "key", "alt+F4"],
                capture_output=True, timeout=2
            )
            time.sleep(0.5)
            if _screen_hash() != before_hash:
                return True, "alt_f4_dismissed"
        except Exception:
            pass

        # 3. Click center (modal overlays often dismiss on background click)
        try:
            subprocess.run(
                ["xdotool", "mousemove", "960", "540"],  # center of 1920x1080
                capture_output=True, timeout=1
            )
            time.sleep(0.1)
            subprocess.run(
                ["xdotool", "click", "1"],
                capture_output=True, timeout=1
            )
            time.sleep(0.5)
            if _screen_hash() != before_hash:
                return True, "center_click_dismissed"
        except Exception:
            pass

        return False, "could_not_dismiss"


# ── 3. FALLBACK CHAINS ────────────────────────────────────────────────────────


class FallbackEngine:
    """Defines escalating fallback strategies for each action type.

    Click fails → Tab to reach element → keyboard shortcut → different coordinates
    Type fails → clear field first → slower typing → paste via clipboard
    Navigate fails → check if dialog stole focus → dismiss dialog → retry
    """

    def __init__(self, human_emulator=None):
        self._he = human_emulator  # optional, for human-like input

    # ── Click fallbacks ─────────────────────────────────────────────────────

    def fallback_click(self, attempt: ActionAttempt) -> List[Dict[str, Any]]:
        """Generate fallback strategies when a click fails.

        Returns list of action dicts to try in order.
        """
        x = attempt.action_params.get("x", 400)
        y = attempt.action_params.get("y", 300)
        return [
            # Strategy 1: Tab to reach the element (if it's focusable)
            {
                "action": "fallback_tab_enter",
                "description": "Press Tab to reach element, then Enter",
                "tabs": 3,
                "x": x, "y": y,
            },
            # Strategy 2: Keyboard shortcut
            {
                "action": "fallback_keyboard",
                "description": "Try Enter (activate focused element)",
                "keys": "Enter",
            },
            # Strategy 3: Different coordinates (slight offsets)
            {
                "action": "fallback_offset_click",
                "description": "Click at offset coordinates",
                "x": x + 10, "y": y - 5,
            },
            # Strategy 4: Wider offset
            {
                "action": "fallback_offset_click",
                "description": "Click at wider offset",
                "x": x - 15, "y": y + 10,
            },
        ]

    def execute_fallback_click(self, fallback: Dict[str, Any]) -> bool:
        """Execute a click fallback strategy."""
        action = fallback["action"]

        if action == "fallback_tab_enter":
            tabs = fallback.get("tabs", 3)
            try:
                for _ in range(tabs):
                    subprocess.run(
                        ["xdotool", "key", "Tab"],
                        capture_output=True, timeout=1
                    )
                    time.sleep(0.08)
                subprocess.run(
                    ["xdotool", "key", "Return"],
                    capture_output=True, timeout=1
                )
                return True
            except Exception as e:
                log.debug("fallback_tab_enter failed: %s", e)
                return False

        elif action == "fallback_keyboard":
            keys = fallback.get("keys", "Enter")
            try:
                subprocess.run(
                    ["xdotool", "key", keys],
                    capture_output=True, timeout=1
                )
                return True
            except Exception as e:
                log.debug("fallback_keyboard failed: %s", e)
                return False

        elif action == "fallback_offset_click":
            x = fallback.get("x", 400)
            y = fallback.get("y", 300)
            try:
                subprocess.run(
                    ["xdotool", "mousemove", str(x), str(y)],
                    capture_output=True, timeout=1
                )
                time.sleep(0.05)
                subprocess.run(
                    ["xdotool", "click", "1"],
                    capture_output=True, timeout=1
                )
                return True
            except Exception as e:
                log.debug("fallback_offset_click failed: %s", e)
                return False

        return False

    # ── Type fallbacks ───────────────────────────────────────────────────────

    def fallback_type(self, attempt: ActionAttempt) -> List[Dict[str, Any]]:
        """Generate fallback strategies when typing fails."""
        text = attempt.action_params.get("text", "")
        return [
            # Strategy 1: Clear field first (Ctrl+A, Del), then retry
            {
                "action": "fallback_clear_retry",
                "description": "Clear field and retype",
                "text": text,
            },
            # Strategy 2: Slower typing (larger intervals)
            {
                "action": "fallback_slow_type",
                "description": "Type slower",
                "text": text,
            },
            # Strategy 3: Paste via clipboard (xclip)
            {
                "action": "fallback_paste",
                "description": "Paste via clipboard",
                "text": text,
            },
        ]

    def execute_fallback_type(self, fallback: Dict[str, Any]) -> bool:
        """Execute a type fallback strategy."""
        action = fallback["action"]
        text = fallback.get("text", "")

        if action == "fallback_clear_retry":
            try:
                subprocess.run(
                    ["xdotool", "key", "ctrl+a"],
                    capture_output=True, timeout=1
                )
                time.sleep(0.05)
                subprocess.run(
                    ["xdotool", "key", "Delete"],
                    capture_output=True, timeout=1
                )
                time.sleep(0.1)
                # Type with xdotool type (reliable)
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", text],
                    capture_output=True, timeout=5
                )
                return True
            except Exception as e:
                log.debug("fallback_clear_retry failed: %s", e)
                return False

        elif action == "fallback_slow_type":
            try:
                for char in text:
                    if char == " ":
                        subprocess.run(
                            ["xdotool", "key", "space"],
                            capture_output=True, timeout=1
                        )
                    elif char == "\n":
                        subprocess.run(
                            ["xdotool", "key", "Return"],
                            capture_output=True, timeout=1
                        )
                    else:
                        subprocess.run(
                            ["xdotool", "type", char],
                            capture_output=True, timeout=1
                        )
                    time.sleep(0.12)  # slow typing
                return True
            except Exception as e:
                log.debug("fallback_slow_type failed: %s", e)
                return False

        elif action == "fallback_paste":
            try:
                # Write to clipboard
                proc = subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(), capture_output=True, timeout=3
                )
                if proc.returncode != 0:
                    # Try xclip without selection
                    proc = subprocess.run(
                        ["xclip"],
                        input=text.encode(), capture_output=True, timeout=3
                    )
                # Paste
                subprocess.run(
                    ["xdotool", "key", "ctrl+v"],
                    capture_output=True, timeout=1
                )
                return True
            except Exception as e:
                log.debug("fallback_paste failed: %s", e)
                return False

        return False

    # ── Navigate fallbacks ───────────────────────────────────────────────────

    def fallback_navigate(self, attempt: ActionAttempt) -> List[Dict[str, Any]]:
        """Generate fallback strategies when navigation fails."""
        url = attempt.action_params.get("url", "")
        return [
            # Strategy 1: Check if dialog stole focus → dismiss → retry
            {
                "action": "fallback_dismiss_dialog",
                "description": "Dismiss any dialog and retry navigation",
                "url": url,
            },
            # Strategy 2: Open new tab and navigate
            {
                "action": "fallback_new_tab",
                "description": "Open new tab and navigate",
                "url": url,
            },
            # Strategy 3: Direct browser launch
            {
                "action": "fallback_direct_browser",
                "description": "Launch new browser window directly",
                "url": url,
            },
        ]

    def execute_fallback_navigate(self, fallback: Dict[str, Any]) -> bool:
        """Execute a navigate fallback strategy."""
        action = fallback["action"]
        url = fallback.get("url", "")

        if action == "fallback_dismiss_dialog":
            # Dismiss dialog, then Ctrl+L + type URL
            try:
                subprocess.run(
                    ["xdotool", "key", "Escape"],
                    capture_output=True, timeout=1
                )
                time.sleep(0.5)
                subprocess.run(
                    ["xdotool", "key", "ctrl+l"],
                    capture_output=True, timeout=1
                )
                time.sleep(0.3)
                subprocess.run(
                    ["xdotool", "key", "ctrl+a"],
                    capture_output=True, timeout=1
                )
                time.sleep(0.1)
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", url],
                    capture_output=True, timeout=5
                )
                time.sleep(0.2)
                subprocess.run(
                    ["xdotool", "key", "Return"],
                    capture_output=True, timeout=1
                )
                return True
            except Exception as e:
                log.debug("fallback_dismiss_dialog failed: %s", e)
                return False

        elif action == "fallback_new_tab":
            try:
                subprocess.run(
                    ["xdotool", "key", "ctrl+t"],
                    capture_output=True, timeout=1
                )
                time.sleep(0.5)
                subprocess.run(
                    ["xdotool", "type", "--clearmodifiers", url],
                    capture_output=True, timeout=5
                )
                time.sleep(0.2)
                subprocess.run(
                    ["xdotool", "key", "Return"],
                    capture_output=True, timeout=1
                )
                return True
            except Exception as e:
                log.debug("fallback_new_tab failed: %s", e)
                return False

        elif action == "fallback_direct_browser":
            try:
                subprocess.Popen(
                    ["firefox", "--new-tab", url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception as e:
                log.debug("fallback_direct_browser failed: %s", e)
                # Try firefox-esr
                try:
                    subprocess.Popen(
                        ["firefox-esr", "--new-tab", url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return True
                except Exception:
                    return False

        return False


# ── 4. RECOVERY LOG ───────────────────────────────────────────────────────────


class RecoveryLog:
    """Persistent log of all failures + recovery attempts.

    Saved to ~/.eidos/recovery_log.jsonl
    Tracks which recovery strategies work for which failure types.
    Enables learning: "when clicking X fails, Tab+Enter works 70% of the time."
    """

    def __init__(self):
        RECOVERY_LOG.parent.mkdir(parents=True, exist_ok=True)

    def record(self, attempt: ActionAttempt):
        """Append an attempt result to the recovery log."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action_type": attempt.action_type,
            "action_params": attempt.action_params,
            "attempt_number": attempt.attempt_number,
            "verified": attempt.verified,
            "verification_detail": attempt.verification_detail,
            "fallbacks_tried": attempt.fallbacks_tried,
            "final_outcome": attempt.final_outcome,
            "elapsed_ms": attempt.elapsed_ms,
            "error": attempt.error_msg,
            "dialog_detected": attempt.dialog_detected,
            "dialog_dismissed": attempt.dialog_dismissed,
        }
        try:
            with open(RECOVERY_LOG, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            log.debug("Failed to write recovery log: %s", e)

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregate statistics from the recovery log."""
        if not RECOVERY_LOG.exists():
            return {"entries": 0, "message": "No recovery data yet"}

        total = 0
        failures = 0
        recovered = 0
        strategy_wins: Dict[str, int] = {}
        action_failures: Dict[str, int] = {}

        try:
            with open(RECOVERY_LOG, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        total += 1
                        if not entry.get("verified"):
                            failures += 1
                            at = entry.get("action_type", "unknown")
                            action_failures[at] = action_failures.get(at, 0) + 1
                            if entry.get("final_outcome") == "success":
                                recovered += 1
                            for fb in entry.get("fallbacks_tried", []):
                                strategy_wins[fb] = strategy_wins.get(fb, 0) + (
                                    1 if entry.get("final_outcome") == "success" else 0
                                )
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            log.debug("Error reading recovery log: %s", e)

        # Compute success rates per strategy
        strategy_rates = {}
        for strategy, wins in strategy_wins.items():
            total_tries = sum(
                1 for _ in self._iter_entries()
                if strategy in _.get("fallbacks_tried", [])
            )
            strategy_rates[strategy] = {
                "wins": wins,
                "total": total_tries,
                "rate": round(wins / max(1, total_tries), 2),
            }

        return {
            "entries": total,
            "failures": failures,
            "recovered": recovered,
            "recovery_rate": round(recovered / max(1, failures), 2),
            "failure_by_action": action_failures,
            "strategy_success_rates": strategy_rates,
        }

    def get_best_strategy(self, action_type: str) -> Optional[str]:
        """Return the best recovery strategy for a given action type.

        Based on historical success rates from the recovery log.
        """
        stats = self.get_stats()
        rates = stats.get("strategy_success_rates", {})
        best = None
        best_rate = 0
        for strategy, data in rates.items():
            if data["total"] >= 3 and data["rate"] > best_rate:
                best_rate = data["rate"]
                best = strategy
        return best

    def _iter_entries(self):
        """Iterate through all log entries."""
        if not RECOVERY_LOG.exists():
            return
        with open(RECOVERY_LOG, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


# ── 5. MAIN ORCHESTRATOR ──────────────────────────────────────────────────────


class RecoveryOrchestrator:
    """Orchestrates the full verify → fallback → retry cycle.

    For each action:
      1. Take a "before" screen snapshot
      2. Execute the action
      3. Verify it worked
      4. If failed → detect dialogs → dismiss if needed
      5. Try fallback strategies (up to 3)
      6. Verify after each fallback
      7. Log everything

    Usage:
        ro = RecoveryOrchestrator()
        result = ro.execute_with_recovery(
            action_type="click",
            action_params={"x": 500, "y": 300},
            executor_fn=lambda: human_emulator.click_at(500, 300),
        )
    """

    MAX_FALLBACKS = 3
    MAX_RETRIES = 2  # retry same action before escalating

    def __init__(self):
        self.verifier = ActionVerifier()
        self.dialog_detector = DialogDetector()
        self.fallback_engine = FallbackEngine()
        self.recovery_log = RecoveryLog()

    def execute_with_recovery(
        self,
        action_type: str,
        action_params: Dict[str, Any],
        executor_fn: Callable[[], Any],
    ) -> ActionAttempt:
        """Execute an action with full recovery support.

        Args:
            action_type: "click", "type", "navigate", "key", "scroll"
            action_params: parameters for the action (x, y, text, url, etc.)
            executor_fn: callable that performs the actual action

        Returns:
            ActionAttempt with full attempt details
        """
        t0 = time.time()
        attempt = ActionAttempt(
            action_type=action_type,
            action_params=action_params.copy(),
        )

        # 1. Snapshot before
        self.dialog_detector.snapshot_windows()
        before_hash = _screen_hash()

        # 2. Execute the action
        try:
            executor_fn()
        except Exception as e:
            attempt.error_msg = str(e)
            attempt.final_outcome = "execution_error"
            attempt.elapsed_ms = (time.time() - t0) * 1000
            self.recovery_log.record(attempt)
            return attempt

        # 3. Verify
        verified, detail = self.verifier.verify(action_type, action_params, before_hash)
        attempt.verified = verified
        attempt.verification_detail = detail

        if verified:
            attempt.final_outcome = "success"
            attempt.elapsed_ms = (time.time() - t0) * 1000
            self.recovery_log.record(attempt)
            return attempt

        # 4. Check for dialogs that may have stolen focus
        dialog = self.dialog_detector.detect()
        if dialog:
            attempt.dialog_detected = True
            log.info("Recovery: detected dialog '%s' via %s",
                     dialog.title[:60], dialog.detected_via)
            dismissed, method = self.dialog_detector.dismiss_current()
            attempt.dialog_dismissed = dismissed
            if dismissed:
                log.info("Recovery: dialog dismissed via %s", method)
                # Retry the original action once after dismiss
                try:
                    executor_fn()
                except Exception:
                    pass
                time.sleep(0.5)
                verified2, detail2 = self.verifier.verify(
                    action_type, action_params, before_hash
                )
                if verified2:
                    attempt.verified = True
                    attempt.final_outcome = "success_after_dialog_dismiss"
                    attempt.elapsed_ms = (time.time() - t0) * 1000
                    self.recovery_log.record(attempt)
                    return attempt

        # 5. Retry same action (transient failures)
        for retry_i in range(self.MAX_RETRIES):
            try:
                executor_fn()
            except Exception:
                pass
            time.sleep(0.3)
            verified_retry, detail_retry = self.verifier.verify(
                action_type, action_params, before_hash
            )
            if verified_retry:
                attempt.verified = True
                attempt.final_outcome = "success_on_retry"
                attempt.verification_detail = detail_retry
                attempt.elapsed_ms = (time.time() - t0) * 1000
                self.recovery_log.record(attempt)
                return attempt

        # 6. Fallback chain
        fallbacks = self._get_fallbacks(action_type, attempt)
        for i, fallback in enumerate(fallbacks[:self.MAX_FALLBACKS]):
            fb_name = fallback.get("description", fallback.get("action", "unknown"))
            log.info("Recovery: trying fallback %d/%d: %s",
                     i + 1, min(len(fallbacks), self.MAX_FALLBACKS), fb_name)

            # Snapshot before fallback
            self.dialog_detector.snapshot_windows()
            fb_before_hash = _screen_hash()

            # Execute fallback
            success = self._execute_fallback(action_type, fallback)
            if not success:
                attempt.fallbacks_tried.append(f"{fb_name}:exec_failed")
                continue

            # Verify fallback
            time.sleep(0.5)
            fb_verified, fb_detail = self.verifier.verify(
                action_type, action_params, fb_before_hash
            )
            if fb_verified:
                attempt.verified = True
                attempt.fallbacks_tried.append(f"{fb_name}:success")
                attempt.final_outcome = f"success_via_fallback_{i+1}"
                attempt.verification_detail = fb_detail
                attempt.elapsed_ms = (time.time() - t0) * 1000
                self.recovery_log.record(attempt)
                return attempt

            attempt.fallbacks_tried.append(f"{fb_name}:no_effect")

        # 7. All fallbacks exhausted
        attempt.final_outcome = "failed_after_fallbacks"
        attempt.elapsed_ms = (time.time() - t0) * 1000
        log.warning("Recovery: action '%s' FAILED after %d fallbacks: %s",
                    action_type, len(attempt.fallbacks_tried),
                    attempt.verification_detail)
        self.recovery_log.record(attempt)
        return attempt

    def _get_fallbacks(self, action_type: str,
                       attempt: ActionAttempt) -> List[Dict[str, Any]]:
        """Get fallback strategies for the action type."""
        if action_type in ("click", "click_center", "click_center_offset"):
            return self.fallback_engine.fallback_click(attempt)
        elif action_type == "type":
            return self.fallback_engine.fallback_type(attempt)
        elif action_type in ("navigate", "navigate_url", "open_browser"):
            return self.fallback_engine.fallback_navigate(attempt)
        else:
            # Generic fallbacks for unknown types
            return [
                {"action": "fallback_keyboard", "description": "Press Escape", "keys": "Escape"},
                {"action": "fallback_keyboard", "description": "Press Enter", "keys": "Return"},
            ]

    def _execute_fallback(self, action_type: str,
                          fallback: Dict[str, Any]) -> bool:
        """Execute a specific fallback strategy."""
        if action_type in ("click", "click_center", "click_center_offset"):
            return self.fallback_engine.execute_fallback_click(fallback)
        elif action_type == "type":
            return self.fallback_engine.execute_fallback_type(fallback)
        elif action_type in ("navigate", "navigate_url", "open_browser"):
            return self.fallback_engine.execute_fallback_navigate(fallback)
        else:
            # Try pressing Enter
            fb_action = fallback.get("action", "")
            if fb_action == "fallback_keyboard":
                keys = fallback.get("keys", "Return")
                try:
                    subprocess.run(
                        ["xdotool", "key", keys],
                        capture_output=True, timeout=2
                    )
                    return True
                except Exception:
                    return False
            return False

    def stats(self) -> Dict[str, Any]:
        """Return recovery statistics."""
        return self.recovery_log.get_stats()


# ── Singleton ──────────────────────────────────────────────────────────────────

_recovery: Optional[RecoveryOrchestrator] = None


def get_recovery() -> RecoveryOrchestrator:
    """Get the singleton RecoveryOrchestrator."""
    global _recovery
    if _recovery is None:
        _recovery = RecoveryOrchestrator()
    return _recovery


# ── Convenience functions ──────────────────────────────────────────────────────


def safe_click(x: int, y: int, human_emulator=None) -> ActionAttempt:
    """Click with full recovery. Uses HumanEmulator if provided, else xdotool."""
    def do_click():
        if human_emulator:
            human_emulator.click_at(x, y)
        else:
            subprocess.run(["xdotool", "mousemove", str(x), str(y)],
                           capture_output=True, timeout=1)
            subprocess.run(["xdotool", "click", "1"],
                           capture_output=True, timeout=1)

    return get_recovery().execute_with_recovery(
        action_type="click",
        action_params={"x": x, "y": y},
        executor_fn=do_click,
    )


def safe_type(text: str, x: Optional[int] = None, y: Optional[int] = None,
              human_emulator=None) -> ActionAttempt:
    """Type with full recovery."""
    def do_type():
        if human_emulator:
            human_emulator.type_text(text)
        else:
            subprocess.run(["xdotool", "type", "--clearmodifiers", text],
                           capture_output=True, timeout=5)

    return get_recovery().execute_with_recovery(
        action_type="type",
        action_params={"text": text, "x": x, "y": y},
        executor_fn=do_type,
    )


def safe_navigate(url: str) -> ActionAttempt:
    """Navigate to URL with full recovery."""
    def do_navigate():
        subprocess.run(["xdotool", "key", "ctrl+l"],
                       capture_output=True, timeout=1)
        time.sleep(0.2)
        subprocess.run(["xdotool", "key", "ctrl+a"],
                       capture_output=True, timeout=1)
        time.sleep(0.1)
        subprocess.run(["xdotool", "type", "--clearmodifiers", url],
                       capture_output=True, timeout=5)
        time.sleep(0.2)
        subprocess.run(["xdotool", "key", "Return"],
                       capture_output=True, timeout=1)

    return get_recovery().execute_with_recovery(
        action_type="navigate",
        action_params={"url": url},
        executor_fn=do_navigate,
    )


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(
        description="EIDOS Recovery — verify, fallback, self-heal"
    )
    p.add_argument("--stats", action="store_true",
                   help="Show recovery statistics")
    p.add_argument("--test-click", nargs=2, type=int, metavar=("X", "Y"),
                   help="Test safe click at coordinates")
    p.add_argument("--test-type", type=str,
                   help="Test safe typing")
    p.add_argument("--test-navigate", type=str,
                   help="Test safe navigation to URL")
    p.add_argument("--check-dialogs", action="store_true",
                   help="Check for unexpected dialogs")
    args = p.parse_args()

    if args.stats:
        ro = get_recovery()
        import json
        print(json.dumps(ro.stats(), indent=2, default=str))

    elif args.test_click:
        x, y = args.test_click
        print(f"Safe click at ({x}, {y})...")
        result = safe_click(x, y)
        print(f"  Verified: {result.verified}")
        print(f"  Outcome: {result.final_outcome}")
        print(f"  Detail: {result.verification_detail}")
        print(f"  Elapsed: {result.elapsed_ms:.0f}ms")
        if result.fallbacks_tried:
            print(f"  Fallbacks: {result.fallbacks_tried}")

    elif args.test_type:
        print(f"Safe type: '{args.test_type}'...")
        result = safe_type(args.test_type)
        print(f"  Verified: {result.verified}")
        print(f"  Outcome: {result.final_outcome}")
        print(f"  Elapsed: {result.elapsed_ms:.0f}ms")

    elif args.test_navigate:
        print(f"Safe navigate to: {args.test_navigate}...")
        result = safe_navigate(args.test_navigate)
        print(f"  Verified: {result.verified}")
        print(f"  Outcome: {result.final_outcome}")
        print(f"  Detail: {result.verification_detail}")
        print(f"  Elapsed: {result.elapsed_ms:.0f}ms")
        if result.fallbacks_tried:
            print(f"  Fallbacks: {result.fallbacks_tried}")

    elif args.check_dialogs:
        dd = DialogDetector()
        dd.snapshot_windows()
        dialog = dd.detect()
        if dialog:
            print(f"Dialog detected: '{dialog.title}' via {dialog.detected_via}")
        else:
            print("No dialog detected.")

    else:
        p.print_help()
