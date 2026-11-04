# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Capability reachability contract.

A UI action gated on a capability is dead unless some device in the shipped
catalog actually carries that capability.  The online-port merge replaced the
OPPO unlock gate (``brand == "oppo"``, which worked) with
``"flashing_unlock" in capabilities`` — a capability no device record has ever
declared, in this tree or in the online tree.  OPPO unlock, documented as a
shipping feature, became unreachable outside test mode, and its finalization
had no test-mode escape at all.  Nothing failed: the buttons simply stayed
disabled forever.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "nebumtk_unlock_tools" / "gui" / "main_window.py"
DEVICES = ROOT / "devices.yaml"

# ``has_xingc = "xingc" in capabilities`` and ``flashing_unlock = "flashing_unlock" in capabilities``
_GATE_RE = re.compile(r'= "([a-z][a-z0-9_]*)" in capabilities')


def _gated_capabilities() -> set[str]:
    return set(_GATE_RE.findall(MAIN_WINDOW.read_text(encoding="utf-8")))


def _declared_capabilities() -> dict[str, set[str]]:
    devices = yaml.safe_load(DEVICES.read_text(encoding="utf-8"))["devices"]
    return {
        key: set(record.get("capabilities") or [])
        for key, record in devices.items()
    }


def test_every_ui_gated_capability_is_carried_by_some_device() -> None:
    gated = _gated_capabilities()
    assert gated, "no capability gates found in main_window; the pattern changed"

    declared: set[str] = set()
    for capabilities in _declared_capabilities().values():
        declared |= capabilities

    unsatisfiable = sorted(gated - declared)
    assert not unsatisfiable, (
        "these capabilities gate UI actions but no device declares them, so "
        f"the gated buttons can never be enabled: {unsatisfiable}"
    )


def test_oppo_devices_can_reach_the_oppo_unlock_flow() -> None:
    """OPPO unlock and finalization are documented shipping features."""

    devices = _declared_capabilities()
    oppo = {k: v for k, v in devices.items() if k.startswith("oppofindx9")}
    assert oppo, "expected the OPPO device records to exist"

    without = sorted(k for k, v in oppo.items() if "flashing_unlock" not in v)
    assert not without, (
        "OPPO unlock/finalize are gated on `flashing_unlock`; these OPPO records "
        f"lack it and their unlock buttons stay disabled forever: {without}"
    )
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
