# SPDX-License-Identifier: AGPL-3.0-only
"""Typed source authorization for SakiSU patch-only candidate generation."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import VerificationError
from ..core.operation_files import VerifiedFileSnapshot
from .boot_image import BootClassification, classify_boot_image


@dataclass(frozen=True, slots=True)
class PatchAuthorization:
    partition: str
    classification: BootClassification
    source_size: int
    source_sha256: str
    replace_confirmed: bool
    rollback_size: int | None
    rollback_sha256: str | None
    _source_snapshot: VerifiedFileSnapshot = field(repr=False, compare=False)
    _rollback_snapshot: VerifiedFileSnapshot | None = field(repr=False, compare=False)


def _verify_snapshot(snapshot: VerifiedFileSnapshot, *, label: str) -> None:
    try:
        size = snapshot.path.stat().st_size
    except FileNotFoundError as exc:
        raise VerificationError(f"immutable {label} is missing") from exc
    digest = hashlib.sha256(snapshot.path.read_bytes()).hexdigest()
    if size != snapshot.size or digest != snapshot.sha256:
        raise VerificationError(f"immutable {label} changed")


def _paths_alias(left: Path, right: Path) -> bool:
    left = Path(left)
    right = Path(right)
    if os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    ):
        return True
    try:
        return left.samefile(right)
    except (FileNotFoundError, OSError):
        return False


def authorize_patch_source(
    source: VerifiedFileSnapshot,
    *,
    partition: str,
    replace_confirmed: bool,
    rollback: VerifiedFileSnapshot | None = None,
) -> PatchAuthorization:
    _verify_snapshot(source, label="source")
    classification = classify_boot_image(source.path, partition=partition)
    normalized = str(partition).strip().lower()
    rollback_size: int | None = None
    rollback_sha256: str | None = None
    if classification is not BootClassification.APPARENT_STOCK:
        if replace_confirmed is not True:
            raise VerificationError("non-stock SakiSU replacement requires explicit confirmation")
        if rollback is None:
            raise VerificationError("non-stock SakiSU replacement requires a preserved rollback snapshot")
        _verify_snapshot(rollback, label="rollback")
        if _paths_alias(source.path, rollback.path):
            raise VerificationError("rollback snapshot must be preserved at a distinct path")
        if rollback.size != source.size or rollback.sha256 != source.sha256:
            raise VerificationError("rollback snapshot does not match the immutable source")
        rollback_size = rollback.size
        rollback_sha256 = rollback.sha256
    elif rollback is not None:
        _verify_snapshot(rollback, label="rollback")
        if rollback.size != source.size or rollback.sha256 != source.sha256:
            raise VerificationError("rollback snapshot does not match the immutable source")
        rollback_size = rollback.size
        rollback_sha256 = rollback.sha256
    return PatchAuthorization(
        partition=normalized,
        classification=classification,
        source_size=source.size,
        source_sha256=source.sha256,
        replace_confirmed=replace_confirmed is True,
        rollback_size=rollback_size,
        rollback_sha256=rollback_sha256,
        _source_snapshot=source,
        _rollback_snapshot=rollback,
    )


def validate_patch_authorization(
    authorization: PatchAuthorization,
    source: VerifiedFileSnapshot,
    *,
    partition: str,
) -> None:
    if type(authorization) is not PatchAuthorization:
        raise VerificationError("typed patch authorization is required")
    _verify_snapshot(source, label="source")
    normalized = str(partition).strip().lower()
    if (
        authorization.partition != normalized
        or authorization.source_size != source.size
        or authorization.source_sha256 != source.sha256
    ):
        raise VerificationError("patch authorization is not bound to the immutable source")
    actual = classify_boot_image(source.path, partition=normalized)
    if actual is not authorization.classification:
        raise VerificationError("patch authorization source classification changed")
    if actual is not BootClassification.APPARENT_STOCK:
        if not authorization.replace_confirmed or authorization._rollback_snapshot is None:
            raise VerificationError("non-stock patch authorization is incomplete")
        rollback = authorization._rollback_snapshot
        _verify_snapshot(rollback, label="rollback")
        if _paths_alias(source.path, rollback.path):
            raise VerificationError("rollback snapshot must remain at a distinct path")
        if (
            authorization.rollback_size != source.size
            or authorization.rollback_sha256 != source.sha256
            or rollback.size != source.size
            or rollback.sha256 != source.sha256
        ):
            raise VerificationError("rollback snapshot does not match the immutable source")
