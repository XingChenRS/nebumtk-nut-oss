"""Bounded numeric parsers for untrusted device command output."""

from __future__ import annotations

from .errors import VerificationError


UINT64_MAX = (1 << 64) - 1
MAX_UINT64_DECIMAL_DIGITS = 20


def parse_bounded_uint64_decimal(raw: str, *, error_message: str) -> int:
    """Parse one exact ASCII decimal value in the unsigned 64-bit range."""

    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > MAX_UINT64_DECIMAL_DIGITS
        or not raw.isascii()
        or not raw.isdecimal()
    ):
        raise VerificationError(error_message)
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise VerificationError(error_message) from exc
    if value > UINT64_MAX:
        raise VerificationError(error_message)
    return value
