# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Official SakiSU 4.3 patch-only support."""

from .boot_image import (
    BootClassification,
    VerifiedPatchCandidate,
    classify_boot_image,
    verify_patch_candidate,
)
from .device_patcher import AndroidDevicePatcher
from .host_importer import (
    OFFICIAL_WINDOWS_HOST,
    load_imported_host_patcher,
    validate_imported_host_patcher,
)
from .host_patcher import WindowsHostPatcher
from .flow import SakisuPatchFlow, SakisuPatchRequest, SakisuPatchResult
from .importer import OFFICIAL_APK, load_imported_patcher
from .kmi import ManualKmiSelection
from .policy import PatchAuthorization, authorize_patch_source
from .receipt import PatchReceipt, build_patch_receipt, load_patch_receipt, write_patch_receipt

__all__ = [
    "AndroidDevicePatcher",
    "BootClassification",
    "ManualKmiSelection",
    "OFFICIAL_APK",
    "OFFICIAL_WINDOWS_HOST",
    "PatchAuthorization",
    "PatchReceipt",
    "SakisuPatchFlow",
    "SakisuPatchRequest",
    "SakisuPatchResult",
    "VerifiedPatchCandidate",
    "WindowsHostPatcher",
    "authorize_patch_source",
    "build_patch_receipt",
    "classify_boot_image",
    "load_imported_patcher",
    "load_imported_host_patcher",
    "validate_imported_host_patcher",
    "load_patch_receipt",
    "verify_patch_candidate",
    "write_patch_receipt",
]
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
