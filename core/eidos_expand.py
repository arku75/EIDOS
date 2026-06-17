"""
core/eidos_expand.py — Expansión sin límites (S76 Fase 5)

Capacidad de EIDOS de expandirse horizontalmente a otras máquinas y
entornos, instalando sus propias dependencias y conectándose vía VNC/SSH.

Módulos:
  1. VNCController — conecta y controla escritorios remotos vía VNC
  2. AutoInstaller — instala dependencias de EIDOS automáticamente
  3. ExpansionManager — coordina la expansión a nuevos nodos
  4. CapabilityDiscovery — descubre qué puede hacer en cada entorno

Principio: EIDOS no debe depender de intervención humana para crecer.
Si detecta un entorno nuevo, instala lo necesario y se expande.

API:
    exp = ExpansionManager()
    nodes = exp.discover_nodes()        # Encontrar máquinas en la red
    result = exp.expand_to("192.168.1.100")  # Expandirse a un nodo
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.expand")

# ── Dependencias requeridas por EIDOS ────────────────────────────────────────
EIDOS_DEPS = {
    "python": {
        "packages": [
            "numpy", "scipy", "pillow", "opencv-python-headless",
            "pytesseract", "requests", "flask", "psutil",
            "cryptography", "nltk"
        ],
        "min_version": "3.10"
    },
    "system": {
        "debian": [
            "tesseract-ocr", "tesseract-ocr-spa", "scrot",
            "xdotool", "ffmpeg", "sqlite3", "python3-pip",
            "python3-venv", "curl", "git"
        ],
        "arch": [
            "tesseract", "tesseract-data-spa", "scrot",
            "xdotool", "ffmpeg", "sqlite3", "python-pip",
            "curl", "git"
        ],
        "fedora": [
            "tesseract", "tesseract-langpack-spa", "scrot",
            "xdotool", "ffmpeg", "sqlite3", "python3-pip",
            "curl", "git"
        ]
    },
    "optional": {
        "debian": ["firefox-esr", "konsole", "dolphin"],
        "arch": ["firefox", "konsole", "dolphin"],
        "fedora": ["firefox", "konsole", "dolphin"]
    }
}


class NodeType(Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MAC = "mac"
    UNKNOWN = "unknown"


@dataclass
class RemoteNode:
    """Un nodo remoto donde EIDOS puede expandirse."""
    host: str
    port: int = 22
    node_type: NodeType = NodeType.UNKNOWN
    reachable: bool = False
    os_info: str = ""
    installed_deps: List[str] = field(default_factory=list)
    missing_deps: List[str] = field(default_factory=list)
    vnc_port: int = 5900
    vnc_available: bool = False

    @property
    def is_ready(self) -> bool:
        return self.reachable and len(self.missing_deps) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "type": self.node_type.value,
            "reachable": self.reachable,
            "os": self.os_info,
            "installed": self.installed_deps,
            "missing": self.missing_deps
        }


# ── VNC Controller ────────────────────────────────────────────────────────────


class VNCController:
    """Controla escritorios remotos vía VNC (xtightvncviewer + xdotool).

    Uso:
        vnc = VNCController()
        vnc.connect("192.168.1.100", password="secreto")
        vnc.click(500, 300)
        vnc.type_text("hola mundo")
        vnc.screenshot("/tmp/vnc_view.png")
        vnc.disconnect()
    """

    def __init__(self, display: str = ":0"):
        self.display = display
        self._viewer_pid: Optional[int] = None
        self._host: str = ""
        self._vnc_display: int = 0
        self._connected = False
        self._viewer_wid: Optional[int] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._viewer_pid is not None

    def connect(self, host: str, port: int = 5900,
                password: Optional[str] = None,
                view_only: bool = False) -> bool:
        """Conecta a un servidor VNC.

        Args:
            host: IP o hostname del servidor VNC.
            port: Puerto VNC (default 5900).
            password: Contraseña VNC (opcional).
            view_only: Solo ver, no interactuar.

        Returns:
            True si la conexión fue exitosa.
        """
        if self._connected:
            self.disconnect()

        self._host = host
        self._vnc_display = port - 5900

        # Construir comando
        cmd = ["xtightvncviewer", "-viewonly" if view_only else "-fullscreen",
               f"{host}::{self._vnc_display}"]

        if password:
            # Pasar password vía stdin
            import subprocess as sp
            env = {**os.environ, "DISPLAY": self.display,
                   "VNCPASSWD": password}

        try:
            env = {**os.environ, "DISPLAY": self.display}
            proc = subprocess.Popen(
                cmd, env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.PIPE if password else None
            )
            if password and proc.stdin:
                proc.stdin.write(password.encode() + b"\n")
                proc.stdin.flush()

            time.sleep(2)  # Esperar a que se abra la ventana

            # Verificar que el proceso sigue vivo
            if proc.poll() is not None:
                log.error("VNC viewer cerró inmediatamente")
                return False

            self._viewer_pid = proc.pid
            self._connected = True
            self._find_viewer_window()
            log.info("VNC conectado a %s:%d (pid=%d, wid=%s)",
                     host, port, proc.pid, self._viewer_wid)
            return True

        except FileNotFoundError:
            log.warning("xtightvncviewer no instalado. Intentando con vncviewer...")
            return self._connect_fallback(host, port, password, view_only)
        except Exception as e:
            log.error("VNC connect error: %s", e)
            return False

    def _connect_fallback(self, host: str, port: int,
                          password: Optional[str],
                          view_only: bool) -> bool:
        """Fallback con otros visores VNC."""
        for viewer in ["vncviewer", "krdc", "vinagre", "remmina"]:
            if shutil.which(viewer):
                try:
                    cmd = [viewer, f"{host}:{port - 5900}"]
                    proc = subprocess.Popen(
                        cmd, env={**os.environ, "DISPLAY": self.display},
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    time.sleep(3)
                    if proc.poll() is None:
                        self._viewer_pid = proc.pid
                        self._connected = True
                        self._host = host
                        self._vnc_display = port - 5900
                        self._find_viewer_window()
                        log.info("VNC (fallback %s) conectado a %s:%d",
                                 viewer, host, port)
                        return True
                except Exception:
                    continue

        log.error("Ningún visor VNC disponible. Instala xtightvncviewer.")
        return False

    def disconnect(self):
        """Desconecta del servidor VNC."""
        if self._viewer_pid:
            try:
                import signal
                os.kill(self._viewer_pid, signal.SIGTERM)
            except Exception:
                pass
            self._viewer_pid = None
        self._connected = False
        self._viewer_wid = None
        log.info("VNC desconectado de %s", self._host)

    def click(self, x: int, y: int, button: int = 1):
        """Click en coordenadas de la ventana VNC."""
        if not self._connected:
            return
        self._ensure_focus()
        # Mover y clickear en la ventana VNC
        self._xdotool(f"mousemove --window {self._viewer_wid} {x} {y}")
        self._xdotool(f"click --window {self._viewer_wid} {button}")

    def type_text(self, text: str):
        """Escribe texto en la ventana VNC."""
        if not self._connected:
            return
        self._ensure_focus()
        escaped = text.replace("'", "\\'")
        self._xdotool(f"type --window {self._viewer_wid} '{escaped}'")

    def key(self, key: str):
        """Envía una tecla a la ventana VNC."""
        if not self._connected:
            return
        self._ensure_focus()
        self._xdotool(f"key --window {self._viewer_wid} {key}")

    def screenshot(self, output_path: str = "/tmp/eidos_vnc.png") -> bool:
        """Captura la ventana VNC."""
        if not self._viewer_wid:
            return False
        try:
            subprocess.run(
                ["scrot", "-z", output_path, "--window", str(self._viewer_wid)],
                env={**os.environ, "DISPLAY": self.display},
                capture_output=True, timeout=10
            )
            return os.path.exists(output_path)
        except Exception:
            return False

    def capture_frame(self) -> Optional[Any]:
        """Captura la ventana VNC como array numpy RGB."""
        tmp = tempfile.mktemp(suffix=".png")
        try:
            if self.screenshot(tmp):
                import numpy as np
                from PIL import Image
                img = Image.open(tmp)
                return np.array(img.convert("RGB"))
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_viewer_window(self):
        """Encuentra el window ID del visor VNC."""
        try:
            for pattern in ["TightVNC", "VNC Viewer", "TigerVNC",
                           "KRDC", "Vinagre", "Remmina"]:
                r = subprocess.run(
                    ["xdotool", "search", "--name", pattern],
                    capture_output=True, text=True, timeout=3
                )
                wids = [int(l) for l in r.stdout.strip().split("\n")
                       if l.strip().isdigit()]
                if wids:
                    self._viewer_wid = wids[0]
                    return
        except Exception:
            pass

    def _ensure_focus(self):
        """Asegura que la ventana VNC tiene el foco."""
        if self._viewer_wid:
            try:
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync",
                     str(self._viewer_wid)],
                    capture_output=True, timeout=2
                )
            except Exception:
                pass

    def _xdotool(self, args: str):
        """Ejecuta comando xdotool."""
        try:
            subprocess.run(
                f"xdotool {args}",
                shell=True, capture_output=True, timeout=5,
                env={**os.environ, "DISPLAY": self.display}
            )
        except Exception:
            pass


# ── Auto Installer ────────────────────────────────────────────────────────────


class AutoInstaller:
    """Instala dependencias de EIDOS automáticamente según el SO.

    Uso:
        ai = AutoInstaller()
        ai.install_missing()             # Instalar todo lo que falta
        report = ai.check_dependencies() # Ver qué falta
    """

    def __init__(self):
        self._distro = self._detect_distro()
        self._package_manager = self._detect_package_manager()
        log.info("AutoInstaller: distro=%s, pm=%s", self._distro, self._package_manager)

    @staticmethod
    def _detect_distro() -> str:
        """Detecta la distribución Linux."""
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    content = f.read().lower()
                for distro in ["kali", "debian", "ubuntu", "arch", "fedora",
                              "centos", "rhel", "opensuse", "manjaro"]:
                    if distro in content:
                        if distro in ("kali", "ubuntu"):
                            return "debian"  # basados en Debian
                        if distro in ("manjaro",):
                            return "arch"    # basado en Arch
                        if distro in ("centos", "rhel"):
                            return "fedora" # familia Red Hat
                        return distro
            # Fallback: probar comandos
            if shutil.which("apt"):
                return "debian"
            if shutil.which("pacman"):
                return "arch"
            if shutil.which("dnf"):
                return "fedora"
            if shutil.which("yum"):
                return "fedora"
        except Exception:
            pass
        return "debian"  # default assumption

    @staticmethod
    def _detect_package_manager() -> str:
        if shutil.which("apt"):
            return "apt"
        if shutil.which("pacman"):
            return "pacman"
        if shutil.which("dnf"):
            return "dnf"
        if shutil.which("yum"):
            return "yum"
        return "unknown"

    def check_dependencies(self) -> Dict[str, Any]:
        """Verifica qué dependencias están instaladas y cuáles faltan."""
        missing_python = []
        installed_system = []
        missing_system = []

        # Verificar paquetes Python
        for pkg in EIDOS_DEPS["python"]["packages"]:
            if not self._has_python_package(pkg):
                missing_python.append(pkg)

        # Verificar paquetes del sistema
        sys_pkgs = EIDOS_DEPS["system"].get(self._distro,
                                             EIDOS_DEPS["system"]["debian"])
        for pkg in sys_pkgs:
            if self._has_system_package(pkg):
                installed_system.append(pkg)
            else:
                missing_system.append(pkg)

        return {
            "distro": self._distro,
            "package_manager": self._package_manager,
            "python_installed": len(EIDOS_DEPS["python"]["packages"]) - len(missing_python),
            "python_missing": missing_python,
            "system_installed": len(installed_system),
            "system_missing": missing_system,
            "ready": len(missing_python) == 0 and len(missing_system) == 0
        }

    def install_missing(self, dry_run: bool = False) -> Dict[str, Any]:
        """Instala las dependencias que faltan.

        Returns:
            Dict con resultado de la instalación.
        """
        deps = self.check_dependencies()
        if deps["ready"]:
            log.info("Todas las dependencias instaladas.")
            return {"installed": 0, "failed": [], "dry_run": dry_run}

        installed = 0
        failed = []

        # Instalar paquetes del sistema
        if deps["system_missing"]:
            if self._package_manager == "apt":
                cmds = [
                    ["sudo", "apt-get", "update"],
                    ["sudo", "apt-get", "install", "-y"] + deps["system_missing"]
                ]
            elif self._package_manager == "pacman":
                cmds = [
                    ["sudo", "pacman", "-Syu", "--noconfirm"] + deps["system_missing"]
                ]
            elif self._package_manager in ("dnf", "yum"):
                cmds = [
                    ["sudo", self._package_manager, "install", "-y"] + deps["system_missing"]
                ]
            else:
                cmds = []

            for cmd in cmds:
                if dry_run:
                    log.info("[dry] Ejecutaría: %s", " ".join(cmd))
                    installed += len(deps["system_missing"])
                else:
                    try:
                        r = subprocess.run(cmd, capture_output=True, text=True,
                                         timeout=120)
                        if r.returncode == 0:
                            installed += len(deps["system_missing"])
                            log.info("Instalados %d paquetes de sistema",
                                     len(deps["system_missing"]))
                        else:
                            failed.append(" ".join(cmd[:3]))
                            log.warning("Falló: %s → %s", " ".join(cmd[:3]),
                                       r.stderr[:100])
                    except Exception as e:
                        failed.append(f"{' '.join(cmd[:3])}: {e}")

        # Instalar paquetes Python
        if deps["python_missing"]:
            for pkg in deps["python_missing"]:
                if dry_run:
                    installed += 1
                else:
                    try:
                        r = subprocess.run(
                            ["pip3", "install", "--user", pkg],
                            capture_output=True, text=True, timeout=60
                        )
                        if r.returncode == 0:
                            installed += 1
                        else:
                            failed.append(pkg)
                    except Exception as e:
                        failed.append(f"{pkg}: {e}")

        log.info("AutoInstall: %d instalados, %d fallos", installed, len(failed))
        return {"installed": installed, "failed": failed, "dry_run": dry_run}

    @staticmethod
    def _has_python_package(pkg: str) -> bool:
        try:
            __import__(pkg.replace("-", "_"))
            return True
        except ImportError:
            return False

    @staticmethod
    def _has_system_package(pkg: str) -> bool:
        """Verifica si un binario/paquete del sistema está instalado."""
        # Si es un binario común
        if pkg in ("scrot", "xdotool", "ffmpeg", "sqlite3", "curl", "git",
                   "tesseract", "firefox-esr", "firefox", "konsole", "dolphin"):
            return shutil.which(pkg) is not None

        # Para paquetes tipo librería, probar dpkg/pacman
        try:
            if shutil.which("dpkg"):
                r = subprocess.run(
                    ["dpkg", "-l", pkg], capture_output=True, timeout=5
                )
                return r.returncode == 0 and b"no packages found" not in r.stdout
            if shutil.which("pacman"):
                r = subprocess.run(
                    ["pacman", "-Qi", pkg], capture_output=True, timeout=5
                )
                return r.returncode == 0
        except Exception:
            pass

        return False


# ── Expansion Manager ─────────────────────────────────────────────────────────


class ExpansionManager:
    """Coordina la expansión de EIDOS a nuevos nodos.

    Uso:
        exp = ExpansionManager()
        nodes = exp.discover_nodes()         # Escanear red local
        result = exp.expand_to("192.168.1.100")  # Expandirse a un nodo
    """

    def __init__(self):
        self.vnc = VNCController()
        self.installer = AutoInstaller()
        self._known_nodes: Dict[str, RemoteNode] = {}
        log.info("ExpansionManager inicializado")

    def discover_nodes(self, subnet: Optional[str] = None,
                       timeout: float = 5.0) -> List[RemoteNode]:
        """Descubre nodos accesibles en la red.

        Args:
            subnet: Subred a escanear (None = auto-detectar).
            timeout: Timeout por host.

        Returns:
            Lista de RemoteNode encontrados.
        """
        # Auto-detectar subred
        if subnet is None:
            subnet = self._detect_subnet()
            if not subnet:
                log.warning("No se pudo detectar la subred")
                return []

        nodes: List[RemoteNode] = []
        # Escanear IPs en la subred (rango /24)
        base = ".".join(subnet.split(".")[:3])
        log.info("Escaneando %s.0/24 ...", base)

        for i in range(1, 255):
            host = f"{base}.{i}"
            if host in self._known_nodes:
                continue

            # Quick TCP ping a puerto 22 (SSH)
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout * 0.3)
                result = sock.connect_ex((host, 22))
                sock.close()

                if result == 0:
                    node = RemoteNode(host=host, port=22, reachable=True)
                    # Intentar detectar OS
                    node.node_type = self._detect_node_type(host, timeout)
                    nodes.append(node)
                    self._known_nodes[host] = node
                    log.debug("Nodo encontrado: %s (%s)", host, node.node_type.value)

            except Exception:
                continue

        log.info("Discovery: %d nodos encontrados", len(nodes))
        return nodes

    def expand_to(self, host: str, *,
                  username: str = "",
                  password: str = "",
                  ssh_key: Optional[str] = None,
                  dry_run: bool = False) -> Dict[str, Any]:
        """Expande EIDOS a un nodo remoto.

        Args:
            host: IP o hostname del nodo.
            username: Usuario SSH.
            password: Contraseña SSH.
            ssh_key: Ruta a clave SSH privada.

        Returns:
            Dict con resultado de la expansión.
        """
        node = self._known_nodes.get(host) or RemoteNode(host=host)
        result = {
            "host": host, "success": False,
            "steps": [], "errors": [], "dry_run": dry_run
        }

        # Paso 1: Verificar conectividad
        if not self._check_reachable(host):
            result["errors"].append("Host no alcanzable")
            return result
        result["steps"].append("✅ Conectividad OK")

        # Paso 2: Detectar OS
        if node.node_type == NodeType.UNKNOWN:
            node.node_type = self._detect_node_type(host)

        if node.node_type == NodeType.WINDOWS:
            result["errors"].append(
                "Windows requiere instalación manual (usa install_clone_windows.ps1)"
            )
            return result

        result["steps"].append(f"OS: {node.node_type.value}")

        # Paso 3: Verificar/instalar dependencias
        deps = self.installer.check_dependencies()
        if not deps["ready"]:
            if dry_run:
                result["steps"].append(
                    f"[dry] Instalaría {len(deps['system_missing'])} sys + "
                    f"{len(deps['python_missing'])} py paquetes"
                )
            else:
                install_result = self.installer.install_missing()
                if install_result["failed"]:
                    result["errors"].append(
                        f"Falló instalación de: {install_result['failed']}"
                    )
                else:
                    result["steps"].append(
                        f"✅ Instaladas {install_result['installed']} dependencias"
                    )
        else:
            result["steps"].append("✅ Dependencias OK")

        # Paso 4: Copiar binarios/core de EIDOS (si SSH disponible)
        if not dry_run:
            push_result = self._push_eidos(host, username, password, ssh_key)
            if push_result.get("success"):
                result["steps"].append(f"✅ EIDOS desplegado en {host}")
            else:
                result["steps"].append(
                    f"⚠️  No se pudo desplegar automáticamente: "
                    f"{push_result.get('error', '?')}"
                )

        result["success"] = len(result["errors"]) == 0
        if node not in self._known_nodes.values():
            self._known_nodes[host] = node

        log.info("Expansión a %s: %s", host,
                 "exitosa" if result["success"] else "con errores")
        return result

    def list_nodes(self) -> List[Dict[str, Any]]:
        """Lista todos los nodos conocidos."""
        return [n.to_dict() for n in self._known_nodes.values()]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_subnet() -> Optional[str]:
        """Detecta la subred local."""
        try:
            r = subprocess.run(["ip", "-br", "addr"], capture_output=True,
                             text=True, timeout=5)
            for line in r.stdout.split("\n"):
                m = re.search(r"(\d+\.\d+\.\d+)\.\d+/\d+", line)
                if m and not line.startswith("lo"):
                    return m.group(1) + ".0"
        except Exception:
            pass
        return None

    @staticmethod
    def _check_reachable(host: str, port: int = 22,
                         timeout: float = 3.0) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    @staticmethod
    def _detect_node_type(host: str, timeout: float = 3.0) -> NodeType:
        """Intenta detectar el SO de un nodo remoto."""
        # Probar SSH + uname
        try:
            r = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=3",
                 "-o", "StrictHostKeyChecking=no",
                 "-o", "BatchMode=yes",
                 host, "uname -s 2>/dev/null || ver 2>/dev/null"],
                capture_output=True, text=True, timeout=timeout
            )
            out = r.stdout.strip().lower()
            if "linux" in out:
                return NodeType.LINUX
            elif "darwin" in out:
                return NodeType.MAC
            elif "windows" in out or "nt" in out:
                return NodeType.WINDOWS
        except Exception:
            pass

        # Probar puertos comunes
        for port, ntype in [(3389, NodeType.WINDOWS),   # RDP
                           (5900, NodeType.LINUX),      # VNC
                           (22, NodeType.LINUX)]:       # SSH
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                if sock.connect_ex((host, port)) == 0:
                    sock.close()
                    return ntype
                sock.close()
            except Exception:
                pass

        return NodeType.UNKNOWN

    @staticmethod
    def _push_eidos(host: str, username: str = "",
                    password: str = "",
                    ssh_key: Optional[str] = None) -> Dict[str, Any]:
        """Intenta copiar EIDOS al nodo remoto vía SCP."""
        target = f"{username}@{host}" if username else host
        eidos_dir = Path.home() / "EIDOS"

        # Intentar crear directorio remoto
        ssh_cmd = ["ssh", "-o", "ConnectTimeout=5",
                   "-o", "StrictHostKeyChecking=no"]
        if ssh_key:
            ssh_cmd.extend(["-i", ssh_key])

        try:
            # Crear directorio
            r = subprocess.run(
                ssh_cmd + [target, "mkdir -p ~/EIDOS/core ~/EIDOS/bin"],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode != 0:
                return {"success": False, "error": f"SSH mkdir: {r.stderr[:100]}"}

            # Copiar archivos core
            scp_cmd = ["scp", "-o", "ConnectTimeout=5",
                      "-o", "StrictHostKeyChecking=no"]
            if ssh_key:
                scp_cmd.extend(["-i", ssh_key])

            for f in eidos_dir.glob("core/*.py"):
                subprocess.run(
                    scp_cmd + [str(f), f"{target}:~/EIDOS/core/"],
                    capture_output=True, timeout=30
                )

            return {"success": True, "host": host}

        except Exception as e:
            return {"success": False, "error": str(e)[:200]}


# ── Capability Discovery ──────────────────────────────────────────────────────


class CapabilityDiscovery:
    """Descubre qué capacidades tiene EIDOS en el entorno actual.

    Uso:
        cd = CapabilityDiscovery()
        caps = cd.discover()
        # caps = {"screen_capture": True, "ocr": True, "gui_control": True, ...}
    """

    @staticmethod
    def discover() -> Dict[str, bool]:
        """Descubre todas las capacidades disponibles."""
        caps = {}

        # Screen capture
        caps["screen_capture"] = (
            shutil.which("scrot") is not None or
            shutil.which("ffmpeg") is not None
        )

        # OCR
        caps["ocr"] = shutil.which("tesseract") is not None

        # GUI control
        caps["gui_control"] = shutil.which("xdotool") is not None

        # VNC
        caps["vnc_viewer"] = (
            shutil.which("xtightvncviewer") is not None or
            shutil.which("vncviewer") is not None
        )
        caps["vnc_server"] = shutil.which("x11vnc") is not None

        # Audio
        caps["audio_playback"] = (
            shutil.which("paplay") is not None or
            shutil.which("aplay") is not None
        )
        caps["audio_capture"] = (
            shutil.which("parecord") is not None or
            shutil.which("arecord") is not None
        )

        # Web
        caps["browser"] = (
            shutil.which("firefox-esr") is not None or
            shutil.which("firefox") is not None or
            shutil.which("google-chrome-stable") is not None
        )
        caps["http_server"] = True  # Flask siempre disponible

        # Network
        caps["ssh"] = shutil.which("ssh") is not None
        caps["vpn"] = shutil.which("tailscale") is not None

        # Docker
        caps["docker"] = shutil.which("docker") is not None

        # Python libs
        caps["ml_available"] = False
        try:
            __import__("numpy")
            __import__("scipy")
            caps["ml_available"] = True
        except ImportError:
            pass

        caps["cv_available"] = False
        try:
            __import__("cv2")
            __import__("PIL")
            caps["cv_available"] = True
        except ImportError:
            pass

        caps["nlp_available"] = False
        try:
            __import__("nltk")
            caps["nlp_available"] = True
        except ImportError:
            pass

        return caps

    @staticmethod
    def report() -> str:
        """Genera un reporte legible de capacidades."""
        caps = CapabilityDiscovery.discover()
        lines = ["🔍 Capacidades de EIDOS en este entorno:", ""]
        categories = {
            "🖥️  Pantalla/GUI": ["screen_capture", "ocr", "gui_control",
                                "vnc_viewer", "vnc_server"],
            "🔊 Audio": ["audio_playback", "audio_capture"],
            "🌐 Red": ["http_server", "browser", "ssh", "vpn"],
            "🐳 Contenedores": ["docker"],
            "🧠 ML/CV/NLP": ["ml_available", "cv_available", "nlp_available"],
        }
        for cat, keys in categories.items():
            lines.append(cat)
            for k in keys:
                icon = "✅" if caps.get(k) else "❌"
                lines.append(f"  {icon} {k}")
        return "\n".join(lines)


# ── Singleton ──────────────────────────────────────────────────────────────────

_expansion_manager: Optional[ExpansionManager] = None


def get_expansion_manager() -> ExpansionManager:
    global _expansion_manager
    if _expansion_manager is None:
        _expansion_manager = ExpansionManager()
    return _expansion_manager


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="EIDOS Expansion Manager")
    p.add_argument("--discover", action="store_true", help="Descubrir nodos en red")
    p.add_argument("--expand", type=str, help="Expandirse a host")
    p.add_argument("--capabilities", action="store_true",
                   help="Mostrar capacidades")
    p.add_argument("--install-deps", action="store_true",
                   help="Instalar dependencias faltantes")
    p.add_argument("--dry-run", action="store_true", help="No ejecutar cambios")
    args = p.parse_args()

    if args.capabilities:
        print(CapabilityDiscovery.report())

    if args.install_deps:
        ai = AutoInstaller()
        deps = ai.check_dependencies()
        print(f"Distro: {deps['distro']}, PM: {deps['package_manager']}")
        print(f"Sistema: {deps['system_installed']} OK, "
              f"{len(deps['system_missing'])} faltan")
        print(f"Python: {deps['python_installed']} OK, "
              f"{len(deps['python_missing'])} faltan")
        if not deps["ready"]:
            print("\nInstalando...")
            result = ai.install_missing(dry_run=args.dry_run)
            print(f"Resultado: {result['installed']} instalados, "
                  f"{len(result['failed'])} fallos")

    if args.discover:
        em = ExpansionManager()
        nodes = em.discover_nodes()
        for n in nodes:
            print(f"  {n.host} — {n.node_type.value} — {'✅' if n.reachable else '❌'}")

    if args.expand:
        em = ExpansionManager()
        result = em.expand_to(args.expand, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
