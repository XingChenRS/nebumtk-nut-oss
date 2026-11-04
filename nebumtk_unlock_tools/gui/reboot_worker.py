# SPDX-License-Identifier: AGPL-3.0-only
"""Transport-aware background worker for explicit device reboot actions."""

from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from ..core.activity import ActivityEvent, ActivityKind, WaitTarget
from ..core.errors import FlowCancelledError, VerificationError


class RebootWorker(QThread):
    progress_changed = Signal(str)
    activity_event = Signal(object)
    result_ready = Signal(object)
    finished_error = Signal(str)
    cancelled_signal = Signal()

    def __init__(self, *, adb_binary: str | None, fastboot_binary: str | None, serial: str, source_mode: str, target: str, parent=None) -> None:
        super().__init__(parent)
        self.adb_binary = adb_binary
        self.fastboot_binary = fastboot_binary
        self.serial = serial
        self.source_mode = source_mode
        self.target = target
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            from ..core.adb import AdbClient
            from ..core.fastboot import FastbootClient

            adb = AdbClient(self.adb_binary, serial=self.serial) if self.adb_binary else None
            fastboot = FastbootClient(self.fastboot_binary, serial=self.serial) if self.fastboot_binary else None
            if self.source_mode == "adb":
                if adb is None or self.serial not in adb.devices():
                    raise VerificationError("ADB 未检测到当前设备")
                self.progress_changed.emit("正在发送重启请求…")
                self.activity_event.emit(ActivityEvent(
                    ActivityKind.RUNNING,
                    "正在发送重启请求",
                    serial=self.serial,
                ))
                adb.reboot({"system": "", "bootloader": "bootloader", "fastbootd": "fastboot", "recovery": "recovery"}[self.target])
            elif self.source_mode == "fastboot":
                if fastboot is None or self.serial not in fastboot.devices():
                    raise VerificationError("Fastboot 未检测到当前设备")
                current_mode = fastboot.fastboot_mode()
                if current_mode == "unknown":
                    raise VerificationError("无法确认当前 Fastboot 模式")
                if self.target == "bootloader" and current_mode == "bootloader":
                    raise VerificationError("设备已处于 Bootloader Fastboot")
                if self.target == "fastbootd" and current_mode == "fastbootd":
                    raise VerificationError("设备已处于 Fastbootd")
                if self.target == "system":
                    if adb is None:
                        raise VerificationError("重启到系统需要 ADB 用于状态验证")
                    self.progress_changed.emit("正在重启到系统…")
                    self.activity_event.emit(ActivityEvent(
                        ActivityKind.RUNNING,
                        "正在发送重启请求",
                        serial=self.serial,
                    ))
                    fastboot.reboot()
                elif self.target == "bootloader":
                    self.progress_changed.emit("正在重启到 Bootloader…")
                    self.activity_event.emit(ActivityEvent(
                        ActivityKind.RUNNING,
                        "正在发送重启请求",
                        serial=self.serial,
                    ))
                    fastboot.reboot_bootloader()
                elif self.target == "fastbootd":
                    self.progress_changed.emit("正在重启到 Fastbootd…")
                    self.activity_event.emit(ActivityEvent(
                        ActivityKind.RUNNING,
                        "正在发送重启请求",
                        serial=self.serial,
                    ))
                    fastboot.reboot_fastboot()
                else:
                    raise VerificationError("Recovery 仅支持从 ADB 请求重启")
            else:
                raise VerificationError("未知设备传输模式")

            if self.target == "recovery":
                self.result_ready.emit(("requested", "Recovery 请求已发送；该模式无法自动确认"))
                return
            self.progress_changed.emit("正在确认重启目标…")
            deadline = time.monotonic() + (120.0 if self.target in {"system", "fastbootd"} else 90.0)
            self.activity_event.emit(ActivityEvent(
                ActivityKind.WAITING,
                "正在确认重启目标",
                deadline=deadline,
                target=WaitTarget.REBOOT_TARGET,
                serial=self.serial,
                expected_mode=self.target,
            ))
            self._verify_target(adb, fastboot, deadline)
            self.activity_event.emit(ActivityEvent(
                ActivityKind.SUCCESS,
                "已确认重启目标",
                serial=self.serial,
                expected_mode=self.target,
            ))
            self.result_ready.emit(("verified", self.target))
        except FlowCancelledError:
            self.cancelled_signal.emit()
        except Exception as exc:
            self.finished_error.emit(str(exc))

    def _verify_target(self, adb, fastboot, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self._cancelled:
                raise FlowCancelledError("已停止等待重启目标确认")
            if self.target == "system":
                if adb is not None and self.serial in adb.devices() and adb.get_state() == "device":
                    return
            elif fastboot is not None and self.serial in fastboot.devices():
                mode = fastboot.fastboot_mode()
                expected = "fastbootd" if self.target == "fastbootd" else "bootloader"
                if mode == expected:
                    return
                if mode in {"fastbootd", "bootloader"}:
                    raise VerificationError(f"检测到 {mode}，并非请求的 {expected}")
            time.sleep(2)
        self.activity_event.emit(ActivityEvent(
            ActivityKind.TIMEOUT,
            "未能确认重启目标",
            target=WaitTarget.REBOOT_TARGET,
            serial=self.serial,
            expected_mode=self.target,
        ))
        raise VerificationError("重启请求已发送，但未能确认目标状态")
