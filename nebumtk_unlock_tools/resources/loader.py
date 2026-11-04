"""Resource loading for device profiles."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.device import DeviceProfile
from ..core.errors import ResourceError
from ..keymaterial import MaterialError, material_root
from .artifact_store import materialize_artifact
from .payload_store import materialize_wrapper, unwrap_payload_key

SAKISU_PATCHER_NAMES = {
    "win32": ("official-patcher-windows-x64.exe", "patcher.exe"),
    "linux": ("official-patcher-linux-x64", "patcher.bin"),
    "darwin": ("official-patcher-arm64", "patcher.bin"),
}
SAKISU_DIR = "sakisu"


@dataclass
class ProtectedPreload:
    payload_id: str
    wrapper: Path
    grant_responder: Callable[[str, str], str] | None = None


@dataclass
class DeviceResources:
    preload: ProtectedPreload | None = None


class ResourceLoader:
    MISC_DIRS = {
        "oppo": "misc_oppo_common",
        "vivo": "misc_vivo_common",
        "honor": "misc_honor_common",
    }

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._materialized_wrappers: set[Path] = set()
        self._active_preloads: list[ProtectedPreload] = []
        self._materialized_artifacts: set[Path] = set()
        self._artifact_temp_dirs: set[Path] = set()

    def resolve(self, profile: DeviceProfile) -> DeviceResources:
        preload_raw = profile.privilege_runtime.preload_ref
        return DeviceResources(preload=self.resolve_preload(preload_raw))

    def resolve_preload(self, preload_ref: str) -> ProtectedPreload | None:
        ref = str(preload_ref).strip()
        if not ref:
            return None
        if not ref.startswith("builtin:"):
            raise ResourceError("仅允许使用内置受保护 preload，禁止推送明文 .so")

        preload_id = ref.split(":", 1)[1].strip()
        if not preload_id:
            return None

        from .catalog import (
            materialize_catalog_wrapper,
            unwrap_catalog_payload_key,
        )
        from .offline_grant import OfflineGrantResponder

        wrapper = materialize_catalog_wrapper(preload_id)
        unwrap = unwrap_catalog_payload_key
        if wrapper is None:
            wrapper = materialize_wrapper(preload_id)
            unwrap = unwrap_payload_key
        protected = ProtectedPreload(
            payload_id=preload_id,
            wrapper=wrapper,
            grant_responder=OfflineGrantResponder(
                preload_id,
                unwrap,
            ),
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
        for path in list(self._materialized_artifacts):
            try:
                path.unlink(missing_ok=True)
                if path.parent in self._artifact_temp_dirs:
                    path.parent.rmdir()
            except OSError:
                pass
            finally:
                self._materialized_artifacts.discard(path)
        for directory in list(self._artifact_temp_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
            finally:
                self._artifact_temp_dirs.discard(directory)

    def resolve_fastboot_artifact(self, artifact_id: str) -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix="nebumtk-fastboot-artifact-"))
        destination = temp_dir / "artifact.bin"
        try:
            materialize_artifact(artifact_id, destination, project_root=self.project_root)
            if not destination.is_file() or destination.stat().st_size <= 0:
                raise ResourceError("fastboot artifact 为空")
        except Exception:
            destination.unlink(missing_ok=True)
            try:
                temp_dir.rmdir()
            except OSError:
                pass
            raise
        self._materialized_artifacts.add(destination)
        self._artifact_temp_dirs.add(temp_dir)
        return destination

    def resolve_vivo_wipe_misc(self, target_suffix: str) -> Path:
        from ..core.vivo_wipe_misc import validate_vivo_wipe_misc

        suffix = str(target_suffix).strip().lower()
        artifact_id = {
            "_a": "vivo_wipe_data_to_a",
            "_b": "vivo_wipe_data_to_b",
        }.get(suffix)
        if artifact_id is None:
            raise ResourceError("Vivo wipe misc 目标槽无效")
        path = self.resolve_fastboot_artifact(artifact_id)
        try:
            validate_vivo_wipe_misc(path, suffix)
        except Exception:
            self._materialized_artifacts.discard(path)
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
            raise
        return path

    def materialize_sakisu_patcher(self, destination: Path) -> Path:
        """Copy the operator's SakiSU host patcher next to the operation."""
        import sys

        destination = Path(destination)
        names = SAKISU_PATCHER_NAMES.get(sys.platform, SAKISU_PATCHER_NAMES["linux"])
        roots = [material_root(), self.project_root]
        source: Path | None = None
        for root in roots:
            for name in names:
                candidate = root / SAKISU_DIR / name
                if candidate.is_file():
                    source = candidate
                    break
            if source is not None:
                break
        if source is None:
            raise MaterialError(
                f"缺少 SakiSU host patcher: {material_root() / SAKISU_DIR / names[0]}"
                "（把官方 host patcher 放到 material/sakisu/ 或项目根 sakisu/）"
            )
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if destination.stat().st_size <= 0:
                raise ResourceError("SakiSU host patcher 为空")
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def resolve_misc(self, brand: str, active_suffix: str) -> Path:
        common = self.MISC_DIRS.get(brand.lower())
        if not common:
            raise ResourceError(f"未知品牌 misc: {brand}")
        name = "a_to_b_bootloader.img" if active_suffix == "_a" else "b_to_a_bootloader.img"
        path = self.project_root / "misc" / common / name
        if not path.is_file():
            raise ResourceError(f"misc 资源不存在: {path}")
        return path
