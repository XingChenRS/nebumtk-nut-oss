# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import inspect
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from typing import get_type_hints

import pytest

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.device import ConnectedDevice, DeviceProfile
from nebumtk_unlock_tools.core.errors import FlowCancelledError, PartitionError, VerificationError
from nebumtk_unlock_tools.core.operation_files import (
    OperationFiles,
    VerifiedFileSnapshot,
    snapshot_regular_file,
)
from nebumtk_unlock_tools.core.partition import BackupRecord, PartitionOps
from nebumtk_unlock_tools.core.slot import SlotManager
from nebumtk_unlock_tools.flows.base import FlowContext
from nebumtk_unlock_tools.flows import stages


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _profile(
    *,
    brand: str = "vivo",
    backup: list[str] | None = None,
    slot_suffix: bool = True,
    capabilities: frozenset[str] | None = None,
) -> DeviceProfile:
    return DeviceProfile(
        key=f"{brand}-test",
        brand=brand,
        display_name=f"{brand} test",
        match={"brand": brand},
        resources={"preload": "unused", "preload_remote": "/data/local/tmp/preload.so"},
        partitions={
            "backup": backup or ["lk", "init_boot"],
            "slot_suffix": slot_suffix,
            "names": {"lk": "lk", "init_boot": "init_boot"},
        },
        post_unlock={},
        capabilities=(
            frozenset({"preload", "xingc"})
            if capabilities is None and brand == "vivo"
            else capabilities or frozenset()
        ),
    )


def _device(slot: str = "_a") -> ConnectedDevice:
    return ConnectedDevice(
        serial="SERIAL",
        brand="vivo",
        model="PD2417",
        device="PD2417",
        firmware="test",
        slot_suffix=slot,
    )


def _legacy_oppo_backup_lk(tmp_path: Path, data: bytes = b"original") -> Path:
    directory = tmp_path / "NEBUMTK_Backup_oppo-test_SERIAL_20260802-010203"
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "verified_lk.bin"
    source.write_bytes(data)
    return source


def _accept_test_patched_lk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        stages.InternalUblPatcher,
        "validate_patched",
        lambda _self, _path: None,
    )


class ForbiddenWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.session_calls: list[str] = []

    def exports_dir(self, serial: str) -> Path:
        path = self.root / "exports" / serial
        path.mkdir(parents=True, exist_ok=True)
        return path

    def operation_files(self, label: str) -> OperationFiles:
        return OperationFiles(self.root, label)

    def __getattr__(self, name: str):
        if "session" in name or "phase" in name or name in {
            "register_backup_records",
            "invalidate_backup_authorization",
            "verify_export_backup",
            "verify_export_lk",
            "register_patched_lk",
            "patched_lk_path",
            "artifacts_dir",
        }:
            self.session_calls.append(name)
            raise AssertionError(f"workspace session API must not be called: {name}")
        raise AttributeError(name)


class FakeAdb:
    def __init__(
        self,
        *,
        live_slot: str = "_a",
        live_slots: list[str] | None = None,
        devices: list[list[str]] | None = None,
        block_size_output: str = "64",
        block_size_rc: int = 0,
        output: str | None = None,
    ) -> None:
        self.serial = "SERIAL"
        self.live_slot = live_slot
        self.live_slots = list(live_slots or [])
        self.device_sequences = list(devices or [])
        self.block_size_output = block_size_output
        self.block_size_rc = block_size_rc
        self.output = output if output is not None else block_size_output
        self.shell_calls: list[tuple[str, bool, int, bool]] = []
        self.pushed: list[tuple[Path, str]] = []
        self.removed: list[str] = []
        self.reboots: list[str] = []
        self.remote_files: dict[str, bytes] = {}

    def getprop(self, name: str) -> str:
        assert name == "ro.boot.slot_suffix"
        if self.live_slots:
            return self.live_slots.pop(0)
        return self.live_slot

    def shell(self, command: str, *, root: bool = False, timeout: int = 30, check: bool = False):
        self.shell_calls.append((command, root, timeout, check))
        if command.startswith("blockdev --getsize64"):
            return SimpleNamespace(returncode=self.block_size_rc, stdout=self.block_size_output, output=self.output)
        if command.startswith("test -e"):
            return SimpleNamespace(returncode=0, stdout="OK\n", output="OK\n")
        return SimpleNamespace(returncode=0, stdout="", output="")

    def push(self, local: Path, remote: str) -> None:
        self.pushed.append((Path(local), remote))
        self.remote_files[remote] = Path(local).read_bytes()

    def remove_remote(self, remote: str, strict: bool = False, root: bool = False) -> None:
        self.removed.append(remote)
        self.remote_files.pop(remote, None)

    def remote_file_size(self, remote: str, *, root: bool = True) -> int:
        data = self.remote_files.get(remote)
        return len(data) if data is not None else -1

    def remote_sha256(self, remote: str, *, root: bool = True) -> str:
        data = self.remote_files.get(remote)
        return hashlib.sha256(data).hexdigest() if data is not None else ""

    def reboot(self, target: str | None = None) -> None:
        self.reboots.append(target or "")

    def devices(self) -> list[str]:
        if self.device_sequences:
            return self.device_sequences.pop(0)
        return [self.serial]

    def get_state(self) -> str:
        return "device"


class FakeFastboot:
    def __init__(
        self,
        serial: str = "SERIAL",
        *,
        devices: list[list[str]] | None = None,
        modes: list[str] | None = None,
        slots: list[str] | None = None,
        sizes: dict[str, int] | None = None,
        unlocked: bool = True,
        fail_on_suffix: str | None = None,
        fail_set_active: bool = False,
        fail_partition: str | None = None,
        fail_reboot_checked: bool = False,
    ) -> None:
        self.serial = serial
        self.device_sequences = list(devices or [[serial]])
        self.mode_values = list(modes or ["bootloader"])
        self.slot_values = list(slots or ["a"])
        self.sizes = sizes or {"lk_a": 64, "lk_b": 64}
        self.unlocked = unlocked
        self.fail_on_suffix = fail_on_suffix
        self.fail_set_active = fail_set_active
        self.fail_partition = fail_partition
        self.fail_reboot_checked = fail_reboot_checked
        self.waits = 0
        self.unlocks: list[int] = []
        self.service_calls: list[str] = []
        self.flashes: list[tuple[str, Path]] = []
        self.partition_flashes: list[tuple[str, Path]] = []
        self.active: list[str] = []
        self.reboots = 0
        self.checked_reboots = 0
        self.reboot_fastboot_calls = 0
        self.reboot_bootloader_calls = 0

    def devices(self) -> list[str]:
        if self.device_sequences:
            return self.device_sequences.pop(0)
        return [self.serial]

    def fastboot_mode(self) -> str:
        if self.mode_values:
            return self.mode_values.pop(0)
        return "bootloader"

    def current_slot(self) -> str:
        if self.slot_values:
            return self.slot_values.pop(0)
        return "a"

    def wait_for_device(self, timeout: float = 120.0) -> None:
        self.waits += 1

    def flashing_unlock_with_retry(self, *, max_retries: int = 3) -> None:
        self.unlocks.append(max_retries)

    def is_unlocked(self) -> bool:
        return self.unlocked

    def partition_size(self, partition: str) -> int:
        return self.sizes[partition]

    def flash_lk(self, slot_suffix: str, image: Path) -> None:
        if self.fail_on_suffix == slot_suffix:
            raise RuntimeError(f"flash failed {slot_suffix}")
        self.flashes.append((slot_suffix, Path(image)))

    def flash_partition(self, partition: str, image: Path) -> None:
        if self.fail_partition == partition:
            raise RuntimeError(f"partition flash failed: {partition}")
        self.partition_flashes.append((partition, Path(image)))

    def flash_dual_lk(self, image: Path, *, before_each=None) -> list[str]:
        flashed: list[str] = []
        for suffix in ("_a", "_b"):
            if before_each is not None:
                before_each(suffix)
            self.flash_lk(suffix, image)
            flashed.append(suffix)
        return flashed

    def set_active(self, slot_letter: str) -> None:
        if self.fail_set_active:
            raise RuntimeError("set_active failed")
        self.active.append(slot_letter)

    def reboot(self) -> None:
        self.reboots += 1

    def reboot_checked(self) -> None:
        if self.fail_reboot_checked:
            raise RuntimeError("reboot failed")
        self.checked_reboots += 1

    def reboot_bootloader(self) -> None:
        self.reboot_bootloader_calls += 1

    def reboot_fastboot(self) -> None:
        self.reboot_fastboot_calls += 1


class FakeBackupOps:
    def __init__(self, operation_dir: Path, profile: DeviceProfile) -> None:
        self.operation_dir = operation_dir
        self.profile = profile
        self.backup_slots: list[str] = []

    def backup_partitions(self, slot):
        self.backup_slots.append(slot.active_suffix)
        records: dict[str, BackupRecord] = {}
        for partition in self.profile.backup_partitions:
            local = self.operation_dir / f"{partition}.bin"
            data = (
                b"ANDROID!init_boot-backup"
                if partition == "init_boot"
                else (partition + "-backup").encode()
            )
            local.write_bytes(data)
            records[partition] = BackupRecord(partition, f"{partition}{slot.active_suffix}", f"/remote/{partition}", local)
        return records

    def block_name(self, partition: str, suffix: str) -> str:
        return f"{partition}{suffix}"

    def preflight_block_exists(self, block_name: str) -> None:
        return None

    def block_size(self, block_name: str) -> int:
        return 64

    def write_inactive_lk_with_setrw(self, remote_lk: str, slot, *, before_write=None) -> None:
        if before_write is not None:
            before_write()
        return None

    def write_misc(self, remote_misc: str, *, before_write=None) -> None:
        if before_write is not None:
            before_write()
        return None


class FlowCtx:
    def __init__(self, tmp_path: Path, *, adb: FakeAdb | None = None, slot: str = "_a") -> None:
        self.workspace = ForbiddenWorkspace(tmp_path / "workspace")
        self.callbacks = SimpleNamespace(log=lambda _message: None, cancelled=lambda: False)
        self.operation_files = self.workspace.operation_files("flow")
        self.adb = adb or FakeAdb(live_slot=slot)
        self.fastboot = FakeFastboot()
        self.loader = SimpleNamespace(resolve_misc=lambda brand, suffix: tmp_path / f"misc{suffix}.img")
        self.loader.resolve_misc("vivo", "_a").write_bytes(b"misc-a")
        self.loader.resolve_misc("vivo", "_b").write_bytes(b"misc-b")
        self.backup_ops = FakeBackupOps(self.operation_files.directory, _profile())
        self.progress: list[tuple[int, str]] = []
        self.stages: list[str] = []
        self.root_checks = 0
        self.escalations = 0
        self.misc_pushes: list[Path] = []
        self.rebooted_to_fastbootd_then_bootloader = False
        self.client_initializations: list[tuple[str, bool, bool]] = []

    def init_clients(self, serial: str, *, need_adb: bool = True, need_fastboot: bool = False, profile=None) -> None:
        self.client_initializations.append((serial, need_adb, need_fastboot))

    def ensure_not_cancelled(self) -> None:
        return None

    def set_progress(self, percent: int, label: str) -> None:
        self.progress.append((percent, label))

    def set_stage(self, label: str) -> None:
        self.stages.append(label)

    def read_slot(self, device: ConnectedDevice):
        return SlotManager.from_suffix((device.slot_suffix or self.adb.getprop("ro.boot.slot_suffix")).strip())

    def ensure_root(self, profile: DeviceProfile, preload_override=None) -> None:
        self.root_checks += 1

    def escalate_root(self, profile: DeviceProfile, preload_override=None) -> None:
        self.escalations += 1

    def partition_ops(self, profile: DeviceProfile, serial: str):
        self.backup_ops.profile = profile
        return self.backup_ops

    def push_misc(self, misc: Path) -> str:
        self.misc_pushes.append(misc)
        return "/data/local/tmp/misc.img"

    def reboot_to_fastbootd_then_bootloader(self) -> None:
        self.rebooted_to_fastbootd_then_bootloader = True


    def wait_fastboot(self, timeout: float = 120.0) -> None:
        if self.fastboot is not None:
            self.fastboot.wait_for_device(timeout=timeout)


class WriteOps:
    def __init__(self, adb: FakeAdb, profile: DeviceProfile, exports_dir: Path, log=None, cancelled=None) -> None:
        self.adb = adb
        self.profile = profile
        self.preflighted: list[str] = []
        self.capacity_block: str | None = None
        self.capacity_blocks: list[str] = []
        self.writes: list[tuple[str, object]] = []

    def block_name(self, partition: str, suffix: str) -> str:
        return PartitionOps(self.adb, self.profile, Path.cwd() / ".pytest-unused").block_name(partition, suffix)

    def block_size(self, block_name: str) -> int:
        self.capacity_block = block_name
        self.capacity_blocks.append(block_name)
        return PartitionOps(self.adb, self.profile, Path.cwd() / ".pytest-unused").block_size(block_name)

    def preflight_block_exists(self, block_name: str) -> None:
        self.preflighted.append(block_name)

    def write_inactive_lk_with_setrw(self, remote_lk: str, slot, *, before_write=None) -> None:
        if before_write is not None:
            before_write()
        self.writes.append((remote_lk, slot))

    def write_misc(self, remote_misc: str, *, before_write=None) -> None:
        if before_write is not None:
            before_write()
        self.writes.append((remote_misc, "misc"))


def _transport_context(
    tmp_path: Path,
    *,
    adb: FakeAdb,
    fastboot: FakeFastboot,
    monkeypatch: pytest.MonkeyPatch,
) -> FlowContext:
    ctx = FlowContext(PROJECT_ROOT, tmp_path / "workspace", Settings())
    ctx.adb = adb  # type: ignore[assignment]
    ctx.fastboot = fastboot  # type: ignore[assignment]
    monkeypatch.setattr(ctx, "cancellable_sleep", lambda _seconds: None)
    return ctx








def _patched_snapshot(ctx: FlowCtx, tmp_path: Path) -> VerifiedFileSnapshot:
    source = tmp_path / "patched-lk.img"
    source.write_bytes(b"patched")
    return ctx.operation_files.copy_input(source, "patched_lk.img")




def test_vivo_finalization_refuses_to_claim_success_on_wrong_android_slot(
    tmp_path: Path,
) -> None:
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_b"))

    with pytest.raises(VerificationError, match="did not return to the original slot"):
        stages._verify_vivo_final_slot(ctx, SlotManager.from_suffix("_a"))






def test_root_escalation_does_not_create_session_or_call_workspace_session_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FlowCtx(tmp_path)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    stages.RootEscalationFlow(PROJECT_ROOT, tmp_path / "workspace", Settings()).run(_device(), _profile())

    assert ctx.escalations == 1
    assert ctx.workspace.session_calls == []
    assert not (tmp_path / "workspace" / "session.json").exists()


def test_backup_returns_operation_private_records_and_exports_without_phase_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FlowCtx(tmp_path)
    exports: list[dict[str, BackupRecord]] = []
    monkeypatch.setattr(stages, "publish_to_desktop", lambda records, **kwargs: exports.append(records) or SimpleNamespace(directory=tmp_path / "desktop", files=[record.local_path for record in records.values()]))
    monkeypatch.setattr(stages, "PartitionOps", lambda adb, profile, exports_dir, log=None, cancelled=None: FakeBackupOps(exports_dir, profile))

    backups, slot = stages._backup_lk(ctx, _device("_b"), _profile(backup=["lk", "init_boot"]))

    assert slot.active_suffix == "_b"
    assert set(backups) == {"lk", "init_boot"}
    assert exports == [backups]
    assert all(record.local_path.parent == ctx.operation_files.directory for record in backups.values())
    assert all(record.local_path.is_file() and record.local_path.stat().st_size > 0 for record in backups.values())
    assert ctx.workspace.session_calls == []


def test_backup_export_failure_is_fatal_and_does_not_report_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FlowCtx(tmp_path)
    logs: list[str] = []
    ctx.callbacks = SimpleNamespace(log=logs.append, cancelled=lambda: False)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(
        stages,
        "PartitionOps",
        lambda adb, profile, exports_dir, log=None, cancelled=None: FakeBackupOps(
            exports_dir,
            profile,
        ),
    )
    monkeypatch.setattr(
        stages,
        "publish_to_desktop",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        stages.BackupExportFlow(
            PROJECT_ROOT,
            tmp_path / "workspace",
            Settings(),
        ).run(_device("_a"), _profile())

    assert (100, "备份流程完成") not in ctx.progress
    assert ctx.stages[-1] == "桌面备份导出失败，流程已停止"
    assert all("disk full" not in message for message in logs)


def test_one_click_stops_before_first_write_when_desktop_export_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FlowCtx(tmp_path)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages.InternalUblPatcher, "availability_check", lambda: None)
    monkeypatch.setattr(
        stages,
        "PartitionOps",
        lambda adb, profile, exports_dir, log=None, cancelled=None: FakeBackupOps(
            exports_dir,
            profile,
        ),
    )
    monkeypatch.setattr(
        stages,
        "publish_to_desktop",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    write_calls: list[str] = []
    monkeypatch.setattr(
        stages,
        "_write_inactive_lk",
        lambda *_args, **_kwargs: write_calls.append("write"),
    )
    flow = stages.VivoCompleteUnlockFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    )

    with pytest.raises(OSError, match="disk full"):
        flow.run(_device("_a"), _profile())

    assert ctx.escalations == 1
    assert write_calls == []


def test_local_patch_requires_explicit_distinct_source_and_output_and_exports_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source_lk.img"
    source.write_bytes(b"original-lk")
    output = tmp_path / "out" / "patched_lk.img"
    ctx = FlowCtx(tmp_path)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages.InternalUblPatcher, "availability_check", lambda: None)
    monkeypatch.setattr(stages.InternalUblPatcher, "patch_and_verify", lambda self, src, dst: Path(dst).write_bytes(Path(src).read_bytes()[:-1]))

    request = stages.LocalLkPatchRequest(source, output, None)
    result = stages.PatchLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings()).run_local(request)

    assert result == output
    assert output.read_bytes() == b"original-l"
    assert source.read_bytes() == b"original-lk"
    assert not list(output.parent.glob("*.pending-*"))

    with pytest.raises(VerificationError, match="same|相同|同一路径"):
        stages.PatchLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings()).run_local(
            stages.LocalLkPatchRequest(source, source, None)
        )


def test_local_patch_exports_provider_verified_raw_growth_without_using_input_as_capacity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source_lk.img"
    source.write_bytes(b"lk")
    output = tmp_path / "out" / "patched_lk.img"
    ctx = FlowCtx(tmp_path)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages.InternalUblPatcher, "availability_check", lambda: None)
    monkeypatch.setattr(stages.InternalUblPatcher, "patch_and_verify", lambda self, src, dst: Path(dst).write_bytes(b"too-large"))

    result = stages.PatchLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings()).run_local(
        stages.LocalLkPatchRequest(source, output, "mt6985_or_lower")
    )

    assert result == output
    assert output.read_bytes() == b"too-large"
    assert not list(output.parent.glob("*.pending-*"))


def test_local_lk_patch_request_rejects_unknown_profile_without_device_context(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.img"
    source.write_bytes(b"lk")

    with pytest.raises(VerificationError, match="profile"):
        stages.LocalLkPatchRequest(
            source,
            tmp_path / "output.img",
            "some-device-model",
        )


def test_write_inactive_uses_private_copy_live_slot_capacity_and_no_phase_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "user-patched.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    write_ops = WriteOps(ctx.adb, _profile(), tmp_path / "exports")
    _accept_test_patched_lk(monkeypatch)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages, "PartitionOps", lambda adb, profile, exports_dir, log=None, cancelled=None: write_ops)

    stages.WriteInactiveFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), patched).run(_device("_a"), _profile())

    assert ctx.workspace.session_calls == []
    assert ctx.adb.pushed[0][0].parent == ctx.operation_files.directory
    assert ctx.adb.pushed[0][0].read_bytes() == b"patched"
    assert "lk_b" in write_ops.capacity_blocks
    assert "misc" in write_ops.capacity_blocks
    assert write_ops.preflighted == ["lk_b"]
    assert len(write_ops.writes) == 2
    assert ctx.rebooted_to_fastbootd_then_bootloader


def test_write_inactive_rejects_unpatched_vivo_before_root_or_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "selected-lk.img"
    selected.write_bytes(b"not-a-reviewed-patch")
    ctx = FlowCtx(tmp_path)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(
        stages.InternalUblPatcher,
        "validate_patched",
        lambda _self, _path: (_ for _ in ()).throw(
            VerificationError("patched LK 校验失败")
        ),
    )

    with pytest.raises(VerificationError, match="patched LK"):
        stages.WriteInactiveFlow(
            PROJECT_ROOT,
            tmp_path / "workspace",
            Settings(),
            selected,
        ).run(_device("_a"), _profile())

    assert ctx.root_checks == 0
    assert ctx.adb.pushed == []


def test_write_inactive_validates_gui_frozen_vivo_snapshot_before_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = tmp_path / "selected-lk.img"
    selected.write_bytes(b"not-a-reviewed-patch")
    frozen = snapshot_regular_file(selected)
    ctx = FlowCtx(tmp_path)
    validated: list[Path] = []
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    def reject(_self, path: Path) -> None:
        validated.append(path)
        raise VerificationError("patched LK validation failed")

    monkeypatch.setattr(stages.InternalUblPatcher, "validate_patched", reject)

    with pytest.raises(VerificationError, match="patched LK"):
        stages.WriteInactiveFlow(
            PROJECT_ROOT,
            tmp_path / "workspace",
            Settings(),
            frozen,
        ).run(_device("_a"), _profile())

    assert validated == [ctx.operation_files.directory / "write_patched_lk.img"]
    assert ctx.root_checks == 0
    assert ctx.adb.pushed == []


def test_write_inactive_rejects_live_slot_change_before_push_or_dd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "user-patched.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_b"))
    _accept_test_patched_lk(monkeypatch)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(VerificationError, match="槽位.*变化|slot.*change"):
        stages.WriteInactiveFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), patched).run(_device("_a"), _profile())

    assert ctx.adb.pushed == []


def test_write_inactive_rejects_target_capacity_smaller_than_private_patched_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "user-patched.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="3"))
    _accept_test_patched_lk(monkeypatch)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(VerificationError, match="大于原 lk|capacity|容量"):
        stages.WriteInactiveFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), patched).run(_device("_a"), _profile())

    assert ctx.adb.pushed == []


def test_write_inactive_reverifies_private_snapshot_before_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "user-patched.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    _accept_test_patched_lk(monkeypatch)
    real_verify = ctx.operation_files.verify_unchanged
    calls = 0

    def mutate_after_first_verify(snapshot):
        nonlocal calls
        calls += 1
        real_verify(snapshot)
        if calls == 1:
            snapshot.path.write_bytes(b"tamper!")

    monkeypatch.setattr(ctx.operation_files, "verify_unchanged", mutate_after_first_verify)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(VerificationError, match="SHA-256|size|changed after confirmation"):
        stages.WriteInactiveFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), patched).run(_device("_a"), _profile())

    assert calls >= 2
    assert ctx.adb.pushed == []


def test_complete_vivo_flow_runs_unlock_and_slot_sync_in_exact_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(
        stages.InternalUblPatcher,
        "availability_check",
        lambda: None,
    )

    lk_path = ctx.operation_files.directory / "verified_lk.bin"
    lk_path.write_bytes(b"lk-backup")
    init_boot_path = ctx.operation_files.directory / "verified_init_boot.bin"
    init_boot_path.write_bytes(b"ANDROID!init-boot-backup")
    backups = {
        "lk": BackupRecord("lk", "lk_a", "/remote/lk", lk_path),
        "init_boot": BackupRecord(
            "init_boot",
            "init_boot_a",
            "/remote/init_boot",
            init_boot_path,
        ),
    }
    initial_slot = SlotManager.from_suffix("_a")

    def fake_backup(*_args, **_kwargs):
        events.append("backup")
        return backups, initial_slot

    patched_source = tmp_path / "patched.img"
    patched_source.write_bytes(b"patched")
    patched = ctx.operation_files.copy_input(patched_source, "patched_lk.img")

    def fake_patch(*_args, **_kwargs):
        events.append("patch")
        return patched

    monkeypatch.setattr(stages, "_backup_lk", fake_backup)
    monkeypatch.setattr(stages, "_patch_lk_to_operation_file", fake_patch)
    monkeypatch.setattr(
        stages,
        "_write_inactive_lk",
        lambda *_args, **_kwargs: events.append("first-inactive-write"),
    )
    monkeypatch.setattr(
        stages,
        "_finalize_explicit_lk_fastboot",
        lambda *_args, **_kwargs: events.append("dual-slot-sync"),
    )
    monkeypatch.setattr(
        ctx,
        "escalate_root",
        lambda *_args, **_kwargs: events.append("root"),
    )
    monkeypatch.setattr(
        ctx,
        "reboot_to_fastbootd_then_bootloader",
        lambda: events.append("fastbootd-then-bootloader"),
    )
    monkeypatch.setattr(
        ctx,
        "wait_android_ready",
        lambda: events.append("android-storage-ready"),
        raising=False,
    )

    flow = stages.VivoCompleteUnlockFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    )
    monkeypatch.setattr(
        flow,
        "_unlock_with_xingc",
        lambda *_args, **_kwargs: events.append("xingc"),
    )
    flow.run(_device("_a"), _profile())

    assert ctx.client_initializations[0] == ("SERIAL", True, True)
    assert events == [
        "root",
        "backup",
        "patch",
        "first-inactive-write",
        "fastbootd-then-bootloader",
        "xingc",
        "dual-slot-sync",
        "android-storage-ready",
    ]


def test_complete_vivo_flow_preflights_internal_patcher_before_root_or_partition_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FlowCtx(tmp_path)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(
        stages.InternalUblPatcher,
        "availability_check",
        lambda: (_ for _ in ()).throw(VerificationError("patcher unavailable")),
    )
    flow = stages.VivoCompleteUnlockFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    )

    with pytest.raises(VerificationError, match="patcher unavailable"):
        flow.run(_device("_a"), _profile())

    assert ctx.escalations == 0
    assert ctx.backup_ops.backup_slots == []


def test_one_click_xingc_requires_expected_bootloader_slot_and_unlocked_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"]],
        modes=["bootloader", "bootloader"],
        slots=["b", "b"],
        unlocked=True,
    )
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    class FakeService:
        def __init__(self, log=None, cancelled=None) -> None:
            pass

        def unlock(self, serial: str, fastboot) -> None:
            fastboot.service_calls.append(serial)

    monkeypatch.setattr(stages, "XingcUnlockService", FakeService)
    flow = stages.VivoCompleteUnlockFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    )

    flow._unlock_with_xingc(_device("_a"), SlotManager.from_suffix("_a"))

    assert ctx.fastboot.service_calls == ["SERIAL"]

    ctx.fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"]],
        modes=["bootloader", "bootloader"],
        slots=["b", "b"],
        unlocked=False,
    )
    with pytest.raises(VerificationError, match="解锁|unlocked"):
        flow._unlock_with_xingc(_device("_a"), SlotManager.from_suffix("_a"))


def test_factory_uses_bundled_directional_misc_for_complete_vivo_flow(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.flows.factory import create_flow

    with pytest.raises(ValueError, match="未知 flow_kind"):
        create_flow(
            "auto_prepare_unlock",
            PROJECT_ROOT,
            tmp_path / "workspace",
            Settings(),
        )

    flow = create_flow(
        "vivo_complete_unlock",
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    )
    assert isinstance(flow, stages.VivoCompleteUnlockFlow)


@pytest.mark.parametrize(
    ("flow_kind", "required"),
    (
        ("vivo_xingc_unlock", "xingc"),
        ("dual_slot_sync", "xingc"),
        ("vivo_complete_unlock", "preload.*xingc|xingc.*preload"),
    ),
)
def test_xingc_service_flows_fail_closed_on_explicit_capabilities_before_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flow_kind: str,
    required: str,
) -> None:
    ctx = FlowCtx(tmp_path)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    profile = _profile(capabilities=frozenset())
    source = tmp_path / "source-lk.img"
    source.write_bytes(b"source")
    output = tmp_path / "output-lk.img"
    patched = tmp_path / "patched-lk.img"
    patched.write_bytes(b"patched")
    if flow_kind == "vivo_xingc_unlock":
        action = lambda: stages.VivoXingcUnlockFlow(
            PROJECT_ROOT, tmp_path / "workspace", Settings()
        ).run(_device("_a"), profile)
    elif flow_kind == "dual_slot_sync":
        action = lambda: stages.DualSlotSyncFlow(
            PROJECT_ROOT, tmp_path / "workspace", Settings(), patched
        ).run(_device("_a"), profile)
    else:
        action = lambda: stages.VivoCompleteUnlockFlow(
            PROJECT_ROOT,
            tmp_path / "workspace",
            Settings(),
        ).run(_device("_a"), profile)

    with pytest.raises(VerificationError, match=required):
        action()

    assert ctx.client_initializations == []
    assert not output.exists()


def test_auto_flow_patches_own_backup_path_and_writes_operation_local_patched_lk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages, "publish_to_desktop", lambda records, **kwargs: SimpleNamespace(directory=tmp_path / "desktop", files=[]))
    monkeypatch.setattr(stages, "PartitionOps", lambda adb, profile, exports_dir, log=None, cancelled=None: FakeBackupOps(exports_dir, profile))
    monkeypatch.setattr(stages.InternalUblPatcher, "availability_check", lambda: None)
    monkeypatch.setattr(
        stages.InternalUblPatcher,
        "validate_patched",
        lambda _self, _path: None,
    )

    patch_inputs: list[Path] = []

    def fake_patch(self, src, dst):
        patch_inputs.append(Path(src))
        Path(dst).write_bytes(Path(src).read_bytes()[:-1])

    monkeypatch.setattr(stages.InternalUblPatcher, "patch_and_verify", fake_patch)

    flow = stages.VivoCompleteUnlockFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    )
    monkeypatch.setattr(flow, "_unlock_with_xingc", lambda *_args: None)
    monkeypatch.setattr(stages, "_finalize_explicit_lk_fastboot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ctx, "wait_android_ready", lambda: None, raising=False)

    flow.run(_device("_a"), _profile())

    assert patch_inputs == [ctx.operation_files.directory / "auto_source_lk.img"]
    assert ctx.adb.pushed[0][0].parent == ctx.operation_files.directory
    assert ctx.workspace.session_calls == []


def test_partition_block_name_uses_aliases_and_rejects_unsafe_names(tmp_path: Path) -> None:
    profile = _profile()
    profile.partitions["names"]["lk"] = "lk.super"
    ops = PartitionOps(FakeAdb(), profile, tmp_path)

    assert ops.block_name("lk", "_b") == "lk.super_b"

    profile.partitions["names"]["lk"] = "lk;reboot"
    with pytest.raises(PartitionError, match="unsafe|非法|分区"):
        PartitionOps(FakeAdb(), profile, tmp_path).block_name("lk", "_b")


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "4096\n", 4096),
        (1, "4096\n", PartitionError),
        (0, "not-int\n", PartitionError),
        (0, "0\n", PartitionError),
        (0, "-1\n", PartitionError),
    ],
)
def test_partition_block_size_validates_command_result(tmp_path: Path, returncode: int, stdout: str, expected) -> None:
    adb = FakeAdb(block_size_output=stdout, block_size_rc=returncode)
    ops = PartitionOps(adb, _profile(), tmp_path)

    if expected is PartitionError:
        with pytest.raises(PartitionError):
            ops.block_size("lk_a")
    else:
        assert ops.block_size("lk_a") == expected

    assert adb.shell_calls[-1] == (
        "blockdev --getsize64 /dev/block/by-name/lk_a",
        True,
        30,
        False,
    )


@pytest.mark.parametrize(
    "stdout",
    ["18446744073709551616\n", "9" * 5000 + "\n"],
    ids=["overflow", "5000-digits"],
)
def test_partition_block_size_rejects_out_of_uint64_device_output(
    tmp_path: Path,
    stdout: str,
) -> None:
    ops = PartitionOps(
        FakeAdb(block_size_output=stdout),
        _profile(),
        tmp_path,
    )

    with pytest.raises(PartitionError):
        ops.block_size("lk_a")


def test_write_inactive_rechecks_live_slot_after_pushes_before_first_dd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "user-patched.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slots=["_a", "_b"], block_size_output="64"))
    write_ops = WriteOps(ctx.adb, _profile(), tmp_path / "exports")
    _accept_test_patched_lk(monkeypatch)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages, "PartitionOps", lambda adb, profile, exports_dir, log=None, cancelled=None: write_ops)

    with pytest.raises(VerificationError, match="槽位.*变化|slot.*change"):
        stages.WriteInactiveFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), patched).run(_device("_a"), _profile())

    assert ctx.adb.pushed
    assert write_ops.writes == []


def test_write_inactive_preserves_auto_snapshot_until_final_prewrite_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "patched-operation.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    snapshot = ctx.operation_files.copy_input(patched, "patched_lk.img")
    write_ops = WriteOps(ctx.adb, _profile(), tmp_path / "exports")
    _accept_test_patched_lk(monkeypatch)
    real_verify = ctx.operation_files.verify_unchanged
    calls = 0

    def mutate_source_after_first_verify(verified: VerifiedFileSnapshot) -> None:
        nonlocal calls
        calls += 1
        real_verify(verified)
        if verified.path == snapshot.path and calls == 1:
            snapshot.path.write_bytes(b"tamper!")

    monkeypatch.setattr(ctx.operation_files, "verify_unchanged", mutate_source_after_first_verify)
    monkeypatch.setattr(stages, "PartitionOps", lambda adb, profile, exports_dir, log=None, cancelled=None: write_ops)

    with pytest.raises(VerificationError, match="SHA-256|size|changed after confirmation"):
        stages._write_inactive_lk(ctx, _device("_a"), _profile(), snapshot)

    assert write_ops.writes == []





def test_auto_flow_passes_verified_patch_snapshot_to_write_and_detects_post_patch_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages, "publish_to_desktop", lambda records, **kwargs: SimpleNamespace(directory=tmp_path / "desktop", files=[]))
    monkeypatch.setattr(stages, "PartitionOps", lambda adb, profile, exports_dir, log=None, cancelled=None: FakeBackupOps(exports_dir, profile))
    monkeypatch.setattr(stages.InternalUblPatcher, "availability_check", lambda: None)
    monkeypatch.setattr(stages.InternalUblPatcher, "patch_and_verify", lambda self, src, dst: Path(dst).write_bytes(Path(src).read_bytes()[:-1]))

    seen_snapshot: list[VerifiedFileSnapshot] = []

    def fake_write(
        ctx_arg,
        device,
        profile,
        patched_lk,
        *,
        preload_override=None,
        enter_bootloader=True,
    ):
        assert isinstance(patched_lk, VerifiedFileSnapshot)
        seen_snapshot.append(patched_lk)
        patched_lk.path.write_bytes(b"mutated-after-patch")
        ctx_arg.operation_files.verify_unchanged(patched_lk)

    monkeypatch.setattr(stages, "_write_inactive_lk", fake_write)

    with pytest.raises(VerificationError, match="SHA-256|size"):
        stages.VivoCompleteUnlockFlow(
            PROJECT_ROOT,
            tmp_path / "workspace",
            Settings(),
        ).run(_device("_a"), _profile())

    assert seen_snapshot


def test_backup_rejects_profile_partition_label_metacharacters_before_shell_or_pull(tmp_path: Path) -> None:
    profile = _profile(backup=["lk;reboot"])
    profile.partitions["names"]["lk;reboot"] = "lk"
    adb = FakeAdb()
    ops = PartitionOps(adb, profile, tmp_path)

    with pytest.raises(PartitionError, match="非法|partition|label"):
        ops.backup_partitions(SlotManager.from_suffix("_a"))

    assert adb.shell_calls == []


def test_backup_rejects_profile_partition_label_traversal_before_shell_or_pull(tmp_path: Path) -> None:
    profile = _profile(backup=["..\\evil"])
    profile.partitions["names"]["..\\evil"] = "lk"
    adb = FakeAdb()
    ops = PartitionOps(adb, profile, tmp_path)

    with pytest.raises(PartitionError, match="非法|partition|label"):
        ops.backup_partitions(SlotManager.from_suffix("_a"))

    assert adb.shell_calls == []


def test_backup_quotes_dd_read_paths_for_ordinary_escaped_partition_name(tmp_path: Path) -> None:
    profile = _profile(backup=["lk.boot_1"])
    profile.partitions["names"]["lk.boot_1"] = "lk"
    adb = FakeAdb()
    pulled: list[Path] = []

    def remote_file_size(remote: str, *, root: bool = True) -> int:
        return 7

    def pull(remote: str, local: Path, cancelled=None) -> None:
        pulled.append(local)
        local.write_bytes(b"payload")

    adb.remote_file_size = remote_file_size
    adb.pull = pull
    ops = PartitionOps(adb, profile, tmp_path)

    records = ops.backup_partitions(SlotManager.from_suffix("_a"))

    assert records["lk.boot_1"].local_path == tmp_path / "lk.boot_1.bin"
    assert pulled == [tmp_path / "lk.boot_1.bin"]
    assert any(
        call[0] == (
            "dd if='/dev/block/by-name/lk_a' "
            "of='/data/local/tmp/lk.boot_1.bin' && "
            "chown 2000:2000 '/data/local/tmp/lk.boot_1.bin' && "
            "chmod 600 '/data/local/tmp/lk.boot_1.bin'"
        )
        and call[1] is True
        for call in adb.shell_calls
    )


def test_backup_lk_recopies_symlink_backup_record_to_operation_safe_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FlowCtx(tmp_path)
    real = ctx.operation_files.directory / "real-lk.bin"
    real.write_bytes(b"payload")
    link = ctx.operation_files.directory / "link-lk.bin"
    try:
        link.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    class SymlinkRecordOps:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def backup_partitions(self, slot):
            return {"lk": BackupRecord("lk", "lk_a", "/remote/lk", link)}

    monkeypatch.setattr(stages, "PartitionOps", SymlinkRecordOps)

    with pytest.raises(VerificationError, match="regular|reparse|symlink"):
        stages._backup_lk(ctx, _device("_a"), _profile(backup=["lk"]))


def test_partition_block_size_rejects_unsafe_block_name(tmp_path: Path) -> None:
    adb = FakeAdb()
    ops = PartitionOps(adb, _profile(), tmp_path)

    with pytest.raises(PartitionError, match="unsafe|非法|分区"):
        ops.block_size("lk_a;reboot")

    assert adb.shell_calls == []


@pytest.mark.parametrize(
    ("stdout", "stderr_like_output"),
    [
        ("", "4096\n"),
        ("4096 bytes\n", "4096 bytes\n"),
        ("4096\n8192\n", "4096\n8192\n"),
    ],
)
def test_partition_block_size_requires_stdout_exact_single_decimal(tmp_path: Path, stdout: str, stderr_like_output: str) -> None:
    adb = FakeAdb(block_size_output=stdout, output=stderr_like_output)
    ops = PartitionOps(adb, _profile(), tmp_path)

    with pytest.raises(PartitionError):
        ops.block_size("lk_a")


def test_write_inactive_final_snapshot_and_slot_guard_runs_after_setrw_preflights_and_before_dd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    patched = tmp_path / "user-patched.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path)
    _accept_test_patched_lk(monkeypatch)

    class OrderingAdb(FakeAdb):
        def __init__(self) -> None:
            super().__init__(live_slot="_a", block_size_output="64")

        def getprop(self, name: str) -> str:
            events.append("getprop")
            return super().getprop(name)

        def shell(self, command: str, *, root: bool = False, timeout: int = 30, check: bool = False):
            if command.startswith("test -e"):
                events.append(f"block-preflight:{command.rsplit('/', 1)[-1].split()[0]}")
                return SimpleNamespace(returncode=0, stdout="OK\n", output="OK\n")
            if command.startswith("test -f"):
                events.append("remote-preflight")
                return SimpleNamespace(returncode=0, stdout="OK\n", output="OK\n")
            if command.startswith("blockdev --getsize64"):
                events.append("capacity")
                return SimpleNamespace(returncode=0, stdout="64\n", output="64\n")
            if command.startswith("blockdev --setrw"):
                events.append("setrw")
                self.live_slot = "_b"
                return SimpleNamespace(returncode=0, stdout="", output="")
            events.append(f"shell:{command}")
            return SimpleNamespace(returncode=0, stdout="", output="")

    class TracingOps(PartitionOps):
        def _dd_write(self, remote_in: str, block_name: str) -> None:
            events.append(f"dd:{block_name}")

    ctx.adb = OrderingAdb()
    monkeypatch.setattr(stages, "PartitionOps", TracingOps)

    with pytest.raises(VerificationError, match="槽位.*变化|slot.*change"):
        stages._write_inactive_lk(ctx, _device("_a"), _profile(), patched)

    assert "dd:lk_b" not in events
    setrw_index = events.index("setrw")
    final_getprop_index = len(events) - 1 - events[::-1].index("getprop")
    assert setrw_index < final_getprop_index
    assert events[final_getprop_index + 1 :] == []
    assert "remote-preflight" in events[:final_getprop_index]
    assert any(event == "block-preflight:lk_b" for event in events[:final_getprop_index])


def test_worker_fastboot_device_uses_live_serial_mode_and_slot_without_workspace_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nebumtk_unlock_tools.gui.worker import FlowWorker

    class WorkerFastboot(FakeFastboot):
        pass

    created: list[WorkerFastboot] = []

    def make_fastboot(binary: str, serial: str, log=None, cancelled=None):
        fb = WorkerFastboot(serial, devices=[[serial]], modes=["bootloader"], slots=["b"])
        fb.cancelled = cancelled
        created.append(fb)
        return fb

    profile = _profile(brand="oppo")
    worker = FlowWorker(tmp_path, tmp_path / "workspace", Settings(), "SERIAL", "oppo-test", "oppo_unlock")
    monkeypatch.setattr("nebumtk_unlock_tools.core.env.EnvChecker.validate_fastboot", lambda self: SimpleNamespace(fastboot="fastboot"))
    monkeypatch.setattr("nebumtk_unlock_tools.gui.worker.FastbootClient", make_fastboot)
    monkeypatch.setattr("nebumtk_unlock_tools.gui.worker.WorkspaceManager", lambda *_: (_ for _ in ()).throw(AssertionError("WorkspaceManager must not be used")), raising=False)

    device = worker._device_for_fastboot(SimpleNamespace(), profile)

    assert device.serial == "SERIAL"
    assert device.profile is profile
    assert device.slot_suffix == "_b"
    assert created[0].serial == "SERIAL"


@pytest.mark.parametrize(
    ("devices", "mode", "slot"),
    [(["SERIAL", "OTHER"], "bootloader", "a"), (["SERIAL"], "fastbootd", "a"), (["SERIAL"], "unknown", "a"), (["SERIAL"], "bootloader", "c")],
)
def test_worker_fastboot_device_rejects_extra_devices_fastbootd_unknown_mode_and_invalid_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    devices: list[str],
    mode: str,
    slot: str,
) -> None:
    from nebumtk_unlock_tools.gui.worker import FlowWorker

    def make_fastboot(binary: str, serial: str, log=None, cancelled=None):
        return FakeFastboot(serial, devices=[devices], modes=[mode], slots=[slot])

    worker = FlowWorker(tmp_path, tmp_path / "workspace", Settings(), "SERIAL", "oppo-test", "oppo_unlock")
    monkeypatch.setattr("nebumtk_unlock_tools.core.env.EnvChecker.validate_fastboot", lambda self: SimpleNamespace(fastboot="fastboot"))
    monkeypatch.setattr("nebumtk_unlock_tools.gui.worker.FastbootClient", make_fastboot)

    with pytest.raises(VerificationError):
        worker._device_for_fastboot(SimpleNamespace(), _profile(brand="oppo"))


def test_worker_error_and_cancel_paths_do_not_record_phase_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nebumtk_unlock_tools.gui.worker import FlowWorker

    profile = _profile(brand="oppo")
    errors: list[str] = []
    cancellations: list[bool] = []

    class CancelFlow:
        ctx = SimpleNamespace(cleanup_materialized_preloads=lambda: None)

        def run(self, device, profile) -> None:
            raise FlowCancelledError("cancelled")

    worker = FlowWorker(tmp_path, tmp_path / "workspace", Settings(), "SERIAL", "oppo-test", "oppo_unlock")
    worker.finished_error.connect(lambda message: errors.append(message))
    worker.cancelled_signal.connect(lambda: cancellations.append(True))
    monkeypatch.setattr("nebumtk_unlock_tools.gui.worker.DeviceDetector", lambda *args, **kwargs: SimpleNamespace(get_profile_by_key=lambda _key: profile))
    monkeypatch.setattr("nebumtk_unlock_tools.gui.worker.create_flow", lambda *args, **kwargs: CancelFlow())
    monkeypatch.setattr(FlowWorker, "_device_for_fastboot", lambda self, detector, profile: _device("_a"))
    monkeypatch.setattr("nebumtk_unlock_tools.gui.worker.WorkspaceManager", lambda *_: (_ for _ in ()).throw(AssertionError("phase recording must not use WorkspaceManager")), raising=False)

    worker.run()

    assert errors == []
    assert cancellations == [True]



def test_oppo_unlock_skips_redundant_unlock_when_bootloader_is_already_unlocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"]],
        modes=["bootloader", "bootloader"],
        slots=["a", "a"],
        unlocked=True,
    )
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    stages.OppoUnlockFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    ).run(_device("_a"), _profile(brand="oppo"))

    assert ctx.fastboot.unlocks == []
    assert (100, "设备已处于 unlocked") in ctx.progress
    assert not (tmp_path / "workspace" / "session.json").exists()


def test_vivo_xingc_unlock_runs_without_historical_gate_and_rechecks_live_fastboot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(devices=[["SERIAL"], ["SERIAL"]], modes=["bootloader", "bootloader"], slots=["a", "a"])
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    class FakeService:
        def __init__(self, log=None, cancelled=None) -> None:
            pass

        def unlock(self, serial: str, fastboot) -> None:
            fastboot.service_calls.append(serial)

    monkeypatch.setattr(stages, "XingcUnlockService", FakeService)

    stages.VivoXingcUnlockFlow(PROJECT_ROOT, tmp_path / "workspace", Settings()).run(_device("_a"), _profile())

    assert ctx.fastboot.waits == 1
    assert ctx.fastboot.service_calls == ["SERIAL"]
    assert ctx.workspace.session_calls == []


def test_vivo_xingc_lock_is_independent_and_rechecks_live_fastboot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"]],
        modes=["bootloader", "bootloader"],
        slots=["a", "a"],
    )
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    class FakeService:
        def __init__(self, log=None, cancelled=None) -> None:
            pass

        def lock(self, serial: str, fastboot) -> None:
            fastboot.service_calls.append(("lock", serial))

    monkeypatch.setattr(stages, "XingcUnlockService", FakeService)

    stages.VivoXingcLockFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        Settings(),
    ).run(_device("_a"), _profile())

    assert ctx.fastboot.waits == 1
    assert ctx.fastboot.service_calls == [("lock", "SERIAL")]
    assert ctx.workspace.session_calls == []


def test_factory_requires_explicit_restore_and_sync_images(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.flows.factory import create_flow

    with pytest.raises(ValueError, match="还原双槽原 LK|显式选择"):
        create_flow("restore_original_lk", PROJECT_ROOT, tmp_path / "workspace", Settings())
    with pytest.raises(ValueError, match="patched LK|双槽同步"):
        create_flow("dual_slot_sync", PROJECT_ROOT, tmp_path / "workspace", Settings())

    source = tmp_path / "source.img"
    source.write_bytes(b"original")
    patched = tmp_path / "patched.img"
    patched.write_bytes(b"patched")
    assert isinstance(create_flow("restore_original_lk", PROJECT_ROOT, tmp_path / "workspace", Settings(), source_lk=source), stages.RestoreOriginalLkFlow)
    assert isinstance(create_flow("dual_slot_sync", PROJECT_ROOT, tmp_path / "workspace", Settings(), patched_lk=patched), stages.DualSlotSyncFlow)
    assert isinstance(
        create_flow("vivo_xingc_lock", PROJECT_ROOT, tmp_path / "workspace", Settings()),
        stages.VivoXingcLockFlow,
    )


def test_restore_uses_explicit_private_copy_live_slot_capacity_guards_and_reboots_after_both_flashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _legacy_oppo_backup_lk(tmp_path)
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(devices=[["SERIAL"], ["SERIAL"], ["SERIAL"], ["SERIAL"]], modes=["bootloader"] * 4, slots=["a"] * 4, sizes={"lk_a": 64, "lk_b": 64})
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device("_a"), _profile(brand="oppo"))

    assert ctx.fastboot.flashes == [("_a", ctx.operation_files.directory / "restore_original_lk.img"), ("_b", ctx.operation_files.directory / "restore_original_lk.img")]
    assert ctx.fastboot.active == ["a"]
    assert ctx.fastboot.reboots == 0
    assert ctx.fastboot.checked_reboots == 1
    assert ctx.workspace.session_calls == []


def test_finalize_requires_unlocked_and_explicit_patched_lk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "patched-lk.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(unlocked=False)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(VerificationError, match="未解锁|unlocked"):
        stages.DualSlotSyncFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), patched).run(_device("_a"), _profile())

    assert ctx.fastboot.flashes == []
    assert ctx.fastboot.reboots == 0


def test_finalize_rejects_slot_change_between_dual_lk_flashes_without_reboot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patched = tmp_path / "patched-lk.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(devices=[["SERIAL"], ["SERIAL"], ["SERIAL"], ["SERIAL"]], modes=["bootloader"] * 4, slots=["a", "a", "a", "b"], sizes={"lk_a": 64, "lk_b": 64})
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(
        stages.InternalUblPatcher,
        "validate_patched",
        lambda _self, _path: None,
    )

    with pytest.raises(VerificationError, match="槽位.*变化|slot.*change"):
        stages.DualSlotSyncFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), patched).run(_device("_a"), _profile())

    assert ctx.fastboot.flashes == [("_a", ctx.operation_files.directory / "finalize_patched_lk.img")]
    assert ctx.fastboot.reboots == 0


def test_restore_rejects_image_larger_than_either_fastboot_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _legacy_oppo_backup_lk(tmp_path, b"too-large")
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(sizes={"lk_a": 64, "lk_b": 3})
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(VerificationError, match="大于原 lk|capacity|容量"):
        stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device("_a"), _profile(brand="oppo"))

    assert ctx.fastboot.flashes == []
    assert ctx.fastboot.reboots == 0


def test_restore_rejects_source_mutation_at_flash_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _legacy_oppo_backup_lk(tmp_path)
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(devices=[["SERIAL"], ["SERIAL"]], modes=["bootloader", "bootloader"], slots=["a", "a"], sizes={"lk_a": 64, "lk_b": 64})
    real_verify = ctx.operation_files.verify_unchanged
    calls = 0

    def mutate_after_first_verify(snapshot: VerifiedFileSnapshot) -> None:
        nonlocal calls
        calls += 1
        real_verify(snapshot)
        if snapshot.path.name == "restore_original_lk.img" and calls == 1:
            snapshot.path.write_bytes(b"mutated")

    monkeypatch.setattr(ctx.operation_files, "verify_unchanged", mutate_after_first_verify)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(VerificationError, match="SHA-256|size"):
        stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device("_a"), _profile(brand="oppo"))

    assert ctx.fastboot.flashes == []
    assert ctx.fastboot.reboots == 0


def test_restore_rejects_slot_change_during_wait_against_initial_live_slot_without_device_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _legacy_oppo_backup_lk(tmp_path)
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(devices=[["SERIAL"], ["SERIAL"]], modes=["bootloader", "bootloader"], slots=["a", "b"], sizes={"lk_a": 64, "lk_b": 64})
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(VerificationError, match="槽位.*变化|slot.*change"):
        stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device(""), _profile(brand="oppo"))

    assert ctx.fastboot.flashes == []
    assert ctx.fastboot.reboots == 0
    assert ctx.fastboot.checked_reboots == 0


def test_restore_first_slot_mutation_guard_is_wrapped_by_production_flash_dual_lk_without_reboot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nebumtk_unlock_tools.core.errors import FastbootError
    from nebumtk_unlock_tools.core.fastboot import FastbootClient

    source = _legacy_oppo_backup_lk(tmp_path)
    ctx = FlowCtx(tmp_path)

    class ProductionDualFastboot(FakeFastboot):
        flash_dual_lk = FastbootClient.flash_dual_lk

    ctx.fastboot = ProductionDualFastboot(devices=[["SERIAL"], ["SERIAL"], ["SERIAL"]], modes=["bootloader"] * 3, slots=["a", "a", "a"], sizes={"lk_a": 64, "lk_b": 64})
    real_verify = ctx.operation_files.verify_unchanged
    seen_restore_snapshot = False

    def mutate_before_first_flash(snapshot: VerifiedFileSnapshot) -> None:
        nonlocal seen_restore_snapshot
        real_verify(snapshot)
        if snapshot.path.name != "restore_original_lk.img":
            return
        if not seen_restore_snapshot:
            seen_restore_snapshot = True
            return
        snapshot.path.write_bytes(b"mutated-before-a")
        real_verify(snapshot)

    monkeypatch.setattr(ctx.operation_files, "verify_unchanged", mutate_before_first_flash)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(FastbootError) as exc_info:
        stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device("_a"), _profile(brand="oppo"))

    assert "已成功槽: 无" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, VerificationError)
    assert ctx.fastboot.flashes == []
    assert ctx.fastboot.reboots == 0
    assert ctx.fastboot.checked_reboots == 0


def test_restore_second_slot_mutation_after_lk_a_success_is_wrapped_and_does_not_reboot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nebumtk_unlock_tools.core.errors import FastbootError
    from nebumtk_unlock_tools.core.fastboot import FastbootClient

    source = _legacy_oppo_backup_lk(tmp_path)
    ctx = FlowCtx(tmp_path)

    class ProductionDualFastboot(FakeFastboot):
        flash_dual_lk = FastbootClient.flash_dual_lk

    ctx.fastboot = ProductionDualFastboot(devices=[["SERIAL"], ["SERIAL"], ["SERIAL"], ["SERIAL"]], modes=["bootloader"] * 4, slots=["a", "a", "a", "a"], sizes={"lk_a": 64, "lk_b": 64})
    real_verify = ctx.operation_files.verify_unchanged
    before_each_verifies = 0

    def mutate_before_second_flash(snapshot: VerifiedFileSnapshot) -> None:
        nonlocal before_each_verifies
        real_verify(snapshot)
        if snapshot.path.name != "restore_original_lk.img":
            return
        before_each_verifies += 1
        if before_each_verifies == 3:
            snapshot.path.write_bytes(b"mutated-before-b")
            real_verify(snapshot)

    monkeypatch.setattr(ctx.operation_files, "verify_unchanged", mutate_before_second_flash)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(FastbootError) as exc_info:
        stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device("_a"), _profile(brand="oppo"))

    assert "_a" in str(exc_info.value) or "lk_a" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, VerificationError)
    assert ctx.fastboot.flashes == [("_a", ctx.operation_files.directory / "restore_original_lk.img")]
    assert ctx.fastboot.reboots == 0
    assert ctx.fastboot.checked_reboots == 0


def test_restore_set_active_failure_propagates_and_does_not_reboot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _legacy_oppo_backup_lk(tmp_path)
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(fail_set_active=True)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(RuntimeError, match="set_active failed"):
        stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device("_a"), _profile(brand="oppo"))

    assert ctx.fastboot.flashes == [("_a", ctx.operation_files.directory / "restore_original_lk.img"), ("_b", ctx.operation_files.directory / "restore_original_lk.img")]
    assert ctx.fastboot.reboots == 0
    assert ctx.fastboot.checked_reboots == 0


def test_restore_uses_checked_reboot_and_propagates_reboot_failure_without_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _legacy_oppo_backup_lk(tmp_path)
    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(fail_reboot_checked=True)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)

    with pytest.raises(RuntimeError, match="reboot failed"):
        stages.RestoreOriginalLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings(), source).run(_device("_a"), _profile(brand="oppo"))

    assert ctx.fastboot.active == ["a"]
    assert ctx.fastboot.reboots == 0
    assert ctx.fastboot.checked_reboots == 0
    assert (100, "还原完成") not in ctx.progress


def test_stage_signatures_have_no_phase_parameter_and_backup_returns_slotinfo() -> None:
    assert "require_patch_phase" not in inspect.signature(stages._write_inactive_lk).parameters
    hints = get_type_hints(stages._backup_lk)
    assert hints["return"] == tuple[dict[str, BackupRecord], stages.SlotInfo]


def test_local_patch_preserves_existing_output_and_removes_pending_on_mid_export_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source_lk.img"
    source.write_bytes(b"original-lk")
    output = tmp_path / "out" / "patched_lk.img"
    output.parent.mkdir()
    output.write_bytes(b"old-output")
    ctx = FlowCtx(tmp_path)
    real_copy_input = ctx.operation_files.copy_input

    def fail_export_verification(source_path: Path, name: str):
        if name == "export_verify.img":
            raise VerificationError("forced export verification failure")
        return real_copy_input(source_path, name)

    monkeypatch.setattr(ctx.operation_files, "copy_input", fail_export_verification)
    monkeypatch.setattr(stages, "FlowContext", lambda *args, **kwargs: ctx)
    monkeypatch.setattr(stages.InternalUblPatcher, "availability_check", lambda: None)
    monkeypatch.setattr(stages.InternalUblPatcher, "patch_and_verify", lambda self, src, dst: Path(dst).write_bytes(Path(src).read_bytes()[:-1]))

    with pytest.raises(VerificationError, match="forced export verification failure"):
        stages.PatchLkFlow(PROJECT_ROOT, tmp_path / "workspace", Settings()).run_local(
            stages.LocalLkPatchRequest(source, output, None)
        )

    assert output.read_bytes() == b"old-output"
    assert not list(output.parent.glob("*.pending-*"))


def test_first_slot_reboot_accepts_fastbootd_then_enters_verified_bootloader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = FakeAdb()
    fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"]],
        modes=["fastbootd", "bootloader"],
    )
    ctx = _transport_context(tmp_path, adb=adb, fastboot=fastboot, monkeypatch=monkeypatch)

    ctx.reboot_to_fastbootd_then_bootloader()

    assert adb.reboots == ["bootloader"]
    assert fastboot.reboot_fastboot_calls == 0
    assert fastboot.reboot_bootloader_calls == 1


def test_first_slot_reboot_uses_exactly_one_bootloader_to_fastbootd_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = FakeAdb()
    fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"], ["SERIAL"]],
        modes=["bootloader", "fastbootd", "bootloader"],
    )
    ctx = _transport_context(tmp_path, adb=adb, fastboot=fastboot, monkeypatch=monkeypatch)

    ctx.reboot_to_fastbootd_then_bootloader()

    assert fastboot.reboot_fastboot_calls == 1
    assert fastboot.reboot_bootloader_calls == 1


def test_first_slot_reboot_stops_when_fallback_remains_bootloader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = FakeAdb()
    fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"]],
        modes=["bootloader", "bootloader"],
    )
    ctx = _transport_context(tmp_path, adb=adb, fastboot=fastboot, monkeypatch=monkeypatch)

    with pytest.raises(VerificationError, match="fastbootd|bootloader"):
        ctx.reboot_to_fastbootd_then_bootloader()

    assert fastboot.reboot_fastboot_calls == 1
    assert fastboot.reboot_bootloader_calls == 0


def test_first_slot_reboot_rejects_extra_fastboot_serial_before_mode_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = FakeAdb()
    fastboot = FakeFastboot(
        devices=[["SERIAL", "OTHER"]],
        modes=["fastbootd"],
    )
    ctx = _transport_context(tmp_path, adb=adb, fastboot=fastboot, monkeypatch=monkeypatch)

    with pytest.raises(VerificationError, match="serial|设备集合"):
        ctx.reboot_to_fastbootd_then_bootloader()

    assert fastboot.reboot_fastboot_calls == 0
    assert fastboot.reboot_bootloader_calls == 0


def test_first_slot_reboot_rechecks_bootloader_mode_after_fastbootd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = FakeAdb()
    fastboot = FakeFastboot(
        devices=[["SERIAL"], ["SERIAL"]],
        modes=["fastbootd", "fastbootd"],
    )
    ctx = _transport_context(tmp_path, adb=adb, fastboot=fastboot, monkeypatch=monkeypatch)

    with pytest.raises(VerificationError, match="bootloader|模式"):
        ctx.reboot_to_fastbootd_then_bootloader()

    assert fastboot.reboot_bootloader_calls == 1



def test_write_inactive_revalidates_remote_misc_at_immediate_dd_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = tmp_path / "patched-operation.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    snapshot = ctx.operation_files.copy_input(patched, "patched_lk.img")

    class MutatingMiscOps(WriteOps):
        def write_misc(self, remote_misc: str, *, before_write=None) -> None:
            self.adb.remote_files[remote_misc] = b"tampered-after-lk-dd"
            if before_write is not None:
                before_write()
            self.writes.append((remote_misc, "misc"))

    write_ops = MutatingMiscOps(ctx.adb, _profile(), tmp_path / "exports")
    _accept_test_patched_lk(monkeypatch)
    monkeypatch.setattr(
        stages,
        "PartitionOps",
        lambda adb, profile, exports_dir, log=None, cancelled=None: write_ops,
    )

    with pytest.raises(VerificationError, match="misc"):
        stages._write_inactive_lk(ctx, _device("_a"), _profile(), snapshot)

    assert all(write[1] != "misc" for write in write_ops.writes)
    assert write_ops.writes[0][1] != "misc"


def test_write_inactive_revalidates_adb_mode_immediately_before_misc_dd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ModeChangingAdb(FakeAdb):
        def __init__(self) -> None:
            super().__init__(live_slot="_a", block_size_output="64")
            self.states = ["device", "offline"]

        def get_state(self) -> str:
            return self.states.pop(0)

    patched = tmp_path / "patched-operation.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=ModeChangingAdb())
    snapshot = ctx.operation_files.copy_input(patched, "patched_lk.img")
    write_ops = WriteOps(ctx.adb, _profile(), tmp_path / "exports")
    _accept_test_patched_lk(monkeypatch)
    monkeypatch.setattr(
        stages,
        "PartitionOps",
        lambda adb, profile, exports_dir, log=None, cancelled=None: write_ops,
    )

    with pytest.raises(VerificationError, match="ADB mode|device state"):
        stages._write_inactive_lk(ctx, _device("_a"), _profile(), snapshot)

    assert len(write_ops.writes) == 1


def test_write_inactive_revalidates_misc_capacity_immediately_before_misc_dd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched = tmp_path / "patched-operation.img"
    patched.write_bytes(b"patched")
    ctx = FlowCtx(tmp_path, adb=FakeAdb(live_slot="_a", block_size_output="64"))
    snapshot = ctx.operation_files.copy_input(patched, "patched_lk.img")

    class SmallMiscOps(WriteOps):
        def block_size(self, block_name: str) -> int:
            return 3 if block_name == "misc" else super().block_size(block_name)

    write_ops = SmallMiscOps(ctx.adb, _profile(), tmp_path / "exports")
    _accept_test_patched_lk(monkeypatch)
    monkeypatch.setattr(
        stages,
        "PartitionOps",
        lambda adb, profile, exports_dir, log=None, cancelled=None: write_ops,
    )

    with pytest.raises(VerificationError, match="misc.*capacity|capacity.*misc"):
        stages._write_inactive_lk(ctx, _device("_a"), _profile(), snapshot)

    assert all(write[1] != "misc" for write in write_ops.writes)



def test_finalize_writes_a_sakisu_init_boot_only_to_the_target_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A patched init_boot goes to the slot the device returns to, never both.

    1.1's contract allowed this only during the finishing boundary and only for
    the original slot; a peer-slot copy would leave the device booting a rooted
    image from the wrong side.
    """

    boot = tmp_path / "sakisu_init_boot.img"
    boot.write_bytes(b"ANDROID!" + bytes(64))
    lk = tmp_path / "patched_lk.img"
    lk.write_bytes(b"patched")

    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(
        devices=[["SERIAL"]] * 4,
        modes=["bootloader"] * 4,
        slots=["a"] * 4,
        sizes={"lk_a": 64, "lk_b": 64, "init_boot_a": 128, "init_boot_b": 128},
    )
    monkeypatch.setattr(stages, "FlowContext", lambda *_args, **_kwargs: ctx)

    stages._finalize_explicit_lk_fastboot(
        ctx,
        _device("_a"),
        lk,
        "finalize_patched_lk.img",
        target_active_slot="_a",
        init_boot=boot,
    )

    assert [name for name, _path in ctx.fastboot.partition_flashes] == ["init_boot_a"]
    assert sorted(suffix for suffix, _path in ctx.fastboot.flashes) == ["_a", "_b"]
    assert ctx.fastboot.active == ["a"]


def test_finalize_writes_no_init_boot_when_the_option_is_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lk = tmp_path / "patched_lk.img"
    lk.write_bytes(b"patched")

    ctx = FlowCtx(tmp_path)
    ctx.fastboot = FakeFastboot(
        devices=[["SERIAL"]] * 4,
        modes=["bootloader"] * 4,
        slots=["a"] * 4,
        sizes={"lk_a": 64, "lk_b": 64},
    )
    monkeypatch.setattr(stages, "FlowContext", lambda *_args, **_kwargs: ctx)

    stages._finalize_explicit_lk_fastboot(
        ctx, _device("_a"), lk, "finalize_patched_lk.img", target_active_slot="_a"
    )

    assert ctx.fastboot.partition_flashes == []
