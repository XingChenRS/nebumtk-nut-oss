# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import tomllib
from pathlib import Path

import nebumtk_unlock_tools
from nebumtk_unlock_tools.build_profile import CLIENT_VERSION


ROOT = Path(__file__).resolve().parent.parent


def test_release_version_is_consistent_at_200() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert nebumtk_unlock_tools.__version__ == "2.0.0"
    assert CLIENT_VERSION == "2.0.0"
    assert metadata["project"]["version"] == "2.0.0"
    assert metadata["project"]["license"]["text"] == "AGPL-3.0-only"


def test_the_repository_ships_the_agpl_and_nothing_mit() -> None:
    """Strong copyleft is the contract; a stray MIT header is a defect."""

    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 19 November 2007" in license_text

    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
    assert "AGPL-3.0-only" in notice

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["license"]["text"] == "AGPL-3.0-only"

    first_party = [
        ROOT / "nebumtk_unlock_tools" / "__init__.py",
        ROOT / "nebumtk_unlock_tools" / "app.py",
        ROOT / "tools" / "audit_distribution.py",
        ROOT / "tests" / "test_release_metadata.py",
    ]
    for path in first_party:
        head = path.read_text(encoding="utf-8").splitlines()[:2]
        assert any("SPDX-License-Identifier: AGPL-3.0-only" in line for line in head), path
