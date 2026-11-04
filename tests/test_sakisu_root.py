# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from nebumtk_unlock_tools.core.runner import CommandResult


def test_legacy_standalone_root_routes_stay_absent(tmp_path) -> None:
    from nebumtk_unlock_tools.config.store import Settings
    from nebumtk_unlock_tools.flows.factory import create_flow

    for flow_kind in ("kernelsu_root", "auto_prepare_unlock"):
        with pytest.raises(ValueError, match="未知 flow_kind"):
            create_flow(flow_kind, tmp_path, tmp_path / "workspace", Settings())


def test_adb_kernel_release_uses_non_root_uname(monkeypatch: pytest.MonkeyPatch) -> None:
    from nebumtk_unlock_tools.core.adb import AdbClient

    adb = AdbClient(serial="SERIAL")
    calls: list[tuple[str, dict]] = []

    def fake_shell(command: str, **kwargs) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(0, "5.15.94-android13-9-g123\n", "")

    monkeypatch.setattr(adb, "shell", fake_shell)

    assert adb.kernel_release() == "5.15.94-android13-9-g123"
    assert calls == [("uname -r", {"root": False, "timeout": 15, "sensitive": True})]
