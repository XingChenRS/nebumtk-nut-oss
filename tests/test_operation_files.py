# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.core.operation_files import OperationFiles, VerifiedFileSnapshot


def test_copy_input_freezes_metadata_and_private_copy(tmp_path: Path) -> None:
    source = tmp_path / "source image.img"
    source.write_bytes(b"image-payload")
    operation = OperationFiles(tmp_path / "workspace", "Copy Image!")

    snapshot = operation.copy_input(source, "copied image.img")

    assert isinstance(snapshot, VerifiedFileSnapshot)
    assert snapshot.size == len(b"image-payload")
    assert len(snapshot.sha256) == 64
    assert snapshot.path.read_bytes() == b"image-payload"
    assert snapshot.path != source
    assert snapshot.path.parent == operation.directory
    assert snapshot.path.name == "copied image.img"
    assert operation.directory.parent == tmp_path / "workspace" / "tmp"
    assert operation.directory.name.startswith("Copy-Image-")


def test_operation_directories_are_unique_for_same_label(tmp_path: Path) -> None:
    first = OperationFiles(tmp_path / "workspace", "same label")
    second = OperationFiles(tmp_path / "workspace", "same label")

    assert first.directory != second.directory
    assert first.directory.parent == second.directory.parent == tmp_path / "workspace" / "tmp"


def test_verify_unchanged_rejects_equal_size_byte_mutation_by_sha256(tmp_path: Path) -> None:
    source = tmp_path / "source.img"
    source.write_bytes(b"same-size-a")
    operation = OperationFiles(tmp_path / "workspace", "verify hash")
    snapshot = operation.copy_input(source, "source.img")

    snapshot.path.write_bytes(b"same-size-b")

    with pytest.raises(VerificationError, match="SHA-256"):
        operation.verify_unchanged(snapshot)


def test_verify_unchanged_rejects_size_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source.img"
    source.write_bytes(b"original-size")
    operation = OperationFiles(tmp_path / "workspace", "verify size")
    snapshot = operation.copy_input(source, "source.img")

    snapshot.path.write_bytes(b"short")

    with pytest.raises(VerificationError, match="size"):
        operation.verify_unchanged(snapshot)


def test_copy_input_rejects_explicit_symlink_input(tmp_path: Path) -> None:
    target = tmp_path / "target.img"
    target.write_bytes(b"payload")
    source = tmp_path / "source-link.img"
    try:
        source.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    operation = OperationFiles(tmp_path / "workspace", "symlink input")

    with pytest.raises(VerificationError, match="regular|reparse|symlink"):
        operation.copy_input(source, "copy.img")


@pytest.mark.skipif(not hasattr(__import__("os"), "lstat"), reason="lstat unavailable")
def test_copy_input_rejects_windows_reparse_point_attribute_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    source = tmp_path / "source.img"
    source.write_bytes(b"payload")
    operation = OperationFiles(tmp_path / "workspace", "reparse input")
    real_lstat = Path.lstat

    class StatWithReparse:
        def __init__(self, wrapped: object) -> None:
            self._wrapped = wrapped
            self.st_file_attributes = getattr(wrapped, "st_file_attributes", 0) | 0x400

        def __getattr__(self, name: str) -> object:
            return getattr(self._wrapped, name)

    def fake_lstat(path: Path):
        stat_result = real_lstat(path)
        if path == source:
            return StatWithReparse(stat_result)
        return stat_result

    monkeypatch.setattr(Path, "lstat", fake_lstat)

    with pytest.raises(VerificationError, match="reparse"):
        operation.copy_input(source, "copy.img")


@pytest.mark.parametrize(
    ("source_name", "prepare", "expected_error"),
    [
        ("missing.img", lambda path: None, FileNotFoundError),
        ("directory.img", lambda path: path.mkdir(), VerificationError),
        ("empty.img", lambda path: path.write_bytes(b""), VerificationError),
    ],
)
def test_copy_input_rejects_missing_directory_and_empty_inputs(
    tmp_path: Path,
    source_name: str,
    prepare,
    expected_error: type[Exception],
) -> None:
    source = tmp_path / source_name
    prepare(source)
    operation = OperationFiles(tmp_path / "workspace", "bad input")

    with pytest.raises(expected_error):
        operation.copy_input(source, "copy.img")


def test_traversal_destination_names_remain_contained(tmp_path: Path) -> None:
    source = tmp_path / "input.img"
    source.write_bytes(b"payload")
    operation = OperationFiles(tmp_path / "workspace", "traversal")

    snapshot = operation.copy_input(source, "..\\..\\outside.img")

    assert snapshot.path.parent == operation.directory
    assert snapshot.path.name == "outside.img"
    assert (operation.directory / "outside.img").read_bytes() == b"payload"
    assert not (tmp_path / "outside.img").exists()


def test_cleanup_fails_closed_when_operation_directory_is_replaced_by_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    operation = OperationFiles(workspace, "cleanup replaced")
    original_directory = operation.directory
    replacement_target = tmp_path / "replacement-target"
    replacement_target.mkdir()
    (replacement_target / "keep.txt").write_text("replacement data", encoding="utf-8")
    shutil.rmtree(original_directory)
    try:
        original_directory.symlink_to(replacement_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")

    with pytest.raises(VerificationError, match="operation directory"):
        operation.cleanup()

    assert original_directory.exists()
    assert (replacement_target / "keep.txt").read_text(encoding="utf-8") == "replacement data"


def test_cleanup_removes_original_directory_after_copy_and_preserves_siblings(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    sibling_log = workspace / "tmp" / "keep.log"
    sibling_export = workspace / "exports" / "keep.img"
    sibling_log.parent.mkdir(parents=True)
    sibling_export.parent.mkdir(parents=True)
    sibling_log.write_text("keep log", encoding="utf-8")
    sibling_export.write_bytes(b"keep export")
    operation = OperationFiles(workspace, "cleanup")
    source = tmp_path / "source.img"
    source.write_bytes(b"payload")
    operation.copy_input(source, "copy.img")

    operation.cleanup()
    operation.cleanup()

    assert not operation.directory.exists()
    assert sibling_log.read_text(encoding="utf-8") == "keep log"
    assert sibling_export.read_bytes() == b"keep export"


def test_cleanup_fails_closed_when_operation_directory_is_replaced_by_real_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    operation = OperationFiles(workspace, "cleanup replaced real dir")
    original_directory = operation.directory
    shutil.rmtree(original_directory)
    original_directory.mkdir()
    marker = original_directory / "replacement-marker.txt"
    marker.write_text("do not delete", encoding="utf-8")

    with pytest.raises(VerificationError, match="operation directory"):
        operation.cleanup()

    assert marker.read_text(encoding="utf-8") == "do not delete"
