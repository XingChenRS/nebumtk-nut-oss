# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebumtk_unlock_tools.core.device import ConnectedDevice, DeviceDetector, PrivilegeRuntime
from nebumtk_unlock_tools.core.errors import DeviceMatchError
from nebumtk_unlock_tools.core.root import RootEscalator
from tests._payload_material import payload_source


ROOT = Path(__file__).resolve().parent.parent
DPD2329_PAYLOAD = "dpd2329_mt6989_gki61_5b0f71af"
PD2502_PAYLOAD = "pd2502_mt6993_abce62cfa4"


def _profiles():
    return DeviceDetector._load_profiles(ROOT / "devices.yaml")


def _elf_type(path: Path) -> int:
    header = path.read_bytes()[:20]
    assert header.startswith(b"\x7fELF\x02\x01")
    return struct.unpack_from("<H", header, 16)[0]


class _ChallengeAdb:
    serial = "TARGET"

    def __init__(self, payload_id: str) -> None:
        self.payload_id = payload_id
        self.commands: list[str] = []
        self.removed: list[str] = []

    def shell_challenge_exchange(self, command: str, responder, **_kwargs):
        challenge = f"NBCH1:{self.payload_id}:" + "11" * 32 + ":" + "22" * 32
        responder(challenge)
        self.commands.append(command)
        return SimpleNamespace(returncode=0, output="")

    def remove_remote(self, remote: str, **_kwargs) -> None:
        self.removed.append(remote)

    def shell(self, _command: str, **_kwargs):
        return SimpleNamespace(returncode=0, output="")


def _grant(_challenge: str, _target: str) -> str:
    return "NBGR1:" + "11" * 16 + ":1:" + "22" * 32 + ":" + "33" * 12 + ":" + "44" * 48


def test_mt6989_and_mt6993_payloads_are_distinct_and_hash_locked() -> None:
    dpd2329 = payload_source(DPD2329_PAYLOAD)
    pd2502 = payload_source(PD2502_PAYLOAD)

    assert dpd2329.stat().st_size == 270552
    assert hashlib.sha256(dpd2329.read_bytes()).hexdigest() == (
        "5b0f71af10675dce5a173f64144fe69d1d66e2728f6616eaf45e5587cf999be0"
    )
    assert _elf_type(dpd2329) == 3  # ET_DYN: MT6989 LD_PRELOAD payload
    assert pd2502.stat().st_size == 362296
    assert hashlib.sha256(pd2502.read_bytes()).hexdigest() == (
        "abce62cfa42a5f9afa1c1a707a353c8096352473d7993b534eac8aa1d7839956"
    )
    assert _elf_type(pd2502) == 2  # ET_EXEC: MT6993 standalone payload


def test_pd2417_import_uses_the_current_reviewed_catalog_bytes() -> None:
    current = payload_source("pd2417_mt6989_81e684c917")
    assert current.stat().st_size == 270152
    assert hashlib.sha256(current.read_bytes()).hexdigest() == (
        "81e684c9170d2d97963d653b2f0eb1953f291008b077df0dfcb0f90d8b2a6c69"
    )


def test_dpd2329_mt6989_profile_keeps_exact_clean_runtime() -> None:
    profiles = _profiles()
    profile = profiles["vivodpd2329"]
    expected = dict(profiles["vivopd2324"].preload_env)
    expected["TRACEFS_KPTR_OFF"] = "0x2EFC6C"

    assert profile.resources["preload"] == f"builtin:{DPD2329_PAYLOAD}"
    assert profile.preload_mode == "preload"
    assert profile.preload_trigger == "/system/bin/id"
    assert profile.preload_clean_env is True
    assert profile.preload_env == expected
    assert profile.su_path == "/data/local/tmp/su"


@pytest.mark.parametrize(
    ("model", "market_name"),
    (("PA2473", "vivo Pad3 Pro"), ("iPA2475", "iQOO Pad2 Pro")),
)
def test_dpd2329_matches_only_reviewed_pad_models(
    model: str,
    market_name: str,
) -> None:
    detector = DeviceDetector.__new__(DeviceDetector)
    detector.profiles = _profiles()
    matched = detector.match_profile(
        ConnectedDevice(
            serial="TEST",
            brand="vivo",
            model=model,
            device="DPD2329",
            firmware="DPD2329_A_15.1.12.1.W10.V000L1",
            slot_suffix="_a",
            market_name=market_name,
        )
    )
    assert matched.key == "vivodpd2329"


def test_pd2502_mt6993_profile_matches_connected_x300_pro_props() -> None:
    detector = DeviceDetector.__new__(DeviceDetector)
    detector.profiles = _profiles()
    matched = detector.match_profile(
        ConnectedDevice(
            serial="TEST",
            brand="vivo",
            model="V2502A",
            device="PD2502",
            firmware="PD2502B_A_16.0.29.7.W10",
            slot_suffix="_b",
            market_name="vivo X300 Pro",
        )
    )
    assert matched.key == "vivopd2502"
    assert matched.resources["preload"] == f"builtin:{PD2502_PAYLOAD}"
    assert matched.preload_mode == "exec"
    assert matched.preload_args == ("--full",)
    assert matched.preload_env == {}
    assert matched.su_path == "/data/local/tmp/cathash_su"


def test_pd2502_exec_runtime_never_uses_ld_preload() -> None:
    profile = _profiles()["vivopd2502"]
    adb = _ChallengeAdb(PD2502_PAYLOAD)
    escalator = RootEscalator(
        adb,
        grant_responder=_grant,
        execution_mode=profile.preload_mode,
        trigger_command=profile.preload_trigger,
        payload_args=profile.preload_args,
        trigger_env=profile.preload_env,
        clean_environment=profile.preload_clean_env,
    )

    escalator._run_preload()

    assert len(adb.commands) == 1
    command = adb.commands[0]
    assert "-m exec -- __NEBUMTK_PAYLOAD__ --full" in command
    assert "LD_PRELOAD" not in command
    assert "/system/bin/id" not in command


def test_dpd2329_runtime_stays_in_mt6989_preload_mode() -> None:
    profile = _profiles()["vivodpd2329"]
    adb = _ChallengeAdb(DPD2329_PAYLOAD)
    escalator = RootEscalator(
        adb,
        grant_responder=_grant,
        execution_mode=profile.preload_mode,
        trigger_command=profile.preload_trigger,
        payload_args=profile.preload_args,
        trigger_env=profile.preload_env,
        clean_environment=profile.preload_clean_env,
    )

    escalator._run_preload()

    command = adb.commands[0]
    assert "-m preload -- /system/bin/toybox env -i" in command
    assert "TRACEFS_KPTR_OFF=0x2EFC6C" in command
    assert "LD_PRELOAD=__NEBUMTK_MEMFD__ /system/bin/id" in command
    assert "__NEBUMTK_PAYLOAD__" not in command


def test_exec_runtime_rejects_untyped_or_multiline_arguments() -> None:
    with pytest.raises(ValueError, match="payload argument"):
        RootEscalator(
            _ChallengeAdb(PD2502_PAYLOAD),
            execution_mode="exec",
            payload_args=["--full\nreboot"],
        )


def test_catalog_runtime_rejects_unknown_mode_and_unsafe_su_path() -> None:
    with pytest.raises(DeviceMatchError, match="执行模式"):
        PrivilegeRuntime.from_resources(
            "vivo",
            {"preload": f"builtin:{PD2502_PAYLOAD}", "preload_mode": "auto"},
        )
    with pytest.raises(DeviceMatchError, match="su 路径"):
        PrivilegeRuntime.from_resources(
            "vivo",
            {
                "preload": f"builtin:{PD2502_PAYLOAD}",
                "preload_mode": "exec",
                "preload_args": ["--full"],
                "su_path": "/data/local/tmp/su;reboot",
            },
        )


def test_native_wrapper_has_separate_reviewed_exec_and_preload_branches() -> None:
    source = (ROOT / "native" / "preload_wrapper" / "wrapper_main.c").read_text(
        encoding="utf-8"
    )
    assert 'strcmp(execution_mode, "exec")' in source
    assert 'strcmp(execution_mode, "preload")' in source
    assert 'strcmp(argv[cmd_index], "__NEBUMTK_PAYLOAD__")' in source
    assert "execv(fd_path, &argv[cmd_index])" in source
    assert "direct_exec_error != EACCES" in source
    assert "O_TMPFILE | O_RDWR | O_CLOEXEC" in source
    assert "execveat_portable(anonymous_fd" in source
    assert 'open(\n        "/data/local/tmp",' in source
    assert '"/data/local/tmp/nebumtk_payload' not in source
    assert "bool placeholder_replaced = false" in source
    assert 'strcmp(argv[i], "LD_PRELOAD=__NEBUMTK_MEMFD__")' in source
    assert 'unsetenv("LD_PRELOAD")' in source
    assert 'else if (setenv("LD_PRELOAD", fd_path, 1) != 0)' in source
    assert "parse_hex_key" not in source
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
