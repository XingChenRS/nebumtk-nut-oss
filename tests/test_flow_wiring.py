# SPDX-License-Identifier: AGPL-3.0-only
"""UI-to-backend flow wiring contract.

A UI action must never name a flow kind the factory cannot build.  This guard
exists because an earlier merge shipped a UI that emitted seven kinds the flow
factory did not handle: every affected button raised ``未知 flow_kind`` only
after the operator had already picked a device and confirmed a destructive
step.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "nebumtk_unlock_tools" / "gui" / "main_window.py"
FACTORY = ROOT / "nebumtk_unlock_tools" / "flows" / "factory.py"

_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{3,}$")


def _factory_handled_kinds() -> set[str]:
    """Every flow kind ``create_flow`` can dispatch."""

    source = FACTORY.read_text(encoding="utf-8")
    handled = set(re.findall(r'flow_kind == "([a-z0-9_]+)"', source))
    for group in re.findall(r'flow_kind in \{([^}]*)\}', source):
        handled.update(re.findall(r'"([a-z0-9_]+)"', group))
    return handled


def _ui_requested_kinds() -> set[str]:
    """Flow kinds the shell can actually ask for.

    Collected from two shapes: string literals passed to ``_start_flow`` and
    the ``flow_kind`` argument of a ``DestructiveFlowRequest``.
    """

    source = MAIN_WINDOW.read_text(encoding="utf-8")
    tree = ast.parse(source)

    collected: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", getattr(func, "id", ""))
        if name == "DestructiveFlowRequest":
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    collected.add(value)
        elif name == "_start_flow":
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    collected.add(value)

    # Kinds the shell resolves from a variable branch rather than a literal.
    for literal in re.findall(r'"([a-z][a-z0-9_]+)"', source):
        collected.add(literal)
    return {k for k in collected if _KIND_RE.match(k)}


# The complete set of flow kinds the operator can reach from the five pages.
# Update this list deliberately when a page gains or loses an action.
UI_REACHABLE_FLOW_KINDS = frozenset(
    {
        # device page
        "root_escalation",
        # partitions page
        "backup_export",
        "write_inactive",
        # root and unlock page
        "vivo_complete_unlock",
        "oppo_unlock",
        "honor_prepare",
        "honor_complete",
        "vivo_xingc_unlock",
        "vivo_xingc_lock",
        "dual_slot_sync",
        "dual_slot_apply",
        "restore_original_lk",
        # images page
        "patch_lk",
    }
)


def test_every_ui_reachable_kind_is_handled_by_the_factory() -> None:
    handled = _factory_handled_kinds()
    missing = sorted(UI_REACHABLE_FLOW_KINDS - handled)
    assert not missing, (
        "these flow kinds are reachable from the UI but the factory cannot "
        f"build them: {missing}"
    )


def test_factory_does_not_handle_kinds_the_ui_cannot_reach() -> None:
    """Unreachable kinds are how the retired legacy paths lingered."""

    handled = _factory_handled_kinds()
    orphaned = sorted(handled - UI_REACHABLE_FLOW_KINDS)
    assert not orphaned, (
        "the factory still dispatches kinds no page can request; remove them "
        f"or add the entry point: {orphaned}"
    )


def test_retired_legacy_kinds_are_gone() -> None:
    handled = _factory_handled_kinds()
    for retired in ("patch_init_boot", "restore_vivo_dyn"):
        assert retired not in handled, (
            f"{retired} belongs to the retired implementation and must not "
            "be dispatchable"
        )


@pytest.mark.parametrize("kind", sorted(UI_REACHABLE_FLOW_KINDS))
def test_each_ui_kind_constructs_or_raises_a_guard_error(kind: str) -> None:
    """Building a flow either succeeds or fails on an explicit input guard.

    A ``未知 flow_kind`` error means the wiring is broken; any other failure is
    a missing-input guard, which is the caller's responsibility to satisfy.
    """

    from nebumtk_unlock_tools.config.store import Settings
    from nebumtk_unlock_tools.flows.factory import create_flow

    tmp = ROOT / ".test-workspace"
    image = ROOT / "pyproject.toml"  # any existing path stands in for an LK
    try:
        create_flow(
            kind,
            ROOT,
            tmp,
            Settings(),
            patched_lk=image,
            source_lk=image,
            backup_directory=tmp,
        )
    except ValueError as exc:
        assert "未知 flow_kind" not in str(exc), str(exc)
    except Exception as exc:  # pragma: no cover - construction-side guards
        pytest.fail(f"{kind} raised {type(exc).__name__}: {exc}")


# The window reaches into its pages by attribute. A page rename or a dropped
# widget must fail here rather than at the first click.
PAGE_SOURCES = {
    "partition_page": "nebumtk_unlock_tools/gui/pages/partitions.py",
    "authorized_page": "nebumtk_unlock_tools/gui/pages/root_unlock.py",
    "images_page": "nebumtk_unlock_tools/gui/pages/images_recovery.py",
    "advanced_page": "nebumtk_unlock_tools/gui/pages/tools_settings.py",
    "device_page": "nebumtk_unlock_tools/gui/pages/device.py",
}


def _page_provides(relative: str) -> set[str]:
    import ast

    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    provided: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            provided.add(node.attr)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            provided.add(node.name)
        if isinstance(node, ast.Assign):
            provided.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    return provided


@pytest.mark.parametrize("page_attr", sorted(PAGE_SOURCES))
def test_window_only_reaches_page_attributes_that_exist(page_attr: str) -> None:
    source = MAIN_WINDOW.read_text(encoding="utf-8")
    used = set(re.findall(rf"self\.{page_attr}\.(\w+)", source))
    unresolved = sorted(used - _page_provides(PAGE_SOURCES[page_attr]))
    assert not unresolved, (
        f"main_window reaches {page_attr} attributes that the page does not "
        f"define, so those actions raise AttributeError on first use: {unresolved}"
    )


def test_every_click_target_exists_on_the_window() -> None:
    """A connect() to a typo'd handler is a dead button."""

    import ast

    source = MAIN_WINDOW.read_text(encoding="utf-8")
    defined = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    externals = {
        "open_settings",
        "open_backup_dir",
        "open_logs_dir",
        "open_workspace_dir",
        "refresh_devices",
        "open_platform_tools_terminal",
    }
    referenced = {
        match.group(1).split(".")[-1]
        for match in re.finditer(r"\.clicked\.connect\(\s*(?:lambda[^)]*?)?(self\.\w+|[\w\.]+)", source)
    }
    missing = sorted(referenced - defined - externals)
    assert not missing, f"click targets with no handler: {missing}"
