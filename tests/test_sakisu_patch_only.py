# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import struct
from pathlib import Path
from unittest.mock import ANY

import pytest

from nebumtk_unlock_tools.sakisu.locations import (
    ASSET_HOST,
    missing_assets_message,
    sakisu_dir,
)

from nebumtk_unlock_tools.core.errors import FlowCancelledError, VerificationError
from nebumtk_unlock_tools.core.adb import AdbClient
from nebumtk_unlock_tools.core.operation_files import VerifiedFileSnapshot, snapshot_regular_file
from nebumtk_unlock_tools.core.runner import CommandResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]

_STORE = sakisu_dir(PROJECT_ROOT, needs=ASSET_HOST)


@pytest.fixture(autouse=True)
def requires_the_sakisu_upstream_store() -> None:
    """These cases execute the real upstream patcher (see docs/MATERIAL.md)."""

    if not (_STORE / ASSET_HOST).is_file():
        pytest.skip(missing_assets_message(_STORE))



def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _elf(payload: bytes, *, elf_type: int = 3, machine: int = 183) -> bytes:
    header = bytearray(64)
    header[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHI", header, 16, elf_type, machine, 1)
    return bytes(header) + payload


def _cpio(entries: list[tuple[str, bytes, int]], *, trailer: bool = True) -> bytes:
    output = bytearray()
    ino = 1
    complete = entries + ([('TRAILER!!!', b'', 0)] if trailer else [])
    for name, data, mode in complete:
        encoded = name.encode("utf-8") + b"\0"
        fields = (
            ino,
            mode,
            0,
            0,
            1,
            0,
            len(data),
            0,
            0,
            0,
            0,
            len(encoded),
            0,
        )
        output.extend(b"070701" + b"".join(f"{field:08x}".encode("ascii") for field in fields))
        output.extend(encoded)
        output.extend(b"\0" * (_align(len(output), 4) - len(output)))
        output.extend(data)
        output.extend(b"\0" * (_align(len(output), 4) - len(output)))
        ino += 1
    return bytes(output)


def _boot(
    entries: list[tuple[str, bytes, int]],
    *,
    kernel: bytes = b"",
    magic: bytes = b"ANDROID!",
    trailer: bool = True,
    valid_avb: bool = True,
    extra_growth: int = 0,
) -> bytes:
    ramdisk = gzip.compress(_cpio(entries, trailer=trailer), mtime=0)
    header = bytearray(4096)
    header[:8] = magic
    struct.pack_into("<IIII", header, 8, len(kernel), len(ramdisk), 0, 1580)
    struct.pack_into("<I", header, 40, 4)
    image = bytes(header) + kernel
    image += b"\0" * (_align(len(image), 4096) - len(image))
    image += ramdisk + b"\0" * extra_growth
    vbmeta_offset = len(image)
    vbmeta = b"AVB0" + b"\0" * 252
    footer = bytearray(64)
    footer[:4] = b"AVBf"
    struct.pack_into(">IIQQQ", footer, 4, 1, 0, len(image), vbmeta_offset, len(vbmeta))
    if not valid_avb:
        struct.pack_into(">Q", footer, 20, len(image) + len(vbmeta) + 1)
    return image + vbmeta + bytes(footer)


REGULAR_EXECUTABLE = 0o100755
REGULAR_MODULE = 0o100644
STOCK_ENTRIES = [("init", _elf(b"stock android init"), REGULAR_EXECUTABLE)]
SAKISU_ENTRIES = [
    ("init", _elf(b"SakiSU ksuinit"), REGULAR_EXECUTABLE),
    ("init.real", _elf(b"stock android init"), REGULAR_EXECUTABLE),
    (
        "kernelsu.ko",
        _elf(
            b"name=kernelsu\0vermagic=generic\0SakiSU\0SukiSU compatibility marker",
            elf_type=1,
        ),
        REGULAR_MODULE,
    ),
]
RESUKISU_ENTRIES = [
    ("init", _elf(b"ReSukiSU ksuinit"), REGULAR_EXECUTABLE),
    ("init.real", _elf(b"stock android init"), REGULAR_EXECUTABLE),
    (
        "lib/modules/kernelsu.ko",
        _elf(b"name=kernelsu\0vermagic=5.15\0ReSukiSU", elf_type=1),
        REGULAR_MODULE,
    ),
]
OFFICIAL_KMIS = (
    "android12-5.10",
    "android13-5.10",
    "android13-5.15",
    "android14-5.15",
    "android14-6.1",
    "android15-6.6",
    "android16-6.12",
)


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("name", "entries", "expected"),
    [
        ("stock", STOCK_ENTRIES, "apparent_stock"),
        ("sakisu", SAKISU_ENTRIES, "sakisu"),
        ("pd2502-resukisu", RESUKISU_ENTRIES, "other_ksu"),
        (
            "unknown",
            [("init", _elf(b"magiskinit modified"), REGULAR_EXECUTABLE)],
            "unknown_modified",
        ),
    ],
)
def test_classifier_distinguishes_all_four_classes(
    tmp_path: Path,
    name: str,
    entries: list[tuple[str, bytes, int]],
    expected: str,
) -> None:
    from nebumtk_unlock_tools.sakisu.boot_image import classify_boot_image

    path = _write(tmp_path / f"{name}.img", _boot(entries))

    assert classify_boot_image(path, partition="init_boot").value == expected


def test_classifier_rejects_vendor_boot_even_when_header_looks_like_boot(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu.boot_image import classify_boot_image

    path = _write(tmp_path / "vendor_boot.img", _boot(STOCK_ENTRIES))
    with pytest.raises(VerificationError, match="vendor_boot"):
        classify_boot_image(path, partition="vendor_boot")


def test_verifier_accepts_changed_coherent_generic_sakisu_candidate(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu.boot_image import (
        VerifiedPatchCandidate,
        verify_patch_candidate,
    )

    source = _write(tmp_path / "source.img", _boot(STOCK_ENTRIES))
    candidate = _write(tmp_path / "candidate.img", _boot(SAKISU_ENTRIES))

    result = verify_patch_candidate(source, candidate, partition="init_boot")

    assert isinstance(result, VerifiedPatchCandidate)
    assert result.classification.value == "sakisu"
    assert result.output_sha256 == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert result.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("case", "candidate", "pattern"),
    [
        ("identical", lambda source: source, "identical"),
        ("oversized", lambda _source: _boot(SAKISU_ENTRIES, extra_growth=17 * 1024 * 1024), "growth"),
        ("header", lambda _source: _boot(SAKISU_ENTRIES, magic=b"BROKEN!!"), "header"),
        ("ramdisk", lambda _source: _boot(SAKISU_ENTRIES, trailer=False), "ramdisk"),
        ("avb", lambda _source: _boot(SAKISU_ENTRIES, valid_avb=False), "AVB"),
        ("missing-init-real", lambda _source: _boot([SAKISU_ENTRIES[0], SAKISU_ENTRIES[2]]), "init.real"),
        (
            "mixed-lkm",
            lambda _source: _boot(
                SAKISU_ENTRIES
                + [
                    (
                        "lib/modules/resukisu.ko",
                        _elf(b"name=resukisu\0ReSukiSU", elf_type=1),
                        REGULAR_MODULE,
                    )
                ]
            ),
            "LKM",
        ),
    ],
)
def test_verifier_rejects_unsafe_candidate(
    tmp_path: Path,
    case: str,
    candidate,
    pattern: str,
) -> None:
    from nebumtk_unlock_tools.sakisu.boot_image import verify_patch_candidate

    source_bytes = _boot(STOCK_ENTRIES)
    source = _write(tmp_path / "source.img", source_bytes)
    output = _write(tmp_path / f"{case}.img", candidate(source_bytes))

    with pytest.raises(VerificationError, match=pattern):
        verify_patch_candidate(source, output, partition="init_boot")


@pytest.mark.parametrize(
    ("case", "entries", "pattern"),
    [
        (
            "non-elf-init",
            [("init", b"SakiSU ksuinit", REGULAR_EXECUTABLE), *SAKISU_ENTRIES[1:]],
            "AArch64 ELF",
        ),
        (
            "non-elf-init-real",
            [SAKISU_ENTRIES[0], ("init.real", b"stock init", REGULAR_EXECUTABLE), SAKISU_ENTRIES[2]],
            "AArch64 ELF",
        ),
        (
            "wrong-arch-module",
            [
                *SAKISU_ENTRIES[:2],
                (
                    "kernelsu.ko",
                    _elf(b"name=kernelsu\0vermagic=generic\0SakiSU", elf_type=1, machine=62),
                    REGULAR_MODULE,
                ),
            ],
            "AArch64 ELF",
        ),
        (
            "symlink-init",
            [("init", _elf(b"SakiSU ksuinit"), 0o120777), *SAKISU_ENTRIES[1:]],
            "regular",
        ),
        (
            "directory-init-real",
            [SAKISU_ENTRIES[0], ("init.real", b"", 0o040755), SAKISU_ENTRIES[2]],
            "regular",
        ),
        (
            "non-executable-init",
            [("init", _elf(b"SakiSU ksuinit"), 0o100644), *SAKISU_ENTRIES[1:]],
            "executable",
        ),
        (
            "hidden-resukisu-module",
            [
                *SAKISU_ENTRIES,
                (
                    "lib/modules/thermal_driver.ko",
                    _elf(b"name=resukisu\0ReSukiSU", elf_type=1),
                    REGULAR_MODULE,
                ),
            ],
            "mixed",
        ),
        (
            "target-without-sakisu-identity",
            [
                *SAKISU_ENTRIES[:2],
                (
                    "kernelsu.ko",
                    _elf(
                        b"name=kernelsu\0vermagic=generic\0SukiSU",
                        elf_type=1,
                    ),
                    REGULAR_MODULE,
                ),
            ],
            "expected generic",
        ),
        (
            "wrong-module-path",
            [
                *SAKISU_ENTRIES[:2],
                (
                    "lib/modules/kernelsu.ko",
                    SAKISU_ENTRIES[2][1],
                    REGULAR_MODULE,
                ),
            ],
            "canonical",
        ),
    ],
)
def test_verifier_rejects_noncanonical_or_non_aarch64_payloads(
    tmp_path: Path,
    case: str,
    entries: list[tuple[str, bytes, int]],
    pattern: str,
) -> None:
    from nebumtk_unlock_tools.sakisu.boot_image import verify_patch_candidate

    source = _write(tmp_path / "source.img", _boot(STOCK_ENTRIES))
    candidate = _write(tmp_path / f"{case}.img", _boot(entries))

    with pytest.raises(VerificationError, match=pattern):
        verify_patch_candidate(source, candidate, partition="init_boot")


class FakeAdb:
    def __init__(
        self,
        candidate: bytes,
        *,
        patch_returncode: int = 0,
        remote_size: int | None = None,
        cleanup_returncode: int = 0,
        cleanup_error: Exception | None = None,
        destination_to_observe: Path | None = None,
        expected_destination: bytes | None = None,
    ) -> None:
        self.candidate = candidate
        self.patch_returncode = patch_returncode
        self.size = len(candidate) if remote_size is None else remote_size
        self.cleanup_returncode = cleanup_returncode
        self.cleanup_error = cleanup_error
        self.destination_to_observe = destination_to_observe
        self.expected_destination = expected_destination
        self.events: list[tuple] = []

    def run(self, *args: str, **kwargs) -> CommandResult:
        self.events.append(("run", args, kwargs))
        if "boot-patch" in args:
            return CommandResult(self.patch_returncode, "", "patch failed" if self.patch_returncode else "")
        if "rm" in args:
            if self.destination_to_observe is not None:
                assert self.destination_to_observe.read_bytes() == self.expected_destination
            if self.cleanup_error is not None:
                raise self.cleanup_error
            return CommandResult(self.cleanup_returncode, "", "cleanup failed" if self.cleanup_returncode else "")
        return CommandResult(0, "", "")

    def push(self, local: Path, remote: str, **kwargs) -> None:
        self.events.append(("push", local.name, remote, kwargs))

    def remote_file_size(self, remote: str, *, root: bool = True) -> int:
        self.events.append(("size", remote, root))
        return self.size

    def pull(self, remote: str, local: Path, *, cancelled=None) -> None:
        self.events.append(("pull", remote, local.name, cancelled is not None))
        local.write_bytes(self.candidate)


def _snapshot(path: Path, data: bytes) -> VerifiedFileSnapshot:
    path.write_bytes(data)
    return VerifiedFileSnapshot(path, len(data), hashlib.sha256(data).hexdigest())


def _patch_inputs(tmp_path: Path, *, source_entries=STOCK_ENTRIES):
    from nebumtk_unlock_tools.sakisu import (
        ManualKmiSelection,
        authorize_patch_source,
        load_imported_patcher,
    )

    patcher, _manifest = load_imported_patcher(PROJECT_ROOT)
    source = _snapshot(tmp_path / "source.img", _boot(source_entries))
    rollback = None
    confirmed = False
    if source_entries != STOCK_ENTRIES:
        rollback = _snapshot(tmp_path / "rollback.img", source.path.read_bytes())
        confirmed = True
    authorization = authorize_patch_source(
        source,
        partition="init_boot",
        replace_confirmed=confirmed,
        rollback=rollback,
    )
    selection = ManualKmiSelection(
        kmi="android13-5.10",
        wrong_kmi_boot_risk_confirmed=True,
    )
    return patcher, source, authorization, selection


def test_windows_host_patcher_runs_locally_with_exact_argv_and_no_device(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu import load_imported_host_patcher
    from nebumtk_unlock_tools.sakisu.host_patcher import WindowsHostPatcher

    _android, source, authorization, selection = _patch_inputs(tmp_path)
    host, _manifest = load_imported_host_patcher(PROJECT_ROOT)
    candidate_bytes = _boot(SAKISU_ENTRIES)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(args, **kwargs):
        calls.append((list(args), kwargs))
        output = Path(args[args.index("--out") + 1]) / args[args.index("--out-name") + 1]
        output.write_bytes(candidate_bytes)
        return CommandResult(0, "done", "")

    destination = tmp_path / "exports" / "candidate image.img"
    result = WindowsHostPatcher(tmp_path / "workspace", runner=runner).patch(
        host,
        source,
        authorization=authorization,
        manual_kmi=selection,
        partition="init_boot",
        destination=destination,
        cancelled=lambda: False,
    )

    assert result.path == destination
    assert destination.read_bytes() == candidate_bytes
    assert calls == [
        (
            [
                str(host.path),
                "boot-patch",
                "--boot",
                str(source.path),
                "--out",
                calls[0][0][5],
                "--out-name",
                "candidate.img",
                "--kmi",
                "android13-5.10",
            ],
            {
                "timeout": 300,
                "check": False,
                "sensitive": True,
                "cancelled": ANY,
            },
        )
    ]
    assert not list((tmp_path / "workspace" / "tmp").iterdir())


@pytest.mark.parametrize("failure", ("nonzero", "missing", "cancelled"))
def test_windows_host_patcher_failure_never_publishes_and_always_cleans(
    tmp_path: Path,
    failure: str,
) -> None:
    from nebumtk_unlock_tools.sakisu import load_imported_host_patcher
    from nebumtk_unlock_tools.sakisu.host_patcher import WindowsHostPatcher

    _android, source, authorization, selection = _patch_inputs(tmp_path)
    host, _manifest = load_imported_host_patcher(PROJECT_ROOT)
    destination = tmp_path / "candidate.img"
    destination.write_bytes(b"existing")

    def runner(args, **kwargs):
        if failure == "cancelled":
            raise FlowCancelledError("cancelled")
        if failure != "missing":
            output = Path(args[args.index("--out") + 1]) / "candidate.img"
            output.write_bytes(_boot(SAKISU_ENTRIES))
        return CommandResult(7 if failure == "nonzero" else 0, "", "failed")

    with pytest.raises((VerificationError, FlowCancelledError)):
        WindowsHostPatcher(tmp_path / "workspace", runner=runner).patch(
            host,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=destination,
            cancelled=lambda: False,
        )

    assert destination.read_bytes() == b"existing"
    assert not list((tmp_path / "workspace" / "tmp").iterdir())


def test_windows_host_patcher_rejects_unreviewed_executable_before_launch(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu.host_patcher import WindowsHostPatcher

    _android, source, authorization, selection = _patch_inputs(tmp_path)
    host = _snapshot(tmp_path / "unreviewed.exe", b"MZ unreviewed executable")
    calls: list[object] = []

    with pytest.raises(VerificationError, match="official SakiSU Windows patcher"):
        WindowsHostPatcher(
            tmp_path / "workspace",
            runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        ).patch(
            host,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=tmp_path / "candidate.img",
            cancelled=lambda: False,
        )

    assert calls == []


@pytest.mark.parametrize("kmi", OFFICIAL_KMIS)
def test_android_patcher_passes_each_confirmed_official_kmi_as_exact_argv(
    tmp_path: Path,
    kmi: str,
) -> None:
    from nebumtk_unlock_tools.sakisu import ManualKmiSelection
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    candidate = _boot(SAKISU_ENTRIES)
    patcher, source, authorization, _selection = _patch_inputs(tmp_path)
    selection = ManualKmiSelection(
        kmi=kmi,
        wrong_kmi_boot_risk_confirmed=True,
    )
    destination = tmp_path / "candidate.img"
    previous = b"preserve until verified and cleaned"
    destination.write_bytes(previous)
    adb = FakeAdb(
        candidate,
        destination_to_observe=destination,
        expected_destination=previous,
    )

    output = AndroidDevicePatcher(adb, token_factory=lambda: "a" * 32).patch(
        patcher,
        source,
        authorization=authorization,
        manual_kmi=selection,
        partition="init_boot",
        destination=destination,
        cancelled=lambda: False,
    )

    remote = "/data/local/tmp/.nebumtk-sakisu-" + "a" * 32
    assert output.path == destination
    assert adb.events == [
        ("run", ("shell", "mkdir", "--", remote), {"timeout": 20, "sensitive": True}),
        (
            "push",
            "official-patcher-arm64",
            f"{remote}/patcher",
            {"cancelled": ANY, "sensitive": True},
        ),
        ("push", "source.img", f"{remote}/source.img", {"cancelled": ANY, "sensitive": True}),
        ("run", ("shell", "chmod", "0700", f"{remote}/patcher"), {"timeout": 20, "sensitive": True}),
        (
            "run",
            (
                "shell",
                "env",
                f"TMPDIR={remote}",
                f"{remote}/patcher",
                "boot-patch",
                "--boot",
                f"{remote}/source.img",
                "--out",
                remote,
                "--out-name",
                "candidate.img",
                "--partition",
                "init_boot",
                "--kmi",
                kmi,
            ),
            {"timeout": 300, "check": False, "sensitive": True, "cancelled": ANY},
        ),
        ("size", f"{remote}/candidate.img", False),
        ("pull", f"{remote}/candidate.img", ANY, True),
        ("run", ("shell", "rm", "-rf", "--", remote), {"timeout": 30, "check": False, "sensitive": True}),
    ]
    pending_name = adb.events[-2][2]
    assert re.fullmatch(r"\.candidate\.img\.[0-9a-f]{32}\.pending", pending_name)
    assert destination.read_bytes() == candidate
    assert not list(tmp_path.glob("*.pending"))
    command_tokens = [token for event in adb.events if event[0] == "run" for token in event[1]]
    assert not {"dd", "su", "fastboot", "flash", "set_active", "reboot", "install"}.intersection(command_tokens)


def test_android_patcher_accepts_confirmed_manual_kmi_for_boot_without_paired_image(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu import (
        ManualKmiSelection,
        authorize_patch_source,
        load_imported_patcher,
    )
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, _manifest = load_imported_patcher(PROJECT_ROOT)
    source = _snapshot(tmp_path / "boot.img", _boot(STOCK_ENTRIES))
    authorization = authorize_patch_source(
        source,
        partition="boot",
        replace_confirmed=False,
    )
    adb = FakeAdb(_boot(SAKISU_ENTRIES))

    AndroidDevicePatcher(adb, token_factory=lambda: "1" * 32).patch(
        patcher,
        source,
        authorization=authorization,
        manual_kmi=ManualKmiSelection(
            kmi="android14-6.1",
            wrong_kmi_boot_risk_confirmed=True,
        ),
        partition="boot",
        destination=tmp_path / "candidate.img",
        cancelled=lambda: False,
    )

    patch_command = next(event[1] for event in adb.events if "boot-patch" in event[1])
    assert patch_command[-4:] == ("--partition", "boot", "--kmi", "android14-6.1")


def test_android_patcher_rejects_self_consistent_nonofficial_patcher_before_adb(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    _official_patcher, source, authorization, selection = _patch_inputs(tmp_path)
    patcher = _snapshot(tmp_path / "patcher", b"\x7fELF self-consistent generic patcher")
    adb = FakeAdb(_boot(SAKISU_ENTRIES))

    with pytest.raises(VerificationError, match="official.*patcher"):
        AndroidDevicePatcher(adb).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=tmp_path / "candidate.img",
            cancelled=lambda: False,
        )

    assert adb.events == []


@pytest.mark.parametrize("failure", ["nonzero", "missing", "oversized", "cancelled"])
def test_android_patcher_failure_and_cancellation_always_cleanup(tmp_path: Path, failure: str) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, source, authorization, selection = _patch_inputs(tmp_path)
    candidate = _boot(SAKISU_ENTRIES)
    adb = FakeAdb(
        candidate,
        patch_returncode=1 if failure == "nonzero" else 0,
        remote_size=-1 if failure == "missing" else (source.size + 17 * 1024 * 1024 if failure == "oversized" else None),
    )
    calls = {"count": 0}

    def cancelled() -> bool:
        calls["count"] += 1
        return failure == "cancelled" and calls["count"] >= 2

    destination = tmp_path / "candidate.img"
    previous = b"existing candidate remains"
    destination.write_bytes(previous)
    with pytest.raises((VerificationError, FlowCancelledError)):
        AndroidDevicePatcher(adb, token_factory=lambda: "b" * 32).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=destination,
            cancelled=cancelled,
        )
    assert adb.events[-1][0:2] == ("run", ("shell", "rm", "-rf", "--", "/data/local/tmp/.nebumtk-sakisu-" + "b" * 32))
    assert destination.read_bytes() == previous
    assert not list(tmp_path.glob(".*.pending"))


@pytest.mark.parametrize("alias_kind", ["source", "patcher", "resolved-source", "hardlink-source"])
def test_android_patcher_rejects_destination_aliases_before_adb(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, source, authorization, selection = _patch_inputs(tmp_path)
    if alias_kind == "source":
        destination = source.path
    elif alias_kind == "patcher":
        destination = patcher.path
    elif alias_kind == "resolved-source":
        nested = tmp_path / "nested"
        nested.mkdir()
        destination = nested / ".." / source.path.name
    else:
        destination = tmp_path / "source-hardlink.img"
        try:
            os.link(source.path, destination)
        except OSError:
            pytest.skip("hard links are unavailable")
    source_before = source.path.read_bytes()
    patcher_before = patcher.path.read_bytes()
    adb = FakeAdb(_boot(SAKISU_ENTRIES))

    with pytest.raises(VerificationError, match="alias"):
        AndroidDevicePatcher(adb).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=destination,
            cancelled=lambda: False,
        )

    assert source.path.read_bytes() == source_before
    assert patcher.path.read_bytes() == patcher_before
    assert adb.events == []


@pytest.mark.parametrize("cleanup_mode", ["returncode", "exception"])
def test_cleanup_failure_preserves_existing_destination_and_removes_pending(
    tmp_path: Path,
    cleanup_mode: str,
) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, source, authorization, selection = _patch_inputs(tmp_path)
    destination = tmp_path / "candidate.img"
    previous = b"existing candidate"
    destination.write_bytes(previous)
    adb = FakeAdb(
        _boot(SAKISU_ENTRIES),
        cleanup_returncode=1 if cleanup_mode == "returncode" else 0,
        cleanup_error=RuntimeError("cleanup transport lost") if cleanup_mode == "exception" else None,
    )

    with pytest.raises(VerificationError, match="cleanup"):
        AndroidDevicePatcher(adb, token_factory=lambda: "c" * 32).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=destination,
            cancelled=lambda: False,
        )

    assert destination.read_bytes() == previous
    assert not list(tmp_path.glob(".*.pending"))


def test_cleanup_exception_does_not_mask_primary_patch_failure(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, source, authorization, selection = _patch_inputs(tmp_path)
    adb = FakeAdb(
        _boot(SAKISU_ENTRIES),
        patch_returncode=9,
        cleanup_error=RuntimeError("cleanup transport lost"),
    )

    with pytest.raises(VerificationError, match="returned nonzero") as raised:
        AndroidDevicePatcher(adb, token_factory=lambda: "d" * 32).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=tmp_path / "candidate.img",
            cancelled=lambda: False,
        )

    assert "cleanup transport lost" not in str(raised.value)
    assert not list(tmp_path.glob(".*.pending"))


def test_invalid_pulled_candidate_is_not_published(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, source, authorization, selection = _patch_inputs(tmp_path)
    destination = tmp_path / "candidate.img"
    previous = b"existing candidate"
    destination.write_bytes(previous)
    adb = FakeAdb(b"not a boot image")

    with pytest.raises(VerificationError, match="header"):
        AndroidDevicePatcher(adb, token_factory=lambda: "e" * 32).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=destination,
            cancelled=lambda: False,
        )

    assert destination.read_bytes() == previous
    assert not list(tmp_path.glob(".*.pending"))


def test_adb_push_forwards_cancellation_and_sensitive_flag(tmp_path: Path, monkeypatch) -> None:
    source = _write(tmp_path / "source.bin", b"immutable")
    recorded: list[tuple[tuple[str, ...], dict[str, object]]] = []
    adb = AdbClient()

    def fake_run(*args: str, **kwargs) -> CommandResult:
        recorded.append((args, kwargs))
        return CommandResult(0, "", "")

    monkeypatch.setattr(adb, "run", fake_run)
    cancelled = lambda: False

    adb.push(source, "/data/local/tmp/source.bin", cancelled=cancelled, sensitive=True)

    assert recorded == [
        (
            ("push", str(source), "/data/local/tmp/source.bin"),
            {"timeout": 300, "cancelled": cancelled, "sensitive": True},
        )
    ]


@pytest.mark.parametrize(
    ("selection", "pattern"),
    [
        (None, "typed manual KMI"),
        ("raw-string", "typed manual KMI"),
    ],
)
def test_missing_or_untyped_manual_kmi_fails_before_any_adb(
    tmp_path: Path,
    selection: object,
    pattern: str,
) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, source, authorization, _selection = _patch_inputs(tmp_path)
    adb = FakeAdb(_boot(SAKISU_ENTRIES))

    with pytest.raises(VerificationError, match=pattern):
        AndroidDevicePatcher(adb).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=tmp_path / "candidate.img",
            cancelled=lambda: False,
        )

    assert adb.events == []


@pytest.mark.parametrize(
    ("kmi", "confirmed", "pattern"),
    [
        ("android11-5.4", True, "allowlist"),
        ("ANDROID14-6.1", True, "allowlist"),
        ("android14-6.1 ", True, "allowlist"),
        ("android14-6.1", False, "explicit confirmation"),
        ("android14-6.1", 1, "explicit confirmation"),
    ],
)
def test_invalid_or_unconfirmed_manual_kmi_fails_before_any_adb(
    tmp_path: Path,
    kmi: str,
    confirmed: object,
    pattern: str,
) -> None:
    from nebumtk_unlock_tools.sakisu import ManualKmiSelection
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, source, authorization, _selection = _patch_inputs(tmp_path)
    adb = FakeAdb(_boot(SAKISU_ENTRIES))

    with pytest.raises(VerificationError, match=pattern):
        AndroidDevicePatcher(adb).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=ManualKmiSelection(
                kmi=kmi,
                wrong_kmi_boot_risk_confirmed=confirmed,
            ),
            partition="init_boot",
            destination=tmp_path / "candidate.img",
            cancelled=lambda: False,
        )

    assert adb.events == []


def test_cross_image_kmi_evidence_api_is_no_longer_reachable() -> None:
    import nebumtk_unlock_tools.sakisu as sakisu

    for removed in ("KmiEvidence", "KmiEvidenceKind", "derive_kmi_evidence"):
        with pytest.raises(AttributeError):
            getattr(sakisu, removed)


def test_push_cancellation_still_runs_remote_cleanup_and_preserves_destination(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    class CancellingPushAdb(FakeAdb):
        def push(self, local: Path, remote: str, **kwargs) -> None:
            super().push(local, remote, **kwargs)
            raise FlowCancelledError("push cancelled")

    patcher, source, authorization, selection = _patch_inputs(tmp_path)
    destination = tmp_path / "candidate.img"
    previous = b"existing candidate"
    destination.write_bytes(previous)
    adb = CancellingPushAdb(_boot(SAKISU_ENTRIES))

    with pytest.raises(FlowCancelledError, match="push cancelled"):
        AndroidDevicePatcher(adb, token_factory=lambda: "f" * 32).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=destination,
            cancelled=lambda: False,
        )

    assert adb.events[1][3] == {"cancelled": ANY, "sensitive": True}
    assert adb.events[-1][0:2] == (
        "run",
        ("shell", "rm", "-rf", "--", "/data/local/tmp/.nebumtk-sakisu-" + "f" * 32),
    )
    assert destination.read_bytes() == previous
    assert not list(tmp_path.glob(".*.pending"))


def test_nonstock_authorization_requires_confirmation_and_preserved_rollback(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu import authorize_patch_source

    source = _snapshot(tmp_path / "source.img", _boot(RESUKISU_ENTRIES))
    with pytest.raises(VerificationError, match="confirmation"):
        authorize_patch_source(source, partition="init_boot", replace_confirmed=False)
    with pytest.raises(VerificationError, match="rollback"):
        authorize_patch_source(source, partition="init_boot", replace_confirmed=True)
    wrong = _snapshot(tmp_path / "wrong-rollback.img", _boot(STOCK_ENTRIES))
    with pytest.raises(VerificationError, match="rollback"):
        authorize_patch_source(
            source,
            partition="init_boot",
            replace_confirmed=True,
            rollback=wrong,
        )

    rollback = _snapshot(tmp_path / "rollback.img", source.path.read_bytes())
    authorization = authorize_patch_source(
        source,
        partition="init_boot",
        replace_confirmed=True,
        rollback=rollback,
    )

    assert authorization.classification.value == "other_ksu"
    assert authorization.rollback_size == source.size
    assert authorization.rollback_sha256 == source.sha256


def test_nonstock_rollback_must_remain_a_distinct_preserved_snapshot(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu import (
        ManualKmiSelection,
        authorize_patch_source,
        load_imported_patcher,
    )
    from nebumtk_unlock_tools.sakisu.device_patcher import AndroidDevicePatcher

    patcher, _manifest = load_imported_patcher(PROJECT_ROOT)
    source = _snapshot(tmp_path / "source.img", _boot(RESUKISU_ENTRIES))
    rollback = _snapshot(tmp_path / "rollback.img", source.path.read_bytes())
    authorization = authorize_patch_source(
        source,
        partition="init_boot",
        replace_confirmed=True,
        rollback=rollback,
    )
    selection = ManualKmiSelection(
        kmi="android13-5.10",
        wrong_kmi_boot_risk_confirmed=True,
    )
    rollback.path.unlink()
    try:
        os.link(source.path, rollback.path)
    except OSError:
        pytest.skip("hard links are unavailable")
    adb = FakeAdb(_boot(SAKISU_ENTRIES))

    with pytest.raises(VerificationError, match="distinct"):
        AndroidDevicePatcher(adb).patch(
            patcher,
            source,
            authorization=authorization,
            manual_kmi=selection,
            partition="init_boot",
            destination=tmp_path / "candidate.img",
            cancelled=lambda: False,
        )

    assert adb.events == []


def test_receipt_contains_hashes_classification_and_public_tool_identity_only(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu import (
        build_patch_receipt,
        load_patch_receipt,
        write_patch_receipt,
    )

    _patcher, source, authorization, selection = _patch_inputs(
        tmp_path,
        source_entries=RESUKISU_ENTRIES,
    )
    output = _snapshot(tmp_path / "candidate.img", _boot(SAKISU_ENTRIES))
    receipt = build_patch_receipt(
        authorization=authorization,
        manual_kmi=selection,
        output=output,
    )
    path = write_patch_receipt(tmp_path / "receipt.json", receipt)

    decoded = json.loads(path.read_text(encoding="utf-8"))
    assert decoded == receipt.to_dict()
    assert load_patch_receipt(path) == receipt
    assert decoded["tool"] == {
        "repository": "XingChenRS/SakiSU",
        "tag": "v4.3.0-sakisu.1",
        "apk_sha256": "269f43a4232eacc3dcff657e7a9e1de1697e0e9732b032aa63c3034bc5b3a0ea",
        "patcher_sha256": "53e432f1b2d2b1ac1cfc69a6fbdeddc1e91ce148ed77dc20ceb0afcba7991dd4",
    }
    assert decoded["kmi"] == {
        "value": "android13-5.10",
        "manual_selection": True,
    }
    assert decoded["rollback"] == {"size": source.size, "sha256": source.sha256}
    assert decoded["output"]["classification"] == "sakisu"
    serialized = json.dumps(decoded, sort_keys=True).casefold()
    for forbidden in ("path", "ramdisk", "payload", "secret", "token", "signature", "command"):
        assert forbidden not in serialized


def test_receipt_writer_rejects_arbitrary_or_mutated_input(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu import build_patch_receipt, write_patch_receipt

    with pytest.raises(VerificationError, match="typed"):
        write_patch_receipt(tmp_path / "receipt.json", {"schema_version": 1})

    _patcher, source, authorization, selection = _patch_inputs(tmp_path)
    output = _snapshot(tmp_path / "candidate.img", _boot(SAKISU_ENTRIES))
    source.path.write_bytes(source.path.read_bytes() + b"changed")
    with pytest.raises(VerificationError, match="source"):
        build_patch_receipt(
            authorization=authorization,
            manual_kmi=selection,
            output=output,
        )


def test_receipt_rejects_unconfirmed_or_nonmanual_kmi_schema(tmp_path: Path) -> None:
    from dataclasses import replace

    from nebumtk_unlock_tools.sakisu import (
        ManualKmiSelection,
        build_patch_receipt,
    )

    _patcher, _source, authorization, selection = _patch_inputs(tmp_path)
    output = _snapshot(tmp_path / "candidate.img", _boot(SAKISU_ENTRIES))
    with pytest.raises(VerificationError, match="explicit confirmation"):
        build_patch_receipt(
            authorization=authorization,
            manual_kmi=ManualKmiSelection(
                kmi="android13-5.10",
                wrong_kmi_boot_risk_confirmed=False,
            ),
            output=output,
        )

    receipt = build_patch_receipt(
        authorization=authorization,
        manual_kmi=selection,
        output=output,
    )
    with pytest.raises(VerificationError, match="manual KMI"):
        replace(receipt, manual_selection=False)


def test_receipt_builder_rejects_self_consistent_nonboot_output(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.sakisu import build_patch_receipt

    _patcher, _source, authorization, selection = _patch_inputs(tmp_path)
    output = _snapshot(tmp_path / "candidate.img", b"self-consistent garbage")

    with pytest.raises(VerificationError, match="boot header"):
        build_patch_receipt(
            authorization=authorization,
            manual_kmi=selection,
            output=output,
        )


@pytest.mark.parametrize(
    ("changes", "pattern"),
    [
        ({"source_size": True}, "size"),
        ({"output_size": True}, "size"),
        ({"replace_confirmed": 1}, "replace confirmation"),
        ({"output_sha256": "same-as-source"}, "different"),
    ],
)
def test_patch_receipt_rejects_python_bool_coercion_and_equal_hashes(
    tmp_path: Path,
    changes: dict[str, object],
    pattern: str,
) -> None:
    from dataclasses import replace

    from nebumtk_unlock_tools.sakisu import build_patch_receipt

    _patcher, _source, authorization, selection = _patch_inputs(tmp_path)
    output = _snapshot(tmp_path / "candidate.img", _boot(SAKISU_ENTRIES))
    receipt = build_patch_receipt(
        authorization=authorization,
        manual_kmi=selection,
        output=output,
    )
    if changes.get("output_sha256") == "same-as-source":
        changes = {"output_sha256": receipt.source_sha256}

    with pytest.raises(VerificationError, match=pattern):
        replace(receipt, **changes)


@pytest.mark.parametrize(
    ("mutation", "pattern"),
    [
        (("source", "size", True), "size"),
        (("source", "replace_confirmed", 1), "replace confirmation"),
        (("output", "sha256", "copy-source"), "different"),
        (("source", "unexpected", "value"), "unknown"),
        (("root", "rollback", None), "object"),
    ],
)
def test_patch_receipt_json_loader_is_strict(
    tmp_path: Path,
    mutation: tuple[str, str, object],
    pattern: str,
) -> None:
    import nebumtk_unlock_tools.sakisu as sakisu

    _patcher, _source, authorization, selection = _patch_inputs(tmp_path)
    output = _snapshot(tmp_path / "candidate.img", _boot(SAKISU_ENTRIES))
    receipt = sakisu.build_patch_receipt(
        authorization=authorization,
        manual_kmi=selection,
        output=output,
    )
    payload = receipt.to_dict()
    section, key, value = mutation
    if value == "copy-source":
        value = payload["source"]["sha256"]
    if section == "root":
        payload[key] = value
    else:
        payload[section][key] = value
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loader = getattr(sakisu, "load_patch_receipt", None)
    assert callable(loader), "strict receipt JSON loader is missing"

    with pytest.raises(VerificationError, match=pattern):
        loader(path)


def test_typed_flow_preserves_rollback_patches_selected_image_and_writes_receipt(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu import (
        SakisuPatchFlow,
        SakisuPatchRequest,
        load_patch_receipt,
    )

    source_bytes = _boot(STOCK_ENTRIES)
    candidate_bytes = _boot(SAKISU_ENTRIES)
    source = _write(tmp_path / "selected-init-boot.img", source_bytes)
    rollback = tmp_path / "exports" / "selected-init-boot.rollback.img"
    candidate = tmp_path / "exports" / "selected-init-boot.sakisu.img"
    receipt = tmp_path / "exports" / "selected-init-boot.sakisu.json"
    adb = FakeAdb(candidate_bytes)
    request = SakisuPatchRequest(
        source=snapshot_regular_file(source),
        partition="init_boot",
        kmi="android13-5.10",
        wrong_kmi_boot_risk_confirmed=True,
        replace_non_stock_confirmed=False,
        rollback=rollback,
        candidate=candidate,
        receipt=receipt,
    )

    result = SakisuPatchFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        adb,
        token_factory=lambda: "9" * 32,
    ).run(request, cancelled=lambda: False)

    assert result.source_classification.value == "apparent_stock"
    assert result.rollback.path == rollback
    assert result.candidate.path == candidate
    assert result.receipt_path == receipt
    assert rollback.read_bytes() == source_bytes
    assert candidate.read_bytes() == candidate_bytes
    assert load_patch_receipt(receipt) == result.receipt
    assert result.receipt.kmi == "android13-5.10"
    assert result.receipt.rollback_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert not list((tmp_path / "workspace" / "tmp").iterdir())
    command_tokens = [
        token
        for event in adb.events
        if event[0] == "run"
        for token in event[1]
    ]
    assert not {
        "dd",
        "flash",
        "set_active",
        "reboot",
        "install",
    }.intersection(command_tokens)


def test_typed_flow_can_patch_entirely_on_windows_without_adb(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu import (
        SakisuPatchFlow,
        SakisuPatchRequest,
        load_patch_receipt,
    )

    source = _write(tmp_path / "selected init boot.img", _boot(STOCK_ENTRIES))
    request = SakisuPatchRequest(
        source=snapshot_regular_file(source),
        partition="init_boot",
        kmi="android13-5.15",
        wrong_kmi_boot_risk_confirmed=True,
        replace_non_stock_confirmed=False,
        rollback=tmp_path / "exports" / "rollback.img",
        candidate=tmp_path / "exports" / "candidate.img",
        receipt=tmp_path / "exports" / "receipt.json",
    )
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        output = Path(args[args.index("--out") + 1]) / "candidate.img"
        output.write_bytes(_boot(SAKISU_ENTRIES))
        return CommandResult(0, "", "")

    result = SakisuPatchFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        execution="host",
        host_runner=runner,
    ).run(request, cancelled=lambda: False)

    assert len(calls) == 1
    assert result.candidate.path == request.candidate
    receipt = load_patch_receipt(request.receipt).to_dict()
    assert receipt["tool"] == {
        "repository": "XingChenRS/SakiSU",
        "tag": "v4.3.0-sakisu.1",
        "source_commit": "6f9672837b9359f8853f47e18e00261edbf6d31e",
        "target": "x86_64-pc-windows-msvc",
        "patcher_sha256": "1b8a6100b301700ec00b3c78f5afcab953960a30322b9f1479f283abb95805b3",
    }


def test_typed_worker_emits_the_verified_patch_result(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.gui.sakisu_worker import SakisuPatchWorker
    from nebumtk_unlock_tools.sakisu import SakisuPatchFlow, SakisuPatchRequest

    source = _write(tmp_path / "selected-boot.img", _boot(STOCK_ENTRIES))
    request = SakisuPatchRequest(
        source=snapshot_regular_file(source),
        partition="boot",
        kmi="android14-6.1",
        wrong_kmi_boot_risk_confirmed=True,
        replace_non_stock_confirmed=False,
        rollback=tmp_path / "exports" / "boot.rollback.img",
        candidate=tmp_path / "exports" / "boot.sakisu.img",
        receipt=tmp_path / "exports" / "boot.sakisu.json",
    )
    flow = SakisuPatchFlow(
        PROJECT_ROOT,
        tmp_path / "workspace",
        FakeAdb(_boot(SAKISU_ENTRIES)),
        token_factory=lambda: "8" * 32,
    )
    results = []
    errors = []
    logs = []
    worker = SakisuPatchWorker(flow, request)
    worker.result_ready.connect(results.append)
    worker.finished_error.connect(errors.append)
    worker.log_message.connect(logs.append)

    worker.run()

    assert errors == []
    assert len(results) == 1
    assert results[0].candidate.path == request.candidate
    assert results[0].receipt.kmi == "android14-6.1"
    assert logs == ["SakiSU 本地修补开始", "SakiSU 本地修补完成"]


def test_typed_worker_cancelled_before_start_creates_no_outputs_or_adb_work(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.gui.sakisu_worker import SakisuPatchWorker
    from nebumtk_unlock_tools.sakisu import SakisuPatchFlow, SakisuPatchRequest

    source = _write(tmp_path / "selected-boot.img", _boot(STOCK_ENTRIES))
    request = SakisuPatchRequest(
        source=snapshot_regular_file(source),
        partition="boot",
        kmi="android14-6.1",
        wrong_kmi_boot_risk_confirmed=True,
        replace_non_stock_confirmed=False,
        rollback=tmp_path / "exports" / "boot.rollback.img",
        candidate=tmp_path / "exports" / "boot.sakisu.img",
        receipt=tmp_path / "exports" / "boot.sakisu.json",
    )
    adb = FakeAdb(_boot(SAKISU_ENTRIES))
    worker = SakisuPatchWorker(
        SakisuPatchFlow(PROJECT_ROOT, tmp_path / "workspace", adb),
        request,
    )
    cancellations = []
    worker.cancelled_signal.connect(lambda: cancellations.append(True))

    worker.cancel()
    worker.run()

    assert cancellations == [True]
    assert adb.events == []
    assert not request.rollback.exists()
    assert not request.candidate.exists()
    assert not request.receipt.exists()


def test_typed_flow_deletes_candidate_if_receipt_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebumtk_unlock_tools.sakisu import SakisuPatchFlow, SakisuPatchRequest
    from nebumtk_unlock_tools.sakisu import flow as flow_module

    source = _write(tmp_path / "selected-boot.img", _boot(STOCK_ENTRIES))
    request = SakisuPatchRequest(
        source=snapshot_regular_file(source),
        partition="boot",
        kmi="android14-6.1",
        wrong_kmi_boot_risk_confirmed=True,
        replace_non_stock_confirmed=False,
        rollback=tmp_path / "exports" / "boot.rollback.img",
        candidate=tmp_path / "exports" / "boot.sakisu.img",
        receipt=tmp_path / "exports" / "boot.sakisu.json",
    )
    monkeypatch.setattr(
        flow_module,
        "write_patch_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt disk full")),
    )

    with pytest.raises(OSError, match="receipt disk full"):
        SakisuPatchFlow(
            PROJECT_ROOT,
            tmp_path / "workspace",
            FakeAdb(_boot(SAKISU_ENTRIES)),
            token_factory=lambda: "7" * 32,
        ).run(request, cancelled=lambda: False)

    assert request.rollback.exists()
    assert not request.candidate.exists()
    assert not request.receipt.exists()


def test_typed_flow_cancel_after_candidate_publish_deletes_unaudited_candidate(
    tmp_path: Path,
) -> None:
    from nebumtk_unlock_tools.sakisu import SakisuPatchFlow, SakisuPatchRequest

    source = _write(tmp_path / "selected-boot.img", _boot(STOCK_ENTRIES))
    request = SakisuPatchRequest(
        source=snapshot_regular_file(source),
        partition="boot",
        kmi="android14-6.1",
        wrong_kmi_boot_risk_confirmed=True,
        replace_non_stock_confirmed=False,
        rollback=tmp_path / "exports" / "boot.rollback.img",
        candidate=tmp_path / "exports" / "boot.sakisu.img",
        receipt=tmp_path / "exports" / "boot.sakisu.json",
    )

    with pytest.raises(FlowCancelledError):
        SakisuPatchFlow(
            PROJECT_ROOT,
            tmp_path / "workspace",
            FakeAdb(_boot(SAKISU_ENTRIES)),
            token_factory=lambda: "6" * 32,
        ).run(request, cancelled=lambda: request.candidate.exists())

    assert request.rollback.exists()
    assert not request.candidate.exists()
    assert not request.receipt.exists()
def test_sakisu_request_requires_confirmed_source_snapshot(tmp_path: Path) -> None:
    from nebumtk_unlock_tools.core.operation_files import snapshot_regular_file
    from nebumtk_unlock_tools.sakisu import SakisuPatchRequest

    source = _write(tmp_path / "selected-boot.img", _boot(STOCK_ENTRIES))
    kwargs = dict(
        partition="boot",
        kmi="android14-6.1",
        wrong_kmi_boot_risk_confirmed=True,
        replace_non_stock_confirmed=False,
        rollback=tmp_path / "rollback.img",
        candidate=tmp_path / "candidate.img",
        receipt=tmp_path / "receipt.json",
    )
    with pytest.raises(TypeError, match="snapshot"):
        SakisuPatchRequest(source=source, **kwargs)

    assert SakisuPatchRequest(
        source=snapshot_regular_file(source), **kwargs
    ).source.path == source
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
