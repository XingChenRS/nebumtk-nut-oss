# SPDX-License-Identifier: AGPL-3.0-only
"""Where the SakiSU upstream assets live.

SakiSU is a separate project (https://github.com/XingChenRS/SakiSU); its
binaries are not redistributed in this repository. ``tools/fetch_sakisu.py``
puts the pinned release into ``material/sakisu/``, and the resource loader reads
it from there.

Resolution order (first directory holding the required file wins):

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

ASSET_HOST = "official-patcher-windows-x64.exe"
ASSET_HOST_MANIFEST = "official-patcher-windows-x64-manifest.json"


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
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return candidates[-1]


def missing_assets_message(expected: Path) -> str:
    return (
        f"缺少 SakiSU 上游产物 {expected}。SakiSU 是本项目的上游依赖，不随本仓库分发。"
        f"见 {UPSTREAM_URL}（{UPSTREAM_TAG}），"
        f"或用 `python tools/fetch_sakisu.py` 拉取到 {material_root() / SAKISU_DIR}。"
    )
