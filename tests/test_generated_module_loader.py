from __future__ import annotations

import sys
from types import ModuleType


def test_generated_module_loader_executes_current_source_not_cached_module(tmp_path) -> None:
    from tools.generated_module_loader import load_generated_module

    module_name = "tests._generated_resource_fixture"
    source = tmp_path / "generated.py"
    source.write_text("VALUE = 'current'\n", encoding="utf-8")
    stale = ModuleType(module_name)
    stale.VALUE = "stale"
    sys.modules[module_name] = stale
    try:
        loaded = load_generated_module(module_name, source)
        assert loaded.VALUE == "current"
    finally:
        sys.modules.pop(module_name, None)
