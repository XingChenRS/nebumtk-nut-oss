# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""adb command wrapper."""

from __future__ import annotations

import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .errors import AdbError, AdbOfflineError, AdbTimeoutError, FlowCancelledError
from .runner import CommandResult, CommandTimeoutError, run_command, subprocess_kwargs


_OFFLINE_TOKENS = (
    "no devices/emulators found",
    "device offline",
    "device not found",
    "device unauthorized",
    "closed",
    "connection reset",
    "cannot connect to daemon",
    "protocol fault",
    "error: no devices",
)


def _classify_error(text: str, command: list[str]) -> AdbError:
    lowered = (text or "").lower()
    if any(token in lowered for token in _OFFLINE_TOKENS):
        return AdbOfflineError(text.strip() or f"adb 掉线: {' '.join(command)}")
    return AdbError(text.strip() or f"adb 失败: {' '.join(command)}")


class AdbClient:
    def __init__(
        self,
        binary: str = "adb",
        serial: str | None = None,
        log: Callable[[str], None] | None = None,
        su_path: str = "su",
    ) -> None:
        self.binary = binary
        self.serial = serial
        self.log = log or (lambda _message: None)
        self.su_path = su_path or "su"

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
        sensitive: bool = False,
        input_text: str | None = None,
        cancelled: Callable[[], bool] | None = None,
        cwd: Path | str | None = None,
    ) -> CommandResult:
        command = self._base_args() + list(args)
        if sensitive:
            quiet = True
        if not quiet:
            self.log("ADB 操作")
        try:
            result = run_command(
                command,
                timeout=timeout,
                check=False,
                sensitive=sensitive,
                input_text=input_text,
                cancelled=cancelled,
                cwd=cwd,
            )
        except FlowCancelledError:
            raise
        except CommandTimeoutError as exc:
            detail = "敏感命令" if sensitive else " ".join(command)
            if sensitive:
                raise AdbTimeoutError(f"adb 命令超时: {detail}") from None
            raise AdbTimeoutError(f"adb 命令超时: {detail}") from exc
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            detail = "敏感命令" if sensitive else " ".join(command)
            if sensitive:
                raise AdbError(f"adb 命令超时: {detail}") from None
            raise AdbError(f"adb 命令超时: {detail}") from exc
        if not quiet and result.returncode != 0:
            self.log("ADB 操作失败")
        if check and result.returncode != 0:
            if sensitive:
                lowered = result.output.lower()
                if any(token in lowered for token in _OFFLINE_TOKENS):
                    raise AdbOfflineError("adb 敏感命令执行时设备掉线")
                raise AdbError("adb 敏感命令失败")
            msg = result.output.strip() or f"adb 失败: {' '.join(command)}"
            if len(msg) > 800:
                msg = msg[:797] + "..."
            raise _classify_error(msg, command)
        return result

    def get_state(self) -> str:
        result = self.run("get-state", timeout=10, quiet=True, check=False)
        return result.stdout.strip()

    def wait_for_device(self, timeout: float = 120.0) -> None:
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            try:
                online = self.devices()
                if self.serial:
                    if self.serial in online:
                        return
                elif online:
                    return
                remaining = max(1.0, deadline - time.time())
                self.run(
                    "wait-for-device",
                    timeout=min(15.0, remaining),
                    check=False,
                    quiet=True,
                )
            except Exception as exc:
                last_error = str(exc)
            time.sleep(2)
        detail = f" ({last_error})" if last_error else ""
        target = self.serial or "任意设备"
        raise AdbOfflineError(f"等待 adb 设备超时: {target}{detail}")

    def devices(self) -> list[str]:
        result = self.run("devices", timeout=10, quiet=True, check=False)
        serials: list[str] = []
        for line in result.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials

    def reboot(self, target: str = "") -> None:
        args = ["reboot"] + ([target] if target else [])
        self.run(*args, timeout=30, check=False)

    def reboot_fastboot(self) -> None:
        self.reboot("fastboot")

    def push(self, local: Path, remote: str) -> None:
        if not local.is_file():
            raise AdbError(f"本地文件不存在: {local}")
        self.run("push", str(local), remote, timeout=300)

    def pull(
        self,
        remote: str,
        local: Path,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = self._ascii_staging_dir()
        staged = staging_dir / "payload.bin"
        pending = local.with_name(f".{local.name}.{uuid.uuid4().hex}.pending")
        try:
            self.run(
                "pull",
                remote,
                staged.name,
                timeout=600,
                cwd=staging_dir,
                cancelled=cancelled,
            )
            if not staged.is_file() or staged.stat().st_size <= 0:
                raise AdbError(f"adb pull 未生成有效暂存文件: {remote}")
            with staged.open("rb") as source, pending.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
            if pending.stat().st_size != staged.stat().st_size:
                raise AdbError(f"adb pull 暂存文件大小不一致: {remote}")
            pending.replace(local)
        finally:
            pending.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)
            shutil.rmtree(staging_dir, ignore_errors=True)

    @staticmethod
    def _ascii_staging_dir() -> Path:
        roots = (Path(tempfile.gettempdir()), Path("C:/Windows/Temp"))
        for root in roots:
            try:
                root.mkdir(parents=True, exist_ok=True)
                candidate = root / f"NEBUMTK_ADB_{uuid.uuid4().hex}"
                if not str(candidate).isascii():
                    continue
                candidate.mkdir()
                return candidate
            except OSError:
                continue
        raise AdbError("无法创建 ASCII ADB 暂存目录，拒绝使用 Unicode 本地目标路径")

    def shell(
        self,
        command: str,
        *,
        root: bool = False,
        su_path: str | None = None,
        timeout: float | None = 120.0,
        check: bool = True,
        sensitive: bool = False,
        input_text: str | None = None,
        no_pty: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> CommandResult:
        if root:
            su = (su_path if su_path is not None else self.su_path).strip() or "su"
            command = f"{su} -c {self._quote(command)}"
        shell_args = ["shell"]
        if no_pty:
            shell_args.append("-T")
        shell_args.append(command)
        return self.run(
            *shell_args,
            timeout=timeout,
            check=check,
            sensitive=sensitive,
            input_text=input_text,
            cancelled=cancelled,
        )

    def shell_challenge_exchange(
        self,
        command: str,
        responder: Callable[[str], str],
        *,
        timeout: float = 180.0,
        cancelled: Callable[[], bool] | None = None,
    ) -> CommandResult:
        args = self._base_args() + ["shell", "-T", command]
        run_kwargs = subprocess_kwargs()
        run_kwargs.update(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        try:
            process = subprocess.Popen(args, **run_kwargs)
        except FileNotFoundError as exc:
            raise AdbError("adb 不存在") from exc

        events: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def pump(name: str, stream) -> None:
            try:
                for line in iter(lambda: stream.readline(8192), ""):
                    events.put((name, line))
            finally:
                events.put((name, None))

        assert process.stdout is not None
        assert process.stderr is not None
        assert process.stdin is not None
        threading.Thread(
            target=pump,
            args=("stdout", process.stdout),
            daemon=True,
        ).start()
        threading.Thread(
            target=pump,
            args=("stderr", process.stderr),
            daemon=True,
        ).start()

        deadline = time.monotonic() + timeout
        output: dict[str, list[str]] = {"stdout": [], "stderr": []}
        output_sizes: dict[str, int] = {"stdout": 0, "stderr": 0}
        ended: set[str] = set()
        responded = False

        def stop() -> None:
            if process.poll() is not None:
                return
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        try:
            while process.poll() is None or len(ended) < 2:
                if cancelled is not None and cancelled():
                    stop()
                    raise FlowCancelledError("用户已取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop()
                    raise AdbTimeoutError("adb 敏感命令超时")
                try:
                    stream_name, line = events.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    ended.add(stream_name)
                    continue
                normalized = line.strip()
                if (
                    stream_name == "stdout"
                    and not responded
                    and normalized.startswith("NBCH1:")
                ):
                    try:
                        response = responder(normalized)
                        if (
                            not response.isascii()
                            or len(response) > 640
                            or "\r" in response
                            or "\n" in response
                        ):
                            raise ValueError("invalid device grant wire format")
                        process.stdin.write(response + "\n")
                        process.stdin.flush()
                        process.stdin.close()
                        responded = True
                    except Exception:
                        stop()
                        raise
                    continue
                remaining_output = 256 * 1024 - output_sizes[stream_name]
                if remaining_output > 0:
                    kept = line[:remaining_output]
                    output[stream_name].append(kept)
                    output_sizes[stream_name] += len(kept)
            returncode = process.wait()
        finally:
            stop()
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except Exception:
                    pass

        result = CommandResult(
            returncode=returncode,
            stdout="".join(output["stdout"]),
            stderr="".join(output["stderr"]),
        )
        if not responded:
            raise _classify_error(
                result.output or "device challenge missing",
                args,
            )
        return result

    def getprop(self, name: str) -> str:
        result = self.shell(f"getprop {name}", timeout=15, check=False)
        return result.stdout.strip()

    def kernel_release(self) -> str:
        """Return one bounded, non-root kernel release string for KMI selection."""

        result = self.shell("uname -r", timeout=15, sensitive=True)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 1 or len(lines[0]) > 512:
            raise AdbError("设备内核版本输出无效")
        return lines[0]

    def remote_file_size(self, remote: str, *, root: bool = True) -> int:
        # 用两种方式，兼容 toybox/busybox
        for cmd in (
            f"stat -c %s {self._quote(remote)}",
            f"wc -c < {self._quote(remote)}",
        ):
            try:
                result = self.shell(cmd, root=root, timeout=20, check=False)
            except AdbError:
                continue
            for token in result.stdout.strip().splitlines():
                token = token.strip()
                if token.isdigit():
                    return int(token)
        return -1

    def remote_sha256(self, remote: str, *, root: bool = True) -> str:
        quoted = self._quote(remote)
        for command in (
            f"sha256sum {quoted}",
            f"toybox sha256sum {quoted}",
        ):
            try:
                result = self.shell(
                    command,
                    root=root,
                    timeout=60,
                    check=False,
                    sensitive=True,
                )
            except AdbError:
                continue
            for line in (result.stdout or "").splitlines():
                token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
                if re.fullmatch(r"[0-9A-Fa-f]{64}", token):
                    return token.lower()
        return ""

    def remove_remote(
        self,
        remote: str,
        *,
        root: bool = True,
        strict: bool = False,
    ) -> None:
        try:
            result = self.shell(f"rm -f {self._quote(remote)}", root=root, timeout=20, check=False)
        except AdbError:
            if strict:
                raise
            return
        if strict and result.returncode != 0:
            raise AdbError(f"删除远端临时文件失败: {remote}; {result.output}")

    @staticmethod
    def _quote(value: str) -> str:
        escaped = value.replace("'", "'\\''")
        return f"'{escaped}'"
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
