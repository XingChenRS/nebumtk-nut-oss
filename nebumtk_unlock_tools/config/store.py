"""Persistent user settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def _default_settings_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "NEBUMTK"
    return Path.home() / ".nebumtk"


@dataclass
class Settings:
    platform_tools_path: str = ""
    test_mode_enabled: bool = False
    custom_su: str = "su"

    def resolved_adb(self) -> str:
        from ..core.platform_tools import resolve_platform_tools

        return resolve_platform_tools(
            self.platform_tools_path, need_adb=True, need_fastboot=False
        ).adb

    def try_resolved_adb(self) -> str | None:
        try:
            return self.resolved_adb()
        except Exception:
            return None


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (_default_settings_dir() / "settings.json")

    def load(self) -> Settings:
        from ..core.platform_tools import migrate_platform_tools_path

        if not self.path.exists():
            return Settings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Settings()

        raw_test_mode = data.get("test_mode_enabled", False)
        test_mode_enabled = (
            raw_test_mode if isinstance(raw_test_mode, bool) else False
        )
        from ..core.su import validate_custom_su

        raw_custom_su = data.get("custom_su", "su")
        try:
            custom_su = validate_custom_su(raw_custom_su)
        except ValueError:
            custom_su = "su"
        return Settings(
            platform_tools_path=migrate_platform_tools_path(data),
            test_mode_enabled=test_mode_enabled,
            custom_su=custom_su,
        )

    def save(self, settings: Settings) -> None:
        from ..core.su import validate_custom_su

        validate_custom_su(settings.custom_su)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)
