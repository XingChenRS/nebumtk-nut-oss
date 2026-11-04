# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Single-flight background device discovery for the main window."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..config.store import Settings
from ..core.adb import AdbClient
from ..core.device import DeviceDetector
from ..core.env import EnvChecker
from ..core.errors import DeviceMatchError
from ..core.fastboot import FastbootClient


@dataclass(frozen=True)
class DeviceScanResult:
    """A device snapshot produced without touching GUI state."""

    serial: str = ""
    mode: str = ""
    fastboot_mode: str = ""
    profile_key: str = ""
    brand: str = ""
    model: str = ""
    market_name: str = ""
    device: str = ""
    firmware: str = ""
    slot_suffix: str = ""
    environment_ready: bool = True
    connection_state_known: bool = True
    profile_error: str = ""
    scan_error: str = ""


class DeviceScanWorker(QThread):
    """Discover one connected device, preferring ADB over Fastboot."""

    scan_completed = Signal(object)

    def __init__(
        self,
        project_root: Path,
        settings: Settings,
        *,
        generation: int = 0,
        automatic: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.settings = settings
        self.generation = generation
        self.automatic = automatic

    def run(self) -> None:
        try:
            tools = EnvChecker(self.settings).resolve_or_none()
            if tools is None:
                self.scan_completed.emit(
                    DeviceScanResult(
                        environment_ready=False,
                        connection_state_known=False,
                    )
                )
                return

            adb_client: AdbClient | None = None
            adb_devices: list[str] = []
            adb_scan_error = ""
            adb_state_known = bool(tools.adb)
            if tools.adb:
                try:
                    adb_client = AdbClient(tools.adb)
                    adb_devices = adb_client.devices()
                except Exception as exc:
                    adb_devices = []
                    adb_scan_error = str(exc)
                    adb_state_known = False

            if adb_devices:
                self._scan_adb(adb_client, adb_devices[0])
                return

            fastboot_devices: list[str] = []
            fastboot_scan_error = ""
            if tools.fastboot:
                try:
                    fastboot_devices = FastbootClient(tools.fastboot).devices()
                except Exception as exc:
                    fastboot_devices = []
                    fastboot_scan_error = str(exc)
            if fastboot_devices:
                serial = fastboot_devices[0]
                try:
                    fastboot_mode = FastbootClient(tools.fastboot, serial=serial).fastboot_mode()
                except Exception:
                    fastboot_mode = "unknown"
                self.scan_completed.emit(
                    DeviceScanResult(serial=serial, mode="fastboot", fastboot_mode=fastboot_mode)
                )
            elif not adb_state_known:
                self.scan_completed.emit(
                    DeviceScanResult(
                        connection_state_known=False,
                        scan_error=adb_scan_error or "ADB 连接状态未知",
                    )
                )
            else:
                self.scan_completed.emit(DeviceScanResult(scan_error=fastboot_scan_error))
        except Exception as exc:
            self.scan_completed.emit(
                DeviceScanResult(
                    connection_state_known=False,
                    scan_error=str(exc),
                )
            )

    def _scan_adb(self, adb_client: AdbClient | None, serial: str) -> None:
        if adb_client is None:
            self.scan_completed.emit(DeviceScanResult(serial=serial, mode="adb"))
            return
        try:
            detector = DeviceDetector(self.project_root, adb_client)
            device = detector.read_connected(serial)
            profile = detector.match_profile(device)
            self.scan_completed.emit(
                DeviceScanResult(
                    serial=serial,
                    mode="adb",
                    profile_key=profile.key,
                    brand=device.brand,
                    model=device.model,
                    market_name=device.market_name,
                    device=device.device,
                    firmware=device.firmware,
                    slot_suffix=device.slot_suffix,
                )
            )
        except DeviceMatchError as exc:
            self.scan_completed.emit(
                DeviceScanResult(
                    serial=serial,
                    mode="adb",
                    brand=device.brand,
                    model=device.model,
                    market_name=device.market_name,
                    device=device.device,
                    firmware=device.firmware,
                    slot_suffix=device.slot_suffix,
                    profile_error=str(exc),
                )
            )
        except Exception as exc:
            self.scan_completed.emit(
                DeviceScanResult(serial=serial, mode="adb", scan_error=str(exc))
            )
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
