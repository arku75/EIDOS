"""
EIDOS core/network_discovery.py — Network Discovery & Asset Tracking
=====================================================================
Mapea la red local, detecta dispositivos, trackea cambios.
Alerta cuando aparecen nuevos hosts o cambian servicios.

Uso:
    from core.network_discovery import get_network_discovery
    nd = get_network_discovery()
    hosts = nd.scan_network()       # Quick ARP/ping scan
    nd.deep_scan("192.168.1.100")   # Port scan individual
    changes = nd.detect_changes()   # Compare with last scan
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from core.db import get_conn
from core.db import get_conn_ctx

log = logging.getLogger("eidos.netdiscovery")

DB_PATH = os.path.expanduser("~/.eidos/network.db")


@dataclass
class NetworkHost:
    """A discovered host on the network."""
    ip: str
    mac: str = ""
    hostname: str = ""
    vendor: str = ""
    open_ports: list[int] = field(default_factory=list)
    services: dict[int, str] = field(default_factory=dict)  # port -> service
    os_guess: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    is_new: bool = False


@dataclass
class NetworkChange:
    """A detected change in the network."""
    change_type: str  # new_host, host_gone, new_port, port_closed
    ip: str
    detail: str
    timestamp: float = field(default_factory=time.time)
    severity: str = "info"  # info, medium, high


class NetworkDiscovery:
    """Discovers and tracks network hosts and services."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._running = False
        self._thread: Optional[threading.Thread] = None
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with get_conn_ctx(self.db_path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS hosts (
                    ip TEXT PRIMARY KEY,
                    mac TEXT DEFAULT '',
                    hostname TEXT DEFAULT '',
                    vendor TEXT DEFAULT '',
                    open_ports TEXT DEFAULT '[]',
                    services TEXT DEFAULT '{}',
                    os_guess TEXT DEFAULT '',
                    first_seen REAL,
                    last_seen REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    change_type TEXT,
                    ip TEXT,
                    detail TEXT,
                    severity TEXT DEFAULT 'info',
                    timestamp REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_type TEXT,
                    target TEXT,
                    hosts_found INTEGER,
                    timestamp REAL,
                    duration_s REAL
                )
            """)

    # ── Network Detection ─────────────────────────────────────────────────

    def get_local_networks(self) -> list[str]:
        """Detect local network CIDRs from system interfaces."""
        networks = []
        try:
            result = subprocess.run(
                ["ip", "-4", "route", "show", "scope", "link"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                # "192.168.122.0/24 dev virbr0 ..."
                match = re.match(r'(\d+\.\d+\.\d+\.\d+/\d+)', line)
                if match:
                    cidr = match.group(1)
                    # Skip docker and loopback
                    if not cidr.startswith("172.17.") and not cidr.startswith("127."):
                        networks.append(cidr)
        except Exception as e:
            log.warning("Failed to detect networks: %s", e)
        return networks

    def get_gateway(self) -> str:
        """Get default gateway IP."""
        try:
            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5
            )
            match = re.search(r'default via (\S+)', result.stdout)
            if match:
                return match.group(1)
        except Exception:
            pass  # error no crítico, continuar
        return ""

    def get_local_ip(self) -> str:
        """Get the primary local IP."""
        try:
            result = subprocess.run(
                ["ip", "route", "get", "1.1.1.1"],
                capture_output=True, text=True, timeout=5
            )
            match = re.search(r'src (\S+)', result.stdout)
            if match:
                return match.group(1)
        except Exception:
            pass  # error no crítico, continuar
        return ""

    # ── Scanning ──────────────────────────────────────────────────────────

    def scan_network(self, target: str = "") -> list[NetworkHost]:
        """Quick network scan using nmap -sn (ping/ARP discovery)."""
        if not target:
            networks = self.get_local_networks()
            if not networks:
                return []
            target = networks[0]

        t0 = time.time()
        hosts = []

        try:
            result = subprocess.run(
                ["nmap", "-sn", "--open", "-oX", "-", target],
                capture_output=True, text=True, timeout=120
            )
            hosts = self._parse_nmap_xml(result.stdout)
        except subprocess.TimeoutExpired:
            log.warning("Network scan timed out for %s", target)
        except FileNotFoundError:
            log.warning("nmap not found, trying ARP fallback")
            hosts = self._arp_scan()
        except Exception as e:
            log.warning("Scan failed: %s", e)

        duration = time.time() - t0

        # Update database
        now = time.time()
        for h in hosts:
            h.last_seen = now
            existing = self._get_host(h.ip)
            if existing:
                h.first_seen = existing.first_seen
            else:
                h.first_seen = now
                h.is_new = True
            self._save_host(h)

        # Record scan
        self._record_scan("quick", target, len(hosts), duration)

        return hosts

    def deep_scan(self, target: str) -> Optional[NetworkHost]:
        """Deep scan a single host — ports, services, OS detection."""
        t0 = time.time()
        try:
            result = subprocess.run(
                ["nmap", "-sV", "-sC", "--top-ports", "100",
                 "-O", "--osscan-limit", "-oX", "-", target],
                capture_output=True, text=True, timeout=180
            )
            hosts = self._parse_nmap_xml(result.stdout)
            if hosts:
                host = hosts[0]
                host.last_seen = time.time()
                existing = self._get_host(host.ip)
                host.first_seen = existing.first_seen if existing else time.time()
                self._save_host(host)
                self._record_scan("deep", target, 1, time.time() - t0)
                return host
        except Exception as e:
            log.warning("Deep scan failed for %s: %s", target, e)
        return None

    def _arp_scan(self) -> list[NetworkHost]:
        """Fallback: use ARP table for host discovery."""
        hosts = []
        try:
            result = subprocess.run(
                ["ip", "neigh", "show"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "lladdr":
                    ip = parts[0]
                    mac = parts[4]
                    state = parts[-1] if parts else ""
                    if state not in ("FAILED", "INCOMPLETE"):
                        hosts.append(NetworkHost(ip=ip, mac=mac))
        except Exception:
            pass  # error no crítico, continuar
        return hosts

    def _parse_nmap_xml(self, xml_output: str) -> list[NetworkHost]:
        """Parse nmap XML output into NetworkHost objects."""
        import xml.etree.ElementTree as ET
        hosts = []
        try:
            root = ET.fromstring(xml_output)
            for host_elem in root.findall(".//host"):
                status = host_elem.find("status")
                if status is not None and status.get("state") != "up":
                    continue

                ip = ""
                mac = ""
                vendor = ""
                hostname = ""

                for addr in host_elem.findall("address"):
                    if addr.get("addrtype") == "ipv4":
                        ip = addr.get("addr", "")
                    elif addr.get("addrtype") == "mac":
                        mac = addr.get("addr", "")
                        vendor = addr.get("vendor", "")

                hostnames = host_elem.find("hostnames")
                if hostnames is not None:
                    hn = hostnames.find("hostname")
                    if hn is not None:
                        hostname = hn.get("name", "")

                # Ports
                open_ports = []
                services = {}
                ports_elem = host_elem.find("ports")
                if ports_elem is not None:
                    for port in ports_elem.findall("port"):
                        state = port.find("state")
                        if state is not None and state.get("state") == "open":
                            portid = int(port.get("portid", 0))
                            open_ports.append(portid)
                            svc = port.find("service")
                            if svc is not None:
                                svc_name = svc.get("name", "")
                                svc_ver = svc.get("version", "")
                                services[portid] = f"{svc_name} {svc_ver}".strip()

                # OS detection
                os_guess = ""
                osmatch = host_elem.find(".//osmatch")
                if osmatch is not None:
                    os_guess = osmatch.get("name", "")

                if ip:
                    hosts.append(NetworkHost(
                        ip=ip, mac=mac, hostname=hostname, vendor=vendor,
                        open_ports=open_ports, services=services, os_guess=os_guess,
                    ))
        except ET.ParseError:
            log.debug("Failed to parse nmap XML")
        return hosts

    # ── Change Detection ──────────────────────────────────────────────────

    def detect_changes(self) -> list[NetworkChange]:
        """Compare current scan with previous — detect new/gone hosts and port changes."""
        changes = []
        current = self.scan_network()
        current_ips = {h.ip for h in current}

        # Check for new hosts
        for h in current:
            if h.is_new:
                sev = "high" if h.open_ports else "medium"
                detail = f"MAC={h.mac}" if h.mac else ""
                if h.hostname:
                    detail += f" hostname={h.hostname}"
                if h.vendor:
                    detail += f" vendor={h.vendor}"
                if h.open_ports:
                    detail += f" ports={h.open_ports}"
                changes.append(NetworkChange(
                    change_type="new_host", ip=h.ip,
                    detail=detail.strip(), severity=sev,
                ))

        # Check for hosts that disappeared (not seen in 24h)
        cutoff = time.time() - 86400
        try:
            with get_conn_ctx(self.db_path) as c:
                rows = c.execute(
                    "SELECT ip, hostname FROM hosts WHERE last_seen < ? AND last_seen > ?",
                    (cutoff, cutoff - 86400 * 7)  # gone 1-7 days
                ).fetchall()
                for ip, hostname in rows:
                    if ip not in current_ips:
                        changes.append(NetworkChange(
                            change_type="host_gone", ip=ip,
                            detail=f"Not seen in 24h (was: {hostname})",
                            severity="info",
                        ))
        except Exception:
            pass  # error no crítico, continuar
        # Store changes
        for ch in changes:
            self._store_change(ch)

        return changes

    # ── Persistence ───────────────────────────────────────────────────────

    def _get_host(self, ip: str) -> Optional[NetworkHost]:
        try:
            with get_conn_ctx(self.db_path) as c:
                row = c.execute(
                    "SELECT ip, mac, hostname, vendor, open_ports, services, os_guess, first_seen, last_seen "
                    "FROM hosts WHERE ip = ?", (ip,)
                ).fetchone()
                if row:
                    return NetworkHost(
                        ip=row[0], mac=row[1], hostname=row[2], vendor=row[3],
                        open_ports=json.loads(row[4] or "[]"),
                        services=json.loads(row[5] or "{}"),
                        os_guess=row[6], first_seen=row[7], last_seen=row[8],
                    )
        except Exception:
            pass  # error no crítico, continuar
        return None

    def _save_host(self, h: NetworkHost) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute("""
                    INSERT OR REPLACE INTO hosts
                    (ip, mac, hostname, vendor, open_ports, services, os_guess, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    h.ip, h.mac, h.hostname, h.vendor,
                    json.dumps(h.open_ports), json.dumps(h.services),
                    h.os_guess, h.first_seen, h.last_seen,
                ))
        except Exception as e:
            log.warning("Failed to save host: %s", e)

    def _store_change(self, ch: NetworkChange) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute(
                    "INSERT INTO changes (change_type, ip, detail, severity, timestamp) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ch.change_type, ch.ip, ch.detail, ch.severity, ch.timestamp)
                )
        except Exception:
            pass  # error no crítico, continuar
    def _record_scan(self, scan_type: str, target: str, count: int, duration: float) -> None:
        try:
            with get_conn_ctx(self.db_path) as c:
                c.execute(
                    "INSERT INTO scans (scan_type, target, hosts_found, timestamp, duration_s) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (scan_type, target, count, time.time(), duration)
                )
        except Exception:
            pass  # error no crítico, continuar
    # ── Queries ───────────────────────────────────────────────────────────

    def get_all_hosts(self) -> list[NetworkHost]:
        """Get all known hosts from database."""
        try:
            with get_conn_ctx(self.db_path) as c:
                rows = c.execute(
                    "SELECT ip, mac, hostname, vendor, open_ports, services, os_guess, first_seen, last_seen "
                    "FROM hosts ORDER BY last_seen DESC"
                ).fetchall()
                return [
                    NetworkHost(
                        ip=r[0], mac=r[1], hostname=r[2], vendor=r[3],
                        open_ports=json.loads(r[4] or "[]"),
                        services=json.loads(r[5] or "{}"),
                        os_guess=r[6], first_seen=r[7], last_seen=r[8],
                    ) for r in rows
                ]
        except Exception:
            return []

    def get_recent_changes(self, n: int = 20) -> list[dict]:
        try:
            with get_conn_ctx(self.db_path) as c:
                rows = c.execute(
                    "SELECT change_type, ip, detail, severity, timestamp "
                    "FROM changes ORDER BY timestamp DESC LIMIT ?", (n,)
                ).fetchall()
                return [
                    {"type": r[0], "ip": r[1], "detail": r[2],
                     "severity": r[3], "ts": r[4]}
                    for r in rows
                ]
        except Exception:
            return []

    # ── Background Monitoring ─────────────────────────────────────────────

    def start(self, interval: int = 600) -> None:
        """Start background network monitoring (default: every 10 min)."""
        if self._running:
            return
        self._running = True

        def _loop():
            while self._running:
                try:
                    changes = self.detect_changes()
                    if changes:
                        high = [c for c in changes if c.severity in ("high", "medium")]
                        if high:
                            log.info("Network changes: %d (%d high/med)",
                                     len(changes), len(high))
                            # Route through AlertManager
                            try:
                                from core.alert_manager import get_alert_manager
                                am = get_alert_manager()
                                for c in high[:5]:
                                    am.alert(c.severity, c.change_type,
                                             f"{c.ip}: {c.detail[:200]}",
                                             source="network", ip=c.ip)
                            except Exception:
                                pass  # error no crítico, continuar
                            # Also notify AutonomousCore
                            try:
                                from core.autonomous import get_autonomous
                                auto = get_autonomous()
                                msgs = [f"{c.change_type}: {c.ip} {c.detail[:50]}"
                                        for c in high[:3]]
                                auto._record_thought(
                                    f"[netwatch] {'; '.join(msgs)}",
                                    led=True,
                                )
                            except Exception:
                                pass  # error no crítico, continuar
                except Exception as e:
                    log.debug("Network scan error: %s", e)
                time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="eidos-netwatch")
        self._thread.start()
        log.info("NetworkDiscovery started (interval=%ds)", interval)

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict:
        try:
            with get_conn_ctx(self.db_path) as c:
                total_hosts = c.execute("SELECT COUNT(*) FROM hosts").fetchone()[0]
                total_changes = c.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
                total_scans = c.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
                # Last scan info
                last = c.execute(
                    "SELECT scan_type, hosts_found, duration_s, timestamp "
                    "FROM scans ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
        except Exception:
            return {"total_hosts": 0, "total_changes": 0, "total_scans": 0, "running": self._running}

        result = {
            "total_hosts": total_hosts,
            "total_changes": total_changes,
            "total_scans": total_scans,
            "running": self._running,
            "networks": self.get_local_networks(),
            "gateway": self.get_gateway(),
            "local_ip": self.get_local_ip(),
        }
        if last:
            result["last_scan"] = {
                "type": last[0], "hosts": last[1],
                "duration": f"{last[2]:.1f}s",
                "when": time.strftime("%H:%M:%S", time.localtime(last[3])),
            }
        return result


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_discovery: Optional[NetworkDiscovery] = None


def get_network_discovery() -> NetworkDiscovery:
    global _discovery
    if _discovery is None:
        _discovery = NetworkDiscovery()
    return _discovery


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    nd = get_network_discovery()

    print(f"Network Discovery")
    print(f"  Local IP: {nd.get_local_ip()}")
    print(f"  Gateway:  {nd.get_gateway()}")
    print(f"  Networks: {nd.get_local_networks()}")

    print(f"\nScanning network...")
    hosts = nd.scan_network()
    print(f"  Found {len(hosts)} hosts:")
    for h in hosts:
        ports = f" ports={h.open_ports}" if h.open_ports else ""
        vendor = f" ({h.vendor})" if h.vendor else ""
        print(f"    {h.ip:18s} {h.mac:18s} {h.hostname:20s}{vendor}{ports}")

    print(f"\nStats: {nd.stats}")
