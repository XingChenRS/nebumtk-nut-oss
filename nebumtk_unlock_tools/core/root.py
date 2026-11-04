"""Root escalation via a protected in-memory preload wrapper."""

from __future__ import annotations

import re
import secrets
import shlex
import time
from typing import Callable

from .adb import AdbClient
from .errors import AdbError, AdbOfflineError, AdbTimeoutError, FlowCancelledError, RootEscalationError


class _ProtectedWrapperError(RootEscalationError):
    def __init__(self, stage: str, error_number: int) -> None:
        super().__init__("受保护 preload wrapper 未启动")
        self.stage = stage
        self.error_number = error_number


class _ProtectedGrantError(RootEscalationError):
    pass


class RootEscalator:
    REMOTE_WRAPPER = "/data/local/tmp/nebumtk_wrapper.so"
    PRELOAD_COMMAND_TIMEOUT = 180.0
    MEMFD_PRELOAD_PLACEHOLDER = "__NEBUMTK_MEMFD__"
    MEMFD_EXEC_PLACEHOLDER = "__NEBUMTK_PAYLOAD__"
    TRANSIENT_PAYLOAD_FILES = (
        "/data/local/tmp/su_daemon.log",
        "/data/local/tmp/su_daemon_watch.log",
        "/data/local/tmp/ashmem_file_ptr",
    )
    ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    WRAPPER_ERROR_PATTERN = re.compile(
        r"^NBERR:([a-z][a-z0-9-]{0,47}):([0-9]{1,10})$"
    )
    NON_RETRYABLE_WRAPPER_STAGES = frozenset(
        {"header", "fd-path", "exec-placeholder", "preload-placeholder", "preload-arg"}
    )
    SINGLE_RETRY_WRAPPER_STAGES = frozenset(
        {
            "materialize",
            "exec",
            "exec-anon-open",
            "exec-anon-copy",
            "exec-anon-mode",
            "exec-anon-reopen",
            "exec-anon",
            "preload-anon-inherit",
            "preload-env",
            "trigger",
        }
    )
    FULL_RETRY_WRAPPER_STAGES = frozenset({"grant"})

    def __init__(
        self,
        adb: AdbClient,
        *,
        max_retries: int = 4,
        reboot_wait: float = 8.0,
        post_boot_settle: float = 30.0,
        online_timeout: float = 180.0,
        remote_wrapper: str | None = None,
        grant_responder: Callable[[str, str], str] | None = None,
        execution_mode: str = "preload",
        trigger_command: str = "/system/bin/true",
        payload_args: tuple[str, ...] | list[str] | None = None,
        trigger_env: dict[str, str] | None = None,
        clean_environment: bool = False,
        log: Callable[[str], None] | None = None,
        stage: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.adb = adb
        self.max_retries = max_retries
        self.reboot_wait = reboot_wait
        self.post_boot_settle = post_boot_settle
        self.online_timeout = online_timeout
        self.remote_wrapper = (remote_wrapper or self.REMOTE_WRAPPER).strip() or self.REMOTE_WRAPPER
        self.grant_responder = grant_responder
        self.execution_mode = str(execution_mode).strip()
        if self.execution_mode not in {"preload", "exec"}:
            raise ValueError("invalid protected payload execution mode")
        self.trigger_args: list[str] = []
        self.payload_args: list[str] = []
        if self.execution_mode == "preload":
            try:
                self.trigger_args = shlex.split(
                    trigger_command.strip() or "/system/bin/true",
                    posix=True,
                )
            except ValueError as exc:
                raise ValueError("invalid preload trigger command") from exc
            if (
                not self.trigger_args
                or not self.trigger_args[0].startswith("/")
                or len(self.trigger_args) > 64
            ):
                raise ValueError("preload trigger must be an absolute command")
            if payload_args:
                raise ValueError("preload mode does not accept payload arguments")
        else:
            for raw_arg in list(payload_args or []):
                arg = str(raw_arg)
                if len(arg) > 1024 or any(char in arg for char in ("\0", "\r", "\n")):
                    raise ValueError("invalid protected payload argument")
                self.payload_args.append(arg)
            if len(self.payload_args) > 32:
                raise ValueError("too many protected payload arguments")
        self.trigger_env: dict[str, str] = {}
        for raw_key, raw_value in dict(trigger_env or {}).items():
            key = str(raw_key).strip()
            value = str(raw_value)
            if (
                not self.ENV_NAME_PATTERN.fullmatch(key)
                or key == "LD_PRELOAD"
                or len(value) > 4096
            ):
                raise ValueError("invalid preload environment")
            self.trigger_env[key] = value
        if len(self.trigger_env) > 128:
            raise ValueError("too many preload environment entries")
        self.clean_environment = bool(clean_environment)
        self.log = log or (lambda _message: None)
        self.stage = stage or (lambda _message: None)
        self.cancelled = cancelled or (lambda: False)

    def _check_cancel(self) -> None:
        if self.cancelled():
            raise FlowCancelledError("用户已取消")

    def escalate(self, before_attempt: Callable[[], None] | None = None) -> None:
        for attempt in range(1, self.max_retries + 1):
            self._check_cancel()
            self.log(f"提权尝试 {attempt}/{self.max_retries}")
            try:
                self._ensure_online(timeout=min(60.0, self.online_timeout))
            except AdbError as exc:
                if attempt >= self.max_retries:
                    raise RootEscalationError(f"设备未上线: {exc}") from exc
                self.log(f"设备未在线，等待中… ({exc})")
                try:
                    self._wait_for_reconnect(reason="等待设备上线", settle=True)
                except AdbError:
                    pass
                continue

            if attempt > 1 and self._safe_verify_root():
                self.stage("root 已获取")
                self.log("已获得 root")
                return

            if before_attempt is not None:
                try:
                    before_attempt()
                except FlowCancelledError:
                    raise
                except (AdbOfflineError, AdbTimeoutError) as exc:
                    self._cleanup_remote_wrapper()
                    self.log(f"准备 preload 失败: {_short(exc)}")
                    if attempt >= self.max_retries:
                        raise RootEscalationError(f"准备 preload 失败: {exc}") from exc
                    try:
                        self._wait_for_reconnect(reason="preload 传输中断后等待", settle=True)
                    except AdbError as wait_exc:
                        self.log(f"等待失败: {_short(wait_exc)}")
                    continue
                except AdbError as exc:
                    self._cleanup_remote_wrapper()
                    self.log(f"准备 preload 失败: {_short(exc)}")
                    if attempt >= self.max_retries:
                        raise RootEscalationError(f"准备 preload 失败: {exc}") from exc
                    self._cancellable_sleep(1.0)
                    continue
                except Exception as exc:
                    self._cleanup_remote_wrapper()
                    raise RootEscalationError(
                        f"本地 preload 材料准备失败，已停止: {exc}"
                    ) from exc

            disconnected = False
            ambiguous = False
            try:
                self._run_preload()
            except FlowCancelledError:
                raise
            except _ProtectedGrantError:
                self._cleanup_remote_wrapper()
                self.log("preload 本轮授权失败，准备新挑战后重试")
                if attempt >= self.max_retries:
                    raise
                self._cancellable_sleep(1.0)
                continue
            except _ProtectedWrapperError as exc:
                self._cleanup_remote_wrapper()
                if (
                    exc.stage
                    not in (
                        self.NON_RETRYABLE_WRAPPER_STAGES
                        | self.SINGLE_RETRY_WRAPPER_STAGES
                        | self.FULL_RETRY_WRAPPER_STAGES
                    )
                    or exc.stage in self.NON_RETRYABLE_WRAPPER_STAGES
                    or (
                        exc.stage in self.SINGLE_RETRY_WRAPPER_STAGES
                        and attempt >= 2
                    )
                    or attempt >= self.max_retries
                ):
                    raise
                self.log("受保护 preload 本轮未启动，准备新进程与新授权后重试")
                self._cancellable_sleep(1.0)
                continue
            except AdbOfflineError as exc:
                disconnected = True
                ambiguous = True
                self.log(f"preload 执行时设备掉线；等待重连并检测 root: {_short(exc)}")
            except AdbTimeoutError as exc:
                ambiguous = True
                self.log(
                    "preload 在执行窗口内未结束；设备可能仍在执行或正在重启，"
                    f"等待重连并检测 root: {_short(exc)}"
                )
            except (AdbError, TimeoutError, OSError) as exc:
                ambiguous = True
                self.log(f"preload 传输异常；等待设备状态稳定后检测 root: {_short(exc)}")

            if disconnected or ambiguous or not self._is_online():
                self.stage("等待设备重新连接…")
                self.log("等待设备重启上线后检测 root…")
                try:
                    self._wait_for_reconnect(reason="exploit 后等待重连", settle=True)
                except AdbError as exc:
                    self.log(f"重连失败: {_short(exc)}")
                    if attempt >= self.max_retries:
                        raise RootEscalationError(f"提权后设备未上线: {exc}") from exc
                    continue
            else:
                self._cancellable_sleep(3.0)

            self.stage("验证 root 权限…")
            root_ready = self._safe_verify_root()
            self._cleanup_remote_wrapper()
            if root_ready:
                self.stage("root 已获取")
                self.log("已获得 root")
                return

            if attempt >= self.max_retries:
                break

            if disconnected or ambiguous:
                self.log("上线后仍无 root，准备下一轮提权…")
                continue

            self.log("提权未成功，重启设备后重试…")
            try:
                self.adb.reboot()
            except Exception as exc:
                self.log(f"reboot 命令失败（设备可能已在重启）: {_short(exc)}")
            try:
                self._wait_for_reconnect(reason="reboot 后等待重连", settle=True)
            except AdbError as exc:
                self.log(f"重启后重连失败: {_short(exc)}")

        raise RootEscalationError(f"提权失败，已重试 {self.max_retries} 次")

    def _run_preload(self) -> None:
        if self.grant_responder is None:
            raise RootEscalationError("受保护 preload 一次性授权不可用")
        effective_env = dict(self.trigger_env)
        log_directory = ""
        if "PRELOAD_LOG_DIR" in effective_env:
            log_directory = (
                "/data/local/tmp/.nebumtk-log-" + secrets.token_hex(8)
            )
            try:
                self.adb.shell(
                    f"umask 077; mkdir {shlex.quote(log_directory)}",
                    root=False,
                    timeout=20,
                    check=True,
                    sensitive=True,
                )
            except Exception:
                self._cleanup_remote_wrapper()
                self._cleanup_payload_artifacts(log_directory)
                raise
            effective_env["PRELOAD_LOG_DIR"] = log_directory
        if self.execution_mode == "exec":
            payload_command = " ".join(
                shlex.quote(value)
                for value in (self.MEMFD_EXEC_PLACEHOLDER, *self.payload_args)
            )
            wrapper_command = (
                f"{shlex.quote(self.remote_wrapper)} -s - -m exec -- "
                f"{payload_command}"
            )
            if self.clean_environment:
                environment = " ".join(
                    shlex.quote(f"{key}={value}")
                    for key, value in effective_env.items()
                )
                wrapper_command = (
                    "/system/bin/toybox env -i "
                    + (f"{environment} " if environment else "")
                    + wrapper_command
                )
            else:
                environment = " ".join(
                    f"{key}={shlex.quote(value)}"
                    for key, value in effective_env.items()
                )
                if environment:
                    wrapper_command = f"{environment} {wrapper_command}"
        else:
            trigger_args = list(self.trigger_args)
            if self.clean_environment:
                trigger_args = [
                    "/system/bin/toybox",
                    "env",
                    "-i",
                    *(
                        f"{key}={value}"
                        for key, value in effective_env.items()
                    ),
                    f"LD_PRELOAD={self.MEMFD_PRELOAD_PLACEHOLDER}",
                    *trigger_args,
                ]
                env_prefix = ""
            else:
                env_prefix = " ".join(
                    f"{key}={shlex.quote(value)}"
                    for key, value in effective_env.items()
                )
            trigger_command = " ".join(
                shlex.quote(value) for value in trigger_args
            )
            wrapper_command = (
                f"{shlex.quote(self.remote_wrapper)} -s - -m preload -- "
                f"{trigger_command}"
            )
            if env_prefix:
                wrapper_command = f"{env_prefix} {wrapper_command}"
        command = (
            f"chmod 700 {shlex.quote(self.remote_wrapper)} && "
            f"{wrapper_command}"
        )
        self.stage("静默提权中…")
        self.log(
            f"启动受保护 preload（最长 {int(self.PRELOAD_COMMAND_TIMEOUT)}s，"
            "日志将持续收集；设备重启时会转入重连检测）"
        )
        try:
            target = self.adb.serial or ""

            def respond(challenge: str) -> str:
                try:
                    assert self.grant_responder is not None
                    return self.grant_responder(challenge, target)
                except FlowCancelledError:
                    raise
                except Exception as exc:
                    raise _ProtectedGrantError("preload 一次性授权失败") from exc

            result = self.adb.shell_challenge_exchange(
                command,
                respond,
                timeout=self.PRELOAD_COMMAND_TIMEOUT,
                cancelled=self.cancelled,
            )
            wrapper_errors: list[tuple[str, int]] = []
            for line in (result.output or "").splitlines():
                match = self.WRAPPER_ERROR_PATTERN.fullmatch(line.strip())
                if match is not None:
                    wrapper_errors.append((match.group(1), int(match.group(2))))
            if result.returncode == 126 and len(wrapper_errors) == 1:
                stage_name, error_number = wrapper_errors[0]
                raise _ProtectedWrapperError(stage_name, error_number)
            if result.returncode != 0:
                self.log(f"preload 返回非零状态: exit={result.returncode}")
            else:
                self.log("preload 命令已返回，开始检测 root")
        finally:
            # The wrapper is encrypted, but it has no reason to remain on the
            # device after the process has returned or failed.
            self._cleanup_remote_wrapper()
            self._cleanup_payload_artifacts(log_directory)

    def _cleanup_remote_wrapper(self) -> None:
        remote = self.remote_wrapper.strip()
        if remote:
            self.adb.remove_remote(remote, root=False)

    def _cleanup_payload_artifacts(self, log_directory: str = "") -> None:
        commands = [
            "rm -f "
            + " ".join(shlex.quote(path) for path in self.TRANSIENT_PAYLOAD_FILES)
        ]
        if re.fullmatch(r"/data/local/tmp/\.nebumtk-log-[0-9a-f]{16}", log_directory):
            commands.append(f"rm -rf {shlex.quote(log_directory)}")
        try:
            self.adb.shell(
                "; ".join(commands),
                root=False,
                timeout=20,
                check=False,
                sensitive=True,
            )
        except AdbError:
            pass

    def _safe_verify_root(self) -> bool:
        try:
            return self.verify_root()
        except (AdbError, TimeoutError, OSError) as exc:
            self.log(f"root 检测失败（设备可能未就绪）: {_short(exc)}")
            return False

    def verify_root(self) -> bool:
        result = self.adb.shell("id", root=True, timeout=15, check=False)
        if result.returncode != 0:
            return False
        return "uid=0" in (result.stdout or "").strip()

    def _is_online(self) -> bool:
        try:
            devices = self.adb.devices()
        except Exception:
            return False
        if self.adb.serial:
            return self.adb.serial in devices
        return bool(devices)

    def _ensure_online(self, *, timeout: float) -> None:
        self.adb.wait_for_device(timeout=timeout)
        deadline = time.time() + min(30.0, timeout)
        while time.time() < deadline:
            self._check_cancel()
            if self._is_online():
                return
            self._cancellable_sleep(2.0)
        raise AdbError("设备未进入 adb device 状态")

    def _wait_for_reconnect(self, *, reason: str, settle: bool = True) -> None:
        self.log(f"{reason}（最长 {int(self.online_timeout)}s）…")
        self._cancellable_sleep(min(self.reboot_wait, 8.0))
        try:
            self.adb.wait_for_device(timeout=self.online_timeout)
        except FlowCancelledError:
            raise
        except Exception as exc:
            raise AdbError(f"{reason} 超时: {_short(exc)}") from exc

        deadline = time.time() + 60.0
        while time.time() < deadline:
            self._check_cancel()
            try:
                if self._is_online():
                    self.log("设备已重新上线")
                    if settle and self.post_boot_settle > 0:
                        self.log(
                            f"等待 {int(self.post_boot_settle)}s 让内核稳定后再检测/重试…"
                        )
                        self._cancellable_sleep(self.post_boot_settle)
                    return
            except Exception:
                pass
            self._cancellable_sleep(3.0)
        raise AdbError(f"{reason}：设备未回到 adb device 状态")

    def _cancellable_sleep(self, seconds: float) -> None:
        remaining = max(0.0, seconds)
        step = 0.5
        while remaining > 0:
            self._check_cancel()
            slice_ = min(step, remaining)
            time.sleep(slice_)
            remaining -= slice_


def _short(exc: BaseException, limit: int = 200) -> str:
    text = str(exc).replace("\n", " ").strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text
