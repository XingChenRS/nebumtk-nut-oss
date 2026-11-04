from __future__ import annotations

from pathlib import Path

import pytest

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.adb import AdbClient
from nebumtk_unlock_tools.core.device import DeviceDetector, DeviceProfile
from nebumtk_unlock_tools.core.errors import AdbError
from nebumtk_unlock_tools.core.runner import CommandResult
from nebumtk_unlock_tools.flows.base import FlowContext
from nebumtk_unlock_tools.flows import stages
from nebumtk_unlock_tools.gui import device_scan_worker as scan_worker_module

from tests._payload_material import compile_or_skip
from nebumtk_unlock_tools.gui.device_scan_worker import DeviceScanResult, DeviceScanWorker
from nebumtk_unlock_tools.gui.worker import UtilityWorker
from nebumtk_unlock_tools.lk import ubl_service
from nebumtk_unlock_tools.lk.ubl_service import InternalUblPatcher
from tools.model_catalog import compile_catalog


ROOT = Path(__file__).resolve().parents[1]


def _profile(*, patch_profile: str = "mt6985_or_lower") -> DeviceProfile:
    return DeviceProfile(
        key="iqooz8",
        brand="vivo",
        display_name="iQOO Z8",
        match={"brand": "vivo", "model": ["iqoo z8"]},
        resources={
            "preload": "builtin:iqooz8_mt6983_c1082bc392",
            "xingc_patch_profile": patch_profile,
        },
        partitions={"backup": ["lk"], "names": {"lk": "lk"}},
        post_unlock={},
        capabilities=frozenset({"preload"}),
    )


def test_kernel_release_uses_one_bounded_non_root_shell_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    adb = AdbClient("adb", serial="SERIAL")

    def shell(command: str, **kwargs: object) -> CommandResult:
        calls.append((command, kwargs))
        return CommandResult(0, "5.15.94-android13-9-g123\n", "")

    monkeypatch.setattr(adb, "shell", shell)

    assert adb.kernel_release() == "5.15.94-android13-9-g123"
    assert calls == [
        (
            "uname -r",
            {"root": False, "timeout": 15, "sensitive": True},
        )
    ]


@pytest.mark.parametrize("stdout", ["one\ntwo\n", "x" * 513])
def test_kernel_release_rejects_ambiguous_or_oversized_output(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    adb = AdbClient("adb", serial="SERIAL")
    monkeypatch.setattr(
        adb,
        "shell",
        lambda *_args, **_kwargs: CommandResult(0, stdout, ""),
    )

    with pytest.raises(AdbError, match="kernel|release|鍐呮牳|鐗堟湰"):
        adb.kernel_release()


def test_device_scan_freezes_kernel_release_with_its_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()

    class FakeAdb:
        def kernel_release(self) -> str:
            return "6.1.75-android14-11"

    class FakeDetector:
        def __init__(self, _root: Path, _adb: object) -> None:
            pass

        def read_connected(self, serial: str):
            return type(
                "Device",
                (),
                {
                    "serial": serial,
                    "brand": "vivo",
                    "model": "iQOO Z8",
                    "market_name": "iQOO Z8",
                    "device": "iqooz8",
                    "firmware": "PD2241_A",
                    "android_version": "14",
                    "platform": "mt6983",
                    "manufacturer": "vivo",
                    "build_incremental": "eng.release.20260812",
                    "security_patch": "2026-07-01",
                    "bootloader_state": "locked",
                    "device_fingerprint": "vivo/fingerprint",
                    "slot_suffix": "_a",
                },
            )()

        def match_profile(self, _device: object) -> DeviceProfile:
            return profile

    monkeypatch.setattr(scan_worker_module, "DeviceDetector", FakeDetector)
    worker = DeviceScanWorker(tmp_path, Settings(), generation=23)
    results: list[DeviceScanResult] = []
    worker.scan_completed.connect(results.append)

    worker._scan_adb(FakeAdb(), "SERIAL")

    assert len(results) == 1
    assert results[0].kernel_release == "6.1.75-android14-11"
    assert results[0].android_version == "14"
    assert results[0].platform == "mt6983"
    assert results[0].manufacturer == "vivo"
    assert results[0].build_incremental == "eng.release.20260812"
    assert results[0].security_patch == "2026-07-01"
    assert results[0].bootloader_state == "locked"
    assert results[0].scan_generation == 23


def test_device_detector_reads_android_version_and_platform() -> None:
    values = {
        DeviceDetector.PROP_BRAND: "vivo",
        DeviceDetector.PROP_MODEL: "V2405A",
        DeviceDetector.PROP_DEVICE: "v2405",
        DeviceDetector.PROP_FIRMWARE: "PD2502B_A_16.0.29",
        DeviceDetector.PROP_FINGERPRINT: "vivo/v2405/release",
        DeviceDetector.PROP_SLOT: "_b",
        DeviceDetector.PROP_MARKET_NAME: "vivo X300 Pro",
        "ro.build.version.release": "16",
        "ro.build.version.incremental": "PD2502B_16.0.29_release",
        "ro.build.version.security_patch": "2026-07-01",
        "ro.product.manufacturer": "vivo",
        "ro.board.platform": "mt6993",
        "ro.boot.flash.locked": "0",
        "ro.boot.vbmeta.device_state": "unlocked",
    }
    adb = type(
        "FakeAdb",
        (),
        {"serial": "", "getprop": lambda self, name: values[name]},
    )()
    detector = object.__new__(DeviceDetector)
    detector.adb = adb

    device = detector.read_connected("SERIAL")

    assert device.android_version == "16"
    assert device.platform == "mt6993"
    assert device.manufacturer == "vivo"
    assert device.build_incremental == "PD2502B_16.0.29_release"
    assert device.security_patch == "2026-07-01"
    assert device.bootloader_state == "unlocked"


@pytest.mark.parametrize(
    ("flash_locked", "vbmeta_state", "expected"),
    [
        ("1", "", "locked"),
        ("0", "", "unlocked"),
        ("", "locked", "locked"),
        ("", "unlocked", "unlocked"),
        ("orange", "green", "unknown"),
    ],
)
def test_bootloader_state_only_accepts_exact_known_device_values(
    flash_locked: str,
    vbmeta_state: str,
    expected: str,
) -> None:
    assert DeviceDetector.classify_bootloader_state(flash_locked, vbmeta_state) == expected


def test_compiled_capabilities_and_patch_profile_survive_runtime_loading() -> None:
    compiled = compile_or_skip(ROOT / "model_catalog.yaml")
    profiles = DeviceDetector._profiles_from_mapping(compiled["devices"])

    assert profiles["iqooz8"].capabilities == frozenset({"preload"})
    assert profiles["iqoopad"].capabilities == frozenset({"preload"})
    assert profiles["iqooz8"].xingc_patch_profile == "mt6985_or_lower"
    assert profiles["iqoopad"].xingc_patch_profile == "mt6985_or_lower"
    assert profiles["honormagic8proair"].capabilities == frozenset(
        {"preload", "generic_lk"}
    )
    assert not profiles["honormagic8proair"].has_capability("xingc")
    assert not profiles["honormagic8proair"].has_capability("flashing_unlock")


def test_lk_service_forwards_profile_through_inspect_build_verify_and_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / "original.img"
    output = tmp_path / "patched.img"
    original.write_bytes(b"original")
    output.write_bytes(b"patched")
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        InternalUblPatcher,
        "availability_check",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        InternalUblPatcher,
        "_protected_resources",
        staticmethod(lambda: (b"key", object())),
    )

    def preflight(_path, _key, _catalog, *, image_profile=None):
        calls.append(("inspect", image_profile))
        return True, "ok"

    def run_patch(_source, destination, _key, _catalog, *, image_profile=None):
        calls.append(("build", image_profile))
        Path(destination).write_bytes(b"patched")
        return 8, 7, ("signed",), ["built"]

    def verify(_source, _output, _key, _catalog, *, image_profile=None):
        calls.append(("verify", image_profile))
        return True, ["verified"]

    def validate(_path, _key, _catalog, *, image_profile=None):
        calls.append(("validate", image_profile))
        return True, ["validated"]

    monkeypatch.setattr(ubl_service, "preflight_check", preflight)
    monkeypatch.setattr(ubl_service, "run_patch", run_patch)
    monkeypatch.setattr(ubl_service, "verify_files", verify)
    monkeypatch.setattr(ubl_service, "validate_patched_image", validate)
    patcher = InternalUblPatcher(image_profile="mt6985_or_lower")

    assert patcher.inspect(original) == (True, "ok")
    patcher.patch_and_verify(original, output)
    patcher.validate_patched(output)

    assert calls == [
        ("inspect", "mt6985_or_lower"),
        ("build", "mt6985_or_lower"),
        ("verify", "mt6985_or_lower"),
        ("validate", "mt6985_or_lower"),
    ]


def test_local_lk_build_and_gui_inspect_use_selected_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "original.img"
    source.write_bytes(b"original")
    profile = _profile()
    constructor_profiles: list[str | None] = []

    class FakePatcher:
        def __init__(self, *, image_profile=None, **_kwargs) -> None:
            constructor_profiles.append(image_profile)

        def patch_and_verify(self, original: Path, output: Path) -> None:
            output.write_bytes(original.read_bytes() + b"-patched")

    monkeypatch.setattr(stages, "InternalUblPatcher", FakePatcher)
    ctx = FlowContext(ROOT, tmp_path / "workspace", Settings())
    stages._patch_lk_to_operation_file(ctx, source, profile)

    utility_results: list[object] = []

    class InspectPatcher:
        def __init__(self, *, image_profile=None, **_kwargs) -> None:
            constructor_profiles.append(image_profile)

        def inspect(self, _source: Path) -> tuple[bool, str]:
            return True, "ok"

    monkeypatch.setattr(ubl_service, "InternalUblPatcher", InspectPatcher)
    worker = UtilityWorker(
        "inspect_lk",
        {"source": str(source), "image_profile": profile.xingc_patch_profile},
    )
    worker.result_ready.connect(utility_results.append)
    worker.run()

    assert constructor_profiles == ["mt6985_or_lower", "mt6985_or_lower"]
    assert utility_results == [("inspect_lk", (True, "ok"))]
