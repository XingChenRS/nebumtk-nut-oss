"""Step verification helpers."""

from __future__ import annotations

from .errors import VerificationError


class StepVerifier:

    @staticmethod
    def check_non_empty(value: str, *, step: str) -> None:
        if not value.strip():
            raise VerificationError(f"{step} 返回空值")

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
