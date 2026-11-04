# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebumtk_unlock_tools.core.activity import ActivityKind, WaitTarget
from nebumtk_unlock_tools.gui.reboot_worker import RebootWorker


def test_reboot_worker_emits_waiting_and_confirmation_events(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = RebootWorker(
        adb_binary="adb",
        fastboot_binary="fastboot",
        serial="SERIAL",
        source_mode="adb",
        target="fastbootd",
    )
    received = []
    worker.activity_event.connect(received.append)
    monkeypatch.setattr(worker, "_verify_target", lambda *_args: None)

    class FakeAdb:
        def __init__(self, *_args, **_kwargs) -> None:
            self.reboot_requests: list[str] = []

        def devices(self) -> list[str]:
            return ["SERIAL"]

        def reboot(self, target: str) -> None:
            self.reboot_requests.append(target)

    monkeypatch.setattr("nebumtk_unlock_tools.core.adb.AdbClient", FakeAdb)

    worker.run()

    assert received[0].kind is ActivityKind.RUNNING
    assert any(event.kind is ActivityKind.WAITING for event in received)
    assert received[-1].kind is ActivityKind.SUCCESS
    assert received[-1].expected_mode == "fastbootd"


def test_reboot_worker_emits_timeout_only_after_natural_verify_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = RebootWorker(
        adb_binary=None,
        fastboot_binary="fastboot",
        serial="SERIAL",
        source_mode="fastboot",
        target="bootloader",
    )
    received = []
    errors = []
    worker.activity_event.connect(received.append)
    worker.finished_error.connect(errors.append)

    class FakeFastboot:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def devices(self) -> list[str]:
            return ["SERIAL"]

        def fastboot_mode(self) -> str:
            return "fastbootd"

        def reboot_bootloader(self) -> None:
            pass

    times = iter([100.0, 191.0])
    monkeypatch.setattr("nebumtk_unlock_tools.core.fastboot.FastbootClient", FakeFastboot)
    monkeypatch.setattr("nebumtk_unlock_tools.gui.reboot_worker.time.monotonic", lambda: next(times))
    monkeypatch.setattr("nebumtk_unlock_tools.gui.reboot_worker.time.sleep", lambda _seconds: None)

    worker.run()

    timeout_events = [event for event in received if event.kind is ActivityKind.TIMEOUT]
    assert len(timeout_events) == 1
    assert timeout_events[0].target is WaitTarget.REBOOT_TARGET
    assert timeout_events[0].expected_mode == "bootloader"
    assert errors == ["重启请求已发送，但未能确认目标状态"]
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
