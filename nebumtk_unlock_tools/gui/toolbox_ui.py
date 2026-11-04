"""Native Fluent-style presentation layer for the NEBUMTK toolbox."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import (
    QEvent,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


class WorkspaceScrollArea(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self._hosted_page: QWidget | None = None

    def setWidget(self, widget: QWidget) -> None:
        if self._hosted_page is not None:
            self._hosted_page.removeEventFilter(self)
            for child in self._hosted_page.findChildren(QWidget):
                child.removeEventFilter(self)
        self._hosted_page = widget
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
        super().setWidget(widget)
        self._sync_page_height()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        handled = super().eventFilter(watched, event)
        if event.type() in (QEvent.LayoutRequest, QEvent.Resize):
            hosted_page = self._hosted_page
            if watched is hosted_page or (isinstance(watched, QWidget) and hosted_page is not None and hosted_page.isAncestorOf(watched)):
                self._sync_page_height()
        return handled

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._sync_page_height()

    def _sync_page_height(self) -> None:
        page = self._hosted_page
        if page is None:
            return
        layout = page.layout()
        width = self.viewport().width()
        if layout is None or width <= 0:
            return
        height = layout.heightForWidth(width) if layout.hasHeightForWidth() else page.sizeHint().height()
        page.setMinimumHeight(max(height, self.viewport().height()))
        page.resize(width, max(height, self.viewport().height()))


class ActivityInspector(QFrame):
    BREAKPOINT = 1160
    EXPANDED_WIDTH = 288
    COLLAPSED_WIDTH = 42

    def __init__(self, body: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = False
        self._wide_mode: bool | None = None
        self._body = body
        self.toggle_button = QPushButton("›")
        self.toggle_button.setObjectName("activityToggle")
        self.toggle_button.setFixedWidth(self.COLLAPSED_WIDTH)
        self.toggle_button.setToolTip("展开活动与日志")
        self.toggle_button.clicked.connect(
            lambda: self.set_expanded(True)
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(body, 1)
        self.set_expanded(False, animate=False)

    def is_expanded(self) -> bool:
        return self._expanded

    def apply_window_width(self, width: int, *, animate: bool = True) -> None:
        wide = width >= self.BREAKPOINT
        if wide == self._wide_mode:
            return
        self._wide_mode = wide
        self.set_expanded(wide, animate=animate)

    def set_expanded(self, expanded: bool, *, animate: bool = True) -> None:
        del animate
        self._expanded = expanded
        target = self.EXPANDED_WIDTH if expanded else self.COLLAPSED_WIDTH
        self._body.setVisible(expanded)
        self.toggle_button.setVisible(not expanded)
        self.setMinimumWidth(target)
        self.setMaximumWidth(target)
        parent = self.parentWidget()
        if isinstance(parent, QSplitter) and parent.width() > target:
            workspace_width = max(
                0,
                parent.width() - target - parent.handleWidth(),
            )
            parent.setSizes([workspace_width, target])


class ResponsiveToolboxShell(QWidget):
    widthChanged = Signal(int)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.widthChanged.emit(event.size().width())


class DisclosurePanel(QFrame):
    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        expanded: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("disclosurePanel")
        self.content_widget = content
        self.header_button = QPushButton(title)
        self.header_button.setObjectName("disclosureHeader")
        self.header_button.setCheckable(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header_button)
        layout.addWidget(content)
        self.header_button.toggled.connect(self.set_expanded)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.header_button.blockSignals(True)
        self.header_button.setChecked(expanded)
        self.header_button.blockSignals(False)
        self.content_widget.setVisible(expanded)


class ToolboxUi:
    """Composes existing MainWindow controls into a dense native toolbox."""

    _cjk_font_family: str | None = None

    def __init__(self, window: "MainWindow") -> None:
        self.window = window

    def build(self) -> None:
        window = self.window
        window._toolbox_ui = self
        shell = ResponsiveToolboxShell()
        shell.setObjectName("toolboxShell")
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._navigation())
        window.pages = QStackedWidget()
        window.page_scroll_areas = {}
        window.page_widgets = {}
        window.pages.setObjectName("workspaceStack")
        activity = ActivityInspector(self._activity_panel())
        window.activity_inspector = activity
        window.activity_toggle_button = activity.toggle_button
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("workspaceActivitySplit")
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(window.pages)
        splitter.addWidget(activity)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([780, ActivityInspector.EXPANDED_WIDTH])
        root.addWidget(splitter, 1)
        window.setCentralWidget(shell)

        pages: tuple[tuple[str, str, Callable[[], QWidget]], ...] = (
            ("设备概览", "overview", self._overview_page),
            ("预备流程", "preparation", self._preparation_page),
            ("Fastboot 分区", "fastboot", self._fastboot_page),
            ("品牌功能", "brands", self._brands_page),
            ("高级工具", "advanced", self._advanced_page),
        )
        nav = self.navigation_items
        for index, (label, key, builder) in enumerate(pages):
            button = QPushButton(label)
            button.setObjectName("sideNavButton")
            button.setCheckable(True)
            window.nav_group.addButton(button, index)
            button.clicked.connect(lambda _checked, current=index: window.pages.setCurrentIndex(current))
            window.nav_buttons[key] = button
            nav.addWidget(button)
            page = builder()
            window.pages.addWidget(self._scrollable_page(page, key))
        window.nav_buttons["overview"].setChecked(True)
        self.apply_theme()
        shell.widthChanged.connect(self._apply_responsive_width)
        window.pages.currentChanged.connect(self._refresh_current_page)
        self._apply_responsive_width(window.width(), animate=False)

    def _apply_responsive_width(self, width: int, *, animate: bool = True) -> None:
        self.window.activity_inspector.apply_window_width(width, animate=animate)
        self.apply_responsive_metrics(width)

    def apply_responsive_metrics(self, width: int) -> None:
        margin = 30 if width >= ActivityInspector.BREAKPOINT else 24
        for page in self.window.page_widgets.values():
            layout = page.layout()
            if layout is not None:
                layout.setContentsMargins(margin, 24, margin, 24)

    def _refresh_current_page(self, index: int) -> None:
        page = self.window.pages.widget(index)
        if page is None:
            return
        button = self.window.nav_group.button(index)
        if button is not None:
            button.setChecked(True)
        page.setGraphicsEffect(None)
        page.update()
        page.repaint()
        viewport = getattr(page, "viewport", lambda: None)()
        if viewport is not None:
            viewport.update()
            viewport.repaint()

    def _scrollable_page(self, page: QWidget, key: str) -> QScrollArea:
        scroll_area = WorkspaceScrollArea()
        scroll_area.setObjectName(f"{key}ScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setSizeAdjustPolicy(QAbstractScrollArea.AdjustIgnored)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setAutoFillBackground(True)
        scroll_area.viewport().setAutoFillBackground(True)
        page.setAutoFillBackground(True)
        for widget in (scroll_area, scroll_area.viewport(), page):
            palette = widget.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#FBFCFC"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#FBFCFC"))
            widget.setPalette(palette)
        page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        scroll_area.setWidget(page)
        self.window.page_widgets[key] = page
        self.window.page_scroll_areas[key] = scroll_area
        return scroll_area

    def _navigation(self) -> QFrame:
        window = self.window
        rail = QFrame()
        rail.setObjectName("sideRail")
        rail.setFixedWidth(204)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(6)
        self.nav_layout = layout
        self.navigation_items = QVBoxLayout()
        self.navigation_items.setSpacing(4)

        wordmark = QLabel("NEBUMTK")
        wordmark.setObjectName("wordmark")
        layout.addWidget(wordmark)
        descriptor = QLabel("DEVICE OPERATIONS")
        descriptor.setObjectName("descriptor")
        layout.addWidget(descriptor)
        layout.addSpacing(14)
        layout.addLayout(self.navigation_items)

        window.nav_group = QButtonGroup(window)
        window.nav_group.setExclusive(True)
        window.nav_buttons = {}

        device_card = QFrame()
        device_card.setObjectName("railDeviceCard")
        device_layout = QVBoxLayout(device_card)
        device_layout.setContentsMargins(11, 11, 11, 11)
        device_layout.setSpacing(7)
        device_title = QLabel("设备")
        device_title.setObjectName("railLabel")
        device_layout.addWidget(device_title)
        window.status_label.setObjectName("railDeviceState")
        window.status_label.setWordWrap(True)
        device_layout.addWidget(window.status_label)
        device_layout.addLayout(self._field("目标机型", window.model_combo))
        scan_row = QHBoxLayout()
        window.refresh_button.setObjectName("secondaryAction")
        window.cancel_button.setObjectName("railCancelButton")
        scan_row.addWidget(window.refresh_button, 1)
        scan_row.addWidget(window.cancel_button)
        device_layout.addLayout(scan_row)
        layout.addWidget(device_card)

        # Test mode changes the safety boundary for every page, so its warning
        # must remain visible independently of the selected navigation page.
        layout.addWidget(window.test_mode_banner)

        layout.addStretch(1)
        utility = QLabel("工具")
        utility.setObjectName("railLabel")
        layout.addWidget(utility)
        for button in (window.settings_button, window.open_backup_button, window.open_logs_button):
            button.setObjectName("railUtility")
            layout.addWidget(button)
        return rail

    def _page(self, title: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("toolboxPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("pageHeading")
        layout.addWidget(title_label)
        return page, layout

    @staticmethod
    def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("sectionPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        return frame, layout

    @staticmethod
    def _field(label: str, control: QWidget) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(4)
        caption = QLabel(label)
        caption.setObjectName("fieldLabel")
        column.addWidget(caption)
        column.addWidget(control)
        return column

    def _overview_page(self) -> QWidget:
        window = self.window
        page, layout = self._page("设备信息概况")

        detected, detected_layout = self._section("检测到的设备")
        for label in (
            window.detected_connection_label,
            window.detected_identity_label,
            window.detected_firmware_label,
            window.detected_slot_label,
            window.detected_profile_label,
        ):
            detected_layout.addWidget(label)
        layout.addWidget(detected)

        reboot, reboot_layout = self._section("设备重启")
        reboot_row = QGridLayout()
        reboot_row.setHorizontalSpacing(8)
        reboot_row.setVerticalSpacing(8)
        for index, button in enumerate((
            window.reboot_system_button,
            window.reboot_fastbootd_button,
            window.reboot_bootloader_button,
            window.reboot_recovery_button,
        )):
            button.setObjectName("secondaryAction")
            reboot_row.addWidget(button, index // 2, index % 2)
        reboot_layout.addLayout(reboot_row)
        layout.addWidget(reboot)

        state, state_layout = self._section("当前操作")
        state_layout.addWidget(window.backup_path_label)
        window.current_operation_panel = state
        state.setVisible(False)
        layout.addWidget(state)
        layout.addStretch(1)
        return page

    def _preparation_page(self) -> QWidget:
        window = self.window
        page, layout = self._page("预备流程")

        selected, selected_layout = self._section("选中 patched LK")
        patched = QHBoxLayout()
        patched.addLayout(self._field("patched LK", window.patched_lk_edit), 1)
        window.patched_lk_browse.setObjectName("secondaryAction")
        patched.addWidget(window.patched_lk_browse, alignment=Qt.AlignBottom)
        selected_layout.addLayout(patched)
        layout.addWidget(selected)

        stages, stages_layout = self._section("系统下分区操作")
        row = QHBoxLayout()
        for button in (window.root_button, window.backup_button, window.write_button):
            button.setObjectName("commitAction" if button is window.write_button else "secondaryAction")
            row.addWidget(button)
        stages_layout.addLayout(row)
        stages_layout.addWidget(window.progress_bar)
        stages_layout.addWidget(window.step_label)
        layout.addWidget(stages)
        layout.addStretch(1)
        return page

    def _fastboot_page(self) -> QWidget:
        window = self.window
        page, layout = self._page("Fastboot 分区")

        sync, sync_layout = self._section("双槽同步")
        patched = QHBoxLayout()
        patched.addLayout(self._field("选中 patched LK", window.fastboot_patched_lk_edit), 1)
        window.fastboot_patched_lk_browse.setObjectName("secondaryAction")
        patched.addWidget(window.fastboot_patched_lk_browse, alignment=Qt.AlignBottom)
        sync_layout.addLayout(patched)
        window.dual_slot_sync_button.setObjectName("commitAction")
        sync_layout.addWidget(window.dual_slot_sync_button)
        layout.addWidget(sync)

        restore, restore_layout = self._section("还原双槽原 LK")
        source = QHBoxLayout()
        source.addLayout(self._field("桌面备份 LK", window.restore_lk_edit), 1)
        window.restore_lk_browse.setObjectName("secondaryAction")
        source.addWidget(window.restore_lk_browse, alignment=Qt.AlignBottom)
        restore_layout.addLayout(source)
        window.restore_backup_lk_button.setObjectName("commitAction")
        restore_layout.addWidget(window.restore_backup_lk_button)
        layout.addWidget(restore)
        layout.addStretch(1)
        return page

    def _brands_page(self) -> QWidget:
        window = self.window
        page, layout = self._page("品牌功能")

        workflow, workflow_layout = self._section("Vivo 完整解锁")
        window.auto_flow_button.setObjectName("primaryAction")
        workflow_layout.addWidget(window.auto_flow_button)
        window.vivo_preflight_label = QLabel("等待设备与配置检查")
        window.vivo_preflight_label.setObjectName("inlineStatus")
        workflow_layout.addWidget(window.vivo_preflight_label)
        layout.addWidget(workflow)

        local_patch, local_patch_layout = self._section("Vivo LK 修补")
        external_source = QHBoxLayout()
        external_source.addLayout(self._field("外部原始 LK", window.source_lk_edit), 1)
        window.source_lk_browse.setObjectName("secondaryAction")
        external_source.addWidget(window.source_lk_browse, alignment=Qt.AlignBottom)
        local_patch_layout.addLayout(external_source)
        external_output = QHBoxLayout()
        external_output.addLayout(self._field("外部 patched LK 输出", window.output_lk_edit), 1)
        window.output_lk_browse.setObjectName("secondaryAction")
        external_output.addWidget(window.output_lk_browse, alignment=Qt.AlignBottom)
        local_patch_layout.addLayout(external_output)
        window.patch_lk_button.setObjectName("primaryAction")
        external_actions = QHBoxLayout()
        window.inspect_lk_button.setObjectName("secondaryAction")
        external_actions.addWidget(window.inspect_lk_button)
        external_actions.addWidget(window.patch_lk_button)
        local_patch_layout.addLayout(external_actions)
        window.image_status_label = QLabel("等待选择或检测 LK")
        window.image_status_label.setObjectName("inlineStatus")
        local_patch_layout.addWidget(window.image_status_label)
        layout.addWidget(local_patch)

        root, root_layout = self._section("Root 镜像管理（SakiSU 4.3）")
        init_source = QHBoxLayout()
        init_source.addLayout(self._field("本地原始 init_boot", window.source_init_boot_edit), 1)
        window.source_init_boot_browse.setObjectName("secondaryAction")
        init_source.addWidget(window.source_init_boot_browse, alignment=Qt.AlignBottom)
        root_layout.addLayout(init_source)
        init_output = QHBoxLayout()
        init_output.addLayout(self._field("patched init_boot 输出", window.output_init_boot_edit), 1)
        window.output_init_boot_browse.setObjectName("secondaryAction")
        init_output.addWidget(window.output_init_boot_browse, alignment=Qt.AlignBottom)
        root_layout.addLayout(init_output)
        window.patch_init_boot_button.setObjectName("primaryAction")
        root_layout.addWidget(window.patch_init_boot_button)
        root_layout.addWidget(
            QLabel("本地修补仅通过 ADB 读取 KMI；不会刷写设备，也不会内置或安装 Manager。")
        )
        root_layout.addWidget(QLabel("Vivo 双槽 Flash 收尾可选项"))
        root_layout.addWidget(window.sakisu_root_checkbox)
        layout.addWidget(root)

        dyn, dyn_layout = self._section("Vivo dyn 还原")
        dyn_source = QHBoxLayout()
        dyn_source.addLayout(self._field("桌面 dyn 备份目录", window.restore_dyn_backup_edit), 1)
        window.restore_dyn_backup_browse.setObjectName("secondaryAction")
        dyn_source.addWidget(window.restore_dyn_backup_browse, alignment=Qt.AlignBottom)
        dyn_layout.addLayout(dyn_source)
        window.restore_vivo_dyn_button.setObjectName("commitAction")
        dyn_layout.addWidget(window.restore_vivo_dyn_button)
        dyn_layout.addWidget(
            QLabel("请先在系统内安装并授权 Manager；工具只使用已有 su，还原后自动重启。")
        )
        layout.addWidget(dyn)

        vivo, vivo_layout = self._section("Vivo Xingc")
        window.vivo_xingc_button.setObjectName("commitAction")
        vivo_layout.addWidget(window.vivo_xingc_button)
        for button in (
            window.xingc_token_button,
            window.xingc_sign_button,
            window.xingc_auth_button,
            window.xingc_lock_button,
        ):
            button.setVisible(False)
        layout.addWidget(vivo)

        oppo, oppo_layout = self._section("OPPO")
        window.oppo_unlock_button.setObjectName("commitAction")
        oppo_layout.addWidget(window.oppo_unlock_button)
        layout.addWidget(oppo)
        layout.addStretch(1)
        return page

    def _advanced_page(self) -> QWidget:
        window = self.window
        page, layout = self._page("高级工具")
        window.advanced_disclosures = {}

        runtime, runtime_layout = self._section("测试模式运行配置")
        runtime_layout.addLayout(self._field("运行配置", window.runtime_combo))
        preload = QHBoxLayout()
        preload.addLayout(self._field("受保护 preload", window.preload_edit), 1)
        window.preload_browse.setObjectName("secondaryAction")
        preload.addWidget(window.preload_browse, alignment=Qt.AlignBottom)
        runtime_layout.addLayout(preload)
        window.test_runtime_panel = runtime
        runtime.setVisible(bool(getattr(window.settings, "test_mode_enabled", False)))
        layout.addWidget(runtime)

        terminal = QWidget()
        terminal_layout = QVBoxLayout(terminal)
        terminal_layout.setContentsMargins(14, 12, 14, 12)
        terminal_layout.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(window.cmd_edit, 1)
        window.cmd_edit.setObjectName("terminalInput")
        window.cmd_run_button.setObjectName("secondaryAction")
        row.addWidget(window.cmd_run_button)
        terminal_layout.addLayout(row)
        terminal_disclosure = DisclosurePanel("设备诊断", terminal, expanded=True)
        window.advanced_disclosures["terminal"] = terminal_disclosure
        layout.addWidget(terminal_disclosure)

        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(14, 12, 14, 12)
        workspace_layout.setSpacing(8)
        workspace_layout.addWidget(window.workspace_path_label)
        workspace_disclosure = DisclosurePanel("工作区与安全", workspace, expanded=False)
        window.advanced_disclosures["workspace"] = workspace_disclosure
        layout.addWidget(workspace_disclosure)
        layout.addStretch(1)
        return page

    def _activity_panel(self) -> QFrame:
        window = self.window
        panel = QFrame()
        panel.setObjectName("activityPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(10)
        heading = QLabel("活动与日志")
        heading.setObjectName("activityHeading")
        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch(1)
        collapse = QPushButton("收起")
        collapse.setObjectName("subtleButton")
        collapse.clicked.connect(
            lambda: window.activity_inspector.set_expanded(False)
        )
        window.activity_collapse_button = collapse
        header.addWidget(collapse)
        layout.addLayout(header)
        layout.addWidget(window.activity_stage_label)
        layout.addWidget(window.activity_progress)
        controls = QHBoxLayout()
        controls.addWidget(window.log_file_label)
        controls.addStretch(1)
        clear = QPushButton("清空视图")
        clear.setObjectName("subtleButton")
        clear.clicked.connect(window.log_view.clear)
        controls.addWidget(clear)
        layout.addLayout(controls)
        window.log_view.setObjectName("logOutput")
        layout.addWidget(window.log_view, 1)
        window.activity_panel = panel
        return panel

    def apply_theme(self) -> None:
        cjk_font = self._resolve_cjk_font()
        if cjk_font:
            self.window.setFont(QFont(cjk_font))
        ui_font_stack = ", ".join(
            f"'{family}'"
            for family in (cjk_font, "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI")
            if family
        )
        self.window.setStyleSheet(f"""
            QMainWindow, QWidget#toolboxShell {{ background: #F5F7F8; color: #17252D; font-family: {ui_font_stack}; font-size: 12px; }}
            QWidget#toolboxPage, QStackedWidget#workspaceStack {{ background: #FBFCFC; }}
            QSplitter#workspaceActivitySplit::handle {{ background: #D9E3E6; }}
            QFrame#sideRail {{ background: #EDF2F3; border-right: 1px solid #D9E3E6; }}
            QFrame#railDeviceCard, QFrame#sectionPanel, QFrame#disclosurePanel {{ background: #FFFFFF; border: 1px solid #D9E3E6; border-radius: 8px; }}
            QLabel#railDeviceState {{ background: #F5F7F8; border: 1px solid #D9E3E6; border-radius: 7px; color: #6D7F88; min-height: 36px; padding: 7px 8px; font-size: 11px; font-weight: 650; }}
            QLabel#railDeviceState[deviceState="adb"] {{ background: #DFF3ED; border-color: #B8DDCF; color: #216F58; }}
            QLabel#railDeviceState[deviceState="fastboot"], QLabel#railDeviceState[deviceState="busy"], QLabel#railDeviceState[deviceState="scanning"] {{ background: #E8F1F4; border-color: #B8CDD5; color: #126F91; }}
            QLabel#railDeviceState[deviceState="environment"] {{ background: #F8F0E2; border-color: #DFC48F; color: #805619; }}
            QPushButton#railCancelButton {{ min-height: 25px; padding: 0 8px; }}
            QFrame#sideRail QComboBox {{ min-height: 26px; padding: 5px 7px; font-size: 11px; }}
            QLabel#wordmark {{ color: #17252D; font-size: 19px; font-weight: 700; letter-spacing: 1.5px; }}
            QLabel#descriptor, QLabel#railLabel {{ color: #6D7F88; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
            QPushButton#sideNavButton {{ background: transparent; border: 0; border-radius: 6px; color: #17252D; min-height: 31px; padding: 0 12px; text-align: left; font-weight: 600; }}
            QPushButton#sideNavButton:hover {{ background: #DFF3ED; color: #216F58; }}
            QPushButton#sideNavButton:checked {{ background: #DFF3ED; color: #216F58; border-left: 3px solid #126F91; }}
            QPushButton#railUtility {{ background: #FFFFFF; border: 1px solid #D9E3E6; border-radius: 6px; color: #17252D; min-height: 27px; text-align: left; padding: 0 10px; }}
            QPushButton#railUtility:hover {{ background: #F5F7F8; border-color: #126F91; }}
            QLabel#pageHeading {{ color: #17252D; font-size: 22px; font-weight: 650; }}
            QLabel#pageHint, QLabel#sectionDetail, QLabel#fieldLabel, QLabel#stepLabel {{ color: #6D7F88; }}
            QLabel#fieldLabel {{ font-size: 11px; font-weight: 600; }}
            QLabel#sectionHeading {{ color: #17252D; font-size: 14px; font-weight: 650; }}
            QLabel#detectedDetail {{ color: #17252D; background: #F5F7F8; border-radius: 5px; padding: 6px 8px; }}
            QLabel#connectionState {{ background: #F5F7F8; border: 1px solid #D9E3E6; border-radius: 12px; color: #17252D; padding: 7px 10px; }}
            QLabel#flowStep, QLabel#activityStage {{ background: #F5F7F8; border: 1px solid #D9E3E6; border-radius: 6px; color: #17252D; min-height: 30px; padding: 6px 7px; font-weight: 650; }}
            QLabel#inlineStatus {{ border: 1px solid #D9E3E6; border-radius: 8px; padding: 8px 10px; color: #6D7F88; background: #F5F7F8; }}
            QLabel#inlineStatus[statusState="ready"] {{ border-color: #B8DDCF; color: #216F58; background: #DFF3ED; }}
            QLabel#inlineStatus[statusState="warning"] {{ border-color: #DFC48F; color: #805619; background: #F8F0E2; }}
            QLabel#inlineStatus[statusState="error"] {{ border-color: #D9A5A5; color: #B54747; background: #F8E8E8; }}
            QLineEdit, QComboBox, QTextEdit {{ background: #FFFFFF; border: 1px solid #D9E3E6; border-radius: 6px; color: #17252D; padding: 5px 7px; selection-background-color: #DFF3ED; }}
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{ border: 2px solid #126F91; }}
            QPushButton {{ background: #FFFFFF; border: 1px solid #D9E3E6; border-radius: 6px; color: #17252D; min-height: 28px; padding: 0 10px; font-weight: 600; }}
            QPushButton:hover {{ background: #F5F7F8; border-color: #126F91; }}
            QPushButton:pressed {{ background: #EDF2F3; }}
            QPushButton:disabled {{ background: #EDF2F3; border-color: #D9E3E6; color: #9AAAB1; }}
            QPushButton#primaryAction {{ background: #126F91; border-color: #126F91; color: #FFFFFF; }}
            QPushButton#primaryAction:hover {{ background: #0E5D79; border-color: #0E5D79; }}
            QPushButton#commitAction {{ background: #F8F0E2; border-color: #DFC48F; color: #805619; }}
            QPushButton#commitAction:hover {{ background: #F2E1BD; border-color: #C59D53; }}
            QPushButton#secondaryAction {{ background: #FFFFFF; border-color: #D9E3E6; color: #17252D; }}
            QPushButton#secondaryAction:hover {{ background: #F5F7F8; border-color: #126F91; }}
            QPushButton#primaryAction:disabled, QPushButton#commitAction:disabled, QPushButton#secondaryAction:disabled {{ background: #EDF2F3; border-color: #D9E3E6; color: #9AAAB1; }}
            QPushButton#subtleButton {{ color: #6D7F88; background: #FFFFFF; min-height: 25px; }}
            QPushButton#disclosureHeader {{ background: #FFFFFF; border: 0; border-bottom: 1px solid #D9E3E6; border-radius: 8px; color: #17252D; min-height: 36px; padding: 0 12px; text-align: left; }}
            QPushButton#disclosureHeader:checked {{ color: #126F91; }}
            QProgressBar#workflowProgress, QProgressBar#activityProgress {{ background: #EDF2F3; border: 1px solid #D9E3E6; border-radius: 5px; color: transparent; }}
            QProgressBar#workflowProgress {{ height: 9px; }}
            QProgressBar#workflowProgress::chunk, QProgressBar#activityProgress::chunk {{ background: #126F91; border-radius: 4px; }}
            QProgressBar#activityProgress {{ height: 7px; }}
            QCheckBox {{ color: #17252D; spacing: 8px; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; background: #FFFFFF; border: 1px solid #D9E3E6; border-radius: 4px; }}
            QCheckBox::indicator:checked {{ background: #126F91; border-color: #126F91; }}
            QCheckBox#restrictedMode {{ color: #805619; font-weight: 600; }}
            QFrame#activityPanel {{ background: #FFFFFF; border-left: 1px solid #D9E3E6; }}
            QLabel#activityHeading {{ color: #17252D; font-size: 16px; font-weight: 700; }}
            QLabel#activityDevice {{ color: #6D7F88; font-size: 11px; }}
            QLineEdit#terminalInput, QTextEdit#logOutput {{ font-family: 'Cascadia Mono', 'Consolas'; }}
            QTextEdit#logOutput {{ background: #FFFFFF; font-size: 12px; line-height: 1.35; }}
        """)

    @classmethod
    def _resolve_cjk_font(cls) -> str:
        if cls._cjk_font_family is not None:
            return cls._cjk_font_family

        cls._cjk_font_family = ""
        font_path = Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts" / "msyh.ttc"
        if not font_path.is_file():
            return cls._cjk_font_family

        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            return cls._cjk_font_family

        families = QFontDatabase.applicationFontFamilies(font_id)
        cls._cjk_font_family = next(
            (family for family in families if family == "Microsoft YaHei UI"),
            families[0] if families else "",
        )
        return cls._cjk_font_family
