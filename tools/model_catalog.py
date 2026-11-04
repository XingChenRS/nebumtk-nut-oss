# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Strict maintainer-side five-table model catalog compiler.

The compiler is deliberately build-time only.  It expands reviewed references
into the legacy ``devices.yaml`` runtime shape; the client never evaluates
inheritance or loose catalog source at runtime.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
from typing import Any

import yaml

from nebumtk_unlock_tools.core.device import PrivilegeRuntime
from nebumtk_unlock_tools.core.errors import DeviceMatchError


class CatalogError(ValueError):
    """A maintainer catalog violates its reviewed schema."""


TABLES = frozenset({"platforms", "payloads", "runtimes", "workflows", "devices"})
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
_PARTITION = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_COMMAND_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_CAPABILITIES = frozenset({"preload", "xingc", "generic_lk", "flashing_unlock"})
_RUNTIME_FIELDS = frozenset(
    {
        "preload",
        "preload_remote",
        "preload_mode",
        "preload_trigger",
        "preload_args",
        "preload_env",
        "preload_clean_env",
        "su_path",
        "su_strategy",
        # Reviewed legacy metadata.  These are identifiers/flags only; no
        # resources, commands or secrets are accepted in the maintainer YAML.
        "xingc_patch_profile",
        "fastboot_artifact",
        "misc_brand",
        "unlock_via_xingc",
    }
)
_SHELL_METACHARACTERS = frozenset("|&;<>()$`")
_POST_UNLOCK_FIELDS = frozenset({"reboot", "wait_for_device"})
_UNLOCK_FIELDS = frozenset({"method"})
_PAYLOAD_FIELDS = frozenset(
    {"platform", "resource", "source", "size", "sha256", "elf", "protection"}
)
_PAYLOAD_PROTECTION = "online-device-grant-v1"
_PAYLOAD_ELF = "ELF64-AARCH64"


class _NoDuplicateLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _NoDuplicateLoader, node: yaml.MappingNode, deep: bool = False):
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise CatalogError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_NoDuplicateLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping
)


def load_catalog(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_NoDuplicateLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise CatalogError("catalog YAML is invalid") from exc
    if not isinstance(raw, dict):
        raise CatalogError("catalog root must be a mapping")
    return raw


def _strict_mapping(value: Any, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CatalogError(f"{name} must be a mapping")
    unknown = set(value) - fields
    if unknown:
        raise CatalogError(f"{name} has unknown fields: {', '.join(sorted(map(str, unknown)))}")
    return value


def _table(raw: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    value = raw.get(name)
    if not isinstance(value, dict) or not value:
        raise CatalogError(f"{name} must be a non-empty mapping")
    result: dict[str, dict[str, Any]] = {}
    for raw_id, record in value.items():
        item_id = str(raw_id).strip()
        if not _ID.fullmatch(item_id) or item_id in result:
            raise CatalogError(f"invalid {name} id: {raw_id}")
        if not isinstance(record, dict):
            raise CatalogError(f"{name}.{item_id} must be a mapping")
        result[item_id] = record
    return result


def _reference(table: dict[str, Any], name: str, owner: str) -> str:
    ref = str(name).strip()
    if ref not in table:
        raise CatalogError(f"{owner} references unknown {ref}")
    return ref


def _unsafe_runtime_token(value: str) -> bool:
    return any(
        character in _SHELL_METACHARACTERS
        or ord(character) < 32
        or ord(character) == 127
        for character in value
    )


def _validate_runtime(brand: str, resources: dict[str, Any], owner: str) -> None:
    unknown = set(resources) - _RUNTIME_FIELDS
    if unknown:
        raise CatalogError(
            f"unknown runtime resource {owner}: {', '.join(sorted(unknown))}"
        )
    for field in ("preload", "preload_remote", "preload_mode", "preload_trigger", "su_path", "su_strategy"):
        if field in resources and not isinstance(resources[field], str):
            raise CatalogError(f"invalid runtime resource: {owner}")
    for field in ("preload_remote", "su_path"):
        if field in resources and _unsafe_runtime_token(resources[field]):
            raise CatalogError(f"unsafe runtime token: {owner}")
    if "preload_clean_env" in resources and type(resources["preload_clean_env"]) is not bool:
        raise CatalogError(f"invalid runtime resource: {owner}")
    if "preload_args" in resources and (
        not isinstance(resources["preload_args"], list)
        or any(not isinstance(arg, str) for arg in resources["preload_args"])
    ):
        raise CatalogError(f"invalid runtime resource: {owner}")
    if "preload_args" in resources and any(
        _unsafe_runtime_token(arg) for arg in resources["preload_args"]
    ):
        raise CatalogError(f"unsafe runtime token: {owner}")
    if "preload_env" in resources:
        environment = resources["preload_env"]
        if not isinstance(environment, dict) or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in environment.items()
        ):
            raise CatalogError(f"invalid runtime resource: {owner}")
        if any(_unsafe_runtime_token(value) for value in environment.values()):
            raise CatalogError(f"unsafe runtime token: {owner}")
    for field in ("xingc_patch_profile", "fastboot_artifact", "misc_brand"):
        if field in resources and (
            not isinstance(resources[field], str)
            or not _COMMAND_ID.fullmatch(resources[field])
        ):
            raise CatalogError(f"invalid runtime resource: {owner}")
    if "unlock_via_xingc" in resources and type(resources["unlock_via_xingc"]) is not bool:
        raise CatalogError(f"invalid runtime resource: {owner}")
    trigger = resources.get("preload_trigger")
    if isinstance(trigger, str) and _unsafe_runtime_token(trigger):
        raise CatalogError(f"unsafe preload trigger: {owner}")
    try:
        PrivilegeRuntime.from_resources(brand, resources)
    except DeviceMatchError as exc:
        raise CatalogError(f"invalid runtime {owner}: {exc}") from exc


def _validate_workflow(workflow: dict[str, Any], owner: str) -> None:
    partitions = workflow.get("partitions", {})
    if not isinstance(partitions, dict):
        raise CatalogError(f"invalid partition mappings: {owner}")
    allowed = {"backup", "slot_suffix", "names"}
    if set(partitions) - allowed:
        raise CatalogError(f"invalid partition mappings: {owner}")
    backup = partitions.get("backup")
    names = partitions.get("names")
    if not isinstance(backup, list) or not backup or not isinstance(names, dict):
        raise CatalogError(f"invalid partition mappings: {owner}")
    labels: set[str] = set()
    for partition in backup:
        if not isinstance(partition, str) or not _PARTITION.fullmatch(partition):
            raise CatalogError(f"invalid partition mappings: {owner}")
        if partition in labels:
            raise CatalogError(f"invalid partition mappings: {owner}")
        labels.add(partition)
    if set(names) != labels:
        raise CatalogError(f"invalid partition mappings: {owner}")
    for partition, mapped in names.items():
        if not isinstance(partition, str) or not _PARTITION.fullmatch(partition):
            raise CatalogError(f"invalid partition mappings: {owner}")
        if not isinstance(mapped, str) or not _PARTITION.fullmatch(mapped):
            raise CatalogError(f"invalid partition mappings: {owner}")
    if len(set(names.values())) != len(names):
        raise CatalogError(f"invalid partition mappings: {owner}")
    if type(partitions.get("slot_suffix")) is not bool:
        raise CatalogError(f"invalid partition mappings: {owner}")


def _validate_workflow_transitions(workflow: dict[str, Any], owner: str) -> None:
    post_unlock = workflow.get("post_unlock", {})
    if not isinstance(post_unlock, dict) or set(post_unlock) - _POST_UNLOCK_FIELDS:
        raise CatalogError(f"invalid workflow transitions: {owner}")
    if any(type(value) is not bool for value in post_unlock.values()):
        raise CatalogError(f"invalid workflow transitions: {owner}")
    unlock = workflow.get("unlock", {})
    if not isinstance(unlock, dict) or set(unlock) - _UNLOCK_FIELDS:
        raise CatalogError(f"invalid workflow transitions: {owner}")
    if any(
        not isinstance(value, str) or not _COMMAND_ID.fullmatch(value)
        for value in unlock.values()
    ):
        raise CatalogError(f"invalid workflow transitions: {owner}")
    commands = workflow.get("protection_disable_cmds", [])
    if not isinstance(commands, list) or any(
        not isinstance(command, str) or not _COMMAND_ID.fullmatch(command)
        for command in commands
    ):
        raise CatalogError(f"invalid workflow transitions: {owner}")


def _validate_payload_binding(
    payload_id: str,
    record: dict[str, Any],
    *,
    source_root: Path | None,
) -> dict[str, Any]:
    """Validate the reviewed source identity before it can enter generation."""
    required = {"source", "size", "sha256", "elf", "protection"}
    if not required.issubset(record):
        raise CatalogError(f"missing payload binding: {payload_id}")
    source = record["source"]
    size = record["size"]
    digest = record["sha256"]
    if (
        not isinstance(source, str)
        or source != f"preloads/{payload_id}/preload.so"
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or record["elf"] != _PAYLOAD_ELF
        or record["protection"] != _PAYLOAD_PROTECTION
    ):
        raise CatalogError(f"invalid payload binding: {payload_id}")
    binding = {
        "source": source,
        "size": size,
        "sha256": digest,
        "elf": _PAYLOAD_ELF,
        "protection": _PAYLOAD_PROTECTION,
    }
    if source_root is None:
        return binding
    path = (source_root / source).resolve()
    if source_root.resolve() not in path.parents or not path.is_file():
        raise CatalogError(f"payload source is missing: {payload_id}")
    data = path.read_bytes()
    if len(data) != size:
        raise CatalogError(f"payload size mismatch: {payload_id}")
    if hashlib.sha256(data).hexdigest() != digest:
        raise CatalogError(f"payload hash mismatch: {payload_id}")
    if len(data) < 20 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise CatalogError(f"payload ELF identity mismatch: {payload_id}")
    if int.from_bytes(data[18:20], "little") != 183:
        raise CatalogError(f"payload ELF identity mismatch: {payload_id}")
    return binding


def compile_catalog(
    source: Path | dict[str, Any], *, source_root: Path | None = None
) -> dict[str, Any]:
    if isinstance(source, Path):
        raw = load_catalog(source)
        source_root = source_root or source.resolve().parent
    else:
        raw = deepcopy(source)
    if set(raw) != TABLES:
        raise CatalogError("catalog must contain exactly the five reviewed tables")
    platforms = _table(raw, "platforms")
    payloads = _table(raw, "payloads")
    runtimes = _table(raw, "runtimes")
    workflows = _table(raw, "workflows")
    devices = _table(raw, "devices")

    for platform_id, record in platforms.items():
        _strict_mapping(record, {"brand"}, f"platforms.{platform_id}")
        brand = record.get("brand")
        if not isinstance(brand, str) or not brand.strip():
            raise CatalogError(f"invalid platform: {platform_id}")

    compiled_payloads: dict[str, dict[str, Any]] = {}
    for payload_id, record in payloads.items():
        _strict_mapping(record, _PAYLOAD_FIELDS, f"payloads.{payload_id}")
        _reference(platforms, record.get("platform", ""), f"payloads.{payload_id}")
        resource = record.get("resource")
        if not isinstance(resource, str) or resource != f"builtin:{payload_id}":
            raise CatalogError(f"invalid payload resource: {payload_id}")
        compiled_payloads[payload_id] = _validate_payload_binding(
            payload_id, record, source_root=source_root
        )
        compiled_payloads[payload_id]["platform"] = record["platform"]
        compiled_payloads[payload_id]["resource"] = resource

    for runtime_id, record in runtimes.items():
        _strict_mapping(record, {"platform", "resources"}, f"runtimes.{runtime_id}")
        platform_id = _reference(platforms, record.get("platform", ""), f"runtimes.{runtime_id}")
        resources = record.get("resources")
        if not isinstance(resources, dict):
            raise CatalogError(f"invalid runtime: {runtime_id}")
        _validate_runtime(str(platforms[platform_id]["brand"]), resources, runtime_id)

    for workflow_id, record in workflows.items():
        _strict_mapping(record, {"partitions", "post_unlock", "unlock", "protection_disable_cmds"}, f"workflows.{workflow_id}")
        _validate_workflow(record, workflow_id)
        _validate_workflow_transitions(record, workflow_id)

    compiled: dict[str, dict[str, Any]] = {}
    aliases: list[tuple[str, str, str]] = []
    for device_id, record in devices.items():
        _strict_mapping(record, {"platform", "payload", "runtime", "workflow", "display_name", "match", "capabilities"}, f"devices.{device_id}")
        platform_id = _reference(platforms, record.get("platform", ""), f"devices.{device_id}")
        payload_id = _reference(payloads, record.get("payload", ""), f"devices.{device_id}")
        runtime_id = _reference(runtimes, record.get("runtime", ""), f"devices.{device_id}")
        workflow_id = _reference(workflows, record.get("workflow", ""), f"devices.{device_id}")
        if payloads[payload_id]["platform"] != platform_id or runtimes[runtime_id]["platform"] != platform_id:
            raise CatalogError(f"platform mismatch: {device_id}")
        match = record.get("match")
        if not isinstance(match, dict) or set(match) - {"brand", "model", "firmware"}:
            raise CatalogError(f"invalid match: {device_id}")
        if not isinstance(match.get("brand"), str) or (
            match["brand"].strip().casefold()
            != str(platforms[platform_id]["brand"]).strip().casefold()
        ):
            raise CatalogError(f"platform mismatch: {device_id}")
        if "firmware" in match and not isinstance(match["firmware"], str):
            raise CatalogError(f"invalid match: {device_id}")
        models = match.get("model")
        if not isinstance(models, list) or not models:
            raise CatalogError(f"invalid match: {device_id}")
        for raw_alias in models:
            if not isinstance(raw_alias, str):
                raise CatalogError(f"invalid match: {device_id}")
            alias = raw_alias.strip().casefold()
            brand = match["brand"].strip().casefold()
            if not alias:
                raise CatalogError(f"invalid match: {device_id}")
            for prior_brand, prior_alias, prior_owner in aliases:
                if alias == prior_alias and (
                    brand in prior_brand or prior_brand in brand
                ):
                    if brand == prior_brand:
                        raise CatalogError(
                            f"duplicate exact alias: {brand}/{alias} "
                            f"({prior_owner}, {device_id})"
                        )
                    raise CatalogError(
                        "ambiguous alias overlap: "
                        f"{prior_owner} ({prior_brand}/{prior_alias}), "
                        f"{device_id} ({brand}/{alias})"
                    )
            aliases.append((brand, alias, device_id))
        capabilities = record.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities or len(set(capabilities)) != len(capabilities):
            raise CatalogError(f"invalid capabilities: {device_id}")
        if set(capabilities) - _CAPABILITIES or "preload" not in capabilities:
            raise CatalogError(f"invalid capabilities: {device_id}")
        resources = deepcopy(runtimes[runtime_id]["resources"])
        if resources.get("preload") != payloads[payload_id]["resource"]:
            raise CatalogError(f"payload/runtime mismatch: {device_id}")
        workflow = workflows[workflow_id]
        compiled[device_id] = {
            "brand": platforms[platform_id]["brand"],
            "display_name": record.get("display_name", device_id),
            "match": deepcopy(match),
            "resources": resources,
            "partitions": deepcopy(workflow.get("partitions", {})),
            "post_unlock": deepcopy(workflow.get("post_unlock", {})),
            "capabilities": list(capabilities),
        }
        if "unlock" in workflow:
            compiled[device_id]["unlock"] = deepcopy(workflow["unlock"])
        if "protection_disable_cmds" in workflow:
            compiled[device_id]["protection_disable_cmds"] = deepcopy(workflow["protection_disable_cmds"])
    return {"devices": compiled, "payloads": compiled_payloads}


def compile_catalog_file(source: Path, output: Path) -> None:
    """Emit the legacy runtime shape deterministically from maintainer source."""
    output.write_text(
        yaml.safe_dump(compile_catalog(source), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
