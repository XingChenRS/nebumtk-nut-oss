"""Application entry helpers."""

from __future__ import annotations

import sys
import traceback
import os
import ctypes
from pathlib import Path

UNTRUSTED_RUNTIME_ENV = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONUSERBASE",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "ADB_SERVER_SOCKET",
    "ANDROID_ADB_SERVER_PORT",
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))


def harden_runtime_environment() -> None:
    if not is_frozen():
        return
    sys.dont_write_bytecode = True
    for name in UNTRUSTED_RUNTIME_ENV:
        os.environ.pop(name, None)
    if sys.platform == "win32":
        try:
            # Application directory, System32 and explicitly added directories.
            ctypes.windll.kernel32.SetDefaultDllDirectories(0x00001000)
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except (AttributeError, OSError):
            pass


def _contains_runtime_data(path: Path) -> bool:
    return (path / "misc").is_dir()


def project_root() -> Path:
    # PyInstaller one-folder/one-file extract dir
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))

    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        # Nuitka standalone: datas sit next to exe
        if _contains_runtime_data(exe_dir):
            return exe_dir
        # Nuitka onefile: datas sit in temp extraction (near compiled package)
        here = Path(__file__).resolve().parent
        for candidate in (here.parent, here, exe_dir):
            if _contains_runtime_data(candidate):
                return candidate
        return exe_dir

    return Path(__file__).resolve().parent.parent


def workspace_root() -> Path:
    # Always writable next to the launched exe (not temp extract)
    if is_frozen():
        return Path(sys.executable).resolve().parent / "workspace"
    return project_root() / "workspace"


def main() -> int:
    try:
        harden_runtime_environment()
        from PySide6.QtWidgets import QApplication

        from .keymaterial import set_project_root
        from .gui.main_window import MainWindow

        root = project_root()
        set_project_root(root)
        app = QApplication(sys.argv)
        window = MainWindow(root, workspace_root())
        window.show()
        return app.exec()
    except Exception:
        if not is_frozen():
            traceback.print_exc()
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "NEBUMTK", "程序初始化失败")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
