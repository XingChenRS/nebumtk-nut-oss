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

from nebumtk_unlock_tools.config.store import Settings, SettingsStore
from nebumtk_unlock_tools.core.activity import ActivityEvent, ActivityKind
from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.gui import main_window as main_window_module
from nebumtk_unlock_tools.gui import device_scan_worker as scan_worker_module
from nebumtk_unlock_tools.gui import worker as worker_module
from nebumtk_unlock_tools.gui.device_scan_worker import DeviceScanResult, DeviceScanWorker
from nebumtk_unlock_tools.gui.main_window import MainWindow
from nebumtk_unlock_tools.gui.worker import FlowWorker
from nebumtk_unlock_tools.flows import stages as stages_module
from nebumtk_unlock_tools.flows.base import FlowCallbacks, FlowContext
from nebumtk_unlock_tools.flows.stages import VivoCompleteUnlockFlow


PAGE_KEYS = ["device", "partitions", "authorized", "images", "advanced"]


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
    # Never let a real modal dialog block a headless test run.
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_a, **_k: QMessageBox.Ok)
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
        assert (window.width(), window.height()) == (1360, 820)
        assert (window.minimumWidth(), window.minimumHeight()) == (1024, 680)
        rail = window.findChild(QFrame, "navigationRail")
        assert rail is not None
        assert window.activity_panel is not None
    finally:
        destroy_window(app, window)


@pytest.mark.parametrize("width,height", [(1360, 820), (1024, 680)])
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
        window.pages.setCurrentWidget(window.page_scroll_areas["partitions"])
        window.show()
        app.processEvents()
        # The stage text is the whitelisted public flow label, never the
        # worker's internal step string.
        window._active_flow_label = "写入对槽分区"
        window._on_progress(42, "正在修补 LK")
        assert window.progress_bar.value() == window.activity_progress.value() == 42
        assert window.step_label.text() == "写入对槽分区"
        assert window.activity_stage_label.text() == "写入对槽分区"
        # The workbench bar stays determinate; the activity panel owns the
        # indeterminate "waiting" presentation.
        window._on_progress(42, "等待设备重启")
        assert window.progress_bar.value() == 42
        assert window.activity_progress.value() == 42
        assert "正在修补 LK" not in window.step_label.text()
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
            'QPushButton[uiRole="primary"]',
            'QPushButton[uiRole="danger"]',
            "QPushButton:disabled",
            'QLabel[state="error"]',
            "QProgressBar",
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


def test_gui_exposes_sakisu_and_dyn_controls_on_the_images_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        images = window.images_page
        # The SakiSU panel owns both boot and init_boot patching with a manual
        # KMI selection, and the dyn panel owns capture/restore.
        for control in (
            images.sakisu_source_edit,
            images.sakisu_source_browse,
            images.sakisu_partition_combo,
            images.sakisu_kmi_combo,
            images.sakisu_patch_button,
            images.dyn_backup_edit,
            images.dyn_backup_browse,
        ):
            assert control.parent() is not None
        assert list(images.sakisu_partition_combo.itemText(i) for i in range(images.sakisu_partition_combo.count())) == ["boot", "init_boot"]
        assert images.sakisu_kmi_combo.count() >= 7
        assert window.page_widgets["images"] is images
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


def test_test_mode_keeps_the_manual_runtime_but_normal_mode_refreshes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        target_model = "vivopd2242"
        expected_runtime = window._runtime_representative_by_model[target_model]
        stale_runtime = next(
            runtime
            for runtime in window._runtime_representative_by_model.values()
            if runtime != expected_runtime
        )
        stale_index = window.runtime_combo.findData(stale_runtime)

        # Normal mode: picking a model re-derives its representative runtime.
        window.model_combo.setCurrentIndex(window.model_combo.findData(target_model))
        assert window.runtime_combo.currentData() == expected_runtime

        # Test mode: an explicitly chosen runtime is preserved across a model
        # change, because manual combination is the point of test mode.
        window.settings.test_mode_enabled = True
        window._apply_test_mode_state(announce=False)
        window.runtime_combo.setCurrentIndex(stale_index)
        window.model_combo.setCurrentIndex(0)
        window.model_combo.setCurrentIndex(window.model_combo.findData(target_model))
        assert window.runtime_combo.currentData() == stale_runtime
    finally:
        destroy_window(app, window)


def _deliver_scan(window: MainWindow, tmp_path: Path, **fields) -> None:
    """Deliver one scan result the way the worker does.

    ``_apply_scan_result`` reads ``self.sender()`` and ignores anything that did
    not arrive over the signal, so the result has to be emitted, not called.
    """

    generation = int(fields.pop("scan_generation"))
    worker = DeviceScanWorker(tmp_path, Settings(), generation=generation, automatic=True)
    worker.scan_completed.connect(window._apply_scan_result)
    window._scan_generation = generation
    worker.scan_completed.emit(
        DeviceScanResult(scan_generation=generation, **fields)
    )


def test_test_mode_keeps_a_hand_picked_template_through_a_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Automatic matching must not undo the operator's template choice.

    Detection is known to be wrong on some models, and test mode exists so the
    operator can override it.  A scan result used to overwrite the combo
    regardless, which changed the capability set underneath them: a hand-picked
    Vivo template that had just made 「准备解锁」 ready was replaced by whatever
    the device reported, and the action greyed out seconds later.
    """

    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.settings.test_mode_enabled = True
        window._apply_test_mode_state(announce=False)

        index = window.model_combo.findData("vivox200")
        assert index >= 0, "vivox200 is missing from the model list"
        window.model_combo.setCurrentIndex(index)
        # `activated` is what a real click emits; the auto-match path uses
        # setCurrentIndex, which deliberately does not emit it.
        window._mark_model_chosen_by_operator()

        _deliver_scan(
            window,
            tmp_path,
            serial="SERIAL-A",
            mode="adb",
            profile_key="oppofindx9",
            scan_generation=5,
        )

        assert window.model_combo.currentData() == "vivox200", (
            "the scan overruled the operator's template and took the Vivo "
            "capabilities away with it"
        )
    finally:
        destroy_window(app, window)


def test_automatic_matching_still_wins_outside_test_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relaxation is a test-mode courtesy, not a change to production."""

    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.settings.test_mode_enabled = False
        window._apply_test_mode_state(announce=False)

        window.model_combo.setCurrentIndex(window.model_combo.findData("vivox200"))
        window._mark_model_chosen_by_operator()

        _deliver_scan(
            window,
            tmp_path,
            serial="SERIAL-A",
            mode="adb",
            profile_key="oppofindx9",
            scan_generation=6,
        )

        assert window.model_combo.currentData() == "oppofindx9", (
            "outside test mode the detected template must still be applied"
        )
    finally:
        destroy_window(app, window)


def test_test_mode_is_announced_where_the_operator_can_see_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test mode relaxes brand matching on every page, so it has to say so.

    Its only trace used to be an empty ``QLabel`` on the settings page, which
    left relaxed gating reading as a bug in whichever page happened to be on
    screen.
    """

    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.settings.test_mode_enabled = False
        window._apply_test_mode_state(announce=False)
        assert not window.top_test_selectors.isVisibleTo(window)

        window.settings.test_mode_enabled = True
        window._apply_test_mode_state(announce=False)
        assert window.top_test_selectors.isVisibleTo(window)
        assert window.top_test_mode_label.text() == "测试模式"
        assert window.top_test_mode_label.isVisibleTo(window)
    finally:
        destroy_window(app, window)


def test_sakisu_patch_requires_an_explicit_wrong_kmi_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The patch-only flow stays opt-in: it refuses to run without the risk ack."""

    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        page = window.images_page
        source = tmp_path / "init_boot.img"
        source.write_bytes(b"ANDROID!" + bytes(4088))
        page.sakisu_source_edit.setText(str(source))
        page.sakisu_rollback_edit.setText(str(tmp_path / "rollback.img"))
        page.sakisu_candidate_edit.setText(str(tmp_path / "candidate.img"))
        page.sakisu_receipt_edit.setText(str(tmp_path / "receipt.json"))

        assert not page.sakisu_wrong_kmi_confirm.isChecked()
        started: list[object] = []
        monkeypatch.setattr(window, "_prepare_file_snapshot", lambda *a, **k: started.append(a))

        window._start_sakisu_patch()

        assert not started
        assert "wrong-KMI" in page.image_status_label.text()

        page.sakisu_wrong_kmi_confirm.setChecked(True)
        window._start_sakisu_patch()
        assert started
    finally:
        destroy_window(app, window)


def test_sakisu_and_dyn_entries_forward_the_operator_selected_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        page = window.images_page
        source = tmp_path / "init_boot.img"
        source.write_bytes(b"ANDROID!" + bytes(4088))
        page.sakisu_source_edit.setText(str(source))
        page.sakisu_rollback_edit.setText(str(tmp_path / "rollback.img"))
        page.sakisu_candidate_edit.setText(str(tmp_path / "candidate.img"))
        page.sakisu_receipt_edit.setText(str(tmp_path / "receipt.json"))
        page.sakisu_wrong_kmi_confirm.setChecked(True)
        page.sakisu_kmi_combo.setCurrentText("android14-6.1")

        captured: list[tuple] = []
        monkeypatch.setattr(
            window,
            "_prepare_file_snapshot",
            lambda path, callback, **kwargs: captured.append((path, kwargs)),
        )

        window._start_sakisu_patch()

        assert captured and captured[0][0] == source
        assert captured[0][1].get("device_bound") is False
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

    assert errors and "必须先在设置中显式开启" in errors[0]


def test_worker_accepts_complete_unlock_in_test_compatibility_mode(
    tmp_path: Path,
) -> None:
    """Test mode may reach the complete workflow.

    The refusal came from the online product, where the model match was what
    authorised a run.  Here the operator hand-picks the template precisely
    because detection is sometimes wrong, and this workflow is the only path
    that performs the finishing steps.  Without it test mode could not do the
    one thing it exists for.
    """

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

    # There is no device behind that serial, so it fails — but it must fail on
    # the device, not on the identity relaxation.
    assert errors, "the worker reported no outcome at all"
    assert "不允许" not in errors[0], errors[0]


def test_worker_rejects_test_compatibility_when_test_mode_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    assert errors and "必须先在设置中显式开启" in errors[0]


def test_worker_rejects_cross_runtime_in_normal_mode(
    tmp_path: Path,
) -> None:
    worker = FlowWorker(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        "SERIAL",
        "identity",
        "backup_export",
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

    assert errors and "仅兼容模式" in errors[0]


def test_flow_context_rejects_test_compatibility_without_offline_setting(
    tmp_path: Path,
) -> None:
    with pytest.raises(VerificationError, match="仅允许"):
        FlowContext(
            tmp_path,
            tmp_path / "workspace",
            Settings(test_mode_enabled=False),
            FlowCallbacks(skip_profile_identity=True),
        )


def test_complete_unlock_domain_allows_test_compatibility_mode(
    tmp_path: Path,
) -> None:
    """The flow-level refusal is gone; only the settings gate remains."""

    flow = VivoCompleteUnlockFlow(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True),
        callbacks=FlowCallbacks(skip_profile_identity=True),
    )

    # This profile satisfies the capability guard, so the run must now get past
    # it.  Whatever it fails on next is not the identity relaxation.
    profile = SimpleNamespace(
        brand="vivo", capabilities=frozenset({"preload", "xingc"})
    )
    try:
        flow.run(SimpleNamespace(), profile)
    except Exception as exc:  # noqa: BLE001 - any downstream failure is fine
        assert "不允许" not in str(exc), str(exc)
    else:  # pragma: no cover - reaching a device would be a surprise here
        pytest.fail("the flow ran to completion without a device")


def test_test_mode_can_reach_the_complete_workflow_from_the_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The button and the init_boot opt-in it configures must be usable."""

    app, window = make_main_window(tmp_path, monkeypatch)
    try:
        window.settings.test_mode_enabled = True
        window._apply_test_mode_state(announce=False)
        window.model_combo.setCurrentIndex(window.model_combo.findData("vivox200"))
        window._mark_model_chosen_by_operator()
        _deliver_scan(
            window,
            tmp_path,
            serial="SERIAL-A",
            mode="adb",
            profile_key="vivox200",
            scan_generation=11,
        )

        assert window.auto_flow_button.isEnabled(), (
            "test mode could not reach the only path that performs the "
            "finishing steps"
        )
        assert window.sakisu_root_checkbox.isEnabled(), (
            "the init_boot write cannot be opted into"
        )
    finally:
        destroy_window(app, window)


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
            window.patched_lk_browse,
            window.fastboot_patched_lk_browse,
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

        worker.scan_completed.emit(
            DeviceScanResult(serial="SERIAL-A", mode="adb", scan_generation=17)
        )
        window.patched_lk_edit.setText("patched.img")
        window.fastboot_patched_lk_edit.setText("patched.img")
        window.original_lk_edit.setText("restore.img")
        window._xingc_token = "token"
        window._xingc_payload = b"payload"
        window._xingc_serial = "SERIAL-A"

        worker.scan_completed.emit(DeviceScanResult(scan_generation=17))
        # A disappeared device drops transport confirmation and any in-memory
        # Xingc material, but the operator context (last confirmed serial) is
        # retained for the reconnect comparison.
        assert window._current_serial == ""
        assert window._context_serial == "SERIAL-A"
        assert window.model_combo.currentData() == selected_model
        assert window.patched_lk_edit.text() == "patched.img"
        assert window._xingc_token == ""

        worker.scan_completed.emit(
            DeviceScanResult(
                serial="SERIAL-A", mode="fastboot", fastboot_mode="bootloader", scan_generation=17
            )
        )
        assert window.patched_lk_edit.text() == "patched.img"
        assert window._xingc_token == ""

        worker.scan_completed.emit(
            DeviceScanResult(
                serial="SERIAL-B", mode="fastboot", fastboot_mode="bootloader", scan_generation=17
            )
        )
        assert window._context_serial == "SERIAL-B"
        assert window.patched_lk_edit.text() == ""
        assert window.original_lk_edit.text() == ""
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
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *_args, **_kwargs: QMessageBox.Yes,
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
    request = flow.run_local.call_args.args[0]
    assert isinstance(request, stages_module.LocalLkPatchRequest)
    assert request.source_lk == source
    assert request.output_lk == output


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
