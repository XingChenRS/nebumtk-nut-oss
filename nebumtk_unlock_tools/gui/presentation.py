"""Dependency-light typed presentation contracts for the shell."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .theme import online_stylesheet


class ProgressKind(str, Enum):
    IDLE = "idle"
    DETERMINATE = "determinate"
    WAITING = "waiting"


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    kind: ProgressKind
    label: str
    percent: int | None = None
    cancellable: bool = False

    def __post_init__(self) -> None:
        if self.kind is ProgressKind.DETERMINATE:
            if type(self.percent) is not int or not 0 <= self.percent <= 100:
                raise ValueError("determinate progress requires an integer from 0 to 100")
        elif self.percent is not None:
            raise ValueError("non-determinate progress cannot carry a percentage")

    @classmethod
    def idle(cls, label: str = "Ready") -> "ProgressSnapshot":
        return cls(ProgressKind.IDLE, label)

    @classmethod
    def waiting(
        cls,
        label: str,
        *,
        cancellable: bool = False,
    ) -> "ProgressSnapshot":
        return cls(ProgressKind.WAITING, label, cancellable=cancellable)

    @classmethod
    def determinate(
        cls,
        percent: int,
        label: str,
        *,
        cancellable: bool = False,
    ) -> "ProgressSnapshot":
        return cls(
            ProgressKind.DETERMINATE,
            label,
            percent=percent,
            cancellable=cancellable,
        )


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    generation: int
    mode: str = "—"
    serial: str = "—"
    model: str = "—"
    manufacturer: str = "—"
    codename: str = "—"
    android: str = "—"
    build_incremental: str = "—"
    security_patch: str = "—"
    firmware: str = "—"
    platform: str = "—"
    kernel: str = "—"
    slot: str = "—"
    bootloader: str = "—"
    profile: str = "—"

