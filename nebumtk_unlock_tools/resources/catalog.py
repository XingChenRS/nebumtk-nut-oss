# SPDX-License-Identifier: AGPL-3.0-only
"""Signed offline device catalog and local model-pack handling."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..build_profile import CLIENT_VERSION


CATALOG_SCHEMA = 1
PACK_SCHEMA = 1
PACK_AAD = b"NEBUMTK-DEVICE-CATALOG-v1"
PRELOAD_KEY_AAD_PREFIX = b"NEBUMTK-CATALOG-PRELOAD-v1:"
PRELOAD_PACK_KEY_DOMAIN = "catalog.preload-pack.v1"
PRELOAD_PACK_KEY_AAD = b"NEBUMTK-CATALOG-PRELOAD-PACK-KEY-v1"
MAX_MODEL_PACK_BYTES = 64 * 1024 * 1024
MAX_WRAPPER_BYTES = 16 * 1024 * 1024


class CatalogError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def b64u_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64u_decode(value: str) -> bytes:
    raw = str(value).strip().encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def _version(value: str) -> tuple[int, int, int]:
    parts = str(value).split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise CatalogError("机型目录版本格式无效")
    return tuple(int(part) for part in parts)


def validate_catalog_payload(payload: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError("机型目录内容无效") from exc
    if canonical_json(decoded) != payload:
        raise CatalogError("机型目录不是规范编码")
    if not isinstance(decoded, dict) or decoded.get("schema") != CATALOG_SCHEMA:
        raise CatalogError("机型目录协议不受支持")
    revision = decoded.get("revision")
    minimum_client = decoded.get("minimum_client_version")
    devices = decoded.get("devices")
    protected_preloads = decoded.get("protected_preloads", {})
    if (
        type(revision) is not int
        or revision < 1
        or not isinstance(minimum_client, str)
        or not isinstance(devices, dict)
        or not devices
        or not isinstance(protected_preloads, dict)
    ):
        raise CatalogError("机型目录字段无效")
    if _version(CLIENT_VERSION) < _version(minimum_client):
        raise CatalogError("机型目录要求更高版本客户端")
    for key, record in devices.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(record, dict)
            or not isinstance(record.get("resources", {}), dict)
            or not isinstance(record.get("partitions", {}), dict)
        ):
            raise CatalogError("机型目录设备记录无效")
    for payload_id, record in protected_preloads.items():
        if (
            not isinstance(payload_id, str)
            or not payload_id
            or len(payload_id) > 80
            or any(not (char.isalnum() or char in "-_.") for char in payload_id)
            or not isinstance(record, dict)
            or not all(
                isinstance(record.get(field), str) and record.get(field)
                for field in (
                    "wrapper",
                    "wrapper_sha256",
                    "plain_sha256",
                    "key_nonce",
                    "encrypted_key",
                )
            )
        ):
            raise CatalogError("机型目录保护资源记录无效")
    return decoded


def verify_pack(
    pack: bytes,
    public_key_raw: bytes,
    content_key: bytes,
) -> dict[str, Any]:
    if not pack or len(pack) > MAX_MODEL_PACK_BYTES:
        raise CatalogError("机型包大小无效")
    if len(public_key_raw) != 32:
        raise CatalogError("机型目录验签公钥无效")
    if len(content_key) != 32:
        raise CatalogError("机型目录解密密钥无效")
    try:
        envelope = json.loads(pack.decode("utf-8"))
        if not isinstance(envelope, dict) or envelope.get("schema") != PACK_SCHEMA:
            raise CatalogError("机型包协议不受支持")
        nonce = b64u_decode(envelope["nonce"])
        ciphertext = b64u_decode(envelope["ciphertext"])
        signature = b64u_decode(envelope["signature"])
        if canonical_json(envelope) != pack:
            raise CatalogError("机型包不是规范编码")
        ed25519.Ed25519PublicKey.from_public_bytes(public_key_raw).verify(
            signature,
            PACK_AAD + nonce + ciphertext,
        )
        payload = AESGCM(content_key).decrypt(
            nonce,
            ciphertext,
            PACK_AAD,
        )
    except CatalogError:
        raise
    except (KeyError, TypeError, ValueError, InvalidSignature, InvalidTag) as exc:
        raise CatalogError("机型包签名校验失败") from exc
    return validate_catalog_payload(payload)


def embedded_public_key() -> bytes:
    from .embedded_catalog import PUBLIC_KEY_B64

    key = b64u_decode(PUBLIC_KEY_B64)
    if len(key) != 32:
        raise CatalogError("内置机型目录公钥无效")
    return key


def embedded_content_key() -> bytes:
    from .embedded_catalog import CONTENT_KEY_B64

    key = b64u_decode(CONTENT_KEY_B64)
    if len(key) != 32:
        raise CatalogError("内置机型目录解密密钥无效")
    return key


def embedded_preload_pack_key() -> bytes:
    """Local symmetric key that wraps model-pack payload CEKs."""
    from ..keymaterial import MaterialError, read_secret_key

    try:
        return read_secret_key("preload-pack.key", label="机型包主体密钥")
    except MaterialError as exc:
        raise CatalogError("机型包资源解密密钥不可用") from exc


def embedded_pack() -> bytes:
    from .embedded_catalog import CATALOG_PACK_B85

    return base64.b85decode("".join(CATALOG_PACK_B85).encode("ascii"))


def model_pack_dir() -> Path:
    appdata = os.environ.get("APPDATA", "").strip()
    root = Path(appdata) / "NEBUMTK" if appdata else Path.home() / ".nebumtk"
    return root / "model-packs"


def _read_model_pack(path: Path) -> bytes:
    size = path.stat().st_size
    if size <= 0 or size > MAX_MODEL_PACK_BYTES:
        raise CatalogError("机型包大小无效")
    with path.open("rb") as stream:
        raw = stream.read(MAX_MODEL_PACK_BYTES + 1)
    if len(raw) != size or len(raw) > MAX_MODEL_PACK_BYTES:
        raise CatalogError("机型包读取校验失败")
    return raw


def load_device_catalog() -> dict[str, Any]:
    public_key = embedded_public_key()
    content_key = embedded_content_key()
    selected = verify_pack(embedded_pack(), public_key, content_key)
    selected_revision = int(selected["revision"])
    directory = model_pack_dir()
    if directory.is_dir():
        for candidate in sorted(directory.glob("*.nbmodel")):
            try:
                decoded = verify_pack(
                    _read_model_pack(candidate),
                    public_key,
                    content_key,
                )
            except (OSError, CatalogError):
                continue
            revision = int(decoded["revision"])
            if revision > selected_revision:
                selected = decoded
                selected_revision = revision
    return selected


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
            AESGCM(embedded_preload_pack_key()).decrypt(
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


def install_model_pack(source: Path) -> tuple[Path, int]:
    raw = _read_model_pack(Path(source))
    public_key = embedded_public_key()
    content_key = embedded_content_key()
    decoded = verify_pack(raw, public_key, content_key)
    embedded = verify_pack(embedded_pack(), public_key, content_key)
    revision = int(decoded["revision"])
    if revision <= int(embedded["revision"]):
        raise CatalogError("机型包版本不高于内置目录")
    directory = model_pack_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"catalog-{revision}.nbmodel"
    pending = target.with_suffix(".tmp")
    pending.write_bytes(raw)
    if pending.read_bytes() != raw:
        pending.unlink(missing_ok=True)
        raise CatalogError("机型包写入校验失败")
    pending.replace(target)
    return target, revision
