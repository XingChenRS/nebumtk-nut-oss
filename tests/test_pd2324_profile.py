# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebumtk_unlock_tools.core.device import ConnectedDevice, DeviceDetector
from nebumtk_unlock_tools.core.errors import RootEscalationError
from nebumtk_unlock_tools.core.root import RootEscalator
from tests._payload_material import payload_source

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_ID = "pd2324_gki61_misc_14e1c6d0"
EXPECTED_ENV = {
    "PATH": "/system/bin:/system_ext/bin:/vendor/bin",
    "HOME": "/data/local/tmp",
    "TMPDIR": "/data/local/tmp",
    "PRELOAD_LOG_DIR": "/data/local/tmp",
    "PRELOAD_LOG_HOST_MIRROR": "1",
    "TRACEFS_KPTR_OFF": "0x2EF804",
    "KASLR_ONLY": "0",
    "STACK_STAMP": "socket",
    "PAGE_RECLAIM_VIA": "filemap",
    "PAGE_FILEMAP_DIR": "/data/local/tmp",
    "PAGE_FILEMAP_PAGES": "4096",
    "PAGE_FILEMAP_MIN_SLOTS": "8",
    "PAGE_FILEMAP_ATTEMPTS": "64",
    "PAGE_RECLAIM_SKB_ALSO": "0",
    "PAGE_RECLAIM_SKB_FALLBACK": "0",
    "PAGE_CLOSE_ALL_POST": "0",
    "PAGE_CLOSE_ALL_SPRAY": "0",
    "PAGE_PHYS_OFFSET_ALLOW_400M_FALLBACK": "1",
    "PAGE_REQUIRE_PAINT": "1",
    "PAGE_DATA_PHYSMAP": "0",
    "PAGE_WRITE_CANARY": "0",
    "PAGE_WRITE_SELINUX": "0",
    "PAGE_WRITE_MISC_FOPS": "1",
    "PAGE_WRITE_FILE_FOP": "0",
    "PAGE_WRITE_ASHMEM_ITER": "0",
    "PAGE_WRITE_PIPEFIFO": "0",
    "PAGE_WRITE_FAKE_FOPS": "0",
    "PAGE_MISC_DEVICE": "ashmem",
    "PAGE_MISC_FOPS_VALUE_DELTA": "0",
    "PAGE_MISC_PREOPEN_REPAINT": "1",
    "PAGE_MISC_PREOPEN_VERIFY": "0",
    "PAGE_MISC_SKIP_OPEN": "0",
    "PAGE_MISC_PROBE_CACHED": "0",
    "PAGE_MISC_RESTORE_ON_OPEN_FAIL": "1",
    "FAKE_FOPS_OPEN_NULL": "0",
    "FAKE_FOPS_RELEASE_NULL": "0",
    "PSELECT_WRITE_SHAPE": "1",
    "STAMP_ARM_CONSUMER": "1",
    "STAMP_FILL": "scan",
    "STAMP_SCAN": "0",
    "STAMP_FORGE_OFF": "120",
    "STAMP_FORGE_STEP": "8",
    "STAMP_REUSE_PAGE": "1",
    "STAMP_WARM_USEC": "50000",
    "STAMP_HOLD_USEC": "80000",
    "STAMP_CONSUMER_DELAY_USEC": "0",
    "STAMP_ATTEMPTS": "1",
    "PSELECT_CONSUMER_NICE": "19",
    "CONSUMER_POLICY_TOGGLE": "0",
    "CONSUMER_MAX_CALLS": "1",
    "PSELECT_CONSUMER_BURST_CALLS": "1",
    "CONSUMER_VERBOSE": "0",
    "ROUTE_THREAD_WAIT_MSEC": "5000",
    "PAGE_PIPE_STAGE_DEBUG": "1",
    "PAGE_PIPE_CACHE_DEBUG": "1",
    "ROOT_SKIP_WALLPAPER": "1",
    "SU_DAEMON_WATCH_SECS": "8",
}


class _CaptureAdb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.removed: list[tuple[str, dict[str, object]]] = []

    def shell(self, command: str, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, output="")

    def remove_remote(self, remote: str, **kwargs) -> None:
        self.removed.append((remote, kwargs))


class _ChallengeAdb(_CaptureAdb):
    serial = "ONLINE-DEVICE"

    def __init__(self) -> None:
        super().__init__()
        self.challenge_calls: list[tuple[str, str, dict[str, object]]] = []

    def shell_challenge_exchange(self, command: str, responder, **kwargs):
        challenge = (
            f"NBCH1:{PAYLOAD_ID}:"
            + f"{len(self.challenge_calls) + 1:064x}:"
            + "22" * 32
        )
        grant = responder(challenge)
        self.challenge_calls.append((command, grant, kwargs))
        return SimpleNamespace(returncode=0, output="")


class _RetryChallengeAdb(_ChallengeAdb):
    def wait_for_device(self, *, timeout: float) -> None:
        return None

    def devices(self) -> list[str]:
        return [self.serial]

    def shell_challenge_exchange(self, command: str, responder, **kwargs):
        challenge = (
            f"NBCH1:{PAYLOAD_ID}:"
            + f"{len(self.challenge_calls) + 1:064x}:"
            + "22" * 32
        )
        grant = responder(challenge)
        self.challenge_calls.append((command, grant, kwargs))
        return SimpleNamespace(
            returncode=126 if len(self.challenge_calls) < 3 else 0,
            output=(
                "NBERR:grant:13\n"
                if len(self.challenge_calls) < 3
                else ""
            ),
        )

    def shell(self, command: str, **kwargs):
        if command == "id" and kwargs.get("root"):
            rooted = len(self.challenge_calls) >= 3
            return SimpleNamespace(
                returncode=0,
                stdout="uid=0(root)\n" if rooted else "uid=2000(shell)\n",
                output="",
            )
        return super().shell(command, **kwargs)


class _Payload126Adb(_ChallengeAdb):
    def shell_challenge_exchange(self, command: str, responder, **kwargs):
        challenge = (
            f"NBCH1:{PAYLOAD_ID}:" + "11" * 32 + ":" + "22" * 32
        )
        grant = responder(challenge)
        self.challenge_calls.append((command, grant, kwargs))
        return SimpleNamespace(returncode=126, output="payload returned 126\n")


class _UnknownWrapperStageAdb(_RetryChallengeAdb):
    def shell_challenge_exchange(self, command: str, responder, **kwargs):
        challenge = f"NBCH1:{PAYLOAD_ID}:" + "11" * 32 + ":" + "22" * 32
        grant = responder(challenge)
        self.challenge_calls.append((command, grant, kwargs))
        return SimpleNamespace(returncode=126, output="NBERR:unexpected-stage:5\n")


def _profile():
    return DeviceDetector._load_profiles(ROOT / "devices.yaml")["vivopd2324"]


def test_pd2324_profile_matches_x100_pro_and_x100s_pro_props() -> None:
    detector = DeviceDetector.__new__(DeviceDetector)
    detector.profiles = DeviceDetector._load_profiles(ROOT / "devices.yaml")
    for model in ("V2324A", "V2324HA"):
        matched = detector.match_profile(
            ConnectedDevice(
                serial="TEST",
                brand="vivo",
                model=model,
                device="PD2324",
                firmware="PD2324_A_16.2.11.1.W10.V000L1",
                slot_suffix="_b",
                market_name=(
                    "vivo X100 Pro" if model == "V2324A" else "vivo X100s Pro"
                ),
            )
        )
        assert matched.key == "vivopd2324"


def test_pd2324_profile_keeps_exact_clean_environment_and_direct_su() -> None:
    profile = _profile()
    assert profile.su_path == "su"
    assert profile.preload_trigger == "/system/bin/id"
    assert profile.preload_clean_env is True
    assert profile.preload_env == EXPECTED_ENV
    assert profile.resources["preload"] == f"builtin:{PAYLOAD_ID}"


def test_pd2324_offline_preload_matches_supplied_hash() -> None:
    source = payload_source(PAYLOAD_ID)
    assert source.stat().st_size == 270064
    assert (
        hashlib.sha256(source.read_bytes()).hexdigest()
        == "14e1c6d0115185291906bc911ff8036682e5800449ab76d94a9035c359d91cfb"
    )


def test_pd2324_wrapper_uses_clean_env_with_memfd_placeholder() -> None:
    profile = _profile()
    adb = _ChallengeAdb()
    escalator = RootEscalator(
        adb,
        grant_responder=lambda _challenge, _target: (
            "NBGR1:" + "11" * 16 + ":1:" + "22" * 32 + ":" + "33" * 12 + ":" + "44" * 48
        ),
        trigger_command=profile.preload_trigger,
        trigger_env=profile.preload_env,
        clean_environment=profile.preload_clean_env,
    )

    escalator._run_preload()

    assert len(adb.challenge_calls) == 1
    command, _grant, kwargs = adb.challenge_calls[0]
    for key, value in EXPECTED_ENV.items():
        if key != "PRELOAD_LOG_DIR":
            assert f"{key}={value}" in command
    assert re.search(
        r"PRELOAD_LOG_DIR=/data/local/tmp/\.nebumtk-log-[0-9a-f]{16}",
        command,
    )
    assert (
        "LD_PRELOAD=__NEBUMTK_MEMFD__ /system/bin/id"
    ) in command
    assert "preload_pd2324_misc_14e1c6d0.so" not in command
    assert "input_text" not in kwargs
    assert adb.removed == [
        (RootEscalator.REMOTE_WRAPPER, {"root": False})
    ]


def test_online_wrapper_requests_a_fresh_grant_for_each_execution() -> None:
    profile = _profile()
    adb = _ChallengeAdb()
    seen: list[tuple[str, str]] = []

    def responder(challenge: str, target: str) -> str:
        seen.append((challenge, target))
        return (
            "NBGR1:"
            + f"{len(seen):032x}:1:"
            + "33" * 32
            + ":"
            + "44" * 12
            + ":"
            + "55" * 48
        )

    escalator = RootEscalator(
        adb,
        grant_responder=responder,
        trigger_command=profile.preload_trigger,
        trigger_env=profile.preload_env,
        clean_environment=profile.preload_clean_env,
    )

    escalator._run_preload()
    escalator._run_preload()

    assert len(adb.challenge_calls) == 2
    assert len(seen) == 2
    assert seen[0][0] != seen[1][0]
    assert all(target == "ONLINE-DEVICE" for _, target in seen)
    assert all("input_text" not in kwargs for _, _, kwargs in adb.challenge_calls)
    assert len(adb.removed) == 2


def test_wrapper_126_retries_with_fresh_process_and_grant_without_reboot() -> None:
    profile = _profile()
    adb = _RetryChallengeAdb()
    prepared: list[int] = []
    seen_challenges: list[str] = []

    def responder(challenge: str, _target: str) -> str:
        seen_challenges.append(challenge)
        return (
            "NBGR1:"
            + f"{len(seen_challenges):032x}:1:"
            + "22" * 32
            + ":"
            + "33" * 12
            + ":"
            + "44" * 48
        )

    escalator = RootEscalator(
        adb,
        max_retries=3,
        reboot_wait=0,
        post_boot_settle=0,
        grant_responder=responder,
        trigger_command=profile.preload_trigger,
        trigger_env=profile.preload_env,
        clean_environment=profile.preload_clean_env,
    )
    escalator._cancellable_sleep = lambda _seconds: None  # type: ignore[method-assign]

    escalator.escalate(before_attempt=lambda: prepared.append(len(prepared) + 1))

    assert prepared == [1, 2, 3]
    assert len(adb.challenge_calls) == 3
    assert len(set(seen_challenges)) == 3
    assert len({call[1] for call in adb.challenge_calls}) == 3
    assert not hasattr(adb, "reboots")


def test_payload_exit_126_without_authenticated_wrapper_marker_is_not_misclassified() -> None:
    profile = _profile()
    adb = _Payload126Adb()
    messages: list[str] = []
    escalator = RootEscalator(
        adb,
        grant_responder=lambda _challenge, _target: (
            "NBGR1:" + "11" * 16 + ":1:" + "22" * 32 + ":" + "33" * 12 + ":" + "44" * 48
        ),
        trigger_command=profile.preload_trigger,
        trigger_env=profile.preload_env,
        clean_environment=profile.preload_clean_env,
        log=messages.append,
    )

    escalator._run_preload()

    assert any("exit=126" in message for message in messages)


def test_unknown_authenticated_wrapper_stage_fails_closed_without_retry() -> None:
    profile = _profile()
    adb = _UnknownWrapperStageAdb()
    prepared: list[int] = []
    escalator = RootEscalator(
        adb,
        max_retries=4,
        reboot_wait=0,
        post_boot_settle=0,
        grant_responder=lambda _challenge, _target: (
            "NBGR1:" + "11" * 16 + ":1:" + "22" * 32 + ":" + "33" * 12 + ":" + "44" * 48
        ),
        trigger_command=profile.preload_trigger,
        trigger_env=profile.preload_env,
        clean_environment=profile.preload_clean_env,
    )
    escalator._cancellable_sleep = lambda _seconds: None  # type: ignore[method-assign]

    with pytest.raises(RootEscalationError):
        escalator.escalate(before_attempt=lambda: prepared.append(1))

    assert prepared == [1]
    assert len(adb.challenge_calls) == 1


def test_local_preload_preparation_error_stops_without_reconnect_retry() -> None:
    adb = _RetryChallengeAdb()
    escalator = RootEscalator(
        adb,
        max_retries=4,
        reboot_wait=0,
        post_boot_settle=0,
        grant_responder=lambda _challenge, _target: "unused",
    )
    reconnects: list[str] = []
    escalator._wait_for_reconnect = (  # type: ignore[method-assign]
        lambda *, reason, settle=True: reconnects.append(reason)
    )

    with pytest.raises(RootEscalationError, match="本地 preload 材料准备失败"):
        escalator.escalate(
            before_attempt=lambda: (_ for _ in ()).throw(FileNotFoundError("missing"))
        )

    assert reconnects == []
    assert adb.challenge_calls == []


@pytest.mark.parametrize(
    "environment",
    [
        {"LD_PRELOAD": "/data/local/tmp/plain.so"},
        {"BAD;reboot": "1"},
    ],
)
def test_preload_environment_rejects_loader_override_and_shell_injection(
    environment: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="invalid preload environment"):
        RootEscalator(
            _CaptureAdb(),
            trigger_command="/system/bin/id",
            trigger_env=environment,
        )
