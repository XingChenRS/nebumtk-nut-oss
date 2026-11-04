# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Device connection, reboot and bounded diagnostics page."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..theme import UiRole
from ..widgets import PolishedComboBox, TaskSection, action_button
from .common import page_layout


class DevicePage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        page, layout = page_layout("设备", "当前连接和可用能力")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(page)

        self.summary_line = QFrame()
        self.summary_line.setObjectName("summaryLine")
        summary = QHBoxLayout(self.summary_line)
        summary.setContentsMargins(13, 10, 13, 10)
        summary_copy = QVBoxLayout()
        summary_copy.setSpacing(1)
        self.connection_status = QLabel("等待设备")
        self.connection_status.setObjectName("summaryTitle")
        self.scan_detail = QLabel("连接设备后会自动扫描")
        self.scan_detail.setObjectName("actionContext")
        self.scan_detail.setWordWrap(True)
        summary_copy.addWidget(self.connection_status)
        summary_copy.addWidget(self.scan_detail)
        summary.addLayout(summary_copy, 1)
        self.model_combo = PolishedComboBox()
        self.model_combo.setVisible(False)
        self.refresh_button = action_button(
            "重新扫描", "deviceRescanAction", role=UiRole.PRIMARY
        )
        summary.addWidget(self.refresh_button)
        layout.addWidget(self.summary_line)

        overview = TaskSection("设备详情", "连接后自动读取；无法可靠确认的项目会显示为未知")
        self.device_fact_grid = QFrame()
        self.device_fact_grid.setObjectName("deviceFactGrid")
        fact_grid = QGridLayout(self.device_fact_grid)
        fact_grid.setContentsMargins(0, 0, 0, 0)
        fact_grid.setHorizontalSpacing(8)
        fact_grid.setVerticalSpacing(8)
        self.device_fact_values: dict[str, QLabel] = {}
        facts = (
            ("bootloader", "Bootloader"),
            ("slot", "当前槽位"),
            ("model", "型号"),
            ("codename", "设备代号"),
            ("manufacturer", "厂商"),
            ("platform", "平台"),
            ("android", "系统版本"),
            ("security_patch", "安全补丁"),
            ("firmware", "系统构建"),
            ("build_incremental", "增量版本"),
            ("kernel", "内核 uname -r"),
            ("profile", "机型模板"),
        )
        for index, (name, caption) in enumerate(facts):
            cell = QFrame(self.device_fact_grid)
            cell.setObjectName("deviceFactCell")
            box = QVBoxLayout(cell)
            box.setContentsMargins(10, 8, 10, 9)
            box.setSpacing(2)
            caption_label = QLabel(caption, cell)
            caption_label.setObjectName("fieldCaption")
            value_label = QLabel("—", cell)
            value_label.setObjectName("deviceFactValue")
            value_label.setWordWrap(True)
            value_label.setMinimumWidth(0)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            box.addWidget(caption_label)
            box.addWidget(value_label)
            fact_grid.addWidget(cell, index // 2, index % 2)
            self.device_fact_values[name] = value_label
        overview.content_layout.addWidget(self.device_fact_grid)
        layout.addWidget(overview)

        reboot = TaskSection("重启设备", "不会写入分区")
        targets = (
            ("system", "系统"),
            ("recovery", "Recovery"),
            ("bootloader", "Bootloader"),
            ("fastbootd", "Fastbootd"),
        )
        reboot_row = QHBoxLayout()
        self.reboot_buttons: dict[str, QPushButton] = {}
        for target, text in targets:
            button = action_button(text, f"reboot{target.title()}Action")
            self.reboot_buttons[target] = button
            reboot_row.addWidget(button)
        reboot.content_layout.addLayout(reboot_row)
        layout.addWidget(reboot)

        diagnostics = TaskSection("连接诊断")
        diagnostic_head = QHBoxLayout()
        self.diagnostics_state = QLabel("连接后自动完成")
        self.diagnostics_state.setObjectName("successText")
        self.diagnostics_button = action_button(
            "重新读取", "deviceDiagnosticsAction", role=UiRole.QUIET
        )
        diagnostic_head.addWidget(self.diagnostics_state, 1)
        diagnostic_head.addWidget(self.diagnostics_button)
        diagnostics.content_layout.addLayout(diagnostic_head)
        self.diagnostic_grid = QFrame()
        self.diagnostic_grid.setObjectName("diagnosticGrid")
        grid = QGridLayout(self.diagnostic_grid)
        grid.setContentsMargins(0, 0, 0, 0)
        self.diagnostic_values: list[QLabel] = []
        for index, (caption, value) in enumerate(
            (("连接", "等待连接"), ("授权", "等待设备"), ("机型匹配", "等待读取"), ("模式", "等待检查"))
        ):
            cell = QFrame()
            cell.setObjectName("diagnosticCell")
            cell_box = QVBoxLayout(cell)
            cell_box.setContentsMargins(9, 7, 9, 7)
            cell_box.setSpacing(1)
            caption_label = QLabel(caption)
            caption_label.setObjectName("fieldCaption")
            value_label = QLabel(value)
            value_label.setObjectName("headerValue")
            self.diagnostic_values.append(value_label)
            cell_box.addWidget(caption_label)
            cell_box.addWidget(value_label)
            grid.addWidget(cell, index // 2, index % 2)
        diagnostics.content_layout.addWidget(self.diagnostic_grid)
        self.diagnostics_output = QTextEdit()
        self.diagnostics_output.setReadOnly(True)
        self.diagnostics_output.setVisible(False)
        diagnostics.content_layout.addWidget(self.diagnostics_output)
        layout.addWidget(diagnostics)

        self.detected_connection_label = QLabel("连接：未检测")
        self.detected_identity_label = QLabel("设备：—")
        self.detected_firmware_label = QLabel("固件 / 内核：—")
        self.detected_slot_label = QLabel("槽位：—")
        self.detected_profile_label = QLabel("配置：—")
        for label in (
            self.detected_connection_label,
            self.detected_identity_label,
            self.detected_firmware_label,
            self.detected_slot_label,
            self.detected_profile_label,
        ):
            label.setVisible(False)
            layout.addWidget(label)

        self.current_operation_panel = TaskSection("当前操作")
        self.backup_path_label = QLabel("当前没有运行中的操作")
        self.current_operation_panel.content_layout.addWidget(self.backup_path_label)
        self.current_operation_panel.setVisible(False)
        layout.addWidget(self.current_operation_panel)
        layout.addStretch(1)

    def apply_device_snapshot(self, snapshot: object) -> None:
        for name, label in self.device_fact_values.items():
            value = str(getattr(snapshot, name, "—") or "—")
            value = value.replace("\r", " ").replace("\n", " ").strip() or "—"
            label.setText(value)
            label.setToolTip(value)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
