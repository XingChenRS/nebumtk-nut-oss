# SPDX-License-Identifier: AGPL-3.0-only
"""Partition backup, setrw, and dd operations (adb+root phase only)."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .adb import AdbClient
from .errors import PartitionError, VerificationError
from .numeric import parse_bounded_uint64_decimal
from .operation_files import OperationFiles
from .safety import SafetyChecker
from .slot import SlotInfo, SlotManager
from .verifier import StepVerifier


def parse_positive_block_size(raw: str, *, error_message: str) -> int:
    """Parse one bounded decimal block size without leaking ``int`` errors."""

    size = parse_bounded_uint64_decimal(raw, error_message=error_message)
    if size == 0:
        raise VerificationError(error_message)
    return size


@dataclass
class BackupRecord:
    partition: str
    block_name: str
    remote_path: str
    local_path: Path
    block_target: str = ""
    size: int = 0
    sha256: str = ""
    captured_at: str = ""
    provenance: str = "device-read-unclassified"


@dataclass(frozen=True)
class PartitionInventoryEntry:
    """One resolved entry from the rooted Android by-name directory."""

    name: str
    target: str
    size: int
    base_name: str
    slot_suffix: str | None
    partner: str | None


@dataclass(frozen=True)
class PartitionInventory:
    entries: tuple[PartitionInventoryEntry, ...]
    # `by-name` entries whose node did not resolve, so the operator can tell
    # "my partition is missing" from "the device reported nothing".
    skipped_entries: int = 0

    MAX_OUTPUT_BYTES = 256 * 1024
    MAX_ENTRIES = 4096
    # `adb shell` gives the remote command exactly one output stream, so the
    # remote stderr lands in our stdout whatever the PTY setting.  Every applet
    # here is redirected for that reason, and any row that still does not parse
    # is a bug worth failing on rather than skipping.
    #
    # A `by-name` entry whose node does not resolve is common (a slot that was
    # never populated) and cannot be read or written anyway, so it is skipped
    # and counted rather than failing the whole listing.
    SKIPPED_MARKER = "#skipped"
    INVENTORY_COMMAND = (
        "skipped=0; "
        "for p in /dev/block/by-name/*; do "
        "[ -L \"$p\" ] || continue; "
        "n=${p##*/}; "
        "t=$(readlink -f \"$p\" 2>/dev/null) || { skipped=$((skipped+1)); continue; }; "
        "[ -e \"$t\" ] || { skipped=$((skipped+1)); continue; }; "
        "s=$(blockdev --getsize64 \"$t\" 2>/dev/null) || { skipped=$((skipped+1)); continue; }; "
        "printf '%s\\t%s\\t%s\\n' \"$n\" \"$t\" \"$s\"; "
        "done; "
        "printf '#skipped\\t%s\\n' \"$skipped\""
    )

    @classmethod
    def from_adb(cls, adb: AdbClient) -> "PartitionInventory":
        result = adb.shell(
            cls.INVENTORY_COMMAND,
            root=True,
            timeout=60,
            check=False,
            sensitive=True,
            # No PTY: with one, the device shell merges stderr into the same
            # stream and wraps lines at the terminal width.  Either turns a good
            # row into a malformed one, and this command is pure tab-separated
            # data with no reason to be human-readable.
            no_pty=True,
        )
        if result.returncode != 0:
            raise VerificationError("partition inventory command failed")
        output = result.stdout or ""
        if len(output.encode("utf-8")) > cls.MAX_OUTPUT_BYTES:
            raise VerificationError("partition inventory output is too large")
        lines = output.splitlines()
        if not lines:
            raise VerificationError("partition inventory is empty")
        if len(lines) > cls.MAX_ENTRIES:
            raise VerificationError("partition inventory has too many entries")

        parsed: list[tuple[str, str, int]] = []
        seen: set[str] = set()
        skipped = 0
        for line in lines:
            if line.startswith(cls.SKIPPED_MARKER):
                _, _, raw_skipped = line.partition("\t")
                if re.fullmatch(r"[0-9]{1,10}", raw_skipped.strip()):
                    skipped = int(raw_skipped.strip())
                continue
            fields = line.split("\t")
            if len(fields) != 3:
                # Name the row: without it this failure is undiagnosable from a
                # screenshot, which is exactly how it was first reported.
                raise VerificationError(
                    "partition inventory row is malformed: "
                    f"{len(fields)} tab-separated fields in {line[:120]!r}"
                )
            name, target, raw_size = fields
            if re.fullmatch(r"[A-Za-z0-9._-]+", name) is None:
                raise VerificationError("partition inventory name is invalid")
            folded = name.casefold()
            if folded in seen:
                raise VerificationError("partition inventory contains a duplicate name")
            seen.add(folded)
            if not cls._valid_block_target(target):
                raise VerificationError("partition inventory symlink target is invalid")
            size = parse_positive_block_size(
                raw_size,
                error_message="partition inventory size is invalid",
            )
            parsed.append((name, target, size))

        names = {name for name, _target, _size in parsed}
        entries: list[PartitionInventoryEntry] = []
        for name, target, size in parsed:
            suffix = name[-2:] if name.endswith(("_a", "_b")) else None
            base = name[:-2] if suffix is not None else name
            other = (
                f"{base}_{'b' if suffix == '_a' else 'a'}"
                if suffix is not None
                else None
            )
            partner = other if other in names else None
            entries.append(
                PartitionInventoryEntry(
                    name=name,
                    target=target,
                    size=size,
                    base_name=base,
                    slot_suffix=suffix,
                    partner=partner,
                )
            )
        return cls(tuple(entries), skipped_entries=skipped)

    @staticmethod
    def _valid_block_target(target: str) -> bool:
        if not target.startswith("/dev/block/"):
            return False
        components = target.split("/")[3:]
        return bool(components) and all(
            component not in {"", ".", ".."}
            and re.fullmatch(r"[A-Za-z0-9._:+-]+", component) is not None
            for component in components
        )

    def by_name(self, name: str) -> PartitionInventoryEntry:
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise VerificationError(f"partition inventory has no entry named {name!r}")

    def resolve_pair(self, base_name: str, slot_suffix: str) -> PartitionInventoryEntry:
        if slot_suffix not in {"_a", "_b"}:
            raise VerificationError("partition slot suffix must be _a or _b")
        base = str(base_name)
        if re.fullmatch(r"[A-Za-z0-9._-]+", base) is None:
            raise VerificationError("partition pair base name is invalid")
        name = f"{base}{slot_suffix}"
        try:
            entry = self.by_name(name)
        except VerificationError:
            if any(item.base_name == base for item in self.entries):
                raise VerificationError(
                    f"partition {base!r} has no confirmed A/B pair"
                ) from None
            raise
        if entry.partner is None:
            raise VerificationError(f"partition {base!r} has no confirmed A/B pair")
        return entry


class PartitionOps:
    REMOTE_TMP = "/data/local/tmp"
    REMOTE_SDCARD = "/sdcard"

    def __init__(
        self,
        adb: AdbClient,
        profile: object,
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

    def estimate_selected_bytes(
        self,
        inventory: PartitionInventory,
        selected: list[str] | tuple[str, ...],
    ) -> int:
        entries = self._selected_entries(inventory, selected)
        return sum(entry.size for entry in entries)

    def backup_selected(
        self,
        inventory: PartitionInventory,
        selected: list[str] | tuple[str, ...],
        *,
        operation_files: OperationFiles | None = None,
    ) -> dict[str, BackupRecord]:
        entries = self._selected_entries(inventory, selected)
        required = sum(entry.size for entry in entries) * 2
        free = shutil.disk_usage(self.exports_dir).free
        if free < required:
            raise VerificationError(
                f"insufficient free local space: need {required} bytes, have {free}"
            )

        records: dict[str, BackupRecord] = {}
        for entry in entries:
            if self.cancelled():
                from .errors import FlowCancelledError

                raise FlowCancelledError("用户已取消")
            remote = f"{self.REMOTE_TMP}/{entry.name}.bin"
            local = self._contained_export_path(f"raw_{entry.name}.bin")
            self.adb.remove_remote(remote, strict=True)
            try:
                self._dd_read(entry.name, remote)
                remote_size = self.adb.remote_file_size(remote, root=False)
                if remote_size != entry.size:
                    raise VerificationError(
                        f"partition {entry.name} size changed during backup"
                    )
                self.adb.pull(remote, local, cancelled=self.cancelled)
                if local.stat().st_size != entry.size:
                    raise VerificationError(
                        f"partition {entry.name} local backup size mismatch"
                    )
                if operation_files is not None:
                    snapshot = operation_files.copy_input(
                        local,
                        f"verified_{entry.name}.bin",
                    )
                    local.unlink(missing_ok=True)
                    snapshot_path = snapshot.path
                    snapshot_size = snapshot.size
                    snapshot_sha256 = snapshot.sha256
                else:
                    snapshot_path = local
                    snapshot_size = local.stat().st_size
                    snapshot_sha256 = self._sha256_file(local)
                records[entry.name] = BackupRecord(
                    partition=entry.name,
                    block_name=entry.name,
                    remote_path=remote,
                    local_path=snapshot_path,
                    block_target=entry.target,
                    size=snapshot_size,
                    sha256=snapshot_sha256,
                    captured_at=self._utc_now(),
                    provenance="device-read-unclassified",
                )
            except Exception:
                try:
                    self._cleanup_remote_backup(remote)
                except Exception as cleanup_exc:
                    self.log(f"remote backup cleanup also failed: {cleanup_exc}")
                raise
            else:
                self._cleanup_remote_backup(remote)
        return records

    @staticmethod
    def _selected_entries(
        inventory: PartitionInventory,
        selected: list[str] | tuple[str, ...],
    ) -> list[PartitionInventoryEntry]:
        if not selected:
            raise VerificationError("no partitions selected for backup")
        seen: set[str] = set()
        entries: list[PartitionInventoryEntry] = []
        for raw_name in selected:
            name = str(raw_name)
            folded = name.casefold()
            if folded in seen:
                raise VerificationError("duplicate selected partition")
            seen.add(folded)
            entries.append(inventory.by_name(name))
        return entries

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

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
            except Exception:
                try:
                    self._cleanup_remote_backup(remote)
                except Exception as cleanup_exc:
                    self.log(f"remote backup cleanup also failed: {cleanup_exc}")
                raise
            else:
                self._cleanup_remote_backup(remote)
        return records

    def _cleanup_remote_backup(self, remote: str) -> None:
        try:
            self.adb.remove_remote(remote, root=False, strict=True)
            return
        except Exception as nonroot_error:
            self.log(f"non-root remote backup cleanup failed: {nonroot_error}")
        try:
            self.adb.remove_remote(remote, root=True, strict=True)
        except Exception as root_error:
            raise VerificationError(
                "remote backup cleanup failed as non-root and root"
            ) from root_error

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
        try:
            size = parse_bounded_uint64_decimal(
                stdout,
                error_message="partition block size is invalid",
            )
        except VerificationError as exc:
            raise PartitionError("partition block size is invalid") from exc
        if size <= 0:
            raise PartitionError(f"读取 {block_name} 容量非正数: {stdout}")
        return size

    def write_lk_to_slot(self, remote_lk: str, slot_suffix: str) -> None:
        block_name = self.block_name("lk", slot_suffix)
        self.safety.preflight_remote_files(remote_lk)
        self.safety.preflight_block_exists(block_name)
        self._dd_write(remote_lk, block_name)

    def write_misc(
        self,
        remote_misc: str,
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        self.safety.preflight_remote_files(remote_misc)
        self.safety.preflight_block_exists("misc")
        if before_write is not None:
            before_write()
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

    def write_partition_with_setrw(
        self,
        remote_image: str,
        block_name: str,
        *,
        before_write: Callable[[], None],
    ) -> None:
        block = self._validate_block_name(block_name)
        self.setrw_block(block)
        before_write()
        self._dd_write(remote_image, block)

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
        result = self.adb.shell(
            command,
            root=True,
            timeout=600,
            check=False,
            cancellable=False,
        )
        output = result.output.lower()
        if result.returncode != 0 or "error" in output or "i/o error" in output or "no space" in output:
            raise PartitionError(f"写入 {block_name} 失败: {result.output}")
        self.log(f"已写入 {block_name}")
