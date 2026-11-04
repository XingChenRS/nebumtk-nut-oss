# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage as LibLkImage


@dataclass(frozen=True)
class CertInfo:
    index: int
    header_range: tuple[int, int]
    data_range: tuple[int, int]
    data_size: int


@dataclass(frozen=True)
class Partition:
    index: int
    name: str
    header_range: tuple[int, int]
    payload_range: tuple[int, int]
    payload_size: int
    certs: tuple[CertInfo, ...] = ()

    def contains_offset(self, offset: int) -> bool:
        start, end = self.payload_range
        return start <= offset < end


@dataclass(frozen=True)
class LkImage:
    path: Path
    data: bytes
    partitions: tuple[Partition, ...]

    @classmethod
    def from_path(cls, path: str | Path) -> "LkImage":
        image_path = Path(path)
        data = image_path.read_bytes()
        # Parse from the same bytes snapshot to avoid TOCTOU with a second disk read.
        return cls(path=image_path, data=data, partitions=tuple(_partitions_from_liblk(data)))

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def partition(self, name: str) -> Partition:
        for partition in self.partitions:
            if partition.name == name:
                return partition
        raise KeyError(name)

    def partition_for_offset(self, offset: int) -> Partition:
        for partition in self.partitions:
            if partition.contains_offset(offset):
                return partition
        raise ValueError(f"offset 0x{offset:X} outside payload ranges")

    def partition_search_ranges(self) -> dict[str, tuple[int, int]]:
        return {partition.name: partition.payload_range for partition in self.partitions}


def _partitions_from_liblk(data: bytes) -> list[Partition]:
    image = LibLkImage(data)
    partitions: list[Partition] = []
    cursor = 0
    for index, (name, item) in enumerate(image.partitions.items()):
        payload = bytes(item.data)
        payload_start = _find_forward(data, payload, cursor)
        payload_end = payload_start + len(payload)
        header_start = max(0, payload_start - int(item.header.size))
        certs: list[CertInfo] = []
        cursor = payload_end
        for cert_index, cert in enumerate(item.certs, start=1):
            cert_data = bytes(cert.data)
            cert_data_start = _find_forward(data, cert_data, cursor)
            cert_data_end = cert_data_start + len(cert_data)
            cert_header_start = max(0, cert_data_start - int(cert.header.size))
            certs.append(
                CertInfo(
                    index=cert_index,
                    header_range=(cert_header_start, cert_data_start),
                    data_range=(cert_data_start, cert_data_end),
                    data_size=len(cert_data),
                )
            )
            cursor = cert_data_end
        partitions.append(
            Partition(
                index=index,
                name=str(name),
                header_range=(header_start, payload_start),
                payload_range=(payload_start, payload_end),
                payload_size=len(payload),
                certs=tuple(certs),
            )
        )
    return partitions


def _find_forward(data: bytes, needle: bytes, cursor: int) -> int:
    offset = data.find(needle, cursor)
    if offset < 0:
        raise ValueError("failed to locate LK partition data inside the container")
    return offset
