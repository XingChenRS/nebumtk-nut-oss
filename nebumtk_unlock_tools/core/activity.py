# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ActivityKind(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCESS = "success"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    ERROR = "error"


class WaitTarget(StrEnum):
    FASTBOOT = "fastboot"
    FASTBOOTD = "fastbootd"
    BOOTLOADER = "bootloader"
    ANDROID_READY = "android_ready"
    REBOOT_TARGET = "reboot_target"


@dataclass(frozen=True)
class ActivityEvent:
    kind: ActivityKind
    title: str
    detail: str = ""
    deadline: float | None = None
    target: WaitTarget | None = None
    serial: str = ""
    expected_mode: str = ""
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
