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
from typing import Callable, Mapping

from PySide6.QtCore import QStandardPaths

from .dyn_backup import DynBackup, DynBackupFile
from .errors import VerificationError
from .partition import BackupRecord


BACKUP_MANIFEST_FILENAME = "backup-manifest.json"
BACKUP_MANIFEST_SCHEMA = "nebumtk.backup"
BACKUP_MANIFEST_VERSION = 2
_MANIFEST_KEYS_V1 = {
    "schema",
    "schema_version",
    "profile_key",
    "adb_serial",
    "initial_slot",
    "partitions",
}
_MANIFEST_KEYS_V2 = _MANIFEST_KEYS_V1 | {"dyn"}
_PARTITION_ENTRY_KEYS = {"file", "size", "sha256"}
_DYN_ENTRY_KEYS = {"name", "file", "size", "sha256", "mode", "uid", "gid"}
_PARTITION_LABEL = re.compile(r"[A-Za-z0-9._-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DYN_MODE = re.compile(r"[0-7]{3,4}")
_DYN_ID = re.compile(r"[0-9]{1,10}")


@dataclass(frozen=True)
class DesktopBackupExport:
    directory: Path
    files: tuple[Path, ...]


@dataclass(frozen=True)
class BackupLkValidation:
    size: int
    sha256: str
    legacy_directory: bool


def publish_to_desktop(
    records: dict[str, BackupRecord],
    *,
    model_key: str,
    serial: str,
    initial_slot: str,
    extra_directories: Mapping[str, Mapping[str, Path]] | None = None,
    dyn_backup: DynBackup | None = None,
) -> DesktopBackupExport:
    profile_key = str(model_key).strip()
    adb_serial = str(serial).strip()
    if not profile_key or not adb_serial:
        raise ValueError("备份 profile 与 ADB serial 不能为空")
    if initial_slot not in {"_a", "_b"}:
        raise ValueError("备份初始槽位必须为 _a 或 _b")
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
            if not source.is_file():
                raise OSError(f"备份源文件不存在: {source.name}")
            filename = source.name
            if filename.casefold() in used_filenames:
                raise OSError("备份导出文件名冲突")
            used_filenames.add(filename.casefold())
            target = pending / source.name
            shutil.copyfile(source, target)
            source_size = source.stat().st_size
            target_size = target.stat().st_size
            if source_size <= 0 or target_size != source_size:
                raise OSError(f"桌面备份大小不一致: {source.name}")
            source_sha256 = _sha256_file(source)
            target_sha256 = _sha256_file(target)
            if target_sha256 != source_sha256:
                raise OSError(f"桌面备份哈希不一致: {source.name}")
            partitions[partition] = {
                "file": filename,
                "size": target_size,
                "sha256": target_sha256,
            }
            copied.append(target)
        if dyn_backup is not None and extra_directories and "dyn" in extra_directories:
            raise ValueError("dyn backup must use the dedicated dyn manifest path")
        copied.extend(_copy_extra_directories(pending, extra_directories))
        dyn_entries, dyn_files = _copy_dyn_backup(pending, dyn_backup)
        copied.extend(dyn_files)
        manifest = {
            "schema": BACKUP_MANIFEST_SCHEMA,
            "schema_version": BACKUP_MANIFEST_VERSION,
            "profile_key": profile_key,
            "adb_serial": adb_serial,
            "initial_slot": initial_slot,
            "partitions": partitions,
            "dyn": dyn_entries,
        }
        _write_manifest_atomic(pending / BACKUP_MANIFEST_FILENAME, manifest)
        pending.rename(final)
        return DesktopBackupExport(
            directory=final,
            files=tuple(final / path.name for path in copied),
        )
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise


def _copy_extra_directories(
    pending: Path,
    extra_directories: Mapping[str, Mapping[str, Path]] | None,
) -> list[Path]:
    if not extra_directories:
        return []
    copied: list[Path] = []
    used_directory_names: set[str] = {BACKUP_MANIFEST_FILENAME.casefold()}
    for directory_name, entries in sorted(extra_directories.items()):
        if not isinstance(directory_name, str) or _PARTITION_LABEL.fullmatch(directory_name) is None:
            raise ValueError(f"backup extra directory name invalid: {directory_name!r}")
        if directory_name.casefold() in used_directory_names:
            raise ValueError("backup extra directory name conflicts")
        used_directory_names.add(directory_name.casefold())
        if not isinstance(entries, Mapping) or not entries:
            raise ValueError(f"backup extra directory is empty: {directory_name}")
        target_directory = pending / directory_name
        target_directory.mkdir()
        used_file_names: set[str] = set()
        for filename, source in sorted(entries.items()):
            if (
                not isinstance(filename, str)
                or not filename
                or filename != Path(filename).name
                or _PARTITION_LABEL.fullmatch(filename) is None
                or filename.casefold() in used_file_names
            ):
                raise ValueError(f"backup extra filename invalid: {filename!r}")
            used_file_names.add(filename.casefold())
            source_path = Path(source)
            if not source_path.is_file():
                raise OSError(f"backup extra source missing: {filename}")
            target = target_directory / filename
            shutil.copyfile(source_path, target)
            source_size = source_path.stat().st_size
            target_size = target.stat().st_size
            if source_size <= 0 or target_size != source_size:
                raise OSError(f"backup extra size mismatch: {filename}")
            if _sha256_file(source_path) != _sha256_file(target):
                raise OSError(f"backup extra hash mismatch: {filename}")
            copied.append(target)
    return copied


def _copy_dyn_backup(
    pending: Path,
    dyn_backup: DynBackup | None,
) -> tuple[list[dict[str, str | int | None]], list[Path]]:
    if dyn_backup is None:
        return [], []
    if not dyn_backup.files:
        raise ValueError("dyn backup is empty")
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
        source = Path(item.path)
        source_size, source_sha256 = _regular_file_snapshot(source)
        if source_size != item.size or source_sha256 != item.sha256:
            raise VerificationError(f"dyn backup changed before export: {name}")
        _validate_dyn_metadata(item.mode, item.uid, item.gid)
        target = target_directory / name
        shutil.copyfile(source, target)
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
    if not source.is_file():
        raise VerificationError("所选原 LK 文件不存在")
    try:
        resolved_source = source.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("无法解析所选原 LK 文件") from exc
    directory = resolved_source.parent
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
    (log or (lambda _message: None))(
        "旧版备份未包含 manifest；已按备份目录归属执行兼容校验"
    )
    return BackupLkValidation(size=size, sha256=sha256, legacy_directory=True)


def load_dyn_backup(
    directory: Path,
    *,
    profile_key: str,
    adb_serial: str,
) -> DynBackup:
    """Load a verified Vivo dyn snapshot from one Desktop backup directory."""

    backup_directory = Path(directory)
    try:
        resolved_directory = backup_directory.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("Vivo dyn backup directory cannot be resolved") from exc
    if not resolved_directory.is_dir():
        raise VerificationError("Vivo dyn backup path is not a directory")
    manifest = _load_manifest(
        resolved_directory / BACKUP_MANIFEST_FILENAME,
        resolved_directory,
    )
    if manifest["schema_version"] != BACKUP_MANIFEST_VERSION:
        raise VerificationError("Vivo dyn backup requires manifest version 2")
    if manifest["profile_key"] != str(profile_key).strip():
        raise VerificationError("Vivo dyn backup does not match the selected model")
    if manifest["adb_serial"] != str(adb_serial).strip():
        raise VerificationError("Vivo dyn backup does not match the connected device")
    entries = manifest["dyn"]
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
    return DynBackup(tuple(files))


def _load_manifest(path: Path, expected_directory: Path) -> dict:
    try:
        resolved = path.resolve(strict=True)
        if resolved.parent != expected_directory:
            raise VerificationError("备份 manifest 路径无效")
        if resolved.stat().st_size > 256 * 1024:
            raise VerificationError("备份 manifest 过大")
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except VerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("备份 manifest 无法读取") from exc
    if not isinstance(raw, dict):
        raise VerificationError("备份 manifest schema 无效")
    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise VerificationError("备份 manifest 版本无效")
    manifest_keys = {
        1: _MANIFEST_KEYS_V1,
        BACKUP_MANIFEST_VERSION: _MANIFEST_KEYS_V2,
    }.get(version)
    if manifest_keys is None or set(raw) != manifest_keys:
        raise VerificationError("备份 manifest schema 无效")
    if raw.get("schema") != BACKUP_MANIFEST_SCHEMA:
        raise VerificationError("备份 manifest 版本不受支持")
    if not isinstance(raw.get("profile_key"), str) or not raw["profile_key"].strip():
        raise VerificationError("备份 manifest profile 无效")
    if not isinstance(raw.get("adb_serial"), str) or not raw["adb_serial"].strip():
        raise VerificationError("备份 manifest serial 无效")
    if raw.get("initial_slot") not in {"_a", "_b"}:
        raise VerificationError("备份 manifest 初始槽位无效")
    partitions = raw.get("partitions")
    if not isinstance(partitions, dict) or not partitions:
        raise VerificationError("备份 manifest 分区条目无效")
    for partition, entry in partitions.items():
        if not isinstance(partition, str) or _PARTITION_LABEL.fullmatch(partition) is None:
            raise VerificationError("备份 manifest 分区名无效")
        if not isinstance(entry, dict) or set(entry) != _PARTITION_ENTRY_KEYS:
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
        _validate_dyn_manifest_entries(raw["dyn"])
    return raw


def _validate_dyn_manifest_entries(entries: object) -> None:
    if not isinstance(entries, list):
        raise VerificationError("Vivo dyn manifest schema invalid")
    seen_names: set[str] = set()
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
        if not isinstance(entry.get("size"), int) or isinstance(entry["size"], bool) or entry["size"] <= 0:
            raise VerificationError("Vivo dyn manifest size invalid")
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
    try:
        resolved_directory = target_directory.resolve(strict=True)
        path = (directory / relative).resolve(strict=True)
    except OSError as exc:
        raise VerificationError("Vivo dyn backup file cannot be resolved") from exc
    if path.parent != resolved_directory or path.name != name:
        raise VerificationError("Vivo dyn backup path escapes its directory")
    return path


def _regular_file_snapshot(path: Path) -> tuple[int, str]:
    try:
        metadata = Path(path).lstat()
    except FileNotFoundError:
        raise VerificationError(f"backup file is missing: {Path(path).name}") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise VerificationError(f"backup file is not a non-empty regular file: {Path(path).name}")
    return metadata.st_size, _sha256_file(Path(path))


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
