# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Local material lookup for the open-source build.

The closed release derived every key from an operator-typed access password and
kept the ciphertext in generated ``embedded_*`` modules. This build has no
password gate and ships no ciphertext:

* nothing in the repository is secret;
* the operator owns the material root and the payload store inside it;
* a missing file is reported as :class:`MaterialError` naming the exact path, so
  the failure is diagnosable instead of looking like a wrong password.

Effective root resolution order:

1. the explicit ``root=`` argument (tests and tools);
2. ``NUT_MATERIAL_ROOT``;
3. ``<project root>/material``.

``project_root`` is set by :func:`set_project_root` at application start.
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path

MATERIAL_ROOT_ENV = "NUT_MATERIAL_ROOT"
DEFAULT_MATERIAL_DIR = "material"

_PROJECT_ROOT: Path | None = None


class MaterialError(RuntimeError):
    """A required local material file is missing or malformed."""


def set_project_root(root: Path) -> None:
    """Point material lookups at one project tree (used by the GUI and tests)."""
    global _PROJECT_ROOT
    _PROJECT_ROOT = Path(root)


def project_root() -> Path:
    if _PROJECT_ROOT is not None:
        return _PROJECT_ROOT
    from .app import project_root as resolve_project_root

    return resolve_project_root()


def project_material_root(project: Path | None = None) -> Path:
    base = Path(project) if project is not None else project_root()
    return base / DEFAULT_MATERIAL_DIR


def material_root(root: Path | None = None) -> Path:
    """The effective material root: explicit, environment, then project-local."""
    if root is not None:
        return Path(root)
    configured = os.environ.get(MATERIAL_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return project_material_root()


def payload_root(root: Path | None = None) -> Path:
    return material_root(root) / "payloads"


def material_path(relative: str, root: Path | None = None) -> Path:
    return material_root(root) / relative


def read_material(relative: str, *, label: str = "", root: Path | None = None) -> bytes:
    path = material_path(relative, root)
    if not path.is_file():
        raise MaterialError(
            f"缺少本地材料 {label or relative}: {path}"
            f"（把该文件放到 {material_root(root)} 下，或设置 {MATERIAL_ROOT_ENV}）"
        )
    data = path.read_bytes()
    if not data:
        raise MaterialError(f"本地材料为空 {label or relative}: {path}")
    return data


def read_material_text(
    relative: str, *, label: str = "", root: Path | None = None
) -> str:
    raw = read_material(relative, label=label, root=root)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterialError(f"本地材料不是 UTF-8 文本 {label or relative}") from exc


def read_secret_key(relative: str, *, label: str = "", root: Path | None = None) -> bytes:
    """Read a 32-byte symmetric key written as hex, base64 or raw bytes."""
    raw = read_material(relative, label=label, root=root).strip()
    candidates: list[bytes] = []
    try:
        candidates.append(binascii.unhexlify(raw))
    except (binascii.Error, ValueError):
        pass
    try:
        candidates.append(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError):
        pass
    candidates.append(raw)
    for candidate in candidates:
        if len(candidate) == 32:
            return candidate
    raise MaterialError(f"本地主体密钥长度不是 32 字节 {label or relative}")


def wipe(value: bytearray | None) -> None:
    if value is None:
        return
    for index in range(len(value)):
        value[index] = 0
