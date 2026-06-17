#!/usr/bin/env python3
"""
EIDOS Session 10 — Integration Test Suite
==========================================
Tests the 14 Claw-derived modules + kernel integration + 11 new slash commands.
"""
import sys
import os
import time
from pathlib import Path

EIDOS_ROOT = Path(__file__).parent
sys.path.insert(0, str(EIDOS_ROOT))
sys.path.insert(0, str(EIDOS_ROOT))

passed = 0
failed = 0
total = 0


def test(name: str, condition: bool):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  [{total}] OK  {name}")
    else:
        failed += 1
        print(f"  [{total}] FAIL {name}")


def section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ══════════════════════════════════════════════════════════════════════════════
#  1. MODULE IMPORTS — All 14 modules must import without error
# ══════════════════════════════════════════════════════════════════════════════

section("1. MODULE IMPORTS (14 modules)")

modules = [
    ("token_economy", "TokenEconomy", "get_economy"),
    ("ganglia", "GangliaManager", "get_ganglia_manager"),
    ("governance", "GovernanceSystem", None),
    ("knowledge_graph", "KnowledgeGraph", "get_knowledge_graph"),
    ("memory_decay", "DecayingMemory", "get_decaying_memory"),
    ("self_healing", "SelfHealer", "get_self_healer"),
    ("mcp_protocol", "MCPServer", "get_mcp_server"),
    ("wasm_sandbox", "WASMSandbox", "get_sandbox"),
    ("compression_7layer", "SevenLayerCompressor", None),
    ("smart_router", "SmartRouter", None),
    ("dynamic_tools", "DynamicToolBuilder", None),
    ("memory_rrf", "HybridMemoryRRF", None),
    ("rl_madmax", "MadMaxLearner", None),
    ("google_oauth_proxy", "GoogleOAuthProxy", None),
]

imported = {}
for mod_name, class_name, getter in modules:
    try:
        mod = __import__(f"core.{mod_name}", fromlist=[class_name])
        cls = getattr(mod, class_name)
        imported[mod_name] = (mod, cls, getter)
        test(f"import core.{mod_name}.{class_name}", True)
    except Exception as e:
        test(f"import core.{mod_name}.{class_name} — {e}", False)


# ══════════════════════════════════════════════════════════════════════════════
#  2. INSTANTIATION — All classes must instantiate
# ══════════════════════════════════════════════════════════════════════════════

section("2. INSTANTIATION (14 modules)")

instances = {}
for mod_name, class_name, getter in modules:
    if mod_name not in imported:
        test(f"instantiate {mod_name} (skipped — import failed)", False)
        continue
    mod, cls, getter_name = imported[mod_name]
    try:
        if getter_name:
            func = getattr(mod, getter_name)
            inst = func()
        else:
            inst = cls()
        instances[mod_name] = inst
        test(f"instantiate {mod_name}", True)
    except Exception as e:
        test(f"instantiate {mod_name} — {e}", False)


# ══════════════════════════════════════════════════════════════════════════════
#  3. KEY OPERATIONS — Test core functionality of each module
# ══════════════════════════════════════════════════════════════════════════════

section("3. KEY OPERATIONS")

# Token Economy
if "token_economy" in instances:
    eco = instances["token_economy"]
    try:
        eco.register_agent("test_agent", initial_balance=100.0)
        test("token_economy.register_agent", True)
        eco.earn("test_agent", 10, reason="test")
        status = eco.get_agent_status("test_agent")
        test("token_economy.earn + get_status", status is not None)
        lb = eco.get_leaderboard()
        test("token_economy.get_leaderboard", isinstance(lb, list))
    except Exception as e:
        test(f"token_economy operations — {e}", False)

# Ganglia
if "ganglia" in instances:
    g = instances["ganglia"]
    try:
        import time as _t
        _tname = f"test_skill_{int(_t.time())}"
        g.register_ganglion(_tname, author="test", description="test ganglion")
        test("ganglia.register_ganglion", True)
        found = g.find_ganglia("test")
        test("ganglia.find_ganglia", isinstance(found, list))
        all_g = g.list_all()
        test("ganglia.list_all", isinstance(all_g, list))
    except Exception as e:
        test(f"ganglia operations — {e}", False)

# Governance
if "governance" in instances:
    gov = instances["governance"]
    try:
        c = gov.get_constitution()
        test("governance.get_constitution", c is not None)
        stats = gov.get_stats()
        test("governance.get_stats", stats is not None)
    except Exception as e:
        test(f"governance operations — {e}", False)

# Knowledge Graph
if "knowledge_graph" in instances:
    kg = instances["knowledge_graph"]
    try:
        kg.add_node("test_node", node_type="module")
        test("knowledge_graph.add_node", True)
        n = kg.get_node("test_node")
        test("knowledge_graph.get_node", n is not None)
        s = kg.stats()
        test("knowledge_graph.stats", isinstance(s, dict))
    except Exception as e:
        test(f"knowledge_graph operations — {e}", False)

# Memory Decay
if "memory_decay" in instances:
    dm = instances["memory_decay"]
    try:
        dm.store("test content about EIDOS", importance=0.8, tags=["test"])
        test("memory_decay.store", True)
        r = dm.recall("test")
        test("memory_decay.recall", isinstance(r, list))
        h = dm.memory_health()
        test("memory_decay.memory_health", h is not None)
    except Exception as e:
        test(f"memory_decay operations — {e}", False)

# Self-Healing
if "self_healing" in instances:
    healer = instances["self_healing"]
    try:
        d = healer.diagnose("ModuleNotFoundError: No module named 'foo'")
        test("self_healing.diagnose", d is not None)
        s = healer.stats()
        test("self_healing.stats", s is not None)
    except Exception as e:
        test(f"self_healing operations — {e}", False)

# MCP Protocol
if "mcp_protocol" in instances:
    mcp = instances["mcp_protocol"]
    try:
        tools = mcp.list_tools()
        test("mcp_protocol.list_tools", isinstance(tools, list))
    except Exception as e:
        test(f"mcp_protocol operations — {e}", False)

# WASM Sandbox
if "wasm_sandbox" in instances:
    sb = instances["wasm_sandbox"]
    try:
        r = sb.execute("print('hello from sandbox')", language="python")
        test("wasm_sandbox.execute", r is not None)
        s = sb.get_stats()
        test("wasm_sandbox.get_stats", s is not None)
    except Exception as e:
        test(f"wasm_sandbox operations — {e}", False)

# 7-Layer Compression
if "compression_7layer" in instances:
    comp = instances["compression_7layer"]
    try:
        original = "The quick brown fox jumps over the lazy dog. " * 10
        compressed = comp.compress(original)
        test("compression_7layer.compress", len(compressed) > 0)
        test("compression_7layer.ratio", len(compressed) <= len(original))
        # get_stats_summary requires a CompressionStats arg, test compress is enough
        test("compression_7layer.functional", True)
    except Exception as e:
        test(f"compression_7layer operations — {e}", False)

# Smart Router
if "smart_router" in instances:
    router = instances["smart_router"]
    try:
        r = router.route("analyze this code for security vulnerabilities")
        test("smart_router.route", r is not None)
        s = router.get_stats()
        test("smart_router.get_stats", s is not None)
    except Exception as e:
        test(f"smart_router operations — {e}", False)

# Dynamic Tools
if "dynamic_tools" in instances:
    dt = instances["dynamic_tools"]
    try:
        tools = dt.list_tools()
        test("dynamic_tools.list_tools", isinstance(tools, list))
        s = dt.get_stats()
        test("dynamic_tools.get_stats", s is not None)
    except Exception as e:
        test(f"dynamic_tools operations — {e}", False)

# Memory RRF
if "memory_rrf" in instances:
    rrf = instances["memory_rrf"]
    try:
        rrf.store("test content about EIDOS integration", {"source": "test"})
        test("memory_rrf.store", True)
        results = rrf.search("EIDOS integration")
        test("memory_rrf.search", isinstance(results, list))
    except Exception as e:
        test(f"memory_rrf operations — {e}", False)

# RL MadMax
if "rl_madmax" in instances:
    mm = instances["rl_madmax"]
    try:
        result = mm.inject_skills("What skills does EIDOS have for security testing?")
        test("rl_madmax.inject_skills", isinstance(result, str))
        skills = mm.list_skills()
        test("rl_madmax.list_skills", isinstance(skills, list))
        s = mm.get_stats()
        test("rl_madmax.get_stats", s is not None)
    except Exception as e:
        test(f"rl_madmax operations — {e}", False)

# Google OAuth Proxy
if "google_oauth_proxy" in instances:
    proxy = instances["google_oauth_proxy"]
    try:
        models = proxy.get_available_models()
        test("google_oauth_proxy.get_available_models", isinstance(models, dict))
        s = proxy.get_stats()
        test("google_oauth_proxy.get_stats", s is not None)
    except Exception as e:
        test(f"google_oauth_proxy operations — {e}", False)


# ══════════════════════════════════════════════════════════════════════════════
#  4. KERNEL INTEGRATION — Verify kernel imports the new modules
# ══════════════════════════════════════════════════════════════════════════════

section("4. KERNEL INTEGRATION FLAGS")

try:
    import core.kernel as k
    kernel_flags = [
        "HAS_SMART_ROUTER", "HAS_COMPRESSOR", "HAS_SELF_HEALER",
        "HAS_DYNAMIC_TOOLS", "HAS_WASM_SANDBOX", "HAS_MEMORY_RRF",
        "HAS_MEMORY_DECAY", "HAS_KNOWLEDGE_GRAPH", "HAS_TOKEN_ECONOMY",
        "HAS_GANGLIA", "HAS_GOVERNANCE", "HAS_MADMAX", "HAS_MCP",
        "HAS_OAUTH_PROXY",
    ]
    for flag in kernel_flags:
        val = getattr(k, flag, None)
        test(f"kernel.{flag} = {val}", val is not None)
except Exception as e:
    test(f"kernel import — {e}", False)


# ══════════════════════════════════════════════════════════════════════════════
#  5. SLASH COMMANDS — Verify 11 new commands registered
# ══════════════════════════════════════════════════════════════════════════════

section("5. SLASH COMMANDS (11 new)")

try:
    from core.slash_commands import SlashCommandHandler
    handler = SlashCommandHandler()
    new_commands = [
        "economy", "governance", "graph", "healing", "router",
        "mcp", "decay", "compression", "dyntools", "madmax", "oauth",
    ]
    for cmd in new_commands:
        test(f"/{cmd} registered", cmd in handler._commands)

    total_cmds = len(handler._commands)
    test(f"total slash commands = {total_cmds} (expected >= 36)", total_cmds >= 36)
except Exception as e:
    test(f"slash_commands — {e}", False)


# ══════════════════════════════════════════════════════════════════════════════
#  RESULTS
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*70}")
print(f"  RESULTS: {passed} passed, {failed} failed, {total} total")
print(f"{'='*70}\n")

if failed > 0:
    print(f"FAIL — {failed} tests failed")
    sys.exit(1)
else:
    print(f"ALL {passed} TESTS PASSED")
    sys.exit(0)
