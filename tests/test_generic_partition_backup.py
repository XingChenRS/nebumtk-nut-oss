# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebumtk_unlock_tools.core import backup_export, partition as partition_module
from nebumtk_unlock_tools.core.backup_export import (
    BACKUP_MANIFEST_FILENAME,
    publish_to_desktop,
)
from nebumtk_unlock_tools.core.errors import FlowCancelledError, VerificationError
from nebumtk_unlock_tools.core.operation_files import OperationFiles
from nebumtk_unlock_tools.core.partition import (
    PartitionInventory,
    PartitionInventoryEntry,
    PartitionOps,
)
from nebumtk_unlock_tools.core.runner import CommandResult


@dataclass
class BackupAdb:
    payloads: dict[str, bytes]
    remote: dict[str, bytes] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    shell_calls: list[str] = field(default_factory=list)
    cancel_during_pull: bool = False

    def shell(self, command: str, **_kwargs) -> CommandResult:
        self.shell_calls.append(command)
        if command.startswith("dd if="):
            block = command.split("if='", 1)[1].split("'", 1)[0].rsplit("/", 1)[1]
            remote = command.split("of='", 1)[1].split("'", 1)[0]
            self.remote[remote] = self.payloads[block]
        return CommandResult(0, "", "")

    def remote_file_size(self, remote: str, *, root: bool = True) -> int:
        del root
        return len(self.remote[remote])

    def pull(self, remote: str, local: Path, *, cancelled=None) -> None:
        if self.cancel_during_pull:
            raise FlowCancelledError("cancelled during pull")
        local.write_bytes(self.remote[remote])

    def remove_remote(self, remote: str, **_kwargs) -> None:
        self.removed.append(remote)
        self.remote.pop(remote, None)


@dataclass
class CleanupFallbackAdb(BackupAdb):
    fail_nonroot_cleanup: bool = False
    fail_root_cleanup: bool = False
    cleanup_attempts: list[tuple[str, bool]] = field(default_factory=list)

    def remove_remote(
        self,
        remote: str,
        *,
        root: bool = True,
        strict: bool = False,
    ) -> None:
        if remote in self.remote:
            self.cleanup_attempts.append((remote, root))
            if (root and self.fail_root_cleanup) or (
                not root and self.fail_nonroot_cleanup
            ):
                raise RuntimeError(f"{'root' if root else 'non-root'} cleanup failed")
        super().remove_remote(remote, root=root, strict=strict)


def _inventory(*entries: tuple[str, int]) -> PartitionInventory:
    return PartitionInventory(
        tuple(
            PartitionInventoryEntry(
                name=name,
                target=f"/dev/block/{name}",
                size=size,
                base_name=name[:-2] if name.endswith(("_a", "_b")) else name,
                slot_suffix=name[-2:] if name.endswith(("_a", "_b")) else None,
                partner=None,
            )
            for name, size in entries
        )
    )


def test_selected_backup_checks_space_snapshots_each_file_and_cleans_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = {"lk_a": b"lk-a", "vendor_boot_a": b"vendor-boot-a"}
    adb = BackupAdb(payloads)
    operation = OperationFiles(tmp_path, "generic-backup")
    ops = PartitionOps(adb, SimpleNamespace(), operation.directory)
    monkeypatch.setattr(
        partition_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1000, used=0, free=1000),
    )

    records = ops.backup_selected(
        _inventory(*((name, len(payload)) for name, payload in payloads.items())),
        ["vendor_boot_a", "lk_a"],
        operation_files=operation,
    )

    assert list(records) == ["vendor_boot_a", "lk_a"]
    for name, record in records.items():
        assert record.local_path.parent == operation.directory
        assert record.local_path.read_bytes() == payloads[name]
        assert record.size == len(payloads[name])
        assert record.sha256 == hashlib.sha256(payloads[name]).hexdigest()
        assert record.provenance == "device-read-unclassified"
    assert sorted(adb.removed) == sorted(
        [
            "/data/local/tmp/vendor_boot_a.bin",
            "/data/local/tmp/lk_a.bin",
        ]
        * 2
    )


def test_backup_space_estimate_supports_large_partitions_and_fails_on_shortfall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = BackupAdb({"super_a": b"unused"})
    ops = PartitionOps(adb, SimpleNamespace(), tmp_path / "exports")
    inventory = _inventory(("super_a", 9 * 1024**3))

    assert ops.estimate_selected_bytes(inventory, ["super_a"]) == 9 * 1024**3
    monkeypatch.setattr(
        partition_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1024, used=512, free=512),
    )
    with pytest.raises(VerificationError, match="free local space"):
        ops.backup_selected(inventory, ["super_a"])
    assert adb.shell_calls == []


def test_selected_backup_rejects_one_payload_of_free_space_before_device_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"one-partition"
    adb = BackupAdb({"vendor_boot_a": payload})
    operation = OperationFiles(tmp_path, "space-peak")
    ops = PartitionOps(adb, SimpleNamespace(), operation.directory)
    monkeypatch.setattr(
        partition_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1000, used=1000 - len(payload), free=len(payload)),
    )

    with pytest.raises(VerificationError, match="free local space"):
        ops.backup_selected(
            _inventory(("vendor_boot_a", len(payload))),
            ["vendor_boot_a"],
            operation_files=operation,
        )

    assert adb.shell_calls == []
    assert adb.removed == []


def test_backup_cancellation_during_pull_still_removes_remote_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = BackupAdb({"lk_a": b"lk-a"}, cancel_during_pull=True)
    ops = PartitionOps(adb, SimpleNamespace(), tmp_path / "exports")
    monkeypatch.setattr(
        partition_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1000, used=0, free=1000),
    )

    with pytest.raises(FlowCancelledError):
        ops.backup_selected(_inventory(("lk_a", 4)), ["lk_a"])

    assert adb.removed[-1] == "/data/local/tmp/lk_a.bin"


def test_backup_cancellation_preserves_primary_and_retries_root_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = CleanupFallbackAdb(
        {"lk_a": b"lk-a"},
        cancel_during_pull=True,
        fail_nonroot_cleanup=True,
    )
    ops = PartitionOps(adb, SimpleNamespace(), tmp_path / "exports")
    monkeypatch.setattr(
        partition_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1000, used=0, free=1000),
    )

    with pytest.raises(FlowCancelledError, match="cancelled during pull"):
        ops.backup_selected(_inventory(("lk_a", 4)), ["lk_a"])

    assert adb.cleanup_attempts[-2:] == [
        ("/data/local/tmp/lk_a.bin", False),
        ("/data/local/tmp/lk_a.bin", True),
    ]
    assert adb.remote == {}


@pytest.mark.parametrize("root_cleanup_fails", [False, True])
def test_backup_primary_failure_survives_cleanup_failures_and_attempts_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_cleanup_fails: bool,
) -> None:
    adb = CleanupFallbackAdb(
        {"lk_a": b"lk-a"},
        fail_nonroot_cleanup=True,
        fail_root_cleanup=root_cleanup_fails,
    )
    ops = PartitionOps(adb, SimpleNamespace(), tmp_path / "exports")
    monkeypatch.setattr(
        partition_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1000, used=0, free=1000),
    )

    with pytest.raises(VerificationError, match="size changed during backup"):
        ops.backup_selected(_inventory(("lk_a", 5)), ["lk_a"])

    assert adb.cleanup_attempts[-2:] == [
        ("/data/local/tmp/lk_a.bin", False),
        ("/data/local/tmp/lk_a.bin", True),
    ]
    assert (adb.remote == {}) is (not root_cleanup_fails)


class _FakeStandardPaths:
    class StandardLocation:
        DesktopLocation = object()

    desktop: Path

    @classmethod
    def writableLocation(cls, _location) -> str:
        return str(cls.desktop)


def test_manifest_v3_records_actual_device_provenance_product_block_time_and_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "lk_a.bin"
    source.write_bytes(b"already-patched-lk")
    from nebumtk_unlock_tools.core.partition import BackupRecord

    record = BackupRecord(
        "lk_a",
        "lk_a",
        "/data/local/tmp/lk_a.bin",
        source,
        block_target="/dev/block/sda1",
        size=source.stat().st_size,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        captured_at="2026-08-11T01:02:03Z",
        provenance="device-read-unclassified",
    )
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    _FakeStandardPaths.desktop = desktop
    monkeypatch.setattr(backup_export, "QStandardPaths", _FakeStandardPaths)

    exported = publish_to_desktop(
        {"lk_a": record},
        model_key="vivo-test",
        serial="SERIAL-FULL",
        product="PDTEST",
        initial_slot="_a",
        tool_build="0.1.0+task2",
    )
    manifest = json.loads(
        (exported.directory / BACKUP_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == 3
    assert manifest["adb_serial"] == "SERIAL-FULL"
    assert manifest["product"] == "PDTEST"
    assert manifest["initial_slot"] == "_a"
    assert manifest["tool_build"] == "0.1.0+task2"
    assert manifest["created_at"].endswith("Z")
    entry = manifest["partitions"]["lk_a"]
    assert entry["block_name"] == "lk_a"
    assert entry["block_target"] == "/dev/block/sda1"
    assert entry["captured_at"] == "2026-08-11T01:02:03Z"
    assert entry["provenance"] == "device-read-unclassified"
    assert "stock" not in json.dumps(manifest).lower()
