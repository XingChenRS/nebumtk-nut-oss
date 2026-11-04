# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import audit_distribution as audit_module


ROOT = Path(__file__).resolve().parent.parent
audit_distribution = audit_module.audit_distribution


def _write_release_fixture(root: Path) -> None:
    import nebumtk_unlock_tools as nut

    # The build must keep the provenance mark inside the executable.
    (root / "NEBUMTK_UNLOCK_TOOLS.exe").write_bytes(
        b"MZ" + nut.PROVENANCE_MARK.encode("utf-8") + bytes(32)
    )
    (root / "LICENSE").write_bytes(b"GNU AFFERO GENERAL PUBLIC LICENSE")
    (root / "NOTICE.md").write_bytes(b"licence map")
    platform_tools = root / "platform-tools"
    platform_tools.mkdir()
    (platform_tools / "adb.exe").write_bytes(b"adb")
    (platform_tools / "fastboot.exe").write_bytes(b"fastboot")
    audit_module.BUILTIN_PLATFORM_TOOL_SHA256 = {
        "adb.exe": audit_module._sha256_bytes(b"adb"),
        "fastboot.exe": audit_module._sha256_bytes(b"fastboot"),
    }
    libusb = (
        root
        / "nebumtk_unlock_tools"
        / "lk"
        / "libusb"
        / "_platform"
        / "_windows"
        / "x64"
        / "libusb-1.0.dll"
    )
    libusb.parent.mkdir(parents=True)
    libusb.write_bytes(b"libusb")
    audit_module.write_release_manifest(root)


def test_builds_exclude_screen_mirroring_and_persistent_root_assets() -> None:
    powershell = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    wine = (ROOT / "scripts" / "build-wine.sh").read_text(encoding="utf-8")

    for script in (powershell, wine):
        lowered = script.casefold()
        assert "scrcpy" not in lowered
        assert "embed_ksu_resources.py" not in lowered
        assert "nebumtk_unlock_tools/ksu" not in lowered
        assert "apk/ksu.apk" not in lowered
        assert "apk/sukisu.apk" not in lowered
        assert "build_device_catalog.py" in script
        assert "embed_preloads.py" in script
        # The open-source build has no password-derived resource generator.
        assert "embed_sakisu_resources.py" not in script
        assert "embed_xingc_resources.py" not in script
        assert "python-flag=no_docstrings" in script
        assert "python-flag=isolated" in script
        assert "python-flag=safe_path" in script
        assert "include-data-files=devices.yaml" not in script
    assert "BUILD_CHANNEL" in powershell
    assert "build channel mismatch" in powershell


def test_powershell_launch_smoke_uses_disposable_copy_and_rechecks_manifest() -> None:
    powershell = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")

    assert "NEBUMTK_BUILD_SMOKE_" in powershell
    assert "Copy-Item -LiteralPath $DistributionRoot -Destination $SmokeRoot" in powershell
    assert "Start-Process -FilePath $SmokeExe" in powershell
    assert "Start-Process -FilePath $ExePath" not in powershell
    assert "python tools/audit_distribution.py $DistributionRoot" in powershell


def test_release_audit_accepts_minimal_complete_distribution(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    audit_distribution(tmp_path)


def test_release_manifest_has_complete_hash_inventory(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    manifest = __import__("json").loads(
        (tmp_path / audit_module.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    paths = {entry["path"] for entry in manifest["files"]}

    assert set(audit_module.BASE_REQUIRED_RELEASE_FILES) <= paths
    assert all(len(entry["sha256"]) == 64 for entry in manifest["files"])
    assert not any("/ksu/" in f"/{path.casefold()}/" for path in paths)
    assert not any(path.casefold().endswith(".nbk") for path in paths)


def test_release_audit_rejects_unsigned_devices_yaml(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    (tmp_path / "devices.yaml").write_bytes(b"devices")
    audit_module.write_release_manifest(tmp_path)

    with pytest.raises(RuntimeError, match="devices.yaml"):
        audit_distribution(tmp_path)


@pytest.mark.parametrize(
    "relative",
    (
        "nebumtk_unlock_tools/ksu/protected/manager-01.nbk",
        "nebumtk_unlock_tools/magisk/manager.bin",
        "copied/KSU.APK",
        "copied/Sukisu.APK",
        "copied/libksud.so",
        "copied/ksud.exe",
        "copied/android16-6.12_kernelsu.ko",
    ),
)
def test_distribution_audit_rejects_persistent_root_artifacts(
    tmp_path: Path,
    relative: str,
) -> None:
    artifact = tmp_path / relative
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"persistent-root-artifact")

    with pytest.raises(RuntimeError, match="Root artifact|forbidden file"):
        audit_distribution(tmp_path)


@pytest.mark.parametrize(
    "relative",
    ("copied/scrcpy/scrcpy.exe", "copied/scrcpy-server"),
)
def test_distribution_audit_rejects_removed_scrcpy_runtime(
    tmp_path: Path,
    relative: str,
) -> None:
    artifact = tmp_path / relative
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"removed-screen-runtime")

    with pytest.raises(RuntimeError, match="removed component|forbidden file"):
        audit_distribution(tmp_path)


def test_distribution_audit_rejects_renamed_plaintext_preload(tmp_path: Path) -> None:
    from tests._payload_material import payload_sources

    sources = payload_sources()
    if not sources:
        pytest.skip("operator payload material not present under preloads/")
    source = next(iter(sources.values()))
    (tmp_path / "runtime-cache.bin").write_bytes(source.read_bytes())

    with pytest.raises(RuntimeError, match="plaintext protected source"):
        audit_distribution(tmp_path)


def test_distribution_audit_rejects_manifest_hash_mismatch(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    (tmp_path / "NEBUMTK_UNLOCK_TOOLS.exe").write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="release manifest hash mismatch"):
        audit_distribution(tmp_path)


def test_distribution_audit_rejects_unlisted_file(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    (tmp_path / "late-extra.dll").write_bytes(b"not-in-manifest")

    with pytest.raises(RuntimeError, match="unlisted release file"):
        audit_distribution(tmp_path)


def test_distribution_audit_rejects_vmp_intermediate(tmp_path: Path) -> None:
    (tmp_path / "NEBUMTK_UNLOCK_TOOLS.vmp.exe").write_bytes(b"intermediate")

    with pytest.raises(RuntimeError, match="VMP intermediate or symbol"):
        audit_distribution(tmp_path)


def test_distribution_audit_script_runs_from_project_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "tools/audit_distribution.py", str(tmp_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_smoke_test_script_imports_project_package_from_project_root() -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "tools/smoke_test.py"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_distribution_audit_requires_the_provenance_mark_in_the_executable(
    tmp_path: Path,
) -> None:
    _write_release_fixture(tmp_path)
    (tmp_path / "NEBUMTK_UNLOCK_TOOLS.exe").write_bytes(b"MZ" + bytes(64))
    audit_module.write_release_manifest(tmp_path)

    with pytest.raises(RuntimeError, match="provenance mark"):
        audit_distribution(tmp_path)


def test_distribution_audit_requires_the_conveyed_licence(tmp_path: Path) -> None:
    _write_release_fixture(tmp_path)
    (tmp_path / "LICENSE").unlink()
    audit_module.write_release_manifest(tmp_path)

    with pytest.raises(RuntimeError, match="required file"):
        audit_distribution(tmp_path)
