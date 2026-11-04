# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass

from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage as LibLkImage


# A cert2 override is currently about 80 bytes. Keep a bounded allowance for
# DER-length/alignment variants, while making a malformed rebuild fail closed.
MAX_RAW_IMAGE_GROWTH = 256
MIN_SLOT_ZERO_PADDING = 4096
MAX_LAYOUT_ALIGNMENT_DELTA = 16


@dataclass(frozen=True)
class OutputSizePolicy:
    allowed: bool
    expected_size: int | None
    original_used: int
    output_used: int
    trailing_zeroes: int
    cert2_growth: int
    detail: str


def _used_region_len(image: LibLkImage) -> int:
    used = 0
    for partition in image.partitions.values():
        used = max(used, partition.end_offset)
        for cert in partition.certs:
            used = max(used, cert.end_offset)
    return used


def _trailing_zeroes(data: bytes) -> int:
    return len(data) - len(data.rstrip(b"\0"))


def inspect_output_size(
    original_data: bytes,
    output_data: bytes,
) -> OutputSizePolicy:
    original = LibLkImage(original_data)
    output = LibLkImage(output_data)
    original_used = _used_region_len(original)
    output_used = _used_region_len(output)
    trailing_zeroes = _trailing_zeroes(original_data)

    original_lk = original.partitions.get("lk")
    output_lk = output.partitions.get("lk")
    cert2_growth = -1
    if (
        original_lk is not None
        and output_lk is not None
        and original_lk.cert2 is not None
        and output_lk.cert2 is not None
    ):
        cert2_growth = (
            len(output_lk.cert2.data)
            - len(original_lk.cert2.data)
        )

    if output_used <= len(original_data):
        expected_size = len(original_data)
        growth_region_is_zero = (
            output_used <= original_used
            or original_data[original_used:output_used]
            == bytes(output_used - original_used)
        )
        allowed = (
            len(output_data) in {output_used, expected_size}
            and growth_region_is_zero
        )
        reason = (
            "fits input image"
            if growth_region_is_zero
            else "structured growth collides with nonzero input data"
        )
    else:
        file_growth = output_used - len(original_data)
        structured_growth = output_used - original_used
        raw_like = trailing_zeroes < MIN_SLOT_ZERO_PADDING
        cert_growth_matches_layout = (
            cert2_growth > 0
            and abs(structured_growth - cert2_growth)
            < MAX_LAYOUT_ALIGNMENT_DELTA
        )
        allowed = (
            raw_like
            and 0 < file_growth <= MAX_RAW_IMAGE_GROWTH
            and cert_growth_matches_layout
            and len(output_data) == output_used
        )
        expected_size = output_used if allowed else None
        reason = (
            "bounded raw-image cert2 growth"
            if allowed
            else "growth exceeds raw-image policy"
        )

    return OutputSizePolicy(
        allowed=allowed,
        expected_size=expected_size,
        original_used=original_used,
        output_used=output_used,
        trailing_zeroes=trailing_zeroes,
        cert2_growth=cert2_growth,
        detail=(
            f"{reason}; file={len(output_data)}/{len(original_data)} "
            f"used={output_used}/{original_used} "
            f"zero_tail={trailing_zeroes} cert2_growth={cert2_growth}"
        ),
    )
