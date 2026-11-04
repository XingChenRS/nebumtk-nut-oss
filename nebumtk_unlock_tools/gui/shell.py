# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Dependency-light responsive shell for the task workbench."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import (
    QByteArray,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


PAGE_LABELS = {
    "device": "设备",
    "partitions": "分区与备份",
    "authorized": "Root 与解锁",
    "images": "镜像与恢复",
    "advanced": "工具与设置",
}
COMPACT_LABELS = {
    "device": "",
    "partitions": "",
    "authorized": "",
    "images": "",
    "advanced": "",
}
NAV_ICON_PATHS = {
    "device": '<rect width="14" height="20" x="5" y="2" rx="2"/><path d="M12 18h.01"/>',
    "partitions": '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/><path d="M2 12h20M6 16h.01M10 16h.01"/>',
    "authorized": '<rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
    "images": '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
    "advanced": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M2 14h4M10 8h4M18 16h4"/>',
}


def _line_icon(paths: str) -> QIcon:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="#514A43" stroke-width="1.7" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f"{paths}</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class ResponsiveShell(QWidget):
    width_changed = Signal(int)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.width_changed.emit(event.size().width())


class OnlineShell(ResponsiveShell):
    NAV_EXPANDED_WIDTH = 176
    NAV_COMPACT_WIDTH = 56
    NAV_BREAKPOINT = 1100

    def __init__(
        self,
        page_widgets: Mapping[str, QWidget],
        activity_panel: QWidget,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if tuple(page_widgets) != tuple(PAGE_LABELS):
            raise ValueError("shell requires the five canonical pages in order")
        self.setObjectName("onlineShell")
        self.setMinimumSize(1024, 680)
        self.resize(1360, 820)
        self.page_widgets = dict(page_widgets)
        self.page_scroll_areas: dict[str, QScrollArea] = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.navigation_rail = QFrame(self)
        self.navigation_rail.setObjectName("navigationRail")
        nav = QVBoxLayout(self.navigation_rail)
        nav.setContentsMargins(10, 16, 10, 12)
        nav.setSpacing(5)
        self.wordmark = QLabel("NEBUMTK", self.navigation_rail)
        self.wordmark.setObjectName("wordmark")
        nav.addWidget(self.wordmark)
        nav.addSpacing(10)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}

        self.workbench = QWidget(self)
        self.workbench.setObjectName("workbench")
        workbench_layout = QVBoxLayout(self.workbench)
        workbench_layout.setContentsMargins(18, 18, 18, 18)
        workbench_layout.setSpacing(0)

        self.pages = QStackedWidget(self.workbench)
        for surface in (self.workbench, self.pages):
            surface.setAutoFillBackground(True)
            palette = surface.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#F8F4ED"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#F8F4ED"))
            surface.setPalette(palette)
        for index, (key, page) in enumerate(self.page_widgets.items()):
            button = QPushButton(PAGE_LABELS[key], self.navigation_rail)
            button.setObjectName("navButton")
            button.setAccessibleName(PAGE_LABELS[key])
            button.setToolTip(PAGE_LABELS[key])
            button.setIcon(_line_icon(NAV_ICON_PATHS[key]))
            button.setIconSize(QSize(16, 16))
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, page_index=index: self.pages.setCurrentIndex(page_index)
            )
            self.nav_group.addButton(button, index)
            self.nav_buttons[key] = button
            nav.addWidget(button)
            area = self._scrollable(page, key)
            self.page_scroll_areas[key] = area
            self.pages.addWidget(area)
        nav.addStretch(1)
        self.nav_buttons["device"].setChecked(True)

        self.activity_panel = activity_panel
        self.activity_dock = activity_panel
        self._activity_layout_user_set = False
        workbench_layout.addWidget(self.pages, 1)

        root.addWidget(self.navigation_rail)
        root.addWidget(self.workbench, 1)
        root.addWidget(self.activity_dock)
        self.pages.currentChanged.connect(self._sync_navigation)
        self.width_changed.connect(self._apply_width)
        toggle = getattr(self.activity_dock, "toggle_button", None)
        if toggle is not None:
            # Connected after the panel's own handler, so the dock has already
            # applied the new state by the time this records who chose it.
            toggle.clicked.connect(self._mark_activity_layout_user_set)
        self._apply_width(self.width())

    def _mark_activity_layout_user_set(self) -> None:
        self._activity_layout_user_set = True

    def _sync_navigation(self, index: int) -> None:
        button = self.nav_group.button(index)
        if button is not None:
            button.setChecked(True)

    def _apply_width(self, width: int) -> None:
        self.set_compact_navigation(width < self.NAV_BREAKPOINT)
        # The window width only chooses the dock's *starting* state. Re-applying
        # it on every resize fought the operator: expanding the dock widened the
        # layout, that changed the width, and the panel was collapsed again on
        # the spot — so once it went in, it could not be brought back out.
        if self._activity_layout_user_set:
            return
        setter = getattr(self.activity_dock, "set_log_expanded", None)
        if callable(setter):
            setter(width >= self.NAV_BREAKPOINT)

    def set_compact_navigation(self, compact: bool) -> None:
        self.navigation_rail.setFixedWidth(
            self.NAV_COMPACT_WIDTH if compact else self.NAV_EXPANDED_WIDTH
        )
        self.wordmark.setText("N" if compact else "NEBUMTK")
        self.wordmark.setAlignment(
            Qt.AlignmentFlag.AlignCenter if compact else Qt.AlignmentFlag.AlignLeft
        )
        for key, button in self.nav_buttons.items():
            button.setText(COMPACT_LABELS[key] if compact else PAGE_LABELS[key])
            button.setStyleSheet("text-align:center;" if compact else "")

    @staticmethod
    def _scrollable(page: QWidget, key: str) -> QScrollArea:
        area = QScrollArea()
        area.setObjectName(f"{key}ScrollArea")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        area.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        area.setAutoFillBackground(True)
        area.viewport().setAutoFillBackground(True)
        page.setAutoFillBackground(True)
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for widget in (area, area.viewport(), page):
            palette = widget.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#F8F4ED"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#F8F4ED"))
            widget.setPalette(palette)
        area.setWidget(page)
        return area
