# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Best-effort native Windows chrome without replacing the non-client area."""

from __future__ import annotations

import ctypes
import sys
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_MAINWINDOW = 2
DWMSBT_TRANSIENTWINDOW = 3
DWMSBT_NONE = 1
DWMWCP_ROUND = 2


@dataclass(frozen=True, slots=True)
class NativeWindowAppearance:
    dark: bool
    backdrop_type: int
    corner_preference: int = DWMWCP_ROUND
    border_color: int = 0x00202324
    caption_color: int = 0x00202324
    text_color: int = 0x00E8F2F7

    @classmethod
    def main(cls) -> "NativeWindowAppearance":
        return cls(dark=True, backdrop_type=DWMSBT_MAINWINDOW)

    @classmethod
    def frameless(cls) -> "NativeWindowAppearance":
        """Keep DWM window mechanics while explicitly disabling glass/Mica."""

        return cls(
            dark=False,
            backdrop_type=DWMSBT_NONE,
            border_color=0x00AABAC7,
            caption_color=0x00E9F2F7,
            text_color=0x0031363B,
        )

    @classmethod
    def transient(cls) -> "NativeWindowAppearance":
        return cls(dark=True, backdrop_type=DWMSBT_TRANSIENTWINDOW)


def apply_native_window_chrome(
    widget: "QWidget",
    appearance: NativeWindowAppearance,
) -> bool:
    """Request native caption, corners and backdrop; unsupported hosts fall back."""

    if not isinstance(appearance, NativeWindowAppearance):
        raise TypeError("appearance must be NativeWindowAppearance")
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
        values = (
            (DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if appearance.dark else 0),
            (DWMWA_WINDOW_CORNER_PREFERENCE, appearance.corner_preference),
            (DWMWA_BORDER_COLOR, appearance.border_color),
            (DWMWA_CAPTION_COLOR, appearance.caption_color),
            (DWMWA_TEXT_COLOR, appearance.text_color),
            (DWMWA_SYSTEMBACKDROP_TYPE, appearance.backdrop_type),
        )
        accepted = False
        for attribute, raw_value in values:
            value = ctypes.c_int(raw_value)
            result = set_attribute(
                ctypes.c_void_p(hwnd),
                attribute,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            accepted = accepted or result == 0
        return accepted
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def schedule_native_window_chrome(
    widget: "QWidget",
    appearance: NativeWindowAppearance,
) -> None:
    """Apply native appearance after Qt creates the native window handle."""

    widget_ref = weakref.ref(widget)

    def apply_if_alive() -> None:
        target = widget_ref()
        if target is not None:
            apply_native_window_chrome(target, appearance)

    QTimer.singleShot(0, apply_if_alive)
