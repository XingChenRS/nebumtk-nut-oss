"""Background workers for device flows and utility actions."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..config.store import Settings
from ..core.adb import AdbClient
from ..core.device import ConnectedDevice, DeviceDetector
from ..core.errors import FlowCancelledError, NebumtkError, VerificationError
from ..core.env import EnvChecker
from ..core.fastboot import FastbootClient
from ..core.operation_files import VerifiedFileSnapshot
from ..core.safety import SafetyChecker
from ..core.su import probe_custom_su
from ..flows.base import FlowCallbacks
from ..flows.factory import FASTBOOT_FLOWS, LOCAL_FLOWS, create_flow


class FlowWorker(QThread):
    log_message = Signal(str)
    progress_changed = Signal(int, str)
    activity_event = Signal(object)
    finished_ok = Signal()
    finished_error = Signal(str)
    cancelled_signal = Signal()
    confirmation_requested = Signal(str)

    def __init__(
        self,
        project_root: Path,
        workspace_root: Path,
        settings: Settings,
        serial: str,
        model_key: str,
        flow_kind: str,
        patched_lk: Path | VerifiedFileSnapshot | str | None = None,
        source_lk: Path | VerifiedFileSnapshot | None = None,
        backup_directory: Path | None = None,
        preload_override: Path | str | None = None,
        skip_profile_identity: bool = False,
        runtime_model_key: str = "",
        confirmed_active_slot: str = "",
        local_lk_profile: str = "",
        install_sakisu_root: bool = False,
        sakisu_init_boot: Path | None = None,
        sakisu_kmi: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_root, self.workspace_root, self.settings = project_root, workspace_root, settings
        self.serial, self.model_key, self.flow_kind = serial, model_key, flow_kind
        self.patched_lk, self.source_lk, self.preload_override = patched_lk, source_lk, preload_override
        self.backup_directory = backup_directory
        self._confirmation_wait = threading.Event()
        self._confirmation_result = False
        self.skip_profile_identity = bool(skip_profile_identity)
        self.runtime_model_key = str(runtime_model_key).strip()
        self.confirmed_active_slot = str(confirmed_active_slot)
        self.local_lk_profile = str(local_lk_profile).strip()
        self.install_sakisu_root = bool(install_sakisu_root)
        self.sakisu_init_boot = Path(sakisu_init_boot) if sakisu_init_boot else None
        self.sakisu_kmi = str(sakisu_kmi).strip()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self._confirmation_result = False
        self._confirmation_wait.set()
    def _set_stage(self, percent: int, label: str) -> None: self.progress_changed.emit(percent, label)

    def _confirm_from_main_window(self, message: str) -> bool:
        if self._cancelled:
            return False
        self._confirmation_result = False
        self._confirmation_wait.clear()
        self.confirmation_requested.emit(message)
        while not self._confirmation_wait.wait(0.2):
            if self._cancelled:
                return False
        return bool(self._confirmation_result) and not self._cancelled

    def run(self) -> None:
        flow = None
        try:
            if self.skip_profile_identity:
                if not self.settings.test_mode_enabled:
                    raise VerificationError(
                        "测试兼容模式必须先在设置中显式开启"
                    )
            if self.flow_kind not in LOCAL_FLOWS and not self.serial:
                raise VerificationError("未指定设备序列号")
            if not self.model_key and self.flow_kind not in LOCAL_FLOWS:
                raise VerificationError("请先选择机型")
            if self.flow_kind in LOCAL_FLOWS:
                from ..flows.stages import LocalLkPatchRequest

                if self.source_lk is None or self.patched_lk is None:
                    raise VerificationError(
                        "local LK patch requires explicit source and output paths"
                    )
                callbacks = FlowCallbacks(
                    log=self.log_message.emit,
                    progress=lambda value, label: self.progress_changed.emit(value, label),
                    activity=self.activity_event.emit,
                    cancelled=lambda: self._cancelled,
                    confirmation=self._confirm_from_main_window,
                    skip_profile_identity=False,
                )
                flow = create_flow(
                    self.flow_kind,
                    self.project_root,
                    self.workspace_root,
                    self.settings,
                    callbacks,
                    patched_lk=self.patched_lk,
                    source_lk=self.source_lk,
                )
                flow.run_local(
                    LocalLkPatchRequest(
                        self.source_lk,
                        Path(self.patched_lk),
                        self.local_lk_profile or None,
                    )
                )
                self.finished_ok.emit()
                return

            self._set_stage(5, "等待设备…")
            detector = DeviceDetector(
                self.project_root,
                AdbClient(self.settings.try_resolved_adb() or "adb"),
            )
            profile = detector.get_profile_by_key(self.model_key)
            runtime_key = self.runtime_model_key or self.model_key
            if runtime_key != self.model_key and not self.skip_profile_identity:
                raise VerificationError("仅兼容模式允许组合不同机型运行配置")
            if runtime_key != self.model_key:
                runtime_profile = detector.get_profile_by_key(runtime_key)
                profile = profile.with_privilege_runtime(
                    runtime_profile.privilege_runtime
                )
            callbacks = FlowCallbacks(log=self.log_message.emit, progress=lambda value, label: self.progress_changed.emit(value, label), activity=self.activity_event.emit, cancelled=lambda: self._cancelled, confirmation=self._confirm_from_main_window, skip_profile_identity=self.skip_profile_identity)
            flow = create_flow(
                self.flow_kind,
                self.project_root,
                self.workspace_root,
                self.settings,
                callbacks,
                patched_lk=self.patched_lk,
                source_lk=self.source_lk,
                preload_override=self.preload_override,
                backup_directory=self.backup_directory,
                install_sakisu_root=self.install_sakisu_root,
                sakisu_init_boot=self.sakisu_init_boot,
                sakisu_kmi=self.sakisu_kmi,
            )
            device = self._device_for_fastboot(detector, profile) if self.flow_kind in FASTBOOT_FLOWS else self._device_for_adb(detector, profile)
            flow.run(device, profile)
            self.finished_ok.emit()
        except FlowCancelledError:
            self.log_message.emit("流程已被用户取消")
            self.cancelled_signal.emit()
        except NebumtkError as exc:
            self.finished_error.emit(str(exc))
        except (TimeoutError, ValueError, FileNotFoundError, OSError) as exc:
            self.finished_error.emit(str(exc))
        except Exception as exc:
            self.log_message.emit(f"流程异常: {type(exc).__name__}")
            self.finished_error.emit("流程异常，已安全终止")
        finally:
            ctx = getattr(flow, "ctx", None)
            if ctx is not None:
                try: ctx.cleanup_materialized_preloads()
                except Exception as exc: self.log_message.emit(f"清理临时文件失败: {exc}")

    def _device_for_adb(self, detector: DeviceDetector, profile) -> ConnectedDevice:
        from ..core.env import EnvChecker
        tools = EnvChecker(self.settings).validate_adb()
        adb = AdbClient(
            tools.adb,
            serial=self.serial,
            log=self.log_message.emit,
            cancelled=lambda: self._cancelled,
        )
        if self.serial not in adb.devices(): raise VerificationError("adb 未检测到该设备，请确认系统已启动且 USB 调试开启")
        self._set_stage(10, "检测到系统"); detector.adb = adb
        self._set_stage(15, "后台读取设备信息…")
        device = detector.read_connected(self.serial)
        if self.confirmed_active_slot:
            raw_slot = str(device.slot_suffix).strip().lower().lstrip("_")
            if raw_slot not in {"a", "b"}:
                raise VerificationError("fresh ADB active slot is invalid")
            live_slot = f"_{raw_slot}"
            if live_slot != self.confirmed_active_slot:
                raise VerificationError("live slot does not match confirmed active slot")
            device.slot_suffix = live_slot
        SafetyChecker.check_device_profile(device, profile, skip_identity=self.skip_profile_identity)
        device.profile = profile
        self._set_stage(18, "设备信息已确认")
        return device

    def _device_for_fastboot(self, detector: DeviceDetector, profile) -> ConnectedDevice:
        from ..core.env import EnvChecker
        tools = EnvChecker(self.settings).validate_fastboot()
        fastboot = FastbootClient(
            tools.fastboot,
            serial=self.serial,
            log=self.log_message.emit,
            cancelled=lambda: self._cancelled,
        )
        devices = fastboot.devices()
        if len(devices) != 1 or set(devices) != {self.serial}:
            raise VerificationError("fastboot 设备集合必须且只能包含当前 serial，请断开其它 Fastboot 设备并确认目标处于 bootloader 模式")
        mode = fastboot.fastboot_mode()
        if mode != "bootloader":
            raise VerificationError("Fastboot-only 流程要求 bootloader Fastboot（is-userspace=no），当前模式不安全")
        raw_slot = fastboot.current_slot().strip().lower().lstrip("_")
        if raw_slot not in {"a", "b"}:
            raise VerificationError(f"fastboot current-slot 非法: {raw_slot or '<empty>'}")
        live_slot = f"_{raw_slot}"
        if self.confirmed_active_slot and live_slot != self.confirmed_active_slot:
            raise VerificationError("live slot does not match confirmed active slot")
        self._set_stage(10, "检测到 Bootloader Fastboot")
        self._set_stage(15, "Fastboot serial/mode/slot 已确认")
        return ConnectedDevice(serial=self.serial, brand=profile.brand, model="", device="", firmware="", slot_suffix=live_slot, profile=profile)


class UtilityWorker(QThread):
    log_message = Signal(str); progress_changed = Signal(str); result_ready = Signal(object); finished_error = Signal(str); cancelled_signal = Signal()
    def __init__(self, operation: str, payload: dict[str, object], parent=None) -> None:
        super().__init__(parent); self.operation, self.payload, self._cancelled = operation, payload, False
    def cancel(self) -> None: self._cancelled = True
    def run(self) -> None:
        try:
            if self.operation == "inspect_lk":
                from ..lk.ubl_service import InternalUblPatcher
                source = Path(str(self.payload["source"]))
                patcher = InternalUblPatcher(
                    log=self.log_message.emit,
                    cancelled=lambda: self._cancelled,
                    image_profile=(
                        str(self.payload.get("image_profile", "")).strip() or None
                    ),
                )
                self.progress_changed.emit("检测外部 LK…"); self.result_ready.emit(("inspect_lk", patcher.inspect(source)))
                return
            from ..lk.xingc_service import XingcUnlockService
            serial = str(self.payload["serial"]); service = XingcUnlockService(log=self.log_message.emit, cancelled=lambda: self._cancelled)
            service.model_id = str(self.payload.get("model_id", ""))
            if self.operation == "xingc_token": self.progress_changed.emit("读取 Xingc Token…"); self.result_ready.emit(("xingc_token", serial, service.read_token(serial)))
            elif self.operation == "xingc_sign": self.progress_changed.emit("签名 Xingc Token…"); self.result_ready.emit(("xingc_sign", serial, service.build_payload(str(self.payload["token"]), target=serial)))
            elif self.operation == "xingc_auth": self.progress_changed.emit("发送 Xingc Auth…"); self.result_ready.emit(("xingc_auth", serial, service.authorize(serial, self.payload["payload"])))
            else: raise VerificationError(f"未知工具操作: {self.operation}")
        except FlowCancelledError: self.cancelled_signal.emit()
        except Exception as exc: self.finished_error.emit(str(exc))


class CustomSuProbeWorker(QThread):
    """Validate the locally stored custom provider with the fixed ``id`` probe."""

    result_ready = Signal(object)
    finished_error = Signal(str)
    cancelled_signal = Signal()

    def __init__(self, settings: Settings, serial: str, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.serial = str(serial).strip()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()

    def _is_cancelled(self) -> bool:
        return self._cancelled or self.isInterruptionRequested()

    def run(self) -> None:
        try:
            if self._is_cancelled():
                raise FlowCancelledError("custom su probe cancelled")
            if not self.serial:
                raise VerificationError("custom su probe requires an ADB serial")
            tools = EnvChecker(self.settings).validate_adb()
            if self._is_cancelled():
                raise FlowCancelledError("custom su probe cancelled")
            adb = AdbClient(
                tools.adb,
                serial=self.serial,
                cancelled=self._is_cancelled,
            )
            devices = adb.devices()
            if len(devices) != 1 or devices[0] != self.serial:
                raise VerificationError(
                    "custom su probe requires one unique ADB serial"
                )
            if self._is_cancelled():
                raise FlowCancelledError("custom su probe cancelled")
            result = probe_custom_su(adb, self.settings.custom_su)
            if result.uid != 0:
                raise VerificationError("custom su fixed id probe must return uid 0")
            if self._is_cancelled():
                raise FlowCancelledError("custom su probe cancelled")
            self.result_ready.emit(result)
        except FlowCancelledError:
            self.cancelled_signal.emit()
        except Exception as exc:
            self.finished_error.emit(str(exc))


class TerminalWorker(QThread):
    result_ready = Signal(object); finished_error = Signal(str); cancelled_signal = Signal()
    def __init__(self, command: list[str], parent=None) -> None:
        super().__init__(parent); self.command, self._cancelled = command, False
    def cancel(self) -> None: self._cancelled = True
    def run(self) -> None:
        from ..core.runner import run_command
        try: self.result_ready.emit(run_command(self.command, timeout=180, cancelled=lambda: self._cancelled))
        except FlowCancelledError: self.cancelled_signal.emit()
        except Exception as exc: self.finished_error.emit(str(exc))
