from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebumtk_unlock_tools.config.store import Settings, SettingsStore
from nebumtk_unlock_tools.core.adb import AdbClient
from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.core.runner import CommandResult


@pytest.mark.parametrize(
    "value",
    [
        "su",
        "ksu_1",
        "su.debug",
        "/data/local/tmp/su",
        "/apex/com.android.virt/bin/su",
    ],
)
def test_custom_su_accepts_one_safe_android_executable_token(value: str) -> None:
    from nebumtk_unlock_tools.core.su import validate_custom_su

    assert validate_custom_su(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        " su",
        "su ",
        "su -c",
        "su\n",
        "su\x00x",
        "su;reboot",
        "su|id",
        "su&id",
        "su$id",
        "su`id`",
        'su"x',
        "su'x",
        "data/local/tmp/su",
        "/data/local/../tmp/su",
        "/data//local/su",
        "/data/本地/su",
        "x" * 257,
    ],
)
def test_custom_su_rejects_non_token_shell_syntax(value: str) -> None:
    from nebumtk_unlock_tools.core.su import validate_custom_su

    with pytest.raises(ValueError, match="custom su"):
        validate_custom_su(value)


def test_su_provider_keeps_protected_runtime_and_custom_setting_separate() -> None:
    from nebumtk_unlock_tools.core.su import resolve_su_provider

    settings = Settings(custom_su="/data/local/tmp/operator-su")

    protected = resolve_su_provider(
        "protected",
        protected_executable="/data/local/tmp/profile-su",
        settings=settings,
    )
    custom = resolve_su_provider(
        "custom",
        protected_executable="/data/local/tmp/profile-su",
        settings=settings,
    )

    assert (protected.kind, protected.executable) == (
        "protected",
        "/data/local/tmp/profile-su",
    )
    assert (custom.kind, custom.executable) == (
        "custom",
        "/data/local/tmp/operator-su",
    )


@dataclass
class ProbeAdb:
    calls: list[tuple[str, bool, str | None]]

    def shell(
        self,
        command: str,
        *,
        root: bool = False,
        su_path: str | None = None,
        **_kwargs,
    ) -> CommandResult:
        self.calls.append((command, root, su_path))
        return CommandResult(0, "uid=0(root) gid=0(root)\n", "")


def test_custom_su_probe_runs_only_fixed_id_without_grant_request() -> None:
    from nebumtk_unlock_tools.core.su import probe_custom_su

    adb = ProbeAdb([])

    result = probe_custom_su(adb, "/data/local/tmp/operator-su")

    assert result.uid == 0
    assert result.output == "uid=0(root) gid=0(root)"
    assert adb.calls == [("id", True, "/data/local/tmp/operator-su")]


def test_custom_su_probe_rejects_oversized_device_uid_as_verification_error() -> None:
    from nebumtk_unlock_tools.core.su import probe_custom_su

    class OversizedUidAdb(ProbeAdb):
        def shell(self, *args, **kwargs) -> CommandResult:
            super().shell(*args, **kwargs)
            return CommandResult(0, f"uid={'9' * 5000}(root)\n", "")

    with pytest.raises(VerificationError, match="fixed id probe"):
        probe_custom_su(OversizedUidAdb([]), "su")


def test_custom_su_settings_round_trip_locally(tmp_path) -> None:
    store = SettingsStore(tmp_path / "settings.json")

    store.save(Settings(custom_su="/data/local/tmp/operator-su"))

    loaded = store.load()
    assert loaded.custom_su == "/data/local/tmp/operator-su"


def test_adb_root_renderer_keeps_custom_su_as_one_argv_token(monkeypatch) -> None:
    adb = AdbClient(serial="SERIAL")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        adb,
        "run",
        lambda *args, **_kwargs: calls.append(args)
        or CommandResult(0, "uid=0(root)\n", ""),
    )

    adb.shell(
        "id",
        root=True,
        su_path="/data/local/tmp/operator-su",
        check=False,
    )

    assert calls == [
        ("shell", "/data/local/tmp/operator-su -c id"),
    ]


def test_flow_context_custom_provider_probes_without_protected_escalation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebumtk_unlock_tools.flows.base import FlowContext

    adb = ProbeAdb([])
    context = FlowContext(
        tmp_path,
        tmp_path / "workspace",
        Settings(
            test_mode_enabled=True,
            custom_su="/data/local/tmp/operator-su",
        ),
    )
    context.adb = adb  # type: ignore[assignment]
    profile = SimpleNamespace(su_path="/data/local/tmp/profile-su")
    context.select_su_provider(profile, "custom")
    monkeypatch.setattr(
        context,
        "escalate_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("custom provider must not request protected escalation")
        ),
    )

    context.ensure_root(profile, None)

    assert adb.calls == [("id", True, "/data/local/tmp/operator-su")]


def test_root_provider_prefers_global_shell_su_before_custom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebumtk_unlock_tools.flows import base

    context = base.FlowContext(
        tmp_path,
        tmp_path / "workspace",
        Settings(
            test_mode_enabled=True,
            custom_su="/data/local/tmp/operator-su",
        ),
    )
    context.adb = SimpleNamespace(su_path="/vendor/bin/profile-su")
    profile = SimpleNamespace(su_path="/vendor/bin/profile-su")
    context._make_escalator = lambda _profile=None: SimpleNamespace(
        verify_root=lambda: context.adb.su_path == "su"
    )
    monkeypatch.setattr(
        base,
        "probe_custom_su",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("custom su must not run before global su")
        ),
    )

    context.ensure_root(profile, None)

    assert context.su_provider.executable == "su"


def test_custom_su_is_tried_after_profile_and_global_su_in_emergency_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nebumtk_unlock_tools.flows import base

    custom = "/data/local/tmp/operator-su"
    context = base.FlowContext(
        tmp_path,
        tmp_path / "workspace",
        Settings(test_mode_enabled=True, custom_su=custom),
    )
    context.adb = SimpleNamespace(su_path="/vendor/bin/profile-su")
    profile = SimpleNamespace(su_path="/vendor/bin/profile-su")
    context._make_escalator = lambda _profile=None: SimpleNamespace(
        verify_root=lambda: False
    )
    monkeypatch.setattr(
        base,
        "probe_custom_su",
        lambda _adb, executable: SimpleNamespace(uid=0, executable=executable),
    )

    context.ensure_root(profile, None)

    assert context.su_provider.kind == "custom"
    assert context.su_provider.executable == custom
