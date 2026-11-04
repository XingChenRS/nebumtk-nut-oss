# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Operator-built partition checklist.

The two transports are deliberately different, because the hardware is:

* ADB (rooted) can **read** and **write** a partition byte-for-byte. Both go
  through ``dd``; a write is preceded by ``blockdev --setrw`` and every write
  path is ``/dev/block/by-name/<partition>``.
* Fastboot can only **write** (``flash``) and **erase** (``erase``). It has no
  way to read a partition back.

Items run one at a time on the calling thread; the GUI runs this inside a
worker so the window stays responsive, and every step checks cancellation
before it touches the device. Deliberately *not* enforced here: a per-write
image re-hash and an "exactly one fastboot device" rule — the operator is
driving an explicit list against one already-confirmed serial.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import FlowCancelledError, PartitionError, VerificationError
from .operation_files import OperationFiles
from .partition import PartitionInventory, PartitionOps

ADB_ACTIONS: tuple[str, ...] = ("read", "write")
FASTBOOT_ACTIONS: tuple[str, ...] = ("write", "erase")
ACTION_LABELS = {"read": "读取", "write": "写入", "erase": "擦除"}
FASTBOOT_MODES = frozenset({"fastboot", "bootloader", "fastbootd"})


def actions_for_mode(mode: str) -> tuple[str, ...]:
    """Which per-partition actions the current transport can actually do."""

    normalized = str(mode).strip().casefold()
    if normalized == "adb":
        return ADB_ACTIONS
    if normalized in FASTBOOT_MODES:
        return FASTBOOT_ACTIONS
    raise VerificationError(f"不支持的分区操作模式: {mode or '<empty>'}")


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    partition: str
    action: str
    image: Path | None = None

    def __post_init__(self) -> None:
        if self.action not in ACTION_LABELS:
            raise VerificationError(f"未知分区动作: {self.action}")
        if self.action == "write":
            if self.image is None:
                raise VerificationError(f"写入 {self.partition} 需要先选择镜像")
            source = Path(self.image)
            if not source.is_file() or source.stat().st_size <= 0:
                raise VerificationError(f"写入 {self.partition} 的镜像不存在或为空")

    @property
    def label(self) -> str:
        return f"{self.partition} · {ACTION_LABELS[self.action]}"


@dataclass(frozen=True, slots=True)
class ChecklistOutcome:
    label: str
    detail: str


class PartitionChecklistRunner:
    """Run a list of :class:`ChecklistItem` against one confirmed device."""

    def __init__(
        self,
        adb,
        fastboot,
        ops: PartitionOps | None,
        *,
        operation_files: OperationFiles,
        inventory: PartitionInventory | None = None,
        export_dir: Path | None = None,
        log: Callable[[str], None],
        cancelled: Callable[[], bool],
    ) -> None:
        self.adb = adb
        self.fastboot = fastboot
        self.ops = ops
        self.inventory = inventory
        self.operation_files = operation_files
        self.export_dir = Path(export_dir) if export_dir is not None else None
        self.log = log
        self.cancelled = cancelled

    def _require_adb(self) -> PartitionOps:
        if self.ops is None:
            raise VerificationError("读取/写入需要已确认的 ADB 连接")
        return self.ops

    # -- transport ---------------------------------------------------------

    @staticmethod
    def mode_of(adb, fastboot) -> str:
        if fastboot is not None:
            return "fastboot"
        if adb is not None:
            return "adb"
        raise VerificationError("分区操作需要 ADB 或 Fastboot 连接")

    def run(self, items: list[ChecklistItem]) -> list[ChecklistOutcome]:
        if not items:
            raise VerificationError("分区清单为空")
        mode = self.mode_of(self.adb, self.fastboot)
        allowed = set(actions_for_mode(mode))
        for item in items:
            if item.action not in allowed:
                raise VerificationError(
                    f"{mode} 模式不支持「{ACTION_LABELS[item.action]}」: {item.partition}"
                )
        outcomes: list[ChecklistOutcome] = []
        for index, item in enumerate(items, start=1):
            if self.cancelled():
                raise FlowCancelledError("分区清单已被取消")
            self.log(f"[{index}/{len(items)}] {item.label}")
            detail = self._run_item(item)
            outcomes.append(ChecklistOutcome(item.label, detail))
        return outcomes

    def _run_item(self, item: ChecklistItem) -> str:
        if item.action == "read":
            return self._read(item)
        if item.action == "write":
            return self._write(item)
        return self._erase(item)

    # -- per action --------------------------------------------------------

    def _entry_size(self, partition: str) -> int:
        if self.inventory is None:
            raise VerificationError("读取需要先刷新分区清单")
        try:
            return self.inventory.by_name(partition).size
        except (KeyError, VerificationError) as exc:
            raise VerificationError(f"分区不存在: {partition}") from exc

    def _read(self, item: ChecklistItem) -> str:
        assert self.adb is not None
        ops = self._require_adb()
        if self.export_dir is None:
            raise VerificationError("读取需要一个桌面导出目录")
        expected = self._entry_size(item.partition)
        ops.preflight_block_exists(item.partition)
        remote = f"{PartitionOps.REMOTE_TMP}/{item.partition}.read.bin"
        self.adb.remove_remote(remote, strict=True)
        local = self.operation_files.directory / f"read_{item.partition}.bin"
        try:
            ops._dd_read(item.partition, remote)
            remote_size = self.adb.remote_file_size(remote, root=False)
            if remote_size != expected:
                raise PartitionError(
                    f"{item.partition} 读取大小与清单不符: {remote_size} != {expected}"
                )
            self.adb.pull(remote, local, cancelled=self.cancelled)
            if local.stat().st_size != expected:
                raise PartitionError(f"{item.partition} 本地落盘大小不符")
            published = self.export_dir / local.name
            published.parent.mkdir(parents=True, exist_ok=True)
            published.write_bytes(local.read_bytes())
            local.unlink(missing_ok=True)
        finally:
            try:
                self.adb.remove_remote(remote, strict=True)
            except Exception as exc:  # pragma: no cover - cleanup best effort
                self.log(f"远端临时文件清理失败: {exc}")
        return f"{published.name} · {expected} 字节"

    def _write(self, item: ChecklistItem) -> str:
        assert item.image is not None
        source = Path(item.image)
        if self.fastboot is not None:
            self.fastboot.flash_partition(item.partition, source)
            return f"{source.name} → {item.partition}"
        assert self.adb is not None
        ops = self._require_adb()
        # Capacity is the one rail worth keeping: an oversized image cannot be
        # written cleanly and a half-written partition is worse than refusing.
        capacity = ops.block_size(item.partition)
        if source.stat().st_size > capacity:
            raise PartitionError(
                f"{source.name} ({source.stat().st_size}) 大于 {item.partition} 容量 ({capacity})"
            )
        ops.setrw_block(item.partition)
        remote = f"{PartitionOps.REMOTE_TMP}/{item.partition}.write.bin"
        try:
            self.adb.push(source, remote)
            if self.cancelled():
                raise FlowCancelledError("分区清单已被取消")
            ops._dd_write(remote, item.partition)
        finally:
            try:
                self.adb.remove_remote(remote, strict=True)
            except Exception as exc:  # pragma: no cover - cleanup best effort
                self.log(f"远端临时文件清理失败: {exc}")
        return f"{source.name} → {item.partition}"

    def _erase(self, item: ChecklistItem) -> str:
        if self.fastboot is None:
            raise VerificationError("擦除只在 Fastboot 下提供")
        self.fastboot.erase_partition(item.partition)
        return f"{item.partition} 已擦除"
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
