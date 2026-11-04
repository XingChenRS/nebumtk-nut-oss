# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations


from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage as LibLkImage
from nebumtk_unlock_tools.lk.vendor.cert_bypass import CertBypassMode, apply_cert_bypass


def _used_region_end(image: LibLkImage) -> int:
    region_end = 0
    for partition in image.partitions.values():
        region_end = max(region_end, partition.end_offset)
        for cert in partition.certs:
            region_end = max(region_end, cert.end_offset)
    return region_end


def apply_cert_bypass_to_bytes(contents: bytes, mode: str = "override") -> tuple[bytes, tuple[str, ...]]:
    image = LibLkImage(contents)
    signed_partitions = tuple(apply_cert_bypass(image, mode=CertBypassMode(mode)))
    region_end = _used_region_end(image)
    output = bytes(image.contents[:region_end])
    return output, signed_partitions
