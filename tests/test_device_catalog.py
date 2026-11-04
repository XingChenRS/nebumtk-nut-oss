# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nebumtk_unlock_tools.core.device import ConnectedDevice, DeviceDetector
from nebumtk_unlock_tools.core.errors import DeviceMatchError
from nebumtk_unlock_tools.resources import catalog


def _make_pack(
    private: ed25519.Ed25519PrivateKey,
    content_key: bytes,
    *,
    revision: int,
) -> bytes:
    payload = catalog.canonical_json(
        {
            "schema": catalog.CATALOG_SCHEMA,
            "revision": revision,
            "minimum_client_version": "0.1.0",
            "source_sha256": "00" * 32,
            "devices": {
                "test-model": {
                    "brand": "vivo",
                    "display_name": "Test Model",
                    "match": {"brand": "vivo", "model": ["test"]},
                    "resources": {
                        "preload": "builtin:test-payload",
                        "preload_trigger": "/system/bin/id",
                    },
                    "partitions": {"backup": ["lk"]},
                    "post_unlock": {},
                }
            },
        }
    )
    nonce = os.urandom(12)
    ciphertext = AESGCM(content_key).encrypt(
        nonce,
        payload,
        catalog.PACK_AAD,
    )
    return catalog.canonical_json(
        {
            "schema": catalog.PACK_SCHEMA,
            "nonce": catalog.b64u_encode(nonce),
            "ciphertext": catalog.b64u_encode(ciphertext),
            "signature": catalog.b64u_encode(
                private.sign(catalog.PACK_AAD + nonce + ciphertext)
            ),
        }
    )


def test_signed_encrypted_catalog_verifies_without_plain_profile_text() -> None:
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    content_key = os.urandom(32)
    pack = _make_pack(private, content_key, revision=2)

    decoded = catalog.verify_pack(pack, public, content_key)

    assert decoded["revision"] == 2
    assert "test-model" in decoded["devices"]
    assert b"test-payload" not in pack


def test_catalog_tamper_fails_before_use() -> None:
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    content_key = os.urandom(32)
    pack = bytearray(_make_pack(private, content_key, revision=2))
    pack[len(pack) // 2] ^= 1

    with pytest.raises(catalog.CatalogError):
        catalog.verify_pack(bytes(pack), public, content_key)


def test_embedded_catalog_matches_source_profile_set() -> None:
    root = Path(__file__).resolve().parent.parent
    signed = catalog.load_device_catalog()
    source = DeviceDetector._load_profiles(root / "devices.yaml")
    loaded = DeviceDetector._profiles_from_mapping(signed["devices"])

    assert set(loaded) == set(source)
    for key in source:
        assert loaded[key].privilege_runtime == source[key].privilege_runtime
        assert loaded[key].partitions == source[key].partitions


def test_privilege_runtime_override_changes_only_temporary_privilege_material() -> None:
    root = Path(__file__).resolve().parent.parent
    profiles = DeviceDetector._load_profiles(root / "devices.yaml")
    target = profiles["vivox200"]
    runtime_source = profiles["vivopd2324"]

    combined = target.with_privilege_runtime(runtime_source.privilege_runtime)

    assert combined.key == target.key
    assert combined.match == target.match
    assert combined.partitions == target.partitions
    assert combined.privilege_runtime == runtime_source.privilege_runtime
    assert combined.preload_clean_env is True
    assert combined.su_path == "su"


def test_catalog_rejects_duplicate_exact_alias_within_brand() -> None:
    devices = {
        "one": {
            "brand": "vivo",
            "match": {"brand": "VIVO", "model": ["PD0001"]},
            "resources": {},
            "partitions": {},
            "post_unlock": {},
        },
        "two": {
            "brand": "vivo",
            "match": {"brand": "vivo", "model": ["pd0001"]},
            "resources": {},
            "partitions": {},
            "post_unlock": {},
        },
    }

    with pytest.raises(DeviceMatchError, match="重复精确 alias"):
        DeviceDetector._profiles_from_mapping(devices)


def test_device_match_rejects_multiple_matching_profiles() -> None:
    profiles = DeviceDetector._profiles_from_mapping(
        {
            "model-match": {
                "brand": "vivo",
                "match": {"brand": "vivo", "model": ["model-name"]},
                "resources": {},
                "partitions": {},
                "post_unlock": {},
            },
            "device-match": {
                "brand": "vivo",
                "match": {"brand": "vivo", "model": ["device-name"]},
                "resources": {},
                "partitions": {},
                "post_unlock": {},
            },
        }
    )
    detector = object.__new__(DeviceDetector)
    detector.profiles = profiles
    device = ConnectedDevice(
        serial="SERIAL",
        brand="vivo",
        model="model-name",
        device="device-name",
        firmware="firmware",
        slot_suffix="_a",
    )

    with pytest.raises(DeviceMatchError, match="同时匹配多个"):
        detector.match_profile(device)


def test_catalog_preload_material_stays_encrypted_until_grant_unwrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload_id = "external-protected"
    wrapper = b"\x7fELF" + os.urandom(512)
    expected_key = bytes(range(32))
    content_key = os.urandom(32)
    nonce = os.urandom(12)
    encrypted_key = AESGCM(content_key).encrypt(
        nonce,
        expected_key,
        catalog.PRELOAD_KEY_AAD_PREFIX + payload_id.encode("ascii"),
    )
    record = {
        "wrapper": catalog.b64u_encode(wrapper),
        "wrapper_sha256": __import__("hashlib").sha256(wrapper).hexdigest(),
        "plain_sha256": "11" * 32,
        "key_nonce": catalog.b64u_encode(nonce),
        "encrypted_key": catalog.b64u_encode(encrypted_key),
    }
    monkeypatch.setattr(
        catalog,
        "load_device_catalog",
        lambda: {"protected_preloads": {payload_id: record}},
    )
    monkeypatch.setattr(catalog, "embedded_preload_pack_key", lambda: content_key)
    monkeypatch.setattr(catalog.tempfile, "gettempdir", lambda: str(tmp_path))

    materialized = catalog.materialize_catalog_wrapper(payload_id)
    unwrapped = catalog.unwrap_catalog_payload_key(payload_id)

    assert materialized is not None
    assert materialized.read_bytes() == wrapper
    assert materialized.parent.name.startswith(f"catalog-{payload_id}-")
    assert bytes(unwrapped) == expected_key
    unwrapped[:] = b"\0" * len(unwrapped)


def test_model_pack_root_key_is_read_from_the_local_material_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = os.urandom(32)
    (tmp_path / "preload-pack.key").write_bytes(expected.hex().encode("ascii"))
    monkeypatch.setenv("NUT_MATERIAL_ROOT", str(tmp_path))

    assert catalog.embedded_preload_pack_key() == expected


def test_model_pack_root_key_reports_missing_local_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NUT_MATERIAL_ROOT", str(tmp_path))

    with pytest.raises(catalog.CatalogError, match="解密密钥不可用"):
        catalog.embedded_preload_pack_key()


def test_install_model_pack_requires_newer_signed_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    content_key = os.urandom(32)
    embedded = _make_pack(private, content_key, revision=1)
    update = _make_pack(private, content_key, revision=2)
    source = tmp_path / "update.nbmodel"
    source.write_bytes(update)
    destination = tmp_path / "installed"
    monkeypatch.setattr(catalog, "embedded_public_key", lambda: public)
    monkeypatch.setattr(catalog, "embedded_content_key", lambda: content_key)
    monkeypatch.setattr(catalog, "embedded_pack", lambda: embedded)
    monkeypatch.setattr(catalog, "model_pack_dir", lambda: destination)

    target, revision = catalog.install_model_pack(source)

    assert revision == 2
    assert target.read_bytes() == update


def test_model_pack_size_is_bounded_before_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "oversized.nbmodel"
    source.write_bytes(b"x" * 65)
    monkeypatch.setattr(catalog, "MAX_MODEL_PACK_BYTES", 64)

    with pytest.raises(catalog.CatalogError, match="大小无效"):
        catalog.install_model_pack(source)
