# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Resolve adb/fastboot from Android SDK platform-tools (single directory)."""

from __future__ import annotations

import hashlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


BUILTIN_PLATFORM_TOOL_SHA256 = {
    "adb.exe": "705ddc21f33ac52105d1b075b019962ad0e44fb3d560bde69ce8cb3a36bca183",
    "fastboot.exe": "377ff87ec40a2a9b947e00b487f4f4a42b03e195612bdcd8a526b70a1b0a918a",
}


@dataclass(frozen=True)
class PlatformTools:
    directory: Path | None
    adb: str
    fastboot: str


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _binary_name(tool: str) -> str:
    return f"{tool}.exe" if _is_windows() else tool


def _candidate_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        roots.append(Path(getattr(sys, "_MEIPASS")))
    here = Path(__file__).resolve()
    roots.extend((here.parents[3], here.parents[2], Path(sys.executable).resolve().parent))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        deduped.append(root)
    return tuple(deduped)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pinned_builtin_is_valid(directory: Path) -> bool:
    if not _is_windows():
        return True
    try:
        return all(
            (path := directory / name).is_file()
            and _sha256_file(path) == expected
            for name, expected in BUILTIN_PLATFORM_TOOL_SHA256.items()
        )
    except OSError:
        return False


def find_builtin_platform_tools(search_roots: tuple[Path, ...] | None = None) -> Path | None:
    adb_name = _binary_name("adb")
    fb_name = _binary_name("fastboot")
    for root in search_roots or _candidate_roots():
        candidate = root / "platform-tools"
        if not candidate.is_dir():
            continue
        if (
            (candidate / adb_name).is_file()
            and (candidate / fb_name).is_file()
            and _pinned_builtin_is_valid(candidate)
        ):
            return candidate
    return None


def migrate_platform_tools_path(data: dict) -> str:
    configured = str(data.get("platform_tools_path", "")).strip()
    if configured:
        return configured
    for key in ("adb_path", "fastboot_path"):
        raw = str(data.get(key, "")).strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.is_dir():
            return str(path)
        if path.is_file():
            return str(path.parent)
        if path.suffix:
            return str(path.parent)
    return ""


def resolve_platform_tools(
    platform_tools_path: str = "",
    *,
    need_adb: bool = True,
    need_fastboot: bool = True,
    builtin_search_roots: tuple[Path, ...] | None = None,
) -> PlatformTools:
    """Resolve tool paths only. Never executes adb/fastboot."""
    configured = platform_tools_path.strip()
    if configured:
        directory = Path(configured).expanduser()
        if not directory.is_dir():
            raise FileNotFoundError(f"platform-tools 目录不存在: {directory}")
        adb_path = directory / _binary_name("adb")
        fb_path = directory / _binary_name("fastboot")
        if need_adb and not adb_path.is_file():
            raise FileNotFoundError(f"未找到 adb: {adb_path}")
        if need_fastboot and not fb_path.is_file():
            raise FileNotFoundError(f"未找到 fastboot: {fb_path}")
        return PlatformTools(
            directory=directory,
            adb=str(adb_path) if adb_path.is_file() else "",
            fastboot=str(fb_path) if fb_path.is_file() else "",
        )

    builtin = find_builtin_platform_tools(builtin_search_roots)
    if builtin is not None:
        adb_path = builtin / _binary_name("adb")
        fb_path = builtin / _binary_name("fastboot")
        return PlatformTools(
            directory=builtin,
            adb=str(adb_path) if adb_path.is_file() else "",
            fastboot=str(fb_path) if fb_path.is_file() else "",
        )

    adb = shutil.which("adb") or ""
    fastboot = shutil.which("fastboot") or ""
    missing = []
    if need_adb and not adb:
        missing.append("adb")
    if need_fastboot and not fastboot:
        missing.append("fastboot")
    if missing:
        raise FileNotFoundError(
            "未找到可用 platform-tools（内置目录和 PATH 都缺少: "
            + ", ".join(missing)
            + "）。请在设置中指定 platform-tools 目录"
        )
    directory = Path(adb).parent if adb else (Path(fastboot).parent if fastboot else None)
    return PlatformTools(directory=directory, adb=adb, fastboot=fastboot)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
