# SPDX-License-Identifier: AGPL-3.0-only
"""Environment validation for adb/fastboot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config.store import Settings
from .errors import EnvError
from .platform_tools import resolve_platform_tools


@dataclass
class ToolPaths:
    adb: str
    fastboot: str


class EnvChecker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(self, *, need_adb: bool = True, need_fastboot: bool = True) -> ToolPaths:
        try:
            tools = resolve_platform_tools(
                self.settings.platform_tools_path,
                need_adb=need_adb,
                need_fastboot=need_fastboot,
            )
        except FileNotFoundError as exc:
            raise EnvError(str(exc)) from exc
        return ToolPaths(adb=tools.adb, fastboot=tools.fastboot)

    def resolve_or_none(self) -> ToolPaths | None:
        try:
            # Refresh may need either tool; require both files when configured as a dir.
            return self.resolve(need_adb=True, need_fastboot=True)
        except EnvError:
            try:
                return self.resolve(need_adb=True, need_fastboot=False)
            except EnvError:
                try:
                    return self.resolve(need_adb=False, need_fastboot=True)
                except EnvError:
                    return None

    def validate(self, *, need_adb: bool = True, need_fastboot: bool = True) -> ToolPaths:
        """Only resolve paths. Do NOT run adb/fastboot (some SDKs hang on version)."""
        paths = self.resolve(need_adb=need_adb, need_fastboot=need_fastboot)
        if need_adb:
            self._ensure_exists(paths.adb, "adb")
        if need_fastboot:
            self._ensure_exists(paths.fastboot, "fastboot")
        return paths

    def validate_adb(self) -> ToolPaths:
        return self.validate(need_adb=True, need_fastboot=False)

    def validate_fastboot(self) -> ToolPaths:
        return self.validate(need_adb=False, need_fastboot=True)

    @staticmethod
    def _ensure_exists(path: str, name: str) -> None:
        if not path:
            raise EnvError(f"{name} 未配置")
        candidate = Path(path)
        if candidate.is_absolute() or "\\" in path or "/" in path:
            if not candidate.is_file():
                raise EnvError(f"{name} 不存在: {path}")
