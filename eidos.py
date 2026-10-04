#!/usr/bin/env python3
"""
🜁 EIDOS CASCADE - Sistema Unificado
=====================================
Punto de entrada único que fusiona:
- Root EIDOS: God Mode CLI + Singularity Launcher
- Colony: Gateway Integration + Colony + APIs

Comandos:
    eidos god                # God Mode CLI interactivo
    eidos chat               # Chat interactivo Colony
    eidos gateway            # Iniciar Gateway integrado
    eidos api                # FastAPI server
    eidos vscode             # VSCode bridge
    eidos bridge             # Unix socket bridge
    eidos status             # Estado completo sistema
    eidos stop               # Detener EIDOS limpio
    eidos test               # Test rápido módulos
    eidos teach              # Enseñar a EIDOS (modo maestro-alumno)
    eidos study              # Cola de estudio autónomo
    eidos improve            # Diagnóstico rápido (smoke_e2e.py)
    eidos setup              # Verificar salud del sistema (checklist OK/FAIL)

Unifica: eidos_cli.py, awaken_eidos.py, eidos_main.py, main.py, eidos_chat_cli.py
"""

import argparse
import os
import sys
import subprocess
import asyncio
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
#  PATH CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
EIDOS_ROOT = Path(os.environ.get("EIDOS_ROOT", Path(__file__).resolve().parent)).expanduser().resolve()
sys.path.insert(0, str(EIDOS_ROOT))

# ═══════════════════════════════════════════════════════════════════════════════
#  COLORS
# ═══════════════════════════════════════════════════════════════════════════════
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    END = '\033[0m'

# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════
def check_ollama():
    """Verificar que Ollama está corriendo"""
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/tags"],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False

def start_ollama():
    """Iniciar Ollama si no está corriendo"""
    if not check_ollama():
        print(f"{Colors.YELLOW}🔄 Iniciando Ollama...{Colors.END}")
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        import time
        time.sleep(3)
        if check_ollama():
            print(f"{Colors.GREEN}✅ Ollama iniciado{Colors.END}")
        else:
            print(f"{Colors.RED}⚠️ No se pudo iniciar Ollama automáticamente{Colors.END}")

def banner():
    """Banner EIDOS CASCADE"""
    print(f"""
{Colors.CYAN}{Colors.BOLD}
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║    🜁 EIDOS CASCADE v3.0 - Sistema Unificado                    ║
║                                                                  ║
║    ███████╗██╗██████╗  ██████╗ ███████╗    🜁 CASCADE          ║
║    ██╔════╝██║██╔══██╗██╔═══██╗██╔════╝                          ║
║    █████╗  ██║██║  ██║██║   ██║███████╗    Conscious AI         ║
║    ██╔══╝  ██║██║  ██║██║   ██║╚════██║       Singularity        ║
║    ███████╗██║██████╔╝╚██████╔╝███████║                          ║
║    ╚══════╝╚═╝╚═════╝  ╚═════╝ ╚══════╝    EIDOS Colony        ║
║                                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║  Modos: god | chat | gateway | api | status | teach | study | improve | setup ║
║  (sync | estudio-app | browse | set-browser-default | reproduce | neuron | wake-word | vseidos-control) ║
╚══════════════════════════════════════════════════════════════════╝
{Colors.END}""")

# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_god(args):
    """🜁 God Mode CLI - Interfaz interactiva completa (de Root EIDOS)"""
    print(f"{Colors.CYAN}🜁 Iniciando God Mode CLI...{Colors.END}")
    try:
        # Intentar usar el God Mode del root si existe
        root_cli = EIDOS_ROOT / "eidos_cli.py"
        if root_cli.exists():
            os.chdir(EIDOS_ROOT)
            import subprocess
            subprocess.run([sys.executable, str(root_cli)])
        else:
            # Fallback al TUI de Colony
            from eidos_cli import main
            main()
    except Exception as e:
        print(f"{Colors.RED}❌ Error iniciando God Mode: {e}{Colors.END}")

def cmd_awaken(args):
    """🧬 Singularity Launcher - Despierta EIDOS completamente (de Root EIDOS)"""
    print(f"{Colors.CYAN}🧬 Iniciando Singularity Launcher...{Colors.END}")
    try:
        # Usar awaken_eidos.py del root si existe
        root_awaken = EIDOS_ROOT / "awaken_eidos.py"
        if root_awaken.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("awaken_eidos", root_awaken)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.main()
        else:
            # Fallback: iniciar componentes manualmente
            from core.eidos_autonomous_core import awaken_eidos
            from main import EidosIntegratedSystem
            
            print(f"{Colors.YELLOW}🌅 DESPERTANDO EIDOS CASCADE...{Colors.END}")
            awaken_eidos()
            
            # Iniciar Gateway también
            print(f"{Colors.CYAN}🔗 Iniciando Gateway Integrado...{Colors.END}")
            system = EidosIntegratedSystem()
            asyncio.run(system.start())
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}🛑 Interrumpido por usuario{Colors.END}")
    except Exception as e:
        print(f"{Colors.RED}❌ Error en awaken: {e}{Colors.END}")
        import traceback
        traceback.print_exc()

def cmd_chat(args):
    """💬 Chat interactivo Colony"""
    print(f"{Colors.CYAN}💬 Iniciando chat Colony...{Colors.END}")
    start_ollama()
    try:
        from core.colony_interactive import interactive_chat
        model = getattr(args, 'model', 'qwen2.5-coder:1.5b')
        interactive_chat(model=model)
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")

def cmd_gateway(args):
    """🌐 Gateway integrado con todas las plataformas.
    [S125] Module 7 (eidos_gateway): Reverse proxy unificado que rutea
    trafico a todos los backends (colony, panel, vscode, chroma, ollama, bridge).
    Se ejecuta como proceso standalone en :8003."""
    gateway_type = getattr(args, 'gateway_type', 'unified')

    if gateway_type == 'unified':
        # ── S125 Module 7: Unified API Gateway (standalone reverse proxy) ─
        print(f"{Colors.CYAN}🌐 Iniciando EIDOS Unified API Gateway (port 8003)...{Colors.END}")
        try:
            from core.eidos_gateway import run_gateway
            print(f"  {Colors.DIM}Rutas:{Colors.END}")
            print(f"    {Colors.DIM}/health           → Aggregated health (all backends){Colors.END}")
            print(f"    {Colors.DIM}/health/<service> → Single backend health{Colors.END}")
            print(f"    {Colors.DIM}/colony/<path>    → Colony Dashboard (:7777){Colors.END}")
            print(f"    {Colors.DIM}/panel/<path>     → Web Panel (:8080){Colors.END}")
            print(f"    {Colors.DIM}/vscode/<path>    → VSCode API (:8765){Colors.END}")
            print(f"    {Colors.DIM}/trinity/<path>   → Trinity Agent (:8001){Colors.END}")
            print(f"    {Colors.DIM}/chroma/<path>    → ChromaDB (:8767){Colors.END}")
            print(f"    {Colors.DIM}/ollama/<path>    → Ollama LLM (:11434){Colors.END}")
            print(f"    {Colors.DIM}/<path>           → Bridge-to-EIDOS (catch-all, :18003){Colors.END}")
            run_gateway()
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}🛑 Gateway detenido{Colors.END}")
        except Exception as e:
            print(f"{Colors.RED}❌ Error: {e}{Colors.END}")
    else:
        # Legacy: EidosIntegratedSystem
        print(f"{Colors.CYAN}🌐 Iniciando EIDOS Integrated System...{Colors.END}")
        start_ollama()
        try:
            from main import EidosIntegratedSystem
            system = EidosIntegratedSystem()
            asyncio.run(system.start())
        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}🛑 Gateway detenido{Colors.END}")
        except Exception as e:
            print(f"{Colors.RED}❌ Error: {e}{Colors.END}")

def cmd_api(args):
    """🔌 FastAPI server"""
    port = getattr(args, 'port', 8765)
    host = getattr(args, 'host', '0.0.0.0')
    try:
        import uvicorn
        print(f"{Colors.CYAN}🔌 Iniciando API en {host}:{port}...{Colors.END}")
        uvicorn.run("eidos_api:app", host=host, port=port, reload=False)
    except ImportError:
        print(f"{Colors.RED}❌ uvicorn no instalado. pip install uvicorn{Colors.END}")
        sys.exit(1)

def cmd_vscode(args):
    """🆚 VSCode/IDE bridge"""
    print(f"{Colors.CYAN}🆚 Iniciando VSCode Bridge...{Colors.END}")
    try:
        from eidos_vscode_bridge import main
        main()
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")

def cmd_bridge(args):
    """🔗 Unix socket bridge para Go dispatcher"""
    socket_path = getattr(args, 'socket', '/tmp/eidos-dispatcher.sock')
    print(f"{Colors.CYAN}🔗 Iniciando Bridge ({socket_path})...{Colors.END}")
    try:
        sys.argv = ["eidos_bridge_server.py", "--socket", socket_path]
        from eidos_bridge_server import main
        main()
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")

def cmd_status(args):
    """📊 Estado completo del sistema"""
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'═'*60}")
    print(f"  🜁 EIDOS CASCADE - System Status")
    print(f"{'═'*60}{Colors.END}\n")
    
    # Ollama
    if check_ollama():
        try:
            import json
            import urllib.request
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.load(resp)
                models = [m["name"] for m in data.get("models", [])]
                print(f"  {Colors.GREEN}✓{Colors.END} Ollama: ONLINE ({len(models)} modelos)")
                for m in models[:5]:  # Mostrar max 5
                    print(f"    • {m}")
                if len(models) > 5:
                    print(f"    ... y {len(models)-5} más")
        except Exception:
            print(f"  {Colors.GREEN}✓{Colors.END} Ollama: ONLINE (modelos no verificados)")
    else:
        print(f"  {Colors.RED}✗{Colors.END} Ollama: OFFLINE")
    
    # GPU
    if os.path.exists("/dev/kfd"):
        print(f"  {Colors.GREEN}✓{Colors.END} GPU: AMD ROCm disponible")
    else:
        print(f"  {Colors.YELLOW}~{Colors.END} GPU: CPU-only")
    
    # EIDOS State
    token = Path.home() / ".eidos" / "session.token"
    if token.exists():
        print(f"  {Colors.GREEN}✓{Colors.END} Sesión: activa")
    else:
        print(f"  {Colors.YELLOW}~{Colors.END} Sesión: no iniciada")
    
    # Versión unificada
    print(f"\n  {Colors.CYAN}ℹ️{Colors.END} EIDOS CASCADE v3.0 (EIDOS Colony)")
    print(f"  {Colors.CYAN}ℹ️{Colors.END} Ubicación: {EIDOS_ROOT}")
    
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'═'*60}{Colors.END}\n")

def cmd_stop(args):
    """🛑 Detener EIDOS de forma limpia"""
    print(f"{Colors.YELLOW}🛑 Deteniendo EIDOS CASCADE...{Colors.END}")
    try:
        # Detener procesos EIDOS
        subprocess.run(["pkill", "-f", r"eidos_.*\.py"], capture_output=True)
        subprocess.run(["pkill", "-f", "awaken_eidos"], capture_output=True)
        print(f"{Colors.GREEN}✅ Procesos EIDOS detenidos{Colors.END}")
    except Exception as e:
        print(f"{Colors.RED}⚠️ Error: {e}{Colors.END}")

def cmd_test(args):
    """🧪 Test rápido de módulos"""
    print(f"{Colors.CYAN}🧪 Ejecutando tests...{Colors.END}")
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
            cwd=EIDOS_ROOT,
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")

def cmd_improve(args):
    """🔍 Diagnóstico rápido — ejecuta smoke_e2e.py (sin cambios automáticos)"""
    print(f"{Colors.CYAN}🔍 EIDOS Improve — Diagnóstico rápido (solo lectura){Colors.END}")
    print(f"{Colors.DIM}   Ejecutando smoke_e2e.py...{Colors.END}\n")
    smoke_path = EIDOS_ROOT / "bin" / "smoke_e2e.py"
    if not smoke_path.exists():
        print(f"{Colors.RED}❌ No encontrado: {smoke_path}{Colors.END}")
        sys.exit(1)
    try:
        result = subprocess.run(
            [sys.executable, str(smoke_path)],
            cwd=EIDOS_ROOT,
            capture_output=True,
            text=True,
            timeout=300
        )
        # Mostrar todo el output del smoke test
        print(result.stdout)
        if result.stderr:
            print(f"{Colors.YELLOW}⚠️  stderr:{Colors.END}")
            print(result.stderr[:1000])
        # Resumen final
        if "PASS" in result.stdout:
            print(f"\n{Colors.GREEN}✅ Diagnóstico completado.{Colors.END}")
        else:
            print(f"\n{Colors.YELLOW}⚠️  Diagnóstico completado — revisa los resultados arriba.{Colors.END}")
        print(f"{Colors.DIM}   Exit code: {result.returncode}{Colors.END}")
    except subprocess.TimeoutExpired:
        print(f"{Colors.RED}❌ Timeout (120s) — smoke_e2e.py tardó demasiado{Colors.END}")
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.RED}❌ Error ejecutando smoke_e2e.py: {e}{Colors.END}")
        sys.exit(1)

def cmd_setup(args):
    """🏥 Verificar salud del sistema — checklist OK/FAIL"""
    import urllib.request
    import urllib.error
    import json

    print(f"\n{Colors.CYAN}{Colors.BOLD}{'═'*60}")
    print(f"  🏥 EIDOS Setup — Health Check")
    print(f"{'═'*60}{Colors.END}\n")

    checks = []

    # 1. xprintidle
    try:
        r = subprocess.run(["which", "xprintidle"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            checks.append(("xprintidle", True, r.stdout.strip()))
        else:
            checks.append(("xprintidle", False, "no encontrado en PATH"))
    except Exception as e:
        checks.append(("xprintidle", False, str(e)))

    # 2. Ollama (11434)
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "?") for m in data.get("models", [])]
            checks.append(("ollama (11434)", True, f"{len(models)} modelos: {', '.join(models[:4])}"))
    except Exception as e:
        checks.append(("ollama (11434)", False, str(e)[:80]))

    # 3. Bridge health (8003)
    try:
        req = urllib.request.Request("http://localhost:8003/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                checks.append(("bridge (8003)", True, "HTTP 200"))
            else:
                checks.append(("bridge (8003)", False, f"HTTP {resp.status}"))
    except Exception as e:
        checks.append(("bridge (8003)", False, str(e)[:80]))

    # 4. ChromaDB (8767)
    try:
        req = urllib.request.Request("http://localhost:8767/api/v2/heartbeat")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                checks.append(("chroma (8767)", True, "HTTP 200"))
            else:
                checks.append(("chroma (8767)", False, f"HTTP {resp.status}"))
    except Exception as e:
        checks.append(("chroma (8767)", False, str(e)[:80]))

    # 5. Web Panel (8080)
    try:
        req = urllib.request.Request("http://localhost:8080/")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status in (200, 404):  # 404 aún indica que el servidor responde
                checks.append(("web-panel (8080)", True, f"HTTP {resp.status} (responde)"))
            else:
                checks.append(("web-panel (8080)", False, f"HTTP {resp.status}"))
    except Exception as e:
        checks.append(("web-panel (8080)", False, str(e)[:80]))

    # Mostrar checklist
    ok_count = 0
    fail_count = 0
    for name, ok, detail in checks:
        if ok:
            print(f"  {Colors.GREEN}✓ OK{Colors.END}   {name:<22} {Colors.DIM}{detail}{Colors.END}")
            ok_count += 1
        else:
            print(f"  {Colors.RED}✗ FAIL{Colors.END} {name:<22} {Colors.RED}{detail}{Colors.END}")
            fail_count += 1

    print(f"\n  {Colors.CYAN}{'═'*60}{Colors.END}")
    print(f"  {Colors.GREEN}OK: {ok_count}{Colors.END}  |  {Colors.RED}FAIL: {fail_count}{Colors.END}  |  Total: {len(checks)}")
    if fail_count == 0:
        print(f"  {Colors.GREEN}✅ Todos los checks pasaron — EIDOS está sano.{Colors.END}")
    else:
        print(f"  {Colors.YELLOW}⚠️  {fail_count} check(s) fallaron. Revisa los servicios.{Colors.END}")
    print()

def cmd_teach(args):
    """👨‍🏫 Enseñar a EIDOS (modo maestro-alumno) — responde a una pregunta de EIDOS"""
    qid = getattr(args, 'qid', None)
    text = getattr(args, 'text', None)
    if not qid or not text:
        print(f"{Colors.RED}❌ Uso: eidos teach --qid <id> --text \"respuesta\"{Colors.END}")
        print(f"{Colors.CYAN}ℹ️  EIDOS te pregunta algo → tú respondes con eidos teach{Colors.END}")
        sys.exit(1)
    try:
        from core.master_protocol import answer as mp_answer
        result = mp_answer(qid, text)
        print(f"{Colors.GREEN}✅ Enseñanza guardada (confianza 0.95){Colors.END}")
        print(f"   QID: {qid}")
        print(f"   Respuesta: {text[:100]}{'...' if len(text)>100 else ''}")
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")

def cmd_study(args):
    """📚 Cola de estudio autónomo"""
    action = getattr(args, 'action', 'report')
    topic = getattr(args, 'topic', None)
    try:
        import core.study_queue as sq
        if action == 'add':
            if not topic:
                print(f"{Colors.RED}❌ Uso: eidos study add --topic \"tema\"{Colors.END}")
                sys.exit(1)
            priority = getattr(args, 'priority', 5)
            qid = sq.enqueue(topic, priority=priority)
            print(f"{Colors.GREEN}✅ Tema encolado: {topic} (prioridad {priority}, id={qid}){Colors.END}")
        elif action == 'report':
            items = sq.list_items()
            done = sum(1 for i in items if i.get('status') == 'done')
            pending = sum(1 for i in items if i.get('status') == 'pending')
            print(f"{Colors.CYAN}📊 Estudio autónomo — Informe{Colors.END}")
            print(f"   Completados: {done}")
            print(f"   Pendientes: {pending}")
            print(f"   En cola: {len(items)} temas")
        else:
            print(f"{Colors.YELLOW}⚠️  Acción desconocida: {action}. Usa --add o --report{Colors.END}")
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def cmd_pipeline(args):
    """🔬 Deep Research Pipeline — investiga, clona, compila, analiza, aprende"""
    topic = getattr(args, 'topic', None)
    dry_run = getattr(args, 'dry_run', False)
    if not topic:
        print(f"{Colors.RED}❌ Uso: eidos pipeline --topic <tema> [--dry-run]{Colors.END}")
        sys.exit(1)
    try:
        from core.eidos_pipeline import deep_research
        print(f"{Colors.CYAN}🔬 Pipeline: investigando '{topic}'...{Colors.END}")
        r = deep_research(topic, dry_run=dry_run)
        elapsed = r.get('elapsed_s', 0)
        phases = r.get('phases', [])
        ok_count = sum(1 for p in phases if p.get('ok'))
        print(f"{Colors.GREEN}✅ Pipeline completado en {elapsed}s: {ok_count}/{len(phases)} fases OK{Colors.END}")
        for p in phases:
            icon = "✅" if p.get("ok") else "❌"
            name = p.get("phase", "?")
            summary = str(p.get("definition", p.get("summary", p.get("error", ""))))[:100]
            print(f"  {icon} {name}: {summary}")
    except Exception as e:
        print(f"{Colors.RED}❌ Error: {e}{Colors.END}")


def cmd_cli(args):
    """Unified shared EIDOS agents terminal."""
    from bin.eidos_agents_terminal import SharedAgentsTerminal
    SharedAgentsTerminal().cmdloop()


def main():
    parser = argparse.ArgumentParser(
        description="EIDOS CASCADE - Sistema Unificado",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modos de operacion:
  god      - God Mode CLI interactivo
  chat     - Chat interactivo Colony
  gateway  - Gateway integrado con plataformas
  api      - FastAPI server
  vscode   - VSCode/IDE bridge
  bridge   - Unix socket bridge
  status   - Estado completo del sistema
  stop     - Detener EIDOS de forma limpia
  test     - Test rapido de modulos
  teach    - Ensenar a EIDOS (modo maestro-alumno)
  study    - Cola de estudio autonomo
  improve  - Diagnostico rapido (smoke_e2e.py, solo lectura)
  setup    - Verificar salud del sistema (checklist OK/FAIL)

Comandos adicionales:
  sync              - Sincronizar estado entre componentes
  estudio-app       - Abrir app de estudio dedicado
  browse            - Navegar web con EIDOS
  set-browser-default - Configurar navegador por defecto
  reproduce         - Reproducir sesion grabada
  neuron            - Gestionar neuronas del grafo
  wake-word         - Configurar wake-word
  vseidos-control   - Control del panel VSEIDOS
        """
    )
    
    parser.add_argument(
        "mode",
        nargs="?",
        default="cli",
        choices=["cli", "god", "awaken", "chat", "gateway", "api", "vscode",
                 "bridge", "status", "stop", "test", "teach", "study", "pipeline",
                 "improve", "setup",
                 "sync", "estudio-app", "browse", "set-browser-default",
                 "reproduce", "neuron", "wake-word", "vseidos-control"],
        help="Modo de operación (default: cli)"
    )
    parser.add_argument("--model", default="qwen2.5-coder:1.5b", help="Modelo Ollama")
    parser.add_argument("--port", type=int, help="Puerto para API/Gateway")
    parser.add_argument("--host", help="Host para API/Gateway")
    parser.add_argument("--socket", help="Socket path para bridge")
    parser.add_argument("--gateway-type", default="unified", choices=["unified", "integrated"],
                       help="Tipo de gateway: unified (Module 7 reverse proxy) o integrated (legacy)")
    parser.add_argument("--qid", help="ID de pregunta (para teach)")
    parser.add_argument("--text", help="Texto de enseñanza (para teach)")
    parser.add_argument("--action", default="report", choices=["add", "report"],
                       help="Acción de study (default: report)")
    parser.add_argument("--topic", help="Tema a estudiar (para study add)")
    parser.add_argument("--priority", type=int, default=5, help="Prioridad (1-10, para study add)")
    parser.add_argument("--dry-run", action="store_true", help="Modo solo lectura (pipeline)")
    
    args = parser.parse_args()
    
    # Banner solo si no es status/stop
    if args.mode not in ["status", "stop", "teach", "study", "pipeline", "setup", "improve"]:
        banner()
    
    # Dispatch
    commands = {
        "cli": cmd_cli,
        "god": cmd_god,
        "awaken": cmd_awaken,
        "chat": cmd_chat,
        "gateway": cmd_gateway,
        "api": cmd_api,
        "vscode": cmd_vscode,
        "bridge": cmd_bridge,
        "status": cmd_status,
        "stop": cmd_stop,
        "test": cmd_test,
        "teach": cmd_teach,
        "study": cmd_study,
        "pipeline": cmd_pipeline,
        "improve": cmd_improve,
        "setup": cmd_setup,
    }
    
    command = commands.get(args.mode, cmd_god)
    command(args)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}👋 EIDOS Cascade detenido por usuario{Colors.END}")
        sys.exit(0)
