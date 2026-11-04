# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from nebumtk_unlock_tools.core.device import DeviceDetector
from tools.model_catalog import CatalogError, compile_catalog, load_catalog
from tools.migrate_legacy_catalog import migrate

from tests._payload_material import compile_or_skip


ROOT = Path(__file__).resolve().parents[1]


def test_model_catalog_compiles_the_thirteen_legacy_profiles_exactly() -> None:
    legacy = DeviceDetector._load_profiles(ROOT / "tests" / "fixtures" / "legacy_13_devices.yaml")
    compiled = compile_or_skip(ROOT / "model_catalog.yaml")
    profiles = DeviceDetector._profiles_from_mapping(compiled["devices"])

    assert len(legacy) == 13
    for profile_id, legacy_profile in legacy.items():
        actual = profiles[profile_id]
        assert actual.brand == legacy_profile.brand
        assert actual.display_name == legacy_profile.display_name
        # A profile may legitimately gain match aliases from a later feature
        # commit — the vivo Pad 5 codenames (ipa2556/dpd2437m/dpd2437) arrived
        # in 1c216c5 — so require every legacy alias to still match rather than
        # exact equality on the alias list.
        assert set(legacy_profile.match) <= set(actual.match)
        for key, value in legacy_profile.match.items():
            if key == "model":
                assert set(value) <= set(actual.match["model"])
            else:
                assert actual.match[key] == value
        assert actual.resources == legacy_profile.resources
        assert actual.partitions == legacy_profile.partitions
        assert actual.post_unlock == legacy_profile.post_unlock
        assert actual.unlock == legacy_profile.unlock
        assert actual.protection_disable_cmds == legacy_profile.protection_disable_cmds


def test_model_catalog_rejects_unknown_record_fields_and_duplicate_aliases() -> None:
    source = load_catalog(ROOT / "model_catalog.yaml")
    broken = deepcopy(source)
    broken["platforms"]["vivo"]["unexpected"] = True
    with pytest.raises(CatalogError, match="unknown fields"):
        compile_catalog(broken)

    broken = deepcopy(source)
    first, second = list(broken["devices"])[:2]
    broken["devices"][second]["match"] = deepcopy(broken["devices"][first]["match"])
    with pytest.raises(CatalogError, match="duplicate exact alias"):
        compile_catalog(broken)


def test_promoted_capabilities_are_explicit_and_pd2564_has_vendor_boot() -> None:
    compiled = compile_or_skip(ROOT / "model_catalog.yaml")
    devices = compiled["devices"]

    # The reviewed promoted set plus later released maintenance additions.
    assert {
        "oppofindx9", "oppofindx9pro", "vivox200", "vivox200pro",
        "vivox200promini", "vivopad5", "iqoopad5pro", "vivopd2417",
        "vivopd2309", "vivopd2324", "vivopd2339", "vivodpd2329",
        "vivopd2502", "vivopd2241", "honormagic8proair", "vivopd2545",
        "vivopd2564", "iqooz8", "iqoopad",
    } <= set(devices)
    assert set(devices) - {
        "oppofindx9", "oppofindx9pro", "vivox200", "vivox200pro",
        "vivox200promini", "vivopad5", "iqoopad5pro", "vivopd2417",
        "vivopd2309", "vivopd2324", "vivopd2339", "vivodpd2329",
        "vivopd2502", "vivopd2241", "honormagic8proair", "vivopd2545",
        "vivopd2564", "iqooz8", "iqoopad",
    } == {"vivopd2415", "vivodpd2429", "vivopd2302", "vivopd2242"}
    assert devices["vivopd2564"]["partitions"]["names"]["vendor_boot"] == "vendor_boot"
    assert devices["iqooz8"]["capabilities"] == ["preload"]
    assert devices["iqoopad"]["capabilities"] == ["preload"]
    assert devices["honormagic8proair"]["capabilities"] == ["preload", "generic_lk"]
    assert "xingc" not in devices["honormagic8proair"]["capabilities"]
    assert "flashing_unlock" not in devices["honormagic8proair"]["capabilities"]
    assert devices["vivopd2241"]["match"]["model"] == ["pd2241"]


@pytest.mark.skip(
    reason=(
        "one-shot migration from the pre-split two-repository source; already "
        "executed and captured in model_catalog.yaml"
    )
)
def test_legacy_migration_emits_a_safe_yaml_five_table_catalog() -> None:
    migrated = migrate(ROOT / "devices.yaml", ROOT / "tests" / "fixtures" / "migrated_devices.yaml")

    serialized = yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False)

    assert set(yaml.safe_load(serialized)) == {"platforms", "payloads", "runtimes", "workflows", "devices"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda source: source["runtimes"]["runtime-1"]["resources"].update(
                {"private_key": "must-not-compile"}
            ),
            "unknown runtime resource",
        ),
        (
            lambda source: source["runtimes"]["runtime-4"]["resources"].update(
                {"preload_trigger": "/system/bin/id ; reboot"}
            ),
            "unsafe preload trigger",
        ),
        (
            lambda source: source["runtimes"]["runtime-6"]["resources"].update(
                {"preload_clean_env": "true"}
            ),
            "invalid runtime resource",
        ),
    ),
)
def test_runtime_schema_rejects_untyped_or_unsafe_resources(mutation, message: str) -> None:
    source = load_catalog(ROOT / "model_catalog.yaml")
    mutation(source)

    with pytest.raises(CatalogError, match=message):
        compile_catalog(source)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda source: source["workflows"]["workflow-1"]["partitions"].update(
            {"backup": ["lk", "lk"]}
        ),
        lambda source: source["workflows"]["workflow-1"]["partitions"]["names"].update(
            {"not-a-backup": "lk"}
        ),
        lambda source: source["workflows"]["workflow-1"].update(
            {"post_unlock": {"arbitrary": True}}
        ),
        lambda source: source["workflows"]["workflow-1"].update(
            {"protection_disable_cmds": ["reboot; id"]}
        ),
    ),
)
def test_workflow_schema_rejects_ambiguous_or_unsafe_fields(mutation) -> None:
    source = load_catalog(ROOT / "model_catalog.yaml")
    mutation(source)

    with pytest.raises(CatalogError, match="invalid (partition mappings|workflow transitions)"):
        compile_catalog(source)


def test_alias_overlap_rejects_brands_that_runtime_can_match_together() -> None:
    source = load_catalog(ROOT / "model_catalog.yaml")
    source["platforms"]["honor"]["brand"] = "vi"
    source["runtimes"]["runtime-11"]["platform"] = "honor"
    source["devices"]["honormagic8proair"]["match"] = {
        "brand": "vi",
        "model": ["x200"],
    }

    with pytest.raises(CatalogError, match="ambiguous alias overlap"):
        compile_catalog(source)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda source: source["runtimes"]["runtime-9"]["resources"].update(
            {"preload_args": ["--full;reboot"]}
        ),
        lambda source: source["runtimes"]["runtime-9"]["resources"].update(
            {"preload_args": ["--full\x01"]}
        ),
        lambda source: source["runtimes"]["runtime-4"]["resources"]["preload_env"].update(
            {"STACK_STAMP": "socket\nreboot"}
        ),
        lambda source: source["runtimes"]["runtime-4"]["resources"]["preload_env"].update(
            {"STACK_STAMP": "$(reboot)"}
        ),
    ),
)
def test_runtime_schema_rejects_argv_and_environment_control_or_shell_operators(mutation) -> None:
    source = load_catalog(ROOT / "model_catalog.yaml")
    mutation(source)

    with pytest.raises(CatalogError, match="unsafe runtime token"):
        compile_catalog(source)


def test_workflow_partition_names_must_map_to_distinct_physical_bases() -> None:
    source = load_catalog(ROOT / "model_catalog.yaml")
    source["workflows"]["workflow-1"]["partitions"]["names"]["init_boot"] = "lk"

    with pytest.raises(CatalogError, match="invalid partition mappings"):
        compile_catalog(source)


def test_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text("platforms:\n  vivo: {brand: vivo}\n  vivo: {brand: vivo}\n", encoding="utf-8")

    with pytest.raises(CatalogError, match="duplicate YAML key"):
        load_catalog(catalog)


def test_catalog_rejects_unknown_reference_and_platform_mismatch() -> None:
    source = load_catalog(ROOT / "model_catalog.yaml")
    source["devices"]["vivox200"]["runtime"] = "not-a-runtime"
    with pytest.raises(CatalogError, match="references unknown"):
        compile_catalog(source)

    source = load_catalog(ROOT / "model_catalog.yaml")
    source["devices"]["vivox200"]["payload"] = "oppo_fx9_fx9p_16.0.8.301"
    with pytest.raises(CatalogError, match="platform mismatch"):
        compile_catalog(source)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
