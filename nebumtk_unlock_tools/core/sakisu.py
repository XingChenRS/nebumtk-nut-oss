# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Pinned metadata for the constrained offline SakiSU patch capability."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import VerificationError
from .operation_files import OperationFiles, VerifiedFileSnapshot
from .runner import run_command


@dataclass(frozen=True)
class SakiSUReleaseInfo:
    tag: str
    commit: str
    manager_cert_sha256: str
    supported_kmis: tuple[str, ...]


SAKISU_RELEASE = SakiSUReleaseInfo(
    tag="v4.3.0-sakisu.1",
    commit="6f9672837b9359f8853f47e18e00261edbf6d31e",
    manager_cert_sha256="f5391718452b269336a4cf0077b2fc4f9ae91253f061126a2708d5017134287c",
    supported_kmis=(
        "android12-5.10",
        "android13-5.10",
        "android13-5.15",
        "android14-5.15",
        "android14-6.1",
        "android15-6.6",
        "android16-6.12",
    ),
)


_KMI_PATTERN = re.compile(r"(?P<kernel>[56]\.\d+)[^\s]*?android(?P<android>\d+)")


def parse_sakisu_kmi(kernel_release: str) -> str:
    """Map a device-reported kernel release to one bundled SakiSU KMI."""

    release = str(kernel_release).strip()
    if not release or len(release) > 512:
        raise VerificationError("无法从设备内核版本识别 SakiSU KMI")
    match = _KMI_PATTERN.search(release)
    if match is None:
        raise VerificationError("无法从设备内核版本识别 SakiSU KMI")
    kmi = f"android{match.group('android')}-{match.group('kernel')}"
    if kmi not in SAKISU_RELEASE.supported_kmis:
        raise VerificationError(f"当前设备 KMI 不受 SakiSU 4.3 支持: {kmi}")
    return kmi


class SakiSUPatcher:
    """Run the sealed host patcher against an operation-scoped init_boot copy."""

    OUTPUT_NAME = "patched_init_boot.img"

    def __init__(self, executable: Path) -> None:
        self.executable = Path(executable)

    def prepare(
        self,
        source: VerifiedFileSnapshot,
        kernel_release: str,
        operation_files: OperationFiles,
    ) -> VerifiedFileSnapshot:
        operation_files.verify_unchanged(source)
        if not self.executable.is_file() or self.executable.stat().st_size <= 0:
            raise VerificationError("SakiSU host patcher 不可用")
        source_header, source_has_avb = _boot_structure(source.path)
        if source_header != b"ANDROID!":
            raise VerificationError("原 init_boot 不是 Android boot 镜像")
        kmi = parse_sakisu_kmi(kernel_release)
        destination = operation_files.directory / self.OUTPUT_NAME
        destination.unlink(missing_ok=True)
        args = [
            str(self.executable),
            "boot-patch",
            "--boot",
            str(source.path),
            "--kmi",
            kmi,
            "--out",
            str(operation_files.directory),
            "--out-name",
            self.OUTPUT_NAME,
        ]
        result = run_command(
            args,
            timeout=300,
            check=False,
            sensitive=True,
            cwd=operation_files.directory,
        )
        if result.returncode != 0:
            destination.unlink(missing_ok=True)
            raise VerificationError("SakiSU init_boot 修补失败")
        try:
            output = operation_files.snapshot_output(self.OUTPUT_NAME)
            output_header, output_has_avb = _boot_structure(output.path)
            if output_header != b"ANDROID!":
                raise VerificationError("SakiSU 输出不是 Android boot 镜像")
            if output_has_avb != source_has_avb:
                raise VerificationError("SakiSU 输出 AVB footer 状态异常")
            return output
        except Exception:
            destination.unlink(missing_ok=True)
            raise


def _boot_structure(path: Path) -> tuple[bytes, bool]:
    with Path(path).open("rb") as stream:
        size = stream.seek(0, 2)
        if size < 64:
            return b"", False
        stream.seek(0)
        header = stream.read(8)
        stream.seek(-64, 2)
        footer = stream.read(4)
    return header, footer == b"AVBf"
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
