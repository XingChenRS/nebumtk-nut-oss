# SPDX-License-Identifier: AGPL-3.0-only
"""Right-side device inspector, operation state and expandable activity log."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTextEdit,
    QVBoxLayout,
)

from .theme import UiRole, apply_role


class ActivityPanel(QFrame):
    cancel_requested = Signal()
    COLLAPSED_WIDTH = 44
    EXPANDED_WIDTH = 312
    # The toggle must survive the collapse: the strip is 44 wide, so the
    # collapsed margins leave exactly the button's 32.
    TOGGLE_SIZE = QSize(32, 28)
    EXPANDED_HEADER_MARGINS = (14, 10, 10, 8)
    COLLAPSED_HEADER_MARGINS = (6, 10, 6, 8)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("activityDock")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._log_expanded = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("activityHeader")
        # A plain QFrame grows to absorb whatever space the layout frees up, so
        # the Fixed vertical policy caps the header at its own height. Keeping it
        # at the *top* needs the tail spacer added below the body.
        header.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.header = header
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(*self.EXPANDED_HEADER_MARGINS)
        self.header_row = header_row
        self.panel_title = QLabel("设备与日志", header)
        self.panel_title.setObjectName("sectionHeading")
        self.toggle_button = QPushButton("◀", header)
        self.toggle_button.setToolTip("收起设备与日志")
        # A floor, not a fixed size: the stylesheet's own metrics are taller, and
        # without it the button is squeezed to whatever the collapsed strip has
        # left over, which clips the arrow away entirely.
        self.toggle_button.setMinimumSize(self.TOGGLE_SIZE)
        apply_role(self.toggle_button, UiRole.QUIET)
        self.toggle_button.clicked.connect(
            lambda: self.set_log_expanded(not self.is_log_expanded())
        )
        header_row.addWidget(self.panel_title, 1)
        header_row.addWidget(self.toggle_button)
        root.addWidget(header)

        self.body = QFrame(self)
        self.body.setObjectName("activityBody")
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.device_section = QFrame(self.body)
        self.device_section.setObjectName("supportSection")
        device = QVBoxLayout(self.device_section)
        device.setContentsMargins(14, 10, 14, 12)
        device.setSpacing(7)
        self.device_model = QLabel("等待设备")
        self.device_model.setObjectName("supportDeviceTitle")
        self.device_summary = QLabel("—")
        self.device_summary.setObjectName("actionContext")
        device.addWidget(self.device_model)
        device.addWidget(self.device_summary)
        facts = QGridLayout()
        facts.setHorizontalSpacing(10)
        facts.setVerticalSpacing(5)
        self.device_fact_values: dict[str, QLabel] = {}
        for row, (name, caption) in enumerate(
            (
                ("mode", "模式"),
                ("slot", "当前槽位"),
                ("model", "型号"),
                ("bootloader", "BL 状态"),
                ("android", "系统版本"),
                ("firmware", "系统构建"),
                ("platform", "平台"),
                ("kernel", "内核版本"),
                ("profile", "机型模板"),
                ("serial", "序列号"),
            )
        ):
            caption_label = QLabel(caption)
            caption_label.setObjectName("supportCaption")
            value = QLabel("—")
            value.setObjectName("supportValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            value.setTextInteractionFlags(value.textInteractionFlags())
            facts.addWidget(caption_label, row, 0)
            facts.addWidget(value, row, 1)
            self.device_fact_values[name] = value
        device.addLayout(facts)
        body.addWidget(self.device_section)

        self.status_strip = QFrame(self.body)
        self.status_strip.setObjectName("supportSection")
        status = QVBoxLayout(self.status_strip)
        status.setContentsMargins(14, 11, 14, 12)
        status.setSpacing(7)
        operation_caption = QLabel("当前操作")
        operation_caption.setObjectName("supportCaption")
        self.stage_label = QLabel("设备就绪")
        self.stage_label.setObjectName("actionTitle")
        self.stage_label.setWordWrap(True)
        self.stage_detail = QLabel("等待选择操作")
        self.stage_detail.setObjectName("supportCaption")
        self.progress = QProgressBar(self.status_strip)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.cancel_button = QPushButton("取消", self.status_strip)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        status.addWidget(operation_caption)
        status.addWidget(self.stage_label)
        status.addWidget(self.progress)
        status.addWidget(self.stage_detail)
        status.addWidget(self.cancel_button)
        body.addWidget(self.status_strip)

        self.log_section = QFrame(self.body)
        self.log_section.setObjectName("supportSection")
        detail = QVBoxLayout(self.log_section)
        detail.setContentsMargins(14, 11, 14, 12)
        detail.setSpacing(6)
        log_header = QHBoxLayout()
        log_caption = QLabel("运行日志")
        log_caption.setObjectName("supportCaption")
        self.log_file_label = QLabel("就绪")
        self.log_file_label.setObjectName("supportValue")
        log_header.addWidget(log_caption, 1)
        log_header.addWidget(self.log_file_label)
        self.log_view = QTextEdit(self.log_section)
        self.log_view.setObjectName("activityLog")
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(5000)
        detail.addLayout(log_header)
        detail.addWidget(self.log_view, 1)
        body.addWidget(self.log_section, 1)
        root.addWidget(self.body, 1)
        # A trailing spacer takes the space the body gives up when it is hidden.
        # Without it nothing in the layout can absorb that surplus and the
        # header is centred in the strip instead of staying at the top.
        self.tail_spacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        root.addItem(self.tail_spacer)
        self.set_log_expanded(True)

    def apply_device_snapshot(self, snapshot: object) -> None:
        values = {
            name: str(getattr(snapshot, name, "—") or "—")
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()
            or "—"
            for name in self.device_fact_values
        }
        self.device_model.setText(values["model"])
        self.device_summary.setText(
            f"{values['mode']}  ·  {values['slot']}"
        )
        for name, label in self.device_fact_values.items():
            label.setText(values[name])
            label.setToolTip(values[name])

    def set_log_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self._log_expanded = expanded
        self.body.setVisible(expanded)
        self.panel_title.setVisible(expanded)
        self.header_row.setContentsMargins(
            *(self.EXPANDED_HEADER_MARGINS if expanded else self.COLLAPSED_HEADER_MARGINS)
        )
        self.toggle_button.setText("◀" if expanded else "▶")
        self.toggle_button.setToolTip("收起设备与日志" if expanded else "展开设备与日志")
        self.tail_spacer.changeSize(
            0,
            0,
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed if expanded else QSizePolicy.Policy.Expanding,
        )
        self.layout().invalidate()
        self.setFixedWidth(self.EXPANDED_WIDTH if expanded else self.COLLAPSED_WIDTH)

    def is_log_expanded(self) -> bool:
        # Tracked rather than read back from Qt: `isVisible()` is False while an
        # ancestor is hidden, so a minimised window flipped this and the toggle
        # then collapsed instead of expanding on the next click.
        return self._log_expanded

    def apply_progress(self, snapshot: object) -> None:
        kind = getattr(getattr(snapshot, "kind", None), "value", "idle")
        label = str(getattr(snapshot, "label", "设备就绪"))
        cancellable = bool(getattr(snapshot, "cancellable", False))
        self.stage_label.setText(label)
        self.stage_detail.setText("可取消" if cancellable else "等待选择操作" if kind == "idle" else "正在执行")
        self.cancel_button.setEnabled(cancellable)
        self.cancel_button.setVisible(cancellable)
        if kind == "waiting":
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(getattr(snapshot, "percent", 0) or 0))
