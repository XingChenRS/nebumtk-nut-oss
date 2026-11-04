# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.device import DeviceDetector
from nebumtk_unlock_tools.flows.factory import create_flow
from nebumtk_unlock_tools.flows.stages import DualSlotSyncFlow
from tests._payload_material import payload_source


ROOT = Path(__file__).resolve().parent.parent

EXPECTED_PAYLOADS = {
    "pd2241_mt6989_7c13cc72": (325792, "7c13cc72a9d658681e423cb7759b16c88293bcd455cdf8a3d9bc4ba67afd7378"),
    "honor_43499_93c71ecdd3": (124944, "93c71ecdd335f73707c68641e6c394ea46469183582d1d4ec4b2d5ff1b19775d"),
    "pd2417_mt6989_81e684c917": (270152, "81e684c9170d2d97963d653b2f0eb1953f291008b077df0dfcb0f90d8b2a6c69"),
    "pd2502_mt6993_abce62cfa4": (362296, "abce62cfa42a5f9afa1c1a707a353c8096352473d7993b534eac8aa1d7839956"),
    "xfold6_mt6993_dbbe41252d": (371736, "dbbe41252d179c24b0f84465cb58ddc5de4eb6332f487800147bd12c6f28b181"),
    "pd2564_mt6993_ca9c4beb82": (372280, "ca9c4beb829482afa3e76692d160f5220562971c1b20574a0706b8e65ce94bd1"),
    "dpd2429_mt6991_617e8a1988": (380728, "617e8a19884b0f0bb385c0777ca796681f9edd68d0a9852ad77ec89321b0a688"),
    "pd2415_mt6991_30d98b9587": (185008, "30d98b9587ae49f3c4dfab3dd057820af8373e589c68cbfc394c24fa9875611c"),
    "pd2302_mt6985_57555af750": (323640, "57555af7502629b5a5a7332047347d687a3cf98516b2e8ef4b2642f92ee34a52"),
    "pd2242_mt6985_d623c872b0": (330640, "d623c872b08be71cb69d463f01db26601df327ed65112914ea9edefcd3589d06"),
    "dpd2437_mt6989_918bf64b": (271640, "918bf64b74763cad89131e5812fbbffe8dd92cbb413f63cfafc9824ebcf35fb5"),
    "iqooz8_mt6983_c1082bc392": (265952, "c1082bc392a736389cf6ef0cf42dcfc3e2b9ef4cc3161d5bfcf0097c98f82975"),
    "iqoopad_mt6983_5a8aec2813": (268208, "5a8aec28130223b54b2cb7ece256b6966d922957c212c778ef7a949831406ae7"),
}


def _profiles():
    return DeviceDetector._load_profiles(ROOT / "devices.yaml")


def test_all_new_payloads_are_hash_locked_arm64_and_registered() -> None:
    for payload_id, (size, expected_sha) in EXPECTED_PAYLOADS.items():
        source = payload_source(payload_id)
        assert source.is_file()
        assert source.stat().st_size == size
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected_sha
        assert source.read_bytes()[:8] == b"\x7fELF\x02\x01\x01\x00"


@pytest.mark.parametrize(
    ("profile_key", "payload_id", "mode", "su_path"),
    (
        ("vivopd2241", "pd2241_mt6989_7c13cc72", "preload", "/data/local/tmp/su"),
        ("honormagic8proair", "honor_43499_93c71ecdd3", "preload", "/data/local/tmp/su"),
        ("vivopd2417", "pd2417_mt6989_81e684c917", "preload", "/apex/com.android.virt/bin/su"),
        ("vivopd2502", "pd2502_mt6993_abce62cfa4", "exec", "/data/local/tmp/cathash_su"),
        ("vivopd2545", "xfold6_mt6993_dbbe41252d", "exec", "/data/local/tmp/cathash_su"),
        ("vivopd2564", "pd2564_mt6993_ca9c4beb82", "exec", "/data/local/tmp/cathash_su"),
        ("vivodpd2429", "dpd2429_mt6991_617e8a1988", "exec", "/data/local/tmp/pad5promt6991_43074"),
        ("vivopd2415", "pd2415_mt6991_30d98b9587", "exec", "/data/local/tmp/su"),
        ("vivopd2302", "pd2302_mt6985_57555af750", "preload", "/data/local/tmp/su"),
        ("vivopd2242", "pd2242_mt6985_d623c872b0", "preload", "/data/local/tmp/su"),
        ("vivopad5", "dpd2437_mt6989_918bf64b", "preload", "/data/local/tmp/su"),
        ("iqooz8", "iqooz8_mt6983_c1082bc392", "preload", "/data/local/tmp/su"),
        ("iqoopad", "iqoopad_mt6983_5a8aec2813", "preload", "/data/local/tmp/su"),
    ),
)
def test_new_profiles_use_the_reviewed_runtime_recipe(
    profile_key: str,
    payload_id: str,
    mode: str,
    su_path: str,
) -> None:
    profile = _profiles()[profile_key]
    assert profile.resources["preload"] == f"builtin:{payload_id}"
    assert profile.preload_mode == mode
    assert profile.su_path == su_path


def test_addon_preload_recipes_preserve_usage_and_su() -> None:
    profiles = _profiles()
    dpd2437 = profiles["vivopad5"]
    assert dpd2437.preload_trigger == "/system/bin/id"
    assert dpd2437.preload_clean_env is True
    assert "LD_PRELOAD" not in dpd2437.preload_env
    assert dpd2437.preload_env["PAGE_WRITE_MISC_FOPS"] == "1"
    assert dpd2437.preload_env["STAMP_ARM_CONSUMER"] == "1"
    assert profiles["iqooz8"].preload_trigger == "/system/bin/toybox id"
    assert profiles["iqoopad"].preload_trigger == "/system/bin/toybox id"


def test_honor_profile_points_at_protected_lk_and_honor_misc() -> None:
    profile = _profiles()["honormagic8proair"]
    assert profile.brand == "honor"
    assert profile.resources["fastboot_artifact"] == "honor_magic8proair_lk"
    assert profile.resources["misc_brand"] == "honor"
    assert profile.resources.get("unlock_via_xingc") is False


def test_all_second_round_newtype_templates_are_registered() -> None:
    profiles = _profiles()
    expected = {
        "vivopd2564": "pd2564_mt6993_ca9c4beb82",
        "vivopd2415": "pd2415_mt6991_30d98b9587",
        "vivopd2302": "pd2302_mt6985_57555af750",
        "vivodpd2429": "dpd2429_mt6991_617e8a1988",
        "vivopd2242": "pd2242_mt6985_d623c872b0",
        "vivopd2545": "xfold6_mt6993_dbbe41252d",
    }
    assert {key: profiles[key].resources["preload"].removeprefix("builtin:") for key in expected} == expected


def test_honor_reuses_existing_dual_slot_fastboot_flow() -> None:
    flow = create_flow(
        "dual_slot_sync",
        ROOT,
        ROOT / ".test-workspace",
        Settings(),
        patched_lk="builtin:honor_magic8proair_lk",
    )
    assert flow.__class__.__name__ == "DualSlotSyncFlow"


def test_honor_dual_slot_flow_materializes_its_protected_lk() -> None:
    profile = _profiles()["honormagic8proair"]
    materialized = Path("honor-materialized-lk.bin")
    ctx = SimpleNamespace(
        loader=SimpleNamespace(resolve_fastboot_artifact=Mock(return_value=materialized)),
        init_clients=Mock(),
        set_progress=Mock(),
    )
    flow = DualSlotSyncFlow(
        ROOT,
        ROOT / ".test-workspace",
        Settings(),
        "builtin:honor_magic8proair_lk",
    )
    flow.ctx = ctx

    with patch(
        "nebumtk_unlock_tools.flows.stages._finalize_explicit_lk_fastboot"
    ) as finalize:
        flow.run(SimpleNamespace(serial="HONOR-SERIAL"), profile)

    ctx.loader.resolve_fastboot_artifact.assert_called_once_with("honor_magic8proair_lk")
    assert finalize.call_args.args[2] == materialized
    assert finalize.call_args.kwargs["require_unlocked"] is True
    assert finalize.call_args.kwargs["require_patched"] is False


def test_iqoo_profiles_reuse_existing_vivo_unlock_surface() -> None:
    profiles = _profiles()
    for key in ("iqooz8", "iqoopad"):
        profile = profiles[key]
        assert profile.brand == "vivo"
        assert not profile.fastboot_artifact
        assert "unlock_via_xingc" not in profile.resources
    flow = create_flow(
        "vivo_complete_unlock",
        ROOT,
        ROOT / ".test-workspace",
        Settings(),
    )
    assert flow.__class__.__name__ == "VivoCompleteUnlockFlow"


def test_honor_artifact_metadata_is_hash_locked() -> None:
    from nebumtk_unlock_tools.resources.artifact_store import artifacts

    ARTIFACTS = artifacts()
    if not ARTIFACTS:
        pytest.skip("no local artifact store (material/artifacts/manifest.json)")

    record = ARTIFACTS["honor_magic8proair_lk"]
    assert record["size"] == 8 * 1024 * 1024
    assert record["sha256"] == "6af5a77e3ff0a1c6b424db455f8b5ba90d5be66dc710d2c5b889dbdacabb49eb"
    ciphertext = base64.b85decode(record["ciphertext_b85"].encode("ascii"))
    assert len(ciphertext) == record["size"] + 16  # AES-GCM tag
    assert len(record["salt_b64"]) > 0
    assert len(record["nonce_b64"]) > 0
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
