# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nebumtk_unlock_tools.core import backup_export
from nebumtk_unlock_tools.core.backup_export import (
    BACKUP_MANIFEST_FILENAME,
    load_dyn_backup,
    publish_to_desktop,
    validate_restore_lk_backup,
)
from nebumtk_unlock_tools.core.dyn_backup import DynBackup, DynBackupFile
from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.core.partition import BackupRecord


class _FakeStandardPaths:
    class StandardLocation:
        DesktopLocation = object()

    desktop: Path

    @classmethod
    def writableLocation(cls, _location) -> str:
        return str(cls.desktop)


def _records(tmp_path: Path) -> dict[str, BackupRecord]:
    operation = tmp_path / "operation"
    operation.mkdir()
    lk = operation / "verified_lk.bin"
    init_boot = operation / "verified_init_boot.bin"
    lk.write_bytes(b"original-lk")
    init_boot.write_bytes(b"original-init-boot")
    return {
        "lk": BackupRecord("lk", "lk_a", "/data/local/tmp/lk.bin", lk),
        "init_boot": BackupRecord(
            "init_boot",
            "init_boot_a",
            "/data/local/tmp/init_boot.bin",
            init_boot,
        ),
    }


def _publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _FakeStandardPaths.desktop = desktop
    monkeypatch.setattr(backup_export, "QStandardPaths", _FakeStandardPaths)
    return publish_to_desktop(
        _records(tmp_path),
        model_key="oppo-test",
        serial="SERIAL",
        initial_slot="_a",
    )


def test_publish_writes_fixed_atomic_manifest_with_actual_export_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exported = _publish(tmp_path, monkeypatch)

    assert exported.directory.name.startswith(
        "NEBUMTK_Backup_oppo-test_SERIAL_"
    )
    manifest = json.loads(
        (exported.directory / BACKUP_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert set(manifest) == {
        "schema",
        "schema_version",
        "profile_key",
        "adb_serial",
        "initial_slot",
        "partitions",
        "dyn",
    }
    assert manifest["schema"] == "nebumtk.backup"
    assert manifest["schema_version"] == 2
    assert manifest["dyn"] == []
    assert manifest["profile_key"] == "oppo-test"
    assert manifest["adb_serial"] == "SERIAL"
    assert manifest["initial_slot"] == "_a"
    assert set(manifest["partitions"]) == {"lk", "init_boot"}
    for entry in manifest["partitions"].values():
        target = exported.directory / entry["file"]
        assert entry["size"] == target.stat().st_size
        assert entry["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert not list((tmp_path / "desktop").glob(".*.pending"))
    assert not list(exported.directory.glob("*.pending"))


def test_manifest_restore_validation_binds_profile_serial_filename_size_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exported = _publish(tmp_path, monkeypatch)
    lk = exported.directory / "verified_lk.bin"

    validated = validate_restore_lk_backup(
        lk,
        profile_key="oppo-test",
        fastboot_serial="SERIAL",
    )

    assert validated.size == len(b"original-lk")
    assert validated.sha256 == hashlib.sha256(b"original-lk").hexdigest()
    assert validated.legacy_directory is False

    with pytest.raises(VerificationError, match="机型"):
        validate_restore_lk_backup(
            lk,
            profile_key="other-profile",
            fastboot_serial="SERIAL",
        )
    with pytest.raises(VerificationError, match="设备"):
        validate_restore_lk_backup(
            lk,
            profile_key="oppo-test",
            fastboot_serial="OTHER",
        )

    extra = exported.directory / "other-lk.bin"
    extra.write_bytes(lk.read_bytes())
    with pytest.raises(VerificationError, match="manifest 记录"):
        validate_restore_lk_backup(
            extra,
            profile_key="oppo-test",
            fastboot_serial="SERIAL",
        )

    lk.write_bytes(b"tampered")
    with pytest.raises(VerificationError, match="大小或哈希"):
        validate_restore_lk_backup(
            lk,
            profile_key="oppo-test",
            fastboot_serial="SERIAL",
        )


def test_legacy_restore_requires_exact_profile_and_serial_directory_and_warns(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "NEBUMTK_Backup_oppo-test_SERIAL_20260802-010203"
    directory.mkdir()
    lk = directory / "verified_lk.bin"
    lk.write_bytes(b"legacy-lk")
    logs: list[str] = []

    validated = validate_restore_lk_backup(
        lk,
        profile_key="oppo-test",
        fastboot_serial="SERIAL",
        log=logs.append,
    )

    assert validated.legacy_directory is True
    assert len(logs) == 1
    assert "未包含 manifest" in logs[0]
    assert "SERIAL" not in logs[0]

    with pytest.raises(VerificationError, match="无法确认"):
        validate_restore_lk_backup(
            lk,
            profile_key="oppo-test",
            fastboot_serial="OTHER",
        )


def test_manifest_write_failure_leaves_no_visible_or_pending_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _FakeStandardPaths.desktop = desktop
    monkeypatch.setattr(backup_export, "QStandardPaths", _FakeStandardPaths)
    monkeypatch.setattr(
        backup_export,
        "_write_manifest_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        publish_to_desktop(
            _records(tmp_path),
            model_key="oppo-test",
            serial="SERIAL",
            initial_slot="_a",
        )

    assert list(desktop.iterdir()) == []


def test_publish_copies_optional_dyn_files_into_a_separate_dyn_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _FakeStandardPaths.desktop = desktop
    monkeypatch.setattr(backup_export, "QStandardPaths", _FakeStandardPaths)
    dyn_a = tmp_path / "dyn-a.bin"
    dyn_b = tmp_path / "dyn-b.bin"
    dyn_a.write_bytes(b"dyn-a")
    dyn_b.write_bytes(b"dyn-b")

    exported = publish_to_desktop(
        _records(tmp_path),
        model_key="vivo-test",
        serial="SERIAL",
        initial_slot="_a",
        extra_directories={"dyn": {"a.bin": dyn_a, "b.bin": dyn_b}},
    )

    assert (exported.directory / "dyn" / "a.bin").read_bytes() == b"dyn-a"
    assert (exported.directory / "dyn" / "b.bin").read_bytes() == b"dyn-b"
    manifest = json.loads((exported.directory / BACKUP_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert set(manifest) == {
        "schema",
        "schema_version",
        "profile_key",
        "adb_serial",
        "initial_slot",
        "partitions",
        "dyn",
    }
    assert manifest["dyn"] == []


def test_dyn_manifest_preserves_verified_files_and_metadata_for_later_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _FakeStandardPaths.desktop = desktop
    monkeypatch.setattr(backup_export, "QStandardPaths", _FakeStandardPaths)
    source = tmp_path / "dyn-vivo_state"
    source.write_bytes(b"dyn-state")
    dyn = DynBackup((
        DynBackupFile(
            "vivo_state",
            source,
            source.stat().st_size,
            hashlib.sha256(source.read_bytes()).hexdigest(),
            "600",
            "1000",
            "1000",
        ),
    ))

    exported = publish_to_desktop(
        _records(tmp_path),
        model_key="vivo-test",
        serial="SERIAL",
        initial_slot="_a",
        dyn_backup=dyn,
    )

    manifest = json.loads((exported.directory / BACKUP_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["dyn"] == [
        {
            "name": "vivo_state",
            "file": "dyn/vivo_state",
            "size": len(b"dyn-state"),
            "sha256": hashlib.sha256(b"dyn-state").hexdigest(),
            "mode": "600",
            "uid": "1000",
            "gid": "1000",
        }
    ]

    loaded = load_dyn_backup(
        exported.directory,
        profile_key="vivo-test",
        adb_serial="SERIAL",
    )

    assert loaded.files[0].path == exported.directory / "dyn" / "vivo_state"
    assert loaded.files[0].mode == "600"
    assert loaded.files[0].uid == "1000"
    assert loaded.files[0].gid == "1000"

    loaded.files[0].path.write_bytes(b"tampered")
    with pytest.raises(VerificationError, match="dyn|hash|哈希"):
        load_dyn_backup(
            exported.directory,
            profile_key="vivo-test",
            adb_serial="SERIAL",
        )
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
