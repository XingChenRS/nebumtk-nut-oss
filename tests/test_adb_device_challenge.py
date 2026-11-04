# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import subprocess
import sys

import pytest

from nebumtk_unlock_tools.core.adb import AdbClient
from nebumtk_unlock_tools.core.errors import AdbError


def _replace_adb_process(
    monkeypatch: pytest.MonkeyPatch,
    script: str,
) -> None:
    real_popen = subprocess.Popen

    def launch(_args, **kwargs):
        return real_popen(
            [sys.executable, "-u", "-c", script],
            **kwargs,
        )

    monkeypatch.setattr(
        "nebumtk_unlock_tools.core.adb.subprocess.Popen",
        launch,
    )


def test_shell_challenge_exchange_relays_only_one_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge = "NBCH1:test-payload:" + "11" * 32 + ":" + "22" * 32
    grant = "NBGR1:" + "33" * 16 + ":1:" + "44" * 32 + ":" + "55" * 12 + ":" + "66" * 48
    script = (
        "import sys\n"
        f"print({challenge!r}, flush=True)\n"
        "line = sys.stdin.readline().strip()\n"
        f"sys.exit(0 if line == {grant!r} else 9)\n"
    )
    _replace_adb_process(monkeypatch, script)
    seen: list[str] = []

    result = AdbClient("ignored").shell_challenge_exchange(
        "ignored-command",
        lambda line: seen.append(line) or grant,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.output == ""
    assert seen == [challenge]


def test_shell_challenge_exchange_rejects_missing_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _replace_adb_process(
        monkeypatch,
        "print('ordinary output', flush=True)\n",
    )

    with pytest.raises(AdbError, match="ordinary output"):
        AdbClient("ignored").shell_challenge_exchange(
            "ignored-command",
            lambda _line: "unused",
            timeout=5,
        )


def test_shell_challenge_exchange_rejects_multiline_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge = "NBCH1:test-payload:" + "11" * 32 + ":" + "22" * 32
    _replace_adb_process(
        monkeypatch,
        (
            "import sys\n"
            f"print({challenge!r}, flush=True)\n"
            "sys.stdin.readline()\n"
        ),
    )

    with pytest.raises(ValueError, match="wire format"):
        AdbClient("ignored").shell_challenge_exchange(
            "ignored-command",
            lambda _line: "bad\ngrant",
            timeout=5,
        )


def test_shell_challenge_exchange_ignores_stderr_challenge_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    challenge = "NBCH1:test-payload:" + "11" * 32 + ":" + "22" * 32
    _replace_adb_process(
        monkeypatch,
        (
            "import sys\n"
            f"print({challenge!r}, file=sys.stderr, flush=True)\n"
        ),
    )

    with pytest.raises(AdbError, match="NBCH1"):
        AdbClient("ignored").shell_challenge_exchange(
            "ignored-command",
            lambda _line: "unused",
            timeout=5,
        )
