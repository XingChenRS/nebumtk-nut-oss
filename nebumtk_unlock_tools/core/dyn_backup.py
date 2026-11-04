# SPDX-License-Identifier: AGPL-3.0-only
"""Verified Vivo dyn preservation through an already-existing root shell."""

from __future__ import annotations

import hashlib
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .adb import AdbClient
from .errors import AdbError, VerificationError
from .su import validate_custom_su


DYN_REMOTE_DIRECTORY = "/data/vivo-common/dyn"
MAX_DYN_FILES = 128
MAX_DYN_FILE_BYTES = 64 * 1024 * 1024
MAX_DYN_TOTAL_BYTES = 256 * 1024 * 1024
_REMOTE_TMP = "/data/local/tmp"
_SAFE_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_METADATA = re.compile(r"([0-7]{3,4}):([0-9]{1,10}):([0-9]{1,10})")


@dataclass(frozen=True)
class DynBackupFile:
    name: str
    path: Path
    size: int
    sha256: str
    mode: str | None
    uid: str | None
    gid: str | None


@dataclass(frozen=True)
class DynBackup:
    files: tuple[DynBackupFile, ...]


@dataclass(frozen=True)
class DynRestoreOutcome:
    restored: bool
    reason: str
    restored_files: int = 0


def capture_dyn_backup(
    adb: AdbClient,
    destination: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> DynBackup | None:
    """Copy bounded ordinary direct files from Vivo dyn into an operation dir."""

    _ensure_not_cancelled(cancelled)
    log = log or (lambda _message: None)
    local_root = Path(destination)
    if not local_root.is_dir():
        raise VerificationError("dyn operation destination is not a directory")
    command = (
        f"if [ ! -d {AdbClient._quote(DYN_REMOTE_DIRECTORY)} ]; then exit 3; fi; "
        f"for file in {AdbClient._quote(DYN_REMOTE_DIRECTORY)}/*; do "
        '[ -f "$file" ] && [ ! -L "$file" ] || continue; '
        'name=${file##*/}; '
        "meta=$(stat -c '%a:%u:%g' \"$file\" 2>/dev/null || toybox stat -c '%a:%u:%g' \"$file\" 2>/dev/null || true); "
        "printf '%s\\t%s\\n' \"$name\" \"$meta\"; "
        "done"
    )
    listing = adb.shell(command, root=True, timeout=30, check=False)
    _ensure_not_cancelled(cancelled)
    if listing.returncode == 3:
        log("Vivo dyn directory is absent; no dyn backup is needed")
        return None
    if listing.returncode != 0:
        raise AdbError("unable to enumerate Vivo dyn files")
    entries = _parse_listing(listing.stdout)
    if not entries:
        log("Vivo dyn directory is empty; no dyn backup is needed")
        return None

    operation = uuid.uuid4().hex
    copied: list[DynBackupFile] = []
    total_size = 0
    try:
        for index, (name, mode, uid, gid) in enumerate(entries):
            _ensure_not_cancelled(cancelled)
            local = local_root / f"dyn-{name}"
            if local.exists():
                raise VerificationError(f"dyn operation file already exists: {local.name}")
            source = f"{DYN_REMOTE_DIRECTORY}/{name}"
            remote = f"{_REMOTE_TMP}/nebumtk-dyn-{operation}-{index}.bin"
            _ensure_not_cancelled(cancelled)
            adb.remove_remote(remote, root=False)
            try:
                export = (
                    f"dd if={AdbClient._quote(source)} of={AdbClient._quote(remote)} conv=fsync "
                    f"&& chown 2000:2000 {AdbClient._quote(remote)} "
                    f"&& chmod 600 {AdbClient._quote(remote)}"
                )
                _ensure_not_cancelled(cancelled)
                result = adb.shell(export, root=True, timeout=120, check=False)
                _ensure_not_cancelled(cancelled)
                if result.returncode != 0:
                    raise AdbError(f"unable to stage Vivo dyn file: {name}")
                _ensure_not_cancelled(cancelled)
                remote_size = adb.remote_file_size(remote, root=False)
                _ensure_not_cancelled(cancelled)
                if remote_size <= 0 or remote_size > MAX_DYN_FILE_BYTES:
                    raise VerificationError(f"staged Vivo dyn file size is invalid: {name}")
                total_size += remote_size
                if total_size > MAX_DYN_TOTAL_BYTES:
                    raise VerificationError("Vivo dyn backup exceeds total size limit")
                _ensure_not_cancelled(cancelled)
                remote_sha256 = adb.remote_sha256(remote, root=False)
                _ensure_not_cancelled(cancelled)
                adb.pull(remote, local, cancelled=cancelled)
                _ensure_not_cancelled(cancelled)
                local_size, local_sha256 = _local_snapshot(local)
                if local_size != remote_size:
                    raise VerificationError(f"Vivo dyn backup size mismatch: {name}")
                if remote_sha256 and remote_sha256 != local_sha256:
                    raise VerificationError(f"Vivo dyn backup hash mismatch: {name}")
                copied.append(DynBackupFile(name, local, local_size, local_sha256, mode, uid, gid))
            except Exception:
                local.unlink(missing_ok=True)
                raise
            finally:
                adb.remove_remote(remote, root=False)
        _ensure_not_cancelled(cancelled)
    except Exception:
        for item in copied:
            item.path.unlink(missing_ok=True)
        raise
    return DynBackup(tuple(copied))


def restore_dyn_backup(
    adb: AdbClient,
    backup: DynBackup,
    *,
    su_candidates: Iterable[str],
    cancelled: Callable[[], bool] | None = None,
) -> DynRestoreOutcome:
    """Restore a verified snapshot only through an explicitly supplied real su."""

    _ensure_not_cancelled(cancelled)
    if not backup.files:
        return DynRestoreOutcome(False, "empty")
    _validate_backup_bounds(backup)
    for item in backup.files:
        _verify_backup_file(item)
    _ensure_not_cancelled(cancelled)
    su_path = _find_existing_root_su(adb, su_candidates, cancelled=cancelled)
    _ensure_not_cancelled(cancelled)
    if su_path is None:
        return DynRestoreOutcome(False, "root-unavailable")

    operation = uuid.uuid4().hex
    create_dir = (
        f"mkdir -p {AdbClient._quote(DYN_REMOTE_DIRECTORY)} "
        f"&& (command -v restorecon >/dev/null 2>&1 && restorecon {AdbClient._quote(DYN_REMOTE_DIRECTORY)} || true)"
    )
    _ensure_not_cancelled(cancelled)
    prepared = adb.shell(create_dir, root=True, su_path=su_path, timeout=30, check=False)
    _ensure_not_cancelled(cancelled)
    if prepared.returncode != 0:
        raise AdbError("unable to create Vivo dyn destination")

    restored = 0
    for index, item in enumerate(backup.files):
        _ensure_not_cancelled(cancelled)
        remote = f"{_REMOTE_TMP}/nebumtk-dyn-restore-{operation}-{index}.bin"
        target = f"{DYN_REMOTE_DIRECTORY}/{item.name}"
        _ensure_not_cancelled(cancelled)
        adb.remove_remote(remote, root=False)
        try:
            _ensure_not_cancelled(cancelled)
            adb.push(item.path, remote)
            _ensure_not_cancelled(cancelled)
            remote_size = adb.remote_file_size(remote, root=False)
            _ensure_not_cancelled(cancelled)
            remote_sha256 = adb.remote_sha256(remote, root=False)
            _ensure_not_cancelled(cancelled)
            if remote_size != item.size:
                raise VerificationError(f"Vivo dyn restore staging size mismatch: {item.name}")
            if remote_sha256 and remote_sha256 != item.sha256:
                raise VerificationError(f"Vivo dyn restore staging hash mismatch: {item.name}")
            metadata = ""
            if item.uid is not None and item.gid is not None:
                metadata += f" && chown {item.uid}:{item.gid} {AdbClient._quote(target)}"
            if item.mode is not None:
                metadata += f" && chmod {item.mode} {AdbClient._quote(target)}"
            write = (
                f"dd if={AdbClient._quote(remote)} of={AdbClient._quote(target)} conv=fsync"
                f"{metadata}"
                f" && (command -v restorecon >/dev/null 2>&1 && restorecon {AdbClient._quote(target)} || true)"
            )
            _ensure_not_cancelled(cancelled)
            result = adb.shell(
                write,
                root=True,
                su_path=su_path,
                timeout=120,
                check=False,
                cancellable=False,
            )
            _ensure_not_cancelled(cancelled)
            if result.returncode != 0:
                raise AdbError(f"unable to restore Vivo dyn file: {item.name}")
            restored += 1
        finally:
            adb.remove_remote(remote, root=False)
    _ensure_not_cancelled(cancelled)
    adb.reboot(cancellable=False)
    return DynRestoreOutcome(True, "restored", restored)


def _parse_listing(output: str) -> list[tuple[str, str | None, str | None, str | None]]:
    parsed: list[tuple[str, str | None, str | None, str | None]] = []
    seen: set[str] = set()
    for raw in output.splitlines():
        if not raw:
            continue
        name, separator, metadata = raw.partition("\t")
        if not separator or _SAFE_FILE_NAME.fullmatch(name) is None or name.casefold() in seen:
            raise VerificationError("Vivo dyn listing contains an unsafe filename")
        seen.add(name.casefold())
        if len(parsed) >= MAX_DYN_FILES:
            raise VerificationError("Vivo dyn listing exceeds file limit")
        match = _SAFE_METADATA.fullmatch(metadata.strip())
        if match is None:
            parsed.append((name, None, None, None))
        else:
            parsed.append((name, match.group(1), match.group(2), match.group(3)))
    return sorted(parsed, key=lambda item: item[0])


def _find_existing_root_su(
    adb: AdbClient,
    candidates: Iterable[str],
    *,
    cancelled: Callable[[], bool] | None = None,
) -> str | None:
    tried: set[str] = set()
    for raw_candidate in candidates:
        _ensure_not_cancelled(cancelled)
        try:
            candidate = validate_custom_su(str(raw_candidate).strip())
        except ValueError:
            continue
        if candidate in tried:
            continue
        tried.add(candidate)
        try:
            _ensure_not_cancelled(cancelled)
            result = adb.shell("id -u", root=True, su_path=candidate, timeout=20, check=False)
            _ensure_not_cancelled(cancelled)
        except AdbError:
            continue
        if result.returncode == 0 and result.stdout.strip() == "0":
            return candidate
    return None


def _validate_backup_bounds(backup: DynBackup) -> None:
    if len(backup.files) > MAX_DYN_FILES:
        raise VerificationError("Vivo dyn backup exceeds file limit")
    total = 0
    for item in backup.files:
        if item.size <= 0 or item.size > MAX_DYN_FILE_BYTES:
            raise VerificationError(f"Vivo dyn backup size is invalid: {item.name}")
        total += item.size
    if total > MAX_DYN_TOTAL_BYTES:
        raise VerificationError("Vivo dyn backup exceeds total size limit")


def _local_snapshot(path: Path) -> tuple[int, str]:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        raise VerificationError(f"Vivo dyn file is missing: {path.name}") from None
    if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISREG(stat_result.st_mode):
        raise VerificationError(f"Vivo dyn file is not a regular file: {path.name}")
    if stat_result.st_size <= 0 or stat_result.st_size > MAX_DYN_FILE_BYTES:
        raise VerificationError(f"Vivo dyn file size is invalid: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return stat_result.st_size, digest.hexdigest()


def _verify_backup_file(item: DynBackupFile) -> None:
    if _SAFE_FILE_NAME.fullmatch(item.name) is None:
        raise VerificationError("Vivo dyn backup filename is invalid")
    size, sha256 = _local_snapshot(item.path)
    if size != item.size or sha256 != item.sha256:
        raise VerificationError(f"Vivo dyn backup changed: {item.name}")


def _ensure_not_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        from .errors import FlowCancelledError

        raise FlowCancelledError("operation cancelled")
