"""Diagnostic-only policy for the bundled ADB/Fastboot terminal."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .errors import VerificationError

_SAFE_PROPERTY = re.compile(r"^[a-zA-Z0-9_.-]{1,96}$")
_SAFE_GETVAR = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
_FORBIDDEN_META = set(";&|`$<>{}\r\n")


@dataclass(frozen=True)
class DiagnosticCommand:
    tool: str
    args: tuple[str, ...]


def parse_diagnostic_command(command: str) -> DiagnosticCommand:
    if not command or any(char in command for char in _FORBIDDEN_META):
        raise VerificationError("诊断终端拒绝空命令、控制字符或 shell 元字符")
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        raise VerificationError(f"诊断命令解析失败: {exc}") from exc
    tokens = [token.strip('"').strip("'") for token in tokens]
    if not tokens:
        raise VerificationError("诊断命令为空")
    tool = tokens[0].lower()
    args = tokens[1:]
    if any(token == "-s" or token.startswith("-s") for token in args):
        raise VerificationError("诊断终端不允许自行指定设备 serial")

    if tool == "adb":
        normalized = _parse_adb(args)
    elif tool == "fastboot":
        normalized = _parse_fastboot(args)
    else:
        raise VerificationError("仅允许带 adb 或 fastboot 前缀的诊断命令")
    return DiagnosticCommand(tool=tool, args=tuple(normalized))


def _parse_adb(args: list[str]) -> list[str]:
    if args in (["devices"], ["get-state"], ["get-serialno"]):
        return args
    if args == ["shell", "id"]:
        return args
    if len(args) == 3 and args[:2] == ["shell", "getprop"] and _SAFE_PROPERTY.fullmatch(args[2]):
        return args
    if args == ["shell", "uname"] or args == ["shell", "uname", "-a"]:
        return args
    raise VerificationError("ADB 诊断终端仅允许 devices/get-state/get-serialno/getprop/id/uname")


def _parse_fastboot(args: list[str]) -> list[str]:
    if args == ["devices"]:
        return args
    if len(args) == 2 and args[0] == "getvar" and _SAFE_GETVAR.fullmatch(args[1]):
        return args
    raise VerificationError("Fastboot 诊断终端仅允许 devices 或 getvar")
