# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Validated privilege providers for rooted ADB operations."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Literal, Protocol

from .errors import VerificationError
from .numeric import parse_bounded_uint64_decimal


MAX_CUSTOM_SU_UTF8_BYTES = 256
_COMMAND_TOKEN = re.compile(r"[A-Za-z0-9._-]+")
_PATH_COMPONENT = re.compile(r"[A-Za-z0-9._-]+")
_UID = re.compile(r"(?:^|\s)uid=([0-9]+)(?:\([^\r\n)]*\))?")


@dataclass(frozen=True)
class SuProvider:
    kind: Literal["protected", "custom"]
    executable: str


@dataclass(frozen=True)
class SuProbeResult:
    uid: int
    output: str


class _RootShell(Protocol):
    def shell(self, command: str, **kwargs): ...


def validate_custom_su(value: str) -> str:
    """Return one safe Android executable token or reject it."""

    if not isinstance(value, str):
        raise ValueError("custom su must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("custom su must be valid UTF-8") from exc
    if not encoded or len(encoded) > MAX_CUSTOM_SU_UTF8_BYTES:
        raise ValueError("custom su token length is invalid")
    if value.startswith("/"):
        components = value[1:].split("/")
        if not components or any(
            component in {"", ".", ".."}
            or _PATH_COMPONENT.fullmatch(component) is None
            for component in components
        ):
            raise ValueError("custom su absolute path is invalid")
        return value
    if _COMMAND_TOKEN.fullmatch(value) is None:
        raise ValueError("custom su command token is invalid")
    return value


def render_root_command(executable: str, command: str) -> str:
    """Render the executable as exactly one argv token before fixed ``-c``."""

    token = validate_custom_su(executable)
    return f"{shlex.quote(token)} -c {shlex.quote(command)}"


def resolve_su_provider(
    kind: str,
    *,
    protected_executable: str,
    settings,
) -> SuProvider:
    if kind == "protected":
        return SuProvider("protected", validate_custom_su(protected_executable))
    if kind == "custom":
        return SuProvider("custom", validate_custom_su(settings.custom_su))
    raise ValueError(f"unknown su provider: {kind!r}")


def probe_custom_su(adb: _RootShell, executable: str) -> SuProbeResult:
    """Run the sole custom-provider validation command: fixed ``id``."""

    token = validate_custom_su(executable)
    result = adb.shell(
        "id",
        root=True,
        su_path=token,
        timeout=15,
        check=False,
        sensitive=True,
    )
    output = (result.stdout or "").strip()
    match = _UID.search(output)
    if result.returncode != 0 or match is None:
        raise VerificationError("custom su fixed id probe failed")
    uid = parse_bounded_uint64_decimal(
        match.group(1),
        error_message="custom su fixed id probe failed",
    )
    return SuProbeResult(uid=uid, output=output)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
