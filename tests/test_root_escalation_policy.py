# SPDX-License-Identifier: AGPL-3.0-only
"""Escalation policy: what a failed root check is allowed to conclude.

`su -c id` fails in more than one way, and they call for different responses.  A
denial means the exploit did not take effect.  A dead daemon — the tool's own su
is on the device but nothing is listening — means the preload has to run again,
and it is the state the device is in after *every* reboot, including the one the
exploit itself causes.

Treating both as one boolean is what produced this log line on a real device,
three times in a row, each followed by a full device reboot that could not
possibly have helped:

    adb shell su -c id 失败 (exit 127)；su: connect daemon: Connection refused
    提权未成功，重启设备后重试…
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nebumtk_unlock_tools.core.errors import AdbTimeoutError, RootEscalationError
from nebumtk_unlock_tools.core.root import RootEscalator

ROOT = Path(__file__).resolve().parents[1]


class FakeResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.output = (stdout + stderr).strip()


class FakeAdb:
    """Minimal ADB stand-in; only the `id` probe is scripted.

    ``answers`` is consumed one probe at a time; ``default`` answers every probe
    after that, so a test can say "this keeps happening".
    """

    def __init__(self, answers: list[object], default: object) -> None:
        self.serial = "SERIAL-A"
        self.reboots = 0
        self.answers = list(answers)
        self.default = default
        self.root_probes = 0

    def shell(self, command: str, **kwargs):  # noqa: ANN003 - matches AdbClient
        if command == "id":
            self.root_probes += 1
            answer = self.answers.pop(0) if self.answers else self.default
            if isinstance(answer, Exception):
                raise answer
            return answer
        return FakeResult(0, "")

    def devices(self) -> list[str]:
        return ["SERIAL-A"]

    def wait_for_device(self, timeout: float | None = None) -> None:
        return None

    def remove_remote(self, path: str, root: bool = False) -> None:
        return None

    def reboot(self) -> None:
        self.reboots += 1


SHELL_USER = FakeResult(0, "uid=2000(shell) gid=2000(shell)")
ROOT_USER = FakeResult(0, "uid=0(root) gid=0(root)")
DEAD_DAEMON = FakeResult(127, "su: connect daemon: Connection refused")


def _escalator(
    adb: FakeAdb,
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_retries: int = 2,
    settle_attempts: int = 2,
) -> RootEscalator:
    monkeypatch.setattr(RootEscalator, "ROOT_SETTLE_ATTEMPTS", settle_attempts)
    monkeypatch.setattr(RootEscalator, "ROOT_SETTLE_INTERVAL", 0.0)
    monkeypatch.setattr(RootEscalator, "ROOT_VERIFY_DELAY", 0.0)
    escalator = RootEscalator(
        adb, max_retries=max_retries, reboot_wait=0.0, post_boot_settle=0.0
    )
    escalator.grant_responder = lambda _challenge, _target: "grant"
    # The preload execution itself is out of scope here; only the policy that
    # runs once it has returned is under test.
    escalator.preload_runs = 0
    monkeypatch.setattr(
        escalator, "_run_preload", lambda: setattr(escalator, "preload_runs", escalator.preload_runs + 1)
    )
    return escalator


def test_a_dead_su_daemon_neither_reboots_nor_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported log: the tool's su is installed, its daemon is not running.

    Only the preload starts that daemon, so the settle window can never earn a
    different answer and restarting the device changes nothing.
    """

    adb = FakeAdb([], default=DEAD_DAEMON)
    escalator = _escalator(adb, monkeypatch, settle_attempts=5)

    with pytest.raises(RootEscalationError):
        escalator.escalate()

    assert adb.reboots == 0, (
        "a dead daemon survives a reboot; restarting the device here is a boot "
        "cycle that cannot change the outcome"
    )
    assert adb.root_probes <= 4, (
        f"{adb.root_probes} probes: a dead daemon must not burn the whole "
        "settle window waiting for something nothing will start"
    )
    assert escalator.preload_runs == 2, (
        "both attempts must actually run the exploit, which is the only thing "
        "that can bring the daemon back"
    )


def test_a_clear_denial_retries_the_exploit_instead_of_rebooting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adb = FakeAdb([], default=SHELL_USER)
    escalator = _escalator(adb, monkeypatch)

    with pytest.raises(RootEscalationError):
        escalator.escalate()

    assert adb.reboots == 0, (
        "the su path, the daemon and the on-device exploit state are unchanged "
        "by a reboot, so it cannot turn a denial into a root"
    )
    assert escalator.preload_runs == 2


def test_a_device_that_never_answers_is_not_rebooted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escalation may have succeeded; an unanswered probe proves nothing."""

    timeout = AdbTimeoutError("su 未在 20s 内返回")
    adb = FakeAdb([timeout], default=timeout)
    escalator = _escalator(adb, monkeypatch)

    with pytest.raises(RootEscalationError):
        escalator.escalate()

    assert adb.root_probes > 1, "an unanswered probe must be retried, not believed"
    assert adb.reboots == 0


def test_root_that_arrives_late_is_still_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daemon needs a moment; a slow answer must not be read as a denial."""

    adb = FakeAdb([AdbTimeoutError("第一轮还没起来")], default=ROOT_USER)
    escalator = _escalator(adb, monkeypatch)

    escalator.escalate()

    assert adb.reboots == 0
    assert adb.root_probes >= 2


def test_a_successful_probe_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    adb = FakeAdb([], default=ROOT_USER)
    escalator = _escalator(adb, monkeypatch)

    escalator.escalate()

    assert adb.root_probes == 1
    assert adb.reboots == 0
