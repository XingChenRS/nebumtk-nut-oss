# SPDX-License-Identifier: AGPL-3.0-only
"""fastboot command wrapper."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from .errors import FastbootError, VerificationError
from .runner import CommandResult, run_command
from .verifier import StepVerifier


class FastbootClient:
    def __init__(
        self,
        binary: str = "fastboot",
        serial: str | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.binary = binary
        self.serial = serial
        self.log = log or (lambda _message: None)

    def _base_args(self) -> list[str]:
        args = [self.binary]
        if self.serial:
            args.extend(["-s", self.serial])
        return args

    def run(
        self,
        *args: str,
        timeout: float | None = None,
        check: bool = True,
        quiet: bool = False,
    ) -> CommandResult:
        command = self._base_args() + list(args)
        if not quiet:
            self.log("Fastboot 操作")
        try:
            result = run_command(command, timeout=timeout, check=False)
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise FastbootError(f"fastboot 命令超时: {' '.join(command)}") from exc
        if not quiet and result.returncode != 0:
            self.log("Fastboot 操作失败")
        if check and result.returncode != 0:
            msg = result.output.strip() or f"fastboot 失败: {' '.join(command)}"
            if len(msg) > 800:
                msg = msg[:797] + "..."
            raise FastbootError(msg)
        return result

    def devices(self) -> list[str]:
        """Parse `fastboot devices` cross-platform.

        Line format: "<serial>\tfastboot" (Linux) or "<serial>       fastboot" (Windows).
        Empty lines, comments and "(bootloader)" transient lines are skipped.
        """
        # Bare listing must not carry -s, or fastboot may not enumerate other devices.
        cmd = [self.binary, "devices"]
        try:
            result = run_command(cmd, timeout=10, check=False)
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise FastbootError(f"fastboot devices 超时: {' '.join(cmd)}") from exc

        serials: list[str] = []
        for line in (result.stdout or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("("):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            state = parts[-1].lower()
            if state != "fastboot":
                continue
            serials.append(parts[0])
        return serials

    def wait_for_device(self, timeout: float = 120.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            devices = self.devices()
            if self.serial:
                if self.serial in devices:
                    return
            elif devices:
                return
            time.sleep(2)
        target = self.serial or "任意设备"
        raise VerificationError(f"等待 fastboot 超时: {target}")

    def getvar(self, name: str, *, check: bool = False) -> str:
        result = self.run("getvar", name, timeout=30, check=check)
        for line in result.output.splitlines():
            normalized = line.strip()
            if normalized.lower().startswith("(bootloader)"):
                normalized = normalized[len("(bootloader)"):].strip()
            prefix = f"{name}:"
            if normalized.lower().startswith(prefix.lower()):
                return normalized[len(prefix):].strip()
        return result.output.strip()

    def fastboot_mode(self) -> str:
        """Classify the connected Fastboot transport from `is-userspace`."""
        value = self.getvar("is-userspace").strip().lower()
        if value == "yes":
            return "fastbootd"
        if value == "no":
            return "bootloader"
        return "unknown"

    def current_slot(self) -> str:
        return self.getvar("current-slot").strip().lower()

    def is_unlocked(self) -> bool:
        value = self.getvar("unlocked")
        if not value:
            return False
        try:
            StepVerifier.check_unlocked(value)
            return True
        except VerificationError:
            return False

    def partition_size(self, partition: str) -> int:
        label = str(partition).strip()
        if re.fullmatch(r"[A-Za-z0-9._-]+", label) is None:
            raise VerificationError(f"非法 fastboot partition: {partition!r}")
        raw = self.getvar(f"partition-size:{label}").strip()
        if re.fullmatch(r"0[xX][0-9A-Fa-f]+", raw):
            value = int(raw, 16)
        elif re.fullmatch(r"[0-9]+", raw):
            # Fastboot partition-size is commonly hexadecimal, but vendor
            # bootloaders sometimes omit the 0x prefix. A digits-only value is
            # therefore ambiguous (for example 1000000 can mean 1,000,000 or
            # 0x1000000). Select only the unique interpretation aligned to an
            # Android logical block boundary; fail closed if both are
            # plausible instead of inflating a real decimal capacity.
            decimal_value = int(raw, 10)
            hexadecimal_value = int(raw, 16)
            aligned = {
                candidate
                for candidate in (decimal_value, hexadecimal_value)
                if candidate > 0 and candidate % 4096 == 0
            }
            if len(aligned) != 1:
                raise VerificationError(
                    f"partition-size:{label} 进制含义不明确，已拒绝: {raw!r}"
                )
            value = aligned.pop()
        elif re.fullmatch(r"[0-9A-Fa-f]+", raw):
            # A prefixless value containing A-F is unambiguously hexadecimal.
            value = int(raw, 16)
        else:
            raise VerificationError(f"partition-size:{label} 返回值非法: {raw!r}")
        if value <= 0:
            raise VerificationError(f"partition-size:{label} 必须为正数: {raw!r}")
        return value

    def flashing_unlock_with_retry(self, *, max_retries: int = 3) -> None:
        for attempt in range(1, max_retries + 1):
            self.log(f"flashing unlock 尝试 {attempt}/{max_retries}")
            self.run("flashing", "unlock", timeout=180, check=False)
            time.sleep(2)
            if self.is_unlocked():
                self.log("bootloader 已解锁")
                return
            try:
                self.wait_for_device(timeout=60)
            except VerificationError:
                # 设备可能因用户确认按键在重启，再等一轮
                self.wait_for_device(timeout=120)
        raise VerificationError("flashing unlock 失败，已重试多次")

    def flash_partition(
        self,
        partition: str,
        image: Path,
        *,
        timeout: int = 600,
    ) -> None:
        """Flash one validated partition image without implicit slot selection."""

        label = str(partition).strip()
        if re.fullmatch(r"[A-Za-z0-9._-]+", label) is None:
            raise FastbootError(f"非法 fastboot partition: {partition!r}")
        source = Path(image)
        if not source.is_file():
            raise FastbootError(f"分区镜像不存在: {source}")
        self.run("flash", label, str(source), timeout=timeout)

    def flash_lk(self, slot_suffix: str, image: Path) -> None:
        self.flash_partition(f"lk{slot_suffix}", image)

    def flash_dual_lk(
        self,
        image: Path,
        *,
        before_each: Callable[[str], None] | None = None,
    ) -> list[str]:
        flashed: list[str] = []
        for suffix in ("_a", "_b"):
            try:
                if before_each is not None:
                    before_each(suffix)
                self.flash_lk(suffix, image)
                flashed.append(suffix)
            except Exception as exc:
                done = ", ".join(flashed) or "无"
                raise FastbootError(
                    f"双槽 flash 失败于 lk{suffix}，已成功槽: {done}。禁止 reboot，请手动排查后再收尾。详情: {exc}"
                ) from exc
        return flashed

    def set_active(self, slot_letter: str) -> None:
        letter = slot_letter.strip().lstrip("_")
        if letter not in {"a", "b"}:
            raise FastbootError(f"无效槽位: {slot_letter!r}")
        self.run("set_active", letter, timeout=30)

    def reboot(self) -> None:
        self.run("reboot", timeout=30, check=False)

    def reboot_checked(self) -> None:
        self.run("reboot", timeout=30, check=True)

    def reboot_fastboot(self) -> None:
        self.run("reboot", "fastboot", timeout=30, check=False)

    def reboot_bootloader(self) -> None:
        self.run("reboot", "bootloader", timeout=30, check=False)
