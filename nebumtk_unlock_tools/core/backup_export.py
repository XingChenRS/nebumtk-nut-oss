# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Publish verified workspace backup records to the Windows Desktop."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QStandardPaths

from .dyn_backup import (
    MAX_DYN_FILES,
    MAX_DYN_FILE_BYTES,
    MAX_DYN_TOTAL_BYTES,
    DynBackup,
    DynBackupFile,
)
from .errors import VerificationError
from .operation_files import VerifiedFileSnapshot, snapshot_regular_file
from .partition import BackupRecord, PartitionInventory


BACKUP_MANIFEST_FILENAME = "backup-manifest.json"
BACKUP_MANIFEST_SCHEMA = "nebumtk.backup"
BACKUP_MANIFEST_VERSION = 3
_MANIFEST_KEYS_V1 = {
    "schema",
    "schema_version",
    "profile_key",
    "adb_serial",
    "initial_slot",
    "partitions",
}
_MANIFEST_KEYS_V3 = _MANIFEST_KEYS_V1 | {
    "product",
    "created_at",
    "tool_build",
}
_DYN_ENTRY_KEYS = {"name", "file", "size", "sha256", "mode", "uid", "gid"}
_PARTITION_ENTRY_KEYS_V1 = {"file", "size", "sha256"}
_PARTITION_ENTRY_KEYS_V3 = _PARTITION_ENTRY_KEYS_V1 | {
    "block_name",
    "block_target",
    "captured_at",
    "provenance",
}
_PARTITION_LABEL = re.compile(r"[A-Za-z0-9._-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DYN_MODE = re.compile(r"[0-7]{3,4}")
_DYN_ID = re.compile(r"[0-9]{1,10}")
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _path_identity(metadata) -> tuple[int, int, int, int]:
    return (
        int(getattr(metadata, "st_dev", 0)),
        int(getattr(metadata, "st_ino", 0)),
        int(getattr(metadata, "st_size", 0)),
        int(getattr(metadata, "st_mtime_ns", 0)),
    )


def _checked_path_metadata(
    path: Path,
    *,
    directory: bool,
    label: str,
):
    try:
        metadata = Path(path).lstat()
    except OSError as exc:
        raise VerificationError(f"{label} is missing or unreadable") from exc
    expected_kind = (
        stat.S_ISDIR(metadata.st_mode)
        if directory
        else stat.S_ISREG(metadata.st_mode)
    )
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not expected_kind
        or getattr(metadata, "st_file_attributes", 0)
        & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise VerificationError(f"{label} path is unsafe or a reparse point")
    return metadata


def _verify_path_identity(
    path: Path,
    expected,
    *,
    directory: bool,
    label: str,
) -> None:
    current = _checked_path_metadata(path, directory=directory, label=label)
    if _path_identity(current) != _path_identity(expected):
        raise VerificationError(f"{label} identity changed during verification")


@dataclass(frozen=True)
class DesktopBackupExport:
    directory: Path
    files: tuple[Path, ...]


@dataclass(frozen=True)
class BackupLkValidation:
    size: int
    sha256: str
    legacy_directory: bool


@dataclass(frozen=True)
class BackupManifestFile:
    partition: str
    block_name: str
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class VerifiedBackupManifest:
    directory: Path
    profile_key: str
    serial: str
    product: str
    initial_slot: str
    tool_build: str
    partitions: dict[str, BackupManifestFile]


def desktop_export_directory(*, model_key: str, serial: str) -> Path:
    """Create a desktop directory shaped exactly like a verified backup export.

    A partition read from the checklist lands in the same kind of folder as a
    backup, so the operator finds both in one place and with one naming scheme.
    """

    profile_key = str(model_key).strip()
    adb_serial = str(serial).strip()
    if not profile_key or not adb_serial:
        raise ValueError("导出 profile 与 ADB serial 不能为空")
    desktop_text = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DesktopLocation
    )
    desktop = Path(desktop_text)
    if not desktop_text or not desktop.is_dir():
        raise OSError("无法定位可写入的 Windows 桌面目录")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    prefix = f"NEBUMTK_Backup_{_safe_component(profile_key)}_{_safe_component(adb_serial)}_{stamp}"
    final = desktop / prefix
    suffix = 1
    while final.exists():
        suffix += 1
        final = desktop / f"{prefix}_{suffix}"
    final.mkdir(parents=True)
    return final


def publish_to_desktop(
    records: dict[str, BackupRecord],
    *,
    model_key: str,
    serial: str,
    product: str = "unknown",
    initial_slot: str,
    tool_build: str | None = None,
    dyn_backup: DynBackup | None = None,
) -> DesktopBackupExport:
    profile_key = str(model_key).strip()
    adb_serial = str(serial).strip()
    if not profile_key or not adb_serial:
        raise ValueError("备份 profile 与 ADB serial 不能为空")
    if initial_slot not in {"_a", "_b"}:
        raise ValueError("备份初始槽位必须为 _a 或 _b")
    product_name = str(product).strip() or "unknown"
    if tool_build is None:
        from .. import __version__

        tool_build = __version__
    build = str(tool_build).strip()
    if not build:
        raise ValueError("备份 tool build 不能为空")
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    desktop_text = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    desktop = Path(desktop_text)
    if not desktop_text or not desktop.is_dir():
        raise OSError("无法定位可写入的 Windows 桌面目录")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    prefix = f"NEBUMTK_Backup_{_safe_component(model_key)}_{_safe_component(serial)}_{stamp}"
    final = desktop / prefix
    suffix = 1
    while final.exists():
        suffix += 1
        final = desktop / f"{prefix}_{suffix}"
    pending = desktop / f".{final.name}.pending"
    while pending.exists():
        pending = desktop / f".{final.name}.{suffix}.pending"
        suffix += 1
    pending.mkdir()
    copied: list[Path] = []
    partitions: dict[str, dict[str, str | int]] = {}
    used_filenames: set[str] = {BACKUP_MANIFEST_FILENAME.casefold()}
    try:
        for partition, record in records.items():
            partition = str(partition)
            if _PARTITION_LABEL.fullmatch(partition) is None:
                raise ValueError(f"备份分区名无效: {partition!r}")
            if record.partition != partition:
                raise ValueError("备份分区记录与索引不一致")
            source = record.local_path
            source_metadata = _checked_path_metadata(
                source,
                directory=False,
                label=f"backup source {source.name}",
            )
            filename = source.name
            if filename.casefold() in used_filenames:
                raise OSError("备份导出文件名冲突")
            used_filenames.add(filename.casefold())
            target = pending / source.name
            shutil.copyfile(source, target)
            source_size = source_metadata.st_size
            target_size = target.stat().st_size
            if source_size <= 0 or target_size != source_size:
                raise OSError(f"桌面备份大小不一致: {source.name}")
            source_sha256 = _sha256_file(source)
            _verify_path_identity(
                source,
                source_metadata,
                directory=False,
                label=f"backup source {source.name}",
            )
            target_sha256 = _sha256_file(target)
            if target_sha256 != source_sha256:
                raise OSError(f"桌面备份哈希不一致: {source.name}")
            if record.size not in {0, target_size}:
                raise VerificationError(f"备份记录大小与实际文件不一致: {filename}")
            if record.sha256 and record.sha256 != target_sha256:
                raise VerificationError(f"备份记录哈希与实际文件不一致: {filename}")
            block_name = str(record.block_name).strip()
            if _PARTITION_LABEL.fullmatch(block_name) is None:
                raise ValueError(f"备份 block name 无效: {block_name!r}")
            block_target = str(record.block_target).strip() or f"/dev/block/by-name/{block_name}"
            captured_at = str(record.captured_at).strip() or created_at
            provenance = str(record.provenance).strip() or "device-read-unclassified"
            partitions[partition] = {
                "file": filename,
                "block_name": block_name,
                "block_target": block_target,
                "size": target_size,
                "sha256": target_sha256,
                "captured_at": captured_at,
                "provenance": provenance,
            }
            copied.append(target)
        dyn_entries, dyn_files = _copy_dyn_backup(pending, dyn_backup)
        copied.extend(dyn_files)
        if not partitions and not dyn_entries:
            raise ValueError("backup must contain partitions or Vivo dyn files")
        manifest = {
            "schema": BACKUP_MANIFEST_SCHEMA,
            "schema_version": BACKUP_MANIFEST_VERSION,
            "profile_key": profile_key,
            "adb_serial": adb_serial,
            "product": product_name,
            "initial_slot": initial_slot,
            "created_at": created_at,
            "tool_build": build,
            "partitions": partitions,
        }
        if dyn_entries:
            manifest["dyn"] = dyn_entries
        _write_manifest_atomic(pending / BACKUP_MANIFEST_FILENAME, manifest)
        pending.rename(final)
        return DesktopBackupExport(
            directory=final,
            files=tuple(final / path.relative_to(pending) for path in copied),
        )
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise


def _copy_dyn_backup(
    pending: Path,
    dyn_backup: DynBackup | None,
) -> tuple[list[dict[str, str | int | None]], list[Path]]:
    if dyn_backup is None:
        return [], []
    if not dyn_backup.files:
        raise ValueError("dyn backup is empty")
    _validate_dyn_bounds(dyn_backup.files)
    target_directory = pending / "dyn"
    target_directory.mkdir()
    entries: list[dict[str, str | int | None]] = []
    copied: list[Path] = []
    seen_names: set[str] = set()
    for item in sorted(dyn_backup.files, key=lambda value: value.name):
        name = item.name
        if _PARTITION_LABEL.fullmatch(name) is None or name.casefold() in seen_names:
            raise ValueError(f"dyn backup filename invalid: {name!r}")
        seen_names.add(name.casefold())
        source_size, source_sha256 = _regular_file_snapshot(Path(item.path))
        if source_size != item.size or source_sha256 != item.sha256:
            raise VerificationError(f"dyn backup changed before export: {name}")
        _validate_dyn_metadata(item.mode, item.uid, item.gid)
        target = target_directory / name
        shutil.copyfile(item.path, target)
        target_size, target_sha256 = _regular_file_snapshot(target)
        if target_size != source_size or target_sha256 != source_sha256:
            raise OSError(f"dyn backup copy mismatch: {name}")
        entries.append(
            {
                "name": name,
                "file": f"dyn/{name}",
                "size": target_size,
                "sha256": target_sha256,
                "mode": item.mode,
                "uid": item.uid,
                "gid": item.gid,
            }
        )
        copied.append(target)
    return entries, copied


def validate_restore_lk_backup(
    source_lk: Path,
    *,
    profile_key: str,
    fastboot_serial: str,
    log: Callable[[str], None] | None = None,
) -> BackupLkValidation:
    """Validate an explicitly selected original LK against its backup folder.

    The manifest is provenance/integrity metadata only.  It deliberately does
    not grant permission to a later worker or persist operation state.
    """

    source = Path(source_lk)
    source_metadata = _checked_path_metadata(
        source,
        directory=False,
        label="selected original LK",
    )
    try:
        resolved_source = source.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("无法解析所选原 LK 文件") from exc
    directory = resolved_source.parent
    directory_metadata = _checked_path_metadata(
        source.parent,
        directory=True,
        label="selected backup directory",
    )
    manifest_path = directory / BACKUP_MANIFEST_FILENAME
    if manifest_path.exists():
        manifest = _load_manifest(manifest_path, directory)
        if manifest["profile_key"] != str(profile_key).strip():
            raise VerificationError("备份 manifest 与当前机型不匹配")
        if manifest["adb_serial"] != str(fastboot_serial).strip():
            raise VerificationError("备份 manifest 与当前设备不匹配")
        lk_entry = manifest["partitions"].get("lk")
        if lk_entry is None:
            raise VerificationError("备份 manifest 缺少 LK 条目")
        if resolved_source.name != lk_entry["file"]:
            raise VerificationError("所选原 LK 不是 manifest 记录的 LK 文件")
        size = resolved_source.stat().st_size
        sha256 = _sha256_file(resolved_source)
        _verify_path_identity(
            source,
            source_metadata,
            directory=False,
            label="selected original LK",
        )
        _verify_path_identity(
            source.parent,
            directory_metadata,
            directory=True,
            label="selected backup directory",
        )
        if size != lk_entry["size"] or sha256 != lk_entry["sha256"]:
            raise VerificationError("所选原 LK 与备份 manifest 的大小或哈希不一致")
        return BackupLkValidation(size=size, sha256=sha256, legacy_directory=False)

    if not _matches_legacy_backup_directory(
        directory.name,
        profile_key=str(profile_key).strip(),
        serial=str(fastboot_serial).strip(),
    ):
        raise VerificationError("旧版备份目录无法确认属于当前机型与设备")
    size = resolved_source.stat().st_size
    if size <= 0:
        raise VerificationError("所选原 LK 文件为空")
    sha256 = _sha256_file(resolved_source)
    _verify_path_identity(
        source,
        source_metadata,
        directory=False,
        label="selected original LK",
    )
    _verify_path_identity(
        source.parent,
        directory_metadata,
        directory=True,
        label="selected backup directory",
    )
    (log or (lambda _message: None))(
        "旧版备份未包含 manifest；已按备份目录归属执行兼容校验"
    )
    return BackupLkValidation(size=size, sha256=sha256, legacy_directory=True)


def load_verified_backup_manifest(
    directory: Path,
    *,
    serial: str,
    frozen_manifest_path: Path | None = None,
) -> VerifiedBackupManifest:
    """Load canonical backup files bound to one exact device serial."""

    selected_directory = Path(directory)
    directory_metadata = _checked_path_metadata(
        selected_directory,
        directory=True,
        label="backup directory",
    )
    try:
        resolved_directory = selected_directory.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("backup directory cannot be resolved") from exc
    _verify_path_identity(
        selected_directory,
        directory_metadata,
        directory=True,
        label="backup directory",
    )
    manifest_path = (
        resolved_directory / BACKUP_MANIFEST_FILENAME
        if frozen_manifest_path is None
        else Path(frozen_manifest_path)
    )
    try:
        manifest_parent = manifest_path.resolve(strict=True).parent
    except OSError as exc:
        raise VerificationError("backup manifest cannot be resolved") from exc
    manifest = _load_manifest(manifest_path, manifest_parent)
    if manifest["schema_version"] != BACKUP_MANIFEST_VERSION:
        raise VerificationError("generic restore requires canonical backup manifest version 3")
    if manifest["adb_serial"] != str(serial).strip():
        raise VerificationError("backup manifest serial does not match target")
    files: dict[str, BackupManifestFile] = {}
    for partition, entry in manifest["partitions"].items():
        candidate = resolved_directory / entry["file"]
        try:
            metadata = _checked_path_metadata(
                candidate,
                directory=False,
                label=f"backup file {partition}",
            )
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise VerificationError(f"backup file missing: {partition}") from exc
        if (
            resolved.parent != resolved_directory
        ):
            raise VerificationError(f"backup file path is unsafe: {partition}")
        size = metadata.st_size
        sha256 = _sha256_file(resolved)
        _verify_path_identity(
            candidate,
            metadata,
            directory=False,
            label=f"backup file {partition}",
        )
        if size != entry["size"] or sha256 != entry["sha256"]:
            raise VerificationError(f"backup file size or hash mismatch: {partition}")
        files[partition] = BackupManifestFile(
            partition=partition,
            block_name=entry["block_name"],
            path=resolved,
            size=size,
            sha256=sha256,
        )
    _verify_path_identity(
        selected_directory,
        directory_metadata,
        directory=True,
        label="backup directory",
    )
    return VerifiedBackupManifest(
        directory=resolved_directory,
        profile_key=manifest["profile_key"],
        serial=manifest["adb_serial"],
        product=manifest["product"],
        initial_slot=manifest["initial_slot"],
        tool_build=manifest["tool_build"],
        partitions=files,
    )


def load_dyn_backup(
    directory: Path,
    *,
    profile_key: str,
    adb_serial: str,
    expected_manifest: VerifiedFileSnapshot | None = None,
) -> DynBackup:
    """Load a verified bounded dyn snapshot from one selected backup directory."""

    selected_directory = Path(directory)
    directory_metadata = _checked_path_metadata(
        selected_directory,
        directory=True,
        label="Vivo dyn backup directory",
    )
    try:
        resolved_directory = selected_directory.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("Vivo dyn backup directory cannot be resolved") from exc
    _verify_path_identity(
        selected_directory,
        directory_metadata,
        directory=True,
        label="Vivo dyn backup directory",
    )
    manifest_path = resolved_directory / BACKUP_MANIFEST_FILENAME
    if expected_manifest is not None:
        if expected_manifest.path != manifest_path:
            raise VerificationError("confirmed dyn manifest path mismatch")
        current = snapshot_regular_file(manifest_path)
        if current.size != expected_manifest.size or current.sha256 != expected_manifest.sha256:
            raise VerificationError("dyn manifest changed after confirmation")
    manifest = _load_manifest(
        manifest_path,
        resolved_directory,
    )
    if expected_manifest is not None:
        current = snapshot_regular_file(manifest_path)
        if current.size != expected_manifest.size or current.sha256 != expected_manifest.sha256:
            raise VerificationError("dyn manifest changed after confirmation")
    if manifest["schema_version"] != BACKUP_MANIFEST_VERSION:
        raise VerificationError("Vivo dyn backup requires canonical manifest version 3")
    if manifest["profile_key"] != str(profile_key).strip():
        raise VerificationError("Vivo dyn backup does not match the selected model")
    if manifest["adb_serial"] != str(adb_serial).strip():
        raise VerificationError("Vivo dyn backup does not match the connected device")
    entries = manifest.get("dyn")
    if not entries:
        raise VerificationError("Vivo dyn backup contains no files")
    files: list[DynBackupFile] = []
    for entry in entries:
        name = entry["name"]
        path = _resolve_dyn_backup_path(resolved_directory, entry["file"], name)
        size, sha256 = _regular_file_snapshot(path)
        if size != entry["size"] or sha256 != entry["sha256"]:
            raise VerificationError(f"Vivo dyn backup hash mismatch: {name}")
        files.append(
            DynBackupFile(
                name,
                path,
                size,
                sha256,
                entry["mode"],
                entry["uid"],
                entry["gid"],
            )
        )
    _verify_path_identity(
        selected_directory,
        directory_metadata,
        directory=True,
        label="Vivo dyn backup directory",
    )
    return DynBackup(tuple(files))


def _load_manifest(path: Path, expected_directory: Path) -> dict:
    metadata = _checked_path_metadata(
        path,
        directory=False,
        label="backup manifest",
    )
    try:
        resolved = path.resolve(strict=True)
        if resolved.parent != expected_directory:
            raise VerificationError("备份 manifest 路径无效")
        if metadata.st_size > 256 * 1024:
            raise VerificationError("备份 manifest 过大")
        raw = json.loads(resolved.read_text(encoding="utf-8"))
        _verify_path_identity(
            path,
            metadata,
            directory=False,
            label="backup manifest",
        )
    except VerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("备份 manifest 无法读取") from exc
    if not isinstance(raw, dict):
        raise VerificationError("备份 manifest schema 无效")
    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise VerificationError("备份 manifest 版本无效")
    if version == 2:
        raise VerificationError("ambiguous legacy manifest version 2 is not accepted")
    if version == 1:
        valid_key_sets = (_MANIFEST_KEYS_V1,)
    elif version == BACKUP_MANIFEST_VERSION:
        valid_key_sets = (_MANIFEST_KEYS_V3, _MANIFEST_KEYS_V3 | {"dyn"})
    else:
        valid_key_sets = ()
    if set(raw) not in valid_key_sets:
        raise VerificationError("备份 manifest schema 无效")
    if raw.get("schema") != BACKUP_MANIFEST_SCHEMA:
        raise VerificationError("备份 manifest 版本不受支持")
    if not isinstance(raw.get("profile_key"), str) or not raw["profile_key"].strip():
        raise VerificationError("备份 manifest profile 无效")
    if not isinstance(raw.get("adb_serial"), str) or not raw["adb_serial"].strip():
        raise VerificationError("备份 manifest serial 无效")
    if raw.get("initial_slot") not in {"_a", "_b"}:
        raise VerificationError("备份 manifest 初始槽位无效")
    if version == BACKUP_MANIFEST_VERSION:
        for field in ("product", "created_at", "tool_build"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise VerificationError(f"备份 manifest {field} 无效")
    partitions = raw.get("partitions")
    if not isinstance(partitions, dict):
        raise VerificationError("备份 manifest 分区条目无效")
    for partition, entry in partitions.items():
        if not isinstance(partition, str) or _PARTITION_LABEL.fullmatch(partition) is None:
            raise VerificationError("备份 manifest 分区名无效")
        expected_entry_keys = (
            _PARTITION_ENTRY_KEYS_V3
            if version == BACKUP_MANIFEST_VERSION
            else _PARTITION_ENTRY_KEYS_V1
        )
        if not isinstance(entry, dict) or set(entry) != expected_entry_keys:
            raise VerificationError("备份 manifest 分区 schema 无效")
        filename = entry.get("file")
        if (
            not isinstance(filename, str)
            or not filename
            or filename != Path(filename).name
            or filename.casefold() == BACKUP_MANIFEST_FILENAME.casefold()
        ):
            raise VerificationError("备份 manifest 文件名无效")
        if not isinstance(entry.get("size"), int) or isinstance(entry["size"], bool) or entry["size"] <= 0:
            raise VerificationError("备份 manifest 文件大小无效")
        if not isinstance(entry.get("sha256"), str) or _SHA256.fullmatch(entry["sha256"]) is None:
            raise VerificationError("备份 manifest 文件哈希无效")
        if version == BACKUP_MANIFEST_VERSION:
            if (
                not isinstance(entry.get("block_name"), str)
                or _PARTITION_LABEL.fullmatch(entry["block_name"]) is None
            ):
                raise VerificationError("备份 manifest block name 无效")
            for field in ("block_target", "captured_at", "provenance"):
                if not isinstance(entry.get(field), str) or not entry[field].strip():
                    raise VerificationError(f"备份 manifest {field} 无效")
            if not PartitionInventory._valid_block_target(entry["block_target"]):
                raise VerificationError("备份 manifest block target 无效")
    if version == BACKUP_MANIFEST_VERSION and "dyn" in raw:
        _validate_dyn_manifest_entries(raw["dyn"])
    if not partitions and not raw.get("dyn"):
        raise VerificationError("backup manifest contains no backup files")
    return raw


def _validate_dyn_manifest_entries(entries: object) -> None:
    if not isinstance(entries, list) or not entries or len(entries) > MAX_DYN_FILES:
        raise VerificationError("Vivo dyn manifest file count invalid")
    seen_names: set[str] = set()
    total_size = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _DYN_ENTRY_KEYS:
            raise VerificationError("Vivo dyn manifest entry schema invalid")
        name = entry.get("name")
        if (
            not isinstance(name, str)
            or _PARTITION_LABEL.fullmatch(name) is None
            or name.casefold() in seen_names
        ):
            raise VerificationError("Vivo dyn manifest filename invalid")
        seen_names.add(name.casefold())
        if entry.get("file") != f"dyn/{name}":
            raise VerificationError("Vivo dyn manifest path invalid")
        size = entry.get("size")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
            or size > MAX_DYN_FILE_BYTES
        ):
            raise VerificationError("Vivo dyn manifest size invalid")
        total_size += size
        if total_size > MAX_DYN_TOTAL_BYTES:
            raise VerificationError("Vivo dyn manifest total size invalid")
        if not isinstance(entry.get("sha256"), str) or _SHA256.fullmatch(entry["sha256"]) is None:
            raise VerificationError("Vivo dyn manifest hash invalid")
        _validate_dyn_metadata(entry.get("mode"), entry.get("uid"), entry.get("gid"))


def _validate_dyn_metadata(mode: object, uid: object, gid: object) -> None:
    if mode is not None and (not isinstance(mode, str) or _DYN_MODE.fullmatch(mode) is None):
        raise VerificationError("Vivo dyn manifest mode invalid")
    for value in (uid, gid):
        if value is not None and (not isinstance(value, str) or _DYN_ID.fullmatch(value) is None):
            raise VerificationError("Vivo dyn manifest owner invalid")


def _resolve_dyn_backup_path(directory: Path, relative: str, name: str) -> Path:
    target_directory = directory / "dyn"
    candidate = directory / relative
    try:
        directory_metadata = target_directory.lstat()
        candidate_metadata = candidate.lstat()
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or getattr(directory_metadata, "st_file_attributes", 0)
            & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
            or stat.S_ISLNK(candidate_metadata.st_mode)
            or not stat.S_ISREG(candidate_metadata.st_mode)
            or getattr(candidate_metadata, "st_file_attributes", 0)
            & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise VerificationError("Vivo dyn backup path is unsafe")
        resolved_directory = target_directory.resolve(strict=True)
        path = candidate.resolve(strict=True)
    except VerificationError:
        raise
    except OSError as exc:
        raise VerificationError("Vivo dyn backup file cannot be resolved") from exc
    if path.parent != resolved_directory or path.name != name:
        raise VerificationError("Vivo dyn backup path escapes its directory")
    _verify_path_identity(
        target_directory,
        directory_metadata,
        directory=True,
        label="Vivo dyn backup directory",
    )
    _verify_path_identity(
        candidate,
        candidate_metadata,
        directory=False,
        label=f"Vivo dyn backup file {name}",
    )
    return candidate


def _validate_dyn_bounds(files: tuple[DynBackupFile, ...]) -> None:
    if len(files) > MAX_DYN_FILES:
        raise VerificationError("Vivo dyn backup exceeds file limit")
    total = 0
    for item in files:
        if item.size <= 0 or item.size > MAX_DYN_FILE_BYTES:
            raise VerificationError(f"Vivo dyn backup size invalid: {item.name}")
        total += item.size
    if total > MAX_DYN_TOTAL_BYTES:
        raise VerificationError("Vivo dyn backup exceeds total size limit")


def _regular_file_snapshot(path: Path) -> tuple[int, str]:
    metadata = _checked_path_metadata(
        path,
        directory=False,
        label=f"backup file {path.name}",
    )
    if metadata.st_size <= 0:
        raise VerificationError(f"backup file is not a non-empty regular file: {path.name}")
    sha256 = _sha256_file(path)
    _verify_path_identity(
        path,
        metadata,
        directory=False,
        label=f"backup file {path.name}",
    )
    return metadata.st_size, sha256


def _matches_legacy_backup_directory(name: str, *, profile_key: str, serial: str) -> bool:
    prefix = f"NEBUMTK_Backup_{_safe_component(profile_key)}_{_safe_component(serial)}_"
    suffix = name[len(prefix) :] if name.startswith(prefix) else ""
    return re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:_[0-9]+)?", suffix) is not None


def _write_manifest_atomic(path: Path, value: dict) -> None:
    pending = path.parent / f".{path.name}.{uuid.uuid4().hex}.pending"
    try:
        with pending.open("x", encoding="utf-8", newline="\n") as target:
            json.dump(value, target, ensure_ascii=False, sort_keys=True, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def _safe_component(value: str) -> str:
    normalized = "".join(char if char.isascii() and (char.isalnum() or char in "-_") else "_" for char in value.strip())
    return (normalized.strip("._") or "unknown")[:64]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
