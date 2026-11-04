# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractScrollArea,
    QFrame,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QWidget,
)
from shiboken6 import isValid

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.activity import ActivityEvent, ActivityKind
from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.gui import main_window as main_window_module
from nebumtk_unlock_tools.gui import device_scan_worker as scan_worker_module
from nebumtk_unlock_tools.gui import worker as worker_module
from nebumtk_unlock_tools.gui.device_scan_worker import DeviceScanResult, DeviceScanWorker
from nebumtk_unlock_tools.gui.main_window import MainWindow
from nebumtk_unlock_tools.gui.worker import FlowWorker
from nebumtk_unlock_tools.flows.base import FlowCallbacks, FlowContext
from nebumtk_unlock_tools.flows.stages import VivoCompleteUnlockFlow


PAGE_KEYS = ["overview", "preparation", "fastboot", "brands", "advanced"]


class DummySignal:
    def __init__(self) -> None:
        self.slots: list[object] = []

    def connect(self, slot) -> None:
        self.slots.append(slot)


class DummyOperationWorker:
    def __init__(self, operation: str = "") -> None:
        self.operation = operation
        self.flow_kind = operation
        self.log_message = DummySignal()
        self.progress_changed = DummySignal()
        self.activity_event = DummySignal()
        self.result_ready = DummySignal()
        self.finished_ok = DummySignal()
        self.finished_error = DummySignal()
        self.cancelled_signal = DummySignal()
        self.confirmation_requested = DummySignal()
        self.finished = DummySignal()
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def isRunning(self) -> bool:
        return self.started

    def cancel(self) -> None:
        self.cancelled = True

    def deleteLater(self) -> None:
        pass


def make_main_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "refresh_devices", lambda self, *, auto=False: None)
    return app, MainWindow(tmp_path, tmp_path / "workspace")


def destroy_window(app: QApplication, window: MainWindow) -> None:
    window.device_scan_timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


def section_panel_by_heading(page: QWidget, heading_text: str) -> QWidget:
    for label in page.findChildren(QLabel):
        if label.objectName() == "sectionHeading" and label.text() == heading_text:
            parent = label.parentWidget()
            assert parent is not None
            return parent
    raise AssertionError(f"section heading not found: {heading_text}")


def test_common_preparation_buttons_survive_window_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        for button in (window.root_button, window.backup_button, window.write_button):
            assert isValid(button)
            assert button.parent() is not None
    finally:
        destroy_window(app, window)


def test_reclassified_pages_are_registered_in_frame_free_scroll_areas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        assert list(window.page_widgets) == PAGE_KEYS
        assert list(window.page_scroll_areas) == PAGE_KEYS
        assert window.pages.count() == len(PAGE_KEYS)
        for index, key in enumerate(PAGE_KEYS):
            scroll_area = window.page_scroll_areas[key]
            assert isinstance(scroll_area, QScrollArea)
            assert scroll_area.widget() is window.page_widgets[key]
            assert window.pages.widget(index) is scroll_area
            assert scroll_area.widgetResizable()
            assert scroll_area.frameShape() == QScrollArea.NoFrame
            assert scroll_area.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
            assert scroll_area.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
            assert scroll_area.sizeAdjustPolicy() == QAbstractScrollArea.AdjustIgnored
    finally:
        destroy_window(app, window)


def test_window_panel_and_layout_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        assert (window.width(), window.height()) == (1200, 720)
        assert (window.minimumWidth(), window.minimumHeight()) == (900, 600)
        rail = window.findChild(QFrame, "sideRail")
        splitter = window.findChild(QSplitter, "workspaceActivitySplit")
        assert rail is not None and rail.minimumWidth() == rail.maximumWidth() == 204
        assert splitter is not None and not splitter.childrenCollapsible()
        assert window.activity_inspector.minimumWidth() == 288
        assert window.activity_inspector.maximumWidth() == 288

        panel = section_panel_by_heading(
            window.page_widgets["preparation"],
            "系统下分区操作",
        )
        margins = panel.layout().contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (14, 12, 14, 12)
        assert panel.layout().spacing() == 8
    finally:
        destroy_window(app, window)


@pytest.mark.parametrize("width,height", [(1200, 720), (1024, 680), (900, 600)])
@pytest.mark.parametrize("page_key", PAGE_KEYS)
def test_pages_have_no_horizontal_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page_key: str,
    width: int,
    height: int,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.resize(width, height)
        window.show()
        window.pages.setCurrentWidget(window.page_scroll_areas[page_key])
        app.processEvents()
        assert isValid(window.page_widgets[page_key])
        assert window.page_scroll_areas[page_key].horizontalScrollBar().maximum() == 0
    finally:
        destroy_window(app, window)


def test_progress_updates_preparation_and_activity_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.pages.setCurrentWidget(window.page_scroll_areas["preparation"])
        window.show()
        app.processEvents()
        window._on_progress(42, "正在修补 LK")
        assert window.progress_bar.value() == window.activity_progress.value() == 42
        assert window.step_label.text() == window.activity_stage_label.text() == "正在修补 LK"
        window._on_progress(42, "等待设备重启")
        assert window.progress_bar.minimum() == window.progress_bar.maximum() == 0
        assert window.activity_progress.minimum() == window.activity_progress.maximum() == 0
    finally:
        destroy_window(app, window)


def test_stylesheet_keeps_semantic_state_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        stylesheet = " ".join(window.styleSheet().split())
        for fragment in (
            "QPushButton#commitAction",
            "QPushButton#primaryAction",
            "QPushButton:disabled",
            'QLabel#railDeviceState[deviceState="busy"]',
            "QProgressBar#workflowProgress, QProgressBar#activityProgress",
        ):
            assert fragment in stylesheet
    finally:
        destroy_window(app, window)


def test_device_scan_worker_marks_adb_enumeration_failure_as_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAdbClient:
        def __init__(self, _adb: str) -> None:
            pass

        def devices(self) -> list[str]:
            raise RuntimeError("adb server unavailable")

    class EmptyFastbootClient:
        def __init__(self, _fastboot: str, serial: str = "") -> None:
            pass

        def devices(self) -> list[str]:
            return []

    monkeypatch.setattr(
        scan_worker_module.EnvChecker,
        "resolve_or_none",
        lambda _self: SimpleNamespace(adb="adb.exe", fastboot="fastboot.exe"),
    )
    monkeypatch.setattr(scan_worker_module, "AdbClient", FailingAdbClient)
    monkeypatch.setattr(scan_worker_module, "FastbootClient", EmptyFastbootClient)
    worker = DeviceScanWorker(tmp_path, Settings())
    results: list[DeviceScanResult] = []
    worker.scan_completed.connect(results.append)
    worker.run()
    assert len(results) == 1
    assert results[0].serial == ""
    assert results[0].scan_error == "adb server unavailable"
    assert results[0].connection_state_known is False


def test_gui_exposes_separate_local_root_and_dyn_restore_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        for name in (
            "source_init_boot_edit",
            "output_init_boot_edit",
            "patch_init_boot_button",
            "restore_dyn_backup_edit",
            "restore_vivo_dyn_button",
        ):
            assert hasattr(window, name)
            assert getattr(window, name).parent() is not None
        root_panel = section_panel_by_heading(
            window.page_widgets["brands"],
            "Root 镜像管理（SakiSU 4.3）",
        )
        assert window.patch_init_boot_button.isAncestorOf(root_panel) is False
        assert root_panel.isAncestorOf(window.patch_init_boot_button)
        assert root_panel.isAncestorOf(window.sakisu_root_checkbox)
        dyn_panel = section_panel_by_heading(
            window.page_widgets["brands"],
            "Vivo dyn 还原",
        )
        assert dyn_panel.isAncestorOf(window.restore_vivo_dyn_button)
        assert not window.test_runtime_panel.isVisible()
        assert "root" not in window.nav_buttons
    finally:
        destroy_window(app, window)


def test_runtime_selector_deduplicates_complete_privilege_runtime_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        assert window.model_combo.count() == len(window._profiles_cache) + 1
        assert window.runtime_combo.count() == len(
            {profile.privilege_runtime for profile in window._profiles_cache.values()}
        ) + 1
        assert set(window._runtime_representative_by_model) == set(
            window._profiles_cache
        )
        for model_key, representative_key in (
            window._runtime_representative_by_model.items()
        ):
            assert (
                window._profiles_cache[model_key].privilege_runtime
                == window._profiles_cache[representative_key].privilege_runtime
            )
    finally:
        destroy_window(app, window)


def test_first_test_mode_model_change_replaces_stale_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.settings.test_mode_enabled = True
        window._apply_test_mode_state(announce=False)
        target_model = "vivopd2242"
        expected_runtime = window._runtime_representative_by_model[target_model]
        stale_runtime = next(
            runtime
            for runtime in window._runtime_representative_by_model.values()
            if runtime != expected_runtime
        )
        window.runtime_combo.setCurrentIndex(window.runtime_combo.findData(stale_runtime))

        window.model_combo.setCurrentIndex(window.model_combo.findData(target_model))

        assert window.runtime_combo.currentData() == expected_runtime
    finally:
        destroy_window(app, window)


def test_sakisu_checkbox_is_opt_in_and_vivo_complete_unlock_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        assert not window.sakisu_root_checkbox.isChecked()
        vivo = window.model_combo.findData("vivopd2242")
        honor = window.model_combo.findData("honormagic8proair")
        assert vivo >= 0 and honor >= 0

        window.model_combo.setCurrentIndex(vivo)
        assert window.sakisu_root_checkbox.isEnabled()
        window.sakisu_root_checkbox.setChecked(True)
        window.model_combo.setCurrentIndex(honor)
        assert not window.sakisu_root_checkbox.isEnabled()
        assert not window.sakisu_root_checkbox.isChecked()
    finally:
        destroy_window(app, window)


def test_root_and_dyn_starters_forward_explicit_user_selected_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        vivo = window.model_combo.findData("vivopd2242")
        assert vivo >= 0
        window.model_combo.setCurrentIndex(vivo)
        window._current_serial = "SERIAL"
        window._current_mode = "adb"
        source = tmp_path / "init_boot.img"
        source.write_bytes(b"ANDROID!" + b"\0" * 4088)
        output = tmp_path / "patched_init_boot.img"
        backup = tmp_path / "desktop-backup"
        backup.mkdir()
        window.source_init_boot_edit.setText(str(source))
        window.output_init_boot_edit.setText(str(output))
        window.restore_dyn_backup_edit.setText(str(backup))
        calls: list[tuple[str, dict[str, object]]] = []
        monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
        monkeypatch.setattr(
            window,
            "_start_flow",
            lambda kind, **kwargs: calls.append((kind, kwargs)),
        )

        window._start_patch_init_boot()
        window._start_restore_vivo_dyn()

        assert calls == [
            (
                "patch_init_boot",
                {
                    "source_init_boot": source,
                    "output_init_boot": output,
                    "confirm": False,
                },
            ),
            (
                "restore_vivo_dyn",
                {
                    "source_dyn_backup": backup,
                    "preload_override": None,
                    "confirm": False,
                },
            ),
        ]
    finally:
        destroy_window(app, window)


def test_honor_model_preselects_protected_builtin_fastboot_lk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        index = window.model_combo.findData("honormagic8proair")
        assert index >= 0
        window.model_combo.setCurrentIndex(index)
        assert window.patched_lk_edit.text() == "builtin:honor_magic8proair_lk"
        assert window.fastboot_patched_lk_edit.text() == "builtin:honor_magic8proair_lk"
    finally:
        destroy_window(app, window)


def test_test_mode_confirmation_is_bound_to_full_operation_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.settings.test_mode_enabled = True
        window._apply_test_mode_state(announce=False)
        window._current_serial = "SERIAL-1"
        window.model_combo.setCurrentIndex(1)
        window.runtime_combo.setCurrentIndex(1)
        warnings: list[tuple[str, str, str]] = []

        def approve(_parent, title: str, message: str, *_args):
            warnings.append((_parent.objectName(), title, message))
            return QMessageBox.Yes

        monkeypatch.setattr(QMessageBox, "warning", approve)

        assert window._confirm_test_mode_operation("backup_export")
        assert window._confirm_test_mode_operation("backup_export")
        assert len(warnings) == 1

        window._current_serial = "SERIAL-2"
        assert window._confirm_test_mode_operation("backup_export")
        window.model_combo.setCurrentIndex(2)
        assert window._confirm_test_mode_operation("backup_export")
        alternate_runtime = next(
            index
            for index in range(1, window.runtime_combo.count())
            if index != window.runtime_combo.currentIndex()
        )
        window.runtime_combo.setCurrentIndex(alternate_runtime)
        assert window._confirm_test_mode_operation("backup_export")
        assert window._confirm_test_mode_operation("write_inactive")
        assert len(warnings) == 5
    finally:
        destroy_window(app, window)


def test_worker_rejects_untrusted_test_compatibility_request(tmp_path: Path) -> None:
    worker = FlowWorker(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=False),
        "SERIAL",
        "model",
        "backup_export",
        skip_profile_identity=True,
    )
    errors: list[str] = []
    worker.finished_error.connect(errors.append)

    worker.run()

    assert errors and "仅允许离线" in errors[0]


def test_worker_rejects_complete_unlock_in_test_compatibility_mode(
    tmp_path: Path,
) -> None:
    worker = FlowWorker(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True),
        "SERIAL",
        "model",
        "vivo_complete_unlock",
        skip_profile_identity=True,
    )
    errors: list[str] = []
    worker.finished_error.connect(errors.append)

    worker.run()

    assert errors and "完整解锁不允许" in errors[0]


def test_worker_forwards_opt_in_sakisu_flag_only_to_complete_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(key="vivopd2242", brand="vivo")
    flow = SimpleNamespace(run=MagicMock(), ctx=None)
    received: dict[str, object] = {}
    worker = FlowWorker(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        "SERIAL",
        "vivopd2242",
        "vivo_complete_unlock",
        install_sakisu_root=True,
    )
    monkeypatch.setattr(
        worker_module,
        "DeviceDetector",
        lambda *_args, **_kwargs: SimpleNamespace(
            get_profile_by_key=lambda _key: profile
        ),
    )
    monkeypatch.setattr(
        worker,
        "_device_for_adb",
        lambda _detector, _profile: SimpleNamespace(serial="SERIAL"),
    )
    monkeypatch.setattr(
        worker_module,
        "create_flow",
        lambda *_args, **kwargs: received.update(kwargs) or flow,
    )

    worker.run()

    assert received["install_sakisu_root"] is True
    flow.run.assert_called_once()


def test_there_is_no_online_channel_any_more() -> None:
    """The open-source 1.1 baseline is offline-only; the online client is gone."""

    import importlib.util

    from nebumtk_unlock_tools import build_profile

    assert build_profile.BUILD_CHANNEL == "offline"
    assert build_profile.channel() == "offline"
    assert build_profile.is_online() is False
    assert importlib.util.find_spec("nebumtk_unlock_tools.online") is None


def test_worker_rejects_cross_runtime_in_normal_mode(
    tmp_path: Path,
) -> None:
    worker = FlowWorker(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        "",
        "identity",
        "patch_lk",
        patched_lk=tmp_path / "patched.img",
        source_lk=tmp_path / "source.img",
        runtime_model_key="runtime",
    )
    errors: list[str] = []
    worker.finished_error.connect(errors.append)
    records = {
        key: {
            "brand": "vivo",
            "match": {"brand": "vivo", "model": [key]},
            "resources": {},
            "partitions": {},
            "post_unlock": {},
        }
        for key in ("identity", "runtime")
    }

    with patch(
        "nebumtk_unlock_tools.resources.catalog.load_device_catalog",
        return_value={"devices": records},
    ):
        worker.run()

    assert errors and "仅测试模式" in errors[0]


def test_flow_context_rejects_test_compatibility_without_offline_setting(
    tmp_path: Path,
) -> None:
    with pytest.raises(VerificationError, match="仅允许离线"):
        FlowContext(
            tmp_path,
            tmp_path / "workspace",
            Settings(test_mode_enabled=False),
            FlowCallbacks(skip_profile_identity=True),
        )


def test_complete_unlock_domain_rejects_test_compatibility_mode(
    tmp_path: Path,
) -> None:
    flow = VivoCompleteUnlockFlow(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True),
        callbacks=FlowCallbacks(skip_profile_identity=True),
    )

    with pytest.raises(VerificationError, match="完整解锁不允许"):
        flow.run(SimpleNamespace(), SimpleNamespace(brand="vivo"))


def test_busy_state_covers_reclassified_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window._set_busy(True)
        for control in (
            window.root_button,
            window.backup_button,
            window.write_button,
            window.dual_slot_sync_button,
            window.restore_backup_lk_button,
            window.patched_lk_browse,
            window.fastboot_patched_lk_browse,
            window.restore_lk_browse,
        ):
            assert not control.isEnabled()
        assert window.cancel_button.isEnabled()
    finally:
        destroy_window(app, window)


def test_unified_operation_state_blocks_every_starter_and_action_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        blocker = DummyOperationWorker("inspect_lk")
        window.utility_worker = blocker
        window._current_serial = "SERIAL-A"
        window._current_mode = "adb"
        window.model_combo.setCurrentIndex(1)
        window.cmd_edit.setText("adb devices")
        monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Ok)

        window._refresh_action_guidance()
        assert not window.refresh_button.isEnabled()
        assert not window.root_button.isEnabled()
        scanner = DeviceScanWorker(
            tmp_path,
            Settings(),
            generation=window._scan_generation,
            automatic=True,
        )
        scanner.scan_completed.connect(window._apply_scan_result)
        scanner.scan_completed.emit(DeviceScanResult(serial="SERIAL-B", mode="fastboot"))
        assert window._current_serial == "SERIAL-A"
        assert not window.refresh_button.isEnabled()
        window._start_utility("inspect_lk", {"source": "ignored"})
        window._start_reboot("system")
        window.run_cmd()
        window._start_flow("root_escalation", confirm=False)

        assert window.utility_worker is blocker
        assert window.reboot_worker is None
        assert window.terminal_worker is None
        assert window.worker is None
    finally:
        window.utility_worker = None
        destroy_window(app, window)


def test_local_utility_and_terminal_pause_scanning_and_invalidate_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        utility = DummyOperationWorker("inspect_lk")
        monkeypatch.setattr(
            main_window_module,
            "UtilityWorker",
            lambda operation, _payload, _parent: utility,
        )
        window.device_scan_timer.start()
        generation = window._scan_generation
        window._start_utility("inspect_lk", {"source": "lk.img"})
        assert window.utility_worker is utility and utility.started
        assert window._scan_generation == generation + 1
        assert not window.device_scan_timer.isActive()

        window.utility_worker = None
        terminal = DummyOperationWorker("terminal")
        monkeypatch.setattr(
            main_window_module,
            "TerminalWorker",
            lambda _command, _parent: terminal,
        )
        monkeypatch.setattr(
            main_window_module.EnvChecker,
            "validate",
            lambda _self, **_kwargs: SimpleNamespace(adb="adb", fastboot="fastboot"),
        )
        window.cmd_edit.setText("adb devices")
        generation = window._scan_generation
        window.device_scan_timer.start()
        window.run_cmd()
        assert window.terminal_worker is terminal and terminal.started
        assert window._scan_generation == generation + 1
        assert not window.device_scan_timer.isActive()
    finally:
        window.utility_worker = None
        window.terminal_worker = None
        destroy_window(app, window)


def test_cancel_reboot_means_stop_waiting_and_disables_repeat_click(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        reboot = DummyOperationWorker("reboot")
        monkeypatch.setattr(main_window_module, "RebootWorker", lambda **_kwargs: reboot)
        monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
        monkeypatch.setattr(
            main_window_module.EnvChecker,
            "validate",
            lambda _self, **_kwargs: SimpleNamespace(adb="adb", fastboot="fastboot"),
        )
        window._current_serial = "SERIAL-A"
        window._current_mode = "adb"
        window._start_reboot("system")

        assert reboot.started
        assert any(getattr(slot, "__self__", None) is window for slot in reboot.activity_event.slots)
        window.cancel_flow()

        assert reboot.cancelled
        assert not window.cancel_button.isEnabled()
        assert window.activity_stage_label.text() == "正在停止等待…"
    finally:
        window.reboot_worker = None
        window._flow_active = False
        destroy_window(app, window)


def test_activity_event_is_connected_to_safe_compact_activity_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window._current_serial = "SERIAL-SECRET"
        event = ActivityEvent(
            ActivityKind.WAITING,
            "等待设备 SERIAL-SECRET",
            "token=DO-NOT-SHOW",
            serial="SERIAL-SECRET",
        )
        window._on_activity_event(event)

        visible = window.activity_stage_label.text()
        log_text = window.log_view.toPlainText()
        assert "SERIAL-SECRET" not in visible + log_text
        assert "DO-NOT-SHOW" not in visible + log_text
        assert "等待设备" in visible
        assert "<已隐藏>" in visible
    finally:
        destroy_window(app, window)


def test_transient_disconnect_preserves_lk_context_but_clears_xingc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        worker = DeviceScanWorker(tmp_path, Settings(), generation=17, automatic=True)
        worker.scan_completed.connect(window._apply_scan_result)
        window._scan_generation = 17
        window.model_combo.setCurrentIndex(1)
        selected_model = window.model_combo.currentData()

        worker.scan_completed.emit(DeviceScanResult(serial="SERIAL-A", mode="adb"))
        window.patched_lk_edit.setText("patched.img")
        window.fastboot_patched_lk_edit.setText("patched.img")
        window.restore_lk_edit.setText("restore.img")
        window._xingc_token = "token"
        window._xingc_payload = b"payload"
        window._xingc_serial = "SERIAL-A"

        worker.scan_completed.emit(DeviceScanResult())
        assert window._current_serial == ""
        assert window._context_serial == "SERIAL-A"
        assert window.model_combo.currentData() == selected_model
        assert window.patched_lk_edit.text() == "patched.img"
        assert window.restore_lk_edit.text() == "restore.img"
        assert window._xingc_token == ""

        worker.scan_completed.emit(
            DeviceScanResult(serial="SERIAL-A", mode="fastboot", fastboot_mode="bootloader")
        )
        assert window.patched_lk_edit.text() == "patched.img"
        assert window._xingc_token == ""

        worker.scan_completed.emit(
            DeviceScanResult(serial="SERIAL-B", mode="fastboot", fastboot_mode="bootloader")
        )
        assert window._context_serial == "SERIAL-B"
        assert window.patched_lk_edit.text() == ""
        assert window.fastboot_patched_lk_edit.text() == ""
        assert window.restore_lk_edit.text() == ""
        assert window._xingc_token == ""
        assert window._xingc_payload == b""
    finally:
        destroy_window(app, window)


def test_flow_activity_uses_whitelisted_display_name_not_internal_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        flow = DummyOperationWorker("root_escalation")
        monkeypatch.setattr(main_window_module, "FlowWorker", lambda *_args, **_kwargs: flow)
        monkeypatch.setattr(
            main_window_module.EnvChecker,
            "validate",
            lambda _self, **_kwargs: SimpleNamespace(adb="adb", fastboot="fastboot"),
        )
        window._current_serial = "SERIAL-A"
        window._current_mode = "adb"
        window.model_combo.setCurrentIndex(1)

        window._start_flow("root_escalation", confirm=False)
        assert any(getattr(slot, "__self__", None) is window for slot in flow.activity_event.slots)
        window._on_success()

        activity = window.log_view.toPlainText()
        assert "临时提权 开始" in activity
        assert "临时提权 完成" in activity
        assert "root_escalation" not in activity
    finally:
        window.worker = None
        window._flow_active = False
        destroy_window(app, window)


def test_flow_worker_local_patch_uses_explicit_source_and_output(tmp_path: Path) -> None:
    source = tmp_path / "source_lk.img"
    output = tmp_path / "patched_lk.img"
    source.write_bytes(b"x" * 4096)
    profile = SimpleNamespace(key="model", brand="Vivo")
    flow = MagicMock()
    worker = FlowWorker(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        "",
        "model",
        "patch_lk",
        patched_lk=output,
        source_lk=source,
    )
    with patch(
        "nebumtk_unlock_tools.resources.catalog.load_device_catalog",
        return_value={"devices": {"model": {}}},
    ), patch(
        "nebumtk_unlock_tools.gui.worker.DeviceDetector._profiles_from_mapping",
        return_value={"model": profile},
    ), patch(
        "nebumtk_unlock_tools.gui.worker.create_flow",
        return_value=flow,
    ):
        worker.run()
    flow.run_local.assert_called_once_with(source, profile, output)


def test_flow_worker_init_boot_patch_forwards_explicit_images_and_uses_adb_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "init_boot.img"
    output = tmp_path / "patched_init_boot.img"
    source.write_bytes(b"ANDROID!" + b"\0" * 4088)
    profile = SimpleNamespace(key="model", brand="vivo")
    flow = SimpleNamespace(run=MagicMock(), ctx=None)
    received: dict[str, object] = {}
    worker = FlowWorker(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        "SERIAL",
        "model",
        "patch_init_boot",
        source_init_boot=source,
        output_init_boot=output,
    )
    monkeypatch.setattr(
        worker_module,
        "DeviceDetector",
        lambda *_args, **_kwargs: SimpleNamespace(get_profile_by_key=lambda _key: profile),
    )
    monkeypatch.setattr(
        worker,
        "_device_for_adb",
        lambda _detector, _profile: SimpleNamespace(serial="SERIAL"),
    )
    monkeypatch.setattr(
        worker_module,
        "create_flow",
        lambda *_args, **kwargs: received.update(kwargs) or flow,
    )

    worker.run()

    assert received["source_init_boot"] == source
    assert received["output_init_boot"] == output
    flow.run.assert_called_once()


def test_worker_confirmation_bridge_remains_non_blocking_on_cancel(tmp_path: Path) -> None:
    worker = FlowWorker(tmp_path, tmp_path / "workspace", Settings(), "SERIAL", "model", "oppo_unlock")
    seen: list[str] = []

    def approve(message: str) -> None:
        seen.append(message)
        worker._confirmation_result = True
        worker._confirmation_wait.set()

    worker.confirmation_requested.connect(approve)
    assert worker._confirm_from_main_window("flash?") is True
    assert seen == ["flash?"]
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
