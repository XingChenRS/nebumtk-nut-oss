# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct

from .catalog import LocatorRecord, PatchRecord


PATCH_STATES = {
    "original",
    "patched",
    "patched-unverified",
    "conflict",
    "not-found",
    "ambiguous",
    "not-applicable",
}

SEED_AUTH_PROFILES: tuple[bytes, ...] = (
    b"chocolate",
    b"donuts",
    b"hedgehog",
    b"lollipop",
    b"kangaroo",
)
SEED_AUTH_PROFILE_RECORD_SIZE = 56
SEED_AUTH_KEY_POINTER_OFFSET = 40
SEED_AUTH_KEY_SIZE_OFFSET = 48
SEED_AUTH_AVB_KEY_MAGIC = b"\x00\x00\x08\x00"


@dataclass(frozen=True)
class PatchStatus:
    patch_id: str
    state: str
    offset: int | None = None
    replace_len: int | None = None
    current: bytes | None = None
    original: bytes | None = None
    replacement: bytes | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in PATCH_STATES:
            raise ValueError(f"invalid patch state {self.state!r}")


def locate_patch(
    data: bytes,
    patch: PatchRecord,
    replacement: bytes | None = None,
    search_ranges: dict[str, tuple[int, int]] | None = None,
    image_profile: str | None = None,
) -> PatchStatus:
    replacement_bytes = replacement if replacement is not None else _static_patch_or_none(patch)
    statuses: list[PatchStatus] = []

    for locator in patch.locators:
        if not _locator_applies(locator, image_profile):
            continue
        status = _locate_with_locator(
            data,
            patch,
            locator,
            replacement_bytes,
            search_ranges=search_ranges,
        )
        if status.state != "not-found":
            statuses.append(status)

    strong = [status for status in statuses if status.state in {"original", "patched"}]
    if strong:
        return _coalesce_statuses(patch, strong)

    unverified = [status for status in statuses if status.state == "patched-unverified"]
    if unverified:
        return _coalesce_statuses(patch, unverified)

    conflicts = [status for status in statuses if status.state == "conflict"]
    if conflicts:
        return _coalesce_statuses(patch, conflicts)

    ambiguous = [status for status in statuses if status.state == "ambiguous"]
    if ambiguous:
        return ambiguous[0]

    return PatchStatus(patch_id=patch.id, state="not-found")


def _coalesce_statuses(patch: PatchRecord, statuses: list[PatchStatus]) -> PatchStatus:
    if len(statuses) > 1:
        offsets = sorted(status.offset for status in statuses if status.offset is not None)
        if len(set(offsets)) > 1:
            return PatchStatus(
                patch_id=patch.id,
                state="ambiguous",
                detail=f"multiple candidate offsets: {', '.join(hex(o) for o in offsets)}",
            )
    if statuses:
        return statuses[0]
    return PatchStatus(patch_id=patch.id, state="not-found")


def _locator_applies(locator: LocatorRecord, image_profile: str | None) -> bool:
    if locator.image_profile is None:
        return True
    return image_profile is not None and locator.image_profile == image_profile


def _locate_with_locator(
    data: bytes,
    patch: PatchRecord,
    locator: LocatorRecord,
    replacement: bytes | None,
    search_ranges: dict[str, tuple[int, int]] | None = None,
) -> PatchStatus:
    if locator.kind == "signature":
        return _signature_status(data, patch, locator, replacement, search_ranges)
    if locator.kind == "fixed":
        return _fixed_status(data, patch, locator, replacement, search_ranges)
    if locator.kind == "content_hash":
        return _content_hash_status(data, patch, locator, replacement, search_ranges)
    if locator.kind == "seed_auth_profile":
        return _seed_auth_profile_status(data, patch, locator, replacement, search_ranges)
    raise ValueError(f"unsupported locator kind {locator.kind!r}")


def _locator_search_ranges(
    locator: LocatorRecord,
    search_ranges: dict[str, tuple[int, int]] | None,
    data_len: int,
) -> tuple[tuple[int, int], ...]:
    if locator.partition:
        if not search_ranges or locator.partition not in search_ranges:
            return ()
        start, end = search_ranges[locator.partition]
    else:
        start, end = 0, data_len
    start = max(0, start)
    end = min(end, data_len)
    if locator.search_max is not None:
        end = min(end, locator.search_max)
    if end <= start:
        return ()
    return ((start, end),)


def _content_hash_status(
    data: bytes,
    patch: PatchRecord,
    locator: LocatorRecord,
    replacement: bytes | None,
    search_ranges: dict[str, tuple[int, int]] | None,
) -> PatchStatus:
    if locator.original_sha256 is None:
        raise ValueError(f"content_hash locator for {patch.id} has no original_sha256")

    replace_len = locator.length or locator.replace_len or patch.replacement_size
    matches: list[int] = []
    for start, end in _locator_search_ranges(locator, search_ranges, len(data)):
        scan_end = max(start, end - replace_len)
        for offset in range(start, scan_end + 1):
            chunk = data[offset : offset + replace_len]
            if len(chunk) != replace_len:
                continue
            if hashlib.sha256(chunk).hexdigest() == locator.original_sha256:
                matches.append(offset)

    matches = sorted(set(matches))
    if not matches:
        return PatchStatus(patch_id=patch.id, state="not-found")

    if len(matches) > 1:
        return PatchStatus(
            patch_id=patch.id,
            state="ambiguous",
            detail=f"content_hash matched multiple offsets: {', '.join(hex(o) for o in matches)}",
        )

    offset = matches[0]
    current = data[offset : offset + replace_len]
    return _status_for_current(
        patch,
        offset,
        replace_len,
        current,
        replacement,
        locator.original_sha256,
        detail_prefix="content_hash",
    )


def _seed_auth_profile_status(
    data: bytes,
    patch: PatchRecord,
    locator: LocatorRecord,
    replacement: bytes | None,
    search_ranges: dict[str, tuple[int, int]] | None,
) -> PatchStatus:
    profile = locator.profile or "hedgehog"
    replace_len = locator.length or locator.replace_len or patch.replacement_size
    original_sha256 = locator.original_sha256 or _patch_original_sha256(patch)

    offset: int | None = None
    for start, end in _locator_search_ranges(locator, search_ranges, len(data)):
        offset = _resolve_seed_auth_profile_offset(data, start, end, profile, replace_len)
        if offset is not None:
            break

    if offset is None:
        return PatchStatus(
            patch_id=patch.id,
            state="not-found",
            detail=f"seed_auth profile {profile!r} not found",
        )

    current = data[offset : offset + replace_len]
    return _status_for_current(
        patch,
        offset,
        replace_len,
        current,
        replacement,
        original_sha256,
        detail_prefix=f"seed_auth:{profile}",
    )


def _status_for_current(
    patch: PatchRecord,
    offset: int,
    replace_len: int,
    current: bytes,
    replacement: bytes | None,
    original_sha256: str | None,
    detail_prefix: str,
) -> PatchStatus:
    digest = hashlib.sha256(current).hexdigest()
    if replacement is not None and current == replacement:
        state = "patched"
    elif original_sha256 is not None and digest == original_sha256:
        state = "original"
    elif replacement is None and patch.dynamic is not None:
        state = "patched-unverified"
    else:
        state = "conflict"
    detail = f"{detail_prefix}; sha256={digest}"
    if state == "patched-unverified":
        detail += (
            "; dynamic site differs from original but no key blob was provided,"
            " so this may be a custom key or unrelated/corrupt data"
            " (provide the key blob to confirm)"
        )
    return PatchStatus(
        patch_id=patch.id,
        state=state,
        offset=offset,
        replace_len=replace_len,
        current=current,
        original=None,
        replacement=replacement,
        detail=detail,
    )


def _patch_original_sha256(patch: PatchRecord) -> str | None:
    for locator in patch.locators:
        if locator.original_sha256 is not None:
            return locator.original_sha256
    return None


def _find_seed_auth_profile_table(
    data: bytes,
    start: int,
    end: int,
    key_size: int,
) -> int | None:
    marker = b"chocolate\x00"
    for off in range(start, max(start, end - 32)):
        if data[off : off + len(marker)] != marker:
            continue
        table = off
        valid = True
        for index, name in enumerate(SEED_AUTH_PROFILES):
            record = table + index * SEED_AUTH_PROFILE_RECORD_SIZE
            name_end = record + len(name) + 1
            if data[record:name_end] != name + b"\x00":
                valid = False
                break
            if struct.unpack_from("<Q", data, record + SEED_AUTH_KEY_SIZE_OFFSET)[0] != key_size:
                valid = False
                break
        if valid:
            valid = _seed_auth_profile_pointers_valid(data, table, key_size)
        if valid:
            return table
    return None


def _seed_auth_profile_pointers_valid(data: bytes, table: int, key_size: int) -> bool:
    previous_low: int | None = None
    for index in range(len(SEED_AUTH_PROFILES)):
        low = (
            struct.unpack_from(
                "<Q",
                data,
                table + index * SEED_AUTH_PROFILE_RECORD_SIZE + SEED_AUTH_KEY_POINTER_OFFSET,
            )[0]
            & 0xFFFFFFFF
        )
        if previous_low is not None and low - previous_low != key_size:
            return False
        previous_low = low
    return True


def _resolve_seed_auth_profile_offset(
    data: bytes,
    start: int,
    end: int,
    profile: str,
    key_size: int,
) -> int | None:
    table = _find_seed_auth_profile_table(data, start, end, key_size)
    if table is None:
        return None

    try:
        profile_index = [item.decode() for item in SEED_AUTH_PROFILES].index(profile)
    except ValueError as exc:
        raise ValueError(f"unsupported seed_auth profile {profile!r}") from exc

    chocolate_low = (
        struct.unpack_from("<Q", data, table + SEED_AUTH_KEY_POINTER_OFFSET)[0] & 0xFFFFFFFF
    )

    max_rel = end - start - len(SEED_AUTH_PROFILES) * key_size
    if max_rel < 0:
        return None

    for rel_base in range(0, max_rel + 1):
        if data[start + rel_base : start + rel_base + len(SEED_AUTH_AVB_KEY_MAGIC)] != SEED_AUTH_AVB_KEY_MAGIC:
            continue
        key_constant = chocolate_low - rel_base
        matched = True
        target_offset: int | None = None
        for index in range(len(SEED_AUTH_PROFILES)):
            profile_low = (
                struct.unpack_from(
                    "<Q",
                    data,
                    table + index * SEED_AUTH_PROFILE_RECORD_SIZE + SEED_AUTH_KEY_POINTER_OFFSET,
                )[0]
                & 0xFFFFFFFF
            )
            key_offset = start + (profile_low - key_constant)
            if not start <= key_offset <= end - key_size:
                matched = False
                break
            if index == profile_index:
                target_offset = key_offset
                continue
            if data[key_offset : key_offset + len(SEED_AUTH_AVB_KEY_MAGIC)] != SEED_AUTH_AVB_KEY_MAGIC:
                matched = False
                break
        if matched and target_offset is not None:
            return target_offset
    return None


def _signature_status(
    data: bytes,
    patch: PatchRecord,
    locator: LocatorRecord,
    replacement: bytes | None,
    search_ranges: dict[str, tuple[int, int]] | None,
) -> PatchStatus:
    assert locator.needle is not None
    replace_len = locator.replace_len or patch.replacement_size
    original = _locator_original(locator, patch, replace_len)
    effective_replacement = locator.patch or replacement
    variants: list[tuple[str, bytes, bytes | None]] = [("original", locator.needle, original)]
    patched_needle = _patched_needle(locator, replace_len, effective_replacement)
    if patched_needle is not None and patched_needle != locator.needle:
        variants.append(("patched", patched_needle, effective_replacement))

    candidates: list[tuple[str, int, bytes | None, bytes | None]] = []
    for range_start, range_end in _locator_search_ranges(
        locator,
        search_ranges,
        len(data),
    ):
        haystack = data[range_start:range_end]
        for state, needle, expected in variants:
            for match in _find_all(haystack, needle):
                candidates.append(
                    (
                        state,
                        range_start + match + locator.replace_at,
                        original,
                        expected,
                    )
                )

    if not candidates:
        return PatchStatus(patch_id=patch.id, state="not-found")

    candidates = _dedupe_candidates(candidates)
    if len(candidates) > 1:
        offsets = [offset for _state, offset, _original, _expected in candidates]
        return PatchStatus(
            patch_id=patch.id,
            state="ambiguous",
            detail=f"signature matched multiple offsets: {', '.join(hex(o) for o in offsets)}",
        )

    _needle_state, offset, original, expected = candidates[0]
    if original is None or len(original) != replace_len:
        return PatchStatus(
            patch_id=patch.id,
            state="conflict",
            offset=offset,
            replace_len=replace_len,
            current=data[offset : offset + replace_len],
            original=original,
            replacement=effective_replacement,
            detail="signature locator does not contain replacement bytes and has no original_hex",
        )
    current = data[offset : offset + replace_len]
    if effective_replacement is not None and current == effective_replacement:
        state = "patched"
    elif current == original:
        state = "original"
    else:
        state = "conflict"
    return PatchStatus(
        patch_id=patch.id,
        state=state,
        offset=offset,
        replace_len=replace_len,
        current=current,
        original=original,
        replacement=effective_replacement,
    )


def _fixed_status(
    data: bytes,
    patch: PatchRecord,
    locator: LocatorRecord,
    replacement: bytes | None,
    search_ranges: dict[str, tuple[int, int]] | None,
) -> PatchStatus:
    if locator.offset is None:
        raise ValueError(f"fixed locator for {patch.id} has no offset")
    replace_len = locator.length or locator.replace_len or patch.replacement_size
    if locator.partition and not any(
        start <= locator.offset and locator.offset + replace_len <= end
        for start, end in _locator_search_ranges(locator, search_ranges, len(data))
    ):
        return PatchStatus(patch_id=patch.id, state="not-found")
    current = data[locator.offset : locator.offset + replace_len]
    original = locator.original

    if locator.original_sha256 is not None:
        digest = hashlib.sha256(current).hexdigest()
        if replacement is not None and current == replacement:
            state = "patched"
        elif digest == locator.original_sha256:
            state = "original"
        elif replacement is None and patch.dynamic is not None:
            state = "patched-unverified"
        else:
            state = "conflict"
        detail = f"sha256={digest}"
        if state == "patched-unverified":
            detail += (
                "; dynamic site differs from original but no key blob was provided,"
                " so this may be a custom key or unrelated/corrupt data"
                " (provide the key blob to confirm)"
            )
        return PatchStatus(
            patch_id=patch.id,
            state=state,
            offset=locator.offset,
            replace_len=replace_len,
            current=current,
            original=None,
            replacement=replacement,
            detail=detail,
        )

    if original is None:
        raise ValueError(f"fixed locator for {patch.id} has no original bytes or hash")
    if replacement is not None and current == replacement:
        state = "patched"
    elif current == original:
        state = "original"
    else:
        state = "conflict"
    return PatchStatus(
        patch_id=patch.id,
        state=state,
        offset=locator.offset,
        replace_len=replace_len,
        current=current,
        original=original,
        replacement=replacement,
    )


def _known_offset_status(
    data: bytes,
    patch: PatchRecord,
    replacement: bytes | None,
) -> PatchStatus | None:
    variants = _known_variants(patch, replacement)
    original_sha256 = _patch_original_sha256(patch)
    for offset in patch.known_offsets.values():
        for original, candidate_replacement, replace_len in variants:
            current = data[offset : offset + replace_len]
            if candidate_replacement is not None and current == candidate_replacement:
                return PatchStatus(
                    patch_id=patch.id,
                    state="patched",
                    offset=offset,
                    replace_len=replace_len,
                    current=current,
                    original=original,
                    replacement=candidate_replacement,
                    detail="matched replacement at known offset",
                )
            if original is not None and len(original) == replace_len and current == original:
                return PatchStatus(
                    patch_id=patch.id,
                    state="original",
                    offset=offset,
                    replace_len=replace_len,
                    current=current,
                    original=original,
                    replacement=candidate_replacement,
                    detail="matched original at known offset",
                )
            if (
                original_sha256 is not None
                and len(current) == replace_len
                and hashlib.sha256(current).hexdigest() == original_sha256
            ):
                return PatchStatus(
                    patch_id=patch.id,
                    state="original",
                    offset=offset,
                    replace_len=replace_len,
                    current=current,
                    original=original,
                    replacement=candidate_replacement,
                    detail="matched original sha256 at known offset",
                )
    return None


def _known_variants(
    patch: PatchRecord,
    replacement: bytes | None,
) -> tuple[tuple[bytes | None, bytes | None, int], ...]:
    variants: list[tuple[bytes | None, bytes | None, int]] = []
    for locator in patch.locators:
        replace_len = locator.length or locator.replace_len or patch.replacement_size
        candidate_replacement = locator.patch or replacement
        original = _locator_original(locator, patch, replace_len)
        if original is not None or candidate_replacement is not None:
            variants.append((original, candidate_replacement, replace_len))
    if not variants:
        variants.append((None, replacement, patch.replacement_size))
    return tuple(variants)


def _locator_original(
    locator: LocatorRecord,
    patch: PatchRecord,
    replace_len: int,
) -> bytes | None:
    if locator.original is not None:
        return locator.original
    if locator.needle is None:
        return None
    original = locator.needle[locator.replace_at : locator.replace_at + replace_len]
    if len(original) != replace_len:
        return None
    return original


def _patched_needle(
    locator: LocatorRecord,
    replace_len: int,
    replacement: bytes | None,
) -> bytes | None:
    if locator.needle is None or replacement is None or len(replacement) != replace_len:
        return None
    start = locator.replace_at
    end = start + replace_len
    if start < 0 or end > len(locator.needle):
        return None
    return locator.needle[:start] + replacement + locator.needle[end:]


def _dedupe_candidates(
    candidates: list[tuple[str, int, bytes | None, bytes | None]],
) -> list[tuple[str, int, bytes | None, bytes | None]]:
    seen: set[tuple[str, int, bytes | None, bytes | None]] = set()
    out: list[tuple[str, int, bytes | None, bytes | None]] = []
    for candidate in candidates:
        key = candidate
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _narrow_to_known_offsets(
    candidates: list[tuple[str, int, bytes | None, bytes | None]],
    patch: PatchRecord,
) -> list[tuple[str, int, bytes | None, bytes | None]]:
    known = set(patch.known_offsets.values())
    if not known:
        return candidates
    narrowed = [candidate for candidate in candidates if candidate[1] in known]
    return narrowed or candidates


def _static_patch_or_none(patch: PatchRecord) -> bytes | None:
    if patch.patch_hex is None:
        return None
    return patch.patch_bytes


def _find_all(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        index = data.find(needle, start)
        if index < 0:
            return offsets
        offsets.append(index)
        start = index + 1
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
