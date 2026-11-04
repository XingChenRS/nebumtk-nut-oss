# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Persistent session log file (append-only, best-effort)."""

from __future__ import annotations

import threading
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

LOG_TEXT_LIMIT = 240
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:[a-z]:\\|\\\\)[^\r\n\"']+")
_ANDROID_PATH_RE = re.compile(
    r"(?i)(?<![a-z0-9_.-])/(?:data|dev|system|vendor|proc|sys|storage|sdcard)"
    r"(?:/[^\s,;:)]+)+"
)
_LONG_HEX_RE = re.compile(r"(?i)\b[0-9a-f]{32,}\b")
_LONG_BASE64_RE = re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}\b")
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)\b(token|payload|private[ _-]?key|signature|authorization|auth)\b"
    r"\s*[:=]\s*\S+"
)


def compact_log_message(message: object) -> str:
    """Return a bounded release-safe diagnostic line."""
    text = "" if message is None else str(message)
    text = text.replace("\x00", "")
    stripped = text.lstrip()
    if stripped.startswith("$ adb"):
        text = "ADB 操作"
    elif stripped.startswith("$ fastboot"):
        text = "Fastboot 操作"
    text = _WINDOWS_PATH_RE.sub("<本地路径>", text)
    text = _ANDROID_PATH_RE.sub("<设备路径>", text)
    text = _SENSITIVE_FIELD_RE.sub(lambda match: f"{match.group(1)}: <已隐藏>", text)
    text = _LONG_HEX_RE.sub("<摘要>", text)
    text = _LONG_BASE64_RE.sub("<数据>", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = " · ".join(lines[:2])
    if len(lines) > 2:
        text += " · …"
    if len(text) > LOG_TEXT_LIMIT:
        text = text[: LOG_TEXT_LIMIT - 1] + "…"
    return text


class SessionLog:
    """Fan-out logger: forwards a message to GUI callback AND appends to disk."""

    def __init__(self, log_dir: Path, gui_callback: Callable[[str], None]) -> None:
        self.log_dir = Path(log_dir)
        self.gui_callback = gui_callback
        self._lock = threading.Lock()
        self._path: Path | None = None
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._path = self.log_dir / f"session_{stamp}.log"
        except OSError:
            self._path = None

    @property
    def path(self) -> Path | None:
        return self._path

    def __call__(self, message: str) -> None:
        text = compact_log_message(message)
        if not text:
            return
        try:
            self.gui_callback(text)
        except Exception:
            pass
        self._persist(text)

    def _persist(self, text: str) -> None:
        if self._path is None:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text}\n"
        try:
            with self._lock:
                with self._path.open("a", encoding="utf-8", errors="replace") as fh:
                    fh.write(line)
        except OSError:
            pass
