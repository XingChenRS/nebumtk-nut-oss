"""Opaque integrated frame for the main window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

WM_NCHITTEST = 0x0084


def native_to_qt_global(screen_x: int, screen_y: int, *, dpi: int) -> QPoint:
    """Convert physical WM_NCHITTEST coordinates to Qt logical coordinates."""

    safe_dpi = dpi if type(dpi) is int and dpi > 0 else 96
    return QPoint(
        int(round(screen_x * 96 / safe_dpi)),
        int(round(screen_y * 96 / safe_dpi)),
    )


def windows_resize_hit_test(
    position: QPoint,
    size: QSize,
    *,
    maximized: bool,
    border: int = 6,
) -> int | None:
    """Map a local pointer position to a native Windows resize edge."""

    if maximized or border <= 0 or size.width() <= 0 or size.height() <= 0:
        return None
    left = position.x() < border
    right = position.x() >= size.width() - border
    top = position.y() < border
    bottom = position.y() >= size.height() - border
    if top and left:
        return HTTOPLEFT
    if top and right:
        return HTTOPRIGHT
    if bottom and left:
        return HTBOTTOMLEFT
    if bottom and right:
        return HTBOTTOMRIGHT
    if left:
        return HTLEFT
    if right:
        return HTRIGHT
    if top:
        return HTTOP
    if bottom:
        return HTBOTTOM
    return None


def native_windows_resize_hit(widget: QWidget, message: object) -> int | None:
    """Decode one Windows hit-test message without affecting other platforms."""

    if sys.platform != "win32" or widget.isMaximized():
        return None
    try:
        address = int(message)
        native_message = wintypes.MSG.from_address(address)
        if native_message.message != WM_NCHITTEST:
            return None
        packed = int(native_message.lParam)
        screen_x = ctypes.c_short(packed & 0xFFFF).value
        screen_y = ctypes.c_short((packed >> 16) & 0xFFFF).value
        dpi = 96
        try:
            get_dpi = ctypes.windll.user32.GetDpiForWindow
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
            dpi = int(get_dpi(int(widget.winId()))) or 96
        except Exception:  # noqa: BLE001 - older Windows falls back to 96 DPI.
            dpi = 96
        global_pos = native_to_qt_global(screen_x, screen_y, dpi=dpi)
        local = widget.mapFromGlobal(global_pos)
        edge = windows_resize_hit_test(
            local,
            widget.size(),
            maximized=widget.isMaximized(),
        )
        if edge is None:
            return None
        # Never let the resize band swallow the window control buttons.
        title_bar = getattr(widget, "title_bar", None)
        control_hit = getattr(title_bar, "control_button_hit", None)
        if callable(control_hit) and control_hit(global_pos):
            return None
        return edge
    except Exception:  # noqa: BLE001 - native decoding must never raise into Qt.
        return None


class IntegratedTitleBar(QFrame):
    """Small title bar that delegates window behavior to the top-level host."""

    TITLE_BAR_HEIGHT = 38

    def __init__(self, host: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self.setObjectName("windowChrome")
        self.setFixedHeight(self.TITLE_BAR_HEIGHT)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        self.brand_mark = QLabel("N", self)
        self.brand_mark.setObjectName("windowBrandMark")
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_mark.setFixedSize(22, 22)
        layout.addWidget(self.brand_mark)

        self.title_label = QLabel("NEBUMTK · MTK TOOLBOX", self)
        self.title_label.setObjectName("windowTitle")
        layout.addWidget(self.title_label)
        layout.addStretch(1)

        self.minimize_button = self._control("−", "最小化窗口")
        self.maximize_button = self._control("□", "最大化窗口")
        self.close_button = self._control("×", "关闭窗口", close=True)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

        self.minimize_button.clicked.connect(self._host.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button.clicked.connect(self._host.close)
        self._host.installEventFilter(self)
        self.sync_window_state()

    def _control(self, text: str, name: str, *, close: bool = False) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("windowCloseControl" if close else "windowControl")
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFixedSize(44, self.TITLE_BAR_HEIGHT - 2)
        return button

    def toggle_maximized(self) -> None:
        if self._host.isMaximized():
            self._host.showNormal()
        else:
            self._host.showMaximized()
        self.sync_window_state()

    def sync_window_state(self) -> None:
        maximized = self._host.isMaximized()
        self.maximize_button.setText("❐" if maximized else "□")
        name = "还原窗口" if maximized else "最大化窗口"
        self.maximize_button.setAccessibleName(name)
        self.maximize_button.setToolTip(name)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if watched is self._host and event.type() == QEvent.Type.WindowStateChange:
            self.sync_window_state()
        return super().eventFilter(watched, event)

    def control_button_hit(self, global_pos: QPoint) -> bool:
        """Return True when a screen point lands on a window control button.

        Used by the top-level ``nativeEvent`` to exclude the minimize/maximize/
        close buttons from the native resize band so real Windows mouse clicks
        reach the buttons instead of being consumed by ``WM_NCHITTEST``.
        """

        buttons = (self.minimize_button, self.maximize_button, self.close_button)
        for button in buttons:
            if button is None or not button.isVisible():
                continue
            try:
                top_left = button.mapToGlobal(QPoint(0, 0))
                rect = QRect(top_left, button.size())
            except Exception:  # noqa: BLE001 - geometry mapping must be safe.
                continue
            if rect.contains(global_pos):
                return True
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        local_point = event.position().toPoint()
        # If the press lands on a child control (the window buttons), never
        # start a system move: hand the event to the button so its click works.
        if self.childAt(local_point) is not None:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and not self._host.isMaximized():
            global_point = event.globalPosition().toPoint()
            if self.control_button_hit(global_point):
                super().mousePressEvent(event)
                return
            edge = windows_resize_hit_test(
                local_point,
                self.size(),
                maximized=self._host.isMaximized(),
            )
            if edge is not None:
                # Sit on a resize edge; let the native hit-test drive resizing.
                super().mousePressEvent(event)
                return
            handle = self._host.windowHandle()
            start_move = getattr(handle, "startSystemMove", None) if handle else None
            if callable(start_move):
                start_move()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class IntegratedWindowSurface(QFrame):
    """Opaque title-bar and content composition for the main window."""

    def __init__(
        self,
        content: QWidget,
        host: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("windowSurface")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#F7F2E9"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#F7F2E9"))
        self.setPalette(palette)

        self.content = content
        self.title_bar = IntegratedTitleBar(host, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(content, 1)
