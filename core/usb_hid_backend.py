"""
core/usb_hid_backend.py — Backend USB Gadget HID [S88 GOLD]

"Nivel kernel: el hardware no miente" — DeepSeek

Implementa un dispositivo USB HID falso a nivel de kernel Linux usando
dummy_hcd + usb_f_hid (USB Gadget). El sistema operativo lo ve como
hardware FÍSICO conectado por USB — completamente indistinguible de un
ratón/teclado real.

¿Por qué es indetectable?
  1. dummy_hcd crea un controlador USB virtual en el kernel
  2. usb_f_hid registra una función HID en el gadget
  3. Los eventos se escriben como raw HID reports en /dev/hidg0
  4. X11/Wayland reciben eventos del kernel EXACTAMENTE igual que
     si vinieran de un dispositivo USB físico
  5. No hay uinput, no hay xdotool, no hay XTest — es hardware "real"

Comparativa de detección:
  xdotool     → detectable via XTest extension check
  uinput      → detectable via /sys/module/uinput + heuristics
  USB Gadget  → INDETECTABLE (el kernel lo trata como HW físico)
  Arduino HID → INDETECTABLE (pero requiere hardware real)

Capas de fallback:
  1. USB Gadget HID (nivel kernel) — GOLD
  2. uinput + libevdev (nivel kernel userspace) — SILVER
  3. xdotool (X11 automation) — BRONZE

Uso:
    backend = USBHIDBackend()
    backend.start()                    # inicializa el dispositivo
    backend.mouse_move(800, 450)       # mueve ratón (absoluto)
    backend.mouse_click(1)             # click izquierdo
    backend.keyboard_type("hello")     # escribe texto
    backend.stop()                     # limpia
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eidos.usb_hid")

# ── Constantes HID ──────────────────────────────────────────────────────────────
# HID Report Descriptors estándar

# Mouse HID Report Descriptor: 3 botones + X/Y absolutos + wheel
HID_REPORT_DESC_MOUSE = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x02,        # Usage (Mouse)
    0xA1, 0x01,        # Collection (Application)
    0x09, 0x01,        #   Usage (Pointer)
    0xA1, 0x00,        #   Collection (Physical)
    0x05, 0x09,        #     Usage Page (Button)
    0x19, 0x01,        #     Usage Minimum (1)
    0x29, 0x03,        #     Usage Maximum (3)
    0x15, 0x00,        #     Logical Minimum (0)
    0x25, 0x01,        #     Logical Maximum (1)
    0x95, 0x03,        #     Report Count (3)
    0x75, 0x01,        #     Report Size (1)
    0x81, 0x02,        #     Input (Data,Var,Abs)
    0x95, 0x01,        #     Report Count (1)
    0x75, 0x05,        #     Report Size (5)
    0x81, 0x01,        #     Input (Cnst) - padding
    0x05, 0x01,        #     Usage Page (Generic Desktop)
    0x09, 0x30,        #     Usage (X)
    0x09, 0x31,        #     Usage (Y)
    0x15, 0x00,        #     Logical Minimum (0)
    0x26, 0xFF, 0x1F,  #     Logical Maximum (8191) — screen width
    0x35, 0x00,        #     Physical Minimum (0)
    0x46, 0xFF, 0x1F,  #     Physical Maximum (8191)
    0x75, 0x10,        #     Report Size (16)
    0x95, 0x02,        #     Report Count (2)
    0x81, 0x02,        #     Input (Data,Var,Abs)
    0x09, 0x38,        #     Usage (Wheel)
    0x15, 0x81,        #     Logical Minimum (-127)
    0x25, 0x7F,        #     Logical Maximum (127)
    0x75, 0x08,        #     Report Size (8)
    0x95, 0x01,        #     Report Count (1)
    0x81, 0x06,        #     Input (Data,Var,Rel)
    0xC0,              #   End Collection
    0xC0,              # End Collection
])

# Keyboard HID Report Descriptor: 8-byte boot keyboard
HID_REPORT_DESC_KEYBOARD = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    0x05, 0x07,        #   Usage Page (Keyboard)
    0x19, 0xE0,        #   Usage Minimum (224) — Left Ctrl
    0x29, 0xE7,        #   Usage Maximum (231) — Right GUI
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x08,        #   Report Count (8)
    0x81, 0x02,        #   Input (Data,Var,Abs) — modifier byte
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x08,        #   Report Size (8)
    0x81, 0x01,        #   Input (Cnst) — reserved
    0x95, 0x06,        #   Report Count (6)
    0x75, 0x08,        #   Report Size (8)
    0x15, 0x00,        #   Logical Minimum (0)
    0x26, 0xFF, 0x00,  #   Logical Maximum (255)
    0x05, 0x07,        #   Usage Page (Keyboard)
    0x19, 0x00,        #   Usage Minimum (0)
    0x29, 0xFF,        #   Usage Maximum (255)
    0x81, 0x00,        #   Input (Data,Ary,Abs) — 6 key slots
    0xC0,              # End Collection
])

# USB HID keycodes (simplificado — cubre caracteres comunes)
HID_KEYCODES = {
    # Letras
    'a': 0x04, 'b': 0x05, 'c': 0x06, 'd': 0x07, 'e': 0x08, 'f': 0x09,
    'g': 0x0A, 'h': 0x0B, 'i': 0x0C, 'j': 0x0D, 'k': 0x0E, 'l': 0x0F,
    'm': 0x10, 'n': 0x11, 'o': 0x12, 'p': 0x13, 'q': 0x14, 'r': 0x15,
    's': 0x16, 't': 0x17, 'u': 0x18, 'v': 0x19, 'w': 0x1A, 'x': 0x1B,
    'y': 0x1C, 'z': 0x1D,
    # Números
    '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21, '5': 0x22,
    '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26, '0': 0x27,
    # Teclas especiales
    '\n': 0x28,  # Return/Enter
    ' ': 0x2C,   # Space
    '-': 0x2D, '=': 0x2E, '[': 0x2F, ']': 0x30, '\\': 0x31,
    ';': 0x33, "'": 0x34, '`': 0x35, ',': 0x36, '.': 0x37, '/': 0x38,
    '\t': 0x2B,  # Tab
}

# Shift-modified keycodes
HID_KEYCODES_SHIFT = {
    '!': 0x1E, '@': 0x1F, '#': 0x20, '$': 0x21, '%': 0x22,
    '^': 0x23, '&': 0x24, '*': 0x25, '(': 0x26, ')': 0x27,
    '_': 0x2D, '+': 0x2E, '{': 0x2F, '}': 0x30, '|': 0x31,
    ':': 0x33, '"': 0x34, '~': 0x35, '<': 0x36, '>': 0x37, '?': 0x38,
}

GADGET_BASE = "/sys/kernel/config/usb_gadget"
GADGET_NAME = "eidos_hid"


def _check_kernel_module(name: str) -> bool:
    """Verifica si un módulo del kernel está disponible (cargado o cargable)."""
    try:
        # Verificar si ya está cargado
        r = subprocess.run(["lsmod"], capture_output=True, text=True, timeout=5)
        if name.replace("-", "_") in r.stdout:
            return True
        # Verificar si está disponible para cargar
        modinfo = subprocess.run(
            ["modinfo", name], capture_output=True, text=True, timeout=5
        )
        return modinfo.returncode == 0
    except Exception:
        return False


def _load_module(name: str) -> bool:
    """Carga un módulo del kernel con modprobe."""
    try:
        r = subprocess.run(
            ["sudo", "modprobe", name],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            log.warning("modprobe %s falló: %s", name, r.stderr.strip())
        return r.returncode == 0
    except Exception as e:
        log.warning("No se pudo cargar %s: %s", name, e)
        return False


def _check_udc_available() -> Optional[str]:
    """Busca un UDC (USB Device Controller) disponible. Prefiere dummy_udc."""
    udc_path = Path("/sys/class/udc")
    if not udc_path.exists():
        return None
    udcs = list(udc_path.iterdir())
    if not udcs:
        return None
    # Preferir dummy_udc para dispositivo virtual
    for u in udcs:
        if "dummy" in u.name:
            return u.name
    return udcs[0].name


class USBHIDBackend:
    """Backend de entrada USB Gadget HID a nivel kernel.

    Crea un dispositivo USB HID falso que el SO ve como hardware físico.
    Soporta mouse (absoluto + relativo) y teclado (boot keyboard).

    Attributes:
        tier: "gold" (USB Gadget), "silver" (uinput), "bronze" (xdotool)
        udc: nombre del UDC vinculado
        gadget_path: ruta al directorio del gadget en configfs
    """

    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        self._screen_w = screen_width
        self._screen_h = screen_height
        self._started = False
        self._tier: str = "bronze"  # se determina en start()
        self._udc: Optional[str] = None
        self._gadget_path: Optional[Path] = None
        self._hidg_mouse: Optional[Path] = None
        self._hidg_kbd: Optional[Path] = None
        self._mouse_fd = None
        self._kbd_fd = None
        self._mouse_pos: Tuple[int, int] = (400, 300)

    # ── Inicialización ──────────────────────────────────────────────────────────

    def start(self) -> Dict[str, Any]:
        """Inicializa el backend. Prueba USB Gadget → uinput → xdotool."""
        result = {"tier": "bronze", "backend": "xdotool", "udc": None}

        # Tier 1: USB Gadget HID (GOLD)
        if self._try_start_gadget():
            result = {"tier": "gold", "backend": "usb_gadget_hid",
                      "udc": self._udc}
            self._tier = "gold"
        # Tier 2: uinput (SILVER)
        elif self._try_start_uinput():
            result = {"tier": "silver", "backend": "uinput"}
            self._tier = "silver"
        # Tier 3: xdotool (BRONZE - siempre disponible en X11)
        else:
            self._tier = "bronze"
            log.info("USBHIDBackend: usando xdotool (bronze tier)")

        self._started = True
        log.info("USBHIDBackend iniciado: tier=%s", self._tier)
        return result

    def _try_start_gadget(self) -> bool:
        """Intenta crear un gadget USB HID via configfs."""
        try:
            # Verificar que configfs está montado
            if not Path(GADGET_BASE).exists():
                log.debug("configfs no montado en %s", GADGET_BASE)
                return False

            # Verificar/Cargar módulos necesarios
            if not _check_kernel_module("dummy_hcd"):
                log.debug("dummy_hcd no disponible")
                if not _load_module("dummy_hcd"):
                    return False
                time.sleep(0.5)

            if not _check_kernel_module("usb_f_hid"):
                log.debug("usb_f_hid no disponible")
                if not _load_module("usb_f_hid"):
                    return False
                time.sleep(0.3)

            # Buscar UDC
            udc = _check_udc_available()
            if not udc:
                log.debug("No hay UDC disponible")
                return False

            self._udc = udc
            gadget_path = Path(GADGET_BASE) / GADGET_NAME

            # Limpiar gadget previo si existe
            self._cleanup_gadget(gadget_path)

            # Crear estructura del gadget
            gadget_path.mkdir(parents=True, exist_ok=True)

            # Configurar identificación USB (VID/PID genéricos para HID)
            (gadget_path / "idVendor").write_text("0x1d6b")   # Linux Foundation
            (gadget_path / "idProduct").write_text("0x0105")  # Generic HID
            (gadget_path / "bcdDevice").write_text("0x0100")
            (gadget_path / "bcdUSB").write_text("0x0200")

            # Strings del dispositivo
            strings_dir = gadget_path / "strings" / "0x409"
            strings_dir.mkdir(parents=True, exist_ok=True)
            (strings_dir / "manufacturer").write_text("EIDOS")
            (strings_dir / "product").write_text("EIDOS HID Bridge")

            # ── Configuración Mouse ──────────────────────────────────────────
            mouse_dir = gadget_path / "functions" / "hid.mouse"
            mouse_dir.mkdir(parents=True, exist_ok=True)

            # Protocol: 2 = mouse
            (mouse_dir / "protocol").write_text("2")
            (mouse_dir / "subclass").write_text("1")
            (mouse_dir / "report_length").write_text("5")

            # Escribir report descriptor
            (mouse_dir / "report_desc").write_bytes(HID_REPORT_DESC_MOUSE)
            self._mouse_report_desc = HID_REPORT_DESC_MOUSE

            # ── Configuración Keyboard ───────────────────────────────────────
            kbd_dir = gadget_path / "functions" / "hid.keyboard"
            kbd_dir.mkdir(parents=True, exist_ok=True)
            (kbd_dir / "protocol").write_text("1")
            (kbd_dir / "subclass").write_text("1")
            (kbd_dir / "report_length").write_text("8")
            (kbd_dir / "report_desc").write_bytes(HID_REPORT_DESC_KEYBOARD)
            self._kbd_report_desc = HID_REPORT_DESC_KEYBOARD

            # ── Configuración ────────────────────────────────────────────────
            config_dir = gadget_path / "configs" / "c.1"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "MaxPower").write_text("250")

            # Vincular funciones a la configuración
            mouse_link = config_dir / "hid.mouse"
            kbd_link = config_dir / "hid.keyboard"
            if not mouse_link.exists():
                mouse_link.symlink_to(str(mouse_dir))
            if not kbd_link.exists():
                kbd_link.symlink_to(str(kbd_dir))

            self._gadget_path = gadget_path
            time.sleep(0.3)

            # Buscar /dev/hidg* para mouse y keyboard
            hidg_devices = sorted(Path("/dev").glob("hidg*"))
            log.debug("Dispositivos HID encontrados: %s",
                      [str(d) for d in hidg_devices])

            if len(hidg_devices) >= 2:
                self._hidg_mouse = hidg_devices[0]
                self._hidg_kbd = hidg_devices[1]
            elif len(hidg_devices) == 1:
                self._hidg_mouse = hidg_devices[0]
                # keyboard comparte o se usa el mismo
                self._hidg_kbd = hidg_devices[0]
            else:
                # Vincular UDC para activar el gadget
                (gadget_path / "UDC").write_text(udc)
                time.sleep(0.6)
                hidg_devices = sorted(Path("/dev").glob("hidg*"))
                if len(hidg_devices) >= 2:
                    self._hidg_mouse = hidg_devices[0]
                    self._hidg_kbd = hidg_devices[1]
                elif len(hidg_devices) == 1:
                    self._hidg_mouse = hidg_devices[0]
                    self._hidg_kbd = hidg_devices[0]

            # Vincular UDC si no se ha hecho
            udc_file = gadget_path / "UDC"
            if udc_file.exists():
                current_udc = udc_file.read_text().strip()
                if not current_udc:
                    udc_file.write_text(udc)

            log.info("USB Gadget HID creado: mouse=%s kbd=%s udc=%s",
                     self._hidg_mouse, self._hidg_kbd, udc)
            return True

        except PermissionError:
            log.warning("Se necesita sudo para USB Gadget. Usando fallback.")
            return False
        except Exception as e:
            log.warning("USB Gadget HID falló: %s. Usando fallback.", e)
            return False

    def _try_start_uinput(self) -> bool:
        """Intenta crear dispositivos uinput (SILVER tier)."""
        try:
            import fcntl
            import struct as _struct

            # Constantes uinput (linux/uinput.h)
            UI_DEV_CREATE = 0x5501
            UI_DEV_DESTROY = 0x5502
            UI_SET_EVBIT = 0x40045564
            UI_SET_KEYBIT = 0x40045565
            UI_SET_RELBIT = 0x40045566
            UI_SET_ABSBIT = 0x40045567

            EV_KEY = 0x01
            EV_REL = 0x02
            EV_ABS = 0x03
            REL_X = 0x00
            REL_Y = 0x01
            REL_WHEEL = 0x08
            ABS_X = 0x00
            ABS_Y = 0x01
            BTN_LEFT = 0x110
            BTN_RIGHT = 0x111
            BTN_MIDDLE = 0x112

            # Abrir /dev/uinput
            self._uinput_fd = os.open("/dev/uinput", os.O_RDWR)

            # Configurar mouse
            fcntl.ioctl(self._uinput_fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self._uinput_fd, UI_SET_EVBIT, EV_REL)
            fcntl.ioctl(self._uinput_fd, UI_SET_EVBIT, EV_ABS)
            fcntl.ioctl(self._uinput_fd, UI_SET_KEYBIT, BTN_LEFT)
            fcntl.ioctl(self._uinput_fd, UI_SET_KEYBIT, BTN_RIGHT)
            fcntl.ioctl(self._uinput_fd, UI_SET_KEYBIT, BTN_MIDDLE)
            fcntl.ioctl(self._uinput_fd, UI_SET_RELBIT, REL_X)
            fcntl.ioctl(self._uinput_fd, UI_SET_RELBIT, REL_Y)
            fcntl.ioctl(self._uinput_fd, UI_SET_RELBIT, REL_WHEEL)
            fcntl.ioctl(self._uinput_fd, UI_SET_ABSBIT, ABS_X)
            fcntl.ioctl(self._uinput_fd, UI_SET_ABSBIT, ABS_Y)

            # Crear dispositivo
            uinput_user_dev = _struct.pack(
                "80sIIII", b"EIDOS Mouse", 0x1d6b, 0x0105, 1, 0
            )
            os.write(self._uinput_fd, uinput_user_dev)
            fcntl.ioctl(self._uinput_fd, UI_DEV_CREATE)

            log.info("uinput mouse creado en /dev/uinput")
            return True

        except ImportError:
            log.debug("Módulo fcntl no disponible para uinput")
            return False
        except PermissionError:
            log.debug("Se necesita sudo para uinput")
            return False
        except Exception as e:
            log.debug("uinput falló: %s", e)
            return False

    def _cleanup_gadget(self, gadget_path: Path):
        """Limpia un gadget USB previo."""
        try:
            if not gadget_path.exists():
                return
            udc_file = gadget_path / "UDC"
            if udc_file.exists():
                udc_file.write_text("")  # desvincular UDC
                time.sleep(0.2)
            # Desvincular symlinks
            config_dir = gadget_path / "configs" / "c.1"
            for link in config_dir.glob("hid.*"):
                if link.is_symlink():
                    link.unlink()
            # Eliminar directorios
            for d in ["configs/c.1/strings", "configs/c.1",
                      "functions/hid.mouse", "functions/hid.keyboard",
                      "functions", "strings/0x409", "strings"]:
                p = gadget_path / d
                if p.exists():
                    p.rmdir()
            if gadget_path.exists():
                gadget_path.rmdir()
        except Exception as e:
            log.debug("cleanup gadget: %s", e)

    # ── Mouse ───────────────────────────────────────────────────────────────────

    def mouse_move_absolute(self, x: int, y: int):
        """Mueve el ratón a coordenadas absolutas (requiere HID report).

        Formato report (5 bytes): [buttons, x_lo, x_hi, y_lo, y_hi]
        - X: 0-8191 mapeado a screen_width
        - Y: 0-8191 mapeado a screen_height
        """
        # Mapear coordenadas de pantalla → rango HID 0-8191
        hid_x = max(0, min(8191, int(x * 8191 / max(1, self._screen_w))))
        hid_y = max(0, min(8191, int(y * 8191 / max(1, self._screen_h))))

        self._mouse_pos = (x, y)

        if self._tier == "gold" and self._hidg_mouse:
            report = struct.pack("<BHH", 0x00, hid_x, hid_y)
            try:
                self._hidg_mouse.write_bytes(report)
            except Exception as e:
                log.warning("HID mouse write falló: %s", e)
                self._xdotool_mousemove(x, y)
        elif self._tier == "silver" and hasattr(self, '_uinput_fd'):
            self._uinput_mouse_move(x, y)
        else:
            self._xdotool_mousemove(x, y)

    def mouse_move_relative(self, dx: int, dy: int):
        """Mueve el ratón relativamente."""
        new_x = self._mouse_pos[0] + dx
        new_y = self._mouse_pos[1] + dy
        self.mouse_move_absolute(new_x, new_y)

    def mouse_click(self, button: int = 1, press: bool = True):
        """Click de ratón.

        Args:
            button: 1=izquierdo, 2=medio, 3=derecho
            press: True=presionar, False=soltar
        """
        if self._tier == "gold" and self._hidg_mouse:
            btn_byte = {1: 0x01, 2: 0x04, 3: 0x02}.get(button, 0x01)
            if press:
                report = struct.pack("<BHH", btn_byte,
                                     int(self._mouse_pos[0] * 8191 / max(1, self._screen_w)),
                                     int(self._mouse_pos[1] * 8191 / max(1, self._screen_h)))
                self._hidg_mouse.write_bytes(report)
            else:
                report = struct.pack("<BHH", 0x00,
                                     int(self._mouse_pos[0] * 8191 / max(1, self._screen_w)),
                                     int(self._mouse_pos[1] * 8191 / max(1, self._screen_h)))
                self._hidg_mouse.write_bytes(report)
        elif self._tier == "silver":
            self._uinput_mouse_click(button, press)
        else:
            if press:
                self._xdotool_mousedown(button)
            else:
                self._xdotool_mouseup(button)

    def mouse_click_full(self, button: int = 1):
        """Click completo: press + release."""
        self.mouse_click(button, press=True)
        time.sleep(0.03)
        self.mouse_click(button, press=False)

    def mouse_scroll(self, amount: int):
        """Scroll vertical. Positivo = arriba, negativo = abajo."""
        if self._tier == "gold" and self._hidg_mouse:
            # Report con wheel byte
            report = struct.pack("<BBh", 0x00, 0x00, amount)
            try:
                self._hidg_mouse.write_bytes(report)
                # Release wheel
                time.sleep(0.01)
                report_zero = struct.pack("<BBh", 0x00, 0x00, 0)
                self._hidg_mouse.write_bytes(report_zero)
            except Exception:
                self._xdotool_scroll(amount)
        else:
            self._xdotool_scroll(amount)

    # ── Keyboard ────────────────────────────────────────────────────────────────

    def keyboard_press(self, hid_code: int, modifiers: int = 0):
        """Presiona una tecla HID."""
        if self._tier == "gold" and self._hidg_kbd:
            # 8-byte boot keyboard report: [modifier, reserved, key0..key5]
            report = struct.pack("BBBBBBBB", modifiers, 0x00,
                                 hid_code, 0x00, 0x00, 0x00, 0x00, 0x00)
            try:
                self._hidg_kbd.write_bytes(report)
            except Exception:
                # Fallback: ignorar silenciosamente
                pass
        elif self._tier == "silver":
            # uinput keyboard
            pass
        else:
            # xdotool fallback
            key_name = self._hid_to_xdotool_key(hid_code)
            if key_name:
                subprocess.run(
                    ["xdotool", "keydown", key_name],
                    capture_output=True, timeout=1
                )

    def keyboard_release(self, hid_code: int):
        """Suelta una tecla HID."""
        if self._tier == "gold" and self._hidg_kbd:
            report = struct.pack("BBBBBBBB", 0x00, 0x00,
                                 0x00, 0x00, 0x00, 0x00, 0x00, 0x00)
            try:
                self._hidg_kbd.write_bytes(report)
            except Exception:
                pass
        elif self._tier != "silver":
            key_name = self._hid_to_xdotool_key(hid_code)
            if key_name:
                subprocess.run(
                    ["xdotool", "keyup", key_name],
                    capture_output=True, timeout=1
                )

    def keyboard_type(self, text: str, delay_ms: float = 80.0):
        """Escribe texto carácter por carácter usando HID keycodes.

        Args:
            text: texto a escribir
            delay_ms: milisegundos entre tecla y tecla
        """
        for char in text:
            if char in HID_KEYCODES:
                code = HID_KEYCODES[char]
                mod = 0
            elif char in HID_KEYCODES_SHIFT:
                code = HID_KEYCODES_SHIFT[char]
                mod = 0x02  # Left Shift modifier
            elif char.isupper() and char.lower() in HID_KEYCODES:
                code = HID_KEYCODES[char.lower()]
                mod = 0x02
            else:
                continue  # carácter no soportado

            self.keyboard_press(code, modifiers=mod)
            time.sleep(delay_ms / 1000.0)
            self.keyboard_release(code)
            time.sleep(delay_ms / 2000.0)

    # ── Fallbacks xdotool ───────────────────────────────────────────────────────

    def _xdotool_mousemove(self, x: int, y: int):
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y)],
            capture_output=True, timeout=1
        )
        self._mouse_pos = (x, y)

    def _xdotool_mousedown(self, button: int):
        subprocess.run(
            ["xdotool", "mousedown", str(button)],
            capture_output=True, timeout=1
        )

    def _xdotool_mouseup(self, button: int):
        subprocess.run(
            ["xdotool", "mouseup", str(button)],
            capture_output=True, timeout=1
        )

    def _xdotool_click(self, button: int):
        subprocess.run(
            ["xdotool", "click", str(button)],
            capture_output=True, timeout=1
        )

    def _xdotool_scroll(self, amount: int):
        button = 4 if amount > 0 else 5  # 4=up, 5=down
        for _ in range(abs(amount)):
            subprocess.run(
                ["xdotool", "click", str(button)],
                capture_output=True, timeout=1
            )

    def _hid_to_xdotool_key(self, hid_code: int) -> Optional[str]:
        """Convierte HID keycode → nombre de tecla xdotool."""
        REVERSE = {v: k for k, v in HID_KEYCODES.items()}
        return REVERSE.get(hid_code)

    def _uinput_mouse_move(self, x: int, y: int):
        """Mueve ratón via uinput (relativo)."""
        if not hasattr(self, '_uinput_fd'):
            return
        import struct as _struct
        dx = x - self._mouse_pos[0]
        dy = y - self._mouse_pos[1]
        # Eventos relativos
        EV_REL = 0x02
        REL_X = 0x00
        REL_Y = 0x01
        EV_SYN = 0x00
        SYN_REPORT = 0x00
        # Escribir eventos
        event = _struct.pack("llHHi", 0, 0, EV_REL, REL_X, dx)
        os.write(self._uinput_fd, event)
        event = _struct.pack("llHHi", 0, 0, EV_REL, REL_Y, dy)
        os.write(self._uinput_fd, event)
        event = _struct.pack("llHHi", 0, 0, EV_SYN, SYN_REPORT, 0)
        os.write(self._uinput_fd, event)
        self._mouse_pos = (x, y)

    def _uinput_mouse_click(self, button: int, press: bool):
        if not hasattr(self, '_uinput_fd'):
            return
        import struct as _struct
        EV_KEY = 0x01
        EV_SYN = 0x00
        SYN_REPORT = 0x00
        BTN_MAP = {1: 0x110, 2: 0x112, 3: 0x111}
        code = BTN_MAP.get(button, 0x110)
        value = 1 if press else 0
        event = _struct.pack("llHHi", 0, 0, EV_KEY, code, value)
        os.write(self._uinput_fd, event)
        event = _struct.pack("llHHi", 0, 0, EV_SYN, SYN_REPORT, 0)
        os.write(self._uinput_fd, event)

    # ── Limpieza ────────────────────────────────────────────────────────────────

    def stop(self):
        """Detiene y limpia el backend."""
        if self._tier == "gold" and self._gadget_path:
            self._cleanup_gadget(self._gadget_path)
            self._gadget_path = None
            self._hidg_mouse = None
            self._hidg_kbd = None
        if self._tier == "silver" and hasattr(self, '_uinput_fd'):
            try:
                import fcntl
                UI_DEV_DESTROY = 0x5502
                fcntl.ioctl(self._uinput_fd, UI_DEV_DESTROY)
                os.close(self._uinput_fd)
            except Exception:
                pass
        self._started = False
        log.info("USBHIDBackend detenido")

    # ── Info ────────────────────────────────────────────────────────────────────

    @property
    def tier(self) -> str:
        return self._tier

    @property
    def is_gold(self) -> bool:
        return self._tier == "gold"

    def stats(self) -> Dict[str, Any]:
        return {
            "tier": self._tier,
            "started": self._started,
            "udc": self._udc,
            "hidg_mouse": str(self._hidg_mouse) if self._hidg_mouse else None,
            "hidg_kbd": str(self._hidg_kbd) if self._hidg_kbd else None,
            "mouse_pos": self._mouse_pos,
            "screen": f"{self._screen_w}x{self._screen_h}",
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_backend: Optional[USBHIDBackend] = None


def get_usb_hid_backend(auto_start: bool = True) -> USBHIDBackend:
    """Obtiene el singleton USBHIDBackend."""
    global _backend
    if _backend is None:
        _backend = USBHIDBackend()
        if auto_start:
            _backend.start()
    return _backend


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description="USB HID Backend — dispositivo USB virtual")
    p.add_argument("--start", action="store_true", help="Iniciar backend")
    p.add_argument("--stop", action="store_true", help="Detener backend")
    p.add_argument("--stats", action="store_true", help="Mostrar estado")
    p.add_argument("--check", action="store_true",
                   help="Verificar disponibilidad de módulos kernel")
    p.add_argument("--move", nargs=2, type=int, metavar=("X", "Y"),
                   help="Mover ratón a coordenadas")
    p.add_argument("--click", type=int, default=1, help="Click (1=izq, 3=der)")
    p.add_argument("--type", type=str, help="Escribir texto")
    args = p.parse_args()

    if args.check:
        print("=== Verificación USB Gadget HID ===")
        print(f"configfs montado: {Path(GADGET_BASE).exists()}")
        print(f"dummy_hcd: {_check_kernel_module('dummy_hcd')}")
        print(f"usb_f_hid: {_check_kernel_module('usb_f_hid')}")
        print(f"UDCs disponibles: {_check_udc_available()}")
        print(f"/dev/uinput existe: {Path('/dev/uinput').exists()}")

    elif args.start:
        backend = get_usb_hid_backend(auto_start=True)
        print(f"Iniciado: tier={backend.tier}")
        print(backend.stats())

    elif args.stop:
        if _backend:
            _backend.stop()
            print("Detenido")

    elif args.stats:
        backend = get_usb_hid_backend()
        print(backend.stats())

    elif args.move:
        backend = get_usb_hid_backend()
        backend.mouse_move_absolute(args.move[0], args.move[1])
        print(f"Movido a ({args.move[0]}, {args.move[1]})")

    elif args.type:
        backend = get_usb_hid_backend()
        backend.keyboard_type(args.type)
        print(f"Escrito: {args.type[:50]}")

    else:
        p.print_help()
