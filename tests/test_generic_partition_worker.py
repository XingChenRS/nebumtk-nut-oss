# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.runner import CommandResult


class ProbeAdb:
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, _binary: str, *, serial: str, **_kwargs) -> None:
        self.serial = serial

    def devices(self) -> list[str]:
        return [self.serial]

    def shell(self, command: str, **kwargs) -> CommandResult:
        self.calls.append((command, kwargs))
        return CommandResult(0, "uid=0(root) gid=0(root)\n", "")


def test_custom_su_probe_worker_runs_fixed_id_and_returns_typed_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.gui import worker as worker_module
    from nebumtk_unlock_tools.gui.worker import CustomSuProbeWorker

    app = QApplication.instance() or QApplication([])
    ProbeAdb.calls = []
    monkeypatch.setattr(
        worker_module.EnvChecker,
        "validate_adb",
        lambda _self: SimpleNamespace(adb="adb"),
    )
    monkeypatch.setattr(worker_module, "AdbClient", ProbeAdb)
    results: list[object] = []
    errors: list[str] = []
    worker = CustomSuProbeWorker(
        Settings(custom_su="/data/local/tmp/operator-su"),
        "SERIAL",
    )
    worker.result_ready.connect(results.append)
    worker.finished_error.connect(errors.append)

    worker.run()

    assert errors == []
    assert len(results) == 1
    assert results[0].uid == 0
    assert ProbeAdb.calls == [
        (
            "id",
            {
                "root": True,
                "su_path": "/data/local/tmp/operator-su",
                "timeout": 15,
                "check": False,
                "sensitive": True,
            },
        )
    ]
    app.processEvents()
