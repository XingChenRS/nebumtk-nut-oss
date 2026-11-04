# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Operator-owned protected preload store.

The closed release embedded every encrypted wrapper and the RSA private key
inside the package, and unlocked the key with an access password. This build
keeps the on-device protocol exactly as it was — encrypted wrapper, X25519
device challenge, HKDF/AES-GCM grant — but the material lives on disk:

``<material>/payloads/manifest.json``
    ``{"native_build_sha256": "...", "payloads": {<id>: {plain_sha256,
    wrapper_sha256, oaep_label, wrapped_key}}}``
``<material>/payloads/<id>/wrapper.so``
    the encrypted Android wrapper that is pushed to the device
``<material>/wrap-private.pem``
    the RSA private key that unwraps a payload CEK

Nothing here is a secret of the repository: an operator builds this store from
their own payloads with ``tools/embed_preloads.py``. A missing store raises
:class:`~nebumtk_unlock_tools.keymaterial.MaterialError` naming the path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..keymaterial import MaterialError, material_root, payload_root, read_material

MANIFEST_NAME = "manifest.json"
WRAPPER_NAME = "wrapper.so"
PRIVATE_KEY_NAME = "wrap-private.pem"


def manifest_path(root: Path | None = None) -> Path:
    return payload_root(root) / MANIFEST_NAME


def load_manifest(root: Path | None = None, *, required: bool = False) -> dict:
    path = manifest_path(root)
    if not path.is_file():
        if required:
            raise MaterialError(
                f"缺少 preload 清单: {path}（用 tools/embed_preloads.py 生成）"
            )
        return {"payloads": {}}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialError(f"preload 清单无法解析: {path}") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("payloads", {}), dict):
        raise MaterialError(f"preload 清单结构无效: {path}")
    decoded.setdefault("payloads", {})
    return decoded


def list_preloads(root: Path | None = None) -> tuple[str, ...]:
    return tuple(sorted(load_manifest(root)["payloads"]))


def native_build_sha256(root: Path | None = None) -> str:
    return str(load_manifest(root).get("native_build_sha256", ""))


def _record(preload_id: str, root: Path | None = None) -> dict:
    record = load_manifest(root)["payloads"].get(preload_id)
    if not isinstance(record, dict):
        raise KeyError(
            f"unknown protected preload: {preload_id}"
            f"（在 {manifest_path(root)} 中登记并放好 {WRAPPER_NAME}）"
        )
    return record


def wrapper_path(preload_id: str, root: Path | None = None) -> Path:
    _record(preload_id, root)
    return payload_root(root) / preload_id / WRAPPER_NAME


def _cache_root() -> Path:
    return Path(tempfile.gettempdir()) / "NEBUMTK" / "protected_preloads"


def materialize_wrapper(preload_id: str, root: Path | None = None) -> Path:
    """Copy the stored wrapper to an operation-private temp file."""
    record = _record(preload_id, root)
    source = wrapper_path(preload_id, root)
    if not source.is_file():
        raise MaterialError(f"缺少 preload wrapper: {source}")
    data = source.read_bytes()
    expected = str(record.get("wrapper_sha256", ""))
    if not data.startswith(b"\x7fELF"):
        raise MaterialError(f"preload wrapper 不是 ELF: {source}")
    if not expected or hashlib.sha256(data).hexdigest() != expected:
        raise MaterialError(f"preload wrapper 完整性校验失败: {source}")
    cache = _cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    operation = Path(tempfile.mkdtemp(prefix=f"{preload_id}-", dir=cache))
    dest = operation / "nebumtk_wrapper.so"
    try:
        dest.write_bytes(data)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
            raise MaterialError("preload wrapper 写入校验失败")
        try:
            operation.chmod(0o700)
            dest.chmod(0o700)
        except OSError:
            pass
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        try:
            operation.rmdir()
        except OSError:
            pass
        raise


def load_unwrap_key(root: Path | None = None) -> rsa.RSAPrivateKey:
    path = material_root(root) / PRIVATE_KEY_NAME
    if not path.is_file():
        raise MaterialError(
            f"缺少 preload 解封私钥: {path}（用 tools/embed_preloads.py 生成）"
        )
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:
        raise MaterialError(f"preload 解封私钥无法解析: {path}") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise MaterialError(f"preload 解封私钥必须是 RSA: {path}")
    return key


def unwrap_payload_key(preload_id: str, root: Path | None = None) -> bytearray:
    """Unwrap one payload CEK with the operator's local RSA private key."""
    record = _record(preload_id, root)
    try:
        wrapped = base64.b64decode(str(record["wrapped_key"]).encode("ascii"))
        label = base64.b64decode(str(record["oaep_label"]).encode("ascii"))
    except (KeyError, TypeError, ValueError) as exc:
        raise MaterialError(f"preload 密钥记录无效: {preload_id}") from exc
    key = load_unwrap_key(root).decrypt(
        wrapped,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=label,
        ),
    )
    if len(key) != 32:
        raise MaterialError(f"preload 密钥长度无效: {preload_id}")
    return bytearray(key)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
