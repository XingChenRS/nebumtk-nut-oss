from __future__ import annotations

from pathlib import Path

from nebumtk_unlock_tools.lk.ubl.catalog import LocatorRecord, PatchCatalog, PatchRecord
from nebumtk_unlock_tools.lk.ubl.xingc_locator import locate_patch

from tests._payload_material import xingc_catalog_or_skip


ROOT = Path(__file__).resolve().parents[1]


def _signature_patch(
    *,
    needle: bytes,
    replace_at: int,
    partition: str = "lk",
    profile: str | None = None,
) -> PatchRecord:
    return PatchRecord(
        id="xingc-rename-veify-command",
        human_name="test",
        category="test",
        risk="test",
        summary="test",
        principle="test",
        ida_context="test",
        locators=(
            LocatorRecord(
                kind="signature",
                needle=needle,
                replace_at=replace_at,
                replace_len=5,
                partition=partition,
                image_profile=profile,
            ),
        ),
        patch_hex=b"xingc".hex(),
        replace_len=5,
    )


def _fixed_patch(*, profile: str) -> PatchRecord:
    return PatchRecord(
        id="xingc-rename-veify-command",
        human_name="test",
        category="test",
        risk="test",
        summary="test",
        principle="test",
        ida_context="test",
        locators=(
            LocatorRecord(
                kind="fixed",
                offset=20,
                original=b"veify",
                replace_len=5,
                image_profile=profile,
            ),
        ),
        patch_hex=b"xingc".hex(),
        replace_len=5,
    )


def test_signature_locator_is_limited_to_declared_lk_partition() -> None:
    context = b"oem reboot-recovery\x00veify\x00set slot b failed!\x00"
    command_offset = 100
    duplicate_offset = 420
    image = bytearray(b"\x00" * 600)
    image[command_offset : command_offset + len(context)] = context
    image[duplicate_offset : duplicate_offset + len(context)] = context
    patch = _signature_patch(
        needle=context,
        replace_at=context.index(b"veify"),
    )

    status = locate_patch(
        bytes(image),
        patch,
        replacement=b"xingc",
        search_ranges={"lk": (0, 300), "aee": (300, 600)},
    )

    assert status.state == "original"
    assert status.offset == command_offset + context.index(b"veify")


def test_profile_specific_fixed_locator_is_not_used_for_x90_compatibility() -> None:
    image = bytearray(b"\x00" * 64)
    image[20:25] = b"veify"
    patch = _fixed_patch(profile="x100")

    status = locate_patch(
        bytes(image),
        patch,
        replacement=b"xingc",
        image_profile="mt6985_or_lower",
    )

    assert status.state == "not-found"


def test_profile_specific_fixed_locator_applies_to_matching_x100_profile() -> None:
    image = bytearray(b"\x00" * 64)
    image[20:25] = b"veify"
    patch = _fixed_patch(profile="x100")

    status = locate_patch(
        bytes(image),
        patch,
        replacement=b"xingc",
        image_profile="x100",
    )

    assert status.state == "original"
    assert status.offset == 20


def test_catalog_declares_unlock2_and_non_unlock2_command_contexts() -> None:
    catalog = PatchCatalog.from_path(xingc_catalog_or_skip())
    command = catalog.patch("xingc-rename-veify-command")

    assert any(
        locator.layout == "non_unlock2"
        and locator.needle is not None
        and locator.needle.startswith(b"oem reboot-recovery\x00veify\x00set slot")
        and locator.replace_at == locator.needle.index(b"veify")
        for locator in command.locators
    )
    assert all(
        locator.profile == "x100"
        for locator in command.locators
        if locator.kind == "fixed"
    )
    x100_command = next(
        locator
        for locator in command.locators
        if locator.needle == b"\x0a\x00veify\x00"
    )
    assert x100_command.layout == "unlock2"


def test_unlock2_command_context_is_selected_without_a_device_profile_gate() -> None:
    catalog = PatchCatalog.from_path(xingc_catalog_or_skip())
    command = catalog.patch("xingc-rename-veify-command")
    image = bytearray(b"\x00" * 128)
    image[40:48] = b"\x0a\x00veify\x00"

    status = locate_patch(
        bytes(image),
        command,
        replacement=b"xingc",
        search_ranges={"lk": (0, len(image))},
        image_profile="vivopd2502",
    )

    assert status.state == "original"
    assert status.offset == 42


def test_non_unlock2_command_context_is_selected_without_a_device_profile_gate() -> None:
    catalog = PatchCatalog.from_path(xingc_catalog_or_skip())
    command = catalog.patch("xingc-rename-veify-command")
    context = b"oem reboot-recovery\x00veify\x00set slot b failed!\x00"
    image = bytearray(b"\x00" * 256)
    image[40 : 40 + len(context)] = context

    status = locate_patch(
        bytes(image),
        command,
        replacement=b"xingc",
        search_ranges={"lk": (0, len(image))},
        image_profile="vivopd2013",
    )

    assert status.state == "original"
    assert status.offset == 40 + context.index(b"veify")


def test_unlock_never_short_circuits_on_a_reported_unlocked_state(monkeypatch) -> None:
    """A stale ``unlocked`` reading must not skip the signed Xingc exchange.

    The device's own ``xingc unlock`` response is the only proof the
    authorization applied; short-circuiting on ``fastboot getvar unlocked``
    previously returned success without delivering any token.
    """

    from nebumtk_unlock_tools.lk import xingc_service

    service = xingc_service.XingcUnlockService()
    monkeypatch.setattr(service, "readiness_check", lambda **_k: None)

    events: list[str] = []

    class _Protocol:
        def __init__(self, _transport) -> None:
            events.append("open")

        def get_unlock_token(self) -> str:
            events.append("token")
            return "TOKEN"

        def download(self, payload: bytes):
            events.append("download")
            from types import SimpleNamespace

            return SimpleNamespace(status="OKAY", message="")

        def command(self, name: str):
            events.append(f"command:{name}")
            from types import SimpleNamespace

            return SimpleNamespace(status="OKAY", message="")

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(xingc_service, "FastbootProtocol", _Protocol)
    monkeypatch.setattr(xingc_service, "PyUsbFastbootTransport", lambda **_k: object())
    monkeypatch.setattr(service, "build_payload", lambda token, target="": b"payload")

    class _Fastboot:
        def is_unlocked(self) -> bool:
            return True  # already reports unlocked before the exchange

    result = service.unlock("SERIAL", _Fastboot())

    assert events == ["open", "token", "download", "command:xingc unlock", "close"]
    assert result.already_unlocked is False
