# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import base64
import importlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pytest

from nebumtk_unlock_tools.core.errors import ResourceError, VerificationError
from nebumtk_unlock_tools.core.runner import CommandResult


def test_sakisu_release_identity_is_pinned_to_43() -> None:
    sakisu = importlib.import_module("nebumtk_unlock_tools.core.sakisu")

    assert sakisu.SAKISU_RELEASE.tag == "v4.3.0-sakisu.1"
    assert sakisu.SAKISU_RELEASE.commit == "6f9672837b9359f8853f47e18e00261edbf6d31e"
    assert sakisu.SAKISU_RELEASE.manager_cert_sha256 == (
        "f5391718452b269336a4cf0077b2fc4f9ae91253f061126a2708d5017134287c"
    )
    assert sakisu.SAKISU_RELEASE.supported_kmis == (
        "android12-5.10",
        "android13-5.10",
        "android13-5.15",
        "android14-5.15",
        "android14-6.1",
        "android15-6.6",
        "android16-6.12",
    )


def test_legacy_standalone_root_routes_stay_absent(tmp_path) -> None:
    from nebumtk_unlock_tools.config.store import Settings
    from nebumtk_unlock_tools.flows.factory import create_flow

    for flow_kind in ("kernelsu_root", "auto_prepare_unlock"):
        with pytest.raises(ValueError, match="未知 flow_kind"):
            create_flow(flow_kind, tmp_path, tmp_path / "workspace", Settings())


def test_sakisu_patcher_is_copied_from_the_local_material_root(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nebumtk_unlock_tools.resources.loader import ResourceLoader

    patcher = tmp_path / "material" / "sakisu" / "official-patcher-windows-x64.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"MZ host-patcher")
    monkeypatch.setenv("NUT_MATERIAL_ROOT", str(tmp_path / "material"))

    loader = ResourceLoader(tmp_path)
    destination = tmp_path / "operation" / "sakisu-ksud.exe"
    destination.parent.mkdir()

    assert loader.materialize_sakisu_patcher(destination) == destination
    assert destination.read_bytes() == b"MZ host-patcher"


def test_sakisu_patcher_reports_missing_local_material(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nebumtk_unlock_tools.keymaterial import MaterialError
    from nebumtk_unlock_tools.resources.loader import ResourceLoader

    monkeypatch.setenv("NUT_MATERIAL_ROOT", str(tmp_path / "material"))

    loader = ResourceLoader(tmp_path)
    with pytest.raises(MaterialError, match="SakiSU"):
        loader.materialize_sakisu_patcher(tmp_path / "sakisu-ksud.exe")


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ("5.15.94-android13-9-g123", "android13-5.15"),
        ("6.12.0-android16-0-g123", "android16-6.12"),
    ],
)
def test_parse_sakisu_kmi_from_device_kernel_release(release: str, expected: str) -> None:
    from nebumtk_unlock_tools.core.sakisu import parse_sakisu_kmi

    assert parse_sakisu_kmi(release) == expected


def test_parse_sakisu_kmi_rejects_unsupported_release() -> None:
    from nebumtk_unlock_tools.core.sakisu import parse_sakisu_kmi

    with pytest.raises(VerificationError, match="KMI"):
        parse_sakisu_kmi("6.9.0-android17-g123")


def test_adb_kernel_release_uses_non_root_uname(monkeypatch: pytest.MonkeyPatch) -> None:
    from nebumtk_unlock_tools.core.adb import AdbClient

    adb = AdbClient(serial="SERIAL")
    calls: list[tuple[str, dict]] = []

    def fake_shell(command: str, **kwargs) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(0, "5.15.94-android13-9-g123\n", "")

    monkeypatch.setattr(adb, "shell", fake_shell)

    assert adb.kernel_release() == "5.15.94-android13-9-g123"
    assert calls == [("uname -r", {"timeout": 15, "sensitive": True})]


def test_sakisu_patcher_prepares_verified_init_boot_without_shell_bypass(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nebumtk_unlock_tools.core import sakisu
    from nebumtk_unlock_tools.core.operation_files import OperationFiles

    source = tmp_path / "init_boot.img"
    source.write_bytes(b"ANDROID!" + b"\0" * (4096 - 8 - 64) + b"AVBf" + b"\0" * 60)
    files = OperationFiles(tmp_path, "sakisu")
    snapshot = files.copy_input(source, "source_init_boot.img")
    executable = tmp_path / "ksud.exe"
    executable.write_bytes(b"host-patcher")
    commands: list[list[str]] = []

    def fake_run(args, **kwargs) -> CommandResult:
        commands.append(list(args))
        out_dir = args[args.index("--out") + 1]
        out_name = args[args.index("--out-name") + 1]
        (Path(out_dir) / out_name).write_bytes(snapshot.path.read_bytes())
        return CommandResult(0, "- Done!", "")

    monkeypatch.setattr(sakisu, "run_command", fake_run)

    output = sakisu.SakiSUPatcher(executable).prepare(
        snapshot,
        "5.15.94-android13-9-g123",
        files,
    )

    assert output.path.parent == files.directory
    assert output.path.name == "patched_init_boot.img"
    assert output.size == snapshot.size
    assert commands == [
        [
            str(executable),
            "boot-patch",
            "--boot",
            str(snapshot.path),
            "--kmi",
            "android13-5.15",
            "--out",
            str(files.directory),
            "--out-name",
            "patched_init_boot.img",
        ]
    ]


def test_sakisu_patcher_rejects_output_that_drops_source_avb_footer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nebumtk_unlock_tools.core import sakisu
    from nebumtk_unlock_tools.core.operation_files import OperationFiles

    source = tmp_path / "init_boot.img"
    source.write_bytes(b"ANDROID!" + b"\0" * (4096 - 8 - 64) + b"AVBf" + b"\0" * 60)
    files = OperationFiles(tmp_path, "sakisu-avb")
    snapshot = files.copy_input(source, "source_init_boot.img")
    executable = tmp_path / "ksud.exe"
    executable.write_bytes(b"host-patcher")

    def fake_run(args, **kwargs) -> CommandResult:
        out_dir = args[args.index("--out") + 1]
        out_name = args[args.index("--out-name") + 1]
        (Path(out_dir) / out_name).write_bytes(b"ANDROID!" + b"\0" * (4096 - 8))
        return CommandResult(0, "- Done!", "")

    monkeypatch.setattr(sakisu, "run_command", fake_run)

    with pytest.raises(VerificationError, match="AVB"):
        sakisu.SakiSUPatcher(executable).prepare(
            snapshot,
            "5.15.94-android13-9-g123",
            files,
        )
    assert not (files.directory / "patched_init_boot.img").exists()


def test_resource_loader_materializes_sakisu_inside_operation_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nebumtk_unlock_tools.resources.loader import ResourceLoader

    patcher = tmp_path / "sakisu" / "official-patcher-windows-x64.exe"
    patcher.parent.mkdir()
    patcher.write_bytes(b"host-patcher")
    monkeypatch.setenv("NUT_MATERIAL_ROOT", str(tmp_path))
    loader = ResourceLoader(tmp_path)
    destination = tmp_path / "operation" / "sakisu-ksud.exe"
    destination.parent.mkdir()

    assert loader.materialize_sakisu_patcher(destination) == destination
    loader.cleanup_materialized_preloads()
    assert destination.exists()
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
