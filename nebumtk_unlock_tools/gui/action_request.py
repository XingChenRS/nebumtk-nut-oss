# SPDX-License-Identifier: AGPL-3.0-only
"""Typed destructive UI request bound to immutable local-file facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.operation_files import VerifiedFileSnapshot


@dataclass(frozen=True, slots=True)
class DeviceActionContext:
    serial: str
    mode: str
    scan_generation: int
    active_slot: str

    def __post_init__(self) -> None:
        if not self.serial.strip():
            raise ValueError("device action serial is required")
        if self.mode not in {"adb", "fastboot"}:
            raise ValueError("device action mode is invalid")
        if self.scan_generation < 0:
            raise ValueError("device action scan generation is invalid")
        if self.active_slot not in {"_a", "_b"}:
            raise ValueError("device action active slot is invalid")


@dataclass(frozen=True, slots=True)
class DestructiveFlowRequest:
    flow_kind: str
    source: VerifiedFileSnapshot | None
    targets: tuple[str, ...]
    slot_effect: str
    cancellation_boundary: str
    device: DeviceActionContext
    preload_override: object | None = None
    artifact_ref: str = ""
    backup_directory: Path | None = None
    install_sakisu_root: bool = False
    sakisu_init_boot: Path | None = None
    sakisu_kmi: str = ""
