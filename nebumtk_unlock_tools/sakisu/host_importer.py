"""Pinned importer for the reviewed Windows x64 SakiSU host patcher."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import VerificationError
from ..core.operation_files import VerifiedFileSnapshot


_HOST_FILE = "official-patcher-windows-x64.exe"
_HOST_MANIFEST_FILE = "official-patcher-windows-x64-manifest.json"
_ALLOWED_ASSET_NAMES = frozenset(
    {
        "official-patcher-arm64",
        "official-patcher-manifest.json",
        _HOST_FILE,
        _HOST_MANIFEST_FILE,
    }
)


@dataclass(frozen=True, slots=True)
class OfficialWindowsHostSpec:
    repository: str
    tag: str
    source_commit: str
    target: str
    rustc: str
    size: int
    sha256: str
    supported_kmis: tuple[str, ...]
    embedded_assets: tuple[tuple[str, int, str], ...]


OFFICIAL_WINDOWS_HOST = OfficialWindowsHostSpec(
    repository="XingChenRS/SakiSU",
    tag="v4.3.0-sakisu.1",
    source_commit="6f9672837b9359f8853f47e18e00261edbf6d31e",
    target="x86_64-pc-windows-msvc",
    rustc="1.94.0 (4a4ef493e 2026-03-02)",
    size=3_819_520,
    sha256="1b8a6100b301700ec00b3c78f5afcab953960a30322b9f1479f283abb95805b3",
    supported_kmis=(
        "android12-5.10",
        "android13-5.10",
        "android13-5.15",
        "android14-5.15",
        "android14-6.1",
        "android15-6.6",
        "android16-6.12",
    ),
    embedded_assets=(
        (
            "android12-5.10_kernelsu.ko",
            391_352,
            "a41195080a91c9f5f67155e4596b05b33196c799a10e36d6493bbbc6ef3f9a40",
        ),
        (
            "android13-5.10_kernelsu.ko",
            382_056,
            "4e1e057b1b7b612b1990c4a379c7fee379db6e9ea618d7044315abde491d4177",
        ),
        (
            "android13-5.15_kernelsu.ko",
            413_448,
            "8f5420bbaff355c9d46d7f44576980640e55a990f5abd8a5a27d69084af55d2b",
        ),
        (
            "android14-5.15_kernelsu.ko",
            535_136,
            "e1d1f7e3676c82169e57ca7c932a04a3f017ca72e422f124f4a84a9249695933",
        ),
        (
            "android14-6.1_kernelsu.ko",
            440_360,
            "bb57db115eb51645b498d33897b1f572870dba470651ada224a2e26338d018a0",
        ),
        (
            "android15-6.6_kernelsu.ko",
            344_024,
            "51289c33e59dc45b03c0f945126170cfba68f2a6444b098033367e14a4b7dd30",
        ),
        (
            "android16-6.12_kernelsu.ko",
            415_720,
            "221ea2645013c4e2dfd627b4bb1b1542f67598ff2473d9616d567c6c7a3ee088",
        ),
        (
            "ksuinit",
            425_584,
            "de7a913cefd5c6683ab38f3e8bf8732c6b4b5c5baa683b59c4297c9c49dfe02a",
        ),
    ),
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verify_windows_interface(
    value: bytes,
    spec: OfficialWindowsHostSpec,
) -> dict[str, object]:
    if len(value) != spec.size or _sha256(value) != spec.sha256:
        raise VerificationError("official SakiSU Windows patcher identity mismatch")
    if value[:2] != b"MZ" or len(value) < 0x100:
        raise VerificationError("official SakiSU Windows patcher PE header is missing")
    pe_offset = struct.unpack_from("<I", value, 0x3C)[0]
    if pe_offset < 0x40 or pe_offset + 26 > len(value):
        raise VerificationError("official SakiSU Windows patcher PE range is invalid")
    if value[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise VerificationError("official SakiSU Windows patcher PE signature is invalid")
    machine = struct.unpack_from("<H", value, pe_offset + 4)[0]
    optional_magic = struct.unpack_from("<H", value, pe_offset + 24)[0]
    if machine != 0x8664 or optional_magic != 0x20B:
        raise VerificationError("official SakiSU Windows patcher is not x64 PE32+")
    for marker in (b"boot-patch", b"supported-kmis", b"ksuinit", b"kernelsu.ko"):
        if marker not in value:
            raise VerificationError("official SakiSU Windows patcher interface is incomplete")
    kmis = tuple(
        sorted(
            {
                match.decode("ascii")
                for match in re.findall(rb"(android\d{2}-\d+\.\d+)_kernelsu\.ko", value)
            }
        )
    )
    if kmis != tuple(sorted(spec.supported_kmis)):
        raise VerificationError("official SakiSU Windows patcher KMI set is invalid")
    if b"C:\\Users" in value or b".private" in value:
        raise VerificationError("official SakiSU Windows patcher leaks a build path")
    return {"format": "PE32+", "machine": "AMD64"}


def _manifest(
    spec: OfficialWindowsHostSpec,
    executable: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "upstream": {
            "repository": spec.repository,
            "tag": spec.tag,
            "source_commit": spec.source_commit,
        },
        "patcher": {
            "file": _HOST_FILE,
            "size": spec.size,
            "sha256": spec.sha256,
            "target": spec.target,
            "crt": "static",
            "rustc": spec.rustc,
            "executable": executable,
            "interface": "ksud-boot-patch-v4",
            "supported_kmis": list(spec.supported_kmis),
        },
        "embedded_assets": [
            {"name": name, "size": size, "sha256": digest}
            for name, size, digest in spec.embedded_assets
        ],
    }


def _validate_directory_entries(directory: Path) -> None:
    try:
        names = {entry.name for entry in directory.iterdir()}
    except OSError as exc:
        raise VerificationError("official SakiSU asset directory is missing") from exc
    if not names <= _ALLOWED_ASSET_NAMES:
        raise VerificationError("official SakiSU asset directory has unexpected files")


def import_reviewed_host_patcher(
    executable_path: Path,
    output_directory: Path,
    *,
    spec: OfficialWindowsHostSpec = OFFICIAL_WINDOWS_HOST,
) -> dict[str, object]:
    executable_path = Path(executable_path)
    try:
        value = executable_path.read_bytes()
    except OSError as exc:
        raise VerificationError("reviewed SakiSU Windows build is missing") from exc
    executable = _verify_windows_interface(value, spec)
    manifest = _manifest(spec, executable)

    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    _validate_directory_entries(output_directory)
    suffix = uuid.uuid4().hex
    binary_pending = output_directory / f".{_HOST_FILE}.{suffix}.pending"
    manifest_pending = output_directory / f".{_HOST_MANIFEST_FILE}.{suffix}.pending"
    try:
        binary_pending.write_bytes(value)
        if _sha256(binary_pending.read_bytes()) != spec.sha256:
            raise VerificationError("SakiSU Windows patcher write verification failed")
        manifest_pending.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(binary_pending, 0o700)
        except OSError:
            pass
        binary_pending.replace(output_directory / _HOST_FILE)
        manifest_pending.replace(output_directory / _HOST_MANIFEST_FILE)
    finally:
        binary_pending.unlink(missing_ok=True)
        manifest_pending.unlink(missing_ok=True)
    return manifest


def load_imported_host_patcher(
    project_root: Path,
) -> tuple[VerifiedFileSnapshot, dict[str, object]]:
    from .locations import missing_assets_message, sakisu_dir

    directory = sakisu_dir(project_root, needs=_HOST_FILE)
    _validate_directory_entries(directory)
    executable_path = directory / _HOST_FILE
    manifest_path = directory / _HOST_MANIFEST_FILE
    try:
        value = executable_path.read_bytes()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(missing_assets_message(directory)) from exc
    executable = _verify_windows_interface(value, OFFICIAL_WINDOWS_HOST)
    if manifest != _manifest(OFFICIAL_WINDOWS_HOST, executable):
        raise VerificationError("official SakiSU Windows patcher manifest mismatch")
    snapshot = VerifiedFileSnapshot(
        executable_path,
        len(value),
        _sha256(value),
    )
    validate_imported_host_patcher(snapshot)
    return snapshot, manifest


def validate_imported_host_patcher(snapshot: VerifiedFileSnapshot) -> None:
    if type(snapshot) is not VerifiedFileSnapshot:
        raise VerificationError("typed official SakiSU Windows patcher is required")
    try:
        value = snapshot.path.read_bytes()
    except OSError as exc:
        raise VerificationError("official SakiSU Windows patcher is missing") from exc
    if len(value) != snapshot.size or _sha256(value) != snapshot.sha256:
        raise VerificationError("official SakiSU Windows patcher snapshot changed")
    _verify_windows_interface(value, OFFICIAL_WINDOWS_HOST)
