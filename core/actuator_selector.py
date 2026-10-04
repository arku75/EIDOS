"""Environment-aware actuator selection for EIDOS.

Selection is descriptive and fail-closed. Availability is not authorization,
and physical HID/uinput execution remains outside CI/HIL unless explicitly
authorized by the operator.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class ActuatorChoice:
    backend: str
    available: bool
    reason: str
    requires_hil: bool = False


def choose_actuator(env: dict[str, str] | None = None) -> ActuatorChoice:
    e = dict(os.environ if env is None else env)
    session = e.get("XDG_SESSION_TYPE", "").lower()
    display = e.get("DISPLAY", "")
    wayland = e.get("WAYLAND_DISPLAY", "")

    if session == "wayland" or wayland:
        return ActuatorChoice(
            "wayland-no-generic-injector", False,
            "Wayland session detected; xdotool/XTest is not a valid generic actuator",
            True,
        )
    if display and shutil.which("xdotool"):
        return ActuatorChoice(
            "xdotool-x11", True,
            "X11 DISPLAY and xdotool are available",
            False,
        )
    return ActuatorChoice(
        "none", False,
        "No verified desktop actuator for this environment",
        False,
    )
