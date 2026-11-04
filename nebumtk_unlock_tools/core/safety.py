# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Safety checks and preflight validation."""

from __future__ import annotations

from pathlib import Path

from .adb import AdbClient
from .errors import VerificationError
from .verifier import StepVerifier


class SafetyChecker:
    BLOCK_DIR = "/dev/block/by-name"
    TMP_DIR = "/data/local/tmp"

    def __init__(self, adb: AdbClient, log=None) -> None:
        self.adb = adb
        self.log = log or (lambda _message: None)

    def preflight_block_exists(self, block_name: str) -> None:
        # 直接问单块，避免把整个 by-name 目录（几百行）打进日志
        cmd = f"test -e {self.BLOCK_DIR}/{block_name} && echo OK || echo MISSING"
        result = self.adb.shell(cmd, root=True, timeout=15, check=False)
        text = (result.stdout or "").strip()
        if "OK" in text:
            return
        raise VerificationError(f"分区未命中: {block_name}")

    def preflight_remote_files(self, *remote_paths: str) -> None:
        for remote in remote_paths:
            cmd = f"test -f {AdbClient._quote(remote)} && echo OK || echo MISSING"
            result = self.adb.shell(cmd, root=True, timeout=15, check=False)
            if "OK" not in (result.stdout or ""):
                raise VerificationError(f"tmp 文件未命中: {remote}")

    @staticmethod
    def check_patched_lk_size_for_dd(patched_size: int, target_capacity: int) -> None:
        if patched_size <= 0:
            raise VerificationError("patched lk 大小为 0")
        if target_capacity <= 0:
            raise VerificationError("目标 LK 分区容量未知")
        if patched_size > target_capacity:
            raise VerificationError(
                f"patched lk ({patched_size}) 大于目标分区容量 ({target_capacity})，禁止写入"
            )

    @staticmethod
    def check_device_profile(device, profile, *, skip_identity: bool = False) -> None:
        if skip_identity:
            return

        expected_brand = str(profile.match.get("brand") or profile.brand).strip().lower()
        actual_brand = (device.brand or "").strip().lower()
        if expected_brand and expected_brand not in actual_brand:
            raise VerificationError(
                f"设备品牌 {device.brand!r} 与所选机型品牌 {profile.brand!r} 不一致"
            )

        expected_models = [
            str(value).strip().lower()
            for value in (profile.match.get("model") or [])
            if str(value).strip()
        ]
        if expected_models:
            actual_model = (device.model or "").strip().lower()
            actual_device = (device.device or "").strip().lower()
            if actual_model not in expected_models and actual_device not in expected_models:
                raise VerificationError(
                    f"设备型号/代号 model={device.model!r}, device={device.device!r} "
                    f"与所选机型 {profile.display_name!r} 不一致"
                )

        expected_firmware = str(profile.match.get("firmware") or "").strip().lower()
        actual_firmware = (device.firmware or "").strip().lower()
        if expected_firmware and expected_firmware not in actual_firmware:
            raise VerificationError(
                f"设备固件 {device.firmware!r} 与所选机型要求 {expected_firmware!r} 不一致"
            )

    @staticmethod
    def verify_local_file(path: Path, *, step: str) -> int:
        StepVerifier.check_file_size(path, step=step)
        return path.stat().st_size
