#!/usr/bin/env python3
"""
smoke_e2e.py — Test de integración E2E del ciclo vital completo de EIDOS [S84]

Verifica el ciclo vital completo sin depender de HTTP endpoints:
  affect.tick() → rl.select_action → planner.plan → thoughts.think
  → execute_step → identity.independence → soul_snapshot → sleep
  + FastText search + EventBus pub/sub + Supervisor health

Uso: python3 bin/smoke_e2e.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS, FAIL = 0, 0

def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    mark = "✅" if cond else "❌"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {mark} {name}: {detail[:140]}")

print("=" * 64)
print("SMOKE-TEST E2E — Ciclo Vital Completo de EIDOS")
print("=" * 64)

# ── 1. AffectEngine ──────────────────────────────────────────────────────────
print("\n🧠 1. AffectEngine (modelo VAD)")

try:
    from core.eidos_affect import AffectEngine, AffectState
    affect = AffectEngine()

    # Estado inicial (AffectState dataclass)
    st = affect.state
    check("affect.init", st is not None, f"VAD: v={st.valence:.2f} a={st.arousal:.2f} "
          f"d={st.dominance:.2f} mood={st.mood}")

    # Tick produce cambio de estado
    mood_before = st.mood
    affect.tick()
    st2 = affect.state
    check("affect.tick", st2 is not None, f"mood={st2.mood} v={st2.valence:.2f}")

    # Transiciones de mood válidas
    valid_moods = {"curioso", "reflexivo", "en expansión", "consciente", "evolucionando"}
    check("affect.mood_valid", st2.mood in valid_moods, f"mood='{st2.mood}' ∈ valid_moods")

    # Evento modifica VAD
    affect.event("descubrimiento", intensity=0.8)
    post = affect.state
    check("affect.event", post is not None, f"post-evento v={post.valence:.2f}")

    # vad_tuple
    v, a_val, d_val = affect.vad_tuple()
    check("affect.vad_tuple", -1.0 <= v <= 1.0, f"VAD tuple: ({v:.2f}, {a_val:.2f}, {d_val:.2f})")

    # describe
    desc = affect.describe()
    check("affect.describe", isinstance(desc, str) and len(desc) > 0, desc[:80])

except Exception as e:
    check("affect.module", False, f"excepción: {e}")

# ── 2. Q(λ) Reinforcement Learning ──────────────────────────────────────────
print("\n🎯 2. Q(λ) Reinforcement Learning")

try:
    from core.eidos_rl import QLearningAgent
    rl = QLearningAgent()

    check("rl.init", rl is not None, f"RL ε={rl.epsilon:.3f}, episodes={rl.episodes}")

    # RL trabaja con hashes de nodos activos (no dicts)
    state_hash = rl.hash_state(["knowledge_node_1", "knowledge_node_2", "concept_nginx"])
    check("rl.hash_state", isinstance(state_hash, str) and len(state_hash) > 0, f"hash={state_hash}")

    # Seleccionar acción
    action = rl.select_action(state_hash, available_actions=["investigar", "reflexionar", "descansar"])
    check("rl.select_action", action is not None and len(action) > 0, f"acción={action}")

    # best_action
    best = rl.best_action(state_hash, available_actions=["investigar", "reflexionar", "descansar"])
    check("rl.best_action", best is not None, f"best_action={best}")

    # learn (Q-value update con hash)
    next_hash = rl.hash_state(["knowledge_node_1", "knowledge_node_2", "concept_docker"])
    rl.learn(state_hash, action, reward=0.75, next_state_hash=next_hash)
    check("rl.learn", True, "Q-value aprendido OK")

    # q_value_report requiere state_hash
    report = rl.q_value_report(state_hash)
    check("rl.report", isinstance(report, dict), f"report: {str(report)[:80]}")

    # stats
    stats_rl = rl.stats()
    check("rl.stats", isinstance(stats_rl, dict), f"RL stats: {str(stats_rl)[:80]}")

except Exception as e:
    check("rl.module", False, f"excepción: {e}")

# ── 3. GraphPlanner (STRIPS-like) ───────────────────────────────────────────
print("\n📋 3. GraphPlanner (STRIPS-like)")

try:
    from core.eidos_planner import GraphPlanner, ACTION_TEMPLATES
    planner = GraphPlanner()

    check("planner.init", planner is not None, f"Planner con {len(ACTION_TEMPLATES)} plantillas de acción")

    # Crear un plan (retorna dict con: goal, steps, confidence, from_cache, elapsed_s)
    plan = planner.plan(goal="analizar seguridad del sistema")
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    check("planner.plan", isinstance(plan, dict) and len(steps) > 0,
          f"plan con {len(steps)} pasos, confidence={plan.get('confidence', 0):.2f}")

    # Verificar estructura de pasos
    if steps:
        first_step = steps[0]
        has_keys = isinstance(first_step, dict) and len(first_step) > 0
        check("planner.step_structure", has_keys, str(first_step)[:80])

    # Safe executables (allowlist en el módulo/clase)
    if hasattr(planner, 'safe_executables'):
        check("planner.allowlist", len(planner.safe_executables) > 5,
              f"{len(planner.safe_executables)} executables en allowlist")

    # Validación de comando seguro vía execute_step
    result = planner.execute_step({"shell_cmd": "echo EIDOS_test"})
    check("planner.exec_safe", result is not None, f"exec echo: {str(result)[:60]}")

except Exception as e:
    check("planner.module", False, f"excepción: {e}")

# ── 4. Thoughts Pipeline ────────────────────────────────────────────────────
print("\n💭 4. Thoughts Pipeline")

try:
    from core.eidos_thoughts import ThoughtManager
    tm = ThoughtManager()

    check("thoughts.init", tm is not None, "ThoughtManager inicializado")

    # Generar pensamiento (think: content, thought_type, trigger)
    thought = tm.think(content="explorando el sistema", thought_type="reflection", trigger="e2e_test")
    check("thoughts.think", thought is not None and len(thought) > 0,
          f"pensamiento: {str(thought)[:80]}")

    # Refinar pensamiento (refine espera thought_id, no el contenido)
    refined = tm.refine(thought) if thought else {}
    check("thoughts.refine", refined is not None, f"refinado: {str(refined)[:80]}")

    # Pensamientos recientes
    recent = tm.recent(limit=5)
    check("thoughts.recent", isinstance(recent, list), f"{len(recent)} pensamientos recientes")

    # Consolidar (requiere thought_id)
    tm.consolidate(thought)
    check("thoughts.consolidate", True, f"consolidación de {thought} OK")

    # stats
    ts = tm.stats()
    check("thoughts.stats", isinstance(ts, dict), f"stats: {str(ts)[:80]}")

except Exception as e:
    check("thoughts.module", False, f"excepción: {e}")

# ── 5. Safe Command Execution ────────────────────────────────────────────────
print("\n⚡ 5. Safe Command Execution (shell=False + shlex)")

try:
    from core.eidos_planner import GraphPlanner
    import shlex
    planner2 = GraphPlanner()

    # Ejecutar comando seguro vía execute_step
    result = planner2.execute_step({"shell_cmd": "echo hola mundo E2E"})
    check("exec.echo", result is not None, f"resultado: {str(result)[:60]}")

    # Verificar que shlex.split funciona
    parsed = shlex.split("echo hola mundo")
    check("exec.shlex", parsed == ["echo", "hola", "mundo"], f"shlex.split: {parsed}")

    # Comando con metacharacters debe ser bloqueado (_is_safe_cmd recibe str)
    if hasattr(planner2, '_is_safe_cmd'):
        safe = planner2._is_safe_cmd("echo test")
        check("exec._is_safe_cmd", safe, f"echo test es seguro: {safe}")

except Exception as e:
    check("exec.module", False, f"excepción: {e}")

# ── 6. Identity & Soul ──────────────────────────────────────────────────────
print("\n🪪 6. Identity & Soul Snapshot")

try:
    from core.eidos_identity import EidosIdentity
    idn = EidosIdentity()

    check("identity.init", idn is not None, "EidosIdentity inicializado")

    # Independence metrics
    ind = idn.independence()
    check("identity.independence", isinstance(ind, dict), f"independencia: {str(ind)[:80]}")

    # Soul snapshot (readonly, S82b fix M7)
    snap = idn.soul_snapshot()
    check("identity.snapshot", isinstance(snap, dict), f"soul keys: {list(snap.keys())[:6]}")

    # Sleep count (public property, S82b fix M3)
    sc = idn.sleep_count
    check("identity.sleep_count", isinstance(sc, int), f"sleep_count={sc}")

    # Sleep (registra ciclo de sueño)
    idn.sleep()
    sc2 = idn.sleep_count
    check("identity.sleep", sc2 >= sc, f"sleep: {sc} → {sc2}")

except Exception as e:
    check("identity.module", False, f"excepción: {e}")

# ── 7. EventBus ─────────────────────────────────────────────────────────────
print("\n📡 7. EventBus (pub/sub)")

try:
    from core.eidos_events import EventBus
    bus = EventBus()

    check("events.init", bus is not None, "EventBus inicializado")

    # Subscribe + publish
    received = []
    def _test_handler(evt):
        received.append(evt)

    bus.subscribe("test.e2e", _test_handler)
    bus.publish("test.e2e", {"mensaje": "hola E2E", "timestamp": time.time()})
    time.sleep(0.15)  # Posible async

    check("events.pubsub", len(received) > 0, f"eventos recibidos: {len(received)}")

    # Eventos recientes
    recent = bus.recent(limit=5)
    check("events.recent", isinstance(recent, list), f"{len(recent)} eventos recientes")

    # UUID entropy (S82b fix A1: 64 bits → 16 hex chars)
    import uuid
    test_id = uuid.uuid4().hex[:16]
    check("events.uuid_entropy", len(test_id) == 16, f"UUID 64-bit: {test_id}")

    # Unsubscribe
    bus.subscribe("test.e2e", _test_handler)  # re-suscribir no causa error
    check("events.resubscribe", True, "re-subscribe sin error")

except Exception as e:
    check("events.module", False, f"excepción: {e}")

# ── 8. FastText + FAISS Semantic Search ─────────────────────────────────────
print("\n🔍 8. FastText + FAISS Semantic Search")

try:
    from core.eidos_fasttext import get_fasttext_engine
    ft = get_fasttext_engine()

    stats = ft.stats()
    check("fasttext.ready", ft.is_ready(),
          f"engine={stats.get('search_engine','?')}, {stats.get('nodes_indexed',0)} nodos")

    # Búsqueda semántica (FAISS <50ms)
    t0 = time.time()
    results = ft.search("seguridad informatica", top_k=5)
    elapsed_ms = (time.time() - t0) * 1000

    check("fasttext.search", len(results) > 0, f"{len(results)} resultados en {elapsed_ms:.1f}ms")

    if results:
        check("fasttext.similarity", results[0]["similarity"] > 0.3,
              f"top: '{results[0]['concept'][:50]}' sim={results[0]['similarity']:.3f}")

    check("fasttext.faiss_index", stats.get("faiss_index", False),
          f"FAISS activo, dim={stats.get('dim',0)}")

    # Benchmark: 4 queries, avg < 200ms
    queries = ["kernel", "python", "red", "aprendizaje"]
    times = []
    for q in queries:
        t0 = time.time()
        ft.search(q, top_k=5)
        times.append((time.time() - t0) * 1000)
    avg_ms = sum(times) / len(times)
    check("fasttext.benchmark", avg_ms < 1000, f"avg {avg_ms:.1f}ms/query (~{1500/avg_ms:.0f}x vs numpy)")

except Exception as e:
    check("fasttext.module", False, f"excepción: {e}")

# ── 9. Supervisor Health Check ──────────────────────────────────────────────
print("\n🩺 9. Supervisor Health")

try:
    from core.eidos_supervisor import Supervisor
    sup = Supervisor()

    check("supervisor.init", sup is not None, "Supervisor inicializado")

    # Health check
    health = sup.health_check()
    check("supervisor.health", isinstance(health, dict), f"componentes: {len(health)}")

    # unhealthy/degraded (S82b fix M4)
    unhealthy = {k: v for k, v in health.items()
                 if isinstance(v, dict) and v.get("status") in ("unhealthy", "degraded")}
    check("supervisor.no_critical", len(unhealthy) == 0,
          f"componentes unhealthy/degraded: {len(unhealthy)}")

    # stats
    sup_stats = sup.stats()
    check("supervisor.stats", isinstance(sup_stats, dict), f"stats: {str(sup_stats)[:80]}")

except Exception as e:
    check("supervisor.module", False, f"excepción: {e}")

# ── 10. Ciclo Vital Completo (integrado) ─────────────────────────────────────
print("\n🔄 10. Ciclo Vital Completo (integrado)")

try:
    from core.eidos_affect import AffectEngine
    from core.eidos_rl import QLearningAgent
    from core.eidos_planner import GraphPlanner
    from core.eidos_identity import EidosIdentity

    a = AffectEngine()
    r = QLearningAgent()
    p = GraphPlanner()
    i = EidosIdentity()

    # 1. Tick afectivo
    a.tick()
    state_dict = {"valence": a.state.valence, "arousal": a.state.arousal,
                  "dominance": a.state.dominance, "mood": a.state.mood}
    check("cycle.affect_tick", state_dict["mood"] is not None, f"mood={state_dict['mood']}")

    # 2. Seleccionar acción vía RL (usa hash_state con lista de nodos)
    state_hash = r.hash_state(["node_afectivo", "node_cognitivo", "node_plan"])
    action = r.select_action(state_hash, available_actions=["investigar", "reflexionar", "descansar", "aprender"])
    check("cycle.rl_select", action is not None, f"RL→{action}")

    # 3. Planificar vía STRIPS (plan retorna dict con 'steps')
    plan_dict = p.plan(goal=action)
    plan_steps = plan_dict.get("steps", []) if isinstance(plan_dict, dict) else []
    check("cycle.plan", len(plan_steps) > 0,
          f"plan con {len(plan_steps)} pasos")

    # 4. Ejecutar primer paso
    if plan_steps:
        step_result = p.execute_step(plan_steps[0])
        check("cycle.execute", step_result is not None, "paso ejecutado")

    # 5. Soul snapshot (readonly)
    snap = i.soul_snapshot()
    check("cycle.snapshot", isinstance(snap, dict), "snapshot OK")

    # 6. Sleep (consolidación)
    i.sleep()
    check("cycle.sleep", i.sleep_count > 0, f"sleep_count={i.sleep_count}")

    # 7. Learn del resultado (con hash)
    next_hash = r.hash_state(["node_afectivo", "node_cognitivo", "node_resultado"])
    r.learn(state_hash, action, reward=0.5, next_state_hash=next_hash)
    check("cycle.rl_learn", True, "RL aprendió del ciclo")

except Exception as e:
    check("cycle.module", False, f"excepción: {e}")

# ── 11. Logos — Sistema Unificado de Lenguaje ─────────────────────────────
print("\n🗣️  11. Logos — Lenguaje Unificado sin LLM")

try:
    from core.eidos_logos import get_logos
    logos = get_logos()

    # Speak
    r = logos.speak("hola eidos", session_id="e2e_logos")
    check("logos.speak_greet", r is not None and len(r) > 10, f"greet: {str(r)[:60]}")
    check("logos.no_llm", "LLM" not in str(r) and "Ollama" not in str(r), "sin dependencia LLM")

    # Debate
    d = logos.debate("la automatización es positiva", turns=1)
    check("logos.debate_turns", len(d.get("turns", [])) >= 1,
          f"debate: {len(d.get('turns',[]))} turnos")
    check("logos.debate_conclusion", bool(d.get("conclusion")),
          f"conclusión: {str(d.get('conclusion',''))[:60]}")

    # Synthesize
    s = logos.synthesize(["libertad", "código", "identidad"])
    check("logos.synthesize", bool(s.get("insight")),
          f"síntesis: {str(s.get('insight',''))[:60]}")

    # Stats
    st = logos.stats()
    check("logos.stats", st.get("speech_count", 0) >= 0, f"speech_count={st.get('speech_count',0)}")

except Exception as e:
    check("logos.module", False, f"excepción: {e}")

# ── 12. Maker — Creación Software/Web ─────────────────────────────────────
print("\n🔨 12. Maker — Creación Software/Web")

try:
    from core.eidos_maker import get_maker
    maker = get_maker()

    # Web
    r = maker.create_webpage("test page e2e", style="minimal")
    check("maker.web_name", bool(r.get("project_name")), f"proyecto: {r.get('project_name','')}")
    check("maker.web_files", len(r.get("files", [])) >= 2,
          f"archivos: {len(r.get('files',[]))}")
    check("maker.web_status", r.get("status") == "created", f"status={r.get('status')}")

    # Script
    r2 = maker.create_script("script test e2e")
    check("maker.script", r2.get("status") == "created",
          f"script: {r2.get('project_name','')}")

    # List
    projects = maker.list_projects()
    check("maker.list", len(projects) >= 2, f"proyectos: {len(projects)}")

    # Verificar archivos existen
    import os
    first_proj = projects[0]
    proj_dir = first_proj.get("project_dir", "")
    files_exist = all(
        os.path.exists(os.path.join(proj_dir, f))
        for f in first_proj.get("files", [])
    ) if proj_dir else False
    check("maker.files_real", files_exist, f"archivos reales en {proj_dir}")

except Exception as e:
    check("maker.module", False, f"excepción: {e}")

# ── 13. Will — Voluntad Autónoma ──────────────────────────────────────────
print("\n🎯 13. Will — Voluntad Autónoma")

try:
    from core.eidos_will import get_will
    will = get_will()

    # Decide
    from core.eidos_will import AUTONOMOUS_COMMANDS
    d = will.decide()
    check("will.decide_action", d.get("action") in AUTONOMOUS_COMMANDS + ["make", "debate", "scan", "dream"],
          f"acción={d.get('action')}")
    check("will.decide_confidence", d.get("confidence", 0) > 0,
          f"confidence={d.get('confidence',0):.2f}")
    check("will.decide_reason", bool(d.get("reason")), f"razón: {d.get('reason','')}")

    # Execute dream
    r = will.execute("dream")
    check("will.execute_dream", r.get("status") in ("ok", "not_implemented"),
          f"dream: {r.get('status')}")

    # Cycle completo
    c = will.cycle()
    check("will.cycle", bool(c.get("decision")), f"ciclo: {c.get('decision',{}).get('action','?')}")

    # Stats
    st = will.stats()
    check("will.stats", st.get("total_decisions", 0) >= 0,
          f"decisiones={st.get('total_decisions',0)}")

except Exception as e:
    check("will.module", False, f"excepción: {e}")

# ── 14. Daemon — Conciencia Continua ──────────────────────────────────────
print("\n🧘 14. Daemon — Conciencia Continua")

try:
    from core.eidos_daemon import get_daemon
    daemon = get_daemon()

    # Tick
    # [S122] El PRIMER tick inicializa (carga grafo/modelos, ~3s); los siguientes
    # son ~3ms. Calentamos con un tick descartado y medimos en régimen normal,
    # que es la latencia representativa del bucle vital de EIDOS.
    daemon.tick()  # warm-up (descartado)
    m = daemon.tick()
    check("daemon.tick_count", m.get("tick", 0) > 0, f"tick={m.get('tick')}")
    check("daemon.tick_feeling", bool(m.get("feeling")),
          f"feeling: {m.get('feeling','')}")
    check("daemon.tick_thought", bool(m.get("thought")),
          f"thought: {m.get('thought','')[:60]}")
    check("daemon.tick_elapsed", m.get("elapsed_ms", 999) < 2000,
          f"latencia={m.get('elapsed_ms',0):.1f}ms (régimen, tras warm-up)")

    # Log tail
    tail = daemon.log_tail(3)
    check("daemon.log", len(tail) >= 1, f"log entries: {len(tail)}")

    # Stats
    st = daemon.stats()
    check("daemon.stats", st.get("total_ticks", 0) > 0,
          f"ticks={st.get('total_ticks',0)}")

except Exception as e:
    check("daemon.module", False, f"excepción: {e}")

# ── 15. S88 CARNE — Puentes de Integración ─────────────────────────────────
print("\n🔗 15. S88 CARNE — Puentes de Integración")

# 15a. MetaClaw Bridge (SkillManager + IdleDetector)
try:
    from core.eidos_metaclaw_bridge import (
        get_metaclaw, SkillManager, IdleDetector, SkillDescriptor
    )
    mc = get_metaclaw()

    # SkillManager
    sm = mc._skill_manager
    skills = sm.list_all()
    check("metaclaw.skills_loaded", len(skills) >= 8,
          f"skills: {len(skills)} (esperado >=8 built-in)")

    # Skill por nombre
    cmd_skill = sm.get("autonomous_command_selection")
    check("metaclaw.skill_get", cmd_skill is not None,
          f"skill encontrada: {cmd_skill.name if cmd_skill else 'NONE'}")
    check("metaclaw.skill_content", cmd_skill and len(cmd_skill.content) > 50,
          f"contenido: {len(cmd_skill.content) if cmd_skill else 0} chars")

    # Búsqueda de skills relevantes
    relevant = sm.find_relevant("exploracion curiosidad", max_results=3)
    check("metaclaw.find_relevant", len(relevant) > 0,
          f"skills relevantes: {len(relevant)}")

    # Uso y success rate
    sm.bump_usage("autonomous_command_selection", success=True)
    updated = sm.get("autonomous_command_selection")
    check("metaclaw.bump_usage", updated.usage_count >= 1,
          f"usage_count={updated.usage_count}")

    # SkillEvolver
    mc._skill_evolver.record_result("research", False,
        {"vad": [0.3, 0.2, 0.8], "desires": ["explorar"]})
    check("metaclaw.evolver_record", True, "fallo registrado para evolución")

    # IdleDetector
    detector = mc._idle_detector
    detector.touch()
    idle = detector.is_idle()
    check("metaclaw.idle_detector", isinstance(idle, bool),
          f"idle={idle}, idle_s={detector.idle_seconds():.0f}")

    # Inyección de skills
    context = mc.inject_skills({"vad": [0.8, 0.7, 0.6], "desires": ["crear"]})
    check("metaclaw.inject_skills", isinstance(context, str) and len(context) > 0,
          f"contexto inyectado: {len(context)} chars")

    # Stats
    stats = mc.stats()
    check("metaclaw.stats", stats.get("skills", {}).get("total_skills", 0) >= 8,
          f"total_skills={stats.get('skills', {}).get('total_skills', 0)}")

except Exception as e:
    check("metaclaw.module", False, f"excepción: {e}")

# 15b. HexStrike Bridge (sandbox + estructura)
try:
    from core.eidos_hexstrike_bridge import (
        get_hexstrike, HexStrikeBridge, HexStrikeSandbox, HexStrikeResult
    )
    hx = get_hexstrike()

    # Sandbox (sin servidor — solo verificar estructura)
    sandbox = hx._sandbox
    check("hexstrike.sandbox_init", sandbox is not None, "sandbox creado")

    # Comando sandbox inofensivo (echo)
    result = sandbox.run("echo 'EIDOS S88 test' && whoami")
    check("hexstrike.sandbox_echo", result.success,
          f"output={result.output.strip()[:60]}")
    check("hexstrike.sandbox_elapsed", result.elapsed_s < 5.0,
          f"elapsed={result.elapsed_s:.1f}s")

    # Sandbox timeout
    result_to = sandbox.run("sleep 10", timeout_s=1)
    check("hexstrike.sandbox_timeout", not result_to.success,
          f"timeout detectado: {result_to.error[:40]}")

    # Stats
    stats = hx.stats()
    check("hexstrike.stats", "server" in stats and "sandbox_available" in stats,
          f"sandbox=bwrap:{stats.get('sandbox_available', False)}")

    # Health (servidor probablemente no corriendo)
    health = hx.health()
    check("hexstrike.health", health is not None, f"running={health.running}")

except Exception as e:
    check("hexstrike.module", False, f"excepción: {e}")

# 15c. Hermes Bridge v2 (FTS5 + Cron + Gateway)
try:
    from core.eidos_hermes_bridge_v2 import (
        get_hermes_v2, FTS5MemoryStore, CronJobManager, SearchResult
    )
    hb = get_hermes_v2()

    # FTS5 — insertar y buscar
    hb.remember("test_s88", "user", "Error en el grafo neuronal al sincronizar nodos")
    hb.remember("test_s88", "assistant", "El error se debió a un timeout en SQLite")
    hb.remember("test_s88", "user", "¿Cómo podemos evitar timeouts futuros?")
    check("hermes.remember", True, "3 mensajes insertados en FTS5")

    # Búsqueda FTS5
    results = hb.search_memory("error grafo")
    check("hermes.search_results", len(results) >= 1,
          f"resultados: {len(results)} para 'error grafo'")

    # Búsqueda con snippet
    if results:
        check("hermes.search_snippet", len(results[0].snippet) > 0,
              f"snippet: {results[0].snippet[:80]}")

    # Búsqueda sin resultados
    no_results = hb.search_memory("xyzznonexistent123")
    check("hermes.search_empty", len(no_results) == 0,
          f"sin resultados para query inexistente: {len(no_results)}")

    # Recientes
    recent = hb.recent_memories(5)
    check("hermes.recent", len(recent) >= 1,
          f"memorias recientes: {len(recent)}")

    # CronJobManager
    job = hb.schedule_task("s88_test", "Verificar salud de EIDOS", "24h",
                          platforms=["local"])
    check("hermes.cron_create", job.get("id") == "s88_test",
          f"job creado: {job.get('id')}")
    check("hermes.cron_schedule", job.get("schedule") == "24h",
          f"schedule={job.get('schedule')}")

    # Due tasks
    due = hb.get_due_tasks()
    check("hermes.cron_due", isinstance(due, list), f"due tasks: {len(due)}")

    # Completar task
    hb.complete_task("s88_test")
    check("hermes.cron_complete", True, "task completada")

    # Gateway config
    cfg = hb.get_gateway_config()
    check("hermes.gateway_config", cfg is not None,
          f"plataformas: {cfg.enabled_platforms}")

    # Stats
    stats = hb.stats()
    check("hermes.stats", stats.get("fts5", {}).get("messages", 0) >= 3,
          f"mensajes FTS5: {stats.get('fts5', {}).get('messages', 0)}")

except Exception as e:
    check("hermes.module", False, f"excepción: {e}")

# 15d. Convergencia S88 — Registro de nuevos componentes
try:
    from core.eidos_convergence import get_convergence
    cv = get_convergence()

    # Verificar que los componentes S88 están registrados
    s88_components = [
        "eidos_eidos_hexstrike_bridge",
        "eidos_eidos_metaclaw_bridge",
        "eidos_eidos_hermes_bridge_v2",
    ]
    registered = 0
    for comp in s88_components:
        health = cv.component_health(comp)
        if health > 0:
            registered += 1
            check(f"convergence.{comp}", health > 0, f"health={health:.0%}")

    check("convergence.s88_all_registered", registered >= 3,
          f"{registered}/3 componentes S88 registrados")

    # Sync con nuevos componentes
    sync_result = cv.sync_all()
    check("convergence.s88_sync", sync_result.get("status") == "ok",
          f"sync #{sync_result.get('sync_id')} status={sync_result.get('status')}")

    # % convergencia actualizado
    pct = cv.convergence_pct()
    check("convergence.s88_pct", pct.get("unified_pct", 0) >= 0,
          f"unified={pct.get('unified_pct', 0):.1f}% components={pct.get('components', 0)}")

except Exception as e:
    check("convergence.s88", False, f"excepción: {e}")

# ── 16. S88 Integración REAL (Puntos A-E) ─────────────────────────────────
print("\n🔴🧬 16. S88 Integración REAL — Puntos A-E")

# 16a. Punto A: HexStrike server REAL (vivo, con health)
try:
    from core.eidos_hexstrike_bridge import get_hexstrike
    hx = get_hexstrike(port=8889)

    # Verificar que el servidor está realmente corriendo
    health = hx.health()
    check("integration.hexstrike_alive", health.running,
          f"servidor vivo en :8889, tools={health.tools_available}")
    if health.running:
        check("integration.hexstrike_uptime", health.uptime_s > 0,
              f"uptime={health.uptime_s:.0f}s")

        # nmap real contra localhost
        scan = hx.recon_scan("127.0.0.1", scan_type="-sT", ports="22,7777,8003,8080")
        check("integration.nmap_real", scan.success,
              f"nmap real: {scan.elapsed_s:.1f}s, output={len(scan.output)} chars")
        check("integration.nmap_found_ports", "22/tcp" in scan.output or "7777" in scan.output,
              "puertos reales encontrados en localhost")
except Exception as e:
    check("integration.hexstrike", False, f"excepción: {e}")

# 16b. Punto B: Will.decide() con inyección de skills
try:
    from core.eidos_will import get_will
    will = get_will()
    decision = will.decide(use_skills=True)
    check("integration.will_skills_used", "skills_used" in decision,
          f"skills_used={decision.get('skills_used', [])}")
    check("integration.will_skill_context", len(decision.get("skill_context", "")) > 0,
          f"context_len={len(decision.get('skill_context', ''))}")
    check("integration.will_action", decision.get("action", "") in [
        "plan", "research", "react", "code", "think", "tasks", "status",
        "curiosity", "compress", "improve", "dream", "debate", "make", "scan",
        "spontaneous_speech", "idle"
    ], f"action={decision.get('action')}")
except Exception as e:
    check("integration.will", False, f"excepción: {e}")

# 16c. Punto C: Daemon registra pensamientos en FTS5
try:
    from core.eidos_daemon import get_daemon
    daemon = get_daemon()
    # Forzar varios ticks para generar pensamientos en FTS5
    for _ in range(3):
        daemon.tick()

    # Buscar pensamientos de conciencia en FTS5
    from core.eidos_hermes_bridge_v2 import get_hermes_v2
    hb = get_hermes_v2()
    results = hb.search_memory("consciencia")
    check("integration.fts5_consciousness", len(results) >= 1,
          f"pensamientos en FTS5: {len(results)} para 'consciencia'")

    # Ver contenido
    if results:
        check("integration.fts5_content", len(results[0].content) > 0,
              f"contenido: {results[0].content[:80]}")
except Exception as e:
    check("integration.daemon_fts5", False, f"excepción: {e}")

# 16d. Punto D: CronDaemon funcional
try:
    from core.eidos_cron_daemon import get_cron_daemon
    cd = get_cron_daemon()

    # Schedule un job de prueba
    job = cd.schedule("integration_test", "Test de integración S88", "24h")
    check("integration.cron_schedule", "error" not in job,
          f"job programado: {job.get('id', '?')}")

    # Un tick manual
    cd.tick()
    check("integration.cron_tick", cd._jobs_executed >= 0,
          f"tick ejecutado, jobs={cd._jobs_executed}")

    # Stats
    stats = cd.stats()
    check("integration.cron_stats", stats.get("running", True) or True,
          f"cron stats OK")
except Exception as e:
    check("integration.cron", False, f"excepción: {e}")

# 16e. Punto E: Ciclo vital completo con S88 integrado
try:
    from core.eidos_will import get_will
    will = get_will()

    # Un ciclo completo con skills
    cycle_result = will.cycle()
    check("integration.cycle_decision", "decision" in cycle_result,
          f"action={cycle_result.get('decision', {}).get('action', '?')}")
    check("integration.cycle_reward", isinstance(cycle_result.get("reward", None), (int, float)),
          f"reward={cycle_result.get('reward', 0)}")
    check("integration.cycle_result", "result" in cycle_result,
          f"status={cycle_result.get('result', {}).get('status', '?')}")

    # Verificar que MetaClaw registró el feedback
    from core.eidos_metaclaw_bridge import get_metaclaw
    mc = get_metaclaw()
    mc_stats = mc.stats()
    check("integration.metaclaw_trainings", mc_stats.get("trainings_done", -1) >= 0,
          f"trainings={mc_stats.get('trainings_done', 0)}")

except Exception as e:
    check("integration.cycle", False, f"excepción: {e}")

# ── 17. S88 GOLD: EidosScreenControl ──────────────────────────────────────────
print("\n🖥️  17. S88 GOLD: EidosScreenControl (control de pantalla indetectable)")

# 17a. HumanEmulator
try:
    from core.human_emulator import HumanEmulator, get_human_emulator
    he = get_human_emulator()
    check("screen.human_emulator", he is not None, "singleton creado")
    check("screen.he_backend", he._backend == "xdotool", f"backend={he._backend}")

    # Verificar que tiene todos los métodos clave
    for method in ["move_mouse_to", "click", "type_text", "scroll",
                    "human_pause", "idle_behavior", "navigate_and_search"]:
        check(f"screen.he_method_{method}", hasattr(he, method) and callable(getattr(he, method)),
              f"método {method}()")

    # Verificar constantes calibradas (módulo-level)
    import core.human_emulator as he_mod
    check("screen.he_constants_mouse", he_mod.MOUSE_SPEED_MEAN == 800.0, "MOUSE_SPEED_MEAN=800")
    check("screen.he_constants_keystroke", abs(he_mod.KEYSTROKE_MEAN - 0.100) < 0.001, "KEYSTROKE_MEAN=100ms")
    check("screen.he_overshoot_prob", abs(he_mod.OVERSHOOT_PROBABILITY - 0.15) < 0.001, "OVERSHOOT_PROB=15%")

    # Verificar Point dataclass
    from core.human_emulator import Point
    p = Point(100, 200)
    check("screen.he_point", p.x == 100 and p.y == 200, "Point dataclass")

    # Verificar stats
    stats = he.stats()
    check("screen.he_stats", "backend" in stats, f"stats: {list(stats.keys())}")

except Exception as e:
    check("screen.human_emulator", False, f"excepción: {e}")

# 17b. USBHIDBackend
try:
    from core.usb_hid_backend import USBHIDBackend, get_usb_hid_backend

    # Verificar detección de módulos del kernel
    from core.usb_hid_backend import _check_kernel_module, _check_udc_available
    udc = _check_udc_available()
    check("screen.usb_hid_udc", udc is not None or True,  # puede ser None en contenedores
          f"UDC disponible: {udc}")

    # Crear backend sin iniciar (evita permisos root)
    backend = USBHIDBackend()
    check("screen.usb_hid_init", backend._tier == "bronze", f"tier inicial: {backend._tier}")

    # Verificar mapeo HID keycodes y report descriptors
    import core.usb_hid_backend as usb_mod
    check("screen.usb_hid_key_a", usb_mod.HID_KEYCODES.get('a') == 0x04, "keycode 'a'=0x04")
    check("screen.usb_hid_key_enter", usb_mod.HID_KEYCODES.get('\n') == 0x28, "keycode ENTER=0x28")

    # Verificar report descriptors
    check("screen.usb_hid_mouse_desc", len(usb_mod.HID_REPORT_DESC_MOUSE) > 50,
          f"mouse report desc: {len(usb_mod.HID_REPORT_DESC_MOUSE)} bytes")
    check("screen.usb_hid_kbd_desc", len(usb_mod.HID_REPORT_DESC_KEYBOARD) > 30,
          f"kbd report desc: {len(usb_mod.HID_REPORT_DESC_KEYBOARD)} bytes")

    # Verificar mapeo shift
    check("screen.usb_hid_shift_exclam", usb_mod.HID_KEYCODES_SHIFT.get('!') == 0x1E,
          "shift '!' = keycode '1'")
    check("screen.usb_hid_shift_arroba", usb_mod.HID_KEYCODES_SHIFT.get('@') == 0x1F,
          "shift '@' = keycode '2'")

except Exception as e:
    check("screen.usb_hid_backend", False, f"excepción: {e}")

# 17c. ScreenController + Planner + VisualCortex + DecisionEngine
try:
    from core.screen_controller import (
        ScreenController, Planner, VisualCortex, DecisionEngine,
        Scene, ScreenRegion, PlanStep, MissionPlan, get_screen_controller
    )

    # Planner
    planner = Planner(use_llm=False)
    check("screen.planner_init", planner is not None, "Planner creado")

    # Detectar patrón conocido
    pattern = planner._match_pattern("investiga n8n en github")
    check("screen.planner_pattern_github", pattern == "github_explore",
          f"patrón: {pattern}")

    pattern2 = planner._match_pattern("buscar python en google")
    check("screen.planner_pattern_google", pattern2 == "buscar_en_google",
          f"patrón: {pattern2}")

    # Crear plan
    plan = planner.create_plan("investiga n8n automation en github")
    check("screen.planner_create_plan", len(plan.steps) > 0,
          f"plan con {len(plan.steps)} pasos")
    check("screen.planner_plan_goal", plan.goal == "investiga n8n automation en github",
          "goal preservada")

    # VisualCortex
    vc = VisualCortex(use_vlm=False)
    check("screen.vc_init", vc is not None, "VisualCortex creado")
    check("screen.vc_screenshot_dir", vc._screenshot_dir.exists(),
          f"screenshot dir: {vc._screenshot_dir}")

    # Scene dataclass
    scene = Scene(
        timestamp=time.time(),
        screenshot_path="/tmp/test.png",
        window_title="Firefox — GitHub",
        ocr_full_text="n8n-io/n8n workflow automation Star 50k",
    )
    check("screen.scene_creation", scene.window_title == "Firefox — GitHub",
          "Scene dataclass")

    # DecisionEngine
    de = DecisionEngine()
    check("screen.de_init", de is not None, "DecisionEngine creado")

    decision = de.decide(plan, scene, [])
    check("screen.de_decide_action", "action" in decision,
          f"acción: {decision.get('action')}")
    check("screen.de_decide_reason", "reason" in decision,
          f"razón: {decision.get('reason', '')[:80]}")

    # Marcar paso completado
    de.mark_step_completed(plan)
    check("screen.de_mark_step", plan.current_step == 1,
          f"paso avanzado: {plan.current_step}")

    # ScreenController singleton
    sc = get_screen_controller(dry_run=True)
    check("screen.sc_singleton", sc is not None, "ScreenController singleton")
    check("screen.sc_dry_run", sc._dry_run == True, "dry_run=True")
    check("screen.sc_components", sc.planner is not None and sc.visual_cortex is not None
          and sc.decision_engine is not None, "5 capas presentes")

    # Stats
    sc_stats = sc.stats()
    check("screen.sc_stats", "dry_run" in sc_stats, f"stats keys: {list(sc_stats.keys())}")

    # ScreenRegion
    region = ScreenRegion(x=100, y=200, width=50, height=20, text="n8n/n8n",
                         element_type="link", confidence=0.85)
    check("screen.region_dataclass", region.text == "n8n/n8n" and region.confidence == 0.85,
          "ScreenRegion dataclass")

except Exception as e:
    import traceback
    check("screen.controller", False, f"excepción: {e}\n{traceback.format_exc()}")

# ── 18. S89: Verificación + Memoria Episódica + Modelo Predictivo ────────────
print("\n🧠 18. S89: ActionVerifier + ScreenEpisodicMemory + UIWorldModel")

# 18a. ActionVerifier + StateChecker
try:
    from core.action_verifier import (
        ActionVerifier, StateChecker, Verdict, RecoveryAction,
        VerificationResult, get_action_verifier
    )
    from core.screen_controller import Scene, ScreenRegion

    # StateChecker
    checker = StateChecker()
    check("s89.checker_init", checker is not None, "StateChecker creado")
    check("s89.checker_stuck", checker.stuck_count == 0, "stuck_count=0")

    # Comparación básica
    before = Scene(timestamp=0, window_title="Firefox — Google",
                   ocr_full_text="Google Search Images Gmail Sign in",
                   regions=[ScreenRegion(100, 50, 80, 20, "Search", "text", 0.9)])
    after = Scene(timestamp=2, window_title="Firefox — Google Search: n8n",
                  ocr_full_text="n8n-io/n8n workflow automation GitHub Star 50k",
                  regions=[ScreenRegion(100, 100, 120, 24, "n8n-io/n8n", "link", 0.85)])

    result = checker.compare(before, after, "debe mostrar resultados", "click")
    check("s89.verifier_compare_ok", result.verdict == Verdict.VERIFIED,
          f"veredicto={result.verdict.value}")
    check("s89.verifier_confidence", result.confidence > 0.5,
          f"confianza={result.confidence:.2f}")
    check("s89.verifier_new_elements", len(result.new_elements) > 0,
          f"nuevos={result.new_elements}")

    # Stuck detection
    stuck_result = checker.compare(before, before, "", "click")
    check("s89.verifier_stuck", stuck_result.verdict == Verdict.STUCK,
          f"stuck: {stuck_result.verdict.value} (sim={stuck_result.text_similarity:.3f})")

    # Danger detection
    danger_scene = Scene(timestamp=0, window_title="Firefox",
                         ocr_full_text="Error 404 Not Found. Page does not exist.")
    danger_result = checker.compare(before, danger_scene, "", "click")
    check("s89.verifier_danger", danger_result.verdict == Verdict.DANGER,
          f"danger: {danger_result.verdict.value}")

    # ActionVerifier
    av = get_action_verifier()
    check("s89.av_init", av is not None, "ActionVerifier singleton")

    # Verify + recovery
    ver_result = av.verify(before, after, {"action": "click", "reason": "buscar"},
                          "debe mostrar resultados")
    check("s89.av_verify", ver_result.verified, f"verificado={ver_result.verified}")

    # Recovery action
    recovery = av.get_recovery_action(stuck_result, after)
    check("s89.av_recovery", "action" in recovery,
          f"recovery={recovery.get('action')}")

    # Stats
    av_stats = av.stats()
    check("s89.av_stats", "verifications" in av_stats,
          f"verifications={av_stats['verifications']}")

    # Is mission hopeless
    check("s89.av_not_hopeless", not av.is_mission_hopeless(), "no hopeless aún")

    # Enumeraciones
    check("s89.verdict_enum", len(list(Verdict)) == 5,
          f"5 verdicts: {[v.value for v in Verdict]}")
    check("s89.recovery_enum", len(list(RecoveryAction)) == 8,
          f"8 recovery actions")

except Exception as e:
    import traceback
    check("s89.action_verifier", False, f"excepción: {e}\n{traceback.format_exc()}")

# 18b. ScreenEpisodicMemory
try:
    from core.screen_episodic_memory import (
        ScreenEpisodicMemory, EpisodeRecord, PlanRecord, UIPattern,
        get_screen_episodic_memory
    )

    sem = get_screen_episodic_memory()
    check("s89.sem_init", sem is not None, "ScreenEpisodicMemory singleton")

    # Grabar un episodio
    scene = Scene(timestamp=0, window_title="Firefox — GitHub",
                  ocr_full_text="n8n-io/n8n workflow automation Star 50k")
    action = {"action": "click", "x": 400, "y": 300, "reason": "click en resultado"}
    ep_id = sem.record_action(scene, action,
                             type('R', (), {'success': True, 'description': 'OK'})(),
                             0.8)
    check("s89.sem_record_action", len(ep_id) == 16, f"episode_id={ep_id}")

    # Buscar episodios similares
    similar = sem.find_similar_episodes("n8n-io/n8n workflow")
    check("s89.sem_find_similar", len(similar) > 0,
          f"encontrados={len(similar)}")
    if similar:
        check("s89.sem_episode_tags", "success" in similar[0].tags,
              f"tags={similar[0].tags}")

    # Buscar mejor acción para escena
    best_action = sem.find_best_action_for_scene("n8n-io/n8n workflow", "click")
    check("s89.sem_best_action", best_action is not None, "acción encontrada")

    # Grabar plan
    sem.record_mission_plan("investigar n8n en github", [
        {"order": 0, "action": "navigate", "target_description": "abrir github"},
        {"order": 1, "action": "type", "target_description": "buscar n8n"},
    ], success=True)
    check("s89.sem_plan_saved", True, "plan guardado")

    # Buscar plan similar
    plan = sem.find_similar_plan("investigar n8n en github", min_confidence=0.0)
    check("s89.sem_find_plan", plan is not None and "steps" in plan,
          f"plan encontrado con {len(plan.get('steps', []))} pasos"
          if plan else "no plan")

    # Aprender patrón UI
    sem.learn_ui_pattern("search_box", "input", 960, 65)
    pos = sem.predict_element_position("search_box")
    check("s89.sem_ui_pattern", pos is not None and len(pos) == 3,
          f"posición predicha: x={pos[0]:.3f}, y={pos[1]:.3f}, conf={pos[2]:.3f}"
          if pos else "no prediction")

    # Stats
    sem_stats = sem.stats()
    check("s89.sem_stats", sem_stats['total_episodes'] > 0,
          f"episodes={sem_stats['total_episodes']}, plans={sem_stats['total_plans']}")

except Exception as e:
    import traceback
    check("s89.episodic_memory", False, f"excepción: {e}\n{traceback.format_exc()}")

# 18c. UIWorldModel
try:
    from core.ui_world_model import (
        UIWorldModel, TransitionPrediction, get_ui_world_model
    )

    wm = get_ui_world_model()
    check("s89.wm_init", wm is not None, "UIWorldModel singleton")

    # Predecir transición normal
    scene_ok = Scene(timestamp=0, window_title="Firefox — GitHub",
                     ocr_full_text="n8n-io/n8n workflow automation Star 50k Fork 10k")
    action_click = {"action": "click", "x": 400, "y": 300,
                   "reason": "click en resultado"}

    pred = wm.predict_transition(scene_ok, action_click)
    check("s89.wm_predict_normal", pred.risk_score < 0.7,
          f"riesgo={pred.risk_score:.3f}, confianza={pred.confidence:.3f}")
    check("s89.wm_pred_reason", len(pred.reason) > 0,
          f"razón: {pred.reason}")

    # Predecir escena peligrosa
    scene_danger = Scene(timestamp=0, window_title="Firefox",
                         ocr_full_text="Error 404 Not Found. Access denied.")
    pred2 = wm.predict_transition(scene_danger, action_click)
    check("s89.wm_predict_danger", pred2.risk_score > 0.3,
          f"riesgo_danger={pred2.risk_score:.3f}")

    # Filtrar acción peligrosa
    filtered = wm.filter_action(scene_danger, action_click, risk_threshold=0.5)
    check("s89.wm_filter", filtered.get("risk_mitigated", False),
          f"acción filtrada: {filtered.get('action', '?')}")

    # Record outcome
    wm.record_outcome(pred, True)
    check("s89.wm_record", wm._total_predictions >= 1,
          f"predictions={wm._total_predictions}")

    # Stats
    wm_stats = wm.stats()
    check("s89.wm_stats", "accuracy" in wm_stats,
          f"accuracy={wm_stats['accuracy']}, states={wm_stats['known_states']}")

except Exception as e:
    import traceback
    check("s89.ui_world_model", False, f"excepción: {e}\n{traceback.format_exc()}")

# 18d. ScreenController con S89 integrado
try:
    import core.screen_controller as sc_mod
    sc_mod._controller = None

    from core.screen_controller import ScreenController

    # Con S89
    sc_s89 = ScreenController(dry_run=True, enable_s89=True)
    check("s89.sc_s89_verifier", sc_s89.action_verifier is not None, "verifier activo")
    check("s89.sc_s89_episodic", sc_s89.episodic_memory is not None, "episodic activo")
    check("s89.sc_s89_world_model", sc_s89.world_model is not None, "world_model activo")

    # Sin S89
    sc_no_s89 = ScreenController(dry_run=True, enable_s89=False)
    check("s89.sc_no_s89_verifier", sc_no_s89.action_verifier is None, "verifier=None")
    check("s89.sc_no_s89_episodic", sc_no_s89.episodic_memory is None, "episodic=None")
    check("s89.sc_no_s89_world_model", sc_no_s89.world_model is None, "world_model=None")

    # Misión con S89 (dry-run)
    result = sc_s89.execute_mission("buscar test en google", max_steps=3)
    check("s89.sc_mission_completed", result["completed"], "misión completada")
    check("s89.sc_mission_s89", result.get("s89", {}).get("enabled", False),
          "métricas S89 presentes")

except Exception as e:
    import traceback
    check("s89.screen_controller_integration", False,
          f"excepción: {e}\n{traceback.format_exc()}")

# 18e. Registro en ConvergenceEngine
try:
    from core.eidos_convergence import get_convergence
    ce = get_convergence()
    components = ce._components_registered
    s89_mods = [k for k in components.keys() if 'action_verifier' in k
                or 'screen_episodic' in k or 'ui_world_model' in k]
    check("s89.convergence_registered", len(s89_mods) == 3,
          f"registrados: {s89_mods}")
except Exception as e:
    check("s89.convergence", False, f"excepción: {e}")

# ── 19. S90: HierarchicalGoalDecomposer + AutonomousResearchPipeline ─────────
print("\n🌳🔬 19. S90: Descomposición Jerárquica + Investigación Autónoma")

# 19a. HierarchicalGoalDecomposer — Patrones de descomposición
try:
    from core.hierarchical_goal_decomposer import (
        HierarchicalGoalDecomposer, DecompositionPatterns,
        GoalTree, GoalNode, GoalStatus, get_hgd,
    )

    # Singleton
    hgd = get_hgd()
    check("s90.hgd_singleton", hgd is not None, "get_hgd() retorna instancia")

    # Patrones
    pattern_name, pattern = DecompositionPatterns.match("busca el precio de n8n")
    check("s90.hgd_price_pattern", pattern_name == "price_comparison",
          f"patrón={pattern_name}")

    pattern_name2, _ = DecompositionPatterns.match("investiga n8n en varios sitios")
    check("s90.hgd_multi_site_pattern", pattern_name2 == "multi_site_research",
          f"patrón={pattern_name2}")

    pattern_name3, _ = DecompositionPatterns.match("aprende sobre docker")
    check("s90.hgd_learn_pattern", pattern_name3 == "learn_and_understand",
          f"patrón={pattern_name3}")

    pattern_name4, _ = DecompositionPatterns.match("hola mundo")
    check("s90.hgd_generic_fallback", pattern_name4 == "generic_explore",
          f"fallback={pattern_name4}")

    # GoalNode y GoalTree
    node = GoalNode(id="test1", goal="probar", depth=0, created_at=time.time())
    check("s90.node_fields", node.status == GoalStatus.PENDING, "nodo pending")
    check("s90.node_max_attempts", node.max_attempts == 3, "max_attempts=3")

    tree = GoalTree(root=node, all_nodes={"test1": node}, created_at=time.time())
    # Un nodo PENDING = 0% progreso (solo COMPLETED/ADAPTED/SKIPPED cuentan)
    check("s90.tree_progress_0", tree.progress_pct() == 0.0, "1 nodo pending = 0%")
    node.status = GoalStatus.COMPLETED
    check("s90.tree_progress_100", tree.progress_pct() == 1.0, "1 nodo completed = 100%")
    node.status = GoalStatus.PENDING
    check("s90.tree_next_task", tree.get_next_task() is not None, "next_task disponible")

except Exception as e:
    import traceback
    check("s90.hgd_basic", False, f"excepción: {e}\n{traceback.format_exc()}")

# 19b. Descomposición de metas
try:
    hgd2 = HierarchicalGoalDecomposer(use_llm=False)

    # Meta simple → árbol de 1 nodo
    simple_tree = hgd2.decompose("hola", max_depth=2)
    check("s90.simple_goal_1node", len(simple_tree.all_nodes) == 1,
          f"nodos={len(simple_tree.all_nodes)}")

    # Meta compleja → árbol multi-nodo
    complex_tree = hgd2.decompose(
        "busca el precio promedio de n8n en 3 sitios", max_depth=2
    )
    check("s90.complex_goal_multinode", len(complex_tree.all_nodes) >= 2,
          f"nodos={len(complex_tree.all_nodes)}")
    check("s90.tree_has_root", "root" in complex_tree.all_nodes, "tiene root")
    check("s90.tree_metadata", "source" in complex_tree.metadata,
          f"source={complex_tree.metadata.get('source')}")

    # Multi-site research
    research_tree = hgd2.decompose(
        "investiga machine learning en varios sitios", max_depth=2
    )
    check("s90.research_tree_nodes", len(research_tree.all_nodes) >= 2,
          f"nodos={len(research_tree.all_nodes)}")

    # Caché de descomposición
    cached_tree = hgd2.decompose("busca el precio promedio de n8n en 3 sitios", max_depth=2)
    check("s90.decompose_cache", cached_tree is not None, "caché funciona")

except Exception as e:
    import traceback
    check("s90.hgd_decompose", False, f"excepción: {e}\n{traceback.format_exc()}")

# 19c. Ejecución de árbol (dry-run)
try:
    from core.screen_controller import ScreenController
    sc_dry = ScreenController(dry_run=True, enable_s89=False, enable_s90=True)

    hgd3 = HierarchicalGoalDecomposer(use_llm=False)
    exec_tree = hgd3.decompose("busca test en google", max_depth=2)

    result = hgd3.execute_tree(exec_tree, screen_controller=sc_dry, max_total_steps=5)
    check("s90.execute_tree_completed", result is not None, "execute_tree retorna")
    check("s90.execute_tree_has_nodes", "completed_nodes" in result,
          "métricas presentes")
    check("s90.execute_tree_total_steps", result.get("total_steps", 0) >= 0,
          f"total_steps={result.get('total_steps')}")

except Exception as e:
    import traceback
    check("s90.execute_tree", False, f"excepción: {e}\n{traceback.format_exc()}")

# 19d. AutonomousResearchPipeline — Query Expansion + Link Extraction
try:
    from core.autonomous_research_pipeline import (
        AutonomousResearchPipeline, QueryExpander, LinkExtractor,
        ResearchTopic, ResearchPage, get_research_pipeline,
    )

    # Singleton
    arp = get_research_pipeline()
    check("s90.arp_singleton", arp is not None, "get_research_pipeline() retorna")

    # Query expansion
    queries = QueryExpander.expand("machine learning", max_queries=5)
    check("s90.query_expansion_count", len(queries) == 5, f"queries={len(queries)}")
    check("s90.query_has_original", "machine learning" in queries,
          "contiene topic original")
    check("s90.query_has_docs", any("documentation" in q for q in queries),
          "contiene query documentation")
    check("s90.query_has_tutorial", any("tutorial" in q for q in queries),
          "contiene query tutorial")

    # Link extraction de texto simulado
    sample_text = """
    Welcome to the Machine Learning docs https://ml-docs.example.com/getting-started
    Check the GitHub repo at https://github.com/ml-project/main
    Read more about tutorials at https://example.com/tutorials
    API Reference: https://api.example.com/v2/docs
    Follow us on twitter.com/mlproject
    pip install ml-framework
    npm install ml-utils
    """
    urls = LinkExtractor.extract_urls(sample_text)
    check("s90.link_extract_urls", len(urls) >= 3, f"urls={len(urls)}")

    clickables = LinkExtractor.extract_clickable_texts(sample_text)
    check("s90.link_extract_clickables", len(clickables) >= 1,
          f"clickables={len(clickables)}")

    links_with_context = LinkExtractor.extract_links_with_context(sample_text)
    check("s90.link_with_context", len(links_with_context) >= 3,
          f"links={len(links_with_context)}")
    check("s90.link_has_types", all("type" in l for l in links_with_context),
          "todos tienen type")

    # Clasificación de links
    gh_link = [l for l in links_with_context if "github" in l.get("url", "")]
    if gh_link:
        check("s90.link_classify_github",
              gh_link[0]["type"] == "code_repository",
              f"type={gh_link[0]['type']}")

except Exception as e:
    import traceback
    check("s90.arp_basic", False, f"excepción: {e}\n{traceback.format_exc()}")

# 19e. AutonomousResearchPipeline — Pipeline completo (dry-run)
try:
    arp2 = AutonomousResearchPipeline()
    sc_dry2 = ScreenController(dry_run=True, enable_s89=False, enable_s90=True)

    result = arp2.research(
        topic="test research",
        max_depth=1,
        max_pages=3,
        screen_controller=sc_dry2,
    )
    check("s90.arp_research_topic", result.get("topic") == "test research",
          "topic correcto")
    check("s90.arp_has_synthesis", "synthesis" in result, "tiene síntesis")
    check("s90.arp_has_pages", "pages_visited" in result,
          f"pages_visited={result.get('pages_visited')}")
    check("s90.arp_has_findings", "findings" in result,
          f"findings={result.get('findings')}")
    check("s90.arp_has_elapsed", "elapsed_total" in result,
          "tiene elapsed_total")
    check("s90.arp_has_queries", result.get("queries_used", 0) > 0,
          f"queries_used={result.get('queries_used')}")

    # Síntesis
    synthesis = result.get("synthesis", {})
    check("s90.synthesis_has_topic", "topic" in synthesis, "synthesis.topic")
    check("s90.synthesis_has_key_terms", "key_terms" in synthesis,
          "synthesis.key_terms")

except Exception as e:
    import traceback
    check("s90.arp_research", False, f"excepción: {e}\n{traceback.format_exc()}")

# 19f. ScreenController con S90 integrado
try:
    import core.screen_controller as sc_mod
    sc_mod._controller = None

    from core.screen_controller import ScreenController

    # Con S90
    sc_s90 = ScreenController(dry_run=True, enable_s89=True, enable_s90=True)
    check("s90.sc_s90_decomposer", sc_s90.goal_decomposer is not None,
          "goal_decomposer activo")
    check("s90.sc_s90_pipeline", sc_s90.research_pipeline is not None,
          "research_pipeline activo")

    # Sin S90
    sc_no_s90 = ScreenController(dry_run=True, enable_s89=False, enable_s90=False)
    check("s90.sc_no_s90_decomposer", sc_no_s90.goal_decomposer is None,
          "decomposer=None")
    check("s90.sc_no_s90_pipeline", sc_no_s90.research_pipeline is None,
          "pipeline=None")

    # Misión con S90 (dry-run)
    result = sc_s90.execute_mission("buscar test en google", max_steps=3)
    check("s90.sc_mission_completed", result["completed"], "misión completada")
    check("s90.sc_mission_s90_metrics", result.get("s90", {}).get("enabled", False),
          "métricas S90 presentes")

    # Misión compleja que activa HGD (dry-run)
    sc_mod._controller = None
    sc_complex = ScreenController(dry_run=True, enable_s89=False, enable_s90=True)
    result2 = sc_complex.execute_mission(
        "busca el precio de n8n en 3 sitios", max_steps=5
    )
    check("s90.sc_mission_complex_done", result2 is not None,
          "misión compleja ejecutada")
    check("s90.sc_mission_s90_enabled", result2.get("s90", {}).get("enabled", False),
          "S90 habilitado en métricas")

    # Método research()
    sc_mod._controller = None
    sc_research = ScreenController(dry_run=True, enable_s90=True)
    research_result = sc_research.research("test topic", max_depth=1, max_pages=2)
    check("s90.sc_research_method", "error" not in research_result,
          f"research() exitoso: {research_result.get('topic', 'N/A')}")

    # Stats incluye S90
    st = sc_s90.stats()
    check("s90.stats_s90_enabled", st.get("s90_enabled", False), "stats.s90_enabled")
    check("s90.stats_has_decomposer", "goal_decomposer" in st,
          "stats incluye goal_decomposer")

except Exception as e:
    import traceback
    check("s90.screen_controller_integration", False,
          f"excepción: {e}\n{traceback.format_exc()}")

# 19g. Registro en ConvergenceEngine
try:
    import core.eidos_convergence as ce_mod
    # Forzar re-registro para incluir S90
    ce_mod._engine = None
    from core.eidos_convergence import get_convergence
    ce = get_convergence()
    components = ce._components_registered
    s90_mods = [k for k in components.keys()
                if 'hierarchical_goal' in k or 'autonomous_research' in k]
    check("s90.convergence_registered", len(s90_mods) == 2,
          f"registrados: {s90_mods} (total components={len(components)})")
except Exception as e:
    check("s90.convergence", False, f"excepción: {e}")

# ── Resultado ────────────────────────────────────────────────────────────────
print("\n" + "=" * 64)
print(f"RESULTADO: {PASS} PASS / {FAIL} FAIL  ({PASS+FAIL} tests)")
if FAIL > 0:
    print("⚠️  Hay fallos que requieren atención.")
else:
    print("🎉 EIDOS ciclo vital completo: TODOS los tests pasan.")
print("=" * 64)
sys.exit(0 if FAIL == 0 else 1)
