"""Active/inactive slot helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import VerificationError


@dataclass(frozen=True)
class SlotInfo:
    active_suffix: str
    inactive_suffix: str

    @property
    def active_letter(self) -> str:
        return self.active_suffix.lstrip("_")

    @property
    def inactive_letter(self) -> str:
        return self.inactive_suffix.lstrip("_")


class SlotManager:
    @staticmethod
    def from_suffix(raw_suffix: str) -> SlotInfo:
        suffix = raw_suffix.strip()
        if suffix not in {"_a", "_b"}:
            raise VerificationError(f"无效活动槽位: {raw_suffix!r}")
        inactive = "_b" if suffix == "_a" else "_a"
        return SlotInfo(active_suffix=suffix, inactive_suffix=inactive)

    @staticmethod
    def partition_name(base: str, suffix: str, *, use_slot: bool) -> str:
        if use_slot:
            return f"{base}{suffix}"
        return base
