#!/usr/bin/env python3
"""
EIDOS Session 11 Integration Tests
====================================
Tests for all new modules and integrations:
1. VisionLearnerBridge (new module)
2. Browser Selenium enhancements (multi-tab, upload, drag, metrics, text search)
3. Voice multi-voice (speak_as, speak_dialogue)
4. Video Pipeline multi-voice integration
5. Rust Bridge (wrapper, KnowledgeDB, FileObserver, AutoLearn)
6. Kernel integration (HAS_VISION_LEARNER, HAS_RUST_BRIDGE)
7. Slash commands (/vlearn, /rust)
"""
import os
import sys
import time
import json
import tempfile
from pathlib import Path

# Setup path
EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))
sys.path.insert(0, str(EIDOS_ROOT))
os.chdir(EIDOS_ROOT)

passed = 0
failed = 0
total = 0

def test(name, condition):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  [{total}] OK  {name}")
    else:
        failed += 1
        print(f"  [{total}] FAIL {name}")

print("=" * 70)
print("  EIDOS SESSION 11 — INTEGRATION TESTS")
print("=" * 70)

# ══════════════════════════════════════════════════════════════════════════════
# 1. VISION LEARNER BRIDGE
# ══════════════════════════════════════════════════════════════════════════════
print("\n  1. VISION LEARNER BRIDGE")
print("=" * 70)

try:
    from core.vision_learner_bridge import (
        VisionLearnerBridge, get_vision_learner_bridge,
        VisualKnowledge, LearnSession,
        HAS_VISION, HAS_CLIP, HAS_KNOWLEDGE_DB, HAS_BRAIN_MEMORY,
    )
    test("import vision_learner_bridge", True)
except Exception as e:
    test(f"import vision_learner_bridge — {e}", False)

try:
    bridge = get_vision_learner_bridge()
    test("get_vision_learner_bridge singleton", bridge is not None)
except Exception as e:
    test(f"singleton — {e}", False)

try:
    status = bridge.get_status()
    test("get_status returns dict", isinstance(status, dict))
    test("status has modules", "modules" in status)
    test("status has stats", "stats" in status)
    test("status has watching", "watching" in status)
except Exception as e:
    test(f"get_status — {e}", False)

try:
    vk = VisualKnowledge(source="test")
    test("VisualKnowledge instantiation", vk.source == "test")
    d = vk.to_dict()
    test("VisualKnowledge.to_dict()", isinstance(d, dict) and d["source"] == "test")
except Exception as e:
    test(f"VisualKnowledge — {e}", False)

try:
    ls = LearnSession(session_id="test_1", started_at="now")
    test("LearnSession instantiation", ls.session_id == "test_1")
except Exception as e:
    test(f"LearnSession — {e}", False)

# Test code detection
try:
    code_text = """
import torch
import numpy as np

def hello_world():
    return np.array([1, 2, 3])

class MyModel:
    def forward(self, x):
        return torch.relu(x)
"""
    snippets = bridge._detect_code(code_text)
    test("_detect_code finds code", len(snippets) >= 1)

    lang = bridge._detect_language("import torch\ndef foo(): pass")
    test("_detect_language python", lang == "python")

    libs = bridge._extract_libraries(code_text)
    test("_extract_libraries finds torch/numpy", "torch" in libs or "numpy" in libs)

    score = bridge._compute_technical_score(code_text, 2, 2)
    test("_compute_technical_score > 0", score > 0)
except Exception as e:
    test(f"analysis methods — {e}", False)

# Test history
try:
    history = bridge.get_learning_history(limit=5)
    test("get_learning_history returns list", isinstance(history, list))
except Exception as e:
    test(f"history — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 2. BROWSER ENHANCEMENTS
# ══════════════════════════════════════════════════════════════════════════════
print("\n  2. BROWSER SELENIUM ENHANCEMENTS")
print("=" * 70)

try:
    from core.eidos_browser import EidosBrowser, SELENIUM_AVAILABLE, PLAYWRIGHT_AVAILABLE
    test("import eidos_browser", True)
    test("SELENIUM or PLAYWRIGHT available", SELENIUM_AVAILABLE or PLAYWRIGHT_AVAILABLE)
except Exception as e:
    test(f"import browser — {e}", False)

# Check new methods exist
try:
    browser = EidosBrowser.__new__(EidosBrowser)
    test("new_tab method exists", hasattr(browser, 'new_tab'))
    test("switch_tab method exists", hasattr(browser, 'switch_tab'))
    test("close_tab method exists", hasattr(browser, 'close_tab'))
    test("get_tab_count method exists", hasattr(browser, 'get_tab_count'))
    test("upload_file method exists", hasattr(browser, 'upload_file'))
    test("drag_and_drop method exists", hasattr(browser, 'drag_and_drop'))
    test("wait_for_element_gone method exists", hasattr(browser, 'wait_for_element_gone'))
    test("get_page_metrics method exists", hasattr(browser, 'get_page_metrics'))
    test("scroll_page method exists", hasattr(browser, 'scroll_page'))
    test("find_elements_by_text method exists", hasattr(browser, 'find_elements_by_text'))
except Exception as e:
    test(f"browser methods — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 3. VOICE MULTI-VOICE
# ══════════════════════════════════════════════════════════════════════════════
print("\n  3. VOICE MULTI-VOICE SYSTEM")
print("=" * 70)

try:
    from core.voice_system import (
        VoiceSystem, PiperVoiceEngine, get_voice_system
    )
    test("import voice_system", True)
except Exception as e:
    test(f"import voice — {e}", False)

try:
    piper = PiperVoiceEngine.__new__(PiperVoiceEngine)
    test("speak_as method exists", hasattr(piper, 'speak_as'))
    test("speak_dialogue method exists", hasattr(piper, 'speak_dialogue'))
except Exception as e:
    test(f"piper methods — {e}", False)

try:
    voice = get_voice_system()
    test("VoiceSystem instantiation", voice is not None)
    test("engine_type set", hasattr(voice, 'engine_type'))
    test("is_available() callable", callable(voice.is_available))
except Exception as e:
    test(f"voice system — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 4. VIDEO PIPELINE MULTI-VOICE
# ══════════════════════════════════════════════════════════════════════════════
print("\n  4. VIDEO PIPELINE MULTI-VOICE")
print("=" * 70)

try:
    from core.video_pipeline import (
        VideoPipeline, VideoSpec, DialogueSegment,
        GeneratedVideo, ContentGenerator, get_video_pipeline
    )
    test("import video_pipeline", True)
    test("DialogueSegment class exists", DialogueSegment is not None)
except Exception as e:
    test(f"import pipeline — {e}", False)

try:
    seg = DialogueSegment(character="narrator", voice="es_davefx", text="Hola mundo")
    test("DialogueSegment instantiation", seg.character == "narrator")

    spec = VideoSpec(
        title="Test",
        narration="Testing multi-voice",
        dialogue=[seg, DialogueSegment(character="guide", voice="en_lessac", text="Hello world")]
    )
    test("VideoSpec with dialogue", spec.dialogue is not None and len(spec.dialogue) == 2)
except Exception as e:
    test(f"dialogue spec — {e}", False)

try:
    ideas = ContentGenerator.generate_facelessreel_ideas(count=3)
    test("ContentGenerator.generate_facelessreel_ideas", len(ideas) == 3)
except Exception as e:
    test(f"content generator — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 5. RUST BRIDGE
# ══════════════════════════════════════════════════════════════════════════════
print("\n  5. RUST BRIDGE")
print("=" * 70)

try:
    from core.rust_bridge import (
        RUST_AVAILABLE, RustKnowledgeDB, RustFileObserver, AutoLearnObserver,
        get_rust_knowledge_db, get_rust_observer, get_auto_learn_observer
    )
    test("import rust_bridge", True)
    test("RUST_AVAILABLE", RUST_AVAILABLE)
except Exception as e:
    test(f"import rust_bridge — {e}", False)

if RUST_AVAILABLE:
    try:
        kb = get_rust_knowledge_db()
        test("get_rust_knowledge_db singleton", kb is not None)
    except Exception as e:
        test(f"rust kb singleton — {e}", False)

    try:
        stats = kb.get_stats()
        test("get_stats returns dict", isinstance(stats, dict))
        test("stats has total_languages", "total_languages" in stats)
        test("stats has total_libraries", "total_libraries" in stats)
    except Exception as e:
        test(f"rust stats — {e}", False)

    try:
        lang_stats = kb.get_language_stats()
        test("get_language_stats", isinstance(lang_stats, dict))
        test("python files observed", lang_stats.get("python", 0) > 0)
    except Exception as e:
        test(f"rust lang stats — {e}", False)

    try:
        lib_stats = kb.get_library_stats()
        test("get_library_stats", isinstance(lib_stats, dict))
    except Exception as e:
        test(f"rust lib stats — {e}", False)

    # Test observe_file
    try:
        with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as f:
            f.write('import flask\nfrom pathlib import Path\ndef main():\n    app = flask.Flask(__name__)\n')
            f.flush()
            result = kb.observe_file(f.name)
            test("observe_file returns dict", isinstance(result, dict))
            test("observe_file has language", result.get("language") == "python")
            test("observe_file has functions", result.get("functions", 0) >= 1)
            os.unlink(f.name)
    except Exception as e:
        test(f"rust observe_file — {e}", False)

    # Test export_for_sync
    try:
        export = kb.export_for_sync()
        test("export_for_sync", isinstance(export, dict) and "languages" in export)
    except Exception as e:
        test(f"export_for_sync — {e}", False)

    # Test FileObserver
    try:
        obs = get_rust_observer()
        test("get_rust_observer singleton", obs is not None)
        test("observer has watch method", callable(obs.watch))
        test("observer has poll method", callable(obs.poll))
        test("observer has start_daemon method", callable(obs.start_daemon))
    except Exception as e:
        test(f"rust observer — {e}", False)

    # Test AutoLearnObserver
    try:
        alo = get_auto_learn_observer()
        test("get_auto_learn_observer", alo is not None)
        test("auto_learn has start method", callable(alo.start))
        alo_stats = alo.get_stats()
        test("auto_learn get_stats", isinstance(alo_stats, dict))
    except Exception as e:
        test(f"auto_learn — {e}", False)
else:
    test("RUST_AVAILABLE is False — skipping Rust tests", True)

# ══════════════════════════════════════════════════════════════════════════════
# 6. KERNEL INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════
print("\n  6. KERNEL INTEGRATION")
print("=" * 70)

try:
    # Check kernel flags
    import importlib
    kernel_source = open("core/kernel.py").read()
    test("HAS_VISION_LEARNER in kernel", "HAS_VISION_LEARNER" in kernel_source)
    test("HAS_RUST_BRIDGE in kernel", "HAS_RUST_BRIDGE" in kernel_source)
    test("vision_learner_bridge import in kernel", "vision_learner_bridge" in kernel_source)
    test("rust_bridge import in kernel", "rust_bridge" in kernel_source)
    test("VisionLearner hook in safe_call", "5e. VisionLearner" in kernel_source)
except Exception as e:
    test(f"kernel integration — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 7. SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════
print("\n  7. SLASH COMMANDS (2 new)")
print("=" * 70)

try:
    from core.slash_commands import SlashCommandHandler
    handler = SlashCommandHandler()
    cmds = handler._commands
    test("/vlearn registered", "vlearn" in cmds)
    test("/rust registered", "rust" in cmds)
    total_cmds = len(cmds)
    test(f"total slash commands = {total_cmds} (expected >= 38)", total_cmds >= 38)
except Exception as e:
    test(f"slash commands — {e}", False)

# Test /vlearn status
try:
    result = handler.handle("/vlearn status")
    test("/vlearn status executes", result.success)
except Exception as e:
    test(f"/vlearn status — {e}", False)

# Test /rust status
try:
    result = handler.handle("/rust status")
    test("/rust status executes", result.success)
except Exception as e:
    test(f"/rust status — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 8. SYNTAX CHECK ALL NEW FILES
# ══════════════════════════════════════════════════════════════════════════════
print("\n  8. SYNTAX VERIFICATION")
print("=" * 70)

import ast
new_files = [
    "core/vision_learner_bridge.py",
    "core/rust_bridge.py",
    "core/voice_system.py",
    "core/video_pipeline.py",
    "core/eidos_browser.py",
    "core/kernel.py",
    "core/slash_commands.py",
]

for f in new_files:
    try:
        ast.parse(open(f).read())
        test(f"syntax {Path(f).name}", True)
    except SyntaxError as e:
        test(f"syntax {Path(f).name} — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print(f"  RESULTS: {passed} passed, {failed} failed, {total} total")
print("=" * 70)

if failed == 0:
    print("\nALL {} TESTS PASSED".format(total))
else:
    print(f"\nFAIL — {failed} tests failed")

sys.exit(0 if failed == 0 else 1)
