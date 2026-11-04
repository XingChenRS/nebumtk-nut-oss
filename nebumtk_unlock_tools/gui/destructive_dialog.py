"""One complete confirmation immediately before a destructive device action."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .pages import DestructiveSummary
from .theme import UiRole, apply_role, online_stylesheet
from .windows_backdrop import NativeWindowAppearance, schedule_native_window_chrome


class DestructiveDialog(QDialog):
    def __init__(
        self,
        summary: DestructiveSummary,
        *,
        action_text: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(summary, DestructiveSummary):
            raise TypeError("summary must be DestructiveSummary")
        if not isinstance(action_text, str) or not action_text.strip():
            raise ValueError("action text is required")
        self._summary = summary
        self.setWindowTitle("确认危险操作")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(online_stylesheet())
        schedule_native_window_chrome(self, NativeWindowAppearance.transient())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        title = QLabel("请核对最终写入事实", self)
        title.setObjectName("pageHeading")
        explanation = QLabel(
            "下面的信息来自已冻结的设备与来源快照。确认后将进入实际设备操作。",
            self,
        )
        explanation.setObjectName("sectionDescription")
        explanation.setWordWrap(True)
        self.summary_label = QLabel(self._summary.render(), self)
        self.summary_label.setObjectName("inlineStatus")
        self.summary_label.setProperty("state", "warning")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(title)
        layout.addWidget(explanation)
        layout.addWidget(self.summary_label)

        self.buttons = QDialogButtonBox(self)
        self.confirm_button = self.buttons.addButton(
            action_text.strip(),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        apply_role(self.confirm_button, UiRole.DANGER)
        self.cancel_button = self.buttons.addButton(
            "取消",
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @classmethod
    def present(
        cls,
        parent: QWidget,
        summary: DestructiveSummary,
        *,
        action_text: str,
    ) -> bool:
        dialog = cls(summary, action_text=action_text, parent=parent)
        return dialog.exec() == QDialog.DialogCode.Accepted
