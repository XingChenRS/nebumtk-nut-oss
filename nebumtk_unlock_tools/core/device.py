# SPDX-License-Identifier: AGPL-3.0-only
"""Device detection and model matching."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .adb import AdbClient
from .errors import DeviceMatchError


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ABSOLUTE_DEVICE_PATH = re.compile(
    r"^/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+$"
)


@dataclass(frozen=True)
class PrivilegeRuntime:
    preload_ref: str
    remote_wrapper: str
    execution_mode: str
    trigger_argv: tuple[str, ...]
    payload_argv: tuple[str, ...]
    environment_mode: str
    environment: tuple[tuple[str, str], ...]
    su_strategy: str
    su_path: str

    @classmethod
    def from_resources(cls, brand: str, resources: dict[str, Any]) -> "PrivilegeRuntime":
        preload_ref = str(resources.get("preload", "")).strip()
        if preload_ref and not preload_ref.startswith("builtin:"):
            raise DeviceMatchError("preload 必须引用内置受保护资源")
        remote_wrapper = str(resources.get("preload_remote", "")).strip()
        remote_wrapper = remote_wrapper or "/data/local/tmp/nebumtk_wrapper.so"
        if not _ABSOLUTE_DEVICE_PATH.fullmatch(remote_wrapper):
            raise DeviceMatchError("preload 远端路径必须为绝对路径")

        execution_mode = str(resources.get("preload_mode", "preload")).strip()
        if execution_mode not in {"preload", "exec"}:
            raise DeviceMatchError("preload 执行模式无效")
        trigger_argv: tuple[str, ...] = ()
        payload_argv: tuple[str, ...] = ()
        if execution_mode == "preload":
            trigger = str(resources.get("preload_trigger", "")).strip()
            try:
                trigger_argv = tuple(
                    shlex.split(trigger or "/system/bin/true", posix=True)
                )
            except ValueError as exc:
                raise DeviceMatchError("preload 启动参数无效") from exc
            if not trigger_argv or not trigger_argv[0].startswith("/"):
                raise DeviceMatchError("preload 启动程序必须为绝对路径")
            if "preload_args" in resources:
                raise DeviceMatchError("LD_PRELOAD 模式不得配置独立 payload 参数")
        else:
            raw_payload_argv = resources.get("preload_args", [])
            if not isinstance(raw_payload_argv, list) or len(raw_payload_argv) > 32:
                raise DeviceMatchError("独立 payload 参数无效")
            normalized_args: list[str] = []
            for raw_arg in raw_payload_argv:
                if not isinstance(raw_arg, str):
                    raise DeviceMatchError("独立 payload 参数无效")
                arg = str(raw_arg)
                if len(arg) > 1024 or any(char in arg for char in ("\0", "\r", "\n")):
                    raise DeviceMatchError("独立 payload 参数无效")
                normalized_args.append(arg)
            payload_argv = tuple(normalized_args)

        raw_entries = resources.get("preload_env_entries")
        if raw_entries is not None:
            if (
                not isinstance(raw_entries, list)
                or any(
                    not isinstance(item, list) or len(item) != 2
                    for item in raw_entries
                )
            ):
                raise DeviceMatchError("preload 环境配置无效")
            environment_items = raw_entries
        else:
            raw_environment = resources.get("preload_env", {})
            if not isinstance(raw_environment, dict):
                raise DeviceMatchError("preload 环境配置无效")
            environment_items = list(raw_environment.items())
        environment: list[tuple[str, str]] = []
        for raw_name, raw_value in environment_items:
            name = str(raw_name).strip()
            if not _ENV_NAME.fullmatch(name) or name == "LD_PRELOAD":
                raise DeviceMatchError("preload 环境变量名无效")
            value = str(raw_value)
            if any(char in value for char in ("\0", "\r", "\n")):
                raise DeviceMatchError("preload 环境变量值无效")
            environment.append((name, value))

        su_path = str(resources.get("su_path", "")).strip()
        if not su_path:
            su_path = (
                "/apex/com.android.virt/bin/su"
                if brand.lower() == "vivo"
                else "su"
            )
        if su_path != "su" and not _ABSOLUTE_DEVICE_PATH.fullmatch(su_path):
            raise DeviceMatchError("su 路径无效")
        su_strategy = str(resources.get("su_strategy", "shell_c")).strip()
        if su_strategy != "shell_c":
            raise DeviceMatchError(f"未支持的 su 策略: {su_strategy}")

        return cls(
            preload_ref=preload_ref,
            remote_wrapper=remote_wrapper,
            execution_mode=execution_mode,
            trigger_argv=trigger_argv,
            payload_argv=payload_argv,
            environment_mode=(
                "clean" if resources.get("preload_clean_env") is True else "inherit"
            ),
            environment=tuple(environment),
            su_strategy=su_strategy,
            su_path=su_path,
        )

    @property
    def trigger_command(self) -> str:
        return shlex.join(self.trigger_argv)

    @property
    def environment_dict(self) -> dict[str, str]:
        return dict(self.environment)


@dataclass
class DeviceProfile:
    key: str
    brand: str
    display_name: str
    match: dict[str, Any]
    resources: dict[str, Any]
    partitions: dict[str, Any]
    post_unlock: dict[str, bool]
    capabilities: frozenset[str] = field(default_factory=frozenset)
    unlock: dict[str, str] = field(default_factory=dict)
    protection_disable_cmds: list[str] = field(default_factory=list)
    privilege_runtime_override: PrivilegeRuntime | None = field(default=None, repr=False)

    @property
    def privilege_runtime(self) -> PrivilegeRuntime:
        return self.privilege_runtime_override or PrivilegeRuntime.from_resources(
            self.brand,
            self.resources,
        )

    def with_privilege_runtime(self, runtime: PrivilegeRuntime) -> "DeviceProfile":
        return replace(self, privilege_runtime_override=runtime)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def backup_partitions(self) -> list[str]:
        return list(self.partitions.get("backup", ["lk"]))

    @property
    def slot_suffix(self) -> bool:
        return bool(self.partitions.get("slot_suffix", True))

    @property
    def partition_aliases(self) -> dict[str, str]:
        return dict(self.partitions.get("names", {}))

    @property
    def su_path(self) -> str:
        return self.privilege_runtime.su_path

    @property
    def preload_remote(self) -> str:
        return self.privilege_runtime.remote_wrapper

    @property
    def preload_trigger(self) -> str:
        return self.privilege_runtime.trigger_command

    @property
    def preload_mode(self) -> str:
        return self.privilege_runtime.execution_mode

    @property
    def preload_args(self) -> tuple[str, ...]:
        return self.privilege_runtime.payload_argv

    @property
    def preload_env(self) -> dict[str, str]:
        return self.privilege_runtime.environment_dict

    @property
    def preload_clean_env(self) -> bool:
        return self.privilege_runtime.environment_mode == "clean"

    @property
    def xingc_patch_profile(self) -> str:
        return str(self.resources.get("xingc_patch_profile", self.key)).strip() or self.key


@dataclass
class ConnectedDevice:
    serial: str
    brand: str
    model: str
    device: str
    firmware: str
    slot_suffix: str
    android_version: str = ""
    platform: str = ""
    market_name: str = ""
    device_fingerprint: str = ""
    manufacturer: str = ""
    build_incremental: str = ""
    security_patch: str = ""
    bootloader_state: str = "unknown"
    profile: DeviceProfile | None = None


class DeviceDetector:
    PROP_BRAND = "ro.product.brand"
    PROP_MODEL = "ro.product.model"
    PROP_DEVICE = "ro.product.device"
    PROP_FIRMWARE = "ro.build.display.id"
    PROP_ANDROID_VERSION = "ro.build.version.release"
    PROP_BUILD_INCREMENTAL = "ro.build.version.incremental"
    PROP_SECURITY_PATCH = "ro.build.version.security_patch"
    PROP_PLATFORM = "ro.board.platform"
    PROP_MANUFACTURER = "ro.product.manufacturer"
    PROP_FINGERPRINT = "ro.build.fingerprint"
    PROP_SLOT = "ro.boot.slot_suffix"
    PROP_MARKET_NAME = "ro.vivo.market.name"
    PROP_FLASH_LOCKED = "ro.boot.flash.locked"
    PROP_VBMETA_STATE = "ro.boot.vbmeta.device_state"

    def __init__(self, project_root: Path, adb: AdbClient) -> None:
        self.project_root = project_root
        self.adb = adb
        from ..resources.catalog import load_device_catalog

        self.profiles = self._profiles_from_mapping(
            load_device_catalog().get("devices", {})
        )

    @staticmethod
    def _load_profiles(path: Path) -> dict[str, DeviceProfile]:
        if not path.is_file():
            raise DeviceMatchError(f"devices.yaml 不存在: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return DeviceDetector._profiles_from_mapping(raw.get("devices", {}))

    @staticmethod
    def _profiles_from_mapping(raw_devices: Any) -> dict[str, DeviceProfile]:
        if not isinstance(raw_devices, dict) or not raw_devices:
            raise DeviceMatchError("机型目录没有有效设备记录")
        profiles: dict[str, DeviceProfile] = {}
        exact_alias_owners: dict[tuple[str, str], str] = {}
        for raw_key, data in raw_devices.items():
            if not isinstance(data, dict):
                raise DeviceMatchError("机型目录设备记录无效")
            key = str(raw_key).strip()
            if not key or key in profiles:
                raise DeviceMatchError("机型目录设备 ID 无效")
            raw_capabilities = data.get("capabilities", [])
            if (
                not isinstance(raw_capabilities, list)
                or any(not isinstance(item, str) or not item for item in raw_capabilities)
                or len(set(raw_capabilities)) != len(raw_capabilities)
            ):
                raise DeviceMatchError(f"invalid device capabilities: {key}")
            profile = DeviceProfile(
                key=key,
                brand=str(data.get("brand", "")),
                display_name=str(data.get("display_name", key)),
                match=dict(data.get("match", {})),
                resources=dict(data.get("resources", {})),
                partitions=dict(data.get("partitions", {})),
                post_unlock=dict(data.get("post_unlock", {})),
                capabilities=frozenset(raw_capabilities),
                unlock=dict(data.get("unlock", {})),
                protection_disable_cmds=list(data.get("protection_disable_cmds", [])),
            )
            raw_aliases = profile.match.get("model") or []
            if not isinstance(raw_aliases, list):
                raise DeviceMatchError(f"机型目录 model aliases 必须为列表: {key}")
            match_brand = str(profile.match.get("brand") or profile.brand).strip().casefold()
            for raw_alias in raw_aliases:
                alias = str(raw_alias).strip().casefold()
                if not alias:
                    raise DeviceMatchError(f"机型目录包含空 model alias: {key}")
                identity = (match_brand, alias)
                owner = exact_alias_owners.get(identity)
                if owner is not None:
                    raise DeviceMatchError(
                        f"机型目录存在重复精确 alias: {match_brand}/{alias} ({owner}, {key})"
                    )
                exact_alias_owners[identity] = key
            # Parse and validate every executable/runtime field at catalog load,
            # not only when the profile happens to be selected.
            profile.privilege_runtime
            profiles[key] = profile
        return profiles

    def read_connected(self, serial: str) -> ConnectedDevice:
        self.adb.serial = serial
        flash_locked = self._optional_getprop(self.PROP_FLASH_LOCKED)
        vbmeta_state = self._optional_getprop(self.PROP_VBMETA_STATE)
        return ConnectedDevice(
            serial=serial,
            brand=self.adb.getprop(self.PROP_BRAND),
            model=self.adb.getprop(self.PROP_MODEL),
            device=self.adb.getprop(self.PROP_DEVICE),
            firmware=self.adb.getprop(self.PROP_FIRMWARE),
            android_version=self.adb.getprop(self.PROP_ANDROID_VERSION),
            build_incremental=self._optional_getprop(self.PROP_BUILD_INCREMENTAL),
            security_patch=self._optional_getprop(self.PROP_SECURITY_PATCH),
            platform=self.adb.getprop(self.PROP_PLATFORM),
            manufacturer=self._optional_getprop(self.PROP_MANUFACTURER),
            device_fingerprint=self.adb.getprop(self.PROP_FINGERPRINT),
            slot_suffix=self.adb.getprop(self.PROP_SLOT),
            market_name=self.adb.getprop(self.PROP_MARKET_NAME),
            bootloader_state=self.classify_bootloader_state(
                flash_locked,
                vbmeta_state,
            ),
        )

    def _optional_getprop(self, name: str) -> str:
        try:
            return self.adb.getprop(name)
        except Exception:
            return ""

    @staticmethod
    def classify_bootloader_state(flash_locked: str, vbmeta_state: str) -> str:
        locked_value = str(flash_locked).strip().casefold()
        if locked_value == "1":
            return "locked"
        if locked_value == "0":
            return "unlocked"
        vbmeta_value = str(vbmeta_state).strip().casefold()
        if vbmeta_value in {"locked", "unlocked"}:
            return vbmeta_value
        return "unknown"

    def match_profile(self, device: ConnectedDevice) -> DeviceProfile:
        matches = [
            profile for profile in self.profiles.values() if self._matches(device, profile)
        ]
        if len(matches) == 1:
            device.profile = matches[0]
            return matches[0]
        if len(matches) > 1:
            keys = ", ".join(sorted(profile.key for profile in matches))
            raise DeviceMatchError(f"设备同时匹配多个机型配置: {keys}")
        raise DeviceMatchError(
            f"未匹配到支持机型: brand={device.brand}, model={device.model}, firmware={device.firmware}"
        )

    def list_profiles(self) -> list[DeviceProfile]:
        return list(self.profiles.values())

    def get_profile_by_key(self, key: str) -> DeviceProfile:
        if key not in self.profiles:
            raise DeviceMatchError(f"未知机型: {key}")
        return self.profiles[key]

    def _matches(self, device: ConnectedDevice, profile: DeviceProfile) -> bool:
        brand = profile.match.get("brand")
        if brand and str(brand).lower() not in device.brand.lower():
            return False
        models = profile.match.get("model") or []
        if models:
            model_values = [str(item).lower() for item in models]
            if device.model.lower() not in model_values and device.device.lower() not in model_values:
                return False
        firmware = profile.match.get("firmware")
        if firmware and str(firmware) not in device.firmware:
            return False
        return True
