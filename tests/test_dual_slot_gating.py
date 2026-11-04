# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Dual-slot LK sync is offered per brand, and only in the right direction.

A brand whose unlock cannot put the original LK back (Vivo via `xingc`, Honor
via `generic_lk`) may only sync a *patched* LK.  A brand whose unlock expects
the original LK restored afterwards (OPPO via `flashing_unlock`) may only sync that
*original* LK.  Both buttons were previously gated on nothing but a file being
selected, so the operator could start the wrong one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.gui.device_scan_worker import DeviceScanResult, DeviceScanWorker
from nebumtk_unlock_tools.gui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def _window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "refresh_devices", lambda self, *, auto=False: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_a, **_k: QMessageBox.Ok)
    return app, MainWindow(tmp_path, tmp_path / "workspace")


def _select_fastboot_device_with_both_lks(
    window: MainWindow,
    tmp_path: Path,
    model_key: str,
) -> None:
    index = window.model_combo.findData(model_key)
    assert index >= 0, f"{model_key} is missing from the model list"
    window.model_combo.setCurrentIndex(index)
    worker = DeviceScanWorker(tmp_path, Settings(), generation=17, automatic=True)
    worker.scan_completed.connect(window._apply_scan_result)
    window._scan_generation = 17
    worker.scan_completed.emit(
        DeviceScanResult(serial="SERIAL-A", mode="fastboot", scan_generation=17)
    )
    window.patched_lk_edit.setText("patched.img")
    window.original_lk_edit.setText("original.img")
    window._refresh_action_guidance()


@pytest.mark.parametrize("model_key", ["vivox200", "honormagic8proair"])
def test_brand_without_lk_restore_may_only_sync_a_patched_lk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_key: str,
) -> None:
    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_fastboot_device_with_both_lks(window, tmp_path, model_key)
        assert window.dual_slot_sync_button.isEnabled(), (
            f"{model_key} unlocks with a patched LK and must be able to sync it"
        )
        assert not window.original_lk_sync_button.isEnabled(), (
            f"{model_key} cannot restore the original LK after unlock, so "
            "syncing one to both slots must not be offered"
        )
    finally:
        window.device_scan_timer.stop()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_dual_slot_sync_uses_the_field_proven_finalization() -> None:
    """The patched button must keep the 1.1 finalization shape.

    1.1 wrote both LK slots, then set the active slot and did a checked reboot.
    The brand-neutral path the online port wired in instead skips *both*
    ``set_active`` and the reboot, which is a silent behaviour change for a
    field-proven action.
    """

    import inspect

    from nebumtk_unlock_tools.flows import stages
    from nebumtk_unlock_tools.flows.factory import create_flow

    flow = create_flow(
        "dual_slot_sync",
        ROOT,
        ROOT / ".test-workspace",
        Settings(),
        patched_lk=ROOT / "pyproject.toml",
    )
    assert isinstance(flow, stages.DualSlotSyncFlow)
    assert flow.reboot_after is True, (
        "the dual-slot sync must reboot after the write, as in 1.1"
    )

    signature = inspect.signature(stages._finalize_explicit_lk_fastboot)
    assert signature.parameters["set_active"].default is True, (
        "the dual-slot sync must set the active slot, as in 1.1"
    )
    assert signature.parameters["reboot"].default is True


def test_button_is_wired_to_the_dual_slot_flow() -> None:
    """A button that names the 1.1 path must actually start it."""

    source = (ROOT / "nebumtk_unlock_tools" / "gui" / "main_window.py").read_text(
        encoding="utf-8"
    )
    assert (
        "self.dual_slot_sync_button.clicked.connect(self._start_dual_slot_sync)" in source
    ), "the patched-LK button must go through _start_dual_slot_sync"
    assert "_start_global_lk_sync" not in source


def test_brand_that_restores_lk_may_only_sync_the_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_fastboot_device_with_both_lks(window, tmp_path, "oppofindx9")
        assert window.original_lk_sync_button.isEnabled(), (
            "OPPO restores the original LK after unlock and must be able to "
            "sync it to both slots"
        )
        assert not window.dual_slot_sync_button.isEnabled(), (
            "OPPO must never be offered a patched-LK dual-slot write"
        )
    finally:
        window.device_scan_timer.stop()
        window.close()
        window.deleteLater()
        app.processEvents()
