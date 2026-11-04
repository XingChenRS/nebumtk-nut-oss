# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Best-effort Windows 11 system backdrop support."""

from __future__ import annotations

import ctypes
import sys
import weakref
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_MAINWINDOW = 2
DWMSBT_TRANSIENTWINDOW = 3


def apply_system_backdrop(widget: "QWidget", *, backdrop_type: int = DWMSBT_MAINWINDOW) -> bool:
    """Request a Windows compositor backdrop without changing Qt window chrome."""
    if sys.platform != "win32":
        return False
    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return False
        dwmapi = ctypes.WinDLL("dwmapi")
        set_attribute = dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        set_attribute.restype = ctypes.c_long

        light_caption = ctypes.c_int(0)
        set_attribute(
            ctypes.c_void_p(hwnd),
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(light_caption),
            ctypes.sizeof(light_caption),
        )
        backdrop = ctypes.c_int(backdrop_type)
        result = set_attribute(
            ctypes.c_void_p(hwnd),
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop),
            ctypes.sizeof(backdrop),
        )
        return result == 0
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def schedule_system_backdrop(widget: "QWidget", *, backdrop_type: int = DWMSBT_MAINWINDOW) -> None:
    """Run after Qt creates the native window handle."""
    widget_ref = weakref.ref(widget)

    def apply_if_alive() -> None:
        target = widget_ref()
        if target is not None:
            apply_system_backdrop(target, backdrop_type=backdrop_type)

    QTimer.singleShot(0, apply_if_alive)
