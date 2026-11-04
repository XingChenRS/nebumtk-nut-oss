# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
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
