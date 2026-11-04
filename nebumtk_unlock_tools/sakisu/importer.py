"""Strict importer for the reviewed official SakiSU Android arm64 patcher."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import VerificationError
from ..core.operation_files import VerifiedFileSnapshot


_APK_SIG_MAGIC = b"APK Sig Block 42"
_SIGNER_BLOCK_IDS = frozenset({0x7109871A, 0xF05368C0, 0x1B93AD61})
_PATCHER_FILE = "official-patcher-arm64"
_MANIFEST_FILE = "official-patcher-manifest.json"


@dataclass(frozen=True)
class OfficialApkSpec:
    repository: str
    tag: str
    asset_name: str
    apk_size: int
    apk_sha256: str
    certificate_sha256: str
    patcher_member: str
    patcher_size: int
    patcher_sha256: str
    interface_markers: tuple[bytes, ...]
    supported_kmis: tuple[str, ...]


OFFICIAL_APK = OfficialApkSpec(
    repository="XingChenRS/SakiSU",
    tag="v4.3.0-sakisu.1",
    asset_name="SakiSU_v4.3.0-sakisu.1_35039-arm64-v8a-release.apk",
    apk_size=8_486_473,
    apk_sha256="269f43a4232eacc3dcff657e7a9e1de1697e0e9732b032aa63c3034bc5b3a0ea",
    certificate_sha256="f5391718452b269336a4cf0077b2fc4f9ae91253f061126a2708d5017134287c",
    patcher_member="lib/arm64-v8a/libksud.so",
    patcher_size=4_727_056,
    patcher_sha256="53e432f1b2d2b1ac1cfc69a6fbdeddc1e91ce148ed77dc20ceb0afcba7991dd4",
    interface_markers=(
        b"boot-patch",
        b"out-name",
        b"partition",
        b"init_boot",
        b"ksuinit",
        b"kernelsu.ko",
    ),
    supported_kmis=(
        "android12-5.10",
        "android13-5.10",
        "android13-5.15",
        "android14-5.15",
        "android14-6.1",
        "android15-6.6",
        "android16-6.12",
    ),
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_eocd(apk: bytes) -> int:
    minimum = max(0, len(apk) - (65_535 + 22))
    cursor = len(apk)
    while True:
        offset = apk.rfind(b"PK\x05\x06", minimum, cursor)
        if offset < 0:
            raise VerificationError("APK signing certificate block is missing")
        if offset + 22 <= len(apk):
            comment_size = struct.unpack_from("<H", apk, offset + 20)[0]
            if offset + 22 + comment_size == len(apk):
                return offset
        cursor = offset


def _length_prefixed(value: memoryview, offset: int, end: int, *, label: str) -> tuple[memoryview, int]:
    if offset < 0 or offset + 4 > end:
        raise VerificationError(f"APK signing certificate {label} is truncated")
    length = struct.unpack_from("<I", value, offset)[0]
    start = offset + 4
    stop = start + length
    if length > end - start or stop > end:
        raise VerificationError(f"APK signing certificate {label} length is invalid")
    return value[start:stop], stop


def _certificates_from_signer_block(raw: bytes) -> tuple[bytes, ...]:
    block = memoryview(raw)
    signers, signers_end = _length_prefixed(block, 0, len(block), label="signers")
    if signers_end != len(block):
        raise VerificationError("APK signing certificate signers have trailing data")
    certificates: list[bytes] = []
    cursor = 0
    while cursor < len(signers):
        signer, cursor = _length_prefixed(signers, cursor, len(signers), label="signer")
        signed_data, _ = _length_prefixed(signer, 0, len(signer), label="signed data")
        _, signed_cursor = _length_prefixed(signed_data, 0, len(signed_data), label="digests")
        cert_sequence, _ = _length_prefixed(
            signed_data,
            signed_cursor,
            len(signed_data),
            label="certificate sequence",
        )
        cert_cursor = 0
        signer_certificates: list[bytes] = []
        while cert_cursor < len(cert_sequence):
            certificate, cert_cursor = _length_prefixed(
                cert_sequence,
                cert_cursor,
                len(cert_sequence),
                label="certificate",
            )
            if not certificate:
                raise VerificationError("APK signing certificate is empty")
            signer_certificates.append(bytes(certificate))
        if not signer_certificates:
            raise VerificationError("APK signing certificate sequence is empty")
        certificates.append(signer_certificates[0])
    return tuple(certificates)


def apk_certificate_sha256(apk: bytes) -> str:
    eocd = _find_eocd(apk)
    central_offset = struct.unpack_from("<I", apk, eocd + 16)[0]
    if central_offset < 32 or central_offset > eocd:
        raise VerificationError("APK signing certificate central directory range is invalid")
    footer = apk[central_offset - 24 : central_offset]
    if len(footer) != 24 or footer[8:] != _APK_SIG_MAGIC:
        raise VerificationError("APK signing certificate block is missing")
    block_size = struct.unpack_from("<Q", footer, 0)[0]
    if block_size < 24 or block_size > central_offset - 8:
        raise VerificationError("APK signing certificate block size is invalid")
    block_start = central_offset - (block_size + 8)
    if block_start < 0:
        raise VerificationError("APK signing certificate block range is invalid")
    leading_size = struct.unpack_from("<Q", apk, block_start)[0]
    if leading_size != block_size:
        raise VerificationError("APK signing certificate block size mismatch")

    cursor = block_start + 8
    pairs_end = central_offset - 24
    certificates: list[bytes] = []
    while cursor < pairs_end:
        if cursor + 8 > pairs_end:
            raise VerificationError("APK signing certificate pair is truncated")
        pair_size = struct.unpack_from("<Q", apk, cursor)[0]
        cursor += 8
        if pair_size < 4 or pair_size > pairs_end - cursor:
            raise VerificationError("APK signing certificate pair size is invalid")
        pair_id = struct.unpack_from("<I", apk, cursor)[0]
        value_start = cursor + 4
        value_end = cursor + pair_size
        if pair_id in _SIGNER_BLOCK_IDS:
            certificates.extend(_certificates_from_signer_block(apk[value_start:value_end]))
        cursor = value_end
    if cursor != pairs_end or not certificates:
        raise VerificationError("APK signing certificate signer is missing")
    digests = {_sha256_bytes(certificate) for certificate in certificates}
    if len(digests) != 1:
        raise VerificationError("APK signing certificate set is ambiguous")
    return next(iter(digests))


def _verify_elf_interface(patcher: bytes, spec: OfficialApkSpec) -> dict[str, object]:
    if len(patcher) < 64 or patcher[:4] != b"\x7fELF":
        raise VerificationError("patcher elf header is missing")
    if patcher[4] != 2 or patcher[5] != 1 or patcher[6] != 1:
        raise VerificationError("patcher elf class or endianness is invalid")
    elf_type, machine, version = struct.unpack_from("<HHI", patcher, 16)
    if elf_type != 3 or machine != 183 or version != 1:
        raise VerificationError("patcher elf is not an AArch64 shared executable")
    missing = [marker for marker in spec.interface_markers if marker not in patcher]
    if missing:
        raise VerificationError("patcher interface identity is incomplete")
    supported_kmis = tuple(
        sorted(
            {
                value.decode("ascii")
                for value in re.findall(
                    rb"(android\d{2}-\d+\.\d+)_kernelsu\.ko",
                    patcher,
                )
            }
        )
    )
    if supported_kmis != tuple(sorted(spec.supported_kmis)):
        raise VerificationError("patcher interface supported KMI set is invalid")
    return {
        "class": 64,
        "endianness": "little",
        "machine": "AArch64",
        "type": "DYN",
    }


def _manifest(spec: OfficialApkSpec, elf: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "upstream": {
            "repository": spec.repository,
            "tag": spec.tag,
            "asset_name": spec.asset_name,
            "apk_size": spec.apk_size,
            "apk_sha256": spec.apk_sha256,
            "certificate_sha256": spec.certificate_sha256,
        },
        "patcher": {
            "member": spec.patcher_member,
            "size": spec.patcher_size,
            "sha256": spec.patcher_sha256,
            "elf": elf,
            "interface": "ksud-boot-patch-v4",
            "supported_kmis": list(spec.supported_kmis),
        },
    }


def import_official_apk(
    apk_path: Path,
    output_directory: Path,
    *,
    spec: OfficialApkSpec = OFFICIAL_APK,
) -> dict[str, object]:
    apk_path = Path(apk_path)
    try:
        actual_size = apk_path.stat().st_size
    except FileNotFoundError as exc:
        raise VerificationError(f"official APK is missing: {spec.asset_name}") from exc
    if actual_size != spec.apk_size:
        raise VerificationError(f"official APK size mismatch: expected {spec.apk_size}, got {actual_size}")
    apk = apk_path.read_bytes()
    if _sha256_bytes(apk) != spec.apk_sha256:
        raise VerificationError("official APK hash mismatch")
    if apk_certificate_sha256(apk) != spec.certificate_sha256:
        raise VerificationError("official APK certificate mismatch")

    try:
        with zipfile.ZipFile(apk_path) as archive:
            matches = [entry for entry in archive.infolist() if entry.filename == spec.patcher_member]
            if len(matches) != 1:
                raise VerificationError("official APK patcher member must occur exactly once")
            entry = matches[0]
            if entry.file_size != spec.patcher_size:
                raise VerificationError("official APK patcher member size mismatch")
            patcher = archive.read(entry)
    except zipfile.BadZipFile as exc:
        raise VerificationError("official APK ZIP structure is invalid") from exc
    if len(patcher) != spec.patcher_size:
        raise VerificationError("official APK patcher member size mismatch")
    if _sha256_bytes(patcher) != spec.patcher_sha256:
        raise VerificationError("official APK patcher member hash mismatch")
    elf = _verify_elf_interface(patcher, spec)
    manifest = _manifest(spec, elf)

    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex
    patcher_pending = output_directory / f".{_PATCHER_FILE}.{suffix}.pending"
    manifest_pending = output_directory / f".{_MANIFEST_FILE}.{suffix}.pending"
    patcher_destination = output_directory / _PATCHER_FILE
    manifest_destination = output_directory / _MANIFEST_FILE
    try:
        patcher_pending.write_bytes(patcher)
        if patcher_pending.stat().st_size != spec.patcher_size or _sha256_file(patcher_pending) != spec.patcher_sha256:
            raise VerificationError("imported patcher write verification failed")
        try:
            os.chmod(patcher_pending, 0o600)
        except OSError:
            pass
        manifest_pending.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        patcher_pending.replace(patcher_destination)
        manifest_pending.replace(manifest_destination)
    except Exception:
        patcher_pending.unlink(missing_ok=True)
        manifest_pending.unlink(missing_ok=True)
        raise
    return manifest


def load_imported_patcher(project_root: Path) -> tuple[VerifiedFileSnapshot, dict[str, object]]:
    directory = Path(project_root) / "sakisu"
    patcher = directory / _PATCHER_FILE
    manifest_path = directory / _MANIFEST_FILE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError("official SakiSU import manifest is missing or invalid") from exc
    expected = _manifest(OFFICIAL_APK, _verify_elf_interface(patcher.read_bytes(), OFFICIAL_APK))
    if manifest != expected:
        raise VerificationError("official SakiSU import manifest identity mismatch")
    size = patcher.stat().st_size
    digest = _sha256_file(patcher)
    snapshot = VerifiedFileSnapshot(patcher, size, digest)
    validate_imported_patcher(snapshot)
    return snapshot, manifest


def validate_imported_patcher(snapshot: VerifiedFileSnapshot) -> None:
    """Bind a runtime patcher snapshot to the reviewed official member identity."""

    if type(snapshot) is not VerifiedFileSnapshot:
        raise VerificationError("typed official SakiSU imported patcher is required")
    try:
        patcher = snapshot.path.read_bytes()
    except OSError as exc:
        raise VerificationError("official SakiSU imported patcher is missing") from exc
    size = len(patcher)
    digest = _sha256_bytes(patcher)
    if size != snapshot.size or digest != snapshot.sha256:
        raise VerificationError("official SakiSU imported patcher snapshot changed")
    if size != OFFICIAL_APK.patcher_size or digest != OFFICIAL_APK.patcher_sha256:
        raise VerificationError("official SakiSU imported patcher identity mismatch")
    _verify_elf_interface(patcher, OFFICIAL_APK)
