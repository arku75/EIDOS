#!/usr/bin/env python3
"""
EIDOS Session 12 Integration Tests
====================================
Tests for all new modules and integrations:
1. ColonyQueryEngine (new module)
2. ExtensionIntelligence (new module)
3. IPC Bridge (new module)
4. Kernel integration (HAS_COLONY_ENGINE, HAS_EXTENSION_INTEL, HAS_IPC_BRIDGE)
5. Slash commands (/colony, /extensions, /ipc)
6. Syntax verification
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
print("  EIDOS SESSION 12 — INTEGRATION TESTS")
print("=" * 70)

# ══════════════════════════════════════════════════════════════════════════════
# 1. COLONY QUERY ENGINE
# ══════════════════════════════════════════════════════════════════════════════
print("\n  1. COLONY QUERY ENGINE")
print("=" * 70)

try:
    from core.colony_query_engine import (
        ColonyQueryEngine, get_colony_engine,
        ColonyQuery, ColonyResult, ColonyAgent,
        QueryType, detect_query_type,
        HAS_ROUTER, HAS_MODEL_MGR, HAS_ECONOMY, HAS_GANGLIA, HAS_GOVERNANCE,
    )
    test("import colony_query_engine", True)
except Exception as e:
    test(f"import colony_query_engine — {e}", False)

try:
    engine = get_colony_engine()
    test("get_colony_engine singleton", engine is not None)
except Exception as e:
    test(f"singleton — {e}", False)

# Test query type detection
try:
    test("detect_query_type code", detect_query_type("write a python function") == QueryType.CODE)
    test("detect_query_type vision", detect_query_type("", has_images=True) == QueryType.VISION)
    test("detect_query_type reasoning", detect_query_type("explain why TCP uses three-way handshake") == QueryType.REASONING)
    test("detect_query_type chat", detect_query_type("hello world") == QueryType.CHAT)
    test("detect_query_type tool", detect_query_type("run nmap scan on target") == QueryType.TOOL_USE)
except Exception as e:
    test(f"detect_query_type — {e}", False)

# Test ColonyResult
try:
    result = ColonyResult(success=True, response="test", model_used="hermes3:8b")
    d = result.to_dict()
    test("ColonyResult.to_dict()", isinstance(d, dict) and d["success"] is True)
    test("ColonyResult.model_used", d["model_used"] == "hermes3:8b")
except Exception as e:
    test(f"ColonyResult — {e}", False)

# Test ColonyAgent
try:
    agent = ColonyAgent(
        agent_id="test_agent", name="Test", specialties=[QueryType.CODE],
        system_prompt="test", quality_score=0.9
    )
    test("ColonyAgent.can_handle CODE", agent.can_handle(QueryType.CODE))
    test("ColonyAgent.can_handle VISION", not agent.can_handle(QueryType.VISION))
except Exception as e:
    test(f"ColonyAgent — {e}", False)

# Test get_agents
try:
    agents = engine.get_agents()
    test("get_agents returns list", isinstance(agents, list) and len(agents) >= 5)
    test("agents have agent_id", all("agent_id" in a for a in agents))
except Exception as e:
    test(f"get_agents — {e}", False)

# Test get_status
try:
    status = engine.get_status()
    test("get_status returns dict", isinstance(status, dict))
    test("status has online", status.get("online") is True)
    test("status has subsystems", "subsystems" in status)
except Exception as e:
    test(f"get_status — {e}", False)

# Test get_stats
try:
    stats = engine.get_stats()
    test("get_stats returns dict", isinstance(stats, dict))
    test("stats has subsystems", "subsystems" in stats)
except Exception as e:
    test(f"get_stats — {e}", False)

# Test register_agent
try:
    ok = engine.register_agent(
        f"test_agent_{int(time.time())}",
        "TestBot", ["chat"], "Test system prompt"
    )
    test("register_agent", ok is True)
except Exception as e:
    test(f"register_agent — {e}", False)

# Test get_history
try:
    history = engine.get_history(limit=5)
    test("get_history returns list", isinstance(history, list))
except Exception as e:
    test(f"get_history — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 2. EXTENSION INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════════════
print("\n  2. EXTENSION INTELLIGENCE")
print("=" * 70)

try:
    from core.extension_intelligence import (
        ExtensionIntelligence, get_extension_intelligence,
        ExtensionInfo, ScanResult,
        EXTENSION_DB, FILE_EXT_MAP, CONFIG_FILE_MAP,
    )
    test("import extension_intelligence", True)
except Exception as e:
    test(f"import extension_intelligence — {e}", False)

try:
    ei = get_extension_intelligence()
    test("get_extension_intelligence singleton", ei is not None)
except Exception as e:
    test(f"singleton — {e}", False)

# Test extension DB
try:
    test("EXTENSION_DB has python", "python" in EXTENSION_DB)
    test("EXTENSION_DB has rust", "rust" in EXTENSION_DB)
    test("EXTENSION_DB has docker", "docker" in EXTENSION_DB)
    test("EXTENSION_DB has _universal", "_universal" in EXTENSION_DB)
    total_exts = sum(len(v) for v in EXTENSION_DB.values())
    test(f"EXTENSION_DB has {total_exts} extensions (>= 30)", total_exts >= 30)
except Exception as e:
    test(f"EXTENSION_DB — {e}", False)

# Test file detection maps
try:
    test("FILE_EXT_MAP .py -> python", FILE_EXT_MAP.get(".py") == "python")
    test("FILE_EXT_MAP .rs -> rust", FILE_EXT_MAP.get(".rs") == "rust")
    test("CONFIG_FILE_MAP Dockerfile", CONFIG_FILE_MAP.get("Dockerfile") == "docker")
    test("CONFIG_FILE_MAP Cargo.toml", CONFIG_FILE_MAP.get("Cargo.toml") == "rust")
except Exception as e:
    test(f"detection maps — {e}", False)

# Test analyze_workspace
try:
    scan = ei.analyze_workspace(str(EIDOS_ROOT), max_files=100, max_depth=2)
    test("analyze_workspace returns ScanResult", isinstance(scan, ScanResult))
    test("scan detected python", "python" in scan.detected_languages)
    test("scan has recommended", len(scan.recommended) > 0)
    test("scan has file_counts", isinstance(scan.file_counts, dict))
    test("scan_time > 0", scan.scan_time > 0)
except Exception as e:
    test(f"analyze_workspace — {e}", False)

# Test get_status
try:
    status = ei.get_status()
    test("get_status has code_binary", "code_binary" in status)
    test("get_status has is_vseidos", "is_vseidos" in status)
    test("get_status has installed_count", "installed_count" in status)
except Exception as e:
    test(f"get_status — {e}", False)

# Test get_stats
try:
    stats = ei.get_stats()
    test("get_stats has installed_count", "installed_count" in stats)
except Exception as e:
    test(f"get_stats — {e}", False)

# Test search_extensions
try:
    results = ei.search_extensions("python")
    test("search_extensions python", len(results) > 0)
    test("search results have ext_id", all("ext_id" in r for r in results))
except Exception as e:
    test(f"search_extensions — {e}", False)

# Test user preferences
try:
    ei.set_preference("test.ext.123", "never", "test reason")
    prefs = ei._get_user_preferences()
    test("set_preference + get", prefs.get("test.ext.123") == "never")
except Exception as e:
    test(f"preferences — {e}", False)

# Test get_history
try:
    history = ei.get_history(limit=5)
    test("get_history returns list", isinstance(history, list))
except Exception as e:
    test(f"get_history — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 3. IPC BRIDGE
# ══════════════════════════════════════════════════════════════════════════════
print("\n  3. IPC BRIDGE")
print("=" * 70)

try:
    from core.ipc_bridge import (
        IPCBridge, get_ipc_bridge, IPCMessage,
        SOCKET_PATH, MAILBOX_DIR,
    )
    test("import ipc_bridge", True)
except Exception as e:
    test(f"import ipc_bridge — {e}", False)

try:
    ipc = get_ipc_bridge()
    test("get_ipc_bridge singleton", ipc is not None)
except Exception as e:
    test(f"singleton — {e}", False)

# Test IPCMessage
try:
    msg = IPCMessage(msg_type="event", source="test", payload={"key": "value"})
    test("IPCMessage creation", msg.msg_type == "event")
    json_str = msg.to_json()
    test("IPCMessage.to_json()", isinstance(json_str, str))
    msg2 = IPCMessage.from_json(json_str)
    test("IPCMessage.from_json()", msg2.msg_type == "event" and msg2.payload["key"] == "value")
    test("IPCMessage.msg_id preserved", msg2.msg_id == msg.msg_id)
except Exception as e:
    test(f"IPCMessage — {e}", False)

# Test get_status
try:
    status = ipc.get_status()
    test("get_status has identity", "identity" in status)
    test("get_status has server_running", "server_running" in status)
    test("get_status has socket_path", "socket_path" in status)
    test("get_status has connections", "connections" in status)
except Exception as e:
    test(f"get_status — {e}", False)

# Test mailbox directories exist
try:
    test("mailbox dir exists", MAILBOX_DIR.exists())
    test("mailbox to_cli exists", (MAILBOX_DIR / "to_cli").exists())
    test("mailbox to_vseidos exists", (MAILBOX_DIR / "to_vseidos").exists())
except Exception as e:
    test(f"mailbox dirs — {e}", False)

# Test event handler registration
try:
    received = []
    ipc.on("test_event", lambda msg: received.append(msg))
    test("on() handler registration", "test_event" in ipc._message_handlers)
except Exception as e:
    test(f"on() — {e}", False)

# Test is_connected
try:
    test("is_connected() returns bool", isinstance(ipc.is_connected(), bool))
    test("is_server_running() returns bool", isinstance(ipc.is_server_running(), bool))
except Exception as e:
    test(f"is_connected — {e}", False)

# Test get_recent_messages
try:
    msgs = ipc.get_recent_messages(limit=5)
    test("get_recent_messages returns list", isinstance(msgs, list))
except Exception as e:
    test(f"get_recent_messages — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 3b. WAKE WORD LISTENER
# ══════════════════════════════════════════════════════════════════════════════
print("\n  3b. WAKE WORD LISTENER")
print("=" * 70)

try:
    from core.wake_word import (
        WakeWordListener, get_wake_listener, VoiceCommand,
        ListenMode, HAS_VOSK, HAS_SOUNDDEVICE,
        VOSK_MODEL_ES, VOSK_MODEL_EN,
    )
    test("import wake_word", True)
except Exception as e:
    test(f"import wake_word — {e}", False)

try:
    listener = get_wake_listener()
    test("get_wake_listener singleton", listener is not None)
except Exception as e:
    test(f"singleton — {e}", False)

try:
    test("HAS_VOSK", HAS_VOSK is True)
    test("HAS_SOUNDDEVICE", HAS_SOUNDDEVICE is True)
    test("VOSK_MODEL_ES exists", VOSK_MODEL_ES.exists())
    test("VOSK_MODEL_EN exists", VOSK_MODEL_EN.exists())
    test("is_available()", listener.is_available())
except Exception as e:
    test(f"wake dependencies — {e}", False)

try:
    status = listener.get_status()
    test("get_status has running", "running" in status)
    test("get_status has mode", "mode" in status)
    test("get_status has vosk_available", "vosk_available" in status)
    test("get_status has model_es_exists", "model_es_exists" in status)
except Exception as e:
    test(f"get_status — {e}", False)

try:
    cmd = VoiceCommand(text="open browser", language="en", confidence=0.9)
    test("VoiceCommand instantiation", cmd.text == "open browser")
    test("VoiceCommand.language", cmd.language == "en")
except Exception as e:
    test(f"VoiceCommand — {e}", False)

try:
    test("ListenMode.PASSIVE", ListenMode.PASSIVE.value == "passive")
    test("ListenMode.ACTIVE", ListenMode.ACTIVE.value == "active")
    test("ListenMode.CONTINUOUS", ListenMode.CONTINUOUS.value == "continuous")
except Exception as e:
    test(f"ListenMode — {e}", False)

# Test wake word detection
try:
    test("_detect_wake_word 'eidos'", listener._detect_wake_word("hey eidos abre el navegador") == "hey eidos")
    test("_detect_wake_word 'oye eidos'", listener._detect_wake_word("oye eidos que hora es") == "oye eidos")
    test("_detect_wake_word none", listener._detect_wake_word("hello world") is None)
except Exception as e:
    test(f"wake_word detection — {e}", False)

# Test handler registration
try:
    listener.on_command(lambda cmd: None)
    test("on_command registration", len(listener._command_handlers) >= 1)
    listener.on_wake(lambda ww: None)
    test("on_wake registration", len(listener._wake_handlers) >= 1)
except Exception as e:
    test(f"handlers — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 4. KERNEL INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════
print("\n  4. KERNEL INTEGRATION")
print("=" * 70)

try:
    kernel_source = open("core/kernel.py").read()
    test("HAS_COLONY_ENGINE in kernel", "HAS_COLONY_ENGINE" in kernel_source)
    test("HAS_EXTENSION_INTEL in kernel", "HAS_EXTENSION_INTEL" in kernel_source)
    test("HAS_IPC_BRIDGE in kernel", "HAS_IPC_BRIDGE" in kernel_source)
    test("HAS_WAKE_WORD in kernel", "HAS_WAKE_WORD" in kernel_source)
    test("colony_query_engine import in kernel", "colony_query_engine" in kernel_source)
    test("extension_intelligence import in kernel", "extension_intelligence" in kernel_source)
    test("ipc_bridge import in kernel", "ipc_bridge" in kernel_source)
    test("wake_word import in kernel", "wake_word" in kernel_source)
    test("5f. ColonyEngine hook", "5f. ColonyEngine" in kernel_source)
    test("5g. IPC Bridge hook", "5g. IPC Bridge" in kernel_source)
except Exception as e:
    test(f"kernel integration — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 5. SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════
print("\n  5. SLASH COMMANDS (3 new)")
print("=" * 70)

try:
    from core.slash_commands import SlashCommandHandler
    handler = SlashCommandHandler()
    cmds = handler._commands
    test("/colony registered", "colony" in cmds)
    test("/extensions registered", "extensions" in cmds)
    test("/ipc registered", "ipc" in cmds)
    total_cmds = len(cmds)
    test(f"total slash commands = {total_cmds} (expected >= 41)", total_cmds >= 41)
except Exception as e:
    test(f"slash commands — {e}", False)

# Test /colony status
try:
    result = handler.handle("/colony status")
    test("/colony status executes", result.success)
except Exception as e:
    test(f"/colony status — {e}", False)

# Test /extensions status
try:
    result = handler.handle("/extensions status")
    test("/extensions status executes", result.success)
except Exception as e:
    test(f"/extensions status — {e}", False)

# Test /ipc status
try:
    result = handler.handle("/ipc status")
    test("/ipc status executes", result.success)
except Exception as e:
    test(f"/ipc status — {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# 6. SYNTAX CHECK ALL NEW FILES
# ══════════════════════════════════════════════════════════════════════════════
print("\n  6. SYNTAX VERIFICATION")
print("=" * 70)

import ast
new_files = [
    "core/colony_query_engine.py",
    "core/extension_intelligence.py",
    "core/ipc_bridge.py",
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
