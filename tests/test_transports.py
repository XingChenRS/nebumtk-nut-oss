# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import time
from pathlib import Path

import pytest

import nebumtk_unlock_tools.core.fastboot as fastboot_module
from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.activity import ActivityKind, WaitTarget
from nebumtk_unlock_tools.core.adb import AdbClient
from nebumtk_unlock_tools.core.errors import FlowCancelledError, VerificationError
from nebumtk_unlock_tools.core.fastboot import FastbootClient
from nebumtk_unlock_tools.core.runner import CommandResult
from nebumtk_unlock_tools.flows.base import FlowCallbacks, FlowContext


class RecordingAdb(AdbClient):
    def __init__(self, serial: str = "SERIAL") -> None:
        super().__init__(binary="adb", serial=serial)
        self.calls: list[tuple[str, ...]] = []
        self.device_sequences: list[list[str]] = []
        self.props: dict[str, list[str]] = {}
        self.shell_outputs: dict[str, list[CommandResult]] = {}

    def run(self, *args: str, **kwargs) -> CommandResult:  # type: ignore[override]
        self.calls.append(tuple(args))
        return CommandResult(0, "", "")

    def devices(self) -> list[str]:  # type: ignore[override]
        if self.device_sequences:
            return self.device_sequences.pop(0)
        return [self.serial] if self.serial else []

    def getprop(self, name: str) -> str:  # type: ignore[override]
        values = self.props.get(name)
        if values:
            return values.pop(0)
        return ""

    def shell(self, command: str, **kwargs) -> CommandResult:  # type: ignore[override]
        self.calls.append(("shell", command))
        values = self.shell_outputs.get(command)
        if values:
            return values.pop(0)
        return CommandResult(0, "", "")


class RecordingFastboot(FastbootClient):
    def __init__(self, serial: str = "SERIAL") -> None:
        super().__init__(binary="fastboot", serial=serial)
        self.calls: list[tuple[str, ...]] = []
        self.device_sequences: list[list[str]] = []
        self.mode_values: list[str] = []
        self.slot_values: list[str] = []
        self.getvar_values: dict[str, list[str]] = {}
        self.fail_flash_suffix: str | None = None
        self.reboots = 0

    def run(self, *args: str, **kwargs) -> CommandResult:  # type: ignore[override]
        self.calls.append(tuple(args))
        return CommandResult(0, "", "")

    def devices(self) -> list[str]:  # type: ignore[override]
        if self.device_sequences:
            return self.device_sequences.pop(0)
        return [self.serial] if self.serial else []

    def getvar(self, name: str, *, check: bool = False) -> str:  # type: ignore[override]
        values = self.getvar_values.get(name)
        if values:
            return values.pop(0)
        return ""

    def fastboot_mode(self) -> str:  # type: ignore[override]
        if self.mode_values:
            return self.mode_values.pop(0)
        return "fastbootd"

    def current_slot(self) -> str:  # type: ignore[override]
        if self.slot_values:
            return self.slot_values.pop(0)
        return "a"

    def flash_lk(self, slot_suffix: str, image: Path) -> None:  # type: ignore[override]
        self.calls.append(("flash_lk", slot_suffix, str(image)))
        if self.fail_flash_suffix == slot_suffix:
            from nebumtk_unlock_tools.core.errors import FastbootError
            raise FastbootError(f"flash failed {slot_suffix}")

    def reboot(self) -> None:  # type: ignore[override]
        self.reboots += 1
        self.calls.append(("reboot",))


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def make_context(tmp_path: Path, adb: RecordingAdb, fastboot: RecordingFastboot, *, cancelled=lambda: False) -> FlowContext:
    context = FlowContext(
        project_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        settings=Settings(),
        callbacks=FlowCallbacks(cancelled=cancelled),
    )
    context.adb = adb
    context.fastboot = fastboot
    return context



@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4096", 4096),
        ("4194304", 4194304),
        ("1000000", 16777216),
        ("a00000", 10485760),
        ("0x1000", 4096),
        ("0X10", 16),
    ],
)
def test_fastboot_partition_size_disambiguates_decimal_and_prefixless_hex(raw: str, expected: int) -> None:
    fastboot = RecordingFastboot("SERIAL")
    fastboot.getvar_values["partition-size:lk_a"] = [raw]

    assert fastboot.partition_size("lk_a") == expected


def test_fastboot_partition_size_rejects_ambiguous_aligned_digits() -> None:
    fastboot = RecordingFastboot("SERIAL")
    fastboot.getvar_values["partition-size:lk_a"] = ["4096000"]

    with pytest.raises(VerificationError, match="partition-size"):
        fastboot.partition_size("lk_a")


def test_fastboot_partition_size_parses_colon_variable_getvar_output_decimal_and_hex(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = {
        "partition-size:lk_a": CommandResult(0, "", "(bootloader) partition-size:lk_a: 0x400000\n"),
        "partition-size:lk_b": CommandResult(0, "partition-size:lk_b: 4194304\n", ""),
    }
    commands: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        commands.append(list(args))
        return outputs[list(args)[-1]]

    monkeypatch.setattr(fastboot_module, "run_command", fake_run_command)
    fastboot = FastbootClient(binary="fastboot", serial="SERIAL")

    assert fastboot.partition_size("lk_a") == 4194304
    assert fastboot.partition_size("lk_b") == 4194304
    assert commands == [
        ["fastboot", "-s", "SERIAL", "getvar", "partition-size:lk_a"],
        ["fastboot", "-s", "SERIAL", "getvar", "partition-size:lk_b"],
    ]


@pytest.mark.parametrize("raw", ["", "0", "-1", "4096 bytes", "4096\n8192", "0x", "0x0", "xyz"])
def test_fastboot_partition_size_rejects_malformed_zero_negative_or_unsafe(raw: str) -> None:
    fastboot = RecordingFastboot("SERIAL")
    fastboot.getvar_values["partition-size:lk_a"] = [raw]

    with pytest.raises(VerificationError):
        fastboot.partition_size("lk_a")

    with pytest.raises(VerificationError):
        fastboot.partition_size("lk_a;reboot")


def test_flash_partition_rejects_unsafe_label_or_missing_image_and_uses_one_flash_command(
    tmp_path: Path,
) -> None:
    image = tmp_path / "patched_init_boot.img"
    image.write_bytes(b"ANDROID!image")
    fastboot = RecordingFastboot("SERIAL")

    fastboot.flash_partition("init_boot_a", image)

    assert fastboot.calls == [("flash", "init_boot_a", str(image))]
    with pytest.raises(Exception, match="partition"):
        fastboot.flash_partition("init_boot_a;reboot", image)
    with pytest.raises(Exception, match="不存在|missing"):
        fastboot.flash_partition("init_boot_a", tmp_path / "missing.img")


def test_flash_dual_lk_invokes_guard_in_slot_order_and_reports_partial_failure_without_reboot(tmp_path: Path) -> None:
    image = tmp_path / "lk.img"
    image.write_bytes(b"lk")
    fastboot = RecordingFastboot("SERIAL")
    guard_order: list[str] = []

    fastboot.flash_dual_lk(image, before_each=guard_order.append)

    assert guard_order == ["_a", "_b"]
    assert fastboot.calls == [("flash_lk", "_a", str(image)), ("flash_lk", "_b", str(image))]
    assert fastboot.reboots == 0

    failing = RecordingFastboot("SERIAL")
    failing.fail_flash_suffix = "_b"
    second_guard_order: list[str] = []
    with pytest.raises(Exception, match="_b|lk_b|已成功槽"):
        failing.flash_dual_lk(image, before_each=second_guard_order.append)

    assert second_guard_order == ["_a", "_b"]
    assert failing.calls == [("flash_lk", "_a", str(image)), ("flash_lk", "_b", str(image))]
    assert failing.reboots == 0


def test_fastboot_reboot_checked_passes_full_serial_argv_and_propagates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        commands.append(list(args))
        if list(args)[-1:] == ["reboot"]:
            return CommandResult(1, "", "reboot failed")
        return CommandResult(0, "", "")

    monkeypatch.setattr(fastboot_module, "run_command", fake_run_command)

    with pytest.raises(Exception, match="reboot failed"):
        FastbootClient(binary="fastboot", serial="SERIAL").reboot_checked()

    assert commands == [["fastboot", "-s", "SERIAL", "reboot"]]


def test_fastboot_reboot_checked_propagates_transport_exception() -> None:
    class RebootFailingFastboot(RecordingFastboot):
        def run(self, *args: str, **kwargs) -> CommandResult:  # type: ignore[override]
            self.calls.append(tuple(args))
            if args == ("reboot",):
                raise RuntimeError("reboot failed")
            return CommandResult(0, "", "")

    fastboot = RebootFailingFastboot("SERIAL")

    with pytest.raises(RuntimeError, match="reboot failed"):
        fastboot.reboot_checked()

    assert fastboot.calls == [("reboot",)]


def test_wait_fastboot_emits_start_tick_and_confirmation_events(tmp_path: Path) -> None:
    events = []
    adb = RecordingAdb("SERIAL")
    fastboot = RecordingFastboot("SERIAL")
    fastboot.device_sequences = [[], ["SERIAL"]]
    context = FlowContext(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        FlowCallbacks(activity=events.append),
    )
    context.adb = adb
    context.fastboot = fastboot

    context.wait_fastboot(timeout=3)

    assert events[0].kind is ActivityKind.WAITING
    assert events[0].target is WaitTarget.FASTBOOT
    assert events[0].serial == "SERIAL"
    assert events[0].deadline is not None
    assert any(event.kind is ActivityKind.SUCCESS for event in events)


def test_wait_android_ready_defaults_to_fifteen_minutes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = FlowContext(tmp_path, tmp_path / "workspace", Settings())
    captured = []
    monkeypatch.setattr(
        context,
        "_begin_wait",
        lambda *args, **kwargs: captured.append(kwargs["timeout"]) or time.monotonic() + kwargs["timeout"],
    )
    monkeypatch.setattr(context, "_finish_wait", lambda *args, **kwargs: None)
    context.adb = RecordingAdb("SERIAL")
    context.adb.props["sys.boot_completed"] = ["1"]
    context.adb.shell_outputs["cmd package path android"] = [
        CommandResult(0, "package:/system/framework/framework-res.apk\n", "")
    ]
    context.adb.shell_outputs["test -d /sdcard && ls /sdcard >/dev/null 2>&1"] = [
        CommandResult(0, "", "")
    ]

    context.wait_android_ready()

    assert captured == [900.0]


def test_wait_fastboot_timeout_emits_timeout_before_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    adb = RecordingAdb("SERIAL")
    fastboot = RecordingFastboot("SERIAL")
    fastboot.device_sequences = [[]]
    context = FlowContext(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        FlowCallbacks(activity=events.append),
    )
    context.adb = adb
    context.fastboot = fastboot
    monkeypatch.setattr(fastboot, "wait_for_device", lambda **_kwargs: (_ for _ in ()).throw(VerificationError("not found")))

    with pytest.raises(VerificationError, match="等待 fastboot 超时"):
        context.wait_fastboot(timeout=0.01)

    assert events[-1].kind is ActivityKind.TIMEOUT
    assert events[-1].target is WaitTarget.FASTBOOT


def test_wait_fastboot_cancellation_never_emits_timeout(tmp_path: Path) -> None:
    events = []
    adb = RecordingAdb("SERIAL")
    fastboot = RecordingFastboot("SERIAL")
    context = FlowContext(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        FlowCallbacks(activity=events.append, cancelled=lambda: True),
    )
    context.adb = adb
    context.fastboot = fastboot

    with pytest.raises(FlowCancelledError):
        context.wait_fastboot(timeout=5)

    assert all(event.kind is not ActivityKind.TIMEOUT for event in events)


def test_wait_android_ready_makes_exact_five_second_cancellable_settle(tmp_path: Path) -> None:
    adb = RecordingAdb("SERIAL")
    adb.props["sys.boot_completed"] = ["1"]
    adb.shell_outputs["cmd package path android"] = [CommandResult(0, "package:/system/framework/framework-res.apk\n", "")]
    adb.shell_outputs["test -d /sdcard && ls /sdcard >/dev/null 2>&1"] = [
        CommandResult(0, "", "")
    ]
    fastboot = RecordingFastboot("SERIAL")
    context = make_context(tmp_path, adb, fastboot)
    sleeps: list[float] = []

    def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    context.cancellable_sleep = record_sleep  # type: ignore[method-assign]

    context.wait_android_ready(timeout=30)

    assert sleeps == [5.0]






def test_wait_android_ready_requires_same_serial_boot_completed_package_manager_sdcard_and_settle(tmp_path: Path) -> None:
    adb = RecordingAdb("SERIAL")
    adb.device_sequences = [[], ["OTHER"], ["SERIAL"], ["SERIAL"], ["SERIAL"]]
    adb.props["sys.boot_completed"] = ["0", "1", "1"]
    adb.shell_outputs["cmd package path android"] = [CommandResult(1, "", ""), CommandResult(0, "package:/system/framework/framework-res.apk\n", "")]
    adb.shell_outputs["test -d /sdcard && ls /sdcard >/dev/null 2>&1"] = [
        CommandResult(1, "", ""),
        CommandResult(0, "", ""),
    ]
    fastboot = RecordingFastboot("SERIAL")
    context = make_context(tmp_path, adb, fastboot)

    context.wait_android_ready(timeout=30)

    assert ("shell", "cmd package path android") in adb.calls
    assert ("shell", "test -d /sdcard && ls /sdcard >/dev/null 2>&1") in adb.calls


def test_wait_android_ready_rejects_timeout_and_cancellation(tmp_path: Path) -> None:
    adb = RecordingAdb("SERIAL")
    adb.device_sequences = [[]] * 20
    fastboot = RecordingFastboot("SERIAL")
    context = make_context(tmp_path, adb, fastboot)

    with pytest.raises(VerificationError):
        context.wait_android_ready(timeout=0.01)

    cancelled_context = make_context(tmp_path, adb, fastboot, cancelled=lambda: True)
    with pytest.raises(FlowCancelledError):
        cancelled_context.wait_android_ready(timeout=5)
