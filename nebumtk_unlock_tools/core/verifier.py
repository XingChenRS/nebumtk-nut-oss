# SPDX-License-Identifier: AGPL-3.0-only
"""Step verification helpers."""

from __future__ import annotations

from .errors import VerificationError
from .runner import CommandResult


class StepVerifier:
    @staticmethod
    def check_command(result: CommandResult, *, step: str) -> None:
        if result.returncode != 0:
            raise VerificationError(f"{step} 失败: {result.output}")

    @staticmethod
    def check_non_empty(value: str, *, step: str) -> None:
        if not value.strip():
            raise VerificationError(f"{step} 返回空值")

    @staticmethod
    def check_equals(actual: str, expected: str, *, step: str) -> None:
        if actual.strip().lower() != expected.strip().lower():
            raise VerificationError(f"{step} 不符合预期: 期望 {expected!r}, 实际 {actual!r}")

    @staticmethod
    def check_contains(haystack: str, needle: str, *, step: str) -> None:
        if needle.lower() not in haystack.lower():
            raise VerificationError(f"{step} 未包含预期内容: {needle!r}")

    @staticmethod
    def check_file_size(path, min_size: int = 1, *, step: str) -> None:
        size = path.stat().st_size
        if size < min_size:
            raise VerificationError(f"{step} 文件大小异常: {size} bytes")

    @staticmethod
    def check_unlocked(value: str) -> None:
        normalized = value.strip().lower()
        if normalized not in {"yes", "true", "1", "unlocked"}:
            raise VerificationError(f"设备未解锁: unlocked={value!r}")
