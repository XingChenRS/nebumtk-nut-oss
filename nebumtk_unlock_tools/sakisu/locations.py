# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Where the reviewed SakiSU upstream assets live.

SakiSU is a separate project (https://github.com/XingChenRS/SakiSU); its
binaries are not redistributed in this repository. The operator fetches the
pinned release into ``material/sakisu/`` with ``tools/fetch_sakisu.py``, and the
loaders here read that directory. A bare checkout therefore fails closed with a
message naming both the expected path and the upstream tag.

Resolution order (first directory that actually holds the required asset wins):

1. ``NUT_SAKISU_HOME``;
2. ``<project root>/sakisu`` (a packaged build copies the store next to the exe);
3. ``<material root>/sakisu`` (source-tree development store).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..keymaterial import material_root

SAKISU_DIR = "sakisu"
SAKISU_HOME_ENV = "NUT_SAKISU_HOME"
UPSTREAM_REPOSITORY = "XingChenRS/SakiSU"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPOSITORY}"
UPSTREAM_TAG = "v4.3.0-sakisu.1"
UPSTREAM_RELEASE_URL = f"{UPSTREAM_URL}/releases/tag/{UPSTREAM_TAG}"

ASSET_ARM64 = "official-patcher-arm64"
ASSET_ARM64_MANIFEST = "official-patcher-manifest.json"
ASSET_HOST = "official-patcher-windows-x64.exe"
ASSET_HOST_MANIFEST = "official-patcher-windows-x64-manifest.json"
ASSET_NAMES = (ASSET_ARM64, ASSET_ARM64_MANIFEST, ASSET_HOST, ASSET_HOST_MANIFEST)


def candidate_dirs(project_root: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get(SAKISU_HOME_ENV, "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    if project_root is not None:
        candidates.append(Path(project_root) / SAKISU_DIR)
    candidates.append(material_root() / SAKISU_DIR)
    return candidates


def sakisu_dir(project_root: Path | None = None, *, needs: str | None = None) -> Path:
    """Directory that holds the requested asset, or the best guess for an error."""
    candidates = candidate_dirs(project_root)
    if needs is not None:
        for candidate in candidates:
            if (candidate / needs).is_file():
                return candidate
    for candidate in candidates:
        if candidate.is_dir() and any(
            (candidate / name).is_file() for name in ASSET_NAMES
        ):
            return candidate
    return candidates[-1]


def missing_assets_message(expected: Path) -> str:
    return (
        f"缺少 SakiSU 上游产物 {expected}。SakiSU 是本项目的上游依赖，不随本仓库分发。"
        f"见 {UPSTREAM_URL}（{UPSTREAM_TAG}），"
        f"或用 `python tools/fetch_sakisu.py` 拉取到 {material_root() / SAKISU_DIR}。"
    )
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
