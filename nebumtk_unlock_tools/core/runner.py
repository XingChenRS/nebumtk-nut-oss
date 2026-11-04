# SPDX-License-Identifier: AGPL-3.0-only
"""Command execution helpers."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .errors import FlowCancelledError


class CommandTimeoutError(TimeoutError):
    """A local command exceeded its bounded execution window."""


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr).strip()


def subprocess_kwargs() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    kwargs: dict[str, Any] = {"creationflags": subprocess.CREATE_NO_WINDOW}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    kwargs["startupinfo"] = startupinfo
    return kwargs


def run_command(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    check: bool = False,
    sensitive: bool = False,
    input_text: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    cwd: Path | str | None = None,
) -> CommandResult:
    if cancelled is not None:
        return _run_cancellable(
            args,
            timeout=timeout,
            check=check,
            sensitive=sensitive,
            input_text=input_text,
            cancelled=cancelled,
            cwd=cwd,
        )

    run_kwargs = subprocess_kwargs()
    if cwd is not None:
        run_kwargs["cwd"] = str(cwd)
    if input_text is None:
        run_kwargs["stdin"] = subprocess.DEVNULL
    else:
        run_kwargs["input"] = input_text
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **run_kwargs,
        )
    except FileNotFoundError as exc:
        _raise_not_found(args, sensitive, exc)
    except subprocess.TimeoutExpired as exc:
        _raise_timeout(args, sensitive, exc)
    return _result_from_completed(completed, args, check, sensitive)


def _run_cancellable(
    args: Sequence[str],
    *,
    timeout: float | None,
    check: bool,
    sensitive: bool,
    input_text: str | None,
    cancelled: Callable[[], bool],
    cwd: Path | str | None,
) -> CommandResult:
    run_kwargs = subprocess_kwargs()
    if cwd is not None:
        run_kwargs["cwd"] = str(cwd)
    run_kwargs.update(
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        process = subprocess.Popen(list(args), **run_kwargs)
    except FileNotFoundError as exc:
        _raise_not_found(args, sensitive, exc)

    deadline = None if timeout is None else time.monotonic() + timeout
    pending_input = input_text
    try:
        while True:
            if cancelled():
                _stop_process(process)
                raise FlowCancelledError("用户已取消")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                _stop_process(process)
                _raise_timeout(args, sensitive, None)
            try:
                stdout, stderr = process.communicate(input=pending_input, timeout=min(0.5, remaining) if remaining is not None else 0.5)
                completed = subprocess.CompletedProcess(list(args), process.returncode, stdout, stderr)
                return _result_from_completed(completed, args, check, sensitive)
            except subprocess.TimeoutExpired:
                pending_input = None
    finally:
        if process.poll() is None:
            _stop_process(process)


def _stop_process(process: subprocess.Popen) -> None:
    try:
        process.terminate()
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _result_from_completed(
    completed: subprocess.CompletedProcess,
    args: Sequence[str],
    check: bool,
    sensitive: bool,
) -> CommandResult:
    result = CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if check and result.returncode != 0:
        if sensitive:
            raise RuntimeError("敏感命令失败")
        raise RuntimeError(result.output or f"Command failed: {' '.join(args)}")
    return result


def _raise_not_found(args: Sequence[str], sensitive: bool, exc: FileNotFoundError) -> None:
    command = "敏感命令" if sensitive else (args[0] if args else "")
    if sensitive:
        raise FileNotFoundError(f"命令不存在: {command}") from None
    raise FileNotFoundError(f"命令不存在: {command} ({exc})") from exc


def _raise_timeout(args: Sequence[str], sensitive: bool, exc: BaseException | None) -> None:
    command = "敏感命令" if sensitive else " ".join(args)
    if sensitive:
        raise CommandTimeoutError(f"命令超时: {command}") from None
    if exc is None:
        raise CommandTimeoutError(f"命令超时: {command}")
    raise CommandTimeoutError(f"命令超时: {command}") from exc
