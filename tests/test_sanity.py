"""Focused sanity tests for the session-free core boundary."""

from __future__ import annotations

import json
import hashlib
import tempfile
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebumtk_unlock_tools.config.store import Settings, SettingsStore
from nebumtk_unlock_tools.core.device import ConnectedDevice, DeviceDetector
from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.core.safety import SafetyChecker
from nebumtk_unlock_tools.core.platform_tools import (
    BUILTIN_PLATFORM_TOOL_SHA256,
    find_builtin_platform_tools,
)
from nebumtk_unlock_tools.core.slot import SlotManager
from nebumtk_unlock_tools.core.workspace import WorkspaceManager
from nebumtk_unlock_tools.flows.base import FlowCallbacks, FlowContext
from nebumtk_unlock_tools.resources.loader import ResourceLoader


def test_persistent_root_runtime_identifiers_are_absent_from_source() -> None:
    package = _root() / "nebumtk_unlock_tools"
    forbidden = (
        "Root" + "Runtime",
        "root" + "_runtime",
        "with" + "_root_runtime",
        "runtime" + "_override",
    )
    for path in package.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for identifier in forbidden:
            assert re.search(rf"\b{re.escape(identifier)}\b", source) is None, path


FORBIDDEN_WORKSPACE_APIS = {
    "device_dir",
    "current_dir",
    "artifacts_dir",
    "session_path",
    "list_sessions",
    "ensure_current",
    "load_session",
    "save_session",
    "init_session",
    "mark_phase",
    "record_phase_outcome",
    "record_phase_error",
    "register_patched_lk",
    "patched_lk_path",
    "invalidate_backup_authorization",
    "register_backup_records",
    "verify_export_backup",
    "register_patched_init_boot",
    "patched_init_boot_path",
    "export_lk_path",
    "verify_export_lk",
}


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_checked_in_platform_tools_match_compiled_pins() -> None:
    directory = _root() / "platform-tools"
    for name, expected in BUILTIN_PLATFORM_TOOL_SHA256.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected


def test_builtin_platform_tools_reject_same_name_tamper(tmp_path: Path) -> None:
    directory = tmp_path / "platform-tools"
    directory.mkdir()
    (directory / "adb.exe").write_bytes(b"tampered-adb")
    (directory / "fastboot.exe").write_bytes(b"tampered-fastboot")

    assert find_builtin_platform_tools((tmp_path,)) is None


def test_workspace_boundary_sanity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "workspace"
        workspace = WorkspaceManager(root)

        assert not root.exists()
        assert workspace.logs_dir() == root / "logs"
        assert not root.exists()
        assert FORBIDDEN_WORKSPACE_APIS.isdisjoint(dir(workspace))

        exports = workspace.exports_dir("serial/../danger label")
        assert exports == root / "exports" / "serial-danger-label"
        assert exports.is_dir()
        assert exports.resolve().is_relative_to((root / "exports").resolve())

        first = workspace.operation_files("backup export")
        second = workspace.operation_files("backup export")
        assert first.directory != second.directory
        assert first.directory.parent == second.directory.parent == root / "tmp"


def test_settings_ignore_legacy_device_and_model_keys(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "platform_tools_path": "tools",
                "last_device_id": "OLD",
                "last_model_key": "old-model",
                "scrcpy_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    settings = SettingsStore(path).load()

    assert settings.platform_tools_path == "tools"
    assert not hasattr(settings, "last_device_id")
    assert not hasattr(settings, "last_model_key")
    assert not hasattr(settings, "scrcpy_enabled")

    SettingsStore(path).save(settings)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {
        "platform_tools_path": "tools",
        "test_mode_enabled": False,
    }


def test_settings_persist_offline_test_mode(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"

    SettingsStore(path).save(Settings(test_mode_enabled=True))

    assert SettingsStore(path).load().test_mode_enabled is True


def test_slot_manager() -> None:
    slot = SlotManager.from_suffix("_a")
    assert slot.inactive_suffix == "_b"
    assert SlotManager.partition_name("lk", "_b", use_slot=True) == "lk_b"


def test_devices_yaml_import_and_profile_sanity() -> None:
    profiles = DeviceDetector._load_profiles(_root() / "devices.yaml")
    assert "oppofindx9" in profiles
    assert "vivopd2417" in profiles
    assert profiles["oppofindx9"].backup_partitions == ["lk", "init_boot"]


def test_profile_identity_boundary() -> None:
    profiles = DeviceDetector._load_profiles(_root() / "devices.yaml")
    profile = profiles["vivopd2417"]
    matching = ConnectedDevice(
        serial="TEST",
        brand="vivo",
        model="PD2417",
        device="pd2417",
        firmware="PD2417_A_16.0.8.1",
        slot_suffix="_a",
    )
    SafetyChecker.check_device_profile(matching, profile)

    mismatched = ConnectedDevice(
        serial="TEST",
        brand="oppo",
        model="PD2417",
        device="pd2417",
        firmware="PD2417_A_16.0.8.1",
        slot_suffix="_a",
    )
    raised = False
    try:
        SafetyChecker.check_device_profile(mismatched, profile)
    except Exception:
        raised = True
    assert raised
    SafetyChecker.check_device_profile(mismatched, profile, skip_identity=True)


class _PropertyAdb:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.serial = ""

    def getprop(self, name: str) -> str:
        return self.values.get(name, "")


def test_vivo_market_name_is_captured_without_changing_profile_match() -> None:
    adb = _PropertyAdb(
        {
            "ro.product.brand": "vivo",
            "ro.product.model": "PD2417",
            "ro.product.device": "pd2417",
            "ro.build.display.id": "PD2417_A_16.0.8.1",
            "ro.boot.slot_suffix": "_a",
            "ro.vivo.market.name": "iQOO Z10 Turbo+",
        }
    )
    detector = DeviceDetector(_root(), adb)

    device = detector.read_connected("SERIAL")

    assert device.market_name == "iQOO Z10 Turbo+"
    assert detector.match_profile(device).key == "vivopd2417"


def test_patched_lk_size_for_dd_rejects_zero_unknown_and_oversize_images() -> None:
    SafetyChecker.check_patched_lk_size_for_dd(8, 8)
    SafetyChecker.check_patched_lk_size_for_dd(7, 8)

    with pytest.raises(VerificationError, match="patched lk 大小为 0"):
        SafetyChecker.check_patched_lk_size_for_dd(0, 8)
    with pytest.raises(VerificationError, match="目标 LK 分区容量未知"):
        SafetyChecker.check_patched_lk_size_for_dd(8, 0)
    with pytest.raises(VerificationError, match="目标分区容量"):
        SafetyChecker.check_patched_lk_size_for_dd(9, 8)


def test_verify_local_file_returns_size_and_rejects_empty_files(tmp_path: Path) -> None:
    valid = tmp_path / "valid.img"
    valid.write_bytes(b"payload")
    empty = tmp_path / "empty.img"
    empty.write_bytes(b"")

    assert SafetyChecker.verify_local_file(valid, step="local check") == len(b"payload")
    with pytest.raises(VerificationError):
        SafetyChecker.verify_local_file(empty, step="local check")


class _FakeAdb:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.calls: list[tuple[str, bool, int, bool]] = []

    def shell(self, command: str, *, root: bool, timeout: int, check: bool):
        self.calls.append((command, root, timeout, check))
        return SimpleNamespace(stdout=self.stdout)


def test_preflight_block_exists_checks_named_block_as_root() -> None:
    adb = _FakeAdb("OK\n")

    SafetyChecker(adb).preflight_block_exists("lk_a")

    assert adb.calls == [
        ("test -e /dev/block/by-name/lk_a && echo OK || echo MISSING", True, 15, False)
    ]


def test_preflight_block_exists_rejects_missing_block() -> None:
    adb = _FakeAdb("MISSING\n")

    with pytest.raises(VerificationError, match="分区未命中: lk_a"):
        SafetyChecker(adb).preflight_block_exists("lk_a")


def test_preflight_remote_files_checks_each_remote_file_as_root() -> None:
    adb = _FakeAdb("OK\n")

    SafetyChecker(adb).preflight_remote_files("/data/local/tmp/lk.img", "/data/local/tmp/misc.img")

    assert adb.calls == [
        ("test -f '/data/local/tmp/lk.img' && echo OK || echo MISSING", True, 15, False),
        ("test -f '/data/local/tmp/misc.img' && echo OK || echo MISSING", True, 15, False),
    ]


def test_preflight_remote_files_rejects_missing_remote_file() -> None:
    adb = _FakeAdb("MISSING\n")

    with pytest.raises(VerificationError, match="tmp 文件未命中: /data/local/tmp/lk.img"):
        SafetyChecker(adb).preflight_remote_files("/data/local/tmp/lk.img")


def test_session_phase_safety_apis_are_removed(tmp_path: Path) -> None:
    callbacks = FlowCallbacks()
    context = FlowContext(_root(), tmp_path / "workspace", Settings(), callbacks)

    assert not hasattr(callbacks, "skip_phase_prerequisites")
    assert not hasattr(context, "require_completed_phase")
    assert not hasattr(context, "check_session_profile")
    assert not hasattr(context, "refresh_session_slots")

    for name in (
        "check_session_serial",
        "check_session_profile",
        "check_phase_done",
        "verify_patched_lk_artifact",
    ):
        assert not hasattr(SafetyChecker, name)


def test_flow_context_owns_flow_operation_files(tmp_path: Path) -> None:
    context = FlowContext(_root(), tmp_path / "workspace", Settings())

    assert context.operation_files.directory.parent == tmp_path / "workspace" / "tmp"
    assert context.operation_files.directory.name.startswith("flow-")
    assert context.operation_files.directory.is_dir()


def test_cleanup_materialized_preloads_always_attempts_operation_cleanup(tmp_path: Path) -> None:
    context = FlowContext(_root(), tmp_path / "workspace", Settings())
    calls: list[str] = []
    context.loader = SimpleNamespace(cleanup_materialized_preloads=lambda: calls.append("loader"))
    context.operation_files = SimpleNamespace(cleanup=lambda: calls.append("operation"))

    context.cleanup_materialized_preloads()

    assert calls == ["loader", "operation"]


def test_cleanup_materialized_preloads_chains_operation_failure_after_loader_failure(tmp_path: Path) -> None:
    context = FlowContext(_root(), tmp_path / "workspace", Settings())

    def fail_loader() -> None:
        raise RuntimeError("loader failed")

    def fail_operation() -> None:
        raise RuntimeError("operation failed")

    context.loader = SimpleNamespace(cleanup_materialized_preloads=fail_loader)
    context.operation_files = SimpleNamespace(cleanup=fail_operation)

    with pytest.raises(RuntimeError, match="loader failed") as exc_info:
        context.cleanup_materialized_preloads()

    assert isinstance(exc_info.value.__context__, RuntimeError)
    assert str(exc_info.value.__context__) == "operation failed"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "operation failed"


def test_cleanup_materialized_preloads_does_not_catch_loader_keyboard_interrupt(tmp_path: Path) -> None:
    context = FlowContext(_root(), tmp_path / "workspace", Settings())
    calls: list[str] = []

    def interrupt_loader() -> None:
        calls.append("loader")
        raise KeyboardInterrupt("stop cleanup")

    context.loader = SimpleNamespace(cleanup_materialized_preloads=interrupt_loader)
    context.operation_files = SimpleNamespace(cleanup=lambda: calls.append("operation"))

    with pytest.raises(KeyboardInterrupt, match="stop cleanup"):
        context.cleanup_materialized_preloads()

    assert calls == ["loader"]


def test_resource_loader_misc_resolves() -> None:
    loader = ResourceLoader(_root())
    path = loader.resolve_misc("oppo", "_a")
    assert path.name == "a_to_b_bootloader.img"
    assert path.is_file()


if __name__ == "__main__":
    test_workspace_boundary_sanity()
    test_slot_manager()
    test_devices_yaml_import_and_profile_sanity()
    test_profile_identity_boundary()
    test_resource_loader_misc_resolves()
    print("sanity checks passed")
