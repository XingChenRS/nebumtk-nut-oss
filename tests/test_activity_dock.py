# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""The right-hand dock must always be reachable, in both directions.

Two defects are pinned here.

Collapsed, the dock is a 44px strip.  Its header was a plain QFrame, which grows
to absorb whatever space the layout frees up, so hiding the body made the header
fill the panel and dropped the toggle arrow from the top to the vertical middle.
The button itself was squeezed to whatever the strip had left over (19px), which
clipped the arrow away entirely: the collapsed dock rendered as a blank beige
strip with nothing to click.

And the shell derived the dock's state from the window width on *every* resize.
Expanding the dock changed the layout width, which re-ran the rule, which
collapsed the dock again — so once it went in it could not be brought back out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from nebumtk_unlock_tools.gui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]


def _window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "refresh_devices", lambda self, *, auto=False: None)
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_a, **_k: QMessageBox.Yes
    )
    # The gating under test must not depend on whatever test mode the machine
    # running the suite happens to have saved.
    monkeypatch.setattr(MainWindow, "_test_mode_enabled", lambda self: False)
    return app, MainWindow(tmp_path, tmp_path / "workspace")


def _teardown(app: QApplication, window: MainWindow) -> None:
    window.device_scan_timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_collapsed_dock_leaves_a_usable_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, window = _window(tmp_path, monkeypatch)
    try:
        window.resize(1400, 800)
        window.show()
        app.processEvents()
        panel = window.activity_panel
        panel.set_log_expanded(False)
        app.processEvents()

        button = panel.toggle_button
        assert panel.width() == panel.COLLAPSED_WIDTH
        assert button.isVisible(), "the collapsed dock must still offer a way back"
        assert button.width() >= 24 and button.height() >= 20, (
            f"the toggle is squeezed to {button.width()}x{button.height()}, which "
            "clips the arrow away and leaves a blank strip"
        )
        top_offset = button.mapTo(panel, QPoint(0, 0)).y()
        assert top_offset < panel.height() // 4, (
            f"the toggle drifted {top_offset}px down a {panel.height()}px strip; "
            "it belongs in the header at the top"
        )
        assert button.text() == "▶"
    finally:
        _teardown(app, window)


def test_dock_can_be_brought_back_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The width rule picks the starting state; it must not overrule the operator."""

    app, window = _window(tmp_path, monkeypatch)
    try:
        window.resize(1000, 800)  # below the shell's navigation breakpoint
        window.show()
        app.processEvents()
        panel = window.activity_panel
        assert not panel.is_log_expanded(), (
            "a narrow window still starts with the dock collapsed"
        )

        QTest.mouseClick(panel.toggle_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert panel.is_log_expanded(), "the toggle must bring the dock back"
        assert panel.toggle_button.text() == "◀"

        window.resize(1200, 800)
        app.processEvents()
        assert panel.is_log_expanded(), (
            "a later resize re-applied the width rule and undid the operator's "
            "choice, which is why the dock could not be brought back out"
        )

        window.resize(980, 800)
        app.processEvents()
        assert panel.is_log_expanded(), (
            "the dock must stay where the operator put it, not snap on resize"
        )
    finally:
        _teardown(app, window)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
