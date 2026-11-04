# SPDX-License-Identifier: AGPL-3.0-only
"""Persistent session log file (append-only, best-effort)."""

from __future__ import annotations

import threading
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

LOG_TEXT_LIMIT = 240
DIAGNOSTIC_LINE_LIMIT = 40
DIAGNOSTIC_TEXT_LIMIT = 8000
# `dd` and friends spell their operands `name=value`, which the environment
# rule below would otherwise mask — hiding exactly the line that says which
# partition a write was aimed at.  These names carry no secret; their values
# still pass through the path, hex and base64 masks.
_FIXED_OPERAND_NAMES = frozenset(
    {"if", "of", "bs", "count", "seek", "skip", "conv", "status", "iflag", "oflag"}
)
_OPERAND_RE = re.compile(
    r"(?<![a-z0-9_])(?P<name>" + "|".join(sorted(_FIXED_OPERAND_NAMES)) + r")=\S+"
)
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:[a-z]:\\|\\\\)[^\r\n\"']+")
_ANDROID_PATH_RE = re.compile(
    r"(?i)(?<![a-z0-9_.-])/(?:data|dev|system|vendor|proc|sys|storage|sdcard)"
    r"(?=$|[\s,;:)]|/)(?:/[^\s,;:)]+)*"
)
_COMMAND_LINE_RE = re.compile(
    r"(?im)^[ \t]*\$\s*[^\r\n]*?\b(?P<tool>adb|fastboot)"
    r"(?:\.exe)?\b[^\r\n]*[ \t]*$"
)
_LONG_HEX_RE = re.compile(r"(?i)\b[0-9a-f]{32,}\b")
_LONG_BASE64_RE = re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}\b")
_RESOURCE_REF_RE = re.compile(r"(?i)\bbuiltin:[a-z0-9_.-]+")
_RESOURCE_FILE_RE = re.compile(
    r"(?i)\b[a-z0-9_.-]*(?:preload|wrapper|payload)[a-z0-9_.-]*\.(?:so|elf|bin|img|dll)\b"
)
_INTERNAL_ID_RE = re.compile(
    r"(?i)\b(?:payload|runtime|profile|resource)[_-]?id\b\s*[:=]\s*\S+"
)
_ENV_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![a-z0-9_])[a-z_][a-z0-9_]*\s*=\s*"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)(?P<key>[\"']?(?:password|access[_ -]?token|refresh[_ -]?token|"
    r"xingc[_ -]?token|token|vmp[_ -]?serial|cek|payload|private[ _-]?key|"
    r"signature|authorization|auth|challenge|grant)[\"']?)"
    r"\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\r\n,;]+)"
)


def format_device_summary(
    *,
    serial: str,
    mode: str,
    brand: str = "",
    model: str = "",
    firmware: str = "",
) -> str:
    """Format non-secret operated-device context for local diagnostics."""
    fields = (
        ("设备", serial),
        ("模式", mode),
        ("品牌", brand),
        ("型号", model),
        ("系统", firmware),
    )
    return " · ".join(
        f"{label}: {value.strip()}"
        for label, value in fields
        if value and value.strip()
    )


def compact_log_message(message: object) -> str:
    """Return a bounded release-safe diagnostic line."""
    text = "" if message is None else str(message)
    text = text.replace("\x00", "")
    text = _COMMAND_LINE_RE.sub(
        lambda match: (
            "ADB 操作"
            if match.group("tool").lower() == "adb"
            else "Fastboot 操作"
        ),
        text,
    )
    text = _RESOURCE_REF_RE.sub("<受保护资源>", text)
    text = _RESOURCE_FILE_RE.sub("<受保护资源>", text)
    text = _INTERNAL_ID_RE.sub("资源标识: <已隐藏>", text)
    text = _ENV_ASSIGNMENT_RE.sub("环境参数: <已隐藏>", text)
    text = _WINDOWS_PATH_RE.sub("<本地路径>", text)
    text = _ANDROID_PATH_RE.sub("<设备路径>", text)
    text = _SENSITIVE_FIELD_RE.sub(
        lambda match: f"{match.group('key')}: <已隐藏>",
        text,
    )
    text = _LONG_HEX_RE.sub("<摘要>", text)
    text = _LONG_BASE64_RE.sub("<数据>", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = " · ".join(lines[:2])
    if len(lines) > 2:
        text += " · …"
    if len(text) > LOG_TEXT_LIMIT:
        text = text[: LOG_TEXT_LIMIT - 1] + "…"
    return text


def diagnostic_log_message(message: object) -> str:
    """The local, never-uploaded form of one log line.

    The session log is a diagnostic file on the operator's own machine, and it
    has to say what actually ran and what actually failed.  So it keeps the
    commands, arguments, device paths and multi-line output that the shareable
    compaction removes — that compaction exists for text leaving the machine,
    and applying it here produced a file of nothing but "ADB 操作".

    Secrets stay masked, because a token is secret wherever it is written:
    key material, signatures and challenges, environment values, and long
    opaque blobs.
    """

    text = "" if message is None else str(message)
    text = text.replace("\x00", "")

    # Hold `dd`-style operands aside so the environment rule cannot eat them,
    # then put them back untouched.
    held: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        held.append(match.group(0))
        return f"\x00{len(held) - 1}\x00"

    text = _OPERAND_RE.sub(_hold, text)
    text = _SENSITIVE_FIELD_RE.sub(
        lambda match: f"{match.group('key')}: <已隐藏>", text
    )
    text = _ENV_ASSIGNMENT_RE.sub("环境参数: <已隐藏>", text)
    text = _LONG_HEX_RE.sub("<摘要>", text)
    text = _LONG_BASE64_RE.sub("<数据>", text)
    text = re.sub(r"\x00(\d+)\x00", lambda match: held[int(match.group(1))], text)

    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) > DIAGNOSTIC_LINE_LIMIT:
        omitted = len(lines) - DIAGNOSTIC_LINE_LIMIT
        lines = lines[:DIAGNOSTIC_LINE_LIMIT] + [f"…（另有 {omitted} 行）"]
    text = "\n".join(lines)
    if len(text) > DIAGNOSTIC_TEXT_LIMIT:
        text = text[: DIAGNOSTIC_TEXT_LIMIT - 1] + "…"
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
        display = compact_log_message(message)
        if display:
            try:
                self.gui_callback(display)
            except Exception:
                pass
        self._persist(diagnostic_log_message(message))

    def _persist(self, text: str) -> None:
        if self._path is None or not text:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        lines = text.splitlines()
        block = "\n".join(
            [f"[{stamp}] {lines[0]}"] + [f"{' ' * 11}{line}" for line in lines[1:]]
        )
        try:
            with self._lock:
                with self._path.open("a", encoding="utf-8", errors="replace") as fh:
                    fh.write(block + "\n")
        except OSError:
            pass
