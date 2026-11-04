"""Small dependency-light widgets shared by workbench pages."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import UiRole, apply_role


class PolishedComboBox(QComboBox):
    """A native combo box with one consistent, non-platform chevron."""

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor("#655E56" if self.isEnabled() else "#AAA095")
        painter.setPen(QPen(color, 1.35))
        center_x = float(self.width() - 16)
        center_y = float(self.height() / 2)
        painter.drawLine(
            QPointF(center_x - 3.5, center_y - 1.5),
            QPointF(center_x, center_y + 2.0),
        )
        painter.drawLine(
            QPointF(center_x, center_y + 2.0),
            QPointF(center_x + 3.5, center_y - 1.5),
        )


class TaskSection(QFrame):
    def __init__(self, title: str, description: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("taskSection")
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(0, 14, 0, 15)
        self.content_layout.setSpacing(8)
        self.heading = QLabel(title, self)
        self.heading.setObjectName("sectionHeading")
        self.content_layout.addWidget(self.heading)
        self.description = QLabel(description, self)
        self.description.setObjectName("sectionDescription")
        self.description.setWordWrap(True)
        self.description.setVisible(bool(description))
        self.content_layout.addWidget(self.description)


def action_button(
    text: str,
    object_name: str,
    *,
    role: UiRole = UiRole.SECONDARY,
) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(object_name)
    apply_role(button, role)
    return button


def path_row(edit: QLineEdit, browse: QPushButton) -> QWidget:
    container = QWidget()
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(edit, 1)
    row.addWidget(browse)
    return container
