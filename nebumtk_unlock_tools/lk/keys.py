# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Local Xingc key and capability material.

The closed release decrypted these from password-derived ciphertext embedded in
``lk/embedded_xingc.py``. The open-source build reads the operator's own files
straight from the material root:

``<material>/xingc/protocol-permit``      marker file that enables Xingc work
``<material>/xingc/verify-public.avbpub`` AVB public key blob (libavb format)
``<material>/xingc/verify-private.pem``   matching PEM private key
``<material>/xingc/patch-catalog.json``   reviewed LK patch catalog

None of this is distributable material: every operator supplies their own.
"""

from __future__ import annotations

from ..keymaterial import read_material
from .avbkey import AvbPublicKeyBlob, private_key_pem_to_avb_blob

XINGC_DIR = "xingc"
PROTOCOL_PERMIT = f"{XINGC_DIR}/protocol-permit"
PUBLIC_BLOB = f"{XINGC_DIR}/verify-public.avbpub"
PRIVATE_PEM = f"{XINGC_DIR}/verify-private.pem"
PATCH_CATALOG = f"{XINGC_DIR}/patch-catalog.json"
PROTOCOL_PERMIT_MARKER = b"NEBUMTK-XINGC-PROTOCOL-PERMIT-v1"


def require_protocol_capability() -> None:
    permit = bytearray(read_material(PROTOCOL_PERMIT, label="Xingc 协议许可"))
    try:
        if bytes(permit).strip() != PROTOCOL_PERMIT_MARKER:
            raise RuntimeError("Xingc 协议许可验证失败")
    finally:
        permit[:] = b"\0" * len(permit)


def load_public_blob() -> bytes:
    blob = read_material(PUBLIC_BLOB, label="Xingc AVB 公钥")
    AvbPublicKeyBlob.from_bytes(blob)
    return blob


def load_private_pem() -> bytes:
    protected = load_private_pem_buffer()
    try:
        return bytes(protected)
    finally:
        protected[:] = b"\0" * len(protected)


def load_private_pem_buffer() -> bytearray:
    return bytearray(read_material(PRIVATE_PEM, label="Xingc 私钥"))


def load_patch_catalog_bytes() -> bytes:
    return read_material(PATCH_CATALOG, label="LK 补丁目录")


def verify_key_pair() -> None:
    expected = load_public_blob()
    derived = private_key_pem_to_avb_blob(load_private_pem())
    if derived != expected:
        raise ValueError("本地 Xingc 私钥与 AVB public blob 不匹配")
