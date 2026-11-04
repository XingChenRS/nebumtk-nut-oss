"""Flow factory."""

from __future__ import annotations

from pathlib import Path

from ..core.operation_files import VerifiedFileSnapshot

from ..config.store import Settings
from .base import FlowCallbacks
from .stages import (
    BackupExportFlow,
    DualSlotSyncFlow,
    HonorCompleteUnlockFlow,
    HonorPrepareFlow,
    OppoUnlockFlow,
    PatchLkFlow,
    RestoreOriginalLkFlow,
    RootEscalationFlow,
    VivoCompleteUnlockFlow,
    VivoXingcLockFlow,
    VivoXingcUnlockFlow,
    WriteInactiveFlow,
)

FlowKind = str

FASTBOOT_FLOWS = frozenset(
    {
        "oppo_unlock",
        "restore_original_lk",
        "vivo_xingc_unlock",
        "vivo_xingc_lock",
        "dual_slot_sync",
        "dual_slot_apply",
        "honor_complete",
    }
)
LOCAL_FLOWS = frozenset({"patch_lk"})


def create_flow(
    flow_kind: FlowKind,
    project_root: Path,
    workspace_root: Path,
    settings: Settings,
    callbacks: FlowCallbacks | None = None,
    patched_lk: Path | VerifiedFileSnapshot | str | None = None,
    source_lk: Path | VerifiedFileSnapshot | None = None,
    preload_override: Path | str | None = None,
    backup_directory: Path | None = None,
    install_sakisu_root: bool = False,
    sakisu_init_boot: Path | VerifiedFileSnapshot | None = None,
    sakisu_kmi: str = "",
):
    common = (project_root, workspace_root, settings, preload_override, callbacks)
    if flow_kind == "root_escalation":
        return RootEscalationFlow(*common)
    if flow_kind == "backup_export":
        return BackupExportFlow(*common)
    if flow_kind == "vivo_complete_unlock":
        return VivoCompleteUnlockFlow(
            *common,
            install_sakisu_root=install_sakisu_root,
            sakisu_init_boot=sakisu_init_boot,
            sakisu_kmi=sakisu_kmi,
        )
    if flow_kind == "honor_prepare":
        if patched_lk is None or isinstance(patched_lk, str):
            raise ValueError("Honor 准备解锁需要显式选择本地 LK 镜像")
        return HonorPrepareFlow(
            project_root, workspace_root, settings, patched_lk, callbacks
        )
    if flow_kind == "honor_complete":
        if patched_lk is None or isinstance(patched_lk, str):
            raise ValueError("Honor 完整解锁需要显式选择本地 LK 镜像")
        return HonorCompleteUnlockFlow(
            project_root, workspace_root, settings, patched_lk, callbacks
        )
    if flow_kind == "restore_original_lk":
        if source_lk is None or isinstance(source_lk, str):
            raise ValueError("还原双槽原 LK 需要显式选择镜像")
        return RestoreOriginalLkFlow(
            project_root,
            workspace_root,
            settings,
            source_lk,
            callbacks,
        )
    if flow_kind == "write_inactive":
        if patched_lk is None:
            raise ValueError("写入阶段需要 patched lk 路径")
        return WriteInactiveFlow(project_root, workspace_root, settings, patched_lk, preload_override, callbacks)
    if flow_kind == "patch_lk":
        return PatchLkFlow(project_root, workspace_root, settings, callbacks)
    if flow_kind == "oppo_unlock":
        return OppoUnlockFlow(project_root, workspace_root, settings, callbacks)
    if flow_kind == "vivo_xingc_unlock":
        return VivoXingcUnlockFlow(project_root, workspace_root, settings, callbacks)
    if flow_kind == "vivo_xingc_lock":
        return VivoXingcLockFlow(project_root, workspace_root, settings, callbacks)
    if flow_kind == "dual_slot_sync":
        if patched_lk is None:
            raise ValueError("双槽同步需要显式选中 patched LK")
        return DualSlotSyncFlow(project_root, workspace_root, settings, patched_lk, callbacks)
    if flow_kind == "dual_slot_apply":
        if patched_lk is None:
            raise ValueError("分步执行需要显式选中目标镜像")
        return DualSlotSyncFlow(
            project_root,
            workspace_root,
            settings,
            patched_lk,
            callbacks,
            reboot_after=False,
        )
    raise ValueError(f"未知 flow_kind: {flow_kind}")
