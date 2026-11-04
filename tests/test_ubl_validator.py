# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

import pytest
from pyasn1.codec.der.encoder import encode as der_encode
from pyasn1.type import univ

from nebumtk_unlock_tools.lk.ubl.catalog import (
    LocatorRecord,
    PatchCatalog,
    PatchRecord,
)
from nebumtk_unlock_tools.lk.ubl.certs import inspect_cert_status
from nebumtk_unlock_tools.lk.ubl.size_policy import MAX_RAW_IMAGE_GROWTH
from nebumtk_unlock_tools.lk.ubl.workflow import (
    UblError,
    run_patch,
    validate_patched_image,
    verify_files,
)
from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage as LibLkImage
from nebumtk_unlock_tools.lk.vendor.liblk.structures.partition import LkPartition


def _cert2(header_hash: bytes, image_hash: bytes) -> bytes:
    fields = univ.Sequence()
    for index in range(15):
        if index in {9, 13}:
            fields.setComponentByPosition(index, univ.ObjectIdentifier("2.16.840.1.101.3.4.2.1"))
        elif index == 10:
            fields.setComponentByPosition(index, univ.BitString(hexValue=image_hash.hex()))
        elif index == 14:
            fields.setComponentByPosition(index, univ.BitString(hexValue=header_hash.hex()))
        else:
            fields.setComponentByPosition(index, univ.Integer(index))
    outer = univ.Sequence()
    outer.setComponentByPosition(0, fields)
    return der_encode(outer)


def _partition(name: str, payload: bytes, *, last: bool) -> LkPartition:
    partition = LkPartition.create(name, payload)
    partition.header.image_list_end = 0
    header_hash, image_hash = partition.compute_hashes()
    partition.add_certificate(b"test-cert1-" + name.encode("ascii"), "cert1")
    cert2 = partition.add_certificate(_cert2(header_hash, image_hash), "cert2")
    cert2.header.image_list_end = int(last)
    return partition


def _fixture() -> tuple[bytes, bytes, PatchCatalog]:
    payload = bytearray(b"\xCC" * 900)
    payload[40:45] = b"veify"
    payload[100:105] = b"veify"
    original_key = bytes((index % 251 for index in range(520)))
    replacement_key = bytes(((index + 17) % 251 for index in range(520)))
    payload[200:720] = original_key

    lk = _partition("lk", bytes(payload), last=False)
    other = _partition("tee", b"unchanged-non-lk-payload", last=True)
    source = bytes(lk) + bytes(other) + bytes(256) + bytes(range(1, 201))
    lk_payload_offset = int(lk.header.size)

    def record(
        patch_id: str,
        *,
        offset: int,
        original: bytes,
        patch: bytes | None = None,
        dynamic: str | None = None,
    ) -> PatchRecord:
        return PatchRecord(
            id=patch_id,
            human_name=patch_id,
            category="test",
            risk="test",
            summary="test",
            principle="test",
            ida_context="test",
            locators=(
                LocatorRecord(
                    kind="fixed",
                    offset=lk_payload_offset + offset,
                    original=original,
                    length=len(original),
                    partition="lk",
                ),
            ),
            patch_hex=patch.hex() if patch is not None else None,
            dynamic=dynamic,
            replace_len=len(original),
        )

    catalog = PatchCatalog(
        {
            "xingc-rename-veify-command": record(
                "xingc-rename-veify-command",
                offset=40,
                original=b"veify",
                patch=b"xingc",
            ),
            "xingc-rename-veify-log-marker": record(
                "xingc-rename-veify-log-marker",
                offset=100,
                original=b"veify",
                patch=b"xingc",
            ),
            "xingc-replace-hedgehog-public-key": record(
                "xingc-replace-hedgehog-public-key",
                offset=200,
                original=original_key,
                dynamic="hedgehog_public_key_blob",
            ),
        }
    )
    return source, replacement_key, catalog


def _patch_fixture(tmp_path: Path) -> tuple[Path, Path, bytes, PatchCatalog]:
    source, key, catalog = _fixture()
    original = tmp_path / "lk-original.img"
    output = tmp_path / "lk-patched.img"
    original.write_bytes(source)
    run_patch(original, output, key, catalog)
    return original, output, key, catalog


def test_exact_override_and_all_non_lk_content_are_accepted(tmp_path: Path) -> None:
    original, output, key, catalog = _patch_fixture(tmp_path)

    ok, steps = verify_files(original, output, key, catalog)

    assert ok, steps
    assert any("[OK] cert:lk-override" in step for step in steps)
    assert any("[OK] data:partition:tee" in step for step in steps)

    standalone_ok, standalone_steps = validate_patched_image(
        output,
        key,
        catalog,
    )
    assert standalone_ok, standalone_steps


def test_standalone_patched_validation_rejects_unpatched_input(tmp_path: Path) -> None:
    source, key, catalog = _fixture()
    original = tmp_path / "lk-original.img"
    original.write_bytes(source)

    ok, steps = validate_patched_image(original, key, catalog)

    assert not ok
    assert any(step.endswith(":original") for step in steps)


def test_unparsable_cert2_is_not_treated_as_a_valid_bypass(tmp_path: Path) -> None:
    original, output, key, catalog = _patch_fixture(tmp_path)
    data = bytearray(output.read_bytes())
    parsed = LibLkImage(data)
    cert2 = parsed.partitions["lk"].cert2
    assert cert2 is not None
    cert_offset = data.find(bytes(cert2.data))
    assert cert_offset >= 0
    data[cert_offset] ^= 0x01
    output.write_bytes(data)

    states = {item.partition: item.state for item in inspect_cert_status(bytes(data))}
    ok, steps = verify_files(original, output, key, catalog)

    assert states["lk"] == "unparsable"
    assert not ok
    assert any("[FAIL] cert:lk-override" in step for step in steps)


def test_non_lk_payload_tamper_is_rejected(tmp_path: Path) -> None:
    original, output, key, catalog = _patch_fixture(tmp_path)
    data = bytearray(output.read_bytes())
    parsed = LibLkImage(data)
    tee_payload = bytes(parsed.partitions["tee"].data)
    tee_offset = data.find(tee_payload)
    assert tee_offset >= 0
    data[tee_offset] ^= 0x01
    output.write_bytes(data)

    ok, steps = verify_files(original, output, key, catalog)

    assert not ok
    assert any("[FAIL] data:partition:tee" in step for step in steps)


def test_trailing_slot_bytes_are_preserved_at_absolute_offsets(tmp_path: Path) -> None:
    original, output, key, catalog = _patch_fixture(tmp_path)
    original_data = original.read_bytes()
    output_data = output.read_bytes()
    output_image = LibLkImage(output_data)
    output_used = max(
        item.end_offset
        for partition in output_image.partitions.values()
        for item in (partition, *partition.certs)
    )

    assert len(output_data) == len(original_data)
    assert output_data[output_used:] == original_data[output_used:]


def test_raw_image_without_zero_fill_allows_exact_cert2_growth(
    tmp_path: Path,
) -> None:
    source, key, catalog = _fixture()
    parsed = LibLkImage(source)
    used = max(
        item.end_offset
        for partition in parsed.partitions.values()
        for item in (partition, *partition.certs)
    )
    raw_source = source[:used]
    original = tmp_path / "lk-raw.img"
    output = tmp_path / "lk-raw-patched.img"
    original.write_bytes(raw_source)

    input_size, output_size, _signed, steps = run_patch(
        original,
        output,
        key,
        catalog,
    )

    assert output_size > input_size
    assert output_size - input_size <= MAX_RAW_IMAGE_GROWTH
    assert any(
        "bounded raw-image cert2 growth" in step
        for step in steps
    )
    ok, verify_steps = verify_files(original, output, key, catalog)
    assert ok, verify_steps


def test_zero_filled_slot_keeps_original_file_size(tmp_path: Path) -> None:
    source, key, catalog = _fixture()
    padded_source = source + bytes(4096)
    original = tmp_path / "lk-slot.img"
    output = tmp_path / "lk-slot-patched.img"
    original.write_bytes(padded_source)

    input_size, output_size, _signed, steps = run_patch(
        original,
        output,
        key,
        catalog,
    )

    assert output_size == input_size
    assert output.read_bytes()[-4096:] == bytes(4096)
    assert any("fits input image" in step for step in steps)


def test_structured_growth_rejects_nonzero_data_immediately_after_container(
    tmp_path: Path,
) -> None:
    source, key, catalog = _fixture()
    parsed = LibLkImage(source)
    used = max(
        item.end_offset
        for partition in parsed.partitions.values()
        for item in (partition, *partition.certs)
    )
    collided = bytearray(source)
    collided[used : used + 128] = b"X" * 128
    original = tmp_path / "lk-footer.img"
    output = tmp_path / "lk-footer-patched.img"
    original.write_bytes(collided)

    with pytest.raises(UblError, match="unsafe LK output size"):
        run_patch(original, output, key, catalog)
    assert not output.exists()


def test_raw_image_growth_over_safety_cap_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, key, catalog = _fixture()
    parsed = LibLkImage(source)
    used = max(
        item.end_offset
        for partition in parsed.partitions.values()
        for item in (partition, *partition.certs)
    )
    original = tmp_path / "lk-raw.img"
    output = tmp_path / "lk-oversized.img"
    original.write_bytes(source[:used])

    from nebumtk_unlock_tools.lk.vendor.liblk.structures.certificate import (
        Certificate,
    )

    original_builder = Certificate.build_hash_override_block

    def oversized_builder(self, header_hash, image_hash):
        return (
            original_builder(self, header_hash, image_hash)
            + b"X" * (MAX_RAW_IMAGE_GROWTH + 32)
        )

    monkeypatch.setattr(
        Certificate,
        "build_hash_override_block",
        oversized_builder,
    )

    with pytest.raises(UblError, match="unsafe LK output size"):
        run_patch(original, output, key, catalog)
    assert not output.exists()
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
