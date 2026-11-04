# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Unprivileged Android-device adapter for the official SakiSU patcher."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from pathlib import Path
from types import TracebackType
from typing import Callable

from ..core.errors import FlowCancelledError, VerificationError
from ..core.operation_files import VerifiedFileSnapshot
from .boot_image import MAX_PATCH_GROWTH, verify_patch_candidate
from .importer import validate_imported_patcher
from .kmi import ManualKmiSelection, validate_manual_kmi_selection
from .policy import PatchAuthorization, validate_patch_authorization


class AndroidDevicePatcher:
    def __init__(self, adb, *, token_factory: Callable[[], str] | None = None) -> None:
        self.adb = adb
        self.token_factory = token_factory or (lambda: secrets.token_hex(16))

    @staticmethod
    def _verify_snapshot(snapshot: VerifiedFileSnapshot, *, label: str) -> None:
        try:
            size = snapshot.path.stat().st_size
        except FileNotFoundError as exc:
            raise VerificationError(f"immutable {label} is missing") from exc
        digest = hashlib.sha256(snapshot.path.read_bytes()).hexdigest()
        if size != snapshot.size or digest != snapshot.sha256:
            raise VerificationError(f"immutable {label} changed")

    @staticmethod
    def _ensure_not_cancelled(cancelled: Callable[[], bool]) -> None:
        if cancelled():
            raise FlowCancelledError("SakiSU patch-only operation cancelled")

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
        if partition == "vendor_boot":
            raise VerificationError("vendor_boot is not accepted by the SakiSU patch-only path")
        if partition not in {"boot", "init_boot"}:
            raise VerificationError("SakiSU patch-only partition must be boot or init_boot")
        destination = Path(destination)
        if destination.exists() and not destination.is_file():
            raise VerificationError("SakiSU candidate destination must be a regular file")
        if self._paths_alias(destination, source.path) or self._paths_alias(
            destination,
            patcher.path,
        ):
            raise VerificationError("SakiSU candidate destination aliases an immutable input")
        validate_imported_patcher(patcher)
        self._verify_snapshot(source, label="source")
        validate_patch_authorization(authorization, source, partition=partition)
        validate_manual_kmi_selection(manual_kmi)

        token = self.token_factory()
        if re.fullmatch(r"[0-9a-f]{32}", token) is None:
            raise VerificationError("SakiSU device operation token is invalid")
        remote = f"/data/local/tmp/.nebumtk-sakisu-{token}"
        remote_patcher = f"{remote}/patcher"
        remote_source = f"{remote}/source.img"
        remote_output = f"{remote}/candidate.img"
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.pending"
        )
        if pending.exists():
            raise VerificationError("SakiSU candidate pending path collision")

        operation_error: Exception | None = None
        operation_traceback: TracebackType | None = None
        candidate: VerifiedFileSnapshot | None = None
        cleanup_error: Exception | None = None
        try:
            try:
                self._ensure_not_cancelled(cancelled)
                self.adb.run("shell", "mkdir", "--", remote, timeout=20, sensitive=True)
                self._ensure_not_cancelled(cancelled)
                self.adb.push(
                    patcher.path,
                    remote_patcher,
                    cancelled=cancelled,
                    sensitive=True,
                )
                self._ensure_not_cancelled(cancelled)
                self.adb.push(
                    source.path,
                    remote_source,
                    cancelled=cancelled,
                    sensitive=True,
                )
                self._ensure_not_cancelled(cancelled)
                self.adb.run(
                    "shell",
                    "chmod",
                    "0700",
                    remote_patcher,
                    timeout=20,
                    sensitive=True,
                )
                self._ensure_not_cancelled(cancelled)
                result = self.adb.run(
                    "shell",
                    "env",
                    f"TMPDIR={remote}",
                    remote_patcher,
                    "boot-patch",
                    "--boot",
                    remote_source,
                    "--out",
                    remote,
                    "--out-name",
                    "candidate.img",
                    "--partition",
                    partition,
                    "--kmi",
                    manual_kmi.kmi,
                    timeout=300,
                    check=False,
                    sensitive=True,
                    cancelled=cancelled,
                )
                if result.returncode != 0:
                    raise VerificationError("official SakiSU Android patcher returned nonzero")
                self._ensure_not_cancelled(cancelled)
                remote_size = self.adb.remote_file_size(remote_output, root=False)
                if remote_size <= 0:
                    raise VerificationError("official SakiSU Android patcher output is missing")
                if remote_size > source.size + MAX_PATCH_GROWTH:
                    raise VerificationError("official SakiSU Android patcher output is oversized")
                self._ensure_not_cancelled(cancelled)
                self.adb.pull(remote_output, pending, cancelled=cancelled)
                self._ensure_not_cancelled(cancelled)
                if not pending.is_file() or pending.stat().st_size != remote_size:
                    raise VerificationError("official SakiSU Android patcher pull size mismatch")
                verification = verify_patch_candidate(
                    source.path,
                    pending,
                    partition=partition,
                )
                if verification.output_size != remote_size:
                    raise VerificationError("official SakiSU Android patcher output size changed")
                candidate = VerifiedFileSnapshot(
                    pending,
                    verification.output_size,
                    verification.output_sha256,
                )
                validate_imported_patcher(patcher)
                self._verify_snapshot(source, label="source")
                validate_patch_authorization(authorization, source, partition=partition)
                validate_manual_kmi_selection(manual_kmi)
            except Exception as exc:
                operation_error = exc
                operation_traceback = exc.__traceback__
            finally:
                try:
                    cleanup = self.adb.run(
                        "shell",
                        "rm",
                        "-rf",
                        "--",
                        remote,
                        timeout=30,
                        check=False,
                        sensitive=True,
                    )
                    if cleanup.returncode != 0:
                        raise VerificationError("SakiSU device staging cleanup failed")
                except Exception:
                    cleanup_error = VerificationError("SakiSU device staging cleanup failed")

            if operation_error is not None:
                raise operation_error.with_traceback(operation_traceback)
            if cleanup_error is not None:
                raise cleanup_error
            if candidate is None:
                raise VerificationError("official SakiSU Android patcher produced no candidate")
            self._verify_snapshot(candidate, label="candidate")
            pending.replace(destination)
            return VerifiedFileSnapshot(destination, candidate.size, candidate.sha256)
        finally:
            pending.unlink(missing_ok=True)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
