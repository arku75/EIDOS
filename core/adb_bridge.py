"""
EIDOS core/adb_bridge.py — ADB Bridge: EIDOS ↔ Móvil por USB
=============================================================
Permite a EIDOS ver y controlar cualquier dispositivo Android conectado por USB.

Capacidades:
  - Detecta dispositivos USB y WiFi ADB automáticamente
  - Screenshot del móvil → OCR/VLM para que EIDOS "vea" la pantalla
  - Tap, swipe, texto, teclas, gestos
  - Instalar/desinstalar APKs
  - Push/Pull de archivos
  - Listar, iniciar, detener apps
  - Info completa del dispositivo (modelo, Android version, batería, etc.)
  - Conexión persistente con reconexión automática

Requisitos:
  - adb instalado: sudo apt install adb
  - Depuración USB activada en el móvil
  - Autorización USB aceptada en el dispositivo

Uso:
    from core.adb_bridge import ADBBridge, get_adb
    adb = get_adb()
    info = adb.get_device_info()
    img = adb.take_screenshot()        # → path a PNG en /tmp/
    adb.tap(540, 960)
    adb.type_text("hola mundo")
"""
from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Configuración ────────────────────────────────────────────────────────────
ADB_CMD          = os.environ.get("ADB_PATH", "adb")
SCREENSHOT_DIR   = os.path.expanduser("~/.eidos/adb_screenshots")
RECONNECT_DELAY  = 5   # segundos entre reintentos de conexión
MAX_RETRIES      = 3   # intentos por comando antes de rendirse

os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# ── Excepción propia ─────────────────────────────────────────────────────────
class ADBError(Exception):
    """Error de comunicación con el dispositivo Android."""
    pass


# ── Dataclass de información del dispositivo ─────────────────────────────────
@dataclass
class DeviceInfo:
    serial: str         = ""
    model: str          = ""
    android_version: str = ""
    sdk_version: str    = ""
    battery_level: int  = -1
    screen_width: int   = 0
    screen_height: int  = 0
    wifi_ip: str        = ""
    state: str          = ""   # "device", "offline", "unauthorized"


# ── Core ADB Bridge ──────────────────────────────────────────────────────────
class ADBBridge:
    """
    Controlador completo de dispositivos Android via ADB.
    Diseñado para que EIDOS vea y opere en cualquier móvil conectado por USB.
    """

    def __init__(self, serial: str = "") -> None:
        """
        Args:
            serial: Serie del dispositivo (vacío = usa el primero disponible).
        """
        self._serial     = serial
        self._lock       = threading.Lock()
        self._connected  = False
        self._device     = DeviceInfo()
        self._watchdog   = None  # hilo de reconexión

    # ── Utilidad: ejecutar comando ADB ───────────────────────────────────────

    def _adb(self, *args: str, timeout: int = 30) -> tuple[str, str, int]:
        """
        Ejecuta un comando ADB y devuelve (stdout, stderr, returncode).
        Incluye el serial del dispositivo si está disponible.
        """
        cmd = [ADB_CMD]
        if self._serial:
            cmd += ["-s", self._serial]
        cmd += list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.stdout.strip(), r.stderr.strip(), r.returncode
        except subprocess.TimeoutExpired:
            return "", f"[ADB TIMEOUT] {' '.join(cmd)}", 1
        except FileNotFoundError:
            return "", "[ADB] adb no está instalado. Ejecuta: sudo apt install adb", 1

    def _shell(self, *args: str, timeout: int = 30) -> str:
        """Ejecuta `adb shell <comando>` y devuelve stdout."""
        out, err, code = self._adb("shell", *args, timeout=timeout)
        if code != 0 and err:
            raise ADBError(f"adb shell error: {err}")
        return out

    # ── Conexión y detección ─────────────────────────────────────────────────

    def list_devices(self) -> list[dict[str, str]]:
        """Devuelve lista de dispositivos conectados {serial, state}."""
        out, _, _ = self._adb("devices")
        devices = []
        for line in out.splitlines()[1:]:  # pyre-ignore[arg-type]
            parts = line.split("\t")
            if len(parts) == 2:
                devices.append({"serial": parts[0].strip(), "state": parts[1].strip()})  # pyre-ignore[arg-type]
        return devices

    def connect(self, wifi_address: str = "") -> bool:
        """
        Conecta al dispositivo.
        - Si wifi_address="host:port" → conecta por WiFi
        - Si serial vacío → usa el primer dispositivo USB
        Returns True si hay dispositivo disponible.
        """
        if wifi_address:
            out, err, code = self._adb("connect", wifi_address)
            if "connected" in out or code == 0:
                self._serial = wifi_address
                self._connected = True
                self._refresh_device_info()
                print(f"[📱 ADB] Conectado WiFi: {wifi_address}")
                return True
            print(f"[📱 ADB] Error WiFi: {err or out}")
            return False

        devices = self.list_devices()
        available = [d for d in devices if d["state"] == "device"]
        if not available:
            print("[📱 ADB] No hay dispositivos Android conectados")
            self._connected = False
            return False

        if not self._serial:
            self._serial = available[0]["serial"]  # pyre-ignore[arg-type]
        self._connected = True
        self._refresh_device_info()
        print(f"[📱 ADB] Conectado USB: {self._serial} ({self._device.model})")
        return True

    def _refresh_device_info(self) -> None:
        """Actualiza la caché de info del dispositivo."""
        try:
            model   = self._shell("getprop", "ro.product.model")
            android = self._shell("getprop", "ro.build.version.release")
            sdk     = self._shell("getprop", "ro.build.version.sdk")
            battery = self._shell("dumpsys", "battery")
            wifi    = self._shell("ip", "route", "get", "1.1.1.1")
            size    = self._shell("wm", "size")

            # Batería: extraer nivel
            battery_level = -1
            for line in battery.splitlines():
                if "level:" in line:
                    battery_level = int(line.split(":")[1].strip())  # pyre-ignore[arg-type]
                    break

            # IP WiFi
            wifi_ip = ""
            for line in wifi.splitlines():
                if "src" in line:
                    parts = line.split()
                    idx = parts.index("src") if "src" in parts else -1
                    if idx >= 0 and idx + 1 < len(parts):
                        wifi_ip = parts[idx + 1]

            # Resolución
            w, h = 0, 0
            if "Physical size:" in size:
                res_part = size.split("Physical size:")[1].strip().split()[0]  # pyre-ignore[arg-type]
                dims     = res_part.split("x")
                if len(dims) == 2:
                    w, h = int(dims[0]), int(dims[1])  # pyre-ignore[arg-type]

            devices = self.list_devices()
            state   = next((d["state"] for d in devices if d["serial"] == self._serial), "unknown")

            self._device = DeviceInfo(
                serial         = self._serial,
                model          = model,
                android_version= android,
                sdk_version    = sdk,
                battery_level  = battery_level,
                screen_width   = w,
                screen_height  = h,
                wifi_ip        = wifi_ip,
                state          = state,
            )
        except Exception as e:
            print(f"[📱 ADB] Warn: No se pudo actualizar info del dispositivo: {e}")

    def get_device_info(self) -> dict[str, Any]:
        """Devuelve información completa del dispositivo como dict."""
        if not self._connected:
            if not self.connect():
                return {"error": "No hay dispositivo conectado", "connected": False}
        self._refresh_device_info()
        d = self._device
        return {
            "connected":       True,
            "serial":          d.serial,
            "model":           d.model,
            "android_version": d.android_version,
            "sdk_version":     d.sdk_version,
            "battery_level":   d.battery_level,
            "resolution":      f"{d.screen_width}x{d.screen_height}",
            "wifi_ip":         d.wifi_ip,
            "state":           d.state,
        }

    # ── Reconexión persistente ───────────────────────────────────────────────

    def start_watchdog(self) -> None:
        """Inicia un hilo daemon que mantiene la conexión ADB activa."""
        if self._watchdog and self._watchdog.is_alive():
            return

        def _watch():
            while True:
                time.sleep(RECONNECT_DELAY)
                devs = self.list_devices()
                available = [d for d in devs if d["state"] == "device"]
                if available and not self._connected:
                    print("[📱 ADB] Reconexión automática...")
                    self.connect()
                elif not available and self._connected:
                    print("[📱 ADB] Dispositivo desconectado")
                    self._connected = False

        self._watchdog = threading.Thread(target=_watch, daemon=True, name="eidos_adb_watchdog")
        self._watchdog.start()
        print("[📱 ADB] Watchdog de reconexión activo")

    # ── Screenshot ──────────────────────────────────────────────────────────

    def take_screenshot(self) -> str:
        """
        Captura la pantalla del móvil.
        Returns: ruta al archivo PNG local, o "" si falla.
        """
        if not self._connected:
            if not self.connect():
                return ""
        timestamp  = int(time.time())
        local_path = os.path.join(SCREENSHOT_DIR, f"adb_{timestamp}.png")
        try:
            # screencap -p devuelve PNG raw por stdout
            cmd  = [ADB_CMD]
            if self._serial:
                cmd += ["-s", self._serial]
            cmd += ["exec-out", "screencap", "-p"]
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode == 0 and result.stdout:
                with open(local_path, "wb") as f:
                    f.write(result.stdout)
                return local_path
        except Exception as e:
            print(f"[📱 ADB] Screenshot error: {e}")
        return ""

    def screenshot_as_base64(self) -> str:
        """Screenshot del móvil en base64 (para enviar a VLM)."""
        path = self.take_screenshot()
        if not path:
            return ""
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
        except Exception:
            return ""

    # ── Interacción táctil ──────────────────────────────────────────────────

    def tap(self, x: int, y: int) -> bool:
        """Tap en coordenadas (x, y)."""
        try:
            self._shell("input", "tap", str(x), str(y))
            return True
        except ADBError as e:
            print(f"[📱 ADB] tap error: {e}")
            return False

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> bool:
        """Swipe desde (x1,y1) hasta (x2,y2) en duration_ms milisegundos."""
        try:
            self._shell("input", "swipe",
                        str(x1), str(y1), str(x2), str(y2), str(duration_ms))
            return True
        except ADBError as e:
            print(f"[📱 ADB] swipe error: {e}")
            return False

    def long_press(self, x: int, y: int, duration_ms: int = 1000) -> bool:
        """Long press en (x, y)."""
        return self.swipe(x, y, x, y, duration_ms)

    def type_text(self, text: str) -> bool:
        """Escribe texto en el campo de texto activo (URL-encoding de caracteres especiales)."""
        try:
            # Escapar espacios y caracteres especiales para adb shell
            escaped = text.replace(" ", "%s").replace("'", "\\'").replace("\"", "\\\"")
            self._shell("input", "text", escaped)
            return True
        except ADBError as e:
            print(f"[📱 ADB] type_text error: {e}")
            return False

    def press_key(self, keycode: str | int) -> bool:
        """
        Pulsa una tecla por nombre o keycode.
        Ejemplos: "BACK", "HOME", "ENTER", "VOLUME_UP", 4, 3, 66
        """
        try:
            self._shell("input", "keyevent", str(keycode))
            return True
        except ADBError as e:
            print(f"[📱 ADB] press_key error: {e}")
            return False

    def scroll_down(self, steps: int = 3) -> bool:
        """Scroll hacia abajo (simula swipe)."""
        h = self._device.screen_height or 1920
        w = self._device.screen_width  or 1080
        cx = w // 2
        return self.swipe(cx, int(h * 0.7), cx, int(h * 0.3), 400)

    def scroll_up(self, steps: int = 3) -> bool:
        """Scroll hacia arriba."""
        h = self._device.screen_height or 1920
        w = self._device.screen_width  or 1080
        cx = w // 2
        return self.swipe(cx, int(h * 0.3), cx, int(h * 0.7), 400)

    # ── Apps ─────────────────────────────────────────────────────────────────

    def list_packages(self, only_third_party: bool = True) -> list[str]:
        """Lista paquetes instalados."""
        args = ["pm", "list", "packages"]
        if only_third_party:
            args.append("-3")
        try:
            out = self._shell(*args)
            return [line.replace("package:", "").strip() for line in out.splitlines() if line.strip()]
        except ADBError:
            return []

    def start_app(self, package: str, activity: str = "") -> bool:
        """Lanza una app por nombre de paquete."""
        try:
            if activity:
                self._shell("am", "start", "-n", f"{package}/{activity}")
            else:
                self._shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
            return True
        except ADBError as e:
            print(f"[📱 ADB] start_app error: {e}")
            return False

    def stop_app(self, package: str) -> bool:
        """Fuerza el cierre de una app."""
        try:
            self._shell("am", "force-stop", package)
            return True
        except ADBError:
            return False

    def install_apk(self, local_path: str) -> bool:
        """Instala un APK en el dispositivo."""
        if not os.path.exists(local_path):
            print(f"[📱 ADB] APK no encontrado: {local_path}")
            return False
        _, err, code = self._adb("install", "-r", local_path, timeout=120)
        if code != 0:
            print(f"[📱 ADB] install error: {err}")
            return False
        return True

    def uninstall_app(self, package: str) -> bool:
        """Desinstala una app por nombre de paquete."""
        _, err, code = self._adb("uninstall", package)
        return code == 0

    # ── Archivos ─────────────────────────────────────────────────────────────

    def push_file(self, local_path: str, remote_path: str) -> bool:
        """Sube un archivo al dispositivo."""
        _, err, code = self._adb("push", local_path, remote_path, timeout=120)
        if code != 0:
            print(f"[📱 ADB] push error: {err}")
            return False
        return True

    def pull_file(self, remote_path: str, local_path: str) -> bool:
        """Baja un archivo del dispositivo."""
        _, err, code = self._adb("pull", remote_path, local_path, timeout=120)
        if code != 0:
            print(f"[📱 ADB] pull error: {err}")
            return False
        return True

    # ── System info ──────────────────────────────────────────────────────────

    def get_running_apps(self) -> list[str]:
        """Lista de procesos en primer plano."""
        try:
            out = self._shell("dumpsys", "activity", "recents", "|", "grep", "packageName")
            packages = []
            for line in out.splitlines():
                if "packageName=" in line:
                    pkg = line.split("packageName=")[-1].split()[0].rstrip("}")  # pyre-ignore[arg-type]
                    if pkg not in packages:
                        packages.append(pkg)
            return packages
        except ADBError:
            return []

    def get_current_app(self) -> str:
        """Devuelve el paquete de la app actualmente en primer plano."""
        try:
            out = self._shell("dumpsys", "window", "windows", "|",
                              "grep", "-E", "mCurrentFocus|mFocusedApp")
            for line in out.splitlines():
                if "mCurrentFocus" in line and "/" in line:
                    return line.split("/")[0].split()[-1]  # pyre-ignore[arg-type]
            return ""
        except ADBError:
            return ""

    def exec_command(self, command: str) -> str:
        """Ejecuta un comando shell arbitrario en el dispositivo."""
        try:
            return self._shell(command)
        except ADBError as e:
            return f"[ADB ERROR] {e}"


# ── Singleton global ─────────────────────────────────────────────────────────
_adb: Optional[ADBBridge] = None


def get_adb(auto_connect: bool = True, watchdog: bool = True) -> ADBBridge:
    """
    Devuelve la instancia global de ADBBridge, conectando si es necesario.
    """
    global _adb
    if _adb is None:
        _adb = ADBBridge()
    if auto_connect and not _adb._connected:
        _adb.connect()
    if watchdog and _adb._connected:
        _adb.start_watchdog()
    return _adb


# ── CLI rápido ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    adb = get_adb()
    info = adb.get_device_info()
    if info.get("connected"):
        print(f"📱 Dispositivo: {info['model']} (Android {info['android_version']})")
        print(f"   Batería: {info['battery_level']}% | Resolución: {info['resolution']}")
        print(f"   WiFi IP: {info['wifi_ip'] or 'N/A'}")
        print(f"\n📸 Tomando screenshot...")
        path = adb.take_screenshot()
        print(f"   Guardado en: {path}")
    else:
        print("❌ No hay dispositivo Android conectado")
        print("   1. Activa 'Depuración USB' en Configuración → Opciones de desarrollador")
        print("   2. Conecta el móvil por USB")
        print("   3. Acepta la autorización ADB en el móvil")
        print("   4. Vuelve a ejecutar este script")
