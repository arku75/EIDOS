"""
core/eidos_kali_tools.py — Catálogo de herramientas Kali Linux (S80 "Sentidos")

180+ herramientas Kali pre-categorizadas, inyectadas como skills en el grafo
neuronal. Basado en kali_skills_auto.py de SER (NO TOCAR).

API:
    kt = get_kali_tools()
    kt.inject_all()          # Inyectar todas las tools como nodos skill
    kt.search("nmap")        # Buscar tool por nombre
    kt.by_category("recon")  # Listar tools de una categoría
    kt.stats()               # Estadísticas del catálogo
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from core.db import get_conn

log = logging.getLogger("eidos.kali_tools")

BRAIN_DB = Path.home() / ".eidos" / "evolution_brain.db"
STATE_FILE = Path.home() / ".eidos" / "kali_tools_state.json"

# ── Catálogo completo (de NO TOCAR kali_skills_auto.py) ──────────────────────

KALI_CATALOG: Dict[str, tuple] = {
    # RECONOCIMIENTO
    "nmap":         ("recon", "Port scanner y detección de servicios/OS"),
    "masscan":      ("recon", "Scanner de puertos ultra-rápido (1M pkg/s)"),
    "netdiscover":  ("recon", "Escáner ARP activo/pasivo para descubrir hosts"),
    "arp-scan":     ("recon", "Escáner de red a nivel ARP"),
    "nikto":        ("recon", "Escáner de vulnerabilidades web"),
    "whatweb":      ("recon", "Reconocimiento de tecnologías web"),
    "wafw00f":      ("recon", "Detección de Web Application Firewalls"),
    "subfinder":    ("recon", "Descubrimiento de subdominios"),
    "amass":        ("recon", "Attack surface mapping y OSINT"),
    "theHarvester": ("recon", "Recolección de emails, subdominios, hosts, IPs"),
    "recon-ng":     ("recon", "Framework de reconocimiento web modular"),
    "maltego":      ("recon", "Plataforma OSINT con visualización gráfica"),
    "shodan":       ("recon", "CLI de Shodan (IoT/internet scanner)"),
    "dnsrecon":     ("recon", "Script de reconocimiento DNS"),
    "fierce":       ("recon", "Scanner DNS para mapeo de redes corporativas"),
    "whois":        ("recon", "Consultas WHOIS a registros de dominio"),
    "dig":          ("recon", "DNS lookup avanzado"),
    "fping":        ("recon", "Ping a múltiples hosts en paralelo"),
    "hping3":       ("recon", "TCP/IP packet assembler & analyzer"),
    "traceroute":   ("recon", "Trazado de rutas de red"),
    "p0f":          ("recon", "Fingerprinting pasivo de OS en la red"),
    "wireshark":    ("recon", "Analizador de protocolos de red (GUI)"),
    "tshark":       ("recon", "Wireshark en línea de comandos"),
    "tcpdump":      ("recon", "Captura de paquetes de red"),
    "ettercap":     ("recon", "Suite para MITM attacks en LAN"),
    "netcat":       ("recon", "TCP/UDP swiss army knife"),

    # WEB
    "sqlmap":       ("web", "Detección y explotación automática de SQLi"),
    "gobuster":     ("web", "Brute force de directorios/subdominios/DNS"),
    "dirb":         ("web", "Escáner de contenido web por diccionario"),
    "dirsearch":    ("web", "Brute force de rutas web en Python"),
    "wfuzz":        ("web", "Fuzzer web flexible (parámetros, headers, etc)"),
    "ffuf":         ("web", "Fuzzer HTTP ultrarrápido en Go"),
    "hydra":        ("web", "Brute force de logins (HTTP, SSH, FTP, etc)"),
    "medusa":       ("web", "Brute force de servicios de red en paralelo"),
    "nuclei":       ("web", "Escáner de vulnerabilidades basado en templates"),
    "wpscan":       ("web", "Escáner de vulnerabilidades WordPress"),
    "joomscan":     ("web", "Escáner de vulnerabilidades Joomla"),
    "xsstrike":     ("web", "Escáner avanzado de XSS"),
    "commix":       ("web", "Explotación automática de Command Injection"),
    "dalfox":       ("web", "Finder/Scanner de XSS"),
    "arjun":        ("web", "Descubridor de parámetros HTTP"),

    # EXPLOITATION
    "metasploit":   ("exploit", "Framework de explotación más usado"),
    "msfconsole":   ("exploit", "Consola interactiva de Metasploit"),
    "msfvenom":     ("exploit", "Generador de payloads Metasploit"),
    "searchsploit": ("exploit", "Búsqueda en Exploit-DB offline"),
    "beef-xss":     ("exploit", "Browser Exploitation Framework"),
    "routersploit": ("exploit", "Framework de exploits para routers/IoT"),

    # PASSWORDS
    "hashcat":      ("passwords", "GPU password cracking (más rápido)"),
    "john":         ("passwords", "John the Ripper: password cracker"),
    "aircrack-ng":  ("passwords", "Suite WiFi: captura + cracking WEP/WPA"),
    "crunch":       ("passwords", "Generador de wordlists personalizado"),
    "cewl":         ("passwords", "Generador de wordlists desde páginas web"),
    "cupp":         ("passwords", "Generador de wordlists personalizadas (OSINT)"),
    "hash-identifier": ("passwords", "Identificador de tipos de hash"),
    "fcrackzip":    ("passwords", "Cracker de ZIPs protegidos"),

    # WIRELESS
    "airmon-ng":    ("wireless", "Activa modo monitor en tarjeta WiFi"),
    "airodump-ng":  ("wireless", "Captura de paquetes WiFi"),
    "aireplay-ng":  ("wireless", "Inyección de paquetes WiFi"),
    "kismet":       ("wireless", "Detector de redes WiFi/Bluetooth pasivo"),
    "wifite":       ("wireless", "Auditoría WiFi automatizada"),
    "bettercap":    ("wireless", "Swiss army knife para MITM y WiFi"),
    "reaver":       ("wireless", "Ataque WPS Brute Force"),
    "pixiewps":     ("wireless", "Ataque Pixie Dust para WPS"),
    "bluetooth":    ("wireless", "Herramientas Bluetooth nativas"),

    # FORENSICS
    "autopsy":      ("forensics", "Suite forense digital (GUI)"),
    "sleuthkit":    ("forensics", "Librería de análisis forense"),
    "volatility3":  ("forensics", "Análisis de memoria RAM"),
    "foremost":     ("forensics", "Recuperación de archivos por firma"),
    "photorec":     ("forensics", "Recuperación de archivos de disco"),
    "testdisk":     ("forensics", "Recuperación de particiones y MBR"),
    "binwalk":      ("forensics", "Análisis y extracción de firmware"),
    "bulk-extractor": ("forensics", "Extracción de datos de imágenes de disco"),
    "exiftool":     ("forensics", "Lectura/escritura de metadatos EXIF"),
    "strings":      ("forensics", "Extrae strings de binarios"),
    "hexedit":      ("forensics", "Editor hexadecimal en consola"),
    "dd":           ("forensics", "Copia bit a bit de dispositivos"),

    # REVERSE ENGINEERING
    "ghidra":       ("reverse", "Suite de RE de la NSA (Java, GUI)"),
    "radare2":      ("reverse", "Framework de RE modular y scriptable"),
    "r2":           ("reverse", "Alias de radare2"),
    "gdb":          ("reverse", "GNU Debugger"),
    "pwndbg":       ("reverse", "Extensión GDB para PWN"),
    "ltrace":       ("reverse", "Traza llamadas a bibliotecas"),
    "strace":       ("reverse", "Traza syscalls de un proceso"),
    "objdump":      ("reverse", "Desensablador de binarios ELF"),
    "readelf":      ("reverse", "Analiza archivos ELF"),
    "checksec":     ("reverse", "Verifica protecciones de seguridad en binarios"),
    "apktool":      ("reverse", "RE de APKs Android"),
    "jadx":         ("reverse", "Descompilador de APKs a Java"),
    "dex2jar":      ("reverse", "Convierte APKs a JARs"),
    "frida":        ("reverse", "Dynamic instrumentation toolkit"),

    # SOCIAL ENGINEERING
    "set":          ("social", "Social-Engineer Toolkit"),
    "gophish":      ("social", "Framework de phishing"),
    "evilginx2":    ("social", "MITM phishing framework (bypass 2FA)"),
    "zphisher":     ("social", "Herramienta de phishing con 30+ templates"),

    # POST-EXPLOITATION
    "empire":       ("postexploit", "Framework C2 PowerShell/Python"),
    "sliver":       ("postexploit", "Framework C2 cross-platform en Go"),
    "pwncat":       ("postexploit", "Post-explotación de reverse shells"),
    "mimikatz":     ("postexploit", "Extracción de credenciales Windows"),
    "powershell-empire": ("postexploit", "C2 en PowerShell para Windows"),

    # STEGO
    "steghide":     ("stego", "Ocultación de datos en imágenes/audio"),
    "stegseek":     ("stego", "Cracker de stego rápido"),
    "stegsolve":    ("stego", "Analizador visual de imágenes estego"),
    "zsteg":        ("stego", "Detección de estego en PNG/BMP"),
    "outguess":     ("stego", "Estego en imágenes JPEG"),

    # CRYPTO
    "openssl":      ("crypto", "Toolkit SSL/TLS y criptografía general"),
    "gpg":          ("crypto", "GnuPG: cifrado asimétrico y firma"),
    "age":          ("crypto", "Cifrado de archivos moderno (simple)"),
    "sops":         ("crypto", "Gestión de secretos con cifrado"),
    "base64":       ("crypto", "Codec base64 nativo"),

    # SYSTEM
    "systemctl":    ("system", "Gestión de servicios systemd"),
    "journalctl":   ("system", "Visor de logs systemd"),
    "htop":         ("system", "Monitor de procesos interactivo"),
    "iotop":        ("system", "Monitor de I/O de procesos"),
    "iftop":        ("system", "Monitor de ancho de banda en tiempo real"),
    "nethogs":      ("system", "Monitor de uso de red por proceso"),
    "lsof":         ("system", "Lista de archivos abiertos y sockets"),
    "ss":           ("system", "Netstat moderno"),
    "iptables":     ("system", "Firewall del kernel Linux"),
    "ufw":          ("system", "Firewall simplificado (frontend iptables)"),
    "rsync":        ("system", "Sincronización de archivos remota/locales"),
    "tmux":         ("system", "Terminal multiplexer con sesiones persistentes"),
    "screen":       ("system", "Terminal multiplexer clásico"),
    "cron":         ("system", "Programador de tareas periódicas"),
    "at":           ("system", "Programador de tareas one-shot"),
    "mount":        ("system", "Montaje de sistemas de archivos"),
    "fdisk":        ("system", "Particionado de discos"),
    "lsblk":        ("system", "Lista de dispositivos de bloque"),
    "df":           ("system", "Uso de espacio en disco"),
    "du":           ("system", "Uso de espacio por directorio"),
    "free":         ("system", "Uso de memoria RAM/SWAP"),
    "uptime":       ("system", "Carga del sistema y uptime"),
    "uname":        ("system", "Información del kernel"),
    "lsmod":        ("system", "Módulos del kernel cargados"),
    "modprobe":     ("system", "Carga/descarga módulos del kernel"),
    "dmesg":        ("system", "Buffer de mensajes del kernel"),
    "sysctl":       ("system", "Parámetros del kernel en runtime"),

    # PROGRAMMING
    "python3":      ("programming", "Intérprete Python 3"),
    "pip3":         ("programming", "Gestor de paquetes Python"),
    "node":         ("programming", "Runtime JavaScript/Node.js"),
    "npm":          ("programming", "Gestor de paquetes Node.js"),
    "gcc":          ("programming", "Compilador C/C++ GNU"),
    "g++":          ("programming", "Compilador C++ GNU"),
    "make":         ("programming", "Sistema de build Make"),
    "cmake":        ("programming", "Sistema de build CMake"),
    "git":          ("programming", "Control de versiones Git"),
    "gh":           ("programming", "CLI de GitHub"),
    "docker":       ("programming", "Contenedores Docker"),
    "kubectl":      ("programming", "CLI de Kubernetes"),
    "ansible":      ("programming", "Automatización IT con playbooks"),
    "terraform":    ("programming", "Infraestructura como código"),
    "vagrant":      ("programming", "Gestión de VMs de desarrollo"),
}

CATEGORY_NAMES_ES = {
    "recon": "Reconocimiento",
    "web": "Aplicaciones Web",
    "exploit": "Explotación",
    "passwords": "Contraseñas y Hashes",
    "wireless": "Redes Inalámbricas",
    "forensics": "Forense Digital",
    "reverse": "Ingeniería Inversa",
    "social": "Ingeniería Social",
    "postexploit": "Post-Explotación",
    "stego": "Esteganografía",
    "crypto": "Criptografía",
    "system": "Sistema Operativo",
    "programming": "Programación y DevOps",
}


class KaliTools:
    """Catálogo de herramientas Kali Linux como skills de EIDOS."""

    def __init__(self):
        self._injected = set()
        self._available = set()
        self._load_state()
        self._scan_available()

    # ── Escaneo de disponibilidad ────────────────────────────────────────────

    def _scan_available(self):
        """Verifica qué herramientas están instaladas en el sistema.
        S82b B3 fix: paralelizado con ThreadPoolExecutor (30s → ~2s)."""
        import concurrent.futures as _cf

        def _check_one(tool_name: str) -> Optional[str]:
            try:
                result = subprocess.run(
                    ["which", tool_name],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    return tool_name
            except Exception:
                pass
            return None

        with _cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="kali_scan") as ex:
            futures = [ex.submit(_check_one, t) for t in KALI_CATALOG]
            for fut in _cf.as_completed(futures, timeout=30):
                result = fut.result()
                if result:
                    self._available.add(result)

        log.info("KaliTools: %d/%d herramientas disponibles en el sistema",
                 len(self._available), len(KALI_CATALOG))

    # ── Inyección al grafo ──────────────────────────────────────────────────

    def inject_all(self) -> Dict[str, Any]:
        """Inyecta todas las herramientas como nodos skill en el grafo neuronal."""
        import sqlite3
        added = 0
        skipped = 0
        errors = 0

        try:
            conn = get_conn(BRAIN_DB, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")

            for tool_name, (category, description) in KALI_CATALOG.items():
                if tool_name in self._injected:
                    skipped += 1
                    continue

                node_id = f"kali_skill:{tool_name}"
                concept = f"[Kali/{CATEGORY_NAMES_ES.get(category, category)}] {tool_name}"
                available = tool_name in self._available
                confidence = 0.95 if available else 0.6

                try:
                    before = conn.total_changes
                    conn.execute(
                        "INSERT OR IGNORE INTO knowledge_nodes (id, concept, definition, "
                        "category, source, confidence) VALUES (?,?,?,?,?,?)",
                        (node_id, concept, description,
                         f"kali_{category}", "kali_tools", confidence))
                    if conn.total_changes > before:
                        added += 1
                        self._injected.add(tool_name)
                except Exception as e:
                    log.debug("Inject %s: %s", tool_name, e)
                    errors += 1

            conn.commit()

        except Exception as e:
            log.error("KaliTools inject_all: %s", e)
            return {"added": added, "skipped": skipped, "errors": errors + 1}

        # Guardar estado
        self._save_state()

        log.info("KaliTools: %d inyectados, %d skipped, %d errors", added, skipped, errors)

        # Emitir evento
        try:
            from core.eidos_events import emit
            emit("knowledge_injected", {"source": "kali_tools", "nodes_added": added},
                 source="kali_tools")
        except Exception:
            pass

        return {"added": added, "skipped": skipped, "errors": errors,
                "available": len(self._available), "total": len(KALI_CATALOG)}

    # ── Búsqueda ─────────────────────────────────────────────────────────────

    def search(self, query: str) -> List[Dict[str, Any]]:
        """Busca herramientas por nombre, categoría o descripción."""
        query_lower = query.lower()
        results = []
        for tool_name, (category, description) in KALI_CATALOG.items():
            if (query_lower in tool_name.lower() or
                query_lower in category.lower() or
                query_lower in description.lower()):
                results.append({
                    "name": tool_name,
                    "category": category,
                    "category_es": CATEGORY_NAMES_ES.get(category, category),
                    "description": description,
                    "available": tool_name in self._available,
                    "node_id": f"kali_skill:{tool_name}",
                })
        return results[:20]

    def by_category(self, category: str) -> List[Dict[str, Any]]:
        """Lista herramientas de una categoría específica."""
        results = []
        for tool_name, (cat, description) in KALI_CATALOG.items():
            if cat == category:
                results.append({
                    "name": tool_name, "description": description,
                    "available": tool_name in self._available,
                })
        return results

    # ── Categorías ──────────────────────────────────────────────────────────

    def categories(self) -> Dict[str, Dict[str, Any]]:
        """Retorna todas las categorías con conteos."""
        cats = {}
        for tool_name, (cat, desc) in KALI_CATALOG.items():
            if cat not in cats:
                cats[cat] = {"name_es": CATEGORY_NAMES_ES.get(cat, cat),
                            "total": 0, "available": 0}
            cats[cat]["total"] += 1
            if tool_name in self._available:
                cats[cat]["available"] += 1
        return cats

    # ── Persistencia ────────────────────────────────────────────────────────

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text())
                self._injected = set(data.get("injected", []))
        except Exception:
            pass

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({
                "injected": list(self._injected),
                "available": list(self._available),
                "updated": time.time(),
            }, indent=2))
        except Exception:
            pass

    # ── Stats ───────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "total_tools": len(KALI_CATALOG),
            "available": len(self._available),
            "injected": len(self._injected),
            "categories": len(self.categories()),
            "by_category": self.categories(),
        }


_kali_tools: Optional[KaliTools] = None


def get_kali_tools() -> KaliTools:
    global _kali_tools
    if _kali_tools is None:
        _kali_tools = KaliTools()
    return _kali_tools


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="EIDOS Kali Tools Catalog")
    p.add_argument("--inject", action="store_true", help="Inyectar tools al grafo")
    p.add_argument("--search", type=str, help="Buscar tool")
    p.add_argument("--category", type=str, help="Listar por categoría")
    p.add_argument("--stats", action="store_true", help="Estadísticas")
    args = p.parse_args()

    kt = KaliTools()

    if args.inject:
        result = kt.inject_all()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.search:
        results = kt.search(args.search)
        for r in results:
            avail = "✅" if r["available"] else "❌"
            print(f"  {avail} {r['name']} [{r['category_es']}]: {r['description']}")
        print(f"\n{len(results)} resultados para '{args.search}'")
    elif args.category:
        results = kt.by_category(args.category)
        for r in results:
            avail = "✅" if r["available"] else "❌"
            print(f"  {avail} {r['name']}: {r['description']}")
    elif args.stats:
        print(json.dumps(kt.stats(), indent=2, ensure_ascii=False))
    else:
        p.print_help()
