# SPDX-License-Identifier: AGPL-3.0-only
"""One-shot deterministic migration from reviewed legacy profile files.

This is kept as maintenance evidence for the initial five-table catalog; it is
not used by builds.  Subsequent edits are made directly to model_catalog.yaml.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


PROMOTED = (
    "vivopd2241", "honormagic8proair", "vivopd2545", "vivopd2564", "iqooz8", "iqoopad"
)
IQOO = frozenset({"iqooz8", "iqoopad"})


def _identity(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _intern(table: dict[str, dict[str, Any]], prefix: str, value: dict[str, Any]) -> str:
    marker = _identity(value)
    for item_id, item in table.items():
        if _identity(item) == marker:
            return item_id
    item_id = f"{prefix}-{len(table) + 1}"
    table[item_id] = value
    return item_id


def migrate(online: Path, offline: Path) -> dict[str, Any]:
    online_devices = yaml.safe_load(online.read_text(encoding="utf-8"))["devices"]
    offline_devices = yaml.safe_load(offline.read_text(encoding="utf-8"))["devices"]
    source = dict(online_devices)
    for profile_id in PROMOTED:
        source[profile_id] = offline_devices[profile_id]
    # The reviewed PD2564 record has a historical indentation defect.  Correct
    # it while converting the source, rather than preserving an untyped alias
    # outside ``partitions.names`` in the new catalog.
    pd2564 = source["vivopd2564"]
    pd2564_partitions = pd2564["partitions"]
    pd2564_vendor_boot = pd2564_partitions.pop("vendor_boot", None)
    if pd2564_vendor_boot is not None:
        pd2564_partitions.setdefault("names", {})["vendor_boot"] = pd2564_vendor_boot
    platforms: dict[str, dict[str, Any]] = {}
    payloads: dict[str, dict[str, Any]] = {}
    runtimes: dict[str, dict[str, Any]] = {}
    workflows: dict[str, dict[str, Any]] = {}
    devices: dict[str, dict[str, Any]] = {}
    for profile_id, record in source.items():
        platform_id = str(record["brand"]).casefold()
        platforms.setdefault(platform_id, {"brand": record["brand"]})
        resources = dict(record.get("resources", {}))
        payload_id = str(resources.get("preload", "")).split(":", 1)[1]
        payloads.setdefault(payload_id, {"platform": platform_id, "resource": f"builtin:{payload_id}"})
        runtime_id = _intern(runtimes, "runtime", {"platform": platform_id, "resources": resources})
        workflow = {
            "partitions": dict(record.get("partitions", {})),
            "post_unlock": dict(record.get("post_unlock", {})),
        }
        if record.get("unlock"):
            workflow["unlock"] = dict(record["unlock"])
        if record.get("protection_disable_cmds"):
            workflow["protection_disable_cmds"] = list(record["protection_disable_cmds"])
        workflow_id = _intern(workflows, "workflow", workflow)
        if profile_id in IQOO:
            capabilities = ["preload"]
        elif profile_id == "honormagic8proair":
            capabilities = ["preload", "generic_lk"]
        elif profile_id in PROMOTED:
            capabilities = ["preload", "xingc"]
        else:
            capabilities = ["preload", "xingc"] if platform_id == "vivo" else ["preload"]
        devices[profile_id] = {
            "platform": platform_id,
            "payload": payload_id,
            "runtime": runtime_id,
            "workflow": workflow_id,
            "display_name": record.get("display_name", profile_id),
            "match": dict(record["match"]),
            "capabilities": capabilities,
        }
    return {"platforms": platforms, "payloads": payloads, "runtimes": runtimes, "workflows": workflows, "devices": devices}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("online", type=Path)
    parser.add_argument("offline", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(yaml.safe_dump(migrate(args.online, args.offline), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
