# SPDX-License-Identifier: AGPL-3.0-only
"""Flow factory."""

from __future__ import annotations

from pathlib import Path

from ..config.store import Settings
from .base import FlowCallbacks
from .stages import (
    BackupExportFlow,
    DualSlotSyncFlow,
    OppoUnlockFlow,
    PatchInitBootFlow,
    PatchLkFlow,
    RestoreBackupLkFlow,
    RestoreVivoDynFlow,
    RootEscalationFlow,
    VivoCompleteUnlockFlow,
    VivoXingcUnlockFlow,
    WriteInactiveFlow,
)

FlowKind = str

FASTBOOT_FLOWS = frozenset(
    {"oppo_unlock", "restore_backup_lk", "vivo_xingc_unlock", "dual_slot_sync"}
)
LOCAL_FLOWS = frozenset({"patch_lk"})


def create_flow(
    flow_kind: FlowKind,
    project_root: Path,
    workspace_root: Path,
    settings: Settings,
    callbacks: FlowCallbacks | None = None,
    patched_lk: Path | str | None = None,
    source_lk: Path | None = None,
    source_dyn_backup: Path | None = None,
    source_init_boot: Path | None = None,
    output_init_boot: Path | None = None,
    preload_override: Path | str | None = None,
    install_sakisu_root: bool = False,
):
    common = (project_root, workspace_root, settings, preload_override, callbacks)
    if flow_kind == "root_escalation":
        return RootEscalationFlow(*common)
    if flow_kind == "backup_export":
        return BackupExportFlow(*common)
    if flow_kind == "vivo_complete_unlock":
        return VivoCompleteUnlockFlow(
            *common,
            install_sakisu_root=bool(install_sakisu_root),
        )
    if flow_kind == "write_inactive":
        if patched_lk is None:
            raise ValueError("写入阶段需要 patched lk 路径")
        return WriteInactiveFlow(project_root, workspace_root, settings, patched_lk, preload_override, callbacks)
    if flow_kind == "patch_lk":
        return PatchLkFlow(project_root, workspace_root, settings, callbacks)
    if flow_kind == "patch_init_boot":
        if source_init_boot is None or output_init_boot is None:
            raise ValueError("修补 init_boot 需要显式选择输入和输出文件")
        return PatchInitBootFlow(
            project_root,
            workspace_root,
            settings,
            source_init_boot,
            output_init_boot,
            callbacks,
        )
    if flow_kind == "oppo_unlock":
        return OppoUnlockFlow(project_root, workspace_root, settings, callbacks)
    if flow_kind == "restore_backup_lk":
        if source_lk is None:
            raise ValueError("还原 LK 需要显式选择桌面备份中的原 LK")
        return RestoreBackupLkFlow(project_root, workspace_root, settings, source_lk, callbacks)
    if flow_kind == "restore_vivo_dyn":
        if source_dyn_backup is None:
            raise ValueError("还原 Vivo dyn 需要显式选择桌面备份目录")
        return RestoreVivoDynFlow(
            project_root,
            workspace_root,
            settings,
            source_dyn_backup,
            callbacks,
        )
    if flow_kind == "vivo_xingc_unlock":
        return VivoXingcUnlockFlow(project_root, workspace_root, settings, callbacks)
    if flow_kind == "dual_slot_sync":
        if patched_lk is None:
            raise ValueError("双槽同步需要显式选中 patched LK")
        return DualSlotSyncFlow(project_root, workspace_root, settings, patched_lk, callbacks)
    raise ValueError(f"未知 flow_kind: {flow_kind}")
