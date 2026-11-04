# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any



def parse_int(value: int | str) -> int:
    if isinstance(value, int):
        return value
    return int(value, 0)


@dataclass(frozen=True)
class LocatorRecord:
    kind: str
    needle: bytes | None = None
    replace_at: int = 0
    search_max: int | None = None
    offset: int | None = None
    original: bytes | None = None
    original_sha256: str | None = None
    patch: bytes | None = None
    replace_len: int | None = None
    length: int | None = None
    partition: str | None = None
    profile: str | None = None
    image_profile: str | None = None
    layout: str | None = None

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "LocatorRecord":
        return cls(
            kind=str(item["kind"]),
            needle=bytes.fromhex(item["needle_hex"]) if "needle_hex" in item else None,
            replace_at=parse_int(item.get("replace_at", 0)),
            search_max=parse_int(item["search_max"]) if "search_max" in item else None,
            offset=parse_int(item["offset"]) if "offset" in item else None,
            original=bytes.fromhex(item["original_hex"]) if "original_hex" in item else None,
            original_sha256=item.get("original_sha256"),
            patch=bytes.fromhex(item["patch_hex"]) if "patch_hex" in item else None,
            replace_len=parse_int(item["replace_len"]) if "replace_len" in item else None,
            length=parse_int(item["length"]) if "length" in item else None,
            partition=item.get("partition"),
            profile=item.get("profile"),
            image_profile=item.get("image_profile"),
            layout=item.get("layout"),
        )


@dataclass(frozen=True)
class PatchRecord:
    id: str
    human_name: str
    category: str
    risk: str
    summary: str
    principle: str
    ida_context: str
    locators: tuple[LocatorRecord, ...]
    patch_hex: str | None = None
    dynamic: str | None = None
    replace_len: int | None = None

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "PatchRecord":
        return cls(
            id=str(item["id"]),
            human_name=str(item["human_name"]),
            category=str(item["category"]),
            risk=str(item["risk"]),
            summary=str(item["summary"]),
            principle=str(item["principle"]),
            ida_context=str(item["ida_context"]),
            patch_hex=item.get("patch_hex"),
            dynamic=item.get("dynamic"),
            replace_len=parse_int(item["replace_len"]) if "replace_len" in item else None,
            locators=tuple(LocatorRecord.from_json(locator) for locator in item["locators"]),
        )

    @property
    def patch_bytes(self) -> bytes:
        if self.patch_hex is None:
            raise ValueError(f"patch {self.id} uses dynamic replacement {self.dynamic}")
        return bytes.fromhex(self.patch_hex)

    @property
    def replacement_size(self) -> int:
        if self.replace_len is not None:
            return self.replace_len
        return len(self.patch_bytes)


class PatchCatalog:
    XINGC_IDS = (
        "xingc-rename-veify-command",
        "xingc-rename-veify-log-marker",
        "xingc-replace-hedgehog-public-key",
    )

    def __init__(self, patches: dict[str, PatchRecord]) -> None:
        self._patches = patches

    @classmethod
    def from_bytes(cls, data: bytes) -> "PatchCatalog":
        raw = json.loads(data.decode("utf-8"))
        patches = {patch.id: patch for patch in (PatchRecord.from_json(item) for item in raw["patches"])}
        return cls(patches)

    @classmethod
    def from_path(cls, path: str | Path) -> "PatchCatalog":
        return cls.from_bytes(Path(path).read_bytes())

    @property
    def patches(self) -> tuple[PatchRecord, ...]:
        return tuple(self._patches[patch_id] for patch_id in self.XINGC_IDS)

    def patch(self, patch_id: str) -> PatchRecord:
        return self._patches[patch_id]
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
