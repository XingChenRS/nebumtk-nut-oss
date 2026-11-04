"""Classifier and structural verifier for SakiSU boot/init_boot images."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import lzma
import stat
import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from ..core.errors import VerificationError


MAX_PATCH_GROWTH = 16 * 1024 * 1024
_MAX_IMAGE_SIZE = 256 * 1024 * 1024
_ROOT_MARKERS = (b"magisk", b"apatch", b"kernelsu", b"ksuinit", b"sakisu", b"sukisu", b"resukisu")
# The reviewed v4.3.0 SakiSU patcher installs the generic LKM at the
# ramdisk root.  Keep this exact rather than accepting any matching .ko.
_TARGET_LKM_PATH = "kernelsu.ko"
_KSU_MODULE_MARKERS = (
    b"name=kernelsu",
    b"name=ksu",
    b"kernelsu",
    b"ksuinit",
    b"sakisu",
    b"sukisu",
    b"resukisu",
)


class BootClassification(str, Enum):
    APPARENT_STOCK = "apparent_stock"
    SAKISU = "sakisu"
    OTHER_KSU = "other_ksu"
    UNKNOWN_MODIFIED = "unknown_modified"


@dataclass(frozen=True)
class CpioEntry:
    name: str
    mode: int
    data: bytes


@dataclass(frozen=True)
class ParsedBootImage:
    header_version: int
    ramdisk: bytes
    entries: tuple[CpioEntry, ...]
    avb_original_image_size: int
    avb_vbmeta_offset: int
    avb_vbmeta_size: int


@dataclass(frozen=True)
class VerifiedPatchCandidate:
    classification: BootClassification
    source_size: int
    source_sha256: str
    output_size: int
    output_sha256: str


def _align(value: int, alignment: int) -> int:
    if alignment <= 0:
        raise VerificationError("boot header alignment is invalid")
    return (value + alignment - 1) // alignment * alignment


def _read_image(path: Path) -> bytes:
    try:
        size = Path(path).stat().st_size
    except FileNotFoundError as exc:
        raise VerificationError("boot image is missing") from exc
    if size <= 0 or size > _MAX_IMAGE_SIZE:
        raise VerificationError("boot image size is invalid")
    return Path(path).read_bytes()


def _ramdisk_range(image: bytes) -> tuple[int, int, int]:
    if len(image) < 64 or image[:8] != b"ANDROID!":
        raise VerificationError("boot header magic is invalid")
    header_version = struct.unpack_from("<I", image, 40)[0]
    if header_version > 4:
        raise VerificationError("boot header version is unsupported")
    if header_version >= 3:
        kernel_size, ramdisk_size, _, header_size = struct.unpack_from("<IIII", image, 8)
        if header_size < 44 or header_size > 4096:
            raise VerificationError("boot header size is invalid")
        page_size = 4096
        ramdisk_offset = _align(header_size, page_size) + _align(kernel_size, page_size)
    else:
        kernel_size = struct.unpack_from("<I", image, 8)[0]
        ramdisk_size = struct.unpack_from("<I", image, 16)[0]
        page_size = struct.unpack_from("<I", image, 36)[0]
        if page_size < 512 or page_size > 65_536 or page_size & (page_size - 1):
            raise VerificationError("boot header page size is invalid")
        ramdisk_offset = page_size + _align(kernel_size, page_size)
    ramdisk_end = ramdisk_offset + ramdisk_size
    if ramdisk_size <= 0 or ramdisk_offset < 0 or ramdisk_end > len(image):
        raise VerificationError("boot header ramdisk range is invalid")
    return header_version, ramdisk_offset, ramdisk_end


def _verify_avb_footer(image: bytes) -> tuple[int, int, int]:
    if len(image) < 64 or image[-64:-60] != b"AVBf":
        raise VerificationError("AVB footer is missing")
    major, minor, original_size, vbmeta_offset, vbmeta_size = struct.unpack_from(">IIQQQ", image, len(image) - 60)
    footer_offset = len(image) - 64
    if major != 1 or minor > 1:
        raise VerificationError("AVB footer version is invalid")
    if original_size <= 0 or original_size > footer_offset:
        raise VerificationError("AVB original image range is invalid")
    if vbmeta_size <= 0 or vbmeta_offset < original_size or vbmeta_offset > footer_offset:
        raise VerificationError("AVB vbmeta range is invalid")
    if vbmeta_size > footer_offset - vbmeta_offset:
        raise VerificationError("AVB vbmeta range is invalid")
    if image[vbmeta_offset : vbmeta_offset + 4] != b"AVB0":
        raise VerificationError("AVB vbmeta header is invalid")
    return original_size, vbmeta_offset, vbmeta_size


def _decompress_ramdisk(value: bytes) -> bytes:
    try:
        if value.startswith(b"\x1f\x8b"):
            return gzip.decompress(value)
        if value.startswith(b"BZh"):
            return bz2.decompress(value)
        if value.startswith(b"\xfd7zXZ\x00"):
            return lzma.decompress(value)
        if value.startswith(b"\x04\x22\x4d\x18"):
            import lz4.frame

            return lz4.frame.decompress(value)
        if value.startswith(b"\x02\x21\x4c\x18"):
            import lz4.block

            output = bytearray()
            cursor = 4
            while cursor < len(value):
                if cursor + 4 > len(value):
                    raise ValueError("truncated legacy LZ4 block")
                block_size = struct.unpack_from("<I", value, cursor)[0]
                cursor += 4
                if block_size == 0:
                    break
                if block_size > len(value) - cursor:
                    raise ValueError("legacy LZ4 block range")
                output.extend(
                    lz4.block.decompress(
                        value[cursor : cursor + block_size],
                        uncompressed_size=8 * 1024 * 1024,
                    )
                )
                cursor += block_size
            return bytes(output)
        if value.startswith((b"070701", b"070702")):
            return value
    except (OSError, EOFError, ValueError, lzma.LZMAError) as exc:
        raise VerificationError("ramdisk decompression failed") from exc
    raise VerificationError("ramdisk compression is unsupported")


def _parse_cpio(value: bytes) -> tuple[CpioEntry, ...]:
    entries: list[CpioEntry] = []
    cursor = 0
    trailer_seen = False
    while cursor < len(value):
        if len(value) - cursor < 110:
            if not value[cursor:].strip(b"\0") and trailer_seen:
                break
            raise VerificationError("ramdisk cpio header is truncated")
        header = value[cursor : cursor + 110]
        if header[:6] not in {b"070701", b"070702"}:
            raise VerificationError("ramdisk cpio header magic is invalid")
        try:
            fields = [int(header[6 + index * 8 : 14 + index * 8], 16) for index in range(13)]
        except ValueError as exc:
            raise VerificationError("ramdisk cpio header fields are invalid") from exc
        mode = fields[1]
        file_size = fields[6]
        name_size = fields[11]
        if name_size <= 0 or name_size > 4096 or file_size > _MAX_IMAGE_SIZE:
            raise VerificationError("ramdisk cpio entry size is invalid")
        name_start = cursor + 110
        name_end = name_start + name_size
        if name_end > len(value) or value[name_end - 1] != 0:
            raise VerificationError("ramdisk cpio entry name is invalid")
        try:
            name = value[name_start : name_end - 1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VerificationError("ramdisk cpio entry name is not UTF-8") from exc
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not name
            or path.as_posix() != name
        ):
            raise VerificationError("ramdisk cpio entry path is unsafe")
        data_start = _align(name_end, 4)
        data_end = data_start + file_size
        if data_end > len(value):
            raise VerificationError("ramdisk cpio entry data is truncated")
        cursor = _align(data_end, 4)
        if name == "TRAILER!!!":
            if file_size != 0:
                raise VerificationError("ramdisk cpio trailer is invalid")
            trailer_seen = True
            break
        entries.append(CpioEntry(name=name, mode=mode, data=value[data_start:data_end]))
    if not trailer_seen:
        raise VerificationError("ramdisk cpio trailer is missing")
    if value[cursor:].strip(b"\0"):
        raise VerificationError("ramdisk cpio has data after its trailer")
    return tuple(entries)


def parse_boot_image(path: Path) -> ParsedBootImage:
    image = _read_image(path)
    version, ramdisk_start, ramdisk_end = _ramdisk_range(image)
    original_size, vbmeta_offset, vbmeta_size = _verify_avb_footer(image)
    if ramdisk_end > original_size:
        raise VerificationError("boot header ramdisk overlaps AVB ranges")
    ramdisk = _decompress_ramdisk(image[ramdisk_start:ramdisk_end])
    entries = _parse_cpio(ramdisk)
    return ParsedBootImage(
        header_version=version,
        ramdisk=ramdisk,
        entries=entries,
        avb_original_image_size=original_size,
        avb_vbmeta_offset=vbmeta_offset,
        avb_vbmeta_size=vbmeta_size,
    )


def _entry_map(parsed: ParsedBootImage) -> dict[str, list[CpioEntry]]:
    by_name: dict[str, list[CpioEntry]] = {}
    for entry in parsed.entries:
        by_name.setdefault(entry.name, []).append(entry)
    return by_name


def _ksu_modules(parsed: ParsedBootImage) -> list[CpioEntry]:
    return [
        entry
        for entry in parsed.entries
        if entry.name.casefold().endswith(".ko")
        and any(
            marker
            in (
                entry.name.encode("utf-8", "ignore")
                + b"\0"
                + entry.data
            ).lower()
            for marker in _KSU_MODULE_MARKERS
        )
    ]


def _is_regular(entry: CpioEntry) -> bool:
    return stat.S_IFMT(entry.mode) == stat.S_IFREG


def _is_executable(entry: CpioEntry) -> bool:
    return bool(entry.mode & 0o111)


def _is_aarch64_elf(entry: CpioEntry, *, allowed_types: frozenset[int]) -> bool:
    if len(entry.data) < 64 or entry.data[:7] != b"\x7fELF\x02\x01\x01":
        return False
    elf_type, machine, version = struct.unpack_from("<HHI", entry.data, 16)
    return elf_type in allowed_types and machine == 183 and version == 1


def _looks_sakisu(parsed: ParsedBootImage) -> bool:
    by_name = _entry_map(parsed)
    modules = _ksu_modules(parsed)
    if len(by_name.get("init", [])) != 1 or len(by_name.get("init.real", [])) != 1:
        return False
    if len(modules) != 1 or modules[0].name != _TARGET_LKM_PATH:
        return False
    init_entry = by_name["init"][0]
    init_real_entry = by_name["init.real"][0]
    module_entry = modules[0]
    if not (
        _is_regular(init_entry)
        and _is_executable(init_entry)
        and _is_aarch64_elf(init_entry, allowed_types=frozenset({2, 3}))
        and _is_regular(init_real_entry)
        and _is_executable(init_real_entry)
        and _is_aarch64_elf(init_real_entry, allowed_types=frozenset({2, 3}))
        and _is_regular(module_entry)
        and _is_aarch64_elf(module_entry, allowed_types=frozenset({1}))
    ):
        return False
    init = init_entry.data.lower()
    module = module_entry.data.lower()
    return (
        b"ksuinit" in init
        and b"sakisu" in module
        and b"name=kernelsu" in module
        and b"vermagic=" in module
        and b"resukisu" not in init + module
    )


def _classify(parsed: ParsedBootImage) -> BootClassification:
    if _looks_sakisu(parsed):
        return BootClassification.SAKISU
    combined = b"\0".join(
        entry.name.encode("utf-8", "ignore") + b"\0" + entry.data
        for entry in parsed.entries
    ).lower()
    if any(marker in combined for marker in (b"kernelsu", b"ksuinit", b"sakisu", b"sukisu", b"resukisu")):
        return BootClassification.OTHER_KSU
    by_name = _entry_map(parsed)
    if (
        len(by_name.get("init", [])) == 1
        and not by_name.get("init.real")
        and not any(marker in combined for marker in _ROOT_MARKERS)
    ):
        return BootClassification.APPARENT_STOCK
    return BootClassification.UNKNOWN_MODIFIED


def _validate_partition(partition: str) -> str:
    normalized = str(partition).strip().lower()
    if normalized == "vendor_boot":
        raise VerificationError("vendor_boot is not accepted by the SakiSU patch-only path")
    if normalized not in {"boot", "init_boot"}:
        raise VerificationError("SakiSU patch-only partition must be boot or init_boot")
    return normalized


def classify_boot_image(path: Path, *, partition: str) -> BootClassification:
    _validate_partition(partition)
    return _classify(parse_boot_image(path))


def verify_patch_candidate(
    source: Path,
    candidate: Path,
    *,
    partition: str,
) -> VerifiedPatchCandidate:
    _validate_partition(partition)
    source_bytes = _read_image(source)
    output_bytes = _read_image(candidate)
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    output_hash = hashlib.sha256(output_bytes).hexdigest()
    if source_hash == output_hash:
        raise VerificationError("SakiSU candidate is byte-identical to the source")
    if len(output_bytes) > len(source_bytes) + MAX_PATCH_GROWTH:
        raise VerificationError("SakiSU candidate growth exceeds the bounded allowance")
    parse_boot_image(source)
    parsed = parse_boot_image(candidate)
    by_name = _entry_map(parsed)
    if len(by_name.get("init", [])) != 1:
        raise VerificationError("SakiSU candidate must contain one coherent init")
    if len(by_name.get("init.real", [])) != 1:
        raise VerificationError("SakiSU candidate must contain one coherent init.real")
    init = by_name["init"][0]
    init_real = by_name["init.real"][0]
    for label, entry in (("init", init), ("init.real", init_real)):
        if not _is_regular(entry):
            raise VerificationError(f"SakiSU candidate {label} must be a regular file")
        if not _is_executable(entry):
            raise VerificationError(f"SakiSU candidate {label} must be executable")
        if not _is_aarch64_elf(entry, allowed_types=frozenset({2, 3})):
            raise VerificationError(f"SakiSU candidate {label} must be an AArch64 ELF executable")
    target_modules = by_name.get(_TARGET_LKM_PATH, [])
    if len(target_modules) != 1:
        raise VerificationError("SakiSU candidate LKM must use one canonical path")
    target_module = target_modules[0]
    modules = _ksu_modules(parsed)
    if len(modules) != 1 or modules[0] is not target_module:
        raise VerificationError("SakiSU candidate has a mixed KSU LKM identity")
    if not _is_regular(target_module):
        raise VerificationError("SakiSU candidate LKM must be a regular file")
    if not _is_aarch64_elf(target_module, allowed_types=frozenset({1})):
        raise VerificationError("SakiSU candidate LKM must be an AArch64 ELF relocatable")
    expected_identity = (init.data + b"\0" + target_module.data).lower()
    # The reviewed SakiSU v4.3 module contains a legacy diagnostic string
    # mentioning SukiSU.  ReSukiSU remains a distinct, rejected identity;
    # SakiSU itself is required below by the exact classifier.
    if b"resukisu" in expected_identity:
        raise VerificationError("SakiSU candidate has mixed KSU identity metadata")
    classification = _classify(parsed)
    if classification is not BootClassification.SAKISU:
        raise VerificationError("SakiSU candidate does not contain the expected generic LKM/ksuinit identity")
    return VerifiedPatchCandidate(
        classification=classification,
        source_size=len(source_bytes),
        source_sha256=source_hash,
        output_size=len(output_bytes),
        output_sha256=output_hash,
    )
