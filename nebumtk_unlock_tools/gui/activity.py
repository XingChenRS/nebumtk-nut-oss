# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.activity import ActivityEvent, ActivityKind, WaitTarget


@dataclass(frozen=True)
class DiagnosticItem:
    text: str
    error: bool = False


@dataclass
class ActivityState:
    max_events: int = 30
    max_diagnostics: int = 100
    kind: ActivityKind = ActivityKind.IDLE
    title: str = "就绪"
    detail: str = ""
    deadline: float | None = None
    target: WaitTarget | None = None
    serial: str = ""
    expected_mode: str = ""
    events: list[ActivityEvent] = field(default_factory=list)
    diagnostics: list[DiagnosticItem] = field(default_factory=list)

    def apply(self, event: ActivityEvent) -> None:
        self.kind = event.kind
        self.title = event.title
        self.detail = event.detail
        self.deadline = event.deadline
        self.target = event.target
        self.serial = event.serial
        self.expected_mode = event.expected_mode
        if not self.events or self.events[-1] != event:
            self.events.append(event)
            del self.events[:-self.max_events]

    def add_diagnostic(self, text: str, *, error: bool = False) -> bool:
        normalized = text.strip()
        if not normalized or normalized == "重复轮询":
            return False
        self.diagnostics.append(DiagnosticItem(normalized, error))
        del self.diagnostics[:-self.max_diagnostics]
        return True


def format_remaining_seconds(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60:02d}:{whole % 60:02d}"
