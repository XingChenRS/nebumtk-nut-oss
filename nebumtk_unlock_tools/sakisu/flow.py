# SPDX-License-Identifier: AGPL-3.0-only
"""Typed orchestration for one official SakiSU patch-only operation."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.adb import AdbClient
from ..core.errors import FlowCancelledError, VerificationError
from ..core.operation_files import OperationFiles, VerifiedFileSnapshot
from .boot_image import BootClassification, classify_boot_image
from .device_patcher import AndroidDevicePatcher
from .host_importer import load_imported_host_patcher
from .host_patcher import WindowsHostPatcher
from .importer import load_imported_patcher
from .kmi import ManualKmiSelection, validate_manual_kmi_selection
from .policy import authorize_patch_source
from .receipt import PatchReceipt, build_patch_receipt, write_patch_receipt


@dataclass(frozen=True, slots=True)
class SakisuPatchRequest:
    source: VerifiedFileSnapshot
    partition: str
    kmi: str
    wrong_kmi_boot_risk_confirmed: bool
    replace_non_stock_confirmed: bool
    rollback: Path
    candidate: Path
    receipt: Path

    def __post_init__(self) -> None:
        if not isinstance(self.source, VerifiedFileSnapshot):
            raise TypeError("confirmed source snapshot is required")


@dataclass(frozen=True, slots=True)
class SakisuPatchResult:
    source_classification: BootClassification
    rollback: VerifiedFileSnapshot
    candidate: VerifiedFileSnapshot
    receipt_path: Path
    receipt: PatchReceipt


def _paths_alias(left: Path, right: Path) -> bool:
    if os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    ):
        return True
    try:
        return left.samefile(right)
    except (FileNotFoundError, OSError):
        return False


def _validate_distinct_paths(source: Path, *outputs: Path) -> None:
    paths = (source, *outputs)
    for index, path in enumerate(paths):
        for other in paths[index + 1 :]:
            if _paths_alias(path, other):
                raise VerificationError("SakiSU patch-only input and output paths must be distinct")


def _preserve_rollback(
    source: VerifiedFileSnapshot,
    destination: Path,
) -> VerifiedFileSnapshot:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    created = False
    try:
        with source.path.open("rb") as input_stream, destination.open("xb") as output_stream:
            created = True
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                output_stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        snapshot = VerifiedFileSnapshot(destination, size, digest.hexdigest())
        if snapshot.size != source.size or snapshot.sha256 != source.sha256:
            raise VerificationError("SakiSU rollback preservation verification failed")
        return snapshot
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


class SakisuPatchFlow:
    """Freeze, authorize, patch, verify and receipt one selected local image."""

    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        adb: AdbClient | None = None,
        *,
        token_factory: Callable[[], str] | None = None,
        execution: str = "device",
        host_runner: Callable[..., object] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.workspace_root = Path(workspace_root)
        self.adb = adb
        self.token_factory = token_factory
        if execution not in {"device", "host"}:
            raise ValueError("SakiSU execution backend must be device or host")
        self.execution = execution
        self.host_runner = host_runner

    def run(
        self,
        request: SakisuPatchRequest,
        *,
        cancelled: Callable[[], bool],
    ) -> SakisuPatchResult:
        if type(request) is not SakisuPatchRequest:
            raise VerificationError("typed SakiSU patch request is required")
        if cancelled():
            raise FlowCancelledError("SakiSU patch-only operation cancelled")
        source = request.source.path
        rollback_path = Path(request.rollback)
        candidate_path = Path(request.candidate)
        receipt_path = Path(request.receipt)
        _validate_distinct_paths(source, rollback_path, candidate_path, receipt_path)
        if candidate_path.exists() or receipt_path.exists():
            raise VerificationError("SakiSU candidate and receipt outputs must not already exist")
        selection = ManualKmiSelection(
            kmi=request.kmi,
            wrong_kmi_boot_risk_confirmed=request.wrong_kmi_boot_risk_confirmed,
        )
        validate_manual_kmi_selection(selection)

        operation = OperationFiles(self.workspace_root, "sakisu-patch")
        candidate_published = False
        try:
            frozen_source = operation.copy_verified_input(
                request.source, "selected-source.img"
            )
            classification = classify_boot_image(
                frozen_source.path,
                partition=request.partition,
            )
            if (
                classification is not BootClassification.APPARENT_STOCK
                and request.replace_non_stock_confirmed is not True
            ):
                raise VerificationError(
                    "non-stock SakiSU replacement requires explicit confirmation"
                )
            rollback = _preserve_rollback(frozen_source, rollback_path)
            authorization = authorize_patch_source(
                frozen_source,
                partition=request.partition,
                replace_confirmed=request.replace_non_stock_confirmed,
                rollback=rollback,
            )
            if self.execution == "host":
                patcher, _manifest = load_imported_host_patcher(self.project_root)
                patch_engine = WindowsHostPatcher(
                    self.workspace_root,
                    **(
                        {}
                        if self.host_runner is None
                        else {"runner": self.host_runner}
                    ),
                )
                tool_kind = "windows_host"
            else:
                if self.adb is None:
                    raise VerificationError(
                        "SakiSU device patch backend requires an ADB client"
                    )
                patcher, _manifest = load_imported_patcher(self.project_root)
                patch_engine = AndroidDevicePatcher(
                    self.adb,
                    **(
                        {}
                        if self.token_factory is None
                        else {"token_factory": self.token_factory}
                    ),
                )
                tool_kind = "android_device"
            candidate = patch_engine.patch(
                patcher,
                frozen_source,
                authorization=authorization,
                manual_kmi=selection,
                partition=request.partition,
                destination=candidate_path,
                cancelled=cancelled,
            )
            candidate_published = True
            if cancelled():
                raise FlowCancelledError("SakiSU patch-only operation cancelled")
            receipt = build_patch_receipt(
                authorization=authorization,
                manual_kmi=selection,
                output=candidate,
                tool_kind=tool_kind,
            )
            if cancelled():
                raise FlowCancelledError("SakiSU patch-only operation cancelled")
            write_patch_receipt(receipt_path, receipt)
            return SakisuPatchResult(
                source_classification=classification,
                rollback=rollback,
                candidate=candidate,
                receipt_path=receipt_path,
                receipt=receipt,
            )
        except Exception:
            if candidate_published:
                candidate_path.unlink(missing_ok=True)
            receipt_path.unlink(missing_ok=True)
            raise
        finally:
            operation.cleanup()
