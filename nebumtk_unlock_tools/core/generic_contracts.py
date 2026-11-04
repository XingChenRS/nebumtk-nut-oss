"""Secret-free structural contracts for generic local operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class GenericDevice(Protocol):
    serial: str
    brand: str
    model: str
    device: str
    firmware: str
    slot_suffix: str


class GenericProfile(Protocol):
    key: str
    brand: str
    display_name: str
    match: Mapping[str, object]
    su_path: str


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    serial: str
    scan_generation: int
    operation_id: str
    manifest_sha256: str
    base_name: str
    targets: tuple[str, ...]
    initial_slot: str
    stage: str

    def __post_init__(self) -> None:
        if not self.serial.strip():
            raise ValueError("restore receipt serial is required")
        if self.scan_generation < 0:
            raise ValueError("restore receipt scan generation is invalid")
        if not self.operation_id.strip():
            raise ValueError("restore receipt operation ID is required")
        digest = self.manifest_sha256.strip().lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("restore receipt manifest digest is invalid")
        if self.initial_slot not in {"_a", "_b"}:
            raise ValueError("restore receipt initial slot is invalid")
        if self.targets != (f"{self.base_name}_a", f"{self.base_name}_b"):
            raise ValueError("restore receipt does not contain a complete pair")
        if self.stage not in {"pair-restored", "slot-restored", "reboot-issued"}:
            raise ValueError("restore receipt stage is invalid")
