from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.core.vivo_wipe_misc import (
    BCB_OFFSET,
    BCB_SIZE,
    VIVO_WIPE_MISC_SIZE,
    rewrite_vivo_wipe_misc_target,
    validate_vivo_wipe_misc,
)


def _make_wipe_misc(target_suffix: str) -> bytes:
    image = bytearray(VIVO_WIPE_MISC_SIZE)
    image[: len(b"boot-recovery\0")] = b"boot-recovery\0"
    recovery = b"recovery\n--wipe_data\n--reason=wipe_data_from_ota\n\0"
    image[64 : 64 + len(recovery)] = recovery
    control = bytearray(BCB_SIZE)
    control[:4] = target_suffix.encode("ascii") + b"\0\0"
    control[4:8] = b"BCAB"
    control[8] = 1
    control[9] = 2
    struct.pack_into("<I", control, 28, zlib.crc32(control[:28]) & 0xFFFFFFFF)
    image[BCB_OFFSET : BCB_OFFSET + BCB_SIZE] = control
    return bytes(image)


def test_validate_vivo_wipe_misc_accepts_matching_slot_and_crc(tmp_path: Path) -> None:
    path = tmp_path / "to_b.img"
    path.write_bytes(_make_wipe_misc("_b"))

    snapshot = validate_vivo_wipe_misc(path, "_b")

    assert snapshot.path == path
    assert snapshot.size == VIVO_WIPE_MISC_SIZE
    with pytest.raises(VerificationError, match="目标槽"):
        validate_vivo_wipe_misc(path, "_a")


def test_validate_vivo_wipe_misc_rejects_invalid_bcb_crc(tmp_path: Path) -> None:
    path = tmp_path / "bad-crc.img"
    image = bytearray(_make_wipe_misc("_a"))
    image[BCB_OFFSET + 10] = 1
    path.write_bytes(image)

    with pytest.raises(VerificationError, match="CRC"):
        validate_vivo_wipe_misc(path, "_a")


def test_validate_vivo_wipe_misc_rejects_non_wipe_boot_command(tmp_path: Path) -> None:
    path = tmp_path / "bad-command.img"
    image = bytearray(_make_wipe_misc("_a"))
    image[: len(b"boot-fastboot\0")] = b"boot-fastboot\0"
    path.write_bytes(image)

    with pytest.raises(VerificationError, match="boot-recovery"):
        validate_vivo_wipe_misc(path, "_a")


def test_rewrite_vivo_wipe_misc_changes_only_target_slot_and_bcb_crc(tmp_path: Path) -> None:
    source = _make_wipe_misc("_a")

    rewritten = rewrite_vivo_wipe_misc_target(source, "_b")
    path = tmp_path / "to_b.img"
    path.write_bytes(rewritten)

    assert validate_vivo_wipe_misc(path, "_b").size == VIVO_WIPE_MISC_SIZE
    changed = [index for index, (before, after) in enumerate(zip(source, rewritten)) if before != after]
    assert changed[0] == BCB_OFFSET + 1
    assert set(changed[1:]) <= set(range(BCB_OFFSET + 28, BCB_OFFSET + 32))
