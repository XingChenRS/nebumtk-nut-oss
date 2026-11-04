"""The five-table template must reproduce the shipped device catalog.

``model_catalog.yaml`` is the maintainer source for ``devices.yaml``, which is
what actually gets signed into the runtime catalog.  The two had drifted: a
recompile silently rewrote iqooz8/iqoopad's preload trigger command from
``/system/bin/toybox id`` to ``/system/bin/toybox`` and dropped vivopad5's
``ipa2556``/``dpd2437m``/``dpd2437`` model aliases, so those codenames would
stop matching.  Command-bearing and routing fields must never change on a
recompile; this test fails when they would.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tools.model_catalog import compile_catalog

from tests._payload_material import compile_or_skip

ROOT = Path(__file__).resolve().parents[1]
MODEL_CATALOG = ROOT / "model_catalog.yaml"
DEVICES = ROOT / "devices.yaml"

# Fields that decide what command runs on the device or how a device is routed.
ROUTING_FIELDS = ("brand", "display_name", "match", "capabilities")
RESOURCE_FIELDS = (
    "preload",
    "su_path",
    "preload_trigger",
    "preload_mode",
    "su_strategy",
    "xingc_patch_profile",
    "misc_brand",
)


def _resources(record: dict[str, Any]) -> dict[str, Any]:
    """Resource values with the parser's defaults applied.

    ``core/device.py`` falls back to ``/system/bin/true`` for a missing
    ``preload_trigger`` and to ``su`` (or the virt path on Vivo) for a missing
    ``su_path``, so an omitted field and its explicit default are equivalent.
    """

    values = dict(record.get("resources") or {})
    values.setdefault("preload_trigger", "/system/bin/true")
    if not str(values.get("su_path", "")).strip():
        values["su_path"] = (
            "/apex/com.android.virt/bin/su"
            if str(record.get("brand", "")).lower() == "vivo"
            else "su"
        )
    return values


def test_template_reproduces_shipped_device_routing() -> None:
    compiled = compile_or_skip(MODEL_CATALOG)["devices"]
    shipped = yaml.safe_load(DEVICES.read_text(encoding="utf-8"))["devices"]

    assert set(compiled) == set(shipped), (
        "the template and the shipped catalog list different device keys"
    )
    problems = []
    for key in sorted(shipped):
        for field in ROUTING_FIELDS:
            if compiled[key].get(field) != shipped[key].get(field):
                problems.append(
                    f"{key}.{field}: compiled={compiled[key].get(field)!r} "
                    f"shipped={shipped[key].get(field)!r}"
                )
    assert not problems, (
        "recompiling model_catalog.yaml would change how devices are routed:\n"
        + "\n".join(problems)
    )


def test_template_reproduces_shipped_device_commands() -> None:
    compiled = compile_or_skip(MODEL_CATALOG)["devices"]
    shipped = yaml.safe_load(DEVICES.read_text(encoding="utf-8"))["devices"]

    problems = []
    for key in sorted(shipped):
        produced = _resources(compiled[key])
        expected = _resources(shipped[key])
        for field in RESOURCE_FIELDS:
            if produced.get(field) != expected.get(field):
                problems.append(
                    f"{key}.resources.{field}: compiled={produced.get(field)!r} "
                    f"shipped={expected.get(field)!r}"
                )
    assert not problems, (
        "recompiling model_catalog.yaml would change the commands run on a "
        "device:\n" + "\n".join(problems)
    )
