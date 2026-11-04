# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Generic partition operations: read the list, back a selection up, or run an
operator-built checklist against one confirmed device.

Two transports, matching what the hardware actually allows:

* ADB (rooted) — read and write a partition; both go through ``dd``.
* Fastboot — write and erase only; there is no way to read a partition back.

Everything here is deliberately explicit: the operator names the partitions and
the images, and nothing is inferred from a brand workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config.store import Settings
from ..core.adb import AdbClient
from ..core.backup_export import desktop_export_directory, publish_to_desktop
from ..core.env import EnvChecker
from ..core.errors import FlowCancelledError, VerificationError
from ..core.fastboot import FastbootClient
from ..core.generic_contracts import GenericDevice, GenericProfile
from ..core.operation_files import OperationFiles
from ..core.partition import PartitionInventory, PartitionOps
from ..core.partition_checklist import (
    ChecklistItem,
    ChecklistOutcome,
    PartitionChecklistRunner,
    actions_for_mode,
)
from ..core.safety import SafetyChecker
from ..core.slot import SlotManager
from ..core.su import probe_custom_su, validate_custom_su
from ..core.workspace import WorkspaceManager


@dataclass(frozen=True, slots=True)
class InventoryRequest:
    device: GenericDevice
    profile: GenericProfile
    su_executable: str


@dataclass(frozen=True, slots=True)
class BackupSelectedRequest(InventoryRequest):
    selected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChecklistRequest:
    """One operator-built list, run against the transport it was built for."""

    device: GenericDevice
    profile: GenericProfile
    mode: str
    items: tuple[ChecklistItem, ...]
    su_executable: str = ""

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("分区清单不能为空")
        allowed = set(actions_for_mode(self.mode))
        for item in self.items:
            if item.action not in allowed:
                raise ValueError(f"清单动作与 {self.mode} 模式不符: {item.action}")


@dataclass(slots=True)
class GenericCallbacks:
    log: Callable[[str], None] = field(default=lambda _message: None)


class GenericPartitionFacade:
    """Perform local operations without importing or issuing protected operations."""

    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings: Settings,
        callbacks: GenericCallbacks | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.workspace = WorkspaceManager(Path(workspace_root))
        self.settings = settings
        self.callbacks = callbacks or GenericCallbacks()

    def read_inventory(self, adb: AdbClient) -> PartitionInventory:
        """List the partitions, reporting any `by-name` entry that was skipped.

        A dangling `by-name` entry is skipped rather than failing the listing,
        so the count has to be visible: otherwise a partition that simply is not
        there looks the same as one the device never reported.
        """

        inventory = PartitionInventory.from_adb(adb)
        if inventory.skipped_entries:
            self.callbacks.log(
                f"分区表：{len(inventory.entries)} 个可用条目，"
                f"{inventory.skipped_entries} 个 by-name 项指向的节点不存在，已跳过"
            )
        return inventory

    def inventory(self, request: InventoryRequest, *, cancelled=None) -> PartitionInventory:
        cancelled = cancelled or (lambda: False)
        operation, adb = self._adb_session(request, cancelled)
        try:
            self._ensure_not_cancelled(cancelled)
            return self.read_inventory(adb)
        finally:
            operation.cleanup()

    def backup_selected(self, request: BackupSelectedRequest, *, cancelled=None):
        cancelled = cancelled or (lambda: False)
        operation, adb = self._adb_session(request, cancelled)
        try:
            inventory = self.read_inventory(adb)
            slot = self._live_slot(adb, request.device)
            ops = PartitionOps(
                adb,
                request.profile,
                operation.directory,
                log=self.callbacks.log,
                cancelled=cancelled,
            )
            records = ops.backup_selected(
                inventory,
                request.selected,
                operation_files=operation,
            )
            self._ensure_not_cancelled(cancelled)
            return publish_to_desktop(
                records,
                model_key=request.profile.key,
                serial=request.device.serial,
                product=request.device.device or request.device.model or "unknown",
                initial_slot=slot,
            )
        finally:
            operation.cleanup()

    def checklist(
        self,
        request: ChecklistRequest,
        *,
        cancelled=None,
        inventory: PartitionInventory | None = None,
    ) -> list[ChecklistOutcome]:
        """Run the operator's list one item at a time.

        ``inventory`` is the list the operator already read over ADB: partition
        sizes do not change with the transport, so a caller that has one for
        this serial passes it in and the fastboot path needs no extra query.
        """

        cancelled = cancelled or (lambda: False)
        if request.mode == "adb":
            operation, adb = self._adb_session(request, cancelled)
            try:
                live = inventory or PartitionInventory.from_adb(adb)
                ops = PartitionOps(
                    adb,
                    request.profile,
                    operation.directory,
                    log=self.callbacks.log,
                    cancelled=cancelled,
                )
                runner = PartitionChecklistRunner(
                    adb,
                    None,
                    ops,
                    operation_files=operation,
                    inventory=live,
                    export_dir=desktop_export_directory(
                        model_key=request.profile.key,
                        serial=str(request.device.serial),
                    ),
                    log=self.callbacks.log,
                    cancelled=cancelled,
                )
                return runner.run(list(request.items))
            finally:
                operation.cleanup()

        serial = str(request.device.serial).strip()
        if not serial:
            raise VerificationError("分区操作需要精确的 Fastboot serial")
        tools = EnvChecker(self.settings).validate_fastboot()
        operation = self.workspace.operation_files("generic-partition")
        try:
            runner = PartitionChecklistRunner(
                None,
                FastbootClient(
                    tools.fastboot,
                    serial=serial,
                    log=self.callbacks.log,
                    cancelled=cancelled,
                ),
                None,
                operation_files=operation,
                inventory=inventory,
                log=self.callbacks.log,
                cancelled=cancelled,
            )
            return runner.run(list(request.items))
        finally:
            operation.cleanup()

    def _adb_session(self, request: InventoryRequest, cancelled) -> tuple[OperationFiles, AdbClient]:
        self._ensure_not_cancelled(cancelled)
        serial = str(request.device.serial).strip()
        if not serial:
            raise VerificationError("generic partition operation requires an exact ADB serial")
        executable = validate_custom_su(request.su_executable)
        tools = EnvChecker(self.settings).validate_adb()
        self._ensure_not_cancelled(cancelled)
        adb = AdbClient(
            tools.adb,
            serial=serial,
            log=self.callbacks.log,
            su_path=executable,
            cancelled=cancelled,
        )
        if adb.devices() != [serial] or adb.get_state() != "device":
            raise VerificationError("generic partition operation requires one exact ADB device")
        SafetyChecker.check_device_profile(request.device, request.profile)
        probe = probe_custom_su(adb, executable)
        if probe.uid != 0:
            raise VerificationError("selected su provider is not root")
        return self.workspace.operation_files("generic-partition"), adb

    @staticmethod
    def _live_slot(adb: AdbClient, device: GenericDevice) -> str:
        raw = (device.slot_suffix or "").strip() or adb.getprop("ro.boot.slot_suffix")
        return SlotManager.from_suffix(raw).active_suffix

    @staticmethod
    def _ensure_not_cancelled(cancelled) -> None:
        if cancelled():
            raise FlowCancelledError("generic partition operation cancelled")
