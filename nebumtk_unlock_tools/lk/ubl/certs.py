# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass

from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage as LibLkImage


@dataclass(frozen=True)
class CertStatus:
    partition: str
    state: str


def inspect_cert_status(data: bytes) -> tuple[CertStatus, ...]:
    image = LibLkImage(data)
    statuses: list[CertStatus] = []
    for name, partition in image.partitions.items():
        if partition.cert2 is None:
            statuses.append(CertStatus(partition=name, state="missing-cert2"))
            continue
        try:
            matches = partition.matches_cert2()
        except Exception:
            statuses.append(CertStatus(partition=name, state="unparsable"))
            continue
        if matches is None:
            # An invalid DER blob is not proof of a valid bypass. Exact bypass
            # verification requires the original certificate and is performed
            # by validator.validate_output().
            statuses.append(CertStatus(partition=name, state="unparsable"))
        elif matches:
            statuses.append(CertStatus(partition=name, state="valid"))
        else:
            statuses.append(CertStatus(partition=name, state="invalid"))
    return tuple(statuses)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
