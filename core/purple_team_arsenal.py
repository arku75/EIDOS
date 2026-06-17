"""
EIDOS Purple Team Arsenal - Capacidades completas Red + Blue + Purple Team
Sin restricciones arbitrarias - Solo límites técnicos

IMPORTANTE: Este arsenal es para DEFENSIVE SECURITY y ETHICAL HACKING
- Bug bounties autorizados
- Pentesting con permiso
- Hardening de sistemas propios
- Investigación de seguridad

Purple Team = Red Team (ofensivo) + Blue Team (defensivo)
"""
import subprocess
import os
import json
from typing import Dict, List, Optional, Any
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ScanResult:
    """Resultado de un scan de seguridad"""
    tool: str
    target: str
    findings: List[Dict[str, Any]]
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    timestamp: str
    raw_output: str


class PurpleTeamArsenal:
    """
    Arsenal completo de herramientas Purple Team

    Red Team (Offensive):
    - Network scanning (nmap, masscan)
    - Web vulns (nikto, gobuster, sqlmap)
    - Exploitation (metasploit, pwntools)
    - Password cracking (john, hashcat)
    - Social engineering (setoolkit)

    Blue Team (Defensive):
    - IDS/IPS (suricata)
    - Hardening (lynis, oscap)
    - Log analysis (splunk, elk)
    - Malware detection (yara, clamav)
    - Forensics (volatility, autopsy)

    Purple Team (Hybrid):
    - ATT&CK techniques (atomic-red-team)
    - Adversary emulation (caldera)
    - AD security (bloodhound, pingcastle)
    """

    def __init__(self):
        self.results: List[ScanResult] = []

        # Verificar tools disponibles
        self.available_tools = self._check_available_tools()

        print(f"🛡️  [Purple Team] Arsenal inicializado")
        print(f"   Tools disponibles: {len(self.available_tools)}/{len(self.ALL_TOOLS)}")

    ALL_TOOLS = [
        # Red Team
        "nmap", "masscan", "nikto", "gobuster", "sqlmap", "metasploit",
        "john", "hashcat", "hydra", "medusa", "wpscan", "burpsuite",

        # Blue Team
        "suricata", "snort", "ossec", "fail2ban", "lynis", "rkhunter",
        "chkrootkit", "clamav", "yara", "volatility",

        # Purple Team
        "responder", "bloodhound", "crackmapexec", "enum4linux",

        # Network
        "wireshark", "tshark", "tcpdump", "ettercap",

        # Web
        "dirb", "dirbuster", "wfuzz", "ffuf",
    ]

    def _check_available_tools(self) -> List[str]:
        """Verifica qué tools están instaladas en el sistema"""
        available = []

        for tool in self.ALL_TOOLS:
            try:
                result = subprocess.run(
                    ["which", tool],
                    capture_output=True,
                    timeout=2
                )
                if result.returncode == 0:
                    available.append(tool)
            except Exception:
                pass  # error no crítico, continuar
        return available

    # ═══════════════════════════════════════════════════════════════════════
    # RED TEAM - OFFENSIVE SECURITY
    # ═══════════════════════════════════════════════════════════════════════

    def nmap_scan(
        self,
        target: str,
        scan_type: str = "quick",
        ports: str = "1-65535",
        aggressive: bool = False
    ) -> ScanResult:
        """
        Port scanning con nmap

        Args:
            target: IP o dominio
            scan_type: "quick" | "full" | "stealth" | "udp" | "service_version"
            ports: Puertos a escanear (ej: "80,443" o "1-65535")
            aggressive: OS detection + version detection + scripts

        Returns:
            ScanResult con puertos abiertos y servicios
        """
        print(f"🔍 [Red Team] nmap scan: {target}")

        # Configurar comando según tipo
        cmd = ["nmap"]

        if scan_type == "quick":
            cmd.extend(["-T4", "-F"])  # Fast scan, top 100 ports
        elif scan_type == "full":
            cmd.extend(["-T4", "-p", ports])
        elif scan_type == "stealth":
            cmd.extend(["-sS", "-T2", "-p", ports])  # SYN stealth, slower
        elif scan_type == "udp":
            cmd.extend(["-sU", "-T4"])
        elif scan_type == "service_version":
            cmd.extend(["-sV", "-T4", "-p", ports])

        if aggressive:
            cmd.append("-A")  # OS detection + version + scripts + traceroute

        # Output formats
        cmd.extend(["-oX", "-"])  # XML output to stdout

        cmd.append(target)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 min max
            )

            # Parse output (simplified - full XML parsing would be better)
            findings = self._parse_nmap_output(result.stdout)

            scan_result = ScanResult(
                tool="nmap",
                target=target,
                findings=findings,
                severity="info" if not findings else "medium",
                timestamp=subprocess.run(["date", "-Iseconds"], capture_output=True, text=True).stdout.strip(),
                raw_output=result.stdout
            )

            self.results.append(scan_result)
            print(f"  ✅ Scan completado: {len(findings)} hallazgos")

            return scan_result

        except subprocess.TimeoutExpired:
            print(f"  ⚠️  Timeout - scan incompleto")
            return ScanResult("nmap", target, [], "info", "", "Timeout")
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return ScanResult("nmap", target, [], "info", "", str(e))

    def _parse_nmap_output(self, xml_output: str) -> List[Dict[str, Any]]:
        """Parse básico de salida nmap"""
        # TODO: Implementar parser XML completo
        # Por ahora, retorno lista vacía
        findings = []

        # Buscar líneas con puertos abiertos
        for line in xml_output.split("\n"):
            if "open" in line.lower():
                findings.append({"raw": line})

        return findings

    def gobuster_dir(
        self,
        url: str,
        wordlist: str = "/usr/share/wordlists/dirb/common.txt",
        extensions: str = "php,html,txt",
        threads: int = 10
    ) -> ScanResult:
        """
        Directory/file brute force con gobuster

        Args:
            url: URL base (ej: http://example.com)
            wordlist: Path a wordlist
            extensions: Extensiones a probar
            threads: Número de threads

        Returns:
            ScanResult con directorios/archivos encontrados
        """
        print(f"🔍 [Red Team] gobuster: {url}")

        if "gobuster" not in self.available_tools:
            print("  ⚠️  gobuster no instalado")
            return ScanResult("gobuster", url, [], "info", "", "Tool not available")

        cmd = [
            "gobuster", "dir",
            "-u", url,
            "-w", wordlist,
            "-x", extensions,
            "-t", str(threads),
            "-q",  # Quiet mode
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 min max
            )

            # Parse output
            findings = []
            for line in result.stdout.split("\n"):
                if line.strip() and not line.startswith("="):
                    findings.append({"path": line.strip()})

            scan_result = ScanResult(
                tool="gobuster",
                target=url,
                findings=findings,
                severity="info" if len(findings) < 10 else "medium",
                timestamp=subprocess.run(["date", "-Iseconds"], capture_output=True, text=True).stdout.strip(),
                raw_output=result.stdout
            )

            self.results.append(scan_result)
            print(f"  ✅ Encontrados: {len(findings)} paths")

            return scan_result

        except Exception as e:
            print(f"  ❌ Error: {e}")
            return ScanResult("gobuster", url, [], "info", "", str(e))

    def sqlmap_test(
        self,
        url: str,
        data: Optional[str] = None,
        cookie: Optional[str] = None,
        level: int = 1,
        risk: int = 1
    ) -> ScanResult:
        """
        SQL injection testing con sqlmap

        Args:
            url: URL a testear
            data: POST data (ej: "id=1&name=test")
            cookie: Cookies de sesión
            level: 1-5 (depth of tests)
            risk: 1-3 (risk of tests)

        Returns:
            ScanResult con vulnerabilidades SQL encontradas
        """
        print(f"🔍 [Red Team] sqlmap: {url}")

        if "sqlmap" not in self.available_tools:
            print("  ⚠️  sqlmap no instalado")
            return ScanResult("sqlmap", url, [], "info", "", "Tool not available")

        cmd = [
            "sqlmap",
            "-u", url,
            "--batch",  # Non-interactive
            "--level", str(level),
            "--risk", str(risk),
            "--answers=quit=N,follow=N",
        ]

        if data:
            cmd.extend(["--data", data])
        if cookie:
            cmd.extend(["--cookie", cookie])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )

            # Parse vulnerabilities
            findings = []
            if "vulnerable" in result.stdout.lower():
                findings.append({"type": "sql_injection", "severity": "critical"})

            severity = "critical" if findings else "info"

            scan_result = ScanResult(
                tool="sqlmap",
                target=url,
                findings=findings,
                severity=severity,
                timestamp=subprocess.run(["date", "-Iseconds"], capture_output=True, text=True).stdout.strip(),
                raw_output=result.stdout
            )

            self.results.append(scan_result)
            print(f"  ✅ Vulnerabilidades: {len(findings)}")

            return scan_result

        except Exception as e:
            print(f"  ❌ Error: {e}")
            return ScanResult("sqlmap", url, [], "info", "", str(e))

    # ═══════════════════════════════════════════════════════════════════════
    # BLUE TEAM - DEFENSIVE SECURITY
    # ═══════════════════════════════════════════════════════════════════════

    def lynis_audit(self, profile: str = "default") -> ScanResult:
        """
        System hardening audit con lynis

        Args:
            profile: Perfil de auditoría

        Returns:
            ScanResult con recomendaciones de hardening
        """
        print(f"🛡️  [Blue Team] lynis audit")

        if "lynis" not in self.available_tools:
            print("  ⚠️  lynis no instalado")
            return ScanResult("lynis", "localhost", [], "info", "", "Tool not available")

        cmd = ["lynis", "audit", "system", "--quick", "--quiet"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180
            )

            # Parse warnings/suggestions
            findings = []
            for line in result.stdout.split("\n"):
                if "warning" in line.lower() or "suggestion" in line.lower():
                    findings.append({"issue": line.strip()})

            scan_result = ScanResult(
                tool="lynis",
                target="localhost",
                findings=findings,
                severity="medium" if findings else "info",
                timestamp=subprocess.run(["date", "-Iseconds"], capture_output=True, text=True).stdout.strip(),
                raw_output=result.stdout
            )

            self.results.append(scan_result)
            print(f"  ✅ Recomendaciones: {len(findings)}")

            return scan_result

        except Exception as e:
            print(f"  ❌ Error: {e}")
            return ScanResult("lynis", "localhost", [], "info", "", str(e))

    def clamav_scan(self, path: str = "/home") -> ScanResult:
        """
        Antivirus scan con ClamAV

        Args:
            path: Path a escanear

        Returns:
            ScanResult con malware detectado
        """
        print(f"🛡️  [Blue Team] ClamAV scan: {path}")

        if "clamscan" not in self.available_tools:
            print("  ⚠️  ClamAV no instalado")
            return ScanResult("clamav", path, [], "info", "", "Tool not available")

        cmd = [
            "clamscan",
            "-r",  # Recursive
            "--infected",  # Only show infected files
            path
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600
            )

            # Parse infected files
            findings = []
            for line in result.stdout.split("\n"):
                if "FOUND" in line:
                    findings.append({"file": line.split(":")[0], "malware": "detected"})

            severity = "critical" if findings else "info"

            scan_result = ScanResult(
                tool="clamav",
                target=path,
                findings=findings,
                severity=severity,
                timestamp=subprocess.run(["date", "-Iseconds"], capture_output=True, text=True).stdout.strip(),
                raw_output=result.stdout
            )

            self.results.append(scan_result)
            print(f"  ✅ Malware encontrado: {len(findings)}")

            return scan_result

        except Exception as e:
            print(f"  ❌ Error: {e}")
            return ScanResult("clamav", path, [], "info", "", str(e))

    # ═══════════════════════════════════════════════════════════════════════
    # PURPLE TEAM - COMBINED OFFENSIVE + DEFENSIVE
    # ═══════════════════════════════════════════════════════════════════════

    def full_purple_team_assessment(
        self,
        target: str,
        include_offensive: bool = True,
        include_defensive: bool = True
    ) -> Dict[str, Any]:
        """
        Evaluación completa Purple Team

        Combina:
        - Red Team: nmap, gobuster, sqlmap
        - Blue Team: lynis, clamav
        - Análisis y correlación de resultados

        Args:
            target: Target a evaluar (IP, dominio, o localhost)
            include_offensive: Ejecutar tests ofensivos
            include_defensive: Ejecutar tests defensivos

        Returns:
            Dict con resultados de cada fase
        """
        print(f"\n🔮 [Purple Team] Full Assessment: {target}\n")

        results = {
            "red_team": [],
            "blue_team": [],
            "summary": {}
        }

        # RED TEAM
        if include_offensive and target not in ["localhost", "127.0.0.1"]:
            print("═══ RED TEAM (Offensive) ═══")

            # Network scan
            results["red_team"].append(self.nmap_scan(target, scan_type="quick"))

            # Si es web (http/https)
            if target.startswith("http"):
                results["red_team"].append(self.gobuster_dir(target))
                results["red_team"].append(self.sqlmap_test(target))

        # BLUE TEAM
        if include_defensive:
            print("\n═══ BLUE TEAM (Defensive) ═══")

            results["blue_team"].append(self.lynis_audit())
            results["blue_team"].append(self.clamav_scan("/tmp"))  # Quick scan

        # SUMMARY
        total_findings = sum(len(r.findings) for r in results["red_team"] + results["blue_team"])
        critical = sum(1 for r in results["red_team"] + results["blue_team"] if r.severity == "critical")

        results["summary"] = {
            "total_findings": total_findings,
            "critical_findings": critical,
            "tools_used": len(results["red_team"]) + len(results["blue_team"])
        }

        print(f"\n📊 SUMMARY:")
        print(f"   Total findings: {total_findings}")
        print(f"   Critical: {critical}")
        print(f"   Tools used: {results['summary']['tools_used']}")

        return results

    def get_results(self) -> List[ScanResult]:
        """Obtiene todos los resultados de scans"""
        return self.results

    def export_results(self, output_file: str = "purple_team_results.json"):
        """Exporta resultados a JSON"""
        output_path = Path.home() / ".eidos" / output_file

        data = {
            "results": [
                {
                    "tool": r.tool,
                    "target": r.target,
                    "findings": r.findings,
                    "severity": r.severity,
                    "timestamp": r.timestamp,
                }
                for r in self.results
            ]
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

        print(f"📄 [Purple Team] Resultados exportados: {output_path}")
        return str(output_path)


# Singleton
purple_team = PurpleTeamArsenal()


# ═══════════════════════════════════════════════════════════════════════════
# Funciones de conveniencia para tools.py
# ═══════════════════════════════════════════════════════════════════════════

def security_scan(target: str, scan_type: str = "quick") -> str:
    """
    Scan de seguridad rápido

    Args:
        target: IP, dominio, o "localhost"
        scan_type: "quick" | "full" | "purple"

    Returns:
        Resumen de resultados
    """
    if scan_type == "purple":
        results = purple_team.full_purple_team_assessment(target)
        return f"Purple Team Assessment: {results['summary']['total_findings']} findings"

    elif scan_type == "quick":
        result = purple_team.nmap_scan(target, scan_type="quick")
        return f"Nmap scan: {len(result.findings)} findings"

    elif scan_type == "full":
        result = purple_team.nmap_scan(target, scan_type="full", ports="1-65535")
        return f"Full scan: {len(result.findings)} findings"

    else:
        return f"Unknown scan type: {scan_type}"


# ═══════════════════════════════════════════════════════════════════════════
# Test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Test Purple Team Arsenal ===\n")

    # Test 1: Verificar tools disponibles
    print(f"Tools disponibles: {purple_team.available_tools}\n")

    # Test 2: Quick nmap scan (localhost es seguro)
    print("Test nmap scan (localhost):")
    result = purple_team.nmap_scan("127.0.0.1", scan_type="quick")
    print(f"  Resultados: {len(result.findings)} findings\n")

    # Test 3: Lynis audit
    print("Test lynis audit:")
    result = purple_team.lynis_audit()
    print(f"  Recomendaciones: {len(result.findings)}\n")

    print("✅ Purple Team Arsenal funcional")
