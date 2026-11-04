from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.device import ConnectedDevice, DeviceProfile
from nebumtk_unlock_tools.core.errors import FlowCancelledError
from nebumtk_unlock_tools.flows.base import FlowCallbacks
from nebumtk_unlock_tools.flows.vivo_dyn import (
    DynCaptureRequest,
    DynRestoreRequest,
    VivoDynFacade,
)
from nebumtk_unlock_tools.gui.vivo_dyn_worker import VivoDynWorker
from nebumtk_unlock_tools.core.operation_files import snapshot_regular_file


def _profile() -> DeviceProfile:
    return DeviceProfile(
        key="vivopd2324",
        brand="vivo",
        display_name="Vivo X90 Pro+",
        match={"brand": "vivo", "model": ["PD2324"]},
        resources={},
        partitions={},
        post_unlock={},
    )


def _device() -> ConnectedDevice:
    return ConnectedDevice(
        serial="SERIAL",
        brand="vivo",
        model="PD2324",
        device="PD2324",
        firmware="firmware",
        slot_suffix="_a",
    )


class _FacadeAdb:
    def __init__(self, _binary: str, *, serial: str, su_path: str, **_kwargs) -> None:
        self.serial = serial
        self.su_path = su_path

    def devices(self) -> list[str]:
        return [self.serial]


def test_dyn_facade_capture_prefers_profile_su_and_zero_protected_operations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(module.EnvChecker, "validate_adb", lambda _self: SimpleNamespace(adb="adb"))
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)
    monkeypatch.setattr(
        module,
        "probe_custom_su",
        lambda adb, executable: calls.append(("probe", executable)) or SimpleNamespace(uid=0),
    )
    monkeypatch.setattr(
        module,
        "capture_dyn_backup",
        lambda adb, destination, **_kwargs: calls.append(("capture", adb.su_path)) or object(),
    )
    monkeypatch.setattr(
        module,
        "publish_to_desktop",
        lambda records, **kwargs: calls.append(("publish", (records, kwargs))) or "EXPORTED",
    )
    facade = VivoDynFacade(tmp_path, tmp_path / "workspace", Settings())

    profile = _profile()
    profile.resources["su_path"] = "/vendor/bin/profile-su"
    result = facade.capture(DynCaptureRequest(_device(), profile))

    assert result == "EXPORTED"
    assert calls[0:2] == [
        ("probe", "/vendor/bin/profile-su"),
        ("capture", "/vendor/bin/profile-su"),
    ]
    publish_kwargs = calls[2][1][1]
    assert publish_kwargs["dyn_backup"] is not None
    assert publish_kwargs["serial"] == "SERIAL"


def test_dyn_facade_restore_loads_selected_manifest_before_existing_su_restore(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    selected = tmp_path / "selected-backup"
    selected.mkdir()
    (selected / "backup-manifest.json").write_text("{}", encoding="utf-8")
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(module.EnvChecker, "validate_adb", lambda _self: SimpleNamespace(adb="adb"))
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)
    monkeypatch.setattr(
        module,
        "probe_custom_su",
        lambda _adb, executable: calls.append(("probe", executable)) or SimpleNamespace(uid=0),
    )
    monkeypatch.setattr(
        module,
        "load_dyn_backup",
        lambda directory, **kwargs: calls.append(("load", (directory, kwargs))) or "BACKUP",
    )
    monkeypatch.setattr(
        module,
        "restore_dyn_backup",
        lambda _adb, backup, **kwargs: calls.append(("restore", (backup, kwargs))) or "RESTORED",
    )
    facade = VivoDynFacade(tmp_path, tmp_path / "workspace", Settings())
    manifest_snapshot = snapshot_regular_file(selected / "backup-manifest.json")

    result = facade.restore(
        DynRestoreRequest(
            _device(), _profile(),
            manifest_snapshot,
        )
    )

    assert result == "RESTORED"
    assert calls == [
        ("probe", "/apex/com.android.virt/bin/su"),
        (
            "load",
            (
                selected,
                    {
                        "profile_key": "vivopd2324",
                        "adb_serial": "SERIAL",
                        "expected_manifest": manifest_snapshot,
                    },
            ),
        ),
        ("restore", ("BACKUP", {"su_candidates": ("/apex/com.android.virt/bin/su",), "cancelled": facade.callbacks.cancelled})),
    ]


def test_typed_dyn_worker_dispatches_capture_without_device_kmi_or_online_context(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    request = DynCaptureRequest(_device(), _profile())
    facade = SimpleNamespace(
        capture=lambda actual, **_kwargs: ("capture", actual)
    )
    results: list[object] = []
    errors: list[str] = []
    logs: list[str] = []
    worker = VivoDynWorker(facade, request)
    worker.result_ready.connect(results.append)
    worker.finished_error.connect(errors.append)
    worker.log_message.connect(logs.append)

    worker.run()

    assert errors == []
    assert results == [("capture", request)]
    assert logs == ["Dyn 备份开始", "Dyn 备份完成"]
    app.processEvents()


def test_typed_dyn_worker_pre_cancel_has_no_facade_side_effects(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    request = DynCaptureRequest(_device(), _profile())
    calls: list[object] = []
    facade = SimpleNamespace(capture=lambda actual, **kwargs: calls.append((actual, kwargs)))
    cancellations: list[bool] = []
    worker = VivoDynWorker(facade, request)
    worker.cancelled_signal.connect(lambda: cancellations.append(True))

    worker.cancel()
    worker.run()

    assert calls == []
    assert cancellations == [True]
    app.processEvents()


def test_typed_dyn_worker_passes_owned_cancellation_to_facade(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    request = DynCaptureRequest(_device(), _profile())
    seen: list[bool] = []

    def capture(_request, *, cancelled):
        seen.append(cancelled())
        return "RESULT"

    worker = VivoDynWorker(SimpleNamespace(capture=capture), request)
    worker.run()

    assert seen == [False]
    app.processEvents()


def test_dyn_facade_cancellation_precedes_adb_or_probe_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    effects: list[str] = []
    monkeypatch.setattr(
        module.EnvChecker,
        "validate_adb",
        lambda _self: effects.append("env") or SimpleNamespace(adb="adb"),
    )
    facade = VivoDynFacade(
        tmp_path,
        tmp_path / "workspace",
        Settings(),
        FlowCallbacks(cancelled=lambda: True),
    )

    with pytest.raises(FlowCancelledError):
        facade.capture(DynCaptureRequest(_device(), _profile()))

    assert effects == []


def test_dyn_facade_rechecks_cancellation_after_environment_before_adb(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    cancelled = {"value": False}
    effects: list[str] = []

    def validate_adb(_self):
        effects.append("env")
        cancelled["value"] = True
        return SimpleNamespace(adb="adb")

    monkeypatch.setattr(module.EnvChecker, "validate_adb", validate_adb)
    monkeypatch.setattr(
        module,
        "AdbClient",
        lambda *_args, **_kwargs: effects.append("adb") or _FacadeAdb("adb", serial="SERIAL", su_path="su"),
    )
    facade = VivoDynFacade(tmp_path, tmp_path / "workspace", Settings())

    with pytest.raises(FlowCancelledError):
        facade.capture(
            DynCaptureRequest(_device(), _profile()),
            cancelled=lambda: cancelled["value"],
        )

    assert effects == ["env"]


def test_dyn_facade_adb_inherits_worker_cancellation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    cancelled = lambda: False
    captured: list[object] = []

    class FakeAdb(_FacadeAdb):
        def __init__(self, *args, **kwargs) -> None:
            captured.append(kwargs.get("cancelled"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        module.EnvChecker,
        "validate_adb",
        lambda _self: SimpleNamespace(adb="adb"),
    )
    monkeypatch.setattr(module, "AdbClient", FakeAdb)
    monkeypatch.setattr(
        module,
        "probe_custom_su",
        lambda *_args: SimpleNamespace(uid=0),
    )
    facade = VivoDynFacade(tmp_path, tmp_path / "workspace", Settings())

    adb, _provider = facade._prepare(_device(), _profile(), cancelled=cancelled)

    assert adb.serial == "SERIAL"
    assert captured == [cancelled]


def test_dyn_facade_cancelled_by_capture_helper_never_publishes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    cancelled = {"value": False}
    effects: list[str] = []
    monkeypatch.setattr(module.EnvChecker, "validate_adb", lambda _self: SimpleNamespace(adb="adb"))
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)
    monkeypatch.setattr(module, "probe_custom_su", lambda *_args: SimpleNamespace(uid=0))

    def capture(*_args, **_kwargs):
        effects.append("capture")
        cancelled["value"] = True
        return object()

    monkeypatch.setattr(module, "capture_dyn_backup", capture)
    monkeypatch.setattr(
        module,
        "publish_to_desktop",
        lambda *_args, **_kwargs: effects.append("publish") or "EXPORTED",
    )
    facade = VivoDynFacade(tmp_path, tmp_path / "workspace", Settings())

    with pytest.raises(FlowCancelledError):
        facade.capture(
            DynCaptureRequest(_device(), _profile()),
            cancelled=lambda: cancelled["value"],
        )

    assert effects == ["capture"]
    assert not list((tmp_path / "workspace" / "tmp").iterdir())


def test_dyn_facade_cancelled_by_manifest_load_never_starts_remote_restore(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    selected = tmp_path / "selected-backup"
    selected.mkdir()
    (selected / "backup-manifest.json").write_text("{}", encoding="utf-8")
    cancelled = {"value": False}
    effects: list[str] = []
    monkeypatch.setattr(module.EnvChecker, "validate_adb", lambda _self: SimpleNamespace(adb="adb"))
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)
    monkeypatch.setattr(module, "probe_custom_su", lambda *_args: SimpleNamespace(uid=0))

    def load(*_args, **_kwargs):
        effects.append("load")
        cancelled["value"] = True
        return "BACKUP"

    monkeypatch.setattr(module, "load_dyn_backup", load)
    monkeypatch.setattr(
        module,
        "restore_dyn_backup",
        lambda *_args, **_kwargs: effects.append("restore") or "RESTORED",
    )
    facade = VivoDynFacade(tmp_path, tmp_path / "workspace", Settings())

    with pytest.raises(FlowCancelledError):
        facade.restore(
            DynRestoreRequest(
                _device(), _profile(),
                snapshot_regular_file(selected / "backup-manifest.json"),
            ),
            cancelled=lambda: cancelled["value"],
        )

    assert effects == ["load"]


def test_typed_dyn_worker_cancellation_during_facade_suppresses_success(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    request = DynCaptureRequest(_device(), _profile())
    results: list[object] = []
    cancellations: list[bool] = []
    worker: VivoDynWorker

    def capture(_request, *, cancelled):
        assert cancelled() is False
        worker.cancel()
        return "SHOULD-NOT-EMIT"

    worker = VivoDynWorker(SimpleNamespace(capture=capture), request)
    worker.result_ready.connect(results.append)
    worker.cancelled_signal.connect(lambda: cancellations.append(True))

    worker.run()

    assert results == []
    assert cancellations == [True]
    app.processEvents()
def test_dyn_restore_request_requires_confirmed_manifest_snapshot(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.core.operation_files import snapshot_regular_file

    selected = tmp_path / "selected"
    selected.mkdir()
    manifest = selected / "backup-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(TypeError, match="snapshot"):
        DynRestoreRequest(_device(), _profile(), selected)

    assert DynRestoreRequest(
        _device(), _profile(), snapshot_regular_file(manifest)
    ).manifest.path == manifest


def test_dyn_facade_falls_back_from_profile_to_global_su_before_custom(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    profile = _profile()
    profile.resources["su_path"] = "/vendor/bin/profile-su"
    probes: list[str] = []

    monkeypatch.setattr(module.EnvChecker, "validate_adb", lambda _self: SimpleNamespace(adb="adb"))
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)

    def probe(_adb, executable):
        probes.append(executable)
        if executable == "/vendor/bin/profile-su":
            raise RuntimeError("profile provider unavailable")
        if executable == "su":
            return SimpleNamespace(uid=0)
        raise AssertionError("custom su must not be reached after global su succeeds")

    monkeypatch.setattr(module, "probe_custom_su", probe)
    monkeypatch.setattr(module, "capture_dyn_backup", lambda adb, *_args, **_kwargs: adb.su_path)
    monkeypatch.setattr(module, "publish_to_desktop", lambda _records, **kwargs: kwargs["dyn_backup"])

    facade = VivoDynFacade(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True, custom_su="/data/local/tmp/operator-su"),
    )

    assert facade.capture(DynCaptureRequest(_device(), profile)) == "su"
    assert probes == ["/vendor/bin/profile-su", "su"]


def test_dyn_test_mode_skips_only_profile_identity_check(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    identity_checks: list[bool] = []
    monkeypatch.setattr(
        module.EnvChecker,
        "validate_adb",
        lambda _self: SimpleNamespace(adb="adb"),
    )
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)
    monkeypatch.setattr(
        module.SafetyChecker,
        "check_device_profile",
        lambda _device, _profile, *, skip_identity=False: identity_checks.append(
            skip_identity
        ),
    )
    monkeypatch.setattr(
        module,
        "probe_custom_su",
        lambda _adb, _executable: SimpleNamespace(uid=0),
    )
    monkeypatch.setattr(
        module,
        "capture_dyn_backup",
        lambda _adb, *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        module,
        "publish_to_desktop",
        lambda _records, **_kwargs: "EXPORTED",
    )

    facade = VivoDynFacade(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True),
    )

    assert facade.capture(DynCaptureRequest(_device(), _profile())) == "EXPORTED"
    assert identity_checks == [True]


def test_dyn_facade_uses_custom_su_only_as_test_mode_last_resort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    profile = _profile()
    profile.resources["su_path"] = "/vendor/bin/profile-su"
    custom = "/data/local/tmp/operator-su"
    probes: list[str] = []
    monkeypatch.setattr(module.EnvChecker, "validate_adb", lambda _self: SimpleNamespace(adb="adb"))
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)

    def probe(_adb, executable):
        probes.append(executable)
        if executable == custom:
            return SimpleNamespace(uid=0)
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(module, "probe_custom_su", probe)
    monkeypatch.setattr(module, "capture_dyn_backup", lambda adb, *_args, **_kwargs: adb.su_path)
    monkeypatch.setattr(module, "publish_to_desktop", lambda _records, **kwargs: kwargs["dyn_backup"])
    facade = VivoDynFacade(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True, custom_su=custom),
    )

    assert facade.capture(DynCaptureRequest(_device(), profile)) == custom
    assert probes == ["/vendor/bin/profile-su", "su", custom]


def test_invalid_custom_su_does_not_block_working_global_su(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import vivo_dyn as module

    probes: list[str] = []
    monkeypatch.setattr(module.EnvChecker, "validate_adb", lambda _self: SimpleNamespace(adb="adb"))
    monkeypatch.setattr(module, "AdbClient", _FacadeAdb)
    monkeypatch.setattr(
        module,
        "probe_custom_su",
        lambda _adb, executable: probes.append(executable) or SimpleNamespace(uid=0),
    )
    monkeypatch.setattr(module, "capture_dyn_backup", lambda adb, *_args, **_kwargs: adb.su_path)
    monkeypatch.setattr(module, "publish_to_desktop", lambda _records, **kwargs: kwargs["dyn_backup"])
    facade = VivoDynFacade(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True, custom_su="su; rm -rf /"),
    )

    profile = _profile()
    profile.resources["su_path"] = "su"
    assert facade.capture(DynCaptureRequest(_device(), profile)) == "su"
    assert probes == ["su"]
