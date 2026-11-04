from __future__ import annotations

import tomllib
from pathlib import Path

import nebumtk_unlock_tools
from nebumtk_unlock_tools.build_profile import CLIENT_VERSION


ROOT = Path(__file__).resolve().parent.parent


def test_release_version_is_consistent_at_110() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert nebumtk_unlock_tools.__version__ == "1.1.0"
    assert CLIENT_VERSION == "1.1.0"
    assert metadata["project"]["version"] == "1.1.0"
