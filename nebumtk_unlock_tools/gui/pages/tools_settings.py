# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Platform tools, custom su and local workspace page."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import UiRole
from ..widgets import PolishedComboBox, TaskSection, action_button
from .common import page_layout


class ToolsSettingsPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        page, layout = page_layout(
            "工具与设置", "Platform Tools 与本地目录"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(page)

        platform = TaskSection("Platform Tools")
        platform_row = QHBoxLayout()
        self.workspace_label = QLabel("使用当前配置的 platform-tools")
        self.workspace_label.setObjectName("sectionDescription")
        self.terminal_run_button = action_button(
            "打开终端", "platformToolsTerminalAction", role=UiRole.PRIMARY
        )
        self.settings_button = QPushButton("设置")
        platform_row.addWidget(self.workspace_label, 1)
        platform_row.addWidget(self.settings_button)
        platform_row.addWidget(self.terminal_run_button)
        platform.content_layout.addLayout(platform_row)
        layout.addWidget(platform)

        su = TaskSection("应急 custom su", "仅在应急兼容模式下作为最后候选")
        self.custom_su_section = su
        su_row = QHBoxLayout()
        self.custom_su_edit = QLineEdit("su")
        self.custom_su_edit.setMaxLength(256)
        self.custom_su_validate_button = action_button(
            "验证", "customSuValidateAction", role=UiRole.SECONDARY
        )
        su_row.addWidget(self.custom_su_edit, 1)
        su_row.addWidget(self.custom_su_validate_button)
        su.content_layout.addLayout(su_row)
        self.custom_su_status = QLabel("尚未验证")
        self.custom_su_status.setObjectName("inlineStatus")
        su.content_layout.addWidget(self.custom_su_status)
        layout.addWidget(su)
        su.setVisible(False)

        local_files = TaskSection("本地文件", "备份、工作目录和日志")
        local_row = QHBoxLayout()
        self.open_backup_button = QPushButton("打开备份目录")
        self.open_workspace_button = QPushButton("打开工作目录")
        self.open_logs_button = QPushButton("打开日志目录")
        local_row.addWidget(self.open_backup_button)
        local_row.addWidget(self.open_workspace_button)
        local_row.addWidget(self.open_logs_button)
        local_files.content_layout.addLayout(local_row)
        self.log_file_label = QLabel("日志：就绪")
        self.log_file_label.setVisible(False)
        local_files.content_layout.addWidget(self.log_file_label)
        layout.addWidget(local_files)

        # Compatibility carriers for the existing controller. The public
        # workbench opens a real platform-tools terminal instead of exposing a
        # second command editor.
        self.su_provider_combo = PolishedComboBox()
        self.su_provider_combo.addItem("使用配置内置 su", "existing")
        self.su_provider_combo.addItem("使用 custom su", "custom")
        self.test_mode_banner = QLabel("")
        self.runtime_combo = PolishedComboBox()
        self.test_runtime_panel = QWidget()
        self.terminal_edit = QLineEdit()
        for carrier in (
            self.su_provider_combo,
            self.test_mode_banner,
            self.runtime_combo,
            self.test_runtime_panel,
            self.terminal_edit,
        ):
            carrier.setVisible(False)
            layout.addWidget(carrier)
        layout.addStretch(1)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
