# SPDX-License-Identifier: AGPL-3.0-only
"""Windows-native adapter for the reviewed SakiSU ksud boot patcher."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

from ..core.errors import FlowCancelledError, VerificationError
from ..core.operation_files import (
    OperationFiles,
    VerifiedFileSnapshot,
    snapshot_regular_file,
)
from ..core.runner import CommandResult, run_command
from .boot_image import verify_patch_candidate
from .host_importer import validate_imported_host_patcher
from .kmi import ManualKmiSelection, validate_manual_kmi_selection
from .policy import PatchAuthorization, validate_patch_authorization


class WindowsHostPatcher:
    """Run a pinned Windows x64 ksud without requiring a connected device."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        runner: Callable[..., CommandResult] = run_command,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.runner = runner

    @staticmethod
    def _verify_snapshot(snapshot: VerifiedFileSnapshot, *, label: str) -> None:
        current = snapshot_regular_file(snapshot.path)
        if current.size != snapshot.size or current.sha256 != snapshot.sha256:
            raise VerificationError(f"immutable {label} changed")

    @staticmethod
    def _ensure_not_cancelled(cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise FlowCancelledError("SakiSU local patch operation cancelled")

    @staticmethod
    def _paths_alias(left: Path, right: Path) -> bool:
        if os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
            str(right.resolve(strict=False))
        ):
            return True
        try:
            return left.samefile(right)
        except (FileNotFoundError, OSError):
            return False

    def patch(
        self,
        patcher: VerifiedFileSnapshot,
        source: VerifiedFileSnapshot,
        *,
        authorization: PatchAuthorization,
        manual_kmi: ManualKmiSelection,
        partition: str,
        destination: Path,
        cancelled: Callable[[], bool],
    ) -> VerifiedFileSnapshot:
        partition = str(partition).strip().lower()
        if partition not in {"boot", "init_boot"}:
            raise VerificationError("SakiSU local patch partition must be boot or init_boot")
        destination = Path(destination)
        if destination.exists() and not destination.is_file():
            raise VerificationError("SakiSU candidate destination must be a regular file")
        if self._paths_alias(destination, source.path) or self._paths_alias(
            destination, patcher.path
        ):
            raise VerificationError("SakiSU candidate destination aliases an immutable input")
        validate_imported_host_patcher(patcher)
        self._verify_snapshot(source, label="source")
        validate_patch_authorization(authorization, source, partition=partition)
        validate_manual_kmi_selection(manual_kmi)
        self._ensure_not_cancelled(cancelled)

        operation = OperationFiles(self.workspace_root, "sakisu-host-patch")
        output = operation.directory / "candidate.img"
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.pending"
        )
        try:
            result = self.runner(
                [
                    str(patcher.path),
                    "boot-patch",
                    "--boot",
                    str(source.path),
                    "--out",
                    str(operation.directory),
                    "--out-name",
                    output.name,
                    "--kmi",
                    manual_kmi.kmi,
                ],
                timeout=300,
                check=False,
                sensitive=True,
                cancelled=cancelled,
            )
            if result.returncode != 0:
                raise VerificationError("official SakiSU Windows patcher returned nonzero")
            self._ensure_not_cancelled(cancelled)
            verification = verify_patch_candidate(
                source.path,
                output,
                partition=partition,
            )
            validate_imported_host_patcher(patcher)
            self._verify_snapshot(source, label="source")
            validate_patch_authorization(authorization, source, partition=partition)
            validate_manual_kmi_selection(manual_kmi)
            self._ensure_not_cancelled(cancelled)

            shutil.copyfile(output, pending)
            copied = snapshot_regular_file(pending)
            if (
                copied.size != verification.output_size
                or copied.sha256 != verification.output_sha256
            ):
                raise VerificationError("SakiSU local candidate copy verification failed")
            self._ensure_not_cancelled(cancelled)
            pending.replace(destination)
            return VerifiedFileSnapshot(
                destination,
                copied.size,
                copied.sha256,
            )
        finally:
            pending.unlink(missing_ok=True)
            operation.cleanup()
