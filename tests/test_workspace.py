# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import stat
from pathlib import Path

import pytest

from nebumtk_unlock_tools.core.operation_files import OperationFiles
from nebumtk_unlock_tools.core.workspace import WorkspaceManager


FORBIDDEN_WORKSPACE_APIS = {
    "device_dir",
    "current_dir",
    "artifacts_dir",
    "session_path",
    "list_sessions",
    "ensure_current",
    "load_session",
    "save_session",
    "init_session",
    "mark_phase",
    "record_phase_outcome",
    "record_phase_error",
    "register_patched_lk",
    "patched_lk_path",
    "invalidate_backup_authorization",
    "register_backup_records",
    "verify_export_backup",
    "register_patched_init_boot",
    "patched_init_boot_path",
    "export_lk_path",
    "verify_export_lk",
}


FORBIDDEN_MODULE_SYMBOLS = {
    "PhaseState",
    "SessionData",
    "_utc_now",
    "_metadata_size",
    "_metadata_sha256",
}


def test_workspace_exposes_only_minimal_boundary_and_no_session_registry_apis() -> None:
    workspace = WorkspaceManager(Path("workspace-root"))

    public_names = {name for name in dir(workspace) if not name.startswith("_")}

    assert FORBIDDEN_WORKSPACE_APIS.isdisjoint(dir(workspace))
    assert {"exports_dir", "logs_dir", "operation_files", "sha256_file"}.issubset(public_names)


def test_workspace_module_does_not_export_session_state_symbols() -> None:
    import nebumtk_unlock_tools.core.workspace as workspace_module

    assert FORBIDDEN_MODULE_SYMBOLS.isdisjoint(dir(workspace_module))


def test_constructing_workspace_does_not_create_scan_mutate_or_delete_directories(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    legacy_session = root / "OLD" / "current" / "session.json"
    legacy_session.parent.mkdir(parents=True)
    legacy_payload = '{"serial":"OLD","phases":{"backup_export":{"status":"done"}}}'
    legacy_session.write_text(legacy_payload, encoding="utf-8")
    before_paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))

    workspace = WorkspaceManager(root)

    after_paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    assert workspace.root == root
    assert before_paths == after_paths
    assert legacy_session.read_text(encoding="utf-8") == legacy_payload


def test_constructing_workspace_for_missing_root_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "missing-workspace"

    WorkspaceManager(root)

    assert not root.exists()


def test_logs_dir_is_pure_path_helper_and_does_not_create_directory(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspace")

    logs = workspace.logs_dir()

    assert logs == tmp_path / "workspace" / "logs"
    assert not logs.exists()
    assert not workspace.root.exists()


def test_exports_dir_creates_single_sanitized_segment_under_exports(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspace")

    exports = workspace.exports_dir("../bad label//..\\evil")

    assert exports.parent == workspace.root / "exports"
    assert exports.name == "bad-label-evil"
    assert exports.is_dir()
    assert exports.resolve().is_relative_to((workspace.root / "exports").resolve())
    assert ".." not in exports.relative_to(workspace.root / "exports").parts


def test_exports_dir_uses_default_label(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspace")

    exports = workspace.exports_dir()

    assert exports == workspace.root / "exports" / "exports"
    assert exports.is_dir()


@pytest.mark.parametrize(
    ("reserved", "expected"),
    (
        ("CON", "CON_"),
        ("nul", "nul_"),
        ("PrN.txt", "PrN_.txt"),
        ("AUX.backup", "AUX_.backup"),
        ("CLOCK$", "CLOCK$_"),
        ("com1", "com1_"),
        ("COM9.img", "COM9_.img"),
        ("lpt1", "lpt1_"),
        ("LPT9.txt", "LPT9_.txt"),
    ),
)
def test_exports_dir_transforms_windows_reserved_device_labels(tmp_path: Path, reserved: str, expected: str) -> None:
    workspace = WorkspaceManager(tmp_path / "workspace")

    exports = workspace.exports_dir(reserved)

    assert exports == workspace.root / "exports" / expected
    assert exports.is_dir()


def test_exports_dir_rejects_existing_exports_symlink_without_mutating_it(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    target = tmp_path / "outside"
    root.mkdir()
    target.mkdir()
    exports_parent = root / "exports"
    try:
        exports_parent.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this platform")

    with pytest.raises(ValueError, match="exports|workspace|directory|reparse|symlink"):
        WorkspaceManager(root).exports_dir("safe")

    assert exports_parent.is_symlink()
    assert target.is_dir()
    assert not (target / "safe").exists()


class _FakeStatResult:
    def __init__(self, *, st_dev: int = 1, st_ino: int = 1, reparse: bool = True) -> None:
        self.st_dev = st_dev
        self.st_ino = st_ino
        self.st_mode = stat.S_IFDIR | 0o700
        self.st_file_attributes = 0x400 if reparse else 0


def test_exports_dir_rejects_reparse_point_detected_by_lstat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    exports_parent = root / "exports"
    exports_parent.mkdir(parents=True)
    original_lstat = Path.lstat

    def fake_lstat(path: Path):
        if path == exports_parent:
            return _FakeStatResult()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="exports|workspace|directory|reparse|symlink"):
        WorkspaceManager(root).exports_dir("safe")

    assert exports_parent.is_dir()
    assert not (exports_parent / "safe").exists()


def test_exports_dir_rejects_exports_parent_replaced_before_target_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    exports_parent = root / "exports"
    original_lstat = Path.lstat
    seen_exports_parent = 0

    def fake_lstat(path: Path):
        nonlocal seen_exports_parent
        if path == exports_parent and path.exists():
            seen_exports_parent += 1
            if seen_exports_parent >= 2:
                return _FakeStatResult(st_dev=99, st_ino=99, reparse=False)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="changed|escaped|exports|workspace|symlink|reparse"):
        WorkspaceManager(root).exports_dir("safe")

    assert not (exports_parent / "safe").exists()


def test_exports_dir_rejects_exports_parent_replaced_after_target_creation_without_deleting_through_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    exports_parent = root / "exports"
    target = exports_parent / "safe"
    original_lstat = Path.lstat
    original_mkdir = Path.mkdir
    target_created = False

    def fake_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal target_created
        original_mkdir(path, *args, **kwargs)
        if path == target:
            target_created = True

    def fake_lstat(path: Path):
        if path == exports_parent and target_created:
            return _FakeStatResult(st_dev=101, st_ino=101, reparse=False)
        return original_lstat(path)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="changed|escaped|exports|workspace|symlink|reparse"):
        WorkspaceManager(root).exports_dir("safe")

    assert target.is_dir()


def test_exports_dir_rejects_workspace_root_replaced_after_exports_parent_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    exports_parent = root / "exports"
    original_lstat = Path.lstat
    seen_exports_parent = 0

    def fake_lstat(path: Path):
        nonlocal seen_exports_parent
        if path == exports_parent and path.exists():
            seen_exports_parent += 1
        if path == root and seen_exports_parent >= 1:
            return _FakeStatResult(st_dev=202, st_ino=202, reparse=False)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(ValueError, match="changed|escaped|exports|workspace|symlink|reparse"):
        WorkspaceManager(root).exports_dir("safe")

    assert not (exports_parent / "safe").exists()


class _RecordingBinaryStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.read_sizes: list[int] = []

    def __enter__(self) -> _RecordingBinaryStream:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if self._offset >= len(self._payload):
            return b""
        if size < 0:
            chunk = self._payload[self._offset :]
            self._offset = len(self._payload)
            return chunk
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _RecordingPath:
    def __init__(self, payload: bytes) -> None:
        self.stream = _RecordingBinaryStream(payload)

    def open(self, mode: str) -> _RecordingBinaryStream:
        assert mode == "rb"
        return self.stream


def test_sha256_file_hashes_streamed_one_mebibyte_chunks() -> None:
    payload = (b"0123456789abcdef" * 70_000) + b"tail"
    path = _RecordingPath(payload)

    digest = WorkspaceManager.sha256_file(path)  # type: ignore[arg-type]

    assert digest == hashlib.sha256(payload).hexdigest()
    assert path.stream.read_sizes[:2] == [1024 * 1024, 1024 * 1024]
    assert path.stream.read_sizes[-1] == 1024 * 1024


def test_operation_files_returns_task1_operation_files_with_unique_directories(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspace")

    first = workspace.operation_files("patch lk")
    second = workspace.operation_files("patch lk")

    assert isinstance(first, OperationFiles)
    assert isinstance(second, OperationFiles)
    assert first.directory != second.directory
    assert first.directory.parent == workspace.root / "tmp"
    assert second.directory.parent == workspace.root / "tmp"
    assert first.directory.name.startswith("patch-lk-")
    assert second.directory.name.startswith("patch-lk-")
    assert first.directory.is_dir()
    assert second.directory.is_dir()
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
