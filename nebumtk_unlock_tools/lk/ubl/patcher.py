# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import PatchCatalog, PatchRecord
from .cert_bypass import apply_cert_bypass_to_bytes
from .lk_image import LkImage
from .size_policy import inspect_output_size
from .xingc_locator import PatchStatus, locate_patch


@dataclass(frozen=True)
class PatchChange:
    patch_id: str
    offset: int
    old_bytes: bytes
    new_bytes: bytes
    partition: str


@dataclass(frozen=True)
class PatchPlan:
    image: LkImage
    statuses: tuple[PatchStatus, ...]
    changes: tuple[PatchChange, ...]


class PatchError(RuntimeError):
    pass


def build_plan(
    image: LkImage,
    catalog: PatchCatalog,
    key_blob: bytes,
    *,
    image_profile: str | None = None,
) -> PatchPlan:
    statuses: list[PatchStatus] = []
    changes: list[PatchChange] = []
    search_ranges = image.partition_search_ranges()

    for patch in catalog.patches:
        replacement = _replacement_for_patch(patch, key_blob)
        status = locate_patch(
            image.data,
            patch,
            replacement=replacement,
            search_ranges=search_ranges,
            image_profile=image_profile,
        )
        statuses.append(status)
        if status.state == "patched":
            continue
        if status.state != "original":
            raise PatchError(f"{patch.id}: {status.state} ({status.detail})")
        if status.offset is None or status.replace_len is None:
            raise PatchError(f"{patch.id}: no offset")
        effective = status.replacement if status.replacement is not None else replacement
        old_bytes = image.data[status.offset : status.offset + status.replace_len]
        changes.append(
            PatchChange(
                patch_id=patch.id,
                offset=status.offset,
                old_bytes=old_bytes,
                new_bytes=effective,
                partition=image.partition_for_offset(status.offset).name,
            )
        )
    return PatchPlan(image=image, statuses=tuple(statuses), changes=tuple(changes))


def apply_plan(
    plan: PatchPlan,
    output: str | Path,
    *,
    cert_bypass_mode: str = "override",
) -> tuple[bytes, tuple[str, ...]]:
    output_path = Path(output)
    try:
        in_abs = plan.image.path.resolve(strict=False)
        out_abs = output_path.resolve(strict=False)
        same_path = in_abs == out_abs
    except OSError:
        same_path = str(plan.image.path) == str(output_path)
    if same_path:
        raise ValueError("output must differ from input")

    patched = bytearray(plan.image.data)
    for change in plan.changes:
        end = change.offset + len(change.new_bytes)
        if patched[change.offset:end] != change.old_bytes:
            raise PatchError(f"input changed before apply: {change.patch_id}")
        patched[change.offset:end] = change.new_bytes

    output_bytes, signed = apply_cert_bypass_to_bytes(bytes(patched), mode=cert_bypass_mode)
    if "lk" not in signed:
        raise PatchError("cert bypass did not re-sign lk partition (refusing to write output)")
    cap = len(plan.image.data)
    natural = len(output_bytes)
    size_policy = inspect_output_size(plan.image.data, output_bytes)
    if not size_policy.allowed:
        raise PatchError(f"unsafe LK output size: {size_policy.detail}")
    if natural < cap:
        preserved_end = min(cap, len(plan.image.data))
        output_bytes += plan.image.data[natural:preserved_end]
        if len(output_bytes) < cap:
            output_bytes += bytes(cap - len(output_bytes))
    if len(output_bytes) != size_policy.expected_size:
        raise PatchError(
            "LK output size changed after padding preservation: "
            f"{len(output_bytes)}/{size_policy.expected_size}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace: avoid leaving a half-written image if the process is killed mid-write.
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    tmp_path.write_bytes(output_bytes)
    tmp_path.replace(output_path)
    return output_bytes, signed


def _replacement_for_patch(patch: PatchRecord, key_blob: bytes) -> bytes:
    if patch.dynamic == "hedgehog_public_key_blob":
        if len(key_blob) != patch.replacement_size:
            raise PatchError(f"key blob must be {patch.replacement_size} bytes")
        return key_blob
    return patch.patch_bytes
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
