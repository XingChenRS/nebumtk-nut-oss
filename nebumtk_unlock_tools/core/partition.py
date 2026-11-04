# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Partition backup, setrw, and dd operations (adb+root phase only)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .device import DeviceProfile
from .adb import AdbClient
from .errors import PartitionError
from .safety import SafetyChecker
from .slot import SlotInfo, SlotManager
from .verifier import StepVerifier


@dataclass
class BackupRecord:
    partition: str
    block_name: str
    remote_path: str
    local_path: Path


class PartitionOps:
    REMOTE_TMP = "/data/local/tmp"
    REMOTE_SDCARD = "/sdcard"

    def __init__(
        self,
        adb: AdbClient,
        profile: DeviceProfile,
        exports_dir: Path,
        log: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.adb = adb
        self.profile = profile
        self.exports_dir = exports_dir
        self.log = log or (lambda _message: None)
        self.cancelled = cancelled or (lambda: False)
        self.safety = SafetyChecker(adb, log=self.log)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def backup_partitions(self, slot: SlotInfo) -> dict[str, BackupRecord]:
        records: dict[str, BackupRecord] = {}
        for partition in self.profile.backup_partitions:
            partition_label = self.validate_partition_label(partition)
            if self.cancelled():
                from .errors import FlowCancelledError

                raise FlowCancelledError("用户已取消")
            block_name = self.block_name(partition_label, slot.active_suffix)
            self.safety.preflight_block_exists(block_name)
            remote = f"{self.REMOTE_TMP}/{partition_label}.bin"
            local = self._contained_export_path(f"{partition_label}.bin")

            # 清理旧残留，避免尺寸校验被误导
            self.adb.remove_remote(remote, strict=True)
            try:
                self._dd_read(block_name, remote)

                remote_size = self.adb.remote_file_size(remote, root=False)
                if remote_size <= 0:
                    raise PartitionError(f"备份 {partition_label} 后设备侧文件为空 (path={remote})")

                self.adb.pull(remote, local, cancelled=self.cancelled)
                local_size = local.stat().st_size
                if local_size <= 0:
                    raise PartitionError(f"pull {partition_label} 后本地文件为空: {local}")
                if remote_size != local_size:
                    raise PartitionError(
                        f"备份 {partition_label} pull 大小不一致: 设备={remote_size} 本地={local_size}"
                    )
                StepVerifier.check_file_size(local, step=f"备份 {partition_label}")

                records[partition_label] = BackupRecord(
                    partition=partition_label,
                    block_name=block_name,
                    remote_path=remote,
                    local_path=local,
                )
                self.log(f"已备份 {partition_label} -> {local} ({local_size} bytes)")
            finally:
                try:
                    self.adb.remove_remote(remote, root=False, strict=True)
                except Exception:
                    try:
                        self.adb.remove_remote(remote, root=True, strict=True)
                    except Exception as exc:
                        self.log(f"清理远端备份临时文件失败: {exc}")
        return records

    def preflight_block_exists(self, block_name: str) -> None:
        self.safety.preflight_block_exists(self._validate_block_name(block_name))

    def setrw_block(self, block_name: str) -> None:
        block_name = self._validate_block_name(block_name)
        self.safety.preflight_block_exists(block_name)
        command = f"blockdev --setrw {SafetyChecker.BLOCK_DIR}/{block_name}"
        result = self.adb.shell(command, root=True, timeout=30, check=False)
        # blockdev 在部分设备上没有输出且 rc=0，仅在 rc!=0 才判失败
        if result.returncode != 0:
            raise PartitionError(f"setrw {block_name} 失败: {result.output}")

    def block_size(self, block_name: str) -> int:
        block_name = self._validate_block_name(block_name)
        command = f"blockdev --getsize64 {SafetyChecker.BLOCK_DIR}/{block_name}"
        result = self.adb.shell(command, root=True, timeout=30, check=False)
        stdout = (getattr(result, "stdout", "") or "").strip()
        if result.returncode != 0:
            raise PartitionError(f"读取 {block_name} 容量失败: {stdout}")
        if re.fullmatch(r"[0-9]+", stdout) is None:
            raise PartitionError(f"读取 {block_name} 容量失败: {stdout}")
        size = int(stdout)
        if size <= 0:
            raise PartitionError(f"读取 {block_name} 容量非正数: {stdout}")
        return size

    def write_lk_to_slot(self, remote_lk: str, slot_suffix: str) -> None:
        block_name = self.block_name("lk", slot_suffix)
        self.safety.preflight_remote_files(remote_lk)
        self.safety.preflight_block_exists(block_name)
        self._dd_write(remote_lk, block_name)

    def write_misc(self, remote_misc: str) -> None:
        self.safety.preflight_remote_files(remote_misc)
        self.safety.preflight_block_exists("misc")
        self._dd_write(remote_misc, "misc")

    def write_inactive_lk_with_setrw(
        self,
        remote_lk: str,
        slot: SlotInfo,
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        block_name = self.block_name("lk", slot.inactive_suffix)
        self.setrw_block(block_name)
        self.safety.preflight_remote_files(remote_lk)
        self.safety.preflight_block_exists(block_name)
        if before_write is not None:
            before_write()
        self._dd_write(remote_lk, block_name)

    def block_name(self, partition: str, suffix: str) -> str:
        partition = self.validate_partition_label(partition)
        aliases = self.profile.partition_aliases
        base = aliases.get(partition, partition)
        return self._validate_block_name(SlotManager.partition_name(base, suffix, use_slot=self.profile.slot_suffix))

    @staticmethod
    def validate_partition_label(partition: str) -> str:
        label = str(partition)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", label):
            raise PartitionError(f"非法 partition label: {partition!r}")
        return label

    def _contained_export_path(self, filename: str) -> Path:
        candidate = self.exports_dir / Path(filename).name
        resolved_dir = self.exports_dir.resolve()
        resolved_candidate = candidate.resolve(strict=False)
        if resolved_candidate.parent != resolved_dir:
            raise PartitionError(f"备份输出路径逃逸: {candidate}")
        return candidate

    def _validate_block_name(self, block_name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", block_name):
            raise PartitionError(f"非法分区名: {block_name!r}")
        return block_name

    def _dd_read(self, block_name: str, remote_out: str) -> None:
        block_name = self._validate_block_name(block_name)
        remote_quoted = AdbClient._quote(remote_out)
        command = (
            f"dd if={AdbClient._quote(f'{SafetyChecker.BLOCK_DIR}/{block_name}')} "
            f"of={remote_quoted} && "
            f"chown 2000:2000 {remote_quoted} && "
            f"chmod 600 {remote_quoted}"
        )
        result = self.adb.shell(command, root=True, timeout=600, check=False)
        if result.returncode != 0:
            raise PartitionError(f"读取 {block_name} 失败: {result.output}")
        lower = result.output.lower()
        if "no such file" in lower or "permission denied" in lower:
            raise PartitionError(f"读取 {block_name} 失败: {result.output}")

    def _dd_write(self, remote_in: str, block_name: str) -> None:
        command = (
            f"dd if={remote_in} of={SafetyChecker.BLOCK_DIR}/{block_name} bs=1M conv=fsync"
        )
        result = self.adb.shell(command, root=True, timeout=600, check=False)
        output = result.output.lower()
        if result.returncode != 0 or "error" in output or "i/o error" in output or "no space" in output:
            raise PartitionError(f"写入 {block_name} 失败: {result.output}")
        self.log(f"已写入 {block_name}")
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
