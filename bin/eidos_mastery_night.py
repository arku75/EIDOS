#!/usr/bin/env python3
"""P0 SCALED: Nightly mastery SPV loop entry point for systemd timer.

Runs Study-Practice-Verify on pending curriculum milestones across ALL domains
plus active recall practice from the spaced-repetition practice_queue.

Now processes up to 50 items per tick in night mode (10x scaling from S125 original).
Also auto-creates LearningGoals for ALL 235+ categories in the knowledge graph.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format='%(asctime)s | %(name)s | %(message)s'
)

from core.eidos_mastery import run_night_mastery

# P0: Default to 50 for night mode (was 3-5)
max_ms = int(sys.argv[1]) if len(sys.argv) > 1 else 50
# Check for --practice-only flag
practice_only = "--practice-only" in sys.argv
# Check for --no-cats flag (skip category goal creation)
auto_cats = "--no-cats" not in sys.argv

if practice_only:
    from core.eidos_mastery import get_night_mastery_runner
    runner = get_night_mastery_runner()
    runner.set_night_mode(enabled=True)
    results = runner.run_practice_only(max_items=max_ms)
else:
    results = run_night_mastery(
        max_milestones=max_ms,
        night_mode=True,
        auto_create_category_goals=auto_cats,
    )

curriculum = [r for r in results if r.get("type", "") != "practice"]
practice = [r for r in results if r.get("type", "") == "practice"]
passed = sum(1 for r in results if r.get("passed"))
failed = sum(1 for r in results if not r.get("passed"))

print(f"Night Mastery complete: {passed} passed, {failed} failed, "
      f"{len(curriculum)} curriculum + {len(practice)} practice = {len(results)} total")

sys.exit(0 if failed == 0 else 1)
