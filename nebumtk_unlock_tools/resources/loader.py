# SPDX-License-Identifier: AGPL-3.0-only
"""Resource loading for device profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.device import DeviceProfile
from ..core.errors import ResourceError


@dataclass
class ProtectedPreload:
    payload_id: str
    wrapper: Path
    model_id: str = ""
    grant_responder: Callable[[str, str], str] | None = None


@dataclass
class DeviceResources:
    preload: ProtectedPreload | None = None


class ResourceLoader:
    MISC_DIRS = {
        "honor": "misc_honor_common",
        "oppo": "misc_oppo_common",
        "vivo": "misc_vivo_common",
    }

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._materialized_wrappers: set[Path] = set()
        self._active_preloads: list[ProtectedPreload] = []

    def resolve(self, profile: DeviceProfile) -> DeviceResources:
        preload_raw = profile.privilege_runtime.preload_ref
        return DeviceResources(
            preload=self.resolve_preload(preload_raw, model_id=profile.key)
        )

    def resolve_preload(
        self,
        preload_ref: str,
        *,
        model_id: str = "",
    ) -> ProtectedPreload | None:
        ref = str(preload_ref).strip()
        if not ref:
            return None
        if not ref.startswith("builtin:"):
            raise ResourceError("仅允许使用内置受保护 preload，禁止推送明文 .so")

        preload_id = ref.split(":", 1)[1].strip()
        if not preload_id:
            return None
        from .offline_loader import resolve_offline_preload

        wrapper, responder = resolve_offline_preload(preload_id)
        protected = ProtectedPreload(
            payload_id=preload_id,
            wrapper=wrapper,
            model_id=model_id,
            grant_responder=responder,
        )
        self._materialized_wrappers.add(wrapper)
        self._active_preloads.append(protected)
        return protected

    def cleanup_materialized_preloads(self) -> None:
        self._active_preloads.clear()

        for path in list(self._materialized_wrappers):
            try:
                if path.is_file():
                    path.unlink()
                parent = path.parent
                while parent.name and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    if parent.name == "protected_preloads":
                        break
                    parent = parent.parent
            except OSError:
                pass
            finally:
                self._materialized_wrappers.discard(path)

    def resolve_misc(self, brand: str, active_suffix: str) -> Path:
        common = self.MISC_DIRS.get(brand.lower())
        if not common:
            raise ResourceError(f"未知品牌 misc: {brand}")
        name = "a_to_b_bootloader.img" if active_suffix == "_a" else "b_to_a_bootloader.img"
        path = self.project_root / "misc" / common / name
        if not path.is_file():
            raise ResourceError(f"misc 资源不存在: {path}")
        return path
