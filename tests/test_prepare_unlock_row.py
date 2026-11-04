# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""The 「准备解锁」 row writes the inactive slot, and says so per brand.

Two defects are pinned here.

The row is ``write_inactive`` / ``honor_prepare``: it writes the operator's
selected patched LK into the inactive slot together with the brand's misc image
and reboots into fastboot.  It never issues ``fastboot flashing unlock`` — that
command belongs to the OPPO unlock row below it.

That OPPO row's explanatory line was appended to the section separately from its
row.  ``apply_capabilities`` hid the row in ADB mode but never the line, so the
line kept rendering and reattached itself under 「准备解锁」, describing an action
the row above it does not perform.  A note is now built by ``_action_row`` and
therefore cannot outlive the row it explains.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.gui.device_scan_worker import DeviceScanResult, DeviceScanWorker
from nebumtk_unlock_tools.gui.main_window import MainWindow
from nebumtk_unlock_tools.gui.pages.root_unlock import PREPARE_UNLOCK_NOTES

ROOT = Path(__file__).resolve().parents[1]
UNLOCK_PAGE = ROOT / "nebumtk_unlock_tools" / "gui" / "pages" / "root_unlock.py"

# Brand -> a catalog record that carries it.  The row is reached from ADB.
BRANDS = {"vivo": "vivox200", "honor": "honormagic8proair", "oppo": "oppofindx9"}


def _window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "refresh_devices", lambda self, *, auto=False: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_a, **_k: QMessageBox.Ok)
    # Settings load from %APPDATA%, so without this the gating under test depends
    # on whether the machine running the suite happens to have test mode saved.
    monkeypatch.setattr(MainWindow, "_test_mode_enabled", lambda self: False)
    return app, MainWindow(tmp_path, tmp_path / "workspace")


def _select_adb_device(
    window: MainWindow,
    tmp_path: Path,
    model_key: str,
    *,
    patched_lk: str | None = "patched.img",
) -> None:
    index = window.model_combo.findData(model_key)
    assert index >= 0, f"{model_key} is missing from the model list"
    window.model_combo.setCurrentIndex(index)
    worker = DeviceScanWorker(tmp_path, Settings(), generation=23, automatic=True)
    worker.scan_completed.connect(window._apply_scan_result)
    window._scan_generation = 23
    worker.scan_completed.emit(
        DeviceScanResult(serial="SERIAL-A", mode="adb", scan_generation=23)
    )
    if patched_lk is not None:
        window.patched_lk_edit.setText(patched_lk)
    window._refresh_action_guidance()
    # Row visibility is only meaningful against a shown window on the page that
    # owns the row; the shell keeps the other four pages explicitly hidden.
    window.show()
    window.pages.setCurrentWidget(window.page_scroll_areas["authorized"])
    QApplication.processEvents()


def _teardown(app: QApplication, window: MainWindow) -> None:
    window.device_scan_timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("brand,model_key", sorted(BRANDS.items()))
def test_prepare_unlock_note_describes_this_brand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brand: str,
    model_key: str,
) -> None:
    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_adb_device(window, tmp_path, model_key)
        assert window.authorized_page.write_action_row.isVisible(), (
            f"{brand} has an LK write path and must be offered the row"
        )
        assert window.authorized_page.write_action_note.text() == PREPARE_UNLOCK_NOTES[brand], (
            f"the line under 「准备解锁」 must describe the {brand} recipe"
        )
    finally:
        _teardown(app, window)


@pytest.mark.parametrize("brand,model_key", sorted(BRANDS.items()))
def test_prepare_unlock_note_never_claims_the_row_unlocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brand: str,
    model_key: str,
) -> None:
    """The row writes; it does not unlock.  Only the OPPO row may name the command."""

    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_adb_device(window, tmp_path, model_key)
        note = window.authorized_page.write_action_note.text()
        assert "flashing unlock" not in note or "「完整解锁 BL」" in note, (
            f"the {brand} note reads as if 「准备解锁」 issues `fastboot flashing "
            f"unlock`; if the command is mentioned at all it must be attributed "
            f"to the unlock row: {note}"
        )
        assert "对槽" in note, (
            f"the {brand} note must say the row writes the inactive slot: {note}"
        )
    finally:
        _teardown(app, window)


def test_oppo_can_reach_the_prepare_unlock_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPPO writes a patched LK to the inactive slot before its unlock.

    That is why 「还原双槽原 LK」 exists: the unlock itself is a plain
    ``flashing unlock``, so the stock LK has to go back over both slots
    afterwards.  Gating this row on ``has_lk`` — which means "this brand's
    unlock rides a patched LK" — left it permanently disabled for the one brand
    whose unlock explicitly needs the stock LK restored.
    """

    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_adb_device(window, tmp_path, "oppofindx9")
        assert window.write_button.isEnabled(), (
            "OPPO prepares its unlock by writing the inactive slot and must be "
            "able to reach that row"
        )
    finally:
        _teardown(app, window)


@pytest.mark.parametrize("brand,model_key", sorted(BRANDS.items()))
def test_prepare_unlock_still_requires_a_selected_lk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brand: str,
    model_key: str,
) -> None:
    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_adb_device(window, tmp_path, model_key, patched_lk=None)
        assert not window.write_button.isEnabled(), (
            f"{brand} must not be able to start the write with no LK selected"
        )
    finally:
        _teardown(app, window)


@pytest.mark.parametrize("brand,model_key", sorted(BRANDS.items()))
def test_hidden_unlock_row_takes_its_note_with_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brand: str,
    model_key: str,
) -> None:
    """The reported symptom, reproduced.

    In ADB mode the OPPO unlock row is hidden.  Its note was not, so it stayed
    on screen directly beneath 「准备解锁」 and told the operator that row issues
    ``fastboot flashing unlock``.
    """

    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_adb_device(window, tmp_path, model_key)
        page = window.authorized_page
        assert not page.oppo_action_row.isVisible(), (
            "the OPPO unlock row is a fastboot action and must be hidden in ADB"
        )
        assert not page.oppo_action_row.note_label.isVisible(), (
            f"the hidden unlock row's note is still on screen for {brand}, so it "
            "reads as the explanation for 「准备解锁」"
        )
        assert page.write_action_note.parent() is page.write_action_row, (
            "the note under 「准备解锁」 must be a child of that row"
        )
        # General form of the same defect: any note on this page whose parent is
        # not an action row can outlive the row it was written for.
        strays = [
            label.text()
            for label in page.findChildren(QLabel)
            if label.objectName() == "mutedText"
            and label.parent().objectName() != "manualAction"
        ]
        assert not strays, (
            "these notes are not owned by an action row, so they keep rendering "
            f"after their row is hidden: {strays}"
        )
    finally:
        _teardown(app, window)


@pytest.mark.parametrize("brand,model_key", sorted(BRANDS.items()))
def test_every_brand_can_save_a_recovery_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brand: str,
    model_key: str,
) -> None:
    """「保存恢复点」 had the same overloaded gate as 「准备解锁」.

    It backs up whatever the profile's workflow lists and the flow refuses to
    continue without ``lk`` in that list.  Gating it on the capability set
    instead left it greyed out on OPPO, whose workflow backs up lk and
    init_boot like the others.
    """

    app, window = _window(tmp_path, monkeypatch)
    try:
        _select_adb_device(window, tmp_path, model_key, patched_lk=None)
        assert window.backup_button.isEnabled(), (
            f"{brand}'s workflow backs up lk, so the recovery-point row must be "
            "reachable"
        )
    finally:
        _teardown(app, window)


def test_unlock_page_notes_are_owned_by_their_action_row() -> None:
    """A note added to a section separately outlives the row it explains.

    It then reattaches to whichever row is still on screen and describes the
    wrong action, which is exactly how ``fastboot flashing unlock`` ended up
    printed under 「准备解锁」.
    """

    text = UNLOCK_PAGE.read_text(encoding="utf-8")
    note_lines = [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if 'setObjectName("mutedText")' in line
    ]
    assert note_lines, "the note style disappeared; this guard is stale"

    functions = [
        node
        for node in ast.walk(ast.parse(text))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno is not None
    ]
    for number in note_lines:
        enclosing = [
            node for node in functions if node.lineno <= number <= node.end_lineno
        ]
        assert enclosing, f"line {number} builds a note outside any function"
        innermost = min(enclosing, key=lambda node: node.end_lineno - node.lineno)
        assert innermost.name == "_action_row", (
            f"line {number} builds a note in {innermost.name}(); notes must come "
            "from _action_row so that hiding a row hides its explanation"
        )
