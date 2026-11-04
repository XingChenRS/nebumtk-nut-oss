"""Explicit manual KMI selection for the official SakiSU patcher."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import VerificationError
from .importer import OFFICIAL_APK


@dataclass(frozen=True, slots=True)
class ManualKmiSelection:
    """One operator-selected official KMI with explicit boot-risk acceptance."""

    kmi: str
    wrong_kmi_boot_risk_confirmed: bool


def validate_manual_kmi_selection(selection: ManualKmiSelection) -> None:
    if type(selection) is not ManualKmiSelection:
        raise VerificationError("typed manual KMI selection is required")
    if selection.kmi not in OFFICIAL_APK.supported_kmis:
        raise VerificationError("manual KMI is outside the official patcher allowlist")
    if selection.wrong_kmi_boot_risk_confirmed is not True:
        raise VerificationError(
            "manual KMI selection requires explicit confirmation that a wrong KMI may prevent boot"
        )
