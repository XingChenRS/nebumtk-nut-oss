# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
import struct
import warnings
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from nebumtk_unlock_tools.core.errors import VerificationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _elf(*, machine: int = 183, markers: tuple[bytes, ...] = ()) -> bytes:
    header = bytearray(64)
    header[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHI", header, 16, 3, machine, 1)
    return bytes(header) + b"\0".join(markers)


def _lp(value: bytes) -> bytes:
    return struct.pack("<I", len(value)) + value


def _apk_signing_block(certificate: bytes) -> bytes:
    certificates = _lp(certificate)
    signed_data = _lp(b"") + _lp(certificates) + _lp(b"")
    signer = _lp(signed_data) + _lp(b"") + _lp(b"public-key")
    value = _lp(_lp(signer))
    pair = struct.pack("<Q", 4 + len(value)) + struct.pack("<I", 0x7109871A) + value
    size = len(pair) + 24
    return (
        struct.pack("<Q", size)
        + pair
        + struct.pack("<Q", size)
        + b"APK Sig Block 42"
    )


def _signed_apk(
    path: Path,
    *,
    member: str,
    patcher: bytes,
    certificate: bytes = b"approved-certificate",
    duplicate_member: bool = False,
) -> bytes:
    stream = BytesIO()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name:")
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(member, patcher)
            if duplicate_member:
                archive.writestr(member, patcher)
            archive.writestr("assets/unrelated", b"ignored")
    raw = bytearray(stream.getvalue())
    eocd = raw.rfind(b"PK\x05\x06")
    assert eocd >= 0
    central_offset = struct.unpack_from("<I", raw, eocd + 16)[0]
    block = _apk_signing_block(certificate)
    raw[central_offset:central_offset] = block
    struct.pack_into("<I", raw, eocd + len(block) + 16, central_offset + len(block))
    path.write_bytes(raw)
    return bytes(raw)


def _spec(apk: bytes, patcher: bytes, certificate: bytes):
    from nebumtk_unlock_tools.sakisu.importer import OfficialApkSpec

    return OfficialApkSpec(
        repository="XingChenRS/SakiSU",
        tag="v4.3.0-sakisu.1",
        asset_name="fixture.apk",
        apk_size=len(apk),
        apk_sha256=hashlib.sha256(apk).hexdigest(),
        certificate_sha256=hashlib.sha256(certificate).hexdigest(),
        patcher_member="lib/arm64-v8a/libksud.so",
        patcher_size=len(patcher),
        patcher_sha256=hashlib.sha256(patcher).hexdigest(),
        interface_markers=(b"boot-patch", b"--out-name", b"--partition", b"ksuinit"),
        supported_kmis=("android13-5.10",),
    )


def test_official_apk_and_patcher_identity_is_exactly_pinned() -> None:
    from nebumtk_unlock_tools.sakisu.importer import OFFICIAL_APK

    assert OFFICIAL_APK.repository == "XingChenRS/SakiSU"
    assert OFFICIAL_APK.tag == "v4.3.0-sakisu.1"
    assert OFFICIAL_APK.asset_name == "SakiSU_v4.3.0-sakisu.1_35039-arm64-v8a-release.apk"
    assert OFFICIAL_APK.apk_size == 8_486_473
    assert OFFICIAL_APK.apk_sha256 == "269f43a4232eacc3dcff657e7a9e1de1697e0e9732b032aa63c3034bc5b3a0ea"
    assert OFFICIAL_APK.certificate_sha256 == "f5391718452b269336a4cf0077b2fc4f9ae91253f061126a2708d5017134287c"
    assert OFFICIAL_APK.patcher_member == "lib/arm64-v8a/libksud.so"
    assert OFFICIAL_APK.patcher_size == 4_727_056
    assert OFFICIAL_APK.patcher_sha256 == "53e432f1b2d2b1ac1cfc69a6fbdeddc1e91ce148ed77dc20ceb0afcba7991dd4"
    assert OFFICIAL_APK.supported_kmis == (
        "android12-5.10",
        "android13-5.10",
        "android13-5.15",
        "android14-5.15",
        "android14-6.1",
        "android15-6.6",
        "android16-6.12",
    )


def test_importer_extracts_only_reviewed_member_and_writes_public_manifest(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu.importer import import_official_apk

    certificate = b"approved-certificate"
    patcher = _elf(
        markers=(
            b"boot-patch",
            b"--out-name",
            b"--partition",
            b"ksuinit",
            b"android13-5.10_kernelsu.ko",
        )
    )
    apk_path = tmp_path / "fixture.apk"
    apk = _signed_apk(
        apk_path,
        member="lib/arm64-v8a/libksud.so",
        patcher=patcher,
        certificate=certificate,
    )
    output = tmp_path / "imported"

    manifest = import_official_apk(apk_path, output, spec=_spec(apk, patcher, certificate))

    assert sorted(path.name for path in output.iterdir()) == [
        "official-patcher-arm64",
        "official-patcher-manifest.json",
    ]
    assert (output / "official-patcher-arm64").read_bytes() == patcher
    decoded = json.loads((output / "official-patcher-manifest.json").read_text(encoding="utf-8"))
    assert decoded == manifest
    assert decoded["patcher"]["elf"] == {"class": 64, "endianness": "little", "machine": "AArch64", "type": "DYN"}
    assert decoded["patcher"]["interface"] == "ksud-boot-patch-v4"
    assert decoded["patcher"]["supported_kmis"] == ["android13-5.10"]
    serialized = json.dumps(decoded, sort_keys=True).casefold()
    assert "fixture.apk" in serialized
    assert str(tmp_path).casefold() not in serialized
    assert "private" not in serialized and "secret" not in serialized


@pytest.mark.parametrize("failure", ["size", "hash", "certificate", "member", "duplicate", "elf", "interface"])
def test_importer_rejects_every_identity_boundary(tmp_path: Path, failure: str) -> None:
    from dataclasses import replace

    from nebumtk_unlock_tools.sakisu.importer import import_official_apk

    certificate = b"approved-certificate"
    markers = (
        b"boot-patch",
        b"--out-name",
        b"--partition",
        b"ksuinit",
        b"android13-5.10_kernelsu.ko",
    )
    patcher = _elf(machine=62 if failure == "elf" else 183, markers=markers[:-1] if failure == "interface" else markers)
    member = "lib/arm64-v8a/wrong.so" if failure == "member" else "lib/arm64-v8a/libksud.so"
    apk_path = tmp_path / "fixture.apk"
    apk = _signed_apk(
        apk_path,
        member=member,
        patcher=patcher,
        certificate=certificate,
        duplicate_member=failure == "duplicate",
    )
    spec = _spec(apk, patcher, certificate)
    if failure == "size":
        spec = replace(spec, apk_size=len(apk) + 1)
    elif failure == "hash":
        spec = replace(spec, apk_sha256="0" * 64)
    elif failure == "certificate":
        spec = replace(spec, certificate_sha256="0" * 64)
    elif failure == "member":
        spec = replace(spec, patcher_member="lib/arm64-v8a/libksud.so")
    elif failure == "duplicate":
        spec = replace(spec, patcher_size=len(patcher), patcher_sha256=hashlib.sha256(patcher).hexdigest())
    elif failure == "elf":
        spec = replace(spec, patcher_size=len(patcher), patcher_sha256=hashlib.sha256(patcher).hexdigest())
    elif failure == "interface":
        spec = replace(spec, patcher_size=len(patcher), patcher_sha256=hashlib.sha256(patcher).hexdigest())

    with pytest.raises(VerificationError, match=failure if failure != "duplicate" else "member"):
        import_official_apk(apk_path, tmp_path / "imported", spec=spec)
    assert not (tmp_path / "imported" / "official-patcher-arm64").exists()
    assert not (tmp_path / "imported" / "official-patcher-manifest.json").exists()


def test_committed_android_patcher_matches_import_manifest() -> None:
    from nebumtk_unlock_tools.sakisu.locations import (
        ASSET_ARM64,
        missing_assets_message,
        sakisu_dir,
    )

    store = sakisu_dir(PROJECT_ROOT, needs=ASSET_ARM64)
    if not (store / ASSET_ARM64).is_file():
        pytest.skip(missing_assets_message(store))
    from nebumtk_unlock_tools.sakisu.importer import OFFICIAL_APK, load_imported_patcher

    snapshot, manifest = load_imported_patcher(PROJECT_ROOT)

    assert snapshot.size == OFFICIAL_APK.patcher_size
    assert snapshot.sha256 == OFFICIAL_APK.patcher_sha256
    assert manifest["upstream"]["apk_sha256"] == OFFICIAL_APK.apk_sha256
    assert manifest["upstream"]["certificate_sha256"] == OFFICIAL_APK.certificate_sha256
    assert manifest["patcher"]["supported_kmis"] == list(OFFICIAL_APK.supported_kmis)
    assert "path" not in json.dumps(manifest, sort_keys=True).casefold()
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
