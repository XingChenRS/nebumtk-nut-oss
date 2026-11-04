from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.gui.settings_dialog import SettingsDialog


def test_settings_dialog_shows_custom_su_as_read_only_advanced_owned_state() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = SettingsDialog(Settings(custom_su="/data/local/tmp/operator-su"))
    try:
        assert not hasattr(dialog, "custom_su_edit")
        assert "/data/local/tmp/operator-su" in dialog.custom_su_status.text()
        assert "工具与设置" in dialog.custom_su_status.text()
        assert dialog.result_settings().custom_su == "/data/local/tmp/operator-su"
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
