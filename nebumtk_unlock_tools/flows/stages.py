# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Shared multi-stage flows (OPPO / Vivo)."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from ..core.backup_export import (
    BackupLkValidation,
    load_dyn_backup,
    publish_to_desktop,
    validate_restore_lk_backup,
)
from ..core.dyn_backup import DynBackup, capture_dyn_backup, restore_dyn_backup
from ..core.device import ConnectedDevice, DeviceProfile
from ..core.errors import VerificationError
from ..core.operation_files import VerifiedFileSnapshot
from ..core.partition import BackupRecord, PartitionOps
from ..core.safety import SafetyChecker
from ..core.sakisu import SakiSUPatcher
from ..core.slot import SlotInfo, SlotManager
from ..lk.ubl_service import InternalUblPatcher
from ..lk.xingc_service import XingcUnlockService
from .base import FlowCallbacks, FlowContext


def _validate_partition_label(partition: str) -> str:
    label = str(partition)
    if re.fullmatch(r"[A-Za-z0-9._-]+", label) is None:
        raise VerificationError(f"非法 partition label: {partition!r}")
    return label


def _backup_lk(
    ctx: FlowContext,
    device: ConnectedDevice,
    profile: DeviceProfile,
    preload_override: Path | str | None = None,
) -> tuple[dict[str, BackupRecord], SlotInfo]:
    slot = ctx.read_slot(device)
    ctx.ensure_root(profile, preload_override)
    ctx.ensure_not_cancelled()
    assert ctx.adb is not None
    ops = PartitionOps(
        ctx.adb,
        profile,
        ctx.operation_files.directory,
        log=ctx.callbacks.log,
        cancelled=ctx.callbacks.cancelled,
    )
    backups = ops.backup_partitions(slot)
    if "lk" not in backups:
        raise VerificationError("LK 备份缺失")
    verified_backups: dict[str, BackupRecord] = {}
    operation_dir = ctx.operation_files.directory.resolve()
    for partition, record in backups.items():
        _validate_partition_label(partition)
        resolved_parent = record.local_path.resolve(strict=False).parent
        if resolved_parent != operation_dir:
            raise VerificationError(f"{partition} 备份路径不在本次操作目录内: {record.local_path}")
        snapshot = ctx.operation_files.copy_input(record.local_path, f"verified_{partition}.bin")
        verified_backups[partition] = BackupRecord(
            partition=record.partition,
            block_name=record.block_name,
            remote_path=record.remote_path,
            local_path=snapshot.path,
        )
    backups = verified_backups
    dyn_backup: DynBackup | None = None
    if profile.brand.lower() == "vivo":
        try:
            dyn_backup = capture_dyn_backup(
                ctx.adb,
                ctx.operation_files.directory,
                cancelled=ctx.callbacks.cancelled,
                log=ctx.callbacks.log,
            )
        except Exception as exc:
            # dyn only avoids a post-format warning. Its backup must not turn
            # an otherwise verified LK backup into a failed unlock operation.
            ctx.callbacks.log(f"Vivo dyn backup skipped: {exc}")
    try:
        publish_to_desktop(
            backups,
            model_key=profile.key,
            serial=device.serial,
            initial_slot=slot.active_suffix,
            dyn_backup=dyn_backup,
        )
        ctx.dyn_backup = dyn_backup
        ctx.set_stage("备份已保存到桌面")
        ctx.callbacks.log("备份已导出")
    except Exception:
        ctx.set_stage("桌面备份导出失败，流程已停止")
        ctx.callbacks.log("桌面备份导出失败；未执行后续写入")
        raise
    return backups, slot


def _patch_lk_to_operation_file(
    ctx: FlowContext,
    source_lk: Path | VerifiedFileSnapshot,
    profile: DeviceProfile,
    output_name: str = "patched_lk.img",
    *,
    copy_source: bool = True,
) -> VerifiedFileSnapshot:
    if profile.brand.lower() != "vivo":
        raise VerificationError("内部 LK 补丁目前仅适用于 Vivo")
    if isinstance(source_lk, VerifiedFileSnapshot):
        source_snapshot = source_lk
    elif copy_source:
        source_snapshot = ctx.operation_files.copy_input(source_lk, "source_lk.img")
    else:
        raise VerificationError("内部自动补丁必须传入已验证的 LK 快照")
    ctx.operation_files.verify_unchanged(source_snapshot)
    operation_pending = ctx.operation_files.directory / f"{output_name}.pending"
    operation_pending.unlink(missing_ok=True)
    provider = InternalUblPatcher(
        log=ctx.callbacks.log,
        cancelled=ctx.callbacks.cancelled,
        image_profile=profile.xingc_patch_profile,
    )
    ctx.ensure_not_cancelled()
    provider.patch_and_verify(source_snapshot.path, operation_pending)
    ctx.ensure_not_cancelled()
    ctx.operation_files.verify_unchanged(source_snapshot)
    output_snapshot = ctx.operation_files.copy_input(operation_pending, output_name)
    operation_pending.unlink(missing_ok=True)
    ctx.operation_files.verify_unchanged(source_snapshot)
    # patch_and_verify already enforces the bounded structural raw-image growth
    # policy.  A raw LK file is not a partition-capacity declaration: compare
    # the verified output with the live target block/partition immediately
    # before the write instead (see _write_inactive_lk and Fastboot stages).
    return output_snapshot


def _prepare_sakisu_root_image(
    ctx: FlowContext,
    backups: dict[str, BackupRecord],
) -> VerifiedFileSnapshot:
    """Patch the backed-up original-slot init_boot before any LK write begins."""

    init_boot = backups.get("init_boot")
    if init_boot is None:
        raise VerificationError("已选择 SakiSU root，但原槽 init_boot 备份缺失")
    source = ctx.operation_files.copy_input(
        init_boot.local_path,
        "auto_source_init_boot.img",
    )
    ctx.operation_files.verify_unchanged(source)
    ctx.ensure_not_cancelled()
    assert ctx.adb is not None
    kernel_release = ctx.adb.kernel_release()
    executable = ctx.loader.materialize_sakisu_patcher(
        ctx.operation_files.directory / "sakisu-ksud.exe"
    )
    ctx.ensure_not_cancelled()
    return SakiSUPatcher(executable).prepare(
        source,
        kernel_release,
        ctx.operation_files,
    )


def _live_slot(ctx: FlowContext) -> SlotInfo:
    assert ctx.adb is not None
    raw = ctx.adb.getprop("ro.boot.slot_suffix")
    return SlotManager.from_suffix(raw)


def _write_inactive_lk(
    ctx: FlowContext,
    device: ConnectedDevice,
    profile: DeviceProfile,
    patched_lk: Path | VerifiedFileSnapshot,
    *,
    preload_override: Path | str | None = None,
    enter_bootloader: bool = True,
) -> None:
    source_snapshot = patched_lk if isinstance(patched_lk, VerifiedFileSnapshot) else None
    if source_snapshot is not None:
        ctx.operation_files.verify_unchanged(source_snapshot)
        private_snapshot = ctx.operation_files.copy_input(source_snapshot.path, "write_patched_lk.img")
    else:
        private_snapshot = ctx.operation_files.copy_input(patched_lk, "write_patched_lk.img")
    if source_snapshot is None and profile.brand.lower() == "vivo":
        # A manually selected Vivo image must carry the reviewed LK/Xingc patch
        # structure. Compatibility mode may change the preload runtime, but it
        # never turns an arbitrary LK into a safe write input.
        InternalUblPatcher(
            image_profile=profile.xingc_patch_profile,
        ).validate_patched(private_snapshot.path)
    ctx.ensure_root(profile, preload_override)
    initial_slot = ctx.read_slot(device)
    ctx.ensure_not_cancelled()
    live_slot = _live_slot(ctx)
    if live_slot.active_suffix != initial_slot.active_suffix:
        raise VerificationError(
            f"活动槽位发生变化: 初始 {initial_slot.active_suffix}, 当前 {live_slot.active_suffix}，已停止写入"
        )

    assert ctx.adb is not None
    ops = PartitionOps(
        ctx.adb,
        profile,
        ctx.operation_files.directory,
        log=ctx.callbacks.log,
        cancelled=ctx.callbacks.cancelled,
    )
    target_block = ops.block_name("lk", initial_slot.inactive_suffix)
    ops.preflight_block_exists(target_block)
    target_capacity = ops.block_size(target_block)
    SafetyChecker.check_patched_lk_size_for_dd(private_snapshot.size, target_capacity)

    if source_snapshot is not None:
        ctx.operation_files.verify_unchanged(source_snapshot)
    ctx.operation_files.verify_unchanged(private_snapshot)
    ctx.ensure_not_cancelled()
    ctx.operation_files.verify_unchanged(private_snapshot)
    operation_id = uuid.uuid4().hex
    remote_lk = f"/data/local/tmp/nebumtk-lk-{operation_id}.img"
    remote_misc = f"/data/local/tmp/nebumtk-misc-{operation_id}.img"
    misc = ctx.loader.resolve_misc(profile.misc_brand, initial_slot.active_suffix)
    misc_snapshot = ctx.operation_files.copy_input(misc, "write_misc.img")

    def before_write() -> None:
        devices = ctx.adb.devices()
        if len(devices) != 1 or devices[0] != device.serial:
            raise VerificationError("ADB 写入边界必须且只能包含当前设备")
        final_slot = _live_slot(ctx)
        if final_slot.active_suffix != initial_slot.active_suffix:
            raise VerificationError(
                f"活动槽位发生变化: 初始 {initial_slot.active_suffix}, 当前 {final_slot.active_suffix}，已停止写入"
            )
        if source_snapshot is not None:
            ctx.operation_files.verify_unchanged(source_snapshot)
        ctx.operation_files.verify_unchanged(private_snapshot)
        ctx.operation_files.verify_unchanged(misc_snapshot)
        final_capacity = ops.block_size(target_block)
        SafetyChecker.check_patched_lk_size_for_dd(
            private_snapshot.size,
            final_capacity,
        )
        if ctx.adb.remote_file_size(remote_lk, root=False) != private_snapshot.size:
            raise VerificationError("远端 patched LK 大小校验失败")
        if ctx.adb.remote_sha256(remote_lk, root=False) != private_snapshot.sha256:
            raise VerificationError("远端 patched LK 哈希校验失败")
        if ctx.adb.remote_file_size(remote_misc, root=False) != misc_snapshot.size:
            raise VerificationError("远端 misc 大小校验失败")
        if ctx.adb.remote_sha256(remote_misc, root=False) != misc_snapshot.sha256:
            raise VerificationError("远端 misc 哈希校验失败")

    try:
        ctx.adb.push(private_snapshot.path, remote_lk)
        ctx.ensure_not_cancelled()
        ctx.adb.push(misc_snapshot.path, remote_misc)
        ctx.ensure_not_cancelled()
        # Once this pair starts, finish LK and misc writes together. Killing a
        # live dd can leave the device in a less recoverable state.
        ops.write_inactive_lk_with_setrw(
            remote_lk,
            initial_slot,
            before_write=before_write,
        )
        ops.write_misc(remote_misc)
    finally:
        ctx.adb.remove_remote(remote_lk, root=False)
        ctx.adb.remove_remote(remote_misc, root=False)

    if enter_bootloader:
        ctx.ensure_not_cancelled()
        ctx.init_clients(device.serial, need_adb=True, need_fastboot=True, profile=profile)
        ctx.reboot_to_fastbootd_then_bootloader()


class RootEscalationFlow:
    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings,
        preload_override: Path | str | None = None,
        callbacks: FlowCallbacks | None = None,
    ) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.preload_override = preload_override

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=True, need_fastboot=False, profile=profile)
        ctx.ensure_not_cancelled()
        ctx.set_progress(60, "提权")
        ctx.escalate_root(profile, self.preload_override)
        ctx.set_progress(100, "提权完成")


class BackupExportFlow:
    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings,
        preload_override: Path | str | None = None,
        callbacks: FlowCallbacks | None = None,
    ) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.preload_override = preload_override

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=True, need_fastboot=False, profile=profile)
        ctx.set_progress(15, "确认 root")
        _backup_lk(ctx, device, profile, self.preload_override)
        ctx.set_progress(100, "备份流程完成")


class RestoreVivoDynFlow:
    """Restore one verified Desktop dyn backup through an existing root shell."""

    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings,
        backup_directory: Path,
        callbacks: FlowCallbacks | None = None,
    ) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.backup_directory = Path(backup_directory)

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        if profile.brand.lower() != "vivo":
            raise VerificationError("Vivo dyn 还原仅适用于 Vivo")
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=True, need_fastboot=False, profile=profile)
        assert ctx.adb is not None
        ctx.set_progress(20, "校验已选择的 Vivo dyn 备份")
        backup = load_dyn_backup(
            self.backup_directory,
            profile_key=profile.key,
            adb_serial=device.serial,
        )
        ctx.ensure_not_cancelled()
        ctx.set_progress(55, "确认已授权的 su shell")
        outcome = restore_dyn_backup(
            ctx.adb,
            backup,
            su_candidates=tuple(
                dict.fromkeys((ctx.adb.su_path, "su", "/system/bin/su"))
            ),
            cancelled=ctx.callbacks.cancelled,
        )
        if not outcome.restored:
            if outcome.reason == "root-unavailable":
                raise VerificationError(
                    "Vivo dyn 还原需要已在系统内授权的 root su；请先安装并授权 SakiSU、Magisk 或 KernelSU Manager"
                )
            raise VerificationError(f"Vivo dyn 未还原: {outcome.reason}")
        ctx.callbacks.log(f"Vivo dyn restored ({outcome.restored_files} files); reboot requested")
        ctx.set_progress(100, "Vivo dyn 还原完成，设备正在重启")


class WriteInactiveFlow:
    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings,
        patched_lk: Path,
        preload_override: Path | str | None = None,
        callbacks: FlowCallbacks | None = None,
    ) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.patched_lk = patched_lk
        self.preload_override = preload_override

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=True, need_fastboot=False, profile=profile)
        ctx.set_progress(25, "校验 patched LK")
        ctx.ensure_not_cancelled()
        ctx.set_progress(50, "确认临时提权并写入对槽")
        _write_inactive_lk(ctx, device, profile, self.patched_lk, preload_override=self.preload_override)
        ctx.set_progress(100, "写入完成，已进入 fastboot")


class VivoCompleteUnlockFlow:
    """Vivo unlock flow with an opt-in original-slot SakiSU init_boot patch."""

    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings,
        preload_override: Path | str | None = None,
        callbacks: FlowCallbacks | None = None,
        *,
        install_sakisu_root: bool = False,
    ) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.preload_override = preload_override
        self.install_sakisu_root = bool(install_sakisu_root)

    def _unlock_with_xingc(
        self,
        device: ConnectedDevice,
        initial_slot: SlotInfo,
    ) -> None:
        ctx = self.ctx
        assert ctx.fastboot is not None
        expected_slot = initial_slot.inactive_suffix
        live_slot = _require_bootloader_fastboot(ctx, device.serial)
        if live_slot != expected_slot:
            raise VerificationError(
                f"Xingc 前活动槽位异常: 预期 {expected_slot}，实际 {live_slot}"
            )
        service = XingcUnlockService(
            log=ctx.callbacks.log,
            cancelled=ctx.callbacks.cancelled,
        )
        ctx.ensure_not_cancelled()
        service.unlock(device.serial, ctx.fastboot)
        ctx.ensure_not_cancelled()
        live_slot = _require_bootloader_fastboot(ctx, device.serial)
        if live_slot != expected_slot:
            raise VerificationError(
                f"Xingc 后活动槽位异常: 预期 {expected_slot}，实际 {live_slot}"
            )
        if not ctx.fastboot.is_unlocked():
            raise VerificationError("Xingc 完成后 fastboot 未报告 unlocked，已终止运行")

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        if getattr(self.ctx.callbacks, "skip_profile_identity", False):
            raise VerificationError("Vivo 完整解锁不允许测试兼容模式")
        if profile.brand.lower() != "vivo":
            raise VerificationError("Vivo 完整解锁流程目前仅适用于 Vivo")
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=True, need_fastboot=True, profile=profile)

        ctx.set_progress(5, "检查内部 LK patcher")
        InternalUblPatcher.availability_check()
        ctx.ensure_not_cancelled()

        ctx.set_progress(12, "提权")
        ctx.escalate_root(profile, self.preload_override)

        ctx.set_progress(24, "备份活动槽分区")
        backups, initial_slot = _backup_lk(ctx, device, profile, self.preload_override)
        ctx.ensure_not_cancelled()

        root_image: VerifiedFileSnapshot | None = None
        if self.install_sakisu_root:
            ctx.set_progress(31, "准备原槽 init_boot 的 SakiSU LKM 补丁")
            root_image = _prepare_sakisu_root_image(ctx, backups)
            ctx.ensure_not_cancelled()

        ctx.set_progress(38, "修补本次备份 LK")
        source_lk = ctx.operation_files.copy_input(
            backups["lk"].local_path,
            "auto_source_lk.img",
        )
        patched_lk = _patch_lk_to_operation_file(
            ctx,
            source_lk,
            profile,
            copy_source=False,
        )

        ctx.set_progress(50, "首次写入对槽 LK 与 misc")
        _write_inactive_lk(
            ctx,
            device,
            profile,
            patched_lk,
            preload_override=self.preload_override,
            enter_bootloader=False,
        )
        ctx.ensure_not_cancelled()

        ctx.set_progress(58, "确认 fastbootd 并进入 bootloader Fastboot")
        ctx.reboot_to_fastbootd_then_bootloader()

        ctx.set_progress(66, "执行 Xingc 解锁")
        self._unlock_with_xingc(device, initial_slot)

        ctx.set_progress(74, "Xingc 完成，进入 fastbootd")
        ctx.reboot_bootloader_to_fastbootd()

        ctx.set_progress(82, "写入当前对槽 LK 并切回原槽")
        if root_image is None:
            _flash_original_slot_from_fastbootd(
                ctx,
                device,
                patched_lk,
                initial_slot,
            )
        else:
            _flash_original_slot_from_fastbootd(
                ctx,
                device,
                patched_lk,
                initial_slot,
                root_image=root_image,
            )

        ctx.set_progress(92, "等待系统启动")
        ctx.wait_android_ready()
        _verify_vivo_final_slot(ctx, initial_slot)
        ctx.set_progress(100, "Vivo 完整解锁流程完成")


class PatchLkFlow:
    """Patch an explicit LK image without connecting to a device."""

    def __init__(self, project_root: Path, workspace_root: Path, settings, callbacks: FlowCallbacks | None = None) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)

    def run_local(self, source_lk: Path, profile: DeviceProfile, output_lk: Path) -> Path:
        if profile.brand.lower() != "vivo":
            raise VerificationError("内部 LK 补丁目前仅适用于 Vivo")
        if Path(source_lk).resolve(strict=False) == Path(output_lk).resolve(strict=False):
            raise VerificationError("source_lk 与 output_lk 不能是同一路径")
        self.ctx.set_progress(15, "检查内部 LK patcher")
        InternalUblPatcher.availability_check()
        self.ctx.set_progress(35, "复制并验证源 LK")
        patched = _patch_lk_to_operation_file(self.ctx, source_lk, profile)
        self.ctx.set_progress(85, "导出 patched LK")
        output_lk.parent.mkdir(parents=True, exist_ok=True)
        pending = output_lk.parent / f".{output_lk.name}.pending-{uuid.uuid4().hex}"
        try:
            shutil.copyfile(patched.path, pending)
            pending_snapshot = self.ctx.operation_files.copy_input(pending, "export_verify.img")
            if pending_snapshot.size != patched.size or pending_snapshot.sha256 != patched.sha256:
                raise VerificationError("pending 导出文件校验失败")
            pending.replace(output_lk)
        except Exception:
            pending.unlink(missing_ok=True)
            raise
        self.ctx.set_progress(100, "LK 修补完成，未写入设备")
        return output_lk


class PatchInitBootFlow:
    """Patch one local Vivo init_boot with SakiSU 4.3 without flashing it."""

    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings,
        source_init_boot: Path,
        output_init_boot: Path,
        callbacks: FlowCallbacks | None = None,
    ) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.source_init_boot = Path(source_init_boot)
        self.output_init_boot = Path(output_init_boot)

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> Path:
        if profile.brand.lower() != "vivo":
            raise VerificationError("SakiSU init_boot 修补目前仅适用于 Vivo")
        if self.source_init_boot.resolve(strict=False) == self.output_init_boot.resolve(strict=False):
            raise VerificationError("source init_boot 与 output init_boot 不能是同一路径")
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=True, need_fastboot=False, profile=profile)
        assert ctx.adb is not None
        ctx.set_progress(15, "复制并校验本地 init_boot")
        source = ctx.operation_files.copy_input(
            self.source_init_boot,
            "source_init_boot.img",
        )
        ctx.operation_files.verify_unchanged(source)
        ctx.ensure_not_cancelled()
        ctx.set_progress(40, "读取设备 KMI")
        kernel_release = ctx.adb.kernel_release()
        ctx.ensure_not_cancelled()
        ctx.set_progress(55, "准备 SakiSU 4.3 LKM 修补器")
        executable = ctx.loader.materialize_sakisu_patcher(
            ctx.operation_files.directory / "sakisu-ksud.exe"
        )
        ctx.ensure_not_cancelled()
        ctx.set_progress(70, "修补 init_boot")
        patched = SakiSUPatcher(executable).prepare(
            source,
            kernel_release,
            ctx.operation_files,
        )
        ctx.operation_files.verify_unchanged(source)
        ctx.operation_files.verify_unchanged(patched)
        ctx.set_progress(88, "导出 patched init_boot")
        self.output_init_boot.parent.mkdir(parents=True, exist_ok=True)
        pending = self.output_init_boot.parent / (
            f".{self.output_init_boot.name}.pending-{uuid.uuid4().hex}"
        )
        try:
            shutil.copyfile(patched.path, pending)
            pending_snapshot = ctx.operation_files.copy_input(
                pending,
                "export_verify_init_boot.img",
            )
            if (
                pending_snapshot.size != patched.size
                or pending_snapshot.sha256 != patched.sha256
            ):
                raise VerificationError("patched init_boot 导出校验失败")
            pending.replace(self.output_init_boot)
        except Exception:
            pending.unlink(missing_ok=True)
            raise
        ctx.set_progress(100, "SakiSU init_boot 修补完成，未写入设备")
        return self.output_init_boot


def _require_bootloader_fastboot(ctx: FlowContext, serial: str) -> str:
    assert ctx.fastboot is not None
    devices = ctx.fastboot.devices()
    if len(devices) != 1 or set(devices) != {serial}:
        raise VerificationError("fastboot 设备集合必须且只能包含当前 serial")
    mode = ctx.fastboot.fastboot_mode()
    if mode != "bootloader":
        raise VerificationError("Fastboot-only 流程要求 bootloader Fastboot（is-userspace=no）")
    slot = ctx.fastboot.current_slot().strip().lower().lstrip("_")
    if slot not in {"a", "b"}:
        raise VerificationError(f"fastboot current-slot 非法: {slot or '<empty>'}")
    return f"_{slot}"


def _flash_original_slot_from_fastbootd(
    ctx: FlowContext,
    device: ConnectedDevice,
    patched_lk: VerifiedFileSnapshot,
    initial_slot: SlotInfo,
    *,
    root_image: VerifiedFileSnapshot | None = None,
) -> None:
    assert ctx.fastboot is not None
    expected_live = initial_slot.inactive_suffix.lstrip("_")
    target_suffix = initial_slot.active_suffix
    target_partition = f"lk{target_suffix}"
    root_partition = f"init_boot{target_suffix}" if root_image is not None else None
    wipe_misc_source = ctx.loader.resolve_vivo_wipe_misc(target_suffix)
    wipe_misc = ctx.operation_files.copy_input(
        wipe_misc_source,
        f"vivo_wipe_data_to_{target_suffix.lstrip('_')}.img",
    )

    def validate_boundary() -> None:
        devices = ctx.fastboot.devices()
        if len(devices) != 1 or devices[0] != device.serial:
            raise VerificationError("fastbootd 设备集合必须且只能包含当前 serial")
        mode = ctx.fastboot.fastboot_mode()
        if mode != "fastbootd":
            raise VerificationError(
                f"第二次 LK 写入要求 fastbootd（is-userspace=yes），实际模式: {mode}"
            )
        live_slot = ctx.fastboot.current_slot().strip().lower().lstrip("_")
        if live_slot != expected_live:
            raise VerificationError(
                f"第二次 LK 写入槽位异常: 预期 {expected_live}，实际 {live_slot or '<empty>'}"
            )
        capacity = ctx.fastboot.partition_size(target_partition)
        SafetyChecker.check_patched_lk_size_for_dd(patched_lk.size, capacity)
        ctx.operation_files.verify_unchanged(patched_lk)
        wipe_capacity = ctx.fastboot.partition_size("misc")
        if wipe_misc.size > wipe_capacity:
            raise VerificationError(
                f"Vivo wipe misc ({wipe_misc.size}) 大于目标分区容量 ({wipe_capacity})，禁止写入"
            )
        ctx.operation_files.verify_unchanged(wipe_misc)
        if root_image is not None:
            root_capacity = ctx.fastboot.partition_size(root_partition)
            if root_image.size > root_capacity:
                raise VerificationError(
                    f"patched init_boot ({root_image.size}) 大于目标分区容量 ({root_capacity})，禁止写入"
                )
            ctx.operation_files.verify_unchanged(root_image)

    validate_boundary()
    ctx.ensure_not_cancelled()
    validate_boundary()

    # This is a non-cancellable boundary. The final misc both returns to the
    # original slot and asks recovery to wipe data, so fastboot set_active must
    # not run here.
    ctx.fastboot.flash_lk(target_suffix, patched_lk.path)
    if root_image is not None:
        ctx.fastboot.flash_partition(root_partition, root_image.path)
    ctx.fastboot.flash_partition("misc", wipe_misc.path)
    ctx.fastboot.reboot_checked()


def _verify_vivo_final_slot(ctx: FlowContext, initial_slot: SlotInfo) -> None:
    """Confirm the wipe misc booted Android back into the original slot."""

    assert ctx.adb is not None
    returned_slot = SlotManager.from_suffix(ctx.adb.getprop("ro.boot.slot_suffix").strip())
    if returned_slot.active_suffix != initial_slot.active_suffix:
        raise VerificationError(
            "Vivo 收尾后系统未回到原槽: "
            f"期望 {initial_slot.active_suffix}，实际 {returned_slot.active_suffix}"
        )


def _validate_dual_lk_capacity(ctx: FlowContext, image: VerifiedFileSnapshot) -> None:
    assert ctx.fastboot is not None
    for partition in ("lk_a", "lk_b"):
        capacity = ctx.fastboot.partition_size(partition)
        SafetyChecker.check_patched_lk_size_for_dd(image.size, capacity)


def _finalize_explicit_lk_fastboot(
    ctx: FlowContext,
    device: ConnectedDevice,
    image: Path,
    copy_name: str,
    *,
    require_unlocked: bool = False,
    require_patched: bool = False,
    backup_validation: BackupLkValidation | None = None,
    image_profile: str | None = None,
) -> None:
    assert ctx.fastboot is not None
    snapshot = ctx.operation_files.copy_input(image, copy_name)
    if backup_validation is not None and (
        snapshot.size != backup_validation.size
        or snapshot.sha256 != backup_validation.sha256
    ):
        raise VerificationError("所选原 LK 在归属校验后发生变化")
    initial_live_slot = _require_bootloader_fastboot(ctx, device.serial)
    if device.slot_suffix and device.slot_suffix != initial_live_slot:
        raise VerificationError(
            f"活动槽位发生变化: 初始 {device.slot_suffix}, 当前 {initial_live_slot}，已停止刷写"
        )
    ctx.wait_fastboot()
    current_live_slot = _require_bootloader_fastboot(ctx, device.serial)
    if current_live_slot != initial_live_slot:
        raise VerificationError(
            f"活动槽位发生变化: 初始 {initial_live_slot}, 当前 {current_live_slot}，已停止刷写"
        )
    if require_unlocked and not ctx.fastboot.is_unlocked():
        raise VerificationError("fastboot 报告未解锁。请先完成内部 Xingc 解锁，再执行双槽 flash")
    if require_patched:
        InternalUblPatcher(image_profile=image_profile).validate_patched(snapshot.path)
    _validate_dual_lk_capacity(ctx, snapshot)
    ctx.operation_files.verify_unchanged(snapshot)

    def before_each(suffix: str) -> None:
        live_slot = _require_bootloader_fastboot(ctx, device.serial)
        if live_slot != initial_live_slot:
            raise VerificationError(
                f"活动槽位发生变化: 初始 {initial_live_slot}, 当前 {live_slot}，已停止刷写"
            )
        capacity = ctx.fastboot.partition_size(f"lk{suffix}")
        SafetyChecker.check_patched_lk_size_for_dd(snapshot.size, capacity)
        ctx.operation_files.verify_unchanged(snapshot)

    ctx.fastboot.flash_dual_lk(snapshot.path, before_each=before_each)
    ctx.fastboot.set_active(initial_live_slot.lstrip("_"))
    ctx.fastboot.reboot_checked()


class OppoUnlockFlow:
    def __init__(self, project_root: Path, workspace_root: Path, settings, callbacks: FlowCallbacks | None = None) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        if profile.brand.lower() != "oppo":
            raise VerificationError("flashing unlock 仅适用于 OPPO")
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=False, need_fastboot=True)
        _require_bootloader_fastboot(ctx, device.serial)
        ctx.set_progress(20, "等待 fastboot")
        ctx.wait_fastboot()
        ctx.ensure_not_cancelled()
        _require_bootloader_fastboot(ctx, device.serial)
        assert ctx.fastboot is not None
        if ctx.fastboot.is_unlocked():
            ctx.callbacks.log("bootloader 已解锁，跳过重复 unlock")
            ctx.set_progress(100, "设备已处于 unlocked")
            return
        ctx.set_progress(60, "flashing unlock")
        ctx.fastboot.flashing_unlock_with_retry(max_retries=3)
        ctx.set_progress(100, "unlock 完成")


class RestoreBackupLkFlow:
    def __init__(self, project_root: Path, workspace_root: Path, settings, source_lk: Path, callbacks: FlowCallbacks | None = None) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.source_lk = source_lk

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=False, need_fastboot=True)
        ctx.set_progress(20, "等待 fastboot")
        _require_bootloader_fastboot(ctx, device.serial)
        validation = validate_restore_lk_backup(
            self.source_lk,
            profile_key=profile.key,
            fastboot_serial=device.serial,
            log=ctx.callbacks.log,
        )
        ctx.set_progress(60, "双槽 flash 原 lk")
        _finalize_explicit_lk_fastboot(
            ctx,
            device,
            self.source_lk,
            "restore_source_lk.img",
            backup_validation=validation,
        )
        ctx.set_progress(100, "还原完成")


class VivoXingcUnlockFlow:
    def __init__(self, project_root: Path, workspace_root: Path, settings, callbacks: FlowCallbacks | None = None) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        if profile.brand.lower() != "vivo":
            raise VerificationError("Xingc 解锁仅适用于 Vivo")
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=False, need_fastboot=True)
        _require_bootloader_fastboot(ctx, device.serial)
        ctx.set_progress(15, "等待 fastboot")
        ctx.wait_fastboot()
        assert ctx.fastboot is not None
        ctx.set_progress(35, "检查 Xingc USB 环境")
        service = XingcUnlockService(log=ctx.callbacks.log, cancelled=ctx.callbacks.cancelled)
        ctx.ensure_not_cancelled()
        _require_bootloader_fastboot(ctx, device.serial)
        ctx.set_progress(60, "执行 Xingc 授权")
        service.unlock(device.serial, ctx.fastboot)
        ctx.set_progress(100, "Xingc 解锁完成")


class DualSlotSyncFlow:
    def __init__(self, project_root: Path, workspace_root: Path, settings, patched_lk: Path | str, callbacks: FlowCallbacks | None = None) -> None:
        self.ctx = FlowContext(project_root, workspace_root, settings, callbacks)
        self.patched_lk = patched_lk

    def run(self, device: ConnectedDevice, profile: DeviceProfile) -> None:
        brand = profile.brand.lower()
        if brand not in {"vivo", "honor"}:
            raise VerificationError("当前 patched LK 双槽同步仅支持 Vivo 或 Honor")
        ctx = self.ctx
        ctx.init_clients(device.serial, need_adb=False, need_fastboot=True)
        ctx.set_progress(20, "等待 fastboot")
        ctx.set_progress(60, "双槽 flash patched lk")
        image = self.patched_lk
        if isinstance(image, str) and image.startswith("builtin:"):
            expected_ref = f"builtin:{profile.fastboot_artifact}"
            if brand != "honor" or not profile.fastboot_artifact or image != expected_ref:
                raise VerificationError("内置 fastboot LK 只允许使用当前 Honor profile 的受保护 artifact")
            image = ctx.loader.resolve_fastboot_artifact(profile.fastboot_artifact)
        _finalize_explicit_lk_fastboot(
            ctx,
            device,
            Path(image),
            "finalize_patched_lk.img",
            require_unlocked=True,
            require_patched=brand == "vivo",
            image_profile=profile.xingc_patch_profile,
        )
        ctx.set_progress(100, "收尾完成")
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
