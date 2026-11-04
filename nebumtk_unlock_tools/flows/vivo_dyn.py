# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Local Vivo dyn capture/restore service with no protected operation path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config.store import Settings
from ..core.adb import AdbClient
from ..core.backup_export import load_dyn_backup, publish_to_desktop
from ..core.device import ConnectedDevice, DeviceProfile
from ..core.dyn_backup import DynRestoreOutcome, capture_dyn_backup, restore_dyn_backup
from ..core.env import EnvChecker
from ..core.errors import FlowCancelledError, VerificationError
from ..core.safety import SafetyChecker
from ..core.su import probe_custom_su, validate_custom_su
from ..core.workspace import WorkspaceManager
from ..core.operation_files import VerifiedFileSnapshot
from .base import FlowCallbacks


@dataclass(frozen=True)
class DynCaptureRequest:
    device: ConnectedDevice
    profile: DeviceProfile


@dataclass(frozen=True)
class DynRestoreRequest:
    device: ConnectedDevice
    profile: DeviceProfile
    manifest: VerifiedFileSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, VerifiedFileSnapshot):
            raise TypeError("confirmed backup manifest snapshot is required")


class VivoDynFacade:
    """Typed dyn boundary with ordered local root-provider fallback."""

    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings: Settings,
        callbacks: FlowCallbacks | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.workspace = WorkspaceManager(Path(workspace_root))
        self.settings = settings
        self.callbacks = callbacks or FlowCallbacks()

    def capture(
        self,
        request: DynCaptureRequest,
        *,
        cancelled=None,
    ):
        cancelled = cancelled or self.callbacks.cancelled
        _ensure_not_cancelled(cancelled)
        adb, su_executable = self._prepare(
            request.device,
            request.profile,
            cancelled=cancelled,
        )
        _ensure_not_cancelled(cancelled)
        operation_files = self.workspace.operation_files("vivo-dyn-capture")
        try:
            backup = capture_dyn_backup(
                adb,
                operation_files.directory,
                cancelled=cancelled,
                log=self.callbacks.log,
            )
            _ensure_not_cancelled(cancelled)
            if backup is None:
                return None
            return publish_to_desktop(
                {},
                model_key=request.profile.key,
                serial=request.device.serial,
                product=request.device.device or request.device.model or "unknown",
                initial_slot=request.device.slot_suffix,
                dyn_backup=backup,
            )
        finally:
            operation_files.cleanup()

    def restore(
        self,
        request: DynRestoreRequest,
        *,
        cancelled=None,
    ) -> DynRestoreOutcome:
        cancelled = cancelled or self.callbacks.cancelled
        _ensure_not_cancelled(cancelled)
        adb, su_executable = self._prepare(
            request.device,
            request.profile,
            cancelled=cancelled,
        )
        _ensure_not_cancelled(cancelled)
        backup = load_dyn_backup(
            request.manifest.path.parent,
            profile_key=request.profile.key,
            adb_serial=request.device.serial,
            expected_manifest=request.manifest,
        )
        _ensure_not_cancelled(cancelled)
        return restore_dyn_backup(
            adb,
            backup,
            su_candidates=(su_executable,),
            cancelled=cancelled,
        )

    def _prepare(
        self,
        device: ConnectedDevice,
        profile: DeviceProfile,
        *,
        cancelled,
    ) -> tuple[AdbClient, str]:
        _ensure_not_cancelled(cancelled)
        serial = str(device.serial).strip()
        if not serial:
            raise VerificationError("Vivo dyn requires an exact ADB serial")
        candidates = self._su_candidates(profile)
        _ensure_not_cancelled(cancelled)
        tools = EnvChecker(self.settings).validate_adb()
        _ensure_not_cancelled(cancelled)
        adb = AdbClient(
            tools.adb,
            serial=serial,
            log=self.callbacks.log,
            su_path=candidates[0][1],
            cancelled=cancelled,
        )
        _ensure_not_cancelled(cancelled)
        devices = adb.devices()
        if devices != [serial]:
            raise VerificationError("Vivo dyn requires one exact connected ADB serial")
        _ensure_not_cancelled(cancelled)
        SafetyChecker.check_device_profile(
            device,
            profile,
            skip_identity=self.settings.test_mode_enabled,
        )
        for label, token in candidates:
            _ensure_not_cancelled(cancelled)
            try:
                probe = probe_custom_su(adb, token)
            except FlowCancelledError:
                raise
            except Exception:
                self.callbacks.log(f"{label} root provider unavailable")
                continue
            if probe.uid != 0:
                self.callbacks.log(f"{label} root provider unavailable")
                continue
            adb.su_path = token
            return adb, token
        raise VerificationError("Vivo dyn could not find a usable root provider")

    def _su_candidates(self, profile: DeviceProfile) -> tuple[tuple[str, str], ...]:
        candidates: list[tuple[str, str]] = []

        def append(label: str, raw: str) -> None:
            if not str(raw or "").strip():
                return
            token = validate_custom_su(raw)
            if token not in {existing for _name, existing in candidates}:
                candidates.append((label, token))

        append("profile", profile.su_path)
        append("global", "su")
        if self.settings.test_mode_enabled:
            try:
                append("emergency", self.settings.custom_su)
            except (TypeError, ValueError):
                self.callbacks.log("emergency root provider configuration ignored")
        return tuple(candidates)


def _ensure_not_cancelled(cancelled) -> None:
    if cancelled():
        raise FlowCancelledError("Vivo dyn operation cancelled")
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
