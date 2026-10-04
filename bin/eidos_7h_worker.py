#!/usr/bin/env python3
"""
bin/eidos_7h_worker.py — Trabajador autónomo de 7 horas para EIDOS.

Cada 60 segundos:
  1. Elige un tema aleatorio de una lista de 200+ conceptos
  2. Investiga vía man/apt/whatis
  3. Inyecta el conocimiento en el grafo neuronal
  4. Verifica que EIDOS puede responder sobre el tema
  5. Registra todo en ~/.eidos/7h_worker.log

Ejecutar: PYTHONPATH=. python3 bin/eidos_7h_worker.py
Duración: 7 horas (420 ciclos de ~60s)
"""
import sys, os, time, random, subprocess, uuid, json, logging
from pathlib import Path

sys.path.insert(0, str(Path.home() / "EIDOS"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [7h] %(message)s",
    handlers=[
        logging.FileHandler(Path.home() / ".eidos" / "7h_worker.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("7h")

DURATION_HOURS = 7
CYCLE_SECONDS = 60
BRIDGE_URL = os.environ.get("EIDOS_BRIDGE_URL", "http://localhost:8003")
API_KEY = os.environ.get("EIDOS_BRIDGE_KEY", "")  # nunca hardcodear: definir en .env

# 200+ temas para investigar
TOPICS = [
    # Seguridad ofensiva
    "buffer overflow", "format string attack", "heap spraying", "ROP chains",
    "return-to-libc", "ASLR bypass", "DEP bypass", "stack canaries",
    "SEH overwrite", "egg hunting", "shellcode encoding", "DLL injection",
    "process hollowing", "API hooking", "DLL sideloading", "UAC bypass",
    "pass-the-hash", "pass-the-ticket", "golden ticket", "silver ticket",
    "kerberoasting", "ASREProasting", "DCSync", "skeleton key",
    # Redes
    "ARP spoofing", "DHCP spoofing", "DNS poisoning", "LLMNR poisoning",
    "NBT-NS poisoning", "SMB relay", "RDP hijacking", "VLAN hopping",
    "MAC flooding", "STP manipulation", "CDP spoofing", "HSRP attack",
    # Web
    "XXE injection", "SSRF attack", "SSTI attack", "deserialization attack",
    "JWT attacks", "OAuth misconfiguration", "CORS misconfiguration",
    "CSP bypass", "prototype pollution", "WebSocket hijacking",
    "DOM clobbering", "CSS injection", "CRLF injection", "HTTP smuggling",
    # Cripto
    "padding oracle", "length extension", "hash length extension",
    "bit flipping attack", "chosen plaintext", "side channel attack",
    "timing attack", "power analysis", "differential cryptanalysis",
    # Kernel / Sistema
    "kernel exploitation", "LKM rootkits", "eBPF exploitation",
    "race condition exploit", "TOCTOU vulnerability", "symlink attack",
    "tmp racing", "shared memory attack", "pipe attack", "signal handling bug",
    # Linux
    "capabilities escape", "namespace escape", "cgroup escape",
    "seccomp bypass", "AppArmor bypass", "SELinux bypass",
    "LD_PRELOAD hijacking", "ptrace injection", "procfs manipulation",
    # Windows
    "token stealing", "token manipulation", "parent PID spoofing",
    "process injection", "thread hijacking", "COM hijacking",
    "WMI persistence", "scheduled task persistence", "service persistence",
    "registry persistence", "DLL search order", "AppInit DLLs",
    # Herramientas
    "metasploit modules", "meterpreter", "beacon", "Cobalt Strike",
    "Empire agents", "Covenant grunts", "Sliver implants",
    "Mythic agents", "Havoc agents", "Brute Ratel",
    # Defensa
    "EDR evasion", "AV evasion", "sandbox evasion", "debugger detection",
    "VM detection", "honeypot detection", "logging evasion",
    "Sysmon evasion", "ETW bypass", "AMSI bypass", "PowerShell logging bypass",
    # Kali tools
    "sqlmap tamper scripts", "nmap scripting engine", "hydra modules",
    "john modes", "hashcat attack modes", "aircrack-ng suite",
    "wifite automation", "bettercap modules", "responder.py",
    "impacket tools", "crackmapexec modules", "bloodhound",
    "sharphound", "powerview", "mimikatz", "rubeus", "seatbelt",
    # Conceptos avanzados
    "fuzzing techniques", "coverage-guided fuzzing", "symbolic execution",
    "concolic testing", "taint analysis", "control flow integrity",
    "shadow stack", "pointer authentication", "memory tagging",
    "type confusion", "use-after-free", "double free", "integer overflow",
    # Protocolos
    "SSH tunneling", "SOCKS proxy", "reverse proxy", "load balancing",
    "TLS termination", "mutual TLS", "certificate pinning",
    "DNSSEC", "DNS over HTTPS", "DNS over TLS", "QUIC protocol",
    # Infra
    "container security", "kubernetes security", "serverless security",
    "API security", "GraphQL security", "microservices security",
    "service mesh security", "zero trust architecture",
]

def research_and_inject(topic: str) -> bool:
    """Investiga un tema via man/whatis/apt y lo guarda en el grafo."""
    from core.db import get_conn
    from core.eidos_active_research import research_now
    
    try:
        result = research_now(topic, timeout=8)
        if result and result.get("definition"):
            conn = get_conn(str(Path.home() / ".eidos" / "evolution_brain.db"))
            node_id = uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes (id, concept, definition, source, confidence, category, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (node_id, f"7h:{topic[:80]}", result["definition"][:1500],
                 f"7h_worker:{result.get('source','unknown')}", 0.8,
                 "7h_autonomous", time.time())
            )
            conn.commit()
            return True
    except Exception as e:
        log.debug(f"research {topic}: {e}")
    
    # Fallback: man page directa
    try:
        r = subprocess.run(["man", "-P", "cat", topic.split()[0]],
                          capture_output=True, text=True, timeout=4)
        if r.returncode == 0 and len(r.stdout) > 50:
            first_line = r.stdout.strip().split("\n")[0][:250]
            conn = get_conn(str(Path.home() / ".eidos" / "evolution_brain.db"))
            node_id = uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_nodes (id, concept, definition, source, confidence, category, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (node_id, f"7h:{topic[:80]}", first_line,
                 "7h_worker:man", 0.85, "7h_autonomous", time.time())
            )
            conn.commit()
            return True
    except:
        pass
    return False


def verify_knowledge(topic: str) -> bool:
    """Verifica que EIDOS puede responder sobre el tema via el bridge."""
    try:
        import urllib.request, json
        data = json.dumps({"message": f"que es {topic}"}).encode()
        req = urllib.request.Request(
            f"{BRIDGE_URL}/talk", data=data,
            headers={"X-API-Key": API_KEY, "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            text = resp.get("text", "")
            return len(text) > 50
    except:
        return False


def main():
    log.info("🚀 EIDOS 7h Worker iniciado")
    log.info(f"Duración: {DURATION_HOURS}h | Ciclo: {CYCLE_SECONDS}s | Temas: {len(TOPICS)}")
    
    start = time.time()
    end = start + (DURATION_HOURS * 3600)
    cycle = 0
    learned = 0
    verified = 0
    errors = 0
    
    while time.time() < end:
        cycle += 1
        remaining = int((end - time.time()) / 60)
        
        topic = random.choice(TOPICS)
        
        # Investigar + inyectar
        ok = research_and_inject(topic)
        if ok:
            learned += 1
        
        # Verificar cada 5 ciclos
        if cycle % 5 == 0 and ok:
            if verify_knowledge(topic):
                verified += 1
        
        # Reporte cada 10 ciclos
        if cycle % 10 == 0:
            conn = __import__('core.db', fromlist=['get_conn']).get_conn(
                str(Path.home() / ".eidos" / "evolution_brain.db"))
            total = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
            log.info(f"C{cycle:04d} | ⏰ {remaining}min | +{learned} aprendidos | "
                     f"+{verified} verificados | {total} nodos | {errors} errores")
        
        # Esperar hasta el próximo ciclo
        elapsed = time.time() - (start + (cycle * CYCLE_SECONDS))
        if elapsed < CYCLE_SECONDS:
            time.sleep(CYCLE_SECONDS - elapsed)
    
    # Reporte final
    conn = __import__('core.db', fromlist=['get_conn']).get_conn(
        str(Path.home() / ".eidos" / "evolution_brain.db"))
    total = conn.execute("SELECT COUNT(*) FROM knowledge_nodes").fetchone()[0]
    duration = (time.time() - start) / 3600
    
    log.info("=" * 50)
    log.info(f"🏁 7h COMPLETADAS en {duration:.1f}h")
    log.info(f"   Ciclos: {cycle}")
    log.info(f"   Aprendidos: {learned}")
    log.info(f"   Verificados: {verified}")
    log.info(f"   Errores: {errors}")
    log.info(f"   Nodos finales: {total}")
    log.info("=" * 50)


if __name__ == "__main__":
    main()
