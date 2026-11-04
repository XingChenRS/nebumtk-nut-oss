# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Load a generated Python resource from its source bytes, never from ``.pyc``."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType


def load_generated_module(module_name: str, source: Path) -> ModuleType:
    """Execute the current generated source and return its module namespace.

    Resource generators may overwrite an embedded module and validate it within
    the same filesystem timestamp tick.  Importlib can then accept a stale
    timestamp-based bytecode cache, so validation must execute the source that
    was just written.
    """

    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    previous = sys.modules.get(module_name)
    module = ModuleType(module_name)
    module.__file__ = str(source)
    module.__package__ = module_name.rpartition(".")[0]
    sys.modules[module_name] = module
    try:
        code = compile(source.read_bytes(), str(source), "exec")
        exec(code, module.__dict__)
    except Exception:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
