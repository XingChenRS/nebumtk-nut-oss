# SPDX-License-Identifier: AGPL-3.0-only
"""Settings dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config.store import Settings
from ..build_profile import is_online
from ..core.platform_tools import resolve_platform_tools

class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("NEBUMTK 设置")
        self.setMinimumWidth(620)
        self._settings = settings
        self.platform_tools_edit = QLineEdit(settings.platform_tools_path)
        self.test_mode_check = QCheckBox("测试模式")
        self.test_mode_check.setChecked(
            settings.test_mode_enabled and not is_online()
        )
        self.test_mode_check.setVisible(not is_online())
        self.test_mode_check.setToolTip("允许手动组合机型与受保护运行配置")
        self.catalog_import_button = QPushButton("导入签名机型包")
        self.catalog_import_button.clicked.connect(self._import_model_pack)
        self.catalog_import_button.setVisible(not is_online())
        hint = QLabel("运行环境")
        hint.setObjectName("settingsIntro")
        hint.setWordWrap(True)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft)
        form.addRow("platform-tools", self._with_browse_dir(self.platform_tools_edit))
        if not is_online():
            form.addRow("维护测试", self.test_mode_check)
            form.addRow("机型目录", self.catalog_import_button)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_validated)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        title = QLabel("工具路径")
        title.setObjectName("settingsTitle")
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(buttons)
        self.setStyleSheet("""
            QDialog { background: #EFF9FD; color: #102536; font-family: 'Segoe UI', 'Microsoft YaHei UI'; }
            QLabel#settingsTitle { color: #14364B; font-size: 20px; font-weight: 650; }
            QLabel#settingsIntro { color: #597181; line-height: 1.35; }
            QLineEdit { background: #FFFFFF; border: 1px solid #9BC6D8; border-radius: 6px; color: #102536; padding: 7px 9px; }
            QLineEdit:focus { border: 2px solid #169ACA; }
            QCheckBox { color: #173E55; spacing: 8px; }
            QPushButton { background: #F8FDFF; border: 1px solid #91BDCF; border-radius: 6px; color: #173E55; min-height: 30px; padding: 0 12px; font-weight: 600; }
            QPushButton:hover { background: #DBF3FB; border-color: #159CCB; }
            QDialogButtonBox QPushButton { min-width: 84px; }
            QDialogButtonBox QPushButton:default { background: #0D91C4; border-color: #0D91C4; color: #FFFFFF; }
        """)

    def _with_browse_dir(self, edit: QLineEdit) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit)
        button = QPushButton("浏览")
        button.clicked.connect(lambda: self._pick_dir(edit))
        row.addWidget(button)
        return container

    def _pick_dir(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 platform-tools 目录")
        if not path:
            return
        normalized = self._normalize_platform_tools_dir(path)
        edit.setText(normalized)

    def _import_model_pack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择签名机型包",
            "",
            "NEBUMTK Model Pack (*.nbmodel)",
        )
        if not path:
            return
        try:
            from ..resources.catalog import install_model_pack

            _target, revision = install_model_pack(Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "NEBUMTK", f"机型包导入失败: {exc}")
            return
        QMessageBox.information(
            self,
            "NEBUMTK",
            f"机型目录已导入（revision {revision}），保存设置后生效。",
        )

    @staticmethod
    def _normalize_platform_tools_dir(raw: str) -> str:
        path = Path(raw).expanduser()
        # 若选到含 platform-tools 子目录的 SDK 根，自动下钻
        child = path / "platform-tools"
        if child.is_dir() and ((child / "adb.exe").is_file() or (child / "adb").is_file()):
            return str(child.resolve())
        return str(path.resolve()) if path.exists() else raw.strip()

    def _accept_validated(self) -> None:
        raw = self.platform_tools_edit.text().strip()
        if raw:
            normalized = self._normalize_platform_tools_dir(raw)
            try:
                tools = resolve_platform_tools(normalized)
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "NEBUMTK",
                    f"无效的 platform-tools 路径:\n{normalized}\n\n{exc}",
                )
                return
            self.platform_tools_edit.setText(str(Path(tools.directory or normalized)))
        if (
            not is_online()
            and self.test_mode_check.isChecked()
            and not self._settings.test_mode_enabled
        ):
            reply = QMessageBox.warning(
                self,
                "NEBUMTK 测试模式",
                "测试模式会忽略品牌、型号、设备代号和固件匹配，并允许手动选择受保护运行配置。\n\n"
                "设备序列号、传输模式、槽位、分区容量、镜像哈希和写入前复核不会放宽。确认开启？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.accept()

    def result_settings(self) -> Settings:
        return Settings(
            platform_tools_path=self.platform_tools_edit.text().strip(),
            test_mode_enabled=(
                self.test_mode_check.isChecked() and not is_online()
            ),
        )
