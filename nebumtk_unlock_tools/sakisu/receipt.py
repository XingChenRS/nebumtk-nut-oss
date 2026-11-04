# SPDX-License-Identifier: AGPL-3.0-only
"""Typed, secret-free SakiSU patch-only receipt."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import VerificationError
from ..core.operation_files import VerifiedFileSnapshot
from .boot_image import BootClassification, verify_patch_candidate
from .importer import OFFICIAL_APK
from .host_importer import OFFICIAL_WINDOWS_HOST
from .kmi import ManualKmiSelection, validate_manual_kmi_selection
from .policy import PatchAuthorization, validate_patch_authorization


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _digest(value: str, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise VerificationError(f"invalid {label} SHA-256")
    return value


def _strict_object(
    value: object,
    *,
    label: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> dict[str, object]:
    if type(value) is not dict:
        raise VerificationError(f"SakiSU patch receipt {label} must be an object")
    result = value
    unknown = set(result) - required - optional
    if unknown:
        raise VerificationError(f"SakiSU patch receipt {label} has unknown fields")
    if required - set(result):
        raise VerificationError(f"SakiSU patch receipt {label} is missing required fields")
    return result


def _verify_snapshot(snapshot: VerifiedFileSnapshot, *, label: str) -> None:
    try:
        size = snapshot.path.stat().st_size
    except FileNotFoundError as exc:
        raise VerificationError(f"immutable {label} is missing") from exc
    digest = hashlib.sha256(snapshot.path.read_bytes()).hexdigest()
    if size != snapshot.size or digest != snapshot.sha256:
        raise VerificationError(f"immutable {label} changed")


@dataclass(frozen=True, slots=True)
class PatchReceipt:
    partition: str
    source_size: int
    source_sha256: str
    source_classification: str
    replace_confirmed: bool
    output_size: int
    output_sha256: str
    output_classification: str
    kmi: str
    manual_selection: bool
    rollback_size: int | None
    rollback_sha256: str | None
    tool_kind: str = "android_device"

    def __post_init__(self) -> None:
        if type(self.partition) is not str or self.partition not in {"boot", "init_boot"}:
            raise VerificationError("invalid SakiSU patch receipt partition")
        if self.tool_kind not in {"android_device", "windows_host"}:
            raise VerificationError("invalid SakiSU patch receipt tool kind")
        if type(self.source_classification) is not str or self.source_classification not in {
            "apparent_stock",
            "sakisu",
            "other_ksu",
            "unknown_modified",
        }:
            raise VerificationError("invalid SakiSU source classification")
        if (
            type(self.source_size) is not int
            or type(self.output_size) is not int
            or self.source_size <= 0
            or self.output_size <= 0
        ):
            raise VerificationError("invalid SakiSU patch receipt size")
        _digest(self.source_sha256, "source")
        _digest(self.output_sha256, "output")
        if self.source_sha256 == self.output_sha256:
            raise VerificationError("SakiSU patch receipt source and output hashes must be different")
        if (
            type(self.output_classification) is not str
            or self.output_classification != BootClassification.SAKISU.value
        ):
            raise VerificationError("invalid SakiSU output classification")
        if type(self.replace_confirmed) is not bool:
            raise VerificationError("invalid SakiSU patch receipt replace confirmation")
        if type(self.kmi) is not str or self.kmi not in OFFICIAL_APK.supported_kmis:
            raise VerificationError("invalid SakiSU patch receipt KMI")
        if type(self.manual_selection) is not bool or self.manual_selection is not True:
            raise VerificationError("SakiSU patch receipt requires a manual KMI selection")
        if (self.rollback_size is None) != (self.rollback_sha256 is None):
            raise VerificationError("invalid SakiSU patch receipt rollback identity")
        if self.rollback_size is not None:
            if type(self.rollback_size) is not int or self.rollback_size <= 0:
                raise VerificationError("invalid SakiSU patch receipt rollback size")
            if self.rollback_size != self.source_size:
                raise VerificationError("SakiSU rollback size does not match source")
            if _digest(self.rollback_sha256 or "", "rollback") != self.source_sha256:
                raise VerificationError("SakiSU rollback hash does not match source")
        if self.source_classification != "apparent_stock":
            if not self.replace_confirmed or self.rollback_sha256 is None:
                raise VerificationError("non-stock SakiSU receipt lacks replacement authorization")

    def to_dict(self) -> dict[str, object]:
        if self.tool_kind == "windows_host":
            tool = {
                "repository": OFFICIAL_WINDOWS_HOST.repository,
                "tag": OFFICIAL_WINDOWS_HOST.tag,
                "source_commit": OFFICIAL_WINDOWS_HOST.source_commit,
                "target": OFFICIAL_WINDOWS_HOST.target,
                "patcher_sha256": OFFICIAL_WINDOWS_HOST.sha256,
            }
        else:
            tool = {
                "repository": OFFICIAL_APK.repository,
                "tag": OFFICIAL_APK.tag,
                "apk_sha256": OFFICIAL_APK.apk_sha256,
                "patcher_sha256": OFFICIAL_APK.patcher_sha256,
            }
        receipt: dict[str, object] = {
            "schema_version": 1,
            "operation": "sakisu_patch_only",
            "partition": self.partition,
            "tool": tool,
            "kmi": {
                "value": self.kmi,
                "manual_selection": self.manual_selection,
            },
            "source": {
                "size": self.source_size,
                "sha256": self.source_sha256,
                "classification": self.source_classification,
                "replace_confirmed": self.replace_confirmed,
            },
            "output": {
                "size": self.output_size,
                "sha256": self.output_sha256,
                "classification": self.output_classification,
            },
        }
        if self.rollback_size is not None and self.rollback_sha256 is not None:
            receipt["rollback"] = {
                "size": self.rollback_size,
                "sha256": self.rollback_sha256,
            }
        return receipt

    @classmethod
    def from_dict(cls, value: object) -> PatchReceipt:
        top = _strict_object(
            value,
            label="root",
            required=frozenset(
                {"schema_version", "operation", "partition", "tool", "kmi", "source", "output"}
            ),
            optional=frozenset({"rollback"}),
        )
        if type(top["schema_version"]) is not int or top["schema_version"] != 1:
            raise VerificationError("invalid SakiSU patch receipt schema version")
        if top["operation"] != "sakisu_patch_only":
            raise VerificationError("invalid SakiSU patch receipt operation")
        if type(top["tool"]) is not dict:
            raise VerificationError("SakiSU patch receipt tool must be an object")
        android_tool = {
            "repository": OFFICIAL_APK.repository,
            "tag": OFFICIAL_APK.tag,
            "apk_sha256": OFFICIAL_APK.apk_sha256,
            "patcher_sha256": OFFICIAL_APK.patcher_sha256,
        }
        windows_tool = {
            "repository": OFFICIAL_WINDOWS_HOST.repository,
            "tag": OFFICIAL_WINDOWS_HOST.tag,
            "source_commit": OFFICIAL_WINDOWS_HOST.source_commit,
            "target": OFFICIAL_WINDOWS_HOST.target,
            "patcher_sha256": OFFICIAL_WINDOWS_HOST.sha256,
        }
        tool = top["tool"]
        if tool == android_tool:
            tool_kind = "android_device"
        elif tool == windows_tool:
            tool_kind = "windows_host"
        else:
            raise VerificationError("invalid SakiSU patch receipt tool identity")
        kmi = _strict_object(
            top["kmi"],
            label="KMI",
            required=frozenset({"value", "manual_selection"}),
        )
        source = _strict_object(
            top["source"],
            label="source",
            required=frozenset({"size", "sha256", "classification", "replace_confirmed"}),
        )
        output = _strict_object(
            top["output"],
            label="output",
            required=frozenset({"size", "sha256", "classification"}),
        )
        rollback = None
        if "rollback" in top:
            rollback = _strict_object(
                top["rollback"],
                label="rollback",
                required=frozenset({"size", "sha256"}),
            )
        return cls(
            partition=top["partition"],
            source_size=source["size"],
            source_sha256=source["sha256"],
            source_classification=source["classification"],
            replace_confirmed=source["replace_confirmed"],
            output_size=output["size"],
            output_sha256=output["sha256"],
            output_classification=output["classification"],
            kmi=kmi["value"],
            manual_selection=kmi["manual_selection"],
            rollback_size=None if rollback is None else rollback["size"],
            rollback_sha256=None if rollback is None else rollback["sha256"],
            tool_kind=tool_kind,
        )


def build_patch_receipt(
    *,
    authorization: PatchAuthorization,
    manual_kmi: ManualKmiSelection,
    output: VerifiedFileSnapshot,
    tool_kind: str = "android_device",
) -> PatchReceipt:
    if type(authorization) is not PatchAuthorization:
        raise VerificationError("typed patch authorization is required")
    source = authorization._source_snapshot
    validate_patch_authorization(
        authorization,
        source,
        partition=authorization.partition,
    )
    validate_manual_kmi_selection(manual_kmi)
    _verify_snapshot(output, label="output")
    candidate = verify_patch_candidate(
        source.path,
        output.path,
        partition=authorization.partition,
    )
    if (
        candidate.source_size != authorization.source_size
        or candidate.source_sha256 != authorization.source_sha256
        or candidate.output_size != output.size
        or candidate.output_sha256 != output.sha256
    ):
        raise VerificationError("SakiSU verified candidate identity changed")
    return PatchReceipt(
        partition=authorization.partition,
        source_size=authorization.source_size,
        source_sha256=authorization.source_sha256,
        source_classification=authorization.classification.value,
        replace_confirmed=authorization.replace_confirmed,
        output_size=candidate.output_size,
        output_sha256=candidate.output_sha256,
        output_classification=candidate.classification.value,
        kmi=manual_kmi.kmi,
        manual_selection=True,
        rollback_size=authorization.rollback_size,
        rollback_sha256=authorization.rollback_sha256,
        tool_kind=tool_kind,
    )


def load_patch_receipt(path: Path) -> PatchReceipt:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError("SakiSU patch receipt JSON is missing or invalid") from exc
    return PatchReceipt.from_dict(value)


def write_patch_receipt(path: Path, receipt: PatchReceipt) -> Path:
    if type(receipt) is not PatchReceipt:
        raise VerificationError("typed SakiSU patch receipt is required")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.pending")
    try:
        pending.write_text(
            json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pending.replace(path)
        return path
    except Exception:
        pending.unlink(missing_ok=True)
        raise
