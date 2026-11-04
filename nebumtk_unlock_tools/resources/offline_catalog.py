# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Model-pack preload unwrap support for the offline product.

A signed model pack may ship its own encrypted payload wrappers. The pack body
is already signed; the wrapper key comes from the operator's local material root
(``<material>/preload-pack.key``) instead of an access password.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..keymaterial import MaterialError, read_secret_key
from .catalog import (
    MAX_WRAPPER_BYTES,
    CatalogError,
    b64u_decode,
    load_device_catalog,
)


PRELOAD_KEY_AAD_PREFIX = b"NEBUMTK-CATALOG-PRELOAD-v1:"
PRELOAD_PACK_KEY_NAME = "preload-pack.key"


def preload_pack_key() -> bytes:
    """Read the local symmetric key that wraps model-pack payload CEKs."""
    try:
        return read_secret_key(PRELOAD_PACK_KEY_NAME, label="机型包主体密钥")
    except MaterialError as exc:
        raise CatalogError("机型包资源解密密钥不可用") from exc


def _catalog_preload_record(preload_id: str) -> dict[str, str] | None:
    records = load_device_catalog().get("protected_preloads", {})
    record = records.get(str(preload_id)) if isinstance(records, dict) else None
    return dict(record) if isinstance(record, dict) else None


def materialize_catalog_wrapper(preload_id: str) -> Path | None:
    record = _catalog_preload_record(preload_id)
    if record is None:
        return None
    try:
        wrapper = b64u_decode(record["wrapper"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CatalogError("机型包 wrapper 编码无效") from exc
    if (
        not wrapper.startswith(b"\x7fELF")
        or len(wrapper) > MAX_WRAPPER_BYTES
        or hashlib.sha256(wrapper).hexdigest() != record["wrapper_sha256"]
    ):
        raise CatalogError("机型包 wrapper 完整性校验失败")
    root = Path(tempfile.gettempdir()) / "NEBUMTK" / "protected_preloads"
    root.mkdir(parents=True, exist_ok=True)
    operation_root = Path(
        tempfile.mkdtemp(prefix=f"catalog-{preload_id}-", dir=root)
    )
    target = operation_root / "nebumtk_wrapper.so"
    try:
        target.write_bytes(wrapper)
        if hashlib.sha256(target.read_bytes()).hexdigest() != record["wrapper_sha256"]:
            raise CatalogError("机型包 wrapper 写入校验失败")
        try:
            operation_root.chmod(0o700)
            target.chmod(0o700)
        except OSError:
            pass
        return target
    except Exception:
        target.unlink(missing_ok=True)
        try:
            operation_root.rmdir()
        except OSError:
            pass
        raise


def unwrap_catalog_payload_key(preload_id: str) -> bytearray:
    record = _catalog_preload_record(preload_id)
    if record is None:
        raise KeyError(f"unknown catalog preload: {preload_id}")
    try:
        nonce = b64u_decode(record["key_nonce"])
        encrypted = b64u_decode(record["encrypted_key"])
        key = bytearray(
            AESGCM(preload_pack_key()).decrypt(
                nonce,
                encrypted,
                PRELOAD_KEY_AAD_PREFIX + preload_id.encode("ascii"),
            )
        )
    except (KeyError, TypeError, ValueError, InvalidTag) as exc:
        raise CatalogError("机型包 preload 密钥解封失败") from exc
    if len(key) != 32:
        key[:] = b"\0" * len(key)
        raise CatalogError("机型包 preload 密钥长度无效")
    return key
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
