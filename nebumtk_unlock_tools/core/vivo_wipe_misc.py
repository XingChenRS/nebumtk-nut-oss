# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Validate the fixed-format Vivo misc image used for post-unlock data wipe."""

from __future__ import annotations

import hashlib
import stat
import struct
import zlib
from pathlib import Path

from .errors import VerificationError
from .operation_files import VerifiedFileSnapshot


VIVO_WIPE_MISC_SIZE = 512 * 1024
COMMAND_OFFSET = 0
COMMAND_SIZE = 32
RECOVERY_OFFSET = 64
RECOVERY_SIZE = 768
BCB_OFFSET = 2048
BCB_SIZE = 32
_RECOVERY_COMMAND = b"recovery\n--wipe_data\n--reason=wipe_data_from_ota\n"
_FORBIDDEN_TOKENS = (b"fastboot", b"bootloader", b"set_active")


def validate_vivo_wipe_misc(path: Path, target_suffix: str) -> VerifiedFileSnapshot:
    """Return a verified snapshot of a target-slot data-wipe misc image."""

    target = _normalize_slot(target_suffix)
    source = Path(path)
    try:
        metadata = source.lstat()
    except FileNotFoundError:
        raise VerificationError("Vivo wipe misc 不存在") from None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise VerificationError("Vivo wipe misc 必须是普通文件")
    if metadata.st_size != VIVO_WIPE_MISC_SIZE:
        raise VerificationError("Vivo wipe misc 长度无效")
    data = source.read_bytes()
    _validate_vivo_wipe_misc_data(data, target)

    return VerifiedFileSnapshot(
        path=source,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def rewrite_vivo_wipe_misc_target(data: bytes, target_suffix: str) -> bytes:
    """Return a vetted wipe misc with only its BCB target slot and CRC changed."""

    target = _normalize_slot(target_suffix)
    control = data[BCB_OFFSET : BCB_OFFSET + BCB_SIZE]
    source_slot = _slot_from_control(control)
    _validate_vivo_wipe_misc_data(data, source_slot)
    rewritten = bytearray(data)
    rewritten_control = bytearray(control)
    rewritten_control[:4] = target.encode("ascii") + b"\0\0"
    struct.pack_into(
        "<I",
        rewritten_control,
        28,
        zlib.crc32(rewritten_control[:28]) & 0xFFFFFFFF,
    )
    rewritten[BCB_OFFSET : BCB_OFFSET + BCB_SIZE] = rewritten_control
    result = bytes(rewritten)
    _validate_vivo_wipe_misc_data(result, target)
    return result


def _normalize_slot(value: str) -> str:
    suffix = str(value).strip().lower()
    if suffix not in {"_a", "_b"}:
        raise VerificationError("Vivo wipe misc 目标槽无效")
    return suffix


def _validate_vivo_wipe_misc_data(data: bytes, target_suffix: str) -> None:
    if len(data) != VIVO_WIPE_MISC_SIZE:
        raise VerificationError("Vivo wipe misc 读取长度无效")
    _validate_recovery_message(data)
    _validate_control_block(data, target_suffix)
    for token in _FORBIDDEN_TOKENS:
        if token in data:
            raise VerificationError(f"Vivo wipe misc 含禁止指令: {token.decode('ascii')}")


def _validate_recovery_message(data: bytes) -> None:
    command = _c_string(data[COMMAND_OFFSET : COMMAND_OFFSET + COMMAND_SIZE])
    if command != b"boot-recovery":
        raise VerificationError("Vivo wipe misc 必须使用 boot-recovery")
    recovery = _c_string(data[RECOVERY_OFFSET : RECOVERY_OFFSET + RECOVERY_SIZE])
    if recovery != _RECOVERY_COMMAND:
        raise VerificationError("Vivo wipe misc recovery 参数无效")


def _validate_control_block(data: bytes, target_suffix: str) -> None:
    control = data[BCB_OFFSET : BCB_OFFSET + BCB_SIZE]
    if len(control) != BCB_SIZE:
        raise VerificationError("Vivo wipe misc BCB 长度无效")
    if _slot_from_control(control) != target_suffix:
        raise VerificationError("Vivo wipe misc BCB 目标槽不匹配")
    if control[4:8] != b"BCAB" or control[8] != 1:
        raise VerificationError("Vivo wipe misc BCB 格式无效")
    expected_crc = zlib.crc32(control[:28]) & 0xFFFFFFFF
    actual_crc = struct.unpack_from("<I", control, 28)[0]
    if actual_crc != expected_crc:
        raise VerificationError("Vivo wipe misc BCB CRC 无效")


def _slot_from_control(control: bytes) -> str:
    if len(control) < 4 or control[:4] not in {b"_a\0\0", b"_b\0\0"}:
        raise VerificationError("Vivo wipe misc BCB 槽位无效")
    return control[:2].decode("ascii")


def _c_string(field: bytes) -> bytes:
    terminator = field.find(b"\0")
    if terminator < 0:
        raise VerificationError("Vivo wipe misc 字段未终止")
    return field[:terminator]
