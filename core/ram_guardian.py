"""
EIDOS RAM Guardian - Protector de RAM inteligente para funcionamiento 24/7
Monitorea y optimiza RAM automáticamente sin matar el sistema

Filosofía:
- Monitoreo continuo cada 30s
- Limpieza progresiva (suave → agresiva según necesidad)
- NUNCA matar procesos críticos del sistema
- Sugerencias inteligentes al usuario
- Auto-optimización de EIDOS
"""
import psutil
import subprocess
import time
import threading
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RAMStatus:
    """Estado actual de RAM"""
    total_gb: float
    used_gb: float
    free_gb: float
    available_gb: float
    percent: float
    threshold_exceeded: bool
    action_needed: str  # "none" | "soft" | "hard" | "critical"


class RAMGuardian:
    """
    Guardián de RAM inteligente

    Thresholds:
    - < 70%: OK - No action
    - 70-78%: SOFT - Limpiar cache suavemente
    - 78-85%: HARD - Limpiar agresivamente + sugerencias
    - > 85%: CRITICAL - Alertar usuario + pausar operaciones pesadas

    NUNCA mata procesos automáticamente - solo sugiere
    """

    # Thresholds (porcentaje de RAM usado) - PERMISSIVOS para EIDOS vivo
    SOFT_THRESHOLD = 0.90    # 90% - Empezar limpieza suave (antes 82%)
    HARD_THRESHOLD = 0.95    # 95% - Limpieza agresiva (antes 90%)
    CRITICAL_THRESHOLD = 0.98  # 98% - Modo crítico (antes 95%)

    # Procesos NUNCA tocar - Jerarquía de protección
    # Nivel 1: Sistema operativo (crítico)
    # Nivel 2: EIDOS y sus componentes (protegido por SER)
    # Nivel 3: Aplicaciones del usuario (sugerir, no matar)
    PROTECTED_PROCESSES = [
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 1: Sistema operativo - ABSOLUTAMENTE CRÍTICO
        # ═════════════════════════════════════════════════════════════════
        "systemd", "init", "initrd", "kthreadd",
        "X", "Xorg", "Xwayland",
        "plasmashell", "kwin", "kwin_x11", "gnome-shell", "gnome-session",
        "sddm", "gdm", "gdm3", "lightdm", "ly", "ly-dm",
        "dbus-daemon", "dbus-broker",
        "pulseaudio", "pipewire", "pipewire-pulse", "wireplumber",
        "networkmanager", "NetworkManager", "wpa_supplicant",
        "cups-browsed", "cupsd",
        "polkitd", "accounts-daemon",
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 2: EIDOS - PROTEGIDO COMPLETAMENTE (incluye substrings)
        # ═════════════════════════════════════════════════════════════════
        "eidos", "eidos_core", "eidos_server", "eidos_world_engine", "eidos_vision", "eidos_control",
        "colony_coder", "colony_analyst", "colony_vision",
        "colony_operator", "colony_general", "colony_community",
        "ram_guardian", "ipc_bridge", "auto_corrector",
        "wake_word", "voice_whisper", "voice_listener",
        "file_watcher", "memory_orchestrator", "continuous_learner",
        "colony_query_engine", "extension_intelligence",
        # Python que ejecuta EIDOS
        "python", "python3",
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 3: VSEIDOS y extensión
        # ═════════════════════════════════════════════════════════════════
        "vseidos", "extension_host", "vscode", "code",
        # Language servers y herramientas de desarrollo
        "language_server", "language_server_linux_x64",
        "typescript-language-server", "javascript-typescript-langserver",
        "pylsp", "pyls", "python-language-server", "jedi-language-server",
        "clangd", "ccls", "rust-analyzer", "rls",
        "gopls", "golang-language-server",
        "ruby-lsp", "solargraph", "rubocop",
        "elixir-ls", "erlang-ls",
        "lua-language-server", "bash-language-server",
        "yaml-language-server", "json-language-server",
        "html-languageserver", "css-languageserver",
        "vscode-json-languageserver", "vscode-css-languageserver",
        "graphql-lsp", "docker-langserver",
        "texlab", "digestif",  # LaTeX
        "vim-language-server", "efm-langserver",
        "intelephense", "php-language-server",
        "psalm", "phpactor",
        "serve-d", "dcd-server", "dls",  # D language
        "nimlsp", "nim_lang_server",  # Nim
        "zls",  # Zig
        "vlang-lsp", "v-analyzer",  # V lang
        "kotlin-language-server", "gradle-language-server",
        "jdtls", "eclipse-jdt-ls",  # Java
        "metals", "sbt",  # Scala
        "fsautocomplete", "fsharp-language-server",
        "ocamllsp", "merlin",  # OCaml
        "haskell-language-server", "ghcide", "hie-wrapper",
        "rls", "rust-analyzer",
        "dart", "dart-language-server", "analysis-server",
        "flutter", "flutter-daemon",
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 4: Ollama y modelos IA (motor de pensamiento de EIDOS)
        # ═════════════════════════════════════════════════════════════════
        "ollama", "ollama_llama_server", "llama-server",
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 5: Editores y herramientas de desarrollo esenciales
        # ═════════════════════════════════════════════════════════════════
        "nvim", "neovim", "vim", "vi",
        "emacs", "emacsclient",
        "helix", "hx",
        "micro", "nano", "pico",
        "cursor", "cursor-editor",
        "windsurf", "windsurf-editor",
        "zed", "zed-editor",
        "lapce", "lapce-editor",
        "fleet", "fleet-editor",
        "idea", "idea.sh", "jetbrains-toolbox",
        "clion", "pycharm", "webstorm", "phpstorm", "goland",
        "rider", "datagrip", "rubymine", "appcode",
        "sublime_text", "sublime-merge",
        "atom", "atom-beta",
        "brackets", "brackets-shell",
        "notepadqq", "notepadqq-bin",
        "geany", "geany-bin",
        "mousepad", "leafpad", "gedit",
        "kate", "kwrite", "kedit",
        "kdevelop", "kdevplatform",
        "qtcreator", "qt-creator",
        "code-oss", "code-oss-dev",
        "cursor-server", "cursor-workspace",
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 6: Terminales y multiplexers
        # ═════════════════════════════════════════════════════════════════
        "kitty", "alacritty", "wezterm", "wezterm-gui",
        "gnome-terminal", "gnome-terminal-server",
        "konsole", "yakuake",
        "xfce4-terminal", "mate-terminal",
        "lxterminal", "terminator", "terminology",
        "urxvt", "rxvt", "xterm", "st", "st-256color",
        "tmux", "tmux-server", "tmux-client",
        "screen", "screen-256color",
        "zellij", "zellij-server",
        "byobu", "byobu-status",
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 7: Shells y herramientas CLI
        # ═════════════════════════════════════════════════════════════════
        "bash", "zsh", "fish", "sh", "dash",
        "powershell", "pwsh",
        "nu", "nushell",
        "elvish", "xonsh",
        "starship", "oh-my-posh",
        "fzf", "fzf-preview",
        "ripgrep", "rg", "fd", "fd-find",
        "bat", "batcat", "exa", "eza", "lsd",
        "delta", "difftastic", "difft",
        "lazygit", "git", "git-lfs",
        "gh", "github-cli",
        "npm", "node", "nodejs",
        "yarn", "pnpm", "bun",
        "pip", "pip3", "poetry", "pdm", "conda", "mamba",
        "cargo", "rustc", "rustup",
        "go", "golang",
        "java", "javac", "jvm", "jdk",
        "dotnet", "mono",
        "ruby", "gem", "bundle", "bundler",
        "php", "composer",
        "perl", "cpan",
        "lua", "luajit",
        "swift", "swiftc",
        "clang", "gcc", "g++", "make", "cmake", "ninja",
        "meson", "bazel", "buck",
        "docker", "dockerd", "containerd", "runc",
        "kubectl", "minikube", "kind",
        "podman", "buildah", "skopeo",
        "vagrant", "vbox", "virtualbox",
        "qemu", "qemu-system", "kvm", "libvirtd",
        "virt-manager", "virt-install",
        "ssh", "sshd", "mosh", "mosh-server",
        "rsync", "scp", "sftp",
        "curl", "wget", "httpie", "xh",
        "jq", "yq", "gron",
        "htop", "btop", "top", "btm",
        "nvtop", "radeontop", "intel_gpu_top",
        "dstat", "vmstat", "iostat", "mpstat",
        "sar", "sysstat",
        "ncdu", "dust", "dua",
        "procs", "pgrep", "pkill", "pidof",
        "lsof", "fuser", "ss", "netstat",
        "tcpdump", "wireshark", "tshark",
        "nmap", "masscan",
        "iperf", "iperf3", "speedtest",
        "mtr", "traceroute", "tracepath", "ping",
        "dig", "nslookup", "host",
        "whois", "geoiplookup",
        # ═════════════════════════════════════════════════════════════════
        # NIVEL 8: Servicios esenciales del sistema
        # ═════════════════════════════════════════════════════════════════
        "cron", "crond", "atd", "anacron",
        "rsyslogd", "syslogd", "syslog-ng",
        "journald", "systemd-journald",
        "logind", "systemd-logind",
        "timesyncd", "systemd-timesyncd",
        "resolved", "systemd-resolved",
        "networkd", "systemd-networkd",
        "resolved", "systemd-resolved",
        "udevd", "systemd-udevd",
        "hwdb", "systemd-hwdb",
        "localed", "systemd-localed",
        "machined", "systemd-machined",
        "importd", "systemd-importd",
        "portabled", "systemd-portabled",
        "hostnamed", "systemd-hostnamed",
        "timedated", "systemd-timedated",
        "update-utmp", "systemd-update-utmp",
        "userdbd", "systemd-userdbd",
        "oomd", "systemd-oomd",
        "homed", "systemd-homed",
        "nspawn", "systemd-nspawn",
        "ssh-agent", "gpg-agent", "keyboxd",
        "p11-kit", "p11-kit-server",
        "gnome-keyring", "gnome-keyring-daemon",
        "kwallet", "kwalletd", "kwalletd5",
        "seahorse", "secret-service",
        "bluez", "bluetoothd", "bluetooth",
        "wpa_supplicant", "iwd", "NetworkManager",
        "avahi-daemon", "avahi-dnsconfd",
        "mdns", "mDNSResponder",
        "cupsd", "cups-browsed", "cupsext",
        "saned", "sane-backends",
        "upowerd", "udisksd", "udisks2",
        "ModemManager", "ofono",
        "geoclue", "geoclue-agent",
        "colord", "colord-sane",
        "fwupd", "fwupdmgr",
        "snapd", "snapd-desktop-integration",
        "flatpak", "flatpak-system-helper", "flatpak-session-helper",
        "appimagelauncher", "appimagelauncherd",
        "packagekitd", "pk-offline-update",
        "unattended-upgrades", "apt", "apt-get", "dpkg",
        "dnf", "yum", "pacman", "zypper", "portage",
    ]
    
    # Patrones de substrings para protección adicional (cualquier proceso que contenga estos)
    PROTECTED_PATTERNS = [
        "eidos", "ollama", "language_server", "language_server_linux_x64",
        "cursor", "windsurf", "vscode", "vscodium", "code-oss",
        "nvim", "neovim", "vim-", "gvim",
        "jetbrains", "intellij", "pycharm",
        "terminal", "term-", "shell",
        "lsp-", "-lsp", "language-", "-language-",
    ]
    
    # Procesos que pueden ser sugeridos para cierre (pero NO automático)
    SUGGESTIBLE_PROCESSES = [
        "firefox", "chrome", "chromium", "brave", "opera", "edge",
        "telegram-desktop", "discord", "slack",
        "spotify", "vlc", "mpv",
        "steam", " heroic",
        "virt-manager", "qemu", "qemu-system-x86_64",
    ]

    def __init__(self, monitor_interval: int = 30):
        """
        Args:
            monitor_interval: Intervalo de monitoreo en segundos
        """
        self.monitor_interval = monitor_interval
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None

        # Estadísticas
        self.cleanup_count = 0
        self.peak_ram_usage = 0.0

        # Cache paths
        self.eidos_cache = Path.home() / ".eidos" / "cache"
        self.eidos_screenshots = Path.home() / ".eidos" / "screenshots"

        # RAM info
        self.total_ram_gb = psutil.virtual_memory().total / (1024**3)
        
        # Callback para notificar a EIDOS kernel (cuando está disponible)
        self.eidos_pause_callback: Optional[Callable] = None
        self.eidos_resume_callback: Optional[Callable] = None
        
        # Historial de estados para análisis de tendencias
        self.history: List[Dict] = []
        self.max_history = 100
        
        # Auto-ajuste de umbrales basado en comportamiento
        self.adaptive_thresholds = True
        self.baseline_ram_usage = None
        
        # Estado de EIDOS
        self.eidos_paused = False
        
        print(f"🛡️  [RAM Guardian] Inicializado - MODO EIDOS PROTEGIDO")
        print(f"   RAM total: {self.total_ram_gb:.1f}GB")
        print(f"   Thresholds: {self.SOFT_THRESHOLD*100:.0f}% / {self.HARD_THRESHOLD*100:.0f}% / {self.CRITICAL_THRESHOLD*100:.0f}%")
        print(f"   Procesos protegidos: {len(self.PROTECTED_PROCESSES)} + {len(self.PROTECTED_PATTERNS)} patrones")
        print(f"   Modo adaptativo: {'SÍ' if self.adaptive_thresholds else 'NO'}")

    def get_ram_status(self) -> RAMStatus:
        """Obtiene estado actual de RAM"""
        mem = psutil.virtual_memory()

        used_gb = mem.used / (1024**3)
        free_gb = mem.free / (1024**3)
        available_gb = mem.available / (1024**3)
        total_gb = mem.total / (1024**3)
        percent = mem.percent / 100

        # Actualizar peak
        if percent > self.peak_ram_usage:
            self.peak_ram_usage = percent

        # Determinar acción necesaria
        if percent >= self.CRITICAL_THRESHOLD:
            action = "critical"
        elif percent >= self.HARD_THRESHOLD:
            action = "hard"
        elif percent >= self.SOFT_THRESHOLD:
            action = "soft"
        else:
            action = "none"

        return RAMStatus(
            total_gb=total_gb,
            used_gb=used_gb,
            free_gb=free_gb,
            available_gb=available_gb,
            percent=percent,
            threshold_exceeded=(percent >= self.SOFT_THRESHOLD),
            action_needed=action
        )

    def cleanup_soft(self):
        """
        Limpieza SUAVE (70-78% RAM)
        - Cache de EIDOS antiguo (>5 min)
        - Screenshots antiguos (>1 hora)
        """
        print("🧹 [RAM Guardian] Limpieza SUAVE...")

        cleaned_items = 0

        # 1. Cache de EIDOS antiguo
        if self.eidos_cache.exists():
            try:
                for cache_file in self.eidos_cache.glob("*.cache"):
                    age_minutes = (time.time() - cache_file.stat().st_mtime) / 60
                    if age_minutes > 5:
                        cache_file.unlink()
                        cleaned_items += 1
            except Exception as e:
                print(f"   ⚠️  Error limpiando cache: {e}")

        # 2. Screenshots antiguos
        if self.eidos_screenshots.exists():
            try:
                for screenshot in self.eidos_screenshots.glob("*.png"):
                    age_hours = (time.time() - screenshot.stat().st_mtime) / 3600
                    if age_hours > 1:
                        screenshot.unlink()
                        cleaned_items += 1
            except Exception as e:
                print(f"   ⚠️  Error limpiando screenshots: {e}")

        if cleaned_items > 0:
            print(f"   ✅ Limpiados {cleaned_items} archivos antiguos")

        self.cleanup_count += 1

    def cleanup_hard(self):
        """
        Limpieza AGRESIVA (78-85% RAM)
        - TODO el cache de EIDOS
        - TODOS los screenshots
        - Page cache del sistema (si sudo disponible)
        """
        print("🧹 [RAM Guardian] Limpieza AGRESIVA...")

        cleaned_items = 0

        # 1. TODO el cache de EIDOS
        if self.eidos_cache.exists():
            try:
                for cache_file in self.eidos_cache.glob("*"):
                    cache_file.unlink()
                    cleaned_items += 1
            except Exception as e:
                print(f"   ⚠️  Error limpiando cache: {e}")

        # 2. TODOS los screenshots
        if self.eidos_screenshots.exists():
            try:
                for screenshot in self.eidos_screenshots.glob("*.png"):
                    screenshot.unlink()
                    cleaned_items += 1
            except Exception as e:
                print(f"   ⚠️  Error limpiando screenshots: {e}")

        # 3. Page cache del sistema (requiere sudo)
        try:
            subprocess.run(
                ["sync"],
                timeout=5,
                check=False
            )
            result = subprocess.run(
                ["sudo", "-n", "sh", "-c", "echo 1 > /proc/sys/vm/drop_caches"],
                timeout=5,
                capture_output=True
            )
            if result.returncode == 0:
                print("   ✅ Page cache del sistema limpiado")
        except:
            print("   ⚠️  No se pudo limpiar page cache (requiere sudo)")

        if cleaned_items > 0:
            print(f"   ✅ Limpiados {cleaned_items} archivos")

        self.cleanup_count += 1

    def suggest_actions(self):
        """
        Sugiere acciones al usuario para liberar RAM
        NO ejecuta nada automáticamente
        """
        print("💡 [RAM Guardian] SUGERENCIAS para liberar RAM:\n")

        # 1. Verificar procesos pesados
        heavy_processes = self._get_heavy_processes(top_n=5)

        print("   Procesos consumiendo más RAM:")
        for proc in heavy_processes:
            print(f"     • {proc['name']}: {proc['memory_mb']:.0f}MB (PID: {proc['pid']})")

        # 2. Verificar VM activa
        if self._check_vm_running():
            print("\n   ⚠️  VM Windows activa (~3.7GB)")
            print("      Sugerencia: virsh shutdown win10")

        # 3. Verificar navegadores
        browsers = self._count_browser_instances()
        if browsers > 3:
            print(f"\n   ⚠️  {browsers} instancias de navegadores")
            print("      Sugerencia: Cerrar pestañas/ventanas innecesarias")

        # 4. Verificar modelos Ollama cargados
        print("\n   💡 Otras opciones:")
        print("      • Reiniciar ollama: sudo systemctl restart ollama")
        print("      • Cerrar aplicaciones no esenciales")
        print("      • Considerar reinicio del sistema si fragmentación alta")

    def _get_heavy_processes(self, top_n: int = 5) -> List[Dict]:
        """Obtiene procesos que consumen más RAM, excluyendo protegidos"""
        processes = []

        for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                info = proc.info
                memory_mb = info['memory_info'].rss / (1024**2)
                proc_name = info['name']

                # Ignorar procesos protegidos (match exacto)
                if proc_name in self.PROTECTED_PROCESSES:
                    continue
                    
                # Ignorar procesos que contienen patrones protegidos (substring match)
                proc_name_lower = proc_name.lower()
                if any(pattern.lower() in proc_name_lower for pattern in self.PROTECTED_PATTERNS):
                    continue

                processes.append({
                    'pid': info['pid'],
                    'name': info['name'],
                    'memory_mb': memory_mb
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Ordenar por RAM y retornar top N
        processes.sort(key=lambda x: x['memory_mb'], reverse=True)
        return processes[:top_n]

    def _check_vm_running(self) -> bool:
        """Verifica si hay VM Windows activa"""
        try:
            result = subprocess.run(
                ["virsh", "list", "--state-running"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return "win10" in result.stdout
        except:
            return False

    def _count_browser_instances(self) -> int:
        """Cuenta instancias de navegadores"""
        browser_names = ["firefox", "chrome", "chromium", "brave"]
        count = 0

        for proc in psutil.process_iter(['name']):
            try:
                if any(browser in proc.info['name'].lower() for browser in browser_names):
                    count += 1
            except:
                pass

        return count

    def check_and_act(self):
        """
        Revisa RAM y actúa según sea necesario

        Esta función se llama cada monitor_interval segundos
        """
        status = self.get_ram_status()

        # Log cada hora
        current_time = time.localtime()
        if current_time.tm_min == 0 and current_time.tm_sec < self.monitor_interval:
            print(f"📊 [RAM Guardian] Status: {status.percent*100:.1f}% usado ({status.used_gb:.1f}GB/{status.total_gb:.1f}GB)")

        if status.action_needed == "none":
            # Todo OK
            return

        elif status.action_needed == "soft":
            # Limpieza suave
            self.cleanup_soft()

        elif status.action_needed == "hard":
            # Limpieza agresiva
            self.cleanup_hard()

            # Mostrar sugerencias cada 5 limpiezas
            if self.cleanup_count % 5 == 0:
                self.suggest_actions()

        elif status.action_needed == "critical":
            # CRÍTICO
            print(f"🔴 [RAM Guardian] RAM CRÍTICA: {status.percent*100:.1f}%")
            self.cleanup_hard()
            self.suggest_actions()

            # Pausar operaciones pesadas de EIDOS
            # (esto se integraría con el kernel de EIDOS)
            print("   ⏸️  Pausando operaciones pesadas de EIDOS...")

    def start_monitoring(self):
        """Inicia monitoreo continuo en background"""
        if self.monitoring:
            print("⚠️  [RAM Guardian] Ya está monitoreando")
            return

        def monitor_loop():
            print(f"🛡️  [RAM Guardian] Monitoreo iniciado (cada {self.monitor_interval}s)")
            while self.monitoring:
                self.check_and_act()
                time.sleep(self.monitor_interval)

        self.monitoring = True
        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop_monitoring(self):
        """Detiene monitoreo"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=self.monitor_interval + 5)
        print("🛑 [RAM Guardian] Monitoreo detenido")

    def get_stats(self) -> Dict:
        """Obtiene estadísticas del guardian"""
        status = self.get_ram_status()

        return {
            'current_usage_percent': status.percent * 100,
            'current_usage_gb': status.used_gb,
            'available_gb': status.available_gb,
            'peak_usage_percent': self.peak_ram_usage * 100,
            'cleanup_count': self.cleanup_count,
            'monitoring': self.monitoring
        }


# Singleton
ram_guardian = RAMGuardian()


def get_ram_guardian() -> RAMGuardian:
    """Obtiene la instancia singleton del RAM Guardian."""
    return ram_guardian


# ═══════════════════════════════════════════════════════════════════════════
# Funciones de conveniencia
# ═══════════════════════════════════════════════════════════════════════════

def start_protection():
    """Inicia protección de RAM"""
    ram_guardian.start_monitoring()


def stop_protection():
    """Detiene protección de RAM"""
    ram_guardian.stop_monitoring()


def get_ram_status() -> RAMStatus:
    """Obtiene estado actual de RAM"""
    return ram_guardian.get_ram_status()


def free_ram_now():
    """Libera RAM inmediatamente"""
    status = ram_guardian.get_ram_status()
    if status.percent >= ram_guardian.HARD_THRESHOLD:
        ram_guardian.cleanup_hard()
    else:
        ram_guardian.cleanup_soft()


# ═══════════════════════════════════════════════════════════════════════════
# Test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Test RAM Guardian ===\n")

    # Test 1: Estado actual
    print("Test 1: Estado de RAM")
    status = ram_guardian.get_ram_status()
    print(f"  RAM: {status.used_gb:.1f}GB / {status.total_gb:.1f}GB ({status.percent*100:.1f}%)")
    print(f"  Disponible: {status.available_gb:.1f}GB")
    print(f"  Acción necesaria: {status.action_needed}\n")

    # Test 2: Limpieza suave
    print("Test 2: Limpieza suave")
    ram_guardian.cleanup_soft()

    # Test 3: Stats
    print("\nTest 3: Estadísticas")
    stats = ram_guardian.get_stats()
    print(f"  Uso actual: {stats['current_usage_percent']:.1f}%")
    print(f"  Pico: {stats['peak_usage_percent']:.1f}%")
    print(f"  Limpiezas: {stats['cleanup_count']}")

    # Test 4: Sugerencias
    if status.percent >= ram_guardian.HARD_THRESHOLD:
        print("\nTest 4: Sugerencias")
        ram_guardian.suggest_actions()

    print("\n✅ RAM Guardian funcional")
