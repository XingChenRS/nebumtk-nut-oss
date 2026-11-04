# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest

from nebumtk_unlock_tools.core.errors import VerificationError


def test_bounded_uint64_decimal_accepts_zero_and_maximum() -> None:
    from nebumtk_unlock_tools.core.numeric import parse_bounded_uint64_decimal

    assert parse_bounded_uint64_decimal("0", error_message="invalid size") == 0
    assert (
        parse_bounded_uint64_decimal(
            "18446744073709551615",
            error_message="invalid size",
        )
        == (1 << 64) - 1
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "-1",
        "１２",
        "18446744073709551616",
        "9" * 5000,
    ],
    ids=["empty", "negative", "non-ascii", "overflow", "5000-digits"],
)
def test_bounded_uint64_decimal_rejects_non_ascii_or_out_of_range(raw: str) -> None:
    from nebumtk_unlock_tools.core.numeric import parse_bounded_uint64_decimal

    with pytest.raises(VerificationError, match="invalid size"):
        parse_bounded_uint64_decimal(raw, error_message="invalid size")
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
