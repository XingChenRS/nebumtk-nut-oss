# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from nebumtk_unlock_tools.core.errors import VerificationError


ROOT = Path(__file__).resolve().parents[1]
REVIEWED_BUILD = (
    ROOT
    / ".private"
    / "task3-upstream"
    / "sakisu-host-static-target"
    / "release"
    / "ksud.exe"
)


def test_reviewed_windows_host_identity_and_interface_are_exact() -> None:
    from nebumtk_unlock_tools.sakisu.host_importer import (
        OFFICIAL_WINDOWS_HOST,
        load_imported_host_patcher,
    )

    from nebumtk_unlock_tools.sakisu.locations import (
        ASSET_HOST,
        missing_assets_message,
        sakisu_dir,
    )

    directory = sakisu_dir(ROOT, needs=ASSET_HOST)
    if not (directory / "official-patcher-windows-x64.exe").is_file():
        pytest.skip(missing_assets_message(directory))
    snapshot, manifest = load_imported_host_patcher(ROOT)

    assert OFFICIAL_WINDOWS_HOST.source_commit == "6f9672837b9359f8853f47e18e00261edbf6d31e"
    assert snapshot.size == 3_819_520
    assert snapshot.sha256 == "1b8a6100b301700ec00b3c78f5afcab953960a30322b9f1479f283abb95805b3"
    assert manifest["patcher"]["target"] == "x86_64-pc-windows-msvc"
    assert manifest["patcher"]["crt"] == "static"
    assert manifest["patcher"]["supported_kmis"] == list(
        OFFICIAL_WINDOWS_HOST.supported_kmis
    )
    raw = snapshot.path.read_bytes()
    assert b"C:\\Users" not in raw
    assert b".private" not in raw
    assert b"VCRUNTIME140.dll" not in raw
    assert b"api-ms-win-crt-" not in raw


def test_import_reviewed_windows_host_writes_only_exact_public_assets(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu.host_importer import (
        import_reviewed_host_patcher,
        load_imported_host_patcher,
    )

    if not REVIEWED_BUILD.is_file():
        pytest.skip("reviewed static build material not present under .private/")
    output = tmp_path / "sakisu"
    manifest = import_reviewed_host_patcher(REVIEWED_BUILD, output)

    assert {path.name for path in output.iterdir()} == {
        "official-patcher-windows-x64.exe",
        "official-patcher-windows-x64-manifest.json",
    }
    assert json.loads(
        (output / "official-patcher-windows-x64-manifest.json").read_text(
            encoding="utf-8"
        )
    ) == manifest
    snapshot, loaded = load_imported_host_patcher(tmp_path)
    assert loaded == manifest
    assert snapshot.sha256 == manifest["patcher"]["sha256"]


@pytest.mark.parametrize("mutation", ("binary", "manifest", "extra"))
def test_windows_host_loader_rejects_every_asset_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    from nebumtk_unlock_tools.sakisu.host_importer import load_imported_host_patcher

    from nebumtk_unlock_tools.sakisu.locations import (
        ASSET_HOST,
        missing_assets_message,
        sakisu_dir,
    )

    source = sakisu_dir(ROOT, needs=ASSET_HOST)
    if not (source / "official-patcher-windows-x64.exe").is_file():
        pytest.skip(missing_assets_message(source))
    shutil.copytree(source, tmp_path / "sakisu")
    if mutation == "binary":
        path = tmp_path / "sakisu" / "official-patcher-windows-x64.exe"
        path.write_bytes(path.read_bytes() + b"tampered")
    elif mutation == "manifest":
        path = tmp_path / "sakisu" / "official-patcher-windows-x64-manifest.json"
        path.write_text("{}\n", encoding="utf-8")
    else:
        (tmp_path / "sakisu" / "unreviewed.exe").write_bytes(b"MZ")

    with pytest.raises(VerificationError):
        load_imported_host_patcher(tmp_path)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
