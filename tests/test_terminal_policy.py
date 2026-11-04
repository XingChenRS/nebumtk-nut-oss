# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.core.terminal_policy import parse_diagnostic_command


@pytest.mark.parametrize(
    ("command", "tool", "args"),
    (
        ("adb devices", "adb", ("devices",)),
        ("adb shell id", "adb", ("shell", "id")),
        (
            "adb shell getprop ro.product.device",
            "adb",
            ("shell", "getprop", "ro.product.device"),
        ),
        ("fastboot getvar current-slot", "fastboot", ("getvar", "current-slot")),
    ),
)
def test_diagnostic_policy_accepts_only_read_only_inventory(
    command: str,
    tool: str,
    args: tuple[str, ...],
) -> None:
    parsed = parse_diagnostic_command(command)
    assert parsed.tool == tool
    assert parsed.args == args


@pytest.mark.parametrize(
    "command",
    (
        "cmd /c whoami",
        "adb shell sh -c id",
        "adb shell getprop ro.product.device;whoami",
        "adb shell getprop ro.product.device | more",
        "adb -s ATTACKER devices",
        "fastboot flash boot boot.img",
        "fastboot reboot",
        "fastboot getvar current-slot && whoami",
        "adb shell getprop ../../data",
        "adb devices\nwhoami",
    ),
)
def test_diagnostic_policy_rejects_injection_and_mutating_commands(
    command: str,
) -> None:
    with pytest.raises(VerificationError):
        parse_diagnostic_command(command)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
