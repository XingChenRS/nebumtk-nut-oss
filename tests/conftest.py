# SPDX-License-Identifier: AGPL-3.0-only
"""Keep the suite off the operator's machine state.

Two pieces of real-machine state used to leak into tests:

* ``%APPDATA%/NEBUMTK/settings.json`` — a developer with test mode enabled made
  every MainWindow start in test mode, which popped a modal confirmation and
  hung the run;
* ``material/`` — key and payload material an operator may or may not have.

The fixtures below point both at throwaway directories, so a checkout with no
material and no settings behaves the same everywhere. Individual tests still
build their own material roots explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_settings_dir(tmp_path_factory, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path_factory.mktemp("settings")
    monkeypatch.setenv("NEBUMTK_SETTINGS_DIR", str(directory))
    return directory


@pytest.fixture(autouse=True)
def no_ambient_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to an empty material root; tests opt in with their own path."""
    if os.environ.get("NUT_TEST_KEEP_MATERIAL"):
        return
    monkeypatch.setenv("NUT_MATERIAL_ROOT", str(Path(__file__).parent / ".no-material"))
