"""Main application window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
)

from ..build_profile import is_online
from ..config.store import SettingsStore
from ..core.activity import ActivityEvent, ActivityKind
from ..core.adb import AdbClient
from ..core.device import DeviceDetector, DeviceProfile
from ..core.env import EnvChecker
from ..core.errors import EnvError, VerificationError
from ..core.logging import SessionLog, compact_log_message
from ..core.runner import run_command
from ..core.slot import SlotManager
from ..core.workspace import WorkspaceManager
from .device_scan_worker import DeviceScanResult, DeviceScanWorker
from .settings_dialog import SettingsDialog
from .toolbox_ui import ToolboxUi
from .reboot_worker import RebootWorker
from .worker import FlowWorker, TerminalWorker, UtilityWorker

LOG_VIEW_MAX_BLOCKS = 5000

FLOW_DISPLAY_NAMES = {
    "root_escalation": "临时提权",
    "backup_export": "备份",
    "write_inactive": "写入对槽",
    "patch_lk": "LK 修补",
    "patch_init_boot": "SakiSU init_boot 修补",
    "oppo_unlock": "OPPO Unlock",
    "restore_backup_lk": "还原双槽原 LK",
    "restore_vivo_dyn": "Vivo dyn 还原",
    "vivo_xingc_unlock": "Xingc Unlock",
    "dual_slot_sync": "双槽同步",
    "vivo_complete_unlock": "Vivo 完整解锁",
}


class MainWindow(QMainWindow):
    diagnostic_received = Signal(str)

    def __init__(self, project_root: Path, workspace_root: Path) -> None:
        super().__init__()
        self.project_root = project_root
        self.workspace_root = workspace_root
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        self.workspace = WorkspaceManager(workspace_root)
        self.worker: FlowWorker | None = None
        self.utility_worker: UtilityWorker | None = None
        self.terminal_worker: TerminalWorker | None = None
        self.reboot_worker: RebootWorker | None = None
        self.scan_worker: DeviceScanWorker | None = None
        self.device_scan_timer = QTimer(self)
        self.device_scan_timer.setInterval(2000)
        self._flow_active = False
        self._flow_cancelled = False
        self._scan_active = False
        self._closing = False
        self._refresh_pending = False
        self._scan_generation = 0
        self._current_serial = ""
        self._current_mode = ""
        self._context_serial = ""
        self._active_flow_label = ""
        self._profiles_cache: dict[str, DeviceProfile] = {}
        self._runtime_representative_by_model: dict[str, str] = {}
        self._auto_preload_path = ""
        self._auto_patched_lk_ref = ""
        self._last_scan_signature: tuple[object, ...] | None = None
        self._last_activity_key: tuple[object, ...] | None = None
        self._last_progress_value: int | None = None
        self._confirmed_test_mode_fingerprint: tuple[str, str, str, str] | None = None

        self.setWindowTitle("NEBUMTK · MTK TOOLBOX")
        self.resize(1200, 720)
        self.setMinimumSize(900, 600)

        self.status_label = QLabel("等待设备")
        self.model_combo = QComboBox()
        self.runtime_combo = QComboBox()
        self.test_mode_banner = QLabel("测试模式 · 已放宽兼容匹配")
        self.test_mode_banner.setWordWrap(True)
        self.test_mode_banner.setStyleSheet(
            "background:#FFF0D5;color:#8A3E00;border:1px solid #E5A84B;"
            "border-radius:6px;padding:7px;font-weight:650;"
        )
        self.refresh_button = QPushButton("刷新")
        self.settings_button = QPushButton("设置")
        self.open_backup_button = QPushButton("打开备份目录")
        self.open_logs_button = QPushButton("打开日志目录")
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)

        self.workspace_path_label = QLabel("工作区: 已启用")
        self.backup_path_label = QLabel("暂无操作")
        self.detected_connection_label = QLabel("连接: 未检测到 ADB 或 Fastboot 设备")
        self.detected_identity_label = QLabel("设备: —")
        self.detected_firmware_label = QLabel("系统: —")
        self.detected_slot_label = QLabel("槽位: —")
        self.detected_profile_label = QLabel("机型匹配: —")
        for label in (
            self.detected_connection_label,
            self.detected_identity_label,
            self.detected_firmware_label,
            self.detected_slot_label,
            self.detected_profile_label,
        ):
            label.setObjectName("detectedDetail")
            label.setWordWrap(True)
        self.log_file_label = QLabel("日志: 就绪")
        self.activity_stage_label = QLabel("就绪")
        self.activity_stage_label.setObjectName("activityStage")
        self.activity_stage_label.setWordWrap(True)
        self.activity_progress = QProgressBar()
        self.activity_progress.setObjectName("activityProgress")
        self.activity_progress.setTextVisible(False)
        self.reboot_system_button = QPushButton("重启到系统")
        self.reboot_fastbootd_button = QPushButton("重启到 Fastbootd")
        self.reboot_bootloader_button = QPushButton("重启到 Bootloader")
        self.reboot_recovery_button = QPushButton("重启到 Recovery")

        self.root_button = QPushButton("临时提权")
        self.backup_button = QPushButton("备份原机分区")
        self.patch_lk_button = QPushButton("检测并修补 LK")
        self.inspect_lk_button = QPushButton("检测外部 LK")
        self.patch_init_boot_button = QPushButton("修补本地 init_boot")
        self.restore_vivo_dyn_button = QPushButton("还原 Vivo dyn")
        self.write_button = QPushButton("写入对槽")
        self.auto_flow_button = QPushButton("执行 Vivo 完整解锁")
        self.sakisu_root_checkbox = QCheckBox(
            "完整解锁的双槽 Flash 收尾时，同时写入原槽 init_boot（可选）"
        )
        self.sakisu_root_checkbox.setToolTip(
            "仅影响 Vivo 完整解锁的收尾：修补并只写入原槽 init_boot；"
            "不会安装或管理 Manager，也不会写入对槽 init_boot。"
        )
        self.oppo_unlock_button = QPushButton("OPPO Unlock")
        self.restore_backup_lk_button = QPushButton("还原双槽原 LK")
        self.vivo_xingc_button = QPushButton("Xingc Unlock")
        self.dual_slot_sync_button = QPushButton("同步 patched LK 到双槽")
        self.xingc_token_button = QPushButton("Token")
        self.xingc_sign_button = QPushButton("Sign")
        self.xingc_auth_button = QPushButton("Auth")
        self.xingc_lock_button = QPushButton("Lock（恢复后）")
        self._xingc_token = ""
        self._xingc_payload = b""
        self._xingc_serial = ""
        self._utility_device_bound = False
        self._utility_serial = ""

        self.preload_edit = QLineEdit()
        self.preload_edit.setReadOnly(True)
        self.preload_browse = QPushButton("受保护")
        self.preload_browse.setEnabled(False)
        self.patched_lk_edit = QLineEdit()
        self.patched_lk_edit.setReadOnly(True)
        self.patched_lk_browse = QPushButton("浏览")
        self.fastboot_patched_lk_edit = QLineEdit()
        self.fastboot_patched_lk_edit.setReadOnly(True)
        self.fastboot_patched_lk_browse = QPushButton("浏览")
        self.restore_lk_edit = QLineEdit()
        self.restore_lk_edit.setReadOnly(True)
        self.restore_lk_browse = QPushButton("选择备份")
        self.source_lk_edit = QLineEdit()
        self.source_lk_browse = QPushButton("选择")
        self.output_lk_edit = QLineEdit()
        self.output_lk_browse = QPushButton("输出")
        self.source_init_boot_edit = QLineEdit()
        self.source_init_boot_browse = QPushButton("选择")
        self.output_init_boot_edit = QLineEdit()
        self.output_init_boot_browse = QPushButton("输出")
        self.restore_dyn_backup_edit = QLineEdit()
        self.restore_dyn_backup_edit.setReadOnly(True)
        self.restore_dyn_backup_browse = QPushButton("选择备份目录")
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("workflowProgress")
        self.step_label = QLabel("就绪")
        self.step_label.setObjectName("stepLabel")
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(LOG_VIEW_MAX_BLOCKS)
        self.cmd_edit = QLineEdit()
        self.cmd_run_button = QPushButton("执行")

        ToolboxUi(self).build()
        self.diagnostic_received.connect(self._append_log)

        # signals
        self.refresh_button.clicked.connect(lambda: self.refresh_devices(auto=False))
        self.device_scan_timer.timeout.connect(lambda: self.refresh_devices(auto=True))
        self.cancel_button.clicked.connect(self.cancel_flow)
        self.settings_button.clicked.connect(self.open_settings)
        self.open_backup_button.clicked.connect(self.open_backup_dir)
        self.open_logs_button.clicked.connect(self.open_logs_dir)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.runtime_combo.currentIndexChanged.connect(self._on_runtime_changed)
        self.patched_lk_browse.clicked.connect(self.browse_patched_lk)
        self.fastboot_patched_lk_browse.clicked.connect(self.browse_patched_lk)
        self.restore_lk_browse.clicked.connect(self.browse_restore_lk)
        self.source_lk_browse.clicked.connect(self.browse_source_lk)
        self.output_lk_browse.clicked.connect(self.browse_output_lk)
        self.source_init_boot_browse.clicked.connect(self.browse_source_init_boot)
        self.output_init_boot_browse.clicked.connect(self.browse_output_init_boot)
        self.restore_dyn_backup_browse.clicked.connect(self.browse_restore_dyn_backup)
        self.inspect_lk_button.clicked.connect(self.inspect_external_lk)
        self.root_button.clicked.connect(lambda: self._start_common("root_escalation"))
        self.backup_button.clicked.connect(lambda: self._start_common("backup_export"))
        self.patch_lk_button.clicked.connect(self._start_patch_lk)
        self.patch_init_boot_button.clicked.connect(self._start_patch_init_boot)
        self.restore_vivo_dyn_button.clicked.connect(self._start_restore_vivo_dyn)
        self.write_button.clicked.connect(lambda: self._start_write())
        self.auto_flow_button.clicked.connect(self._start_auto_flow)
        self.oppo_unlock_button.clicked.connect(lambda: self._start_brand_flow("oppo", "oppo_unlock"))
        self.restore_backup_lk_button.clicked.connect(self._start_restore_backup_lk)
        self.vivo_xingc_button.clicked.connect(self._start_vivo_xingc_unlock)
        self.dual_slot_sync_button.clicked.connect(self._start_dual_slot_sync)
        self.xingc_token_button.clicked.connect(self._xingc_read_token)
        self.xingc_sign_button.clicked.connect(self._xingc_sign_token)
        self.xingc_auth_button.clicked.connect(self._xingc_authorize)
        self.xingc_lock_button.clicked.connect(self._xingc_lock)
        self.cmd_run_button.clicked.connect(self.run_cmd)
        self.cmd_edit.returnPressed.connect(self.run_cmd)
        self.reboot_system_button.clicked.connect(lambda: self._start_reboot("system"))
        self.reboot_fastbootd_button.clicked.connect(lambda: self._start_reboot("fastbootd"))
        self.reboot_bootloader_button.clicked.connect(lambda: self._start_reboot("bootloader"))
        self.reboot_recovery_button.clicked.connect(lambda: self._start_reboot("recovery"))

        self._load_models()
        self._apply_test_mode_state(announce=False)
        self._update_default_preload()
        self._update_default_patched_lk()
        self.refresh_devices(auto=True)
        self.device_scan_timer.start()

    # ---------- helpers ----------
    def _all_flow_buttons(self) -> tuple[QPushButton, ...]:
        return (
            self.root_button,
            self.backup_button,
            self.patch_lk_button,
            self.patch_init_boot_button,
            self.restore_vivo_dyn_button,
            self.inspect_lk_button,
            self.write_button,
            self.auto_flow_button,
            self.oppo_unlock_button,
            self.restore_backup_lk_button,
            self.vivo_xingc_button,
            self.xingc_token_button,
            self.xingc_sign_button,
            self.xingc_auth_button,
            self.xingc_lock_button,
            self.dual_slot_sync_button,
            self.refresh_button,
            self.settings_button,
            self.cmd_run_button,
            self.patched_lk_browse,
            self.source_lk_browse,
            self.output_lk_browse,
            self.source_init_boot_browse,
            self.output_init_boot_browse,
            self.restore_dyn_backup_browse,
            self.fastboot_patched_lk_browse,
            self.restore_lk_browse,
            self.reboot_system_button,
            self.reboot_fastbootd_button,
            self.reboot_bootloader_button,
            self.reboot_recovery_button,
        )

    def _operation_active(self) -> bool:
        """Return whether any foreground operation owns the UI.

        A worker object is considered active from construction until its
        ``finished`` handler clears it.  This closes the small pre-start and
        post-finish windows where ``isRunning()`` alone can allow re-entry.
        """
        return self._flow_active or any(
            worker is not None
            for worker in (
                self.worker,
                self.utility_worker,
                self.terminal_worker,
                self.reboot_worker,
            )
        )

    def _pause_device_scanning(self) -> None:
        self.device_scan_timer.stop()
        self._scan_generation += 1
        self._refresh_pending = False

    def _resume_device_scanning(self) -> None:
        if self._closing or self._operation_active():
            return
        self.refresh_devices(auto=True)
        self.device_scan_timer.start()

    @staticmethod
    def _is_yes_reply(reply) -> bool:
        return reply == QMessageBox.Yes or getattr(reply, "name", "") == "Yes"

    def _get_profile(self, key: str) -> DeviceProfile | None:
        if not key:
            return None
        if key in self._profiles_cache:
            return self._profiles_cache[key]
        try:
            detector = DeviceDetector(self.project_root, AdbClient("adb"))
            profile = detector.get_profile_by_key(key)
        except Exception:
            return None
        self._profiles_cache[key] = profile
        return profile

    @staticmethod
    def _fastboot_display_name(fastboot_mode: str) -> str:
        return {
            "fastbootd": "Fastbootd",
            "bootloader": "Fastboot",
        }.get(fastboot_mode, "Fastboot（未确认子模式）")

    @staticmethod
    def _should_replace_auto_preload(current_text: str, last_auto_path: str) -> bool:
        current = current_text.strip()
        last = last_auto_path.strip()
        return not current or (last and current == last)

    def _load_models(self) -> None:
        self.model_combo.blockSignals(True)
        self.runtime_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            self.model_combo.addItem("自动匹配 / 请选择", "")
            self.runtime_combo.clear()
            self.runtime_combo.addItem("请选择运行配置", "")
            self._profiles_cache.clear()
            self._runtime_representative_by_model.clear()
            try:
                detector = DeviceDetector(self.project_root, AdbClient("adb"))
                runtime_representatives = {}
                for profile in detector.list_profiles():
                    self.model_combo.addItem(profile.display_name, profile.key)
                    runtime = profile.privilege_runtime
                    representative_key = runtime_representatives.get(runtime)
                    if representative_key is None:
                        representative_key = profile.key
                        runtime_representatives[runtime] = representative_key
                        payload_id = runtime.preload_ref.removeprefix("builtin:")
                        self.runtime_combo.addItem(
                            f"{profile.display_name} · {payload_id or '无 preload'}",
                            representative_key,
                        )
                    self._runtime_representative_by_model[
                        profile.key
                    ] = representative_key
                    self._profiles_cache[profile.key] = profile
            except Exception as exc:
                self._set_device_presentation("environment")
                self._append_log(f"加载机型失败（请检查设置中的 platform-tools）: {exc}", error=True)
                return
        finally:
            self.model_combo.blockSignals(False)
            self.runtime_combo.blockSignals(False)

    def _set_device_presentation(
        self,
        state: str,
        *,
        serial: str = "",
        mode: str = "",
        fastboot_mode: str = "",
        detail: str = "",
    ) -> None:
        labels = {
            "missing": "等待设备",
            "scanning": "正在扫描…",
            "environment": "环境未就绪",
            "adb": "已连接 · ADB",
            "fastboot": f"已连接 · {self._fastboot_display_name(fastboot_mode)}",
            "busy": "流程执行中",
        }
        text = labels.get(state, "未检测到")
        if serial and state in {"adb", "fastboot", "busy"}:
            text += f"\n{serial}"
        self.status_label.setText(text)
        self.status_label.setProperty("deviceState", state)
        detected_mode = self._fastboot_display_name(fastboot_mode) if mode == "fastboot" else mode.upper()
        self.status_label.setToolTip(detail or detected_mode or "设备扫描")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self._refresh_action_guidance()

    def _refresh_action_guidance(self) -> None:
        busy = self._operation_active()
        if busy:
            for button in self._all_flow_buttons():
                button.setEnabled(False)
            self.sakisu_root_checkbox.setEnabled(False)
            return
        mode = self._current_mode
        brand = self._selected_brand()
        test_mode = MainWindow._test_mode_enabled(self)
        has_model = bool(self.model_combo.currentData())
        adb_ready = bool(self._current_serial and mode == "adb" and has_model and not busy)
        fastboot_ready = bool(self._current_serial and mode == "fastboot" and has_model and not busy)
        local_ready = bool(has_model and not busy)

        one_click_reasons: list[str] = []
        if test_mode:
            one_click_reasons.append("测试模式下禁用一键流程")
        one_click_ready = bool(
            adb_ready
            and brand == "vivo"
            and not test_mode
            and not one_click_reasons
        )
        self.auto_flow_button.setEnabled(one_click_ready)
        self.auto_flow_button.setToolTip(
            "已就绪"
            if one_click_ready
            else "；".join(
                (
                    ([] if adb_ready and brand == "vivo" else ["需要已识别的 Vivo ADB 设备和目标机型"])
                    + one_click_reasons
                )
            )
        )
        sakisu_eligible = bool(has_model and brand == "vivo" and not test_mode)
        if not sakisu_eligible:
            self.sakisu_root_checkbox.setChecked(False)
        self.sakisu_root_checkbox.setEnabled(sakisu_eligible)
        if hasattr(self, "vivo_preflight_label"):
            ready_text = "已就绪"
            blocked = (
                ([] if adb_ready and brand == "vivo" else ["需要已识别的 Vivo ADB 设备和目标机型"])
                + one_click_reasons
            )
            self.vivo_preflight_label.setText(
                ready_text if one_click_ready else "；".join(blocked)
            )
            self.vivo_preflight_label.setProperty(
                "statusState",
                "ready" if one_click_ready else "warning",
            )
            self.vivo_preflight_label.style().unpolish(self.vivo_preflight_label)
            self.vivo_preflight_label.style().polish(self.vivo_preflight_label)
        self.root_button.setEnabled(adb_ready)
        self.backup_button.setEnabled(adb_ready)
        has_patched_lk = bool(self.patched_lk_edit.text().strip())
        has_restore_lk = bool(self.restore_lk_edit.text().strip())
        self.write_button.setEnabled(adb_ready and has_patched_lk)
        self.patch_lk_button.setEnabled(local_ready)
        self.patch_lk_button.setToolTip(
            "已就绪" if local_ready else "请选择目标机型"
        )
        has_dyn_backup = bool(self.restore_dyn_backup_edit.text().strip())
        root_image_ready = bool(adb_ready and brand == "vivo")
        self.patch_init_boot_button.setEnabled(root_image_ready)
        self.patch_init_boot_button.setToolTip(
            "需要已识别的 Vivo ADB 设备，以读取当前内核 KMI"
            if not root_image_ready
            else "仅修补本地 init_boot，不会刷写设备或安装 Manager"
        )
        self.restore_vivo_dyn_button.setEnabled(root_image_ready and has_dyn_backup)
        self.restore_vivo_dyn_button.setToolTip(
            "需要已识别的 Vivo ADB 设备、已选择的备份目录，以及系统内已授权的 su"
            if not (root_image_ready and has_dyn_backup)
            else "仅使用系统内已授权的 su 还原 dyn 后重启"
        )
        self.oppo_unlock_button.setEnabled(fastboot_ready and (brand == "oppo" or test_mode))
        self.restore_backup_lk_button.setEnabled(fastboot_ready and has_restore_lk)
        self.vivo_xingc_button.setEnabled(fastboot_ready and (brand == "vivo" or test_mode))
        self.dual_slot_sync_button.setEnabled(
            fastboot_ready and has_patched_lk and (brand in {"vivo", "honor"} or test_mode)
        )
        self.xingc_token_button.setEnabled(fastboot_ready and (brand == "vivo" or test_mode))
        self.xingc_sign_button.setEnabled(fastboot_ready and (brand == "vivo" or test_mode) and bool(self._xingc_token) and self._xingc_serial == self._current_serial)
        self.xingc_auth_button.setEnabled(fastboot_ready and (brand == "vivo" or test_mode) and bool(self._xingc_payload) and self._xingc_serial == self._current_serial)
        reboot_ready = bool(self._current_serial and mode in {"adb", "fastboot"} and not busy and self.reboot_worker is None)
        self.reboot_system_button.setEnabled(reboot_ready)
        self.reboot_bootloader_button.setEnabled(reboot_ready)
        self.reboot_fastbootd_button.setEnabled(reboot_ready)
        self.reboot_recovery_button.setEnabled(reboot_ready and mode == "adb")
        self.xingc_lock_button.setEnabled(False)
        if not busy and hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(True)

    # ---------- device scan delegate ----------
    def refresh_devices(self, *, auto: bool = False) -> None:
        """Request one background device scan without overlapping probe processes."""
        if self._closing or self._operation_active():
            return
        if self._scan_active:
            self._refresh_pending = True
            return

        self._scan_active = True
        if not auto:
            self._set_device_presentation("scanning")
            self._append_activity("正在扫描 ADB / Fastboot…", key=("scan-request",))
        self._scan_generation += 1
        generation = self._scan_generation
        worker = DeviceScanWorker(
            self.project_root,
            self.settings,
            generation=generation,
            automatic=auto,
        )
        self.scan_worker = worker
        worker.scan_completed.connect(self._apply_scan_result)
        worker.finished.connect(self._on_scan_finished)
        worker.start()

    def _set_detected_device_details(self, result: DeviceScanResult | None, *, state: str = "missing") -> None:
        if state == "environment":
            self.detected_connection_label.setText("连接: platform-tools 环境未就绪")
            self.detected_identity_label.setText("设备: —")
            self.detected_firmware_label.setText("系统: —")
            self.detected_slot_label.setText("槽位: —")
            self.detected_profile_label.setText("机型匹配: —")
            return
        if result is None or not result.serial:
            self.detected_connection_label.setText("连接: 未检测到 ADB 或 Fastboot 设备")
            self.detected_identity_label.setText("设备: —")
            self.detected_firmware_label.setText("系统: —")
            self.detected_slot_label.setText("槽位: —")
            self.detected_profile_label.setText("机型匹配: —")
            return
        if result.mode == "fastboot":
            fastboot_name = self._fastboot_display_name(result.fastboot_mode)
            self.detected_connection_label.setText(f"连接: {fastboot_name} · {result.serial}")
            self.detected_identity_label.setText("设备: Fastboot 模式下未读取 Android 属性")
            self.detected_firmware_label.setText("系统: 将在操作前重新读取并校验")
            self.detected_slot_label.setText("槽位: 将在操作前重新读取并校验")
            self.detected_profile_label.setText("机型匹配: Fastboot 扫描不自动匹配")
            return

        stable_identity = " · ".join(part for part in (result.brand, result.model, result.device) if part) or "未读取到"
        identity = f"{self._display_model_name(result)} · {stable_identity}"
        self.detected_connection_label.setText(f"连接: ADB · {result.serial}")
        self.detected_identity_label.setText(f"设备: {identity}")
        self.detected_firmware_label.setText(f"系统: {result.firmware or '未读取到'}")
        try:
            slot = SlotManager.from_suffix((result.slot_suffix or "").strip())
            slot_text = f"活动槽 {slot.active_suffix}；对槽 {slot.inactive_suffix}"
        except Exception:
            slot_text = result.slot_suffix or "未读取到"
        self.detected_slot_label.setText(f"槽位: {slot_text}")
        if result.profile_key:
            profile = self._get_profile(result.profile_key)
            profile_name = profile.display_name if profile is not None else result.profile_key
            self.detected_profile_label.setText(f"机型匹配: {profile_name}")
        else:
            self.detected_profile_label.setText("机型匹配: 未匹配，请手动选择目标机型")

    @staticmethod
    def _display_model_name(result: DeviceScanResult) -> str:
        return (
            (result.market_name or "").strip()
            or (result.model or "").strip()
            or "未读取到机型名"
        )

    def _scan_signature(self, result: DeviceScanResult) -> tuple[object, ...]:
        return (
            result.environment_ready,
            result.connection_state_known,
            result.serial,
            result.mode,
            result.fastboot_mode,
            result.brand,
            result.model,
            result.market_name,
            result.device,
            result.firmware,
            result.slot_suffix,
            result.profile_key,
            bool(result.profile_error),
            bool(result.scan_error),
        )

    def _clear_current_device_state(self) -> None:
        """Mark transport unavailable without discarding the operator context."""
        self._current_serial = ""
        self._current_mode = ""
        # LK selections may survive a transient mode switch, but an in-memory
        # Xingc token/signature is tied to one live Fastboot interaction and
        # must not survive loss of transport confirmation.
        self._clear_xingc_context()
        self.backup_path_label.setText("等待设备连接")
        if hasattr(self, "current_operation_panel"):
            self.current_operation_panel.setVisible(False)

    def _clear_device_bound_selections(self) -> None:
        """Drop inputs that must not cross an exact-device boundary."""
        self._clear_xingc_context()
        self._auto_patched_lk_ref = ""
        self.patched_lk_edit.clear()
        self.fastboot_patched_lk_edit.clear()
        self.restore_lk_edit.clear()

    def _append_scan_activity(self, result: DeviceScanResult, previous: tuple[object, ...] | None) -> None:
        current = self._scan_signature(result)
        if current == previous:
            return
        if not result.environment_ready:
            self._append_log("平台工具不可用", error=True)
            return
        if not result.serial:
            self._append_log("设备已断开，等待设备连接…" if previous and previous[1] else "等待设备连接…")
            return
        if result.mode == "fastboot":
            fastboot_name = self._fastboot_display_name(result.fastboot_mode)
            if previous and previous[2] == result.serial and previous[3] == "adb":
                self._append_log(f"设备已从 ADB 切换至 {fastboot_name}")
            else:
                self._append_log(f"已识别 {fastboot_name}")
            return

        self._append_log("已识别 Android 系统（ADB）")
        identity = " · ".join(part for part in (result.brand, result.model, result.device) if part)
        if identity:
            self._append_log(f"设备信息：{identity}")
        if result.firmware:
            self._append_log(f"系统：{result.firmware}")
        try:
            slot = SlotManager.from_suffix((result.slot_suffix or "").strip())
            self._append_log(f"槽位：活动 {slot.active_suffix}，对槽 {slot.inactive_suffix}")
        except Exception:
            pass
        if result.profile_key:
            profile = self._get_profile(result.profile_key)
            name = profile.display_name if profile is not None else result.profile_key
            self._append_log(f"机型已匹配：{name}")
        elif result.profile_error:
            self._append_log("设备已连接，机型待手动确认")

    def _apply_scan_result(self, result: DeviceScanResult) -> None:
        worker = self.sender()
        if not isinstance(worker, DeviceScanWorker):
            return
        if self._closing or self._operation_active() or worker.generation != self._scan_generation:
            return
        auto = worker.automatic
        previous_signature = self._last_scan_signature
        self._last_scan_signature = self._scan_signature(result)
        if not result.environment_ready:
            self._clear_current_device_state()
            self._set_detected_device_details(None, state="environment")
            self._set_device_presentation("environment")
            self._append_scan_activity(result, previous_signature)
            self._refresh_action_guidance()
            return
        if not result.serial:
            self._clear_current_device_state()
            self._set_detected_device_details(None)
            self._set_device_presentation("missing", detail="ADB / Fastboot 自动扫描")
            if result.scan_error and not auto:
                self._append_activity("扫描设备失败", key=("scan-error", result.scan_error), error=True)
            self._append_scan_activity(result, previous_signature)
            return

        previous_context_serial = self._context_serial
        if previous_context_serial and previous_context_serial != result.serial:
            self._clear_device_bound_selections()
        self._context_serial = result.serial
        self._current_serial = result.serial
        self._current_mode = result.mode
        detail = "机型待确认" if result.mode == "adb" and not result.profile_key else ""
        self._set_detected_device_details(result)
        self._set_device_presentation(
            result.mode,
            serial=result.serial,
            mode=result.mode,
            fastboot_mode=result.fastboot_mode,
            detail=detail,
        )
        self._update_backup_label(result.serial)
        if result.profile_key:
            current_key = self.model_combo.currentData() or ""
            if current_key != result.profile_key:
                index = self.model_combo.findData(result.profile_key)
                if index >= 0:
                    self.model_combo.setCurrentIndex(index)
        elif result.profile_error and not auto:
            self._append_activity("设备已连接，机型待手动确认", key=("manual-profile", result.serial), error=True)
        elif result.scan_error and not auto:
            self._append_activity("读取设备信息失败", key=("manual-scan", result.serial), error=True)
        self._append_scan_activity(result, previous_signature)
        self._refresh_action_guidance()

    def _on_scan_finished(self) -> None:
        worker = self.sender()
        if not isinstance(worker, DeviceScanWorker):
            return
        if worker is self.scan_worker:
            self._scan_active = False
            self.scan_worker = None
        if worker.generation != self._scan_generation:
            worker.deleteLater()
            if self._closing:
                self._resume_pending_close()
                return
            if self._refresh_pending and not self._closing and not self._operation_active():
                self._refresh_pending = False
                self.refresh_devices(auto=True)
            return
        worker.deleteLater()
        if self._closing:
            self._refresh_pending = False
            self._resume_pending_close()
            return
        if self._operation_active():
            self._refresh_pending = False
            return
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh_devices(auto=True)

    def _update_backup_label(self, serial: str) -> None:
        self.workspace.exports_dir(serial)
        self.backup_path_label.setText("备份目录已就绪")
        if hasattr(self, "current_operation_panel"):
            self.current_operation_panel.setVisible(True)

    # ---------- settings / paths ----------
    def open_settings(self) -> None:
        previous_test_mode = MainWindow._test_mode_enabled(self)
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            new_settings = dialog.result_settings()
            try:
                self.settings_store.save(new_settings)
            except OSError as exc:
                QMessageBox.critical(self, "NEBUMTK", f"保存设置失败: {exc}")
                return
            self.settings = new_settings
            self._apply_test_mode_state(
                announce=previous_test_mode != MainWindow._test_mode_enabled(self)
            )
            path = self.settings.platform_tools_path or "(系统 PATH)"
            self._append_log(f"已保存 platform-tools: {path}")
            self._load_models()
            self.refresh_devices(auto=False)

    def open_backup_dir(self) -> None:
        if self._current_serial:
            target = self.workspace.exports_dir(self._current_serial)
        else:
            target = self.workspace_root
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def open_logs_dir(self) -> None:
        target = self.workspace.logs_dir()
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # ---------- preload / patched lk pickers ----------
    def _update_default_preload(self) -> None:
        key = (
            self.runtime_combo.currentData()
            if MainWindow._test_mode_enabled(self)
            else self.model_combo.currentData()
        )
        if not key:
            self.preload_edit.clear()
            return
        profile = self._get_profile(key)
        if profile is None:
            self.preload_edit.clear()
            return
        preload_ref = profile.privilege_runtime.preload_ref
        if preload_ref:
            self.preload_edit.setText(preload_ref)
            self._auto_preload_path = preload_ref
        else:
            self.preload_edit.clear()

    def _update_default_patched_lk(self) -> None:
        current = self.patched_lk_edit.text().strip()
        if current and current != self._auto_patched_lk_ref:
            return
        key = str(self.model_combo.currentData() or "")
        profile = self._get_profile(key)
        reference = ""
        if profile is not None and profile.brand.lower() == "honor" and profile.fastboot_artifact:
            reference = f"builtin:{profile.fastboot_artifact}"
        if reference:
            self.patched_lk_edit.setText(reference)
            self.fastboot_patched_lk_edit.setText(reference)
            self._auto_patched_lk_ref = reference
        elif self._auto_patched_lk_ref:
            self.patched_lk_edit.clear()
            self.fastboot_patched_lk_edit.clear()
            self._auto_patched_lk_ref = ""

    def _on_model_changed(self, _index: int) -> None:
        self._clear_xingc_context()
        self._synchronize_selected_configuration(sync_runtime=True)

    def _on_runtime_changed(self, _index: int) -> None:
        self._synchronize_selected_configuration(sync_runtime=False)

    def _synchronize_selected_configuration(self, *, sync_runtime: bool) -> None:
        """Apply the model/runtime pair immediately after either selector changes."""

        model_key = str(self.model_combo.currentData() or "")
        if sync_runtime:
            runtime_key = self._runtime_representative_by_model.get(model_key, "")
            runtime_index = self.runtime_combo.findData(runtime_key)
            with QSignalBlocker(self.runtime_combo):
                self.runtime_combo.setCurrentIndex(max(0, runtime_index))
        self._update_default_preload()
        self._update_default_patched_lk()
        self._refresh_action_guidance()

    def _test_mode_enabled(self) -> bool:
        settings = getattr(self, "settings", None)
        return bool(
            getattr(settings, "test_mode_enabled", False) and not is_online()
        )

    def _apply_test_mode_state(self, *, announce: bool) -> None:
        enabled = MainWindow._test_mode_enabled(self)
        self._confirmed_test_mode_fingerprint = None
        self.test_mode_banner.setVisible(enabled)
        if hasattr(self, "test_runtime_panel"):
            self.test_runtime_panel.setVisible(enabled)
        self.runtime_combo.setEnabled(enabled and not self._operation_active())
        if enabled:
            self._synchronize_selected_configuration(sync_runtime=True)
            if announce:
                self._append_activity(
                    "测试模式已开启",
                    key=("test-mode", "enabled"),
                    error=True,
                )
        else:
            self._synchronize_selected_configuration(sync_runtime=True)
            if announce:
                self._append_activity(
                    "测试模式已关闭",
                    key=("test-mode", "disabled"),
                )
        self._synchronize_selected_configuration(sync_runtime=False)

    def _confirm_test_mode_operation(self, flow_kind: str) -> bool:
        fingerprint = (
            self._current_serial,
            str(self.model_combo.currentData() or ""),
            str(self.runtime_combo.currentData() or ""),
            flow_kind,
        )
        if (
            not MainWindow._test_mode_enabled(self)
            or self._confirmed_test_mode_fingerprint == fingerprint
        ):
            return True
        reply = QMessageBox.warning(
            self,
            "确认测试模式操作",
            "测试模式将忽略当前设备与所选机型/运行配置的兼容匹配。\n\n"
            f"流程: {flow_kind}\n机型: {self.model_combo.currentText()}\n"
            f"运行配置: {self.runtime_combo.currentText()}\n\n"
            "序列号、模式、槽位、分区容量、镜像哈希和写入边界仍会强制校验。继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return False
        self._confirmed_test_mode_fingerprint = fingerprint
        return True

    def browse_patched_lk(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 patched lk")
        if path:
            self._set_selected_patched_lk(Path(path))

    def _set_selected_patched_lk(self, path: Path | str) -> None:
        value = str(path)
        self._auto_patched_lk_ref = ""
        self.patched_lk_edit.setText(value)
        self.fastboot_patched_lk_edit.setText(value)
        self._refresh_action_guidance()

    def browse_restore_lk(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择桌面备份中的原 LK",
            str(Path.home() / "Desktop"),
        )
        if path:
            self.restore_lk_edit.setText(path)
            self._refresh_action_guidance()

    def browse_source_lk(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择原始 LK")
        if not path:
            return
        source = Path(path)
        self.source_lk_edit.setText(str(source))
        if not self.output_lk_edit.text().strip():
            self.output_lk_edit.setText(str(source.with_name(f"{source.stem}_xingc{source.suffix or '.img'}")))

    def browse_output_lk(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "选择 patched LK 输出路径", self.output_lk_edit.text().strip())
        if path:
            self.output_lk_edit.setText(path)

    def browse_source_init_boot(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择原始 init_boot")
        if not path:
            return
        source = Path(path)
        self.source_init_boot_edit.setText(str(source))
        if not self.output_init_boot_edit.text().strip():
            self.output_init_boot_edit.setText(
                str(source.with_name(f"{source.stem}_sakisu{source.suffix or '.img'}"))
            )

    def browse_output_init_boot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择 patched init_boot 输出路径",
            self.output_init_boot_edit.text().strip(),
        )
        if path:
            self.output_init_boot_edit.setText(path)

    def browse_restore_dyn_backup(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择含 dyn 的桌面备份目录",
            str(Path.home() / "Desktop"),
        )
        if path:
            self.restore_dyn_backup_edit.setText(path)
            self._refresh_action_guidance()

    def _external_lk_paths(self, *, require_output: bool) -> tuple[Path, Path | None] | None:
        source = Path(self.source_lk_edit.text().strip())
        if not source.is_file() or source.stat().st_size <= 0:
            QMessageBox.warning(self, "NEBUMTK", "请选择非空的原始 LK 文件")
            return None
        if not require_output:
            return source, None
        output_text = self.output_lk_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "NEBUMTK", "请选择 patched LK 输出路径")
            return None
        output = Path(output_text)
        if source.resolve() == output.resolve():
            QMessageBox.warning(self, "NEBUMTK", "原始 LK 与输出路径不能相同")
            return None
        if not output.parent.is_dir():
            QMessageBox.warning(self, "NEBUMTK", "输出目录不存在")
            return None
        return source, output

    def _external_init_boot_paths(self) -> tuple[Path, Path] | None:
        source = Path(self.source_init_boot_edit.text().strip())
        if not source.is_file() or source.stat().st_size <= 0:
            QMessageBox.warning(self, "NEBUMTK", "请选择非空的原始 init_boot 文件")
            return None
        output_text = self.output_init_boot_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, "NEBUMTK", "请选择 patched init_boot 输出路径")
            return None
        output = Path(output_text)
        if source.resolve() == output.resolve():
            QMessageBox.warning(self, "NEBUMTK", "原始 init_boot 与输出路径不能相同")
            return None
        if not output.parent.is_dir():
            QMessageBox.warning(self, "NEBUMTK", "输出目录不存在")
            return None
        return source, output

    def _start_reboot(self, target: str) -> None:
        if self._operation_active() or not self._current_serial or self._current_mode not in {"adb", "fastboot"}:
            return
        labels = {"system": "系统", "fastbootd": "Fastbootd", "bootloader": "Bootloader", "recovery": "Recovery"}
        warning = "Recovery 目标无法自动确认。" if target == "recovery" else "工具会在请求后验证目标状态。"
        reply = QMessageBox.question(
            self,
            "确认重启",
            f"设备: {self._current_serial}\n当前模式: {self._current_mode}\n\n重启到 {labels[target]}？\n{warning}\n不会修改分区数据。",
        )
        if reply != QMessageBox.Yes:
            return
        try:
            tools = EnvChecker(self.settings).validate(
                need_adb=self._current_mode == "adb" or target == "system",
                need_fastboot=self._current_mode == "fastboot",
            )
        except EnvError as exc:
            QMessageBox.critical(self, "NEBUMTK", str(exc))
            return
        self.reboot_worker = RebootWorker(
            adb_binary=tools.adb if tools.adb else None,
            fastboot_binary=tools.fastboot if tools.fastboot else None,
            serial=self._current_serial,
            source_mode=self._current_mode,
            target=target,
            parent=self,
        )
        self._flow_active = True
        self._pause_device_scanning()
        self._set_busy(True)
        self._append_activity(f"请求重启到 {labels[target]}…", key=("reboot-start", target, self._current_serial))
        self.reboot_worker.progress_changed.connect(self._set_indeterminate_progress)
        self.reboot_worker.activity_event.connect(self._on_activity_event)
        self.reboot_worker.result_ready.connect(self._on_reboot_result)
        self.reboot_worker.finished_error.connect(self._on_reboot_error)
        self.reboot_worker.cancelled_signal.connect(lambda: self._append_activity("已停止等待重启目标确认", key=("reboot-cancelled",)))
        self.reboot_worker.finished.connect(self._on_reboot_finished)
        self.reboot_worker.start()

    def _on_reboot_result(self, result: object) -> None:
        state, detail = result
        if state == "verified":
            self._append_activity(f"已确认重启到 {detail}", key=("reboot-verified", detail))
        else:
            self._append_activity(str(detail), key=("reboot-requested", detail))

    def _on_reboot_error(self, message: str) -> None:
        self._append_activity("重启目标未确认", key=("reboot-error", message), error=True)
        QMessageBox.warning(self, "NEBUMTK", message)

    def _on_reboot_finished(self) -> None:
        worker = self.sender()
        if worker is not self.reboot_worker:
            return
        worker.deleteLater()
        self.reboot_worker = None
        self._flow_active = False
        self._reset_progress()
        self._set_busy(False)
        if self._closing:
            self._resume_pending_close()
        else:
            self._resume_device_scanning()

    def _start_utility(self, operation: str, payload: dict[str, object]) -> None:
        if self._operation_active():
            return
        device_bound = operation.startswith("xingc_")
        launch_serial = ""
        if device_bound:
            launch_serial = str(payload.get("serial", "")).strip()
            if not launch_serial or launch_serial != self._current_serial:
                self._clear_xingc_context()
                QMessageBox.warning(self, "NEBUMTK", "设备状态已变化，请重新读取 Xingc Token")
                return
            payload = dict(payload)
            payload["serial"] = launch_serial
        session_log = SessionLog(self.workspace.logs_dir(), lambda _message: None)
        if session_log.path is not None and hasattr(self, "log_file_label"):
            self.log_file_label.setText("日志: 已启用")
        worker = UtilityWorker(operation, payload, self)
        self.utility_worker = worker
        self._utility_device_bound = device_bound
        self._utility_serial = launch_serial
        self._pause_device_scanning()
        self._set_busy(True)
        worker.log_message.connect(session_log)
        worker.progress_changed.connect(self._set_indeterminate_progress)
        worker.result_ready.connect(self._on_utility_result)
        worker.finished_error.connect(self._on_utility_error)
        worker.cancelled_signal.connect(self._on_utility_cancelled)
        worker.finished.connect(self._on_utility_finished)
        worker.start()

    def _set_image_status(self, text: str, state: str) -> None:
        if not hasattr(self, "image_status_label"):
            return
        self.image_status_label.setText(text)
        self.image_status_label.setProperty("statusState", state)
        self.image_status_label.style().unpolish(self.image_status_label)
        self.image_status_label.style().polish(self.image_status_label)

    def _on_utility_result(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) not in {2, 3}:
            raise VerificationError("工具返回结果格式非法")
        if len(result) == 3:
            operation, result_serial, value = result
            result_serial = str(result_serial).strip()
        else:
            operation, value = result
            result_serial = ""
        if str(operation).startswith("xingc_") and (
            not self._utility_device_bound
            or not result_serial
            or result_serial != self._utility_serial
            or result_serial != self._current_serial
        ):
            self._clear_xingc_context()
            self._append_activity(
                "Xingc 设备上下文已变化，结果已丢弃",
                key=("xingc-device-race", str(operation)),
                error=True,
            )
            QMessageBox.warning(self, "NEBUMTK", "设备状态已变化，请重新读取 Xingc Token")
            return
        if operation == "inspect_lk":
            ok, detail = value
            self.activity_stage_label.setText("外部 LK 检测通过" if ok else "外部 LK 检测失败")
            self._append_activity("外部 LK 检测通过" if ok else "外部 LK 不可修补", key=(operation, ok), error=not ok)
            self._set_image_status(
                "外部 LK 检测通过" if ok else f"外部 LK 不可修补：{detail}",
                "ready" if ok else "warning",
            )
            if not ok:
                QMessageBox.warning(self, "NEBUMTK", f"外部 LK 不可修补: {detail}")
        elif operation == "xingc_token":
            token = value
            self._xingc_token, self._xingc_payload, self._xingc_serial = token, b"", result_serial
            self._append_activity("Xingc Token 已读取", key=(operation,))
        elif operation == "xingc_sign":
            self._xingc_payload = value
            self._append_activity("Xingc Token 已签名", key=(operation,))
        elif operation == "xingc_auth":
            self._append_activity(f"Xingc Auth 已发送：{value.payload_size} 字节", key=(operation, value.payload_size))
            self._clear_xingc_context()
        self._refresh_action_guidance()

    def _on_utility_error(self, message: str) -> None:
        if self.utility_worker is not None and self.utility_worker.operation.startswith("xingc_"):
            self._clear_xingc_context()
        self.activity_stage_label.setText("工具操作失败")
        self._append_activity("工具操作失败", key=("utility-error", message), error=True)
        if self.utility_worker is not None and self.utility_worker.operation == "inspect_lk":
            self._set_image_status(f"LK 工具操作失败：{message}", "error")
        QMessageBox.critical(self, "NEBUMTK", message)

    def _on_utility_cancelled(self) -> None:
        if self.utility_worker is not None and self.utility_worker.operation.startswith("xingc_"):
            self._clear_xingc_context()
        self.activity_stage_label.setText("工具操作已取消")
        self._append_activity("工具操作已取消", key=("utility-cancelled",))

    def _on_utility_finished(self) -> None:
        worker = self.sender()
        if worker is not self.utility_worker:
            return
        worker.deleteLater()
        self.utility_worker = None
        self._utility_device_bound = False
        self._utility_serial = ""
        self._reset_progress()
        self._set_busy(False)
        if self._closing:
            self._resume_pending_close()
        else:
            self._resume_device_scanning()

    def inspect_external_lk(self) -> None:
        if self._operation_active():
            return
        paths = self._external_lk_paths(require_output=False)
        if paths is None:
            return
        source, _ = paths
        model_key = str(self.model_combo.currentData() or "").strip()
        profile = self._profiles_cache.get(model_key)
        image_profile = profile.xingc_patch_profile if profile is not None else model_key
        self._start_utility(
            "inspect_lk",
            {"source": str(source), "image_profile": image_profile},
        )

    # ---------- CMD ----------
    def run_cmd(self) -> None:
        if self._operation_active():
            QMessageBox.warning(self, "NEBUMTK", "当前操作尚未结束，诊断功能暂不可用")
            return
        command = self.cmd_edit.text().strip()
        if not command:
            return

        from ..core.terminal_policy import parse_diagnostic_command

        try:
            parsed = parse_diagnostic_command(command)
        except VerificationError as exc:
            QMessageBox.warning(self, "NEBUMTK", str(exc))
            return
        use_fastboot = parsed.tool == "fastboot"

        try:
            tools = EnvChecker(self.settings).validate(
                need_adb=not use_fastboot,
                need_fastboot=use_fastboot,
            )
        except EnvError as exc:
            QMessageBox.critical(self, "NEBUMTK", str(exc))
            return

        binary = tools.fastboot if use_fastboot else tools.adb
        args = list(parsed.args)
        full = [binary]
        if self._current_serial and args != ["devices"]:
            full.extend(["-s", self._current_serial])
        full.extend(args)

        self._append_activity("执行诊断命令…", key=("terminal-start", tuple(args)))
        session_log = SessionLog(self.workspace.logs_dir(), lambda _message: None)
        if session_log.path is not None and hasattr(self, "log_file_label"):
            self.log_file_label.setText("日志: 已启用")
        self.terminal_worker = TerminalWorker(full, self)
        self._pause_device_scanning()
        self._set_busy(True)
        log_serial = self._current_serial
        self.terminal_worker.result_ready.connect(
            lambda result: self._on_terminal_result(session_log, full, result, log_serial)
        )
        self.terminal_worker.finished_error.connect(
            lambda message: self._on_terminal_error(session_log, message, log_serial)
        )
        self.terminal_worker.cancelled_signal.connect(self._on_terminal_cancelled)
        self.terminal_worker.finished.connect(self._on_terminal_finished)
        self.terminal_worker.start()

    @staticmethod
    def _redact_persistent(value: object, serial: str = "") -> str:
        text = str(value)
        normalized = serial.strip()
        return text.replace(normalized, "<device>") if normalized else text

    def _on_terminal_result(
        self,
        session_log: SessionLog,
        command: list[str],
        result,
        serial: str = "",
    ) -> None:
        session_log("$ " + self._redact_persistent(" ".join(command), serial))
        for line in (result.stdout or "").splitlines():
            if line.strip():
                session_log(self._redact_persistent(line.strip(), serial))
        for line in (result.stderr or "").splitlines():
            if line.strip():
                session_log(self._redact_persistent(line.strip(), serial))
        if result.returncode:
            session_log(f"exit {result.returncode}")
            self._append_activity("诊断命令执行失败", key=("terminal-exit", result.returncode), error=True)
        else:
            session_log("exit 0")
            self._append_activity("诊断命令已执行", key=("terminal-ok",))

    def _on_terminal_error(
        self,
        session_log: SessionLog,
        message: str,
        serial: str = "",
    ) -> None:
        session_log(f"诊断命令失败: {self._redact_persistent(message, serial)}")
        self._append_activity("诊断命令执行失败", key=("terminal-error", message), error=True)

    def _on_terminal_cancelled(self) -> None:
        self._append_activity("诊断命令已取消", key=("terminal-cancelled",))

    def _on_terminal_finished(self) -> None:
        worker = self.sender()
        if worker is not self.terminal_worker:
            return
        worker.deleteLater()
        self.terminal_worker = None
        self._reset_progress()
        self._set_busy(False)
        if self._closing:
            self._resume_pending_close()
        else:
            self._resume_device_scanning()

    # ---------- start flow helpers ----------
    def _resolve_preload_override(self, *, required: bool = False) -> Path | str | None:
        text = self.preload_edit.text().strip()
        if not text:
            if required:
                QMessageBox.warning(self, "NEBUMTK", "当前机型未配置内置受保护 preload")
            return None
        if not text.startswith("builtin:"):
            QMessageBox.warning(self, "NEBUMTK", "禁止使用明文 preload.so")
            return None
        return text

    def _resolve_patched_lk(self) -> Path | str | None:
        text = self.patched_lk_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "NEBUMTK", "请选择 patched lk")
            return None
        if text.startswith("builtin:"):
            profile = self._get_profile(str(self.model_combo.currentData() or ""))
            expected = f"builtin:{profile.fastboot_artifact}" if profile is not None else ""
            if profile is None or profile.brand.lower() != "honor" or not profile.fastboot_artifact or text != expected:
                QMessageBox.warning(self, "NEBUMTK", "内置 patched LK 只允许使用当前 Honor 机型的受保护 artifact")
                return None
            return text
        path = Path(text)
        if not path.is_file():
            QMessageBox.warning(self, "NEBUMTK", f"patched lk 不存在: {path}")
            return None
        return path

    def _selected_brand(self) -> str:
        profile = self._get_profile(self.model_combo.currentData() or "")
        return (profile.brand.lower() if profile else "")

    def _require_mode(self, expected: str) -> bool:
        if self._current_mode != expected:
            QMessageBox.warning(
                self,
                "NEBUMTK",
                f"当前连接模式是 {self._current_mode or '未检测到'}，此步骤需要 {expected}。"
                "请先刷新并确认设备状态。",
            )
            return False
        return True

    def _start_common(self, flow_kind: str) -> None:
        if self._operation_active():
            return
        if not self._require_mode("adb"):
            return
        need_preload = flow_kind == "root_escalation"
        preload = self._resolve_preload_override(required=need_preload)
        if need_preload and preload is None:
            return
        self._start_flow(flow_kind, preload_override=preload)

    def _start_write(self) -> None:
        if self._operation_active():
            return
        if not self._require_mode("adb"):
            return
        preload = self._resolve_preload_override(required=False)
        patched_lk = self._resolve_patched_lk()
        if patched_lk is None:
            return
        self._start_flow("write_inactive", patched_lk=patched_lk, preload_override=preload)

    def _start_patch_lk(self) -> None:
        if self._operation_active():
            return
        paths = self._external_lk_paths(require_output=True)
        if paths is None:
            return
        source, output = paths
        assert output is not None
        model_key = self.model_combo.currentData() or ""
        if not model_key:
            QMessageBox.warning(self, "NEBUMTK", "请先选择目标机型")
            return
        reply = QMessageBox.question(
            self,
            "确认本地修补 LK",
            "本操作仅使用显式选择的本地文件，不会连接、写入或重启设备。\n\n"
            f"机型: {self.model_combo.currentText()}\n"
            f"输入: {source}\n输出: {output}\n\n"
            "将执行内部 UBL 预检、修补和独立验证。确认开始？",
        )
        if reply != QMessageBox.Yes:
            return
        self._start_flow(
            "patch_lk",
            source_lk=source,
            patched_lk=output,
            confirm=False,
        )

    def _start_patch_init_boot(self) -> None:
        if self._operation_active():
            return
        if not self._require_mode("adb"):
            return
        if self._selected_brand() != "vivo":
            QMessageBox.warning(self, "NEBUMTK", "SakiSU init_boot 修补目前仅适用于 Vivo")
            return
        paths = self._external_init_boot_paths()
        if paths is None:
            return
        source, output = paths
        reply = QMessageBox.question(
            self,
            "确认本地修补 init_boot",
            "本操作将通过 ADB 仅读取当前设备内核 KMI，并修补所选本地 init_boot。\n"
            "不会临时提权、不会写入设备、不会安装或管理 Manager。\n\n"
            f"机型: {self.model_combo.currentText()}\n"
            f"输入: {source}\n输出: {output}\n\n"
            "确认开始？",
        )
        if not MainWindow._is_yes_reply(reply):
            return
        self._start_flow(
            "patch_init_boot",
            source_init_boot=source,
            output_init_boot=output,
            confirm=False,
        )

    def _start_restore_vivo_dyn(self) -> None:
        if self._operation_active():
            return
        if not self._require_mode("adb"):
            return
        if self._selected_brand() != "vivo":
            QMessageBox.warning(self, "NEBUMTK", "Vivo dyn 还原仅适用于 Vivo")
            return
        directory = Path(self.restore_dyn_backup_edit.text().strip())
        if not directory.is_dir():
            QMessageBox.warning(self, "NEBUMTK", "请选择有效的桌面 dyn 备份目录")
            return
        reply = QMessageBox.question(
            self,
            "确认还原 Vivo dyn",
            "将验证所选桌面备份与当前机型、设备序列号，并仅使用系统内已授权的 su 还原 dyn。\n"
            "缺少授权 su 时不会临时提权，也不会写入文件。还原成功后设备会重启。\n\n"
            f"设备: {self._current_serial}\n备份: {directory}\n\n确认开始？",
        )
        if not MainWindow._is_yes_reply(reply):
            return
        self._start_flow(
            "restore_vivo_dyn",
            source_dyn_backup=directory,
            preload_override=None,
            confirm=False,
        )

    def _start_auto_flow(self) -> None:
        if self._operation_active():
            return
        if MainWindow._test_mode_enabled(self):
            QMessageBox.warning(
                self,
                "NEBUMTK",
                "测试模式不允许一键流程，请使用分步操作。",
            )
            return
        if not self._require_mode("adb"):
            return
        if any(
            field.text().strip()
            for field in (
                self.source_lk_edit,
                self.output_lk_edit,
                self.patched_lk_edit,
            )
        ):
            QMessageBox.warning(
                self,
                "NEBUMTK",
                "检测到 custom LK。一键流程只使用本次设备备份并自动修补；"
                "如需 custom LK，请清空这些字段并改用分步流程。",
            )
            return
        if self._selected_brand() != "vivo":
            QMessageBox.warning(self, "NEBUMTK", "完整解锁流程目前仅适用于 Vivo")
            return
        try:
            profile = self._get_profile(str(self.model_combo.currentData() or ""))
            if profile is None:
                raise VerificationError("请选择有效 Vivo 机型")
        except Exception as exc:
            QMessageBox.warning(self, "NEBUMTK", f"一键流程前置检查失败: {exc}")
            return
        preload = self._resolve_preload_override(required=True)
        if preload is None:
            return
        install_sakisu_root = bool(
            self.sakisu_root_checkbox.isEnabled()
            and self.sakisu_root_checkbox.isChecked()
        )
        root_summary = (
            "将修补原槽 init_boot 并在 fastbootd 收尾时仅写入原槽；工具不会安装 Manager。"
            if install_sakisu_root
            else "不写入 init_boot Root。"
        )
        reply = QMessageBox.question(
            self,
            "确认 Vivo 完整解锁",
            "将执行临时提权、桌面备份、修补本次备份 LK、写入对槽、"
            "Xingc Unlock 和双槽 LK 同步。\n\n"
            f"设备: {self._current_serial}\n机型: {self.model_combo.currentText()}\n"
            "LK: 本次活动槽备份自动修补\n"
            f"SakiSU Root: {root_summary}\n\n"
            "所有破坏性边界都会重新校验；刷写开始后不可取消。确认开始？",
        )
        if not MainWindow._is_yes_reply(reply):
            return
        self._start_flow(
            "vivo_complete_unlock",
            preload_override=preload,
            install_sakisu_root=install_sakisu_root,
            confirm=False,
        )

    def _start_brand_flow(self, brand: str, flow_kind: str) -> None:
        if self._operation_active():
            return
        if not self._require_mode("fastboot"):
            return
        selected = self._selected_brand()
        if selected != brand and not MainWindow._test_mode_enabled(self):
            QMessageBox.warning(self, "NEBUMTK", f"当前机型不是 {brand.upper()}，禁止执行该步骤")
            return
        self._start_flow(flow_kind, preload_override=None)

    def _xingc_ready(self) -> bool:
        if self._operation_active():
            return False
        if not self._require_mode("fastboot"):
            return False
        if self._selected_brand() != "vivo" and not MainWindow._test_mode_enabled(self):
            QMessageBox.warning(self, "NEBUMTK", "当前机型不是 VIVO，禁止执行 Xingc 操作")
            return False
        return True

    def _clear_xingc_context(self) -> None:
        self._xingc_token = ""
        self._xingc_payload = b""
        self._xingc_serial = ""

    def _xingc_read_token(self) -> None:
        if not self._xingc_ready():
            return
        self._start_utility("xingc_token", {"serial": self._current_serial})

    def _xingc_sign_token(self) -> None:
        if not self._xingc_ready():
            return
        if not self._xingc_token or self._xingc_serial != self._current_serial:
            QMessageBox.warning(self, "NEBUMTK", "请先为当前设备读取新的 Xingc Token")
            return
        self._start_utility(
            "xingc_sign",
            {"serial": self._current_serial, "token": self._xingc_token},
        )

    def _xingc_authorize(self) -> None:
        if not self._xingc_ready():
            return
        if not self._xingc_payload or self._xingc_serial != self._current_serial:
            QMessageBox.warning(self, "NEBUMTK", "请先读取并签名当前设备的 Xingc Token")
            return
        self._start_utility(
            "xingc_auth",
            {"serial": self._current_serial, "payload": self._xingc_payload},
        )

    def _xingc_lock(self) -> None:
        QMessageBox.warning(
            self,
            "NEBUMTK",
            "Lock 仅能通过受控恢复流程执行：必须先恢复并验证原始 LK 的双槽状态。\n"
            "当前版本不会从此快捷入口直接锁定，以避免设备不可启动。",
        )

    def _start_vivo_xingc_unlock(self) -> None:
        if self._operation_active():
            return
        if not self._require_mode("fastboot"):
            return
        if self._selected_brand() != "vivo" and not MainWindow._test_mode_enabled(self):
            QMessageBox.warning(self, "NEBUMTK", "当前机型不是 VIVO，禁止执行 Xingc 解锁")
            return
        reply = QMessageBox.question(
            self,
            "确认 Xingc 解锁",
            "将使用内置密钥执行已签名的 Xingc 授权交换。\n"
            f"设备: {self._current_serial}\n机型: {self.model_combo.currentText()}\n\n"
            "当前 serial、Bootloader Fastboot 状态和 Xingc 协议结果全部通过后才会发送 unlock。确认开始？",
        )
        if reply != QMessageBox.Yes:
            return
        self._start_flow("vivo_xingc_unlock", preload_override=None, confirm=False)

    def _start_dual_slot_sync(self) -> None:
        if self._operation_active():
            return
        if not self._require_mode("fastboot"):
            return
        if self._selected_brand() not in {"vivo", "honor"} and not MainWindow._test_mode_enabled(self):
            QMessageBox.warning(self, "NEBUMTK", "当前 patched LK 双槽同步仅支持 Vivo 或 Honor")
            return
        patched_lk = self._resolve_patched_lk()
        if patched_lk is None:
            return
        self._start_flow(
            "dual_slot_sync",
            patched_lk=patched_lk,
            preload_override=None,
        )

    def _start_restore_backup_lk(self) -> None:
        if self._operation_active():
            return
        if not self._require_mode("fastboot"):
            return
        source = Path(self.restore_lk_edit.text().strip())
        if not source.is_file() or source.stat().st_size <= 0:
            QMessageBox.warning(self, "NEBUMTK", "请选择桌面备份目录中的非空原 LK")
            return
        self._start_flow(
            "restore_backup_lk",
            source_lk=source,
            preload_override=None,
        )

    def _on_worker_confirmation_requested(self, message: str) -> None:
        worker = self.worker
        if worker is None or not hasattr(worker, "_confirmation_wait"):
            return
        if self._closing:
            worker._confirmation_result = False
            worker._confirmation_wait.set()
            return
        reply = QMessageBox.question(self, "确认破坏性操作", message)
        worker._confirmation_result = MainWindow._is_yes_reply(reply)
        worker._confirmation_wait.set()

    def cancel_flow(self) -> None:
        if not self._operation_active():
            return
        self.cancel_button.setEnabled(False)
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.step_label.setText("正在取消…")
        elif self.utility_worker and self.utility_worker.isRunning():
            self.utility_worker.cancel()
            self.activity_stage_label.setText("正在取消工具操作…")
        elif self.terminal_worker and self.terminal_worker.isRunning():
            self.terminal_worker.cancel()
            self.activity_stage_label.setText("正在取消诊断命令…")
        elif self.reboot_worker and self.reboot_worker.isRunning():
            self.reboot_worker.cancel()
            self.activity_stage_label.setText("正在停止等待…")

    def _start_flow(
        self,
        flow_kind: str,
        *,
        patched_lk: Path | str | None = None,
        source_lk: Path | None = None,
        source_dyn_backup: Path | None = None,
        source_init_boot: Path | None = None,
        output_init_boot: Path | None = None,
        preload_override: Path | str | None = None,
        install_sakisu_root: bool = False,
        confirm: bool = True,
    ) -> None:
        if self._operation_active():
            return
        from ..flows.factory import FASTBOOT_FLOWS, LOCAL_FLOWS

        is_local = flow_kind in LOCAL_FLOWS
        target_serial = "" if is_local else self._current_serial
        if not is_local and not target_serial:
            QMessageBox.warning(self, "NEBUMTK", "未检测到设备")
            return
        if confirm:
            reply = QMessageBox.question(self, "NEBUMTK", "确认开始？")
            if not MainWindow._is_yes_reply(reply):
                return

        if flow_kind == "vivo_complete_unlock":
            need_adb = True
            need_fastboot = True
        else:
            need_adb = flow_kind not in FASTBOOT_FLOWS and not is_local
            need_fastboot = flow_kind in FASTBOOT_FLOWS
        if not is_local:
            try:
                EnvChecker(self.settings).validate(need_adb=need_adb, need_fastboot=need_fastboot)
            except EnvError as exc:
                QMessageBox.critical(self, "NEBUMTK", str(exc))
                return

        model_key = self.model_combo.currentData() or ""
        if not model_key:
            QMessageBox.warning(self, "NEBUMTK", "请先选择机型")
            return
        skip_profile_identity = MainWindow._test_mode_enabled(self)
        if skip_profile_identity and flow_kind == "vivo_complete_unlock":
            QMessageBox.warning(
                self,
                "NEBUMTK",
                "品牌完整解锁不允许测试兼容模式，请关闭测试模式后重试",
            )
            return
        runtime_model_key = (
            str(self.runtime_combo.currentData() or "")
            if skip_profile_identity
            else str(model_key)
        )
        if skip_profile_identity and not runtime_model_key:
            QMessageBox.warning(self, "NEBUMTK", "测试模式需要选择运行配置")
            return
        if (
            skip_profile_identity
            and not is_local
            and not MainWindow._confirm_test_mode_operation(self, flow_kind)
        ):
            return
        # 完整诊断仅写入日志文件；右侧仅显示简短活动阶段。
        session_log = SessionLog(self.workspace.logs_dir(), lambda _message: None)
        if session_log.path is not None and hasattr(self, "log_file_label"):
            self.log_file_label.setText("日志: 已启用")
        if skip_profile_identity:
            session_log("测试模式：跳过机型身份匹配")

        flow_label = FLOW_DISPLAY_NAMES.get(flow_kind, "设备操作")
        self._active_flow_label = flow_label
        self._flow_active = True
        self._flow_cancelled = False
        self._append_activity(f"{flow_label} 开始", key=("flow-start", flow_label))
        self._set_device_presentation(
            "busy",
            serial=target_serial,
            mode=self._current_mode,
            detail="流程执行中，设备扫描已暂停",
        )
        self._pause_device_scanning()
        self._set_busy(True)
        self.worker = FlowWorker(
            self.project_root,
            self.workspace_root,
            self.settings,
            target_serial,
            model_key,
            flow_kind,
            patched_lk=patched_lk,
            source_lk=source_lk,
            source_dyn_backup=source_dyn_backup,
            source_init_boot=source_init_boot,
            output_init_boot=output_init_boot,
            preload_override=preload_override,
            install_sakisu_root=(
                bool(install_sakisu_root)
                if flow_kind == "vivo_complete_unlock"
                else False
            ),
            skip_profile_identity=skip_profile_identity,
            runtime_model_key=runtime_model_key,
            parent=self,
        )
        self.worker.log_message.connect(session_log)
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.activity_event.connect(self._on_activity_event)
        if hasattr(self.worker, "confirmation_requested"):
            self.worker.confirmation_requested.connect(self._on_worker_confirmation_requested)
        self.worker.finished_ok.connect(self._on_success)
        self.worker.finished_error.connect(self._on_error)
        self.worker.cancelled_signal.connect(self._on_cancelled)
        self.worker.finished.connect(self._on_flow_finished)
        self.worker.start()

    def _set_busy(self, busy: bool) -> None:
        for button in MainWindow._all_flow_buttons(self):
            button.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)
        self.sakisu_root_checkbox.setEnabled(not busy)
        runtime_combo = getattr(self, "runtime_combo", None)
        if runtime_combo is not None:
            runtime_combo.setEnabled(
                not busy and MainWindow._test_mode_enabled(self)
            )
        self.preload_edit.setEnabled(not busy)
        self.patched_lk_edit.setEnabled(not busy)
        self.fastboot_patched_lk_edit.setEnabled(not busy)
        self.restore_lk_edit.setEnabled(not busy)
        self.source_lk_edit.setEnabled(not busy)
        self.output_lk_edit.setEnabled(not busy)
        self.source_init_boot_edit.setEnabled(not busy)
        self.output_init_boot_edit.setEnabled(not busy)
        self.restore_dyn_backup_edit.setEnabled(not busy)
        self.cmd_edit.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        if not busy:
            self._refresh_action_guidance()

    def _set_indeterminate_progress(self, label: str) -> None:
        for bar in (self.progress_bar, self.activity_progress):
            bar.setRange(0, 0)
        self.step_label.setText(label)
        self.activity_stage_label.setText(label)

    def _reset_progress(self) -> None:
        for bar in (self.progress_bar, self.activity_progress):
            bar.setRange(0, 100)
        self._last_progress_value = None

    def _on_progress(self, value: int, label: str) -> None:
        if self._last_progress_value == value:
            self._set_indeterminate_progress(label)
        else:
            for bar in (self.progress_bar, self.activity_progress):
                bar.setRange(0, 100)
                bar.setValue(value)
            self.step_label.setText(label)
            self.activity_stage_label.setText(label)
            self._last_progress_value = value
        self._append_activity(label, key=("stage", value, label))

    def _safe_activity_text(self, event: ActivityEvent) -> str:
        title = str(event.title or "").strip()
        detail = str(event.detail or "").strip()
        for sensitive in (
            event.serial,
            self._current_serial,
            self._context_serial,
            self._utility_serial,
        ):
            value = str(sensitive or "").strip()
            if value:
                title = title.replace(value, "<设备>")
                detail = detail.replace(value, "<设备>")
        title = compact_log_message(title)
        detail = compact_log_message(detail)
        if detail and detail != title:
            return compact_log_message(f"{title} · {detail}")
        return title or detail or "设备操作"

    def _on_activity_event(self, event: object) -> None:
        if not isinstance(event, ActivityEvent):
            return
        text = self._safe_activity_text(event)
        self.activity_stage_label.setText(text)
        self._append_activity(
            text,
            key=("activity", str(event.kind), text),
            error=event.kind in {ActivityKind.ERROR, ActivityKind.TIMEOUT},
        )

    def _on_cancelled(self) -> None:
        self._flow_cancelled = True
        self._reset_progress()
        self.step_label.setText("已取消")
        self.activity_stage_label.setText("已取消")
        self._append_activity("流程已取消", key=("flow-cancelled",))

    def _on_error(self, message: str) -> None:
        if self._flow_cancelled:
            return
        self._reset_progress()
        self.step_label.setText("失败")
        self.activity_stage_label.setText("失败")
        short = message if len(message) <= 800 else message[:797] + "..."
        self._append_activity("流程失败", key=("flow-error", short), error=True)
        QMessageBox.critical(self, "NEBUMTK", short)

    def _on_success(self) -> None:
        if self.worker is not None and self.worker.flow_kind == "patch_lk":
            output = self.worker.patched_lk
            if output is not None and output.is_file():
                self._set_selected_patched_lk(output)
                self._set_image_status("patched LK 已生成并选中", "ready")
        self._reset_progress()
        self.step_label.setText("完成")
        self.activity_stage_label.setText("完成")
        label = self._active_flow_label or "设备操作"
        self._append_activity(f"{label} 完成", key=("flow-success", label))

    def _on_flow_finished(self) -> None:
        finished_worker = self.sender()
        if finished_worker is not self.worker:
            return
        self._flow_active = False
        self._active_flow_label = ""
        finished_worker.deleteLater()
        self.worker = None
        self._reset_progress()
        self._set_busy(False)
        self._set_device_presentation(
            "scanning",
            serial=self._current_serial,
            mode=self._current_mode,
            detail="正在重新确认设备状态",
        )
        if self._closing:
            self._resume_pending_close()
            return
        self._resume_device_scanning()

    def _append_activity(
        self,
        message: str,
        *,
        key: tuple[object, ...],
        error: bool = False,
    ) -> None:
        if key == self._last_activity_key:
            return
        self._last_activity_key = key
        self._append_log(message, error=error)

    def _append_log(self, message: str, *, error: bool = False) -> None:
        text = compact_log_message(message)
        if not text:
            return
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if error:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#c0392b"))
            cursor.insertText(text + "\n", fmt)
        else:
            cursor.insertText(text + "\n")
        self.log_view.setTextCursor(cursor)
        bar = self.log_view.verticalScrollBar()
        if bar is not None:
            bar.setValue(bar.maximum())

    def _operation_workers(self) -> tuple[object, ...]:
        return tuple(
            worker
            for worker in (
                self.worker,
                self.utility_worker,
                self.terminal_worker,
                self.reboot_worker,
            )
            if worker is not None and worker.isRunning()
        )

    def _resume_pending_close(self) -> None:
        scan_running = bool(self.scan_worker is not None and self.scan_worker.isRunning())
        if self._closing and not self._operation_workers() and not scan_running:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:
        active_workers = self._operation_workers()
        if active_workers:
            reply = QMessageBox.question(
                self, "NEBUMTK",
                "操作运行中，确认退出？（将先安全取消，操作结束后再关闭）",
            )
            if reply != QMessageBox.Yes:
                self._closing = False
                event.ignore()
                return
        self._closing = True
        self.device_scan_timer.stop()
        self._refresh_pending = False
        self._clear_xingc_context()
        if active_workers:
            for worker in active_workers:
                worker.cancel()
            event.ignore()
            return
        if self.scan_worker and self.scan_worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)
