# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Contract for the open-source material layer.

The closed release gated every protected resource behind an access password.
These tests pin the replacement: no password surface at all, and material comes
from an explicit local root whose absence is reported, never guessed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from nebumtk_unlock_tools import keymaterial
from nebumtk_unlock_tools.resources import payload_store

ROOT = Path(__file__).resolve().parents[1]


def test_the_access_password_surface_is_gone() -> None:
    assert importlib.util.find_spec("nebumtk_unlock_tools.security") is None

    from nebumtk_unlock_tools import app

    for name in (
        "request_access_password",
        "ACCESS_MAX_ATTEMPTS",
    ):
        assert not hasattr(app, name)

    entry = (ROOT / "nebumtk_unlock_tools" / "app.py").read_text(encoding="utf-8")
    assert "访问密码" not in entry
    assert "unlock_access_password" not in entry


def test_material_root_follows_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(keymaterial.MATERIAL_ROOT_ENV, str(tmp_path))
    assert keymaterial.material_root() == tmp_path
    assert keymaterial.payload_root() == tmp_path / "payloads"

    monkeypatch.delenv(keymaterial.MATERIAL_ROOT_ENV)
    assert keymaterial.project_material_root(ROOT) == ROOT / "material"


def test_missing_material_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(keymaterial.MaterialError, match="缺少本地材料"):
        keymaterial.read_material("absent.bin", root=tmp_path)


def test_secret_key_accepts_hex_and_rejects_wrong_length(tmp_path: Path) -> None:
    key = bytes(range(32))
    (tmp_path / "pack.key").write_bytes(key.hex().encode("ascii"))
    assert keymaterial.read_secret_key("pack.key", root=tmp_path) == key

    (tmp_path / "short.key").write_bytes(b"too-short")
    with pytest.raises(keymaterial.MaterialError, match="长度不是 32 字节"):
        keymaterial.read_secret_key("short.key", root=tmp_path)


def _write_store(root: Path, wrapper: bytes, *, declared: str | None = None) -> None:
    payload_dir = root / "payloads" / "demo_payload"
    payload_dir.mkdir(parents=True)
    (payload_dir / "wrapper.so").write_bytes(wrapper)
    (root / "payloads" / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "native_build_sha256": "00" * 32,
                "payloads": {
                    "demo_payload": {
                        "plain_sha256": "11" * 32,
                        "wrapper_sha256": declared
                        or hashlib.sha256(wrapper).hexdigest(),
                        "oaep_label": "bGFiZWw=",
                        "wrapped_key": "d3JhcHBlZA==",
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_payload_store_lists_and_materializes_a_verified_wrapper(
    tmp_path: Path,
) -> None:
    wrapper = b"\x7fELF" + bytes(128)
    _write_store(tmp_path, wrapper)

    assert payload_store.list_preloads(tmp_path) == ("demo_payload",)
    materialized = payload_store.materialize_wrapper("demo_payload", tmp_path)
    try:
        assert materialized.read_bytes() == wrapper
        assert materialized.parent.name.startswith("demo_payload-")
    finally:
        materialized.unlink(missing_ok=True)
        materialized.parent.rmdir()


def test_payload_store_rejects_a_wrapper_that_does_not_match_its_hash(
    tmp_path: Path,
) -> None:
    _write_store(tmp_path, b"\x7fELF" + bytes(64), declared="22" * 32)

    with pytest.raises(keymaterial.MaterialError, match="完整性校验失败"):
        payload_store.materialize_wrapper("demo_payload", tmp_path)


def test_payload_store_reports_an_unknown_payload(tmp_path: Path) -> None:
    (tmp_path / "payloads").mkdir()
    (tmp_path / "payloads" / "manifest.json").write_text(
        json.dumps({"payloads": {}}), encoding="utf-8"
    )

    with pytest.raises(KeyError, match="unknown protected preload"):
        payload_store.materialize_wrapper("absent", tmp_path)


def test_xingc_material_is_read_as_plaintext_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nebumtk_unlock_tools.lk import keys

    monkeypatch.setenv(keymaterial.MATERIAL_ROOT_ENV, str(tmp_path))
    xingc = tmp_path / "xingc"
    xingc.mkdir()
    (xingc / "protocol-permit").write_bytes(keys.PROTOCOL_PERMIT_MARKER + b"\n")
    (xingc / "patch-catalog.json").write_text('{"patches": []}', encoding="utf-8")

    keys.require_protocol_capability()
    assert keys.load_patch_catalog_bytes() == b'{"patches": []}'

    (xingc / "protocol-permit").write_bytes(b"not-a-permit")
    with pytest.raises(RuntimeError, match="协议许可验证失败"):
        keys.require_protocol_capability()
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
