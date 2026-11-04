from __future__ import annotations

import hashlib
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import FlowCancelledError, VerificationError

_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class _PathIdentity:
    st_dev: int
    st_ino: int


@dataclass(frozen=True)
class VerifiedFileSnapshot:
    path: Path
    size: int
    sha256: str


class OperationFiles:
    def __init__(self, workspace_root: Path, label: str) -> None:
        safe_label = _sanitize_label(label)
        self._directory = Path(workspace_root) / "tmp" / f"{safe_label}-{uuid.uuid4().hex}"
        self._directory.mkdir(parents=True, exist_ok=False)
        self._directory_identity = _directory_identity(self._directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def copy_input(self, source: Path, name: str) -> VerifiedFileSnapshot:
        source_path = Path(source)
        source_snapshot = _snapshot_regular_non_empty(source_path)
        destination = self._directory / Path(name).name
        target_path = _contained_child(self._directory, destination)

        shutil.copyfile(source_path, target_path)
        target_snapshot = _snapshot_regular_non_empty(target_path)
        if source_snapshot.size != target_snapshot.size:
            raise VerificationError("Input file copy size mismatch")
        if source_snapshot.sha256 != target_snapshot.sha256:
            raise VerificationError("Input file copy SHA-256 mismatch")
        return target_snapshot

    def copy_verified_input(
        self,
        expected: VerifiedFileSnapshot,
        name: str,
    ) -> VerifiedFileSnapshot:
        current = snapshot_regular_file(expected.path)
        if current.size != expected.size or current.sha256 != expected.sha256:
            raise VerificationError("Input changed after confirmation")
        destination = _contained_child(self._directory, self._directory / Path(name).name)
        shutil.copyfile(expected.path, destination)
        source_after = snapshot_regular_file(expected.path)
        target = snapshot_regular_file(destination)
        if source_after.size != expected.size or source_after.sha256 != expected.sha256:
            raise VerificationError("Input changed after confirmation")
        if target.size != expected.size or target.sha256 != expected.sha256:
            raise VerificationError("Input copy changed after confirmation")
        return target

    def verify_unchanged(self, snapshot: VerifiedFileSnapshot) -> None:
        current = _snapshot_regular_non_empty(snapshot.path)
        if current.size != snapshot.size:
            raise VerificationError("Verified file size changed")
        if current.sha256 != snapshot.sha256:
            raise VerificationError("Verified file SHA-256 changed")

    def cleanup(self) -> None:
        try:
            current_identity = _directory_identity(self._directory)
        except FileNotFoundError:
            return
        if current_identity != self._directory_identity:
            raise VerificationError("Refusing to remove replaced operation directory")
        shutil.rmtree(self._directory)


def _sanitize_label(label: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._")
    return sanitized or "operation"


def _contained_child(directory: Path, path: Path) -> Path:
    resolved_directory = directory.resolve()
    resolved_path = path.resolve(strict=False)
    if resolved_path.parent != resolved_directory:
        raise VerificationError("Destination path must stay inside operation directory")
    return path


def _snapshot_regular_non_empty(
    path: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> VerifiedFileSnapshot:
    stat_result = _lstat_existing(path)
    _reject_reparse_or_symlink(path, stat_result)
    if not stat.S_ISREG(stat_result.st_mode):
        raise VerificationError(f"Input is not a regular file: {path}")
    size = stat_result.st_size
    if size <= 0:
        raise VerificationError(f"Input file is empty: {path}")
    return VerifiedFileSnapshot(
        path=path,
        size=size,
        sha256=_sha256_file(path, cancelled=cancelled),
    )


def snapshot_regular_file(
    path: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> VerifiedFileSnapshot:
    """Return immutable size/hash facts for a regular non-empty local file."""

    return _snapshot_regular_non_empty(Path(path), cancelled=cancelled)


def _lstat_existing(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        raise FileNotFoundError(path) from None


def _reject_reparse_or_symlink(path: Path, stat_result: object) -> None:
    if stat.S_ISLNK(stat_result.st_mode):
        raise VerificationError(f"Input is not a regular file; symlink/reparse objects are rejected: {path}")
    if getattr(stat_result, "st_file_attributes", 0) & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise VerificationError(f"Input is not a regular file; reparse objects are rejected: {path}")


def _directory_identity(path: Path) -> _PathIdentity:
    stat_result = _lstat_existing(path)
    _reject_reparse_or_symlink(path, stat_result)
    if not stat.S_ISDIR(stat_result.st_mode):
        raise VerificationError(f"Operation directory is not a real directory: {path}")
    return _PathIdentity(
        st_dev=stat_result.st_dev,
        st_ino=stat_result.st_ino,
    )


def _sha256_file(
    path: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if cancelled is not None and cancelled():
                raise FlowCancelledError("local file verification cancelled")
            digest.update(chunk)
    if cancelled is not None and cancelled():
        raise FlowCancelledError("local file verification cancelled")
    return digest.hexdigest()
