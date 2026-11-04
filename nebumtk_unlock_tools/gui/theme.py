# SPDX-License-Identifier: AGPL-3.0-only
"""Shared semantic theme for the workbench and its dialogs."""

from __future__ import annotations

from enum import Enum

from PySide6.QtWidgets import QWidget


class UiRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    DANGER = "danger"
    QUIET = "quiet"


def apply_role(widget: QWidget, role: UiRole) -> None:
    """Style an action semantically without taking over its stable object name."""

    if not isinstance(role, UiRole):
        raise TypeError("role must be a UiRole")
    widget.setProperty("uiRole", role.value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def online_stylesheet() -> str:
    """Return the restrained shared workbench/dialog stylesheet."""

    return """
        QFrame#windowSurface {
            background:#F7F2E9; border:1px solid #C7BAAA; border-radius:0;
        }
        QFrame#windowChrome {
            background:#EEE7DC; border:0; border-bottom:1px solid #D1C5B7;
        }
        QLabel#windowBrandMark {
            background:#302C28; color:#FFF9F0; border-radius:5px;
            font-family:'Georgia','SimSun'; font-size:13px; font-weight:700;
        }
        QLabel#windowTitle {
            color:#3B3631; font-size:12px; font-weight:600; letter-spacing:0.3px;
        }
        QPushButton#windowControl, QPushButton#windowCloseControl {
            background:transparent; color:#514A43; border:0; border-radius:0;
            min-height:36px; max-height:36px; padding:0; font-size:16px;
        }
        QPushButton#windowControl:hover { background:#E2D8CA; color:#2B2825; }
        QPushButton#windowControl:pressed { background:#D7CABB; }
        QPushButton#windowCloseControl:hover { background:#B64835; color:#FFFFFF; }
        QPushButton#windowCloseControl:pressed { background:#963A2C; color:#FFFFFF; }
        QWidget#onlineShell, QDialog {
            background:#F7F2E9; color:#2C2926;
            font-family:'Noto Sans SC','Microsoft YaHei UI','Segoe UI'; font-size:13px;
        }
        QWidget#onlinePage { background:transparent; }
        QLabel, QCheckBox, QRadioButton { color:#2B2925; }
        QToolTip { background:#242320; color:#FAF7F0; border:0; padding:5px 7px; }
        QFrame#navigationRail {
            background:#EEE7DC; border:0; border-right:1px solid #D8CEC0;
        }
        QLabel#wordmark {
            color:#2A2724; font-family:'Georgia','SimSun';
            font-size:20px; font-weight:500; padding-left:10px;
        }
        QPushButton#navButton {
            background:transparent; color:#332F2B; border:0; border-radius:7px;
            min-height:38px; text-align:left; padding:0 12px; font-weight:500;
        }
        QPushButton#navButton:hover { background:#E7DED1; }
        QPushButton#navButton:checked {
            background:#E2D8CA; color:#2D2925; border:0;
            border-left:3px solid #D36345;
        }
        QFrame#deviceHeader, QFrame#recommendedCard, QFrame#taskPanel,
        QFrame#imageTaskPanel, QFrame#summaryLine {
            background:#FFFAF3; border:1px solid #D9D0C4; border-radius:8px;
        }
        QFrame#taskSection, QFrame#manualSection {
            background:transparent; border:0; border-bottom:1px solid #D9D0C4;
            border-radius:0;
        }
        QFrame#recommendedCard {
            background:#FFF9F0; border-color:#D7B6A7;
        }
        QFrame#activityDock, QFrame#activityBody, QFrame#activityHeader,
        QFrame#supportSection {
            background:#F0E9DE; border:0; border-radius:0;
        }
        QFrame#activityDock { border-left:1px solid #D3C9BB; }
        QFrame#activityHeader, QFrame#supportSection {
            border-bottom:1px solid #D3C9BB;
        }
        QLabel#headerCaption, QLabel#fieldCaption { color:#776F65; font-size:11px; }
        QLabel#headerValue { color:#2B2925; font-weight:650; }
        QLabel#pageHeading {
            color:#27231F; font-family:'Georgia','SimSun';
            font-size:24px; font-weight:500;
        }
        QLabel#sectionHeading, QLabel#recommendedTitle {
            color:#27231F; font-family:'Georgia','SimSun';
            font-size:16px; font-weight:500;
        }
        QLabel#sectionDescription { color:#655F57; }
        QLabel#summaryTitle {
            color:#2B2925; font-size:16px; font-weight:600;
        }
        QLabel#eyebrow { color:#B14F3E; font-size:11px; font-weight:700; }
        QLabel#impactSummary {
            background:#FFF9F0; color:#433C36; border:1px solid #DDCFC2;
            border-radius:6px; padding:9px 10px; font-weight:600;
        }
        QLabel#actionTitle, QLabel#manualGroupHeading {
            color:#35312D; font-weight:650;
        }
        QLabel#manualGroupHeading { margin-top:4px; font-size:14px; }
        QLabel#actionContext, QLabel#actionState, QLabel#mutedText {
            color:#756D63; font-size:12px;
        }
        QLabel#successText { color:#3F6844; font-weight:600; }
        QLabel#outputSummary {
            background:#F0EADF; color:#4F4942; border-radius:6px; padding:8px 10px;
        }
        QFrame#manualAction {
            background:transparent; border:0; border-top:1px solid #D9D0C4;
            border-radius:0;
        }
        QFrame#diagnosticCell {
            background:#FAF5EC; border:1px solid #E5DBCE; border-radius:7px;
        }
        QFrame#deviceFactCell {
            background:#FAF5EC; border:1px solid #E5DBCE; border-radius:7px;
        }
        QLabel#deviceFactValue {
            color:#302C28; font-size:13px; font-weight:600;
        }
        QFrame#taskTabs {
            background:transparent; border:0; border-bottom:1px solid #D8CEC0;
            border-radius:0;
        }
        QPushButton#taskTab {
            background:transparent; border:0; border-radius:0;
            min-height:32px; color:#776D63;
        }
        QPushButton#taskTab:checked {
            background:transparent; color:#2D2925;
            border:0; border-bottom:2px solid #B64835;
        }
        QFrame#advancedOptions {
            background:#F3EDE4; border:0; border-radius:7px;
        }
        QLabel#inlineStatus, QLabel[state] {
            background:#F0EADF; color:#4F4942; border-radius:6px; padding:7px 9px;
        }
        QLabel[state="success"] { background:#E7EFE5; color:#365B3B; }
        QLabel[state="warning"] { background:#F5E7C8; color:#725520; }
        QLabel[state="error"] { background:#F3DDD7; color:#8E352C; }
        QLabel[state="info"] { background:#EEE6D8; color:#5A5148; }
        QLineEdit, QComboBox, QTableWidget, QTextEdit {
            background:#FFFAF3; color:#2B2925; border:1px solid #CFC4B7;
            border-radius:7px; min-height:32px; padding:2px 9px;
            selection-background-color:#E9C8BE; selection-color:#2B2925;
        }
        QLineEdit:read-only { background:#F4EEE5; color:#655F57; }
        QComboBox { padding:0 34px 0 10px; }
        QComboBox::drop-down {
            subcontrol-origin:padding; subcontrol-position:top right;
            width:32px; border:0; border-left:1px solid #DED3C6;
            background:#F2EADF; border-top-right-radius:7px;
            border-bottom-right-radius:7px;
        }
        QComboBox::drop-down:hover { background:#E9DED1; }
        QComboBox::down-arrow { image:none; width:10px; height:7px; }
        QComboBox QAbstractItemView {
            background:#FFFAF3; color:#2B2925; border:1px solid #CFC4B7;
            outline:0; padding:5px; selection-background-color:#F0DDD5;
            selection-color:#2B2925;
        }
        QComboBox QAbstractItemView::item { min-height:30px; padding:3px 8px; }
        QLineEdit:focus, QComboBox:focus, QTableWidget:focus, QTextEdit:focus {
            border:1px solid #C65D47;
        }
        QTableWidget {
            background:#FFFAF3; alternate-background-color:#F7F0E6;
            gridline-color:#DDD2C5; padding:0;
        }
        QTableWidget::item { min-height:28px; padding:4px 7px; }
        QTableWidget::item:alternate { background:#FAF5EC; }
        QTableWidget::item:selected { background:#EFD6CD; color:#2B2925; }
        QHeaderView::section {
            background:#F0E9DE; color:#5E554D; border:0;
            border-right:1px solid #D8CDBF; border-bottom:1px solid #CFC2B2;
            min-height:30px; padding:0 7px; font-weight:600;
        }
        QPushButton {
            background:#FFFAF3; color:#3A3530; border:1px solid #C9BCAF;
            border-radius:7px; min-height:30px; padding:0 11px; font-weight:600;
        }
        QPushButton:hover { border-color:#C65D47; background:#F8ECE6; }
        QPushButton:pressed { background:#EED8CF; }
        QPushButton:focus { border:1px solid #A84938; }
        QPushButton:disabled { background:#EEE8DE; color:#A59A8D; border-color:#DED4C8; }
        QPushButton[uiRole="primary"] {
            color:#FFFFFF; background:#302C28; border-color:#302C28;
        }
        QPushButton[uiRole="primary"]:hover { background:#48413B; }
        QPushButton[uiRole="danger"] {
            color:#FFFFFF; background:#B64835; border-color:#B64835;
        }
        QPushButton[uiRole="danger"]:hover { background:#9E3D2E; }
        QPushButton[uiRole="quiet"] { background:transparent; border-color:transparent; }
        QProgressBar {
            background:#E3D9CC; color:#3A3530; border:0; border-radius:3px;
            text-align:center;
            min-height:6px; max-height:6px;
        }
        QProgressBar::chunk { background:#C65D47; border-radius:3px; }
        QTextEdit#activityLog {
            background:transparent; border:0; border-radius:0;
            font-family:'Cascadia Mono','Consolas'; font-size:11px;
            color:#776D63; padding:0;
        }
        QLabel#supportCaption { color:#776D63; font-size:12px; }
        QLabel#supportValue { color:#302C28; font-size:12px; }
        QLabel#supportDeviceTitle { color:#27231F; font-size:14px; font-weight:600; }
        QLabel#settingsTitle {
            color:#27231F; font-family:'Georgia','SimSun';
            font-size:21px; font-weight:500;
        }
        QLabel#devicePrimaryValue { color:#27231F; font-size:14px; font-weight:600; }
        QLabel#deviceInlineValue { color:#776D63; font-size:12px; }
        QScrollArea { background:#F8F4ED; border:0; }
        QDialogButtonBox QPushButton { min-width:88px; }
    """
