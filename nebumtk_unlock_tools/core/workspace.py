"""Workspace file-service boundary."""

from __future__ import annotations

import hashlib
import re
import stat
from pathlib import Path

from .operation_files import OperationFiles

_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_RESERVED_DOS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _sanitize_label(label: str) -> str:
    without_parent_segments = re.sub(r"(?:^|[\\/]+)\.\.(?=$|[\\/]+)", "-", label)
    sanitized = re.sub(r"[^A-Za-z0-9._$-]+", "-", without_parent_segments)
    sanitized = re.sub(r"-+", "-", sanitized).strip("-._") or "exports"
    return _avoid_reserved_dos_device_name(sanitized)


def _avoid_reserved_dos_device_name(segment: str) -> str:
    stem, dot, suffix = segment.partition(".")
    if stem.upper() in _RESERVED_DOS_DEVICE_NAMES:
        return f"{stem}_{dot}{suffix}"
    return segment


def _directory_identity(path: Path, label: str) -> tuple[int, int]:
    stat_result = path.lstat()
    if stat.S_ISLNK(stat_result.st_mode):
        raise ValueError(f"{label} must not be a symlink or reparse point: {path}")
    if getattr(stat_result, "st_file_attributes", 0) & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f"{label} must not be a symlink or reparse point: {path}")
    if not stat.S_ISDIR(stat_result.st_mode):
        raise ValueError(f"{label} must be a real directory: {path}")
    return (stat_result.st_dev, stat_result.st_ino)


def _assert_unchanged(path: Path, label: str, expected: tuple[int, int]) -> None:
    if _directory_identity(path, label) != expected:
        raise ValueError(f"{label} changed while preparing workspace path: {path}")


def _ensure_real_workspace_directory(path: Path, label: str) -> tuple[tuple[int, int], Path]:
    path.mkdir(exist_ok=True)
    identity = _directory_identity(path, label)
    canonical = path.resolve(strict=True)
    return identity, canonical


class WorkspaceManager:
    """Minimal workspace path and operation-file service."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def logs_dir(self) -> Path:
        return self.root / "logs"

    def exports_dir(self, label: str = "exports") -> Path:
        root_identity, workspace_root = _ensure_real_workspace_directory(self.root, "workspace root")

        exports_parent = self.root / "exports"
        exports_identity, canonical_exports_parent = _ensure_real_workspace_directory(
            exports_parent,
            "workspace exports directory",
        )
        _assert_unchanged(self.root, "workspace root", root_identity)
        _assert_unchanged(exports_parent, "workspace exports directory", exports_identity)
        if exports_parent.parent.resolve(strict=True) != workspace_root:
            raise ValueError(f"exports directory parent escaped workspace root: {exports_parent}")

        directory = exports_parent / _sanitize_label(label)
        _assert_unchanged(self.root, "workspace root", root_identity)
        _assert_unchanged(exports_parent, "workspace exports directory", exports_identity)
        directory_identity, _canonical_directory = _ensure_real_workspace_directory(
            directory,
            "workspace export directory",
        )
        _assert_unchanged(self.root, "workspace root", root_identity)
        _assert_unchanged(exports_parent, "workspace exports directory", exports_identity)
        _assert_unchanged(directory, "workspace export directory", directory_identity)
        if directory.resolve(strict=True).parent != canonical_exports_parent:
            raise ValueError(f"export directory escaped original workspace exports directory: {directory}")
        return directory

    @staticmethod
    def sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def operation_files(self, label: str) -> OperationFiles:
        return OperationFiles(self.root, label)
