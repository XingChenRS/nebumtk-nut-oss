# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Console smoke test for frozen-style startup."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

def main() -> int:
    try:
        from nebumtk_unlock_tools.app import project_root, workspace_root
        from nebumtk_unlock_tools.core.device import DeviceDetector
        from nebumtk_unlock_tools.resources.catalog import load_device_catalog
        from nebumtk_unlock_tools.resources.loader import ResourceLoader
        from PySide6.QtWidgets import QApplication

        root = project_root()
        print("project_root:", root)

        profiles = DeviceDetector._profiles_from_mapping(
            load_device_catalog().get("devices", {})
        )
        print("signed catalog profiles:", len(profiles))

        loader = ResourceLoader(root)
        misc = loader.resolve_misc("oppo", "_a")
        print("misc:", misc.name)

        app = QApplication(sys.argv)
        from PySide6.QtCore import QEvent
        from nebumtk_unlock_tools.gui.main_window import MainWindow

        window = MainWindow(root, workspace_root())
        print("mainwindow ok")
        window.device_scan_timer.stop()
        scan_worker = window.scan_worker
        window.close()
        if scan_worker is not None and scan_worker.isRunning():
            if not scan_worker.wait(30000):
                raise RuntimeError("Device scan worker did not stop before smoke test exit")
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        app.quit()
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
