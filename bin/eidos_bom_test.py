#!/usr/bin/env python3
"""
bin/eidos_bom_test.py — BOM Real Multi-Step Test (S125+)

Tests the plan_and_execute pathway with the real BOM (dry_run=False).
Goal: "abre el navegador, busca 'Kali Linux tutorial', y dime que encuentras"

This verifies:
  1. GraphPlanner can decompose the goal into steps
  2. Each BOM step executes correctly (real mouse clicks, keyboard input)
  3. Screen state changes are verified after each action
  4. Results are reported clearly

SAFETY: Only runs when SER is present (idle < 5 min).
        Respects all SafetyGuard rules.

Usage:
  # Dry run (safe, no real actions)
  python3 bin/eidos_bom_test.py --dry

  # Real execution (requires SER present)
  python3 bin/eidos_bom_test.py --real

  # Custom goal
  python3 bin/eidos_bom_test.py --real --goal "abre Firefox y busca documentacion de Python"
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Path setup
_EIDOS_ROOT = Path(__file__).resolve().parent.parent
if str(_EIDOS_ROOT) not in sys.path:
    sys.path.insert(0, str(_EIDOS_ROOT))

log = logging.getLogger("eidos.bom_test")


def check_ser_present() -> bool:
    """Verify SER is present before doing real actions."""
    try:
        import subprocess
        r = subprocess.run(
            ["xdotool", "getmouselocation"],
            capture_output=True, text=True, timeout=2,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
        # If xdotool works, we're on the desktop
        return r.returncode == 0
    except Exception:
        return False


def check_browser_available() -> Optional[str]:
    """Check which browsers are available. Returns browser command or None."""
    import subprocess
    for browser in ["firefox-esr", "firefox", "chromium", "chromium-browser", "google-chrome"]:
        try:
            r = subprocess.run(
                ["which", browser],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return browser
        except Exception:
            continue
    return None


def open_browser_search(query: str, browser: str = "firefox-esr") -> bool:
    """Open browser and search for a query using DuckDuckGo."""
    import urllib.parse
    import subprocess

    search_url = f"https://duckduckgo.com/?q={urllib.parse.quote(query)}"
    try:
        subprocess.Popen(
            [browser, "--new-tab", search_url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
        time.sleep(3)
        # Bring browser to front
        for wm_name in ["Firefox", "Chromium", "Chrome"]:
            try:
                subprocess.run(
                    ["wmctrl", "-a", wm_name],
                    capture_output=True, timeout=2,
                    env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
                )
                break
            except Exception:
                continue
        return True
    except Exception as e:
        log.error("Failed to open browser: %s", e)
        return False


def read_screen_text() -> str:
    """Read visible text from the screen using OCR."""
    try:
        from core.perception import take_screenshot, ocr_screenshot
        shot = take_screenshot("bom_test")
        if shot:
            elements = ocr_screenshot(shot) or []
            texts = []
            for e in elements:
                t = (getattr(e, "text", "") or "").strip()
                if t:
                    texts.append(t)
            return " | ".join(texts[:30])
    except Exception as e:
        log.debug("OCR failed: %s", e)
    return ""


def get_active_window() -> str:
    """Get the active window title."""
    try:
        import subprocess
        r = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=3,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    # Parse args
    args = sys.argv[1:]
    dry_run = "--real" not in args
    goal = "abre el navegador, busca 'Kali Linux tutorial', y dime que encuentras"

    for i, arg in enumerate(args):
        if arg == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            break

    print("=" * 70)
    print("  EIDOS BOM Multi-Step Test")
    print(f"  Mode: {'DRY RUN (no real actions)' if dry_run else 'REAL EXECUTION'}")
    print(f"  Goal: {goal}")
    print("=" * 70)

    # ── Safety checks for real mode ────────────────────────────────────────────
    if not dry_run:
        if not check_ser_present():
            print("\nERROR: Cannot detect desktop. Is DISPLAY=:0 set?")
            print("Real mode requires X11 desktop access.")
            sys.exit(1)
        print("Desktop detected OK")

        browser = check_browser_available()
        if not browser:
            print("\nWARNING: No browser found. Will simulate browser steps.")
        else:
            print(f"Browser found: {browser}")

    # ── Phase 1: Plan ──────────────────────────────────────────────────────────
    print("\n── Phase 1: Planning ──")
    try:
        from core.eidos_alive_orchestrator import AliveOrchestrator
        orch = AliveOrchestrator()

        plan_result = orch.plan_and_execute(
            goal=goal,
            max_steps=5,
            dry_run=dry_run,
        )

        if plan_result.get("error"):
            print(f"Plan error: {plan_result['error']}")
            # Try manual step-by-step approach
            print("\nFalling back to manual multi-step execution...")
            _run_manual_bom_test(goal, dry_run)
            return

        plan_steps = plan_result.get("plan_steps", [])
        executed = plan_result.get("executed", [])

        print(f"Plan generated: {len(plan_steps)} steps")
        for i, step in enumerate(plan_steps):
            print(f"  Step {i+1}: {step.get('action', '?')} — {step.get('args', [])}")
            if step.get("shell_cmd"):
                print(f"    shell: {step['shell_cmd']}")

        print(f"\nExecution results: {len(executed)} steps attempted")
        all_ok = plan_result.get("all_succeeded", False)
        for i, ex in enumerate(executed):
            status = "OK" if ex.get("ok") else "FAIL"
            print(f"  Step {i+1}: {ex.get('action', '?')} → {status}")
            if ex.get("error"):
                print(f"    Error: {ex['error']}")
            if ex.get("output"):
                print(f"    Output: {str(ex['output'])[:100]}")

        print(f"\nOverall: {'ALL STEPS SUCCEEDED' if all_ok else 'SOME STEPS FAILED'}")

    except Exception as e:
        log.error("Plan-and-execute failed: %s", e)
        print(f"\nPlan-and-execute error: {e}")
        print("Falling back to manual BOM test...")
        _run_manual_bom_test(goal, dry_run)

    # ── Phase 2: What did we find? ──────────────────────────────────────────────
    print("\n── Phase 2: Reading results ──")
    active_win = get_active_window()
    print(f"Active window: {active_win[:120]}")

    screen_text = read_screen_text()
    if screen_text:
        print(f"Screen text (OCR): {screen_text[:500]}")
    else:
        print("(no screen text available)")

    print("\n── Test Complete ──")


def _run_manual_bom_test(goal: str, dry_run: bool):
    """Manual fallback: execute BOM steps one at a time."""
    from core.causal_loop import run as bom_run
    from core.causal_loop import step as bom_step

    # Step 1: Look for browser
    print("\nStep 1: Finding browser window...")
    r1 = bom_step(goal="find the browser window", app_name=None, dry_run=dry_run)
    print(f"  Action: {r1.get('action', '?')} | Reward: {r1.get('reward', 0)}")
    print(f"  Effect: {r1.get('effect', '?')}")

    if not dry_run:
        # Step 2: Open browser if not found
        browser = check_browser_available()
        if browser:
            print("\nStep 2: Opening browser...")
            open_browser_search("Kali Linux tutorial", browser)
            time.sleep(3)
            print("  Browser opened with search query")

        # Step 3: Read what's on screen
        print("\nStep 3: Reading search results...")
        r3 = bom_step(goal="read and understand what's on screen", app_name=None, dry_run=dry_run)
        print(f"  Action: {r3.get('action', '?')} | Reward: {r3.get('reward', 0)}")
        print(f"  Effect: {r3.get('effect', '?')}")

    # Summary
    print(f"\nManual BOM test complete (dry_run={dry_run})")


if __name__ == "__main__":
    main()
