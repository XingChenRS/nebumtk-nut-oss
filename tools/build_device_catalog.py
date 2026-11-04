# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Generate and verify the signed embedded device catalog."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nebumtk_unlock_tools.build_profile import CLIENT_VERSION
from nebumtk_unlock_tools.resources.catalog import (
    CATALOG_SCHEMA,
    PACK_AAD,
    PACK_SCHEMA,
    b64u_encode,
    canonical_json,
    verify_pack,
)
from nebumtk_unlock_tools.resources.offline_catalog import PRELOAD_KEY_AAD_PREFIX

SOURCE = ROOT / "devices.yaml"
OUTPUT = ROOT / "nebumtk_unlock_tools" / "resources" / "embedded_catalog.py"
DEFAULT_KEY = ROOT / "local-dev" / "catalog_signing_private.pem"
DEFAULT_CONTENT_KEY = ROOT / "local-dev" / "catalog_content_key.bin"
DEFAULT_PRELOAD_KEY = ROOT / "local-dev" / "catalog_preload_key.bin"
# Runtime material root. The pack key is a local file, not a password-derived
# envelope: src/nebumtk_unlock_tools/resources/offline_catalog.py reads it here.
MATERIAL_PRELOAD_KEY = ROOT / "material" / "preload-pack.key"


def _key_path(raw: str) -> Path:
    configured = raw.strip() or os.environ.get(
        "NEBUMTK_CATALOG_SIGNING_KEY",
        "",
    ).strip()
    return Path(configured) if configured else DEFAULT_KEY


def _init_key(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    private = ed25519.Ed25519PrivateKey.generate()
    path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _content_key_path(raw: str) -> Path:
    configured = raw.strip() or os.environ.get(
        "NEBUMTK_CATALOG_CONTENT_KEY",
        "",
    ).strip()
    return Path(configured) if configured else DEFAULT_CONTENT_KEY


def _preload_key_path(raw: str) -> Path:
    configured = raw.strip() or os.environ.get(
        "NEBUMTK_CATALOG_PRELOAD_KEY",
        "",
    ).strip()
    return Path(configured) if configured else DEFAULT_PRELOAD_KEY


def _init_content_key(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(32))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _load_content_key(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(
            f"catalog content key missing: {path}; run --init-key once"
        )
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("catalog content key must contain 32 bytes")
    return key


def _load_private(path: Path) -> ed25519.Ed25519PrivateKey:
    if not path.is_file():
        raise FileNotFoundError(
            f"catalog signing key missing: {path}; run --init-key once"
        )
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("catalog signing key must be Ed25519")
    return key


def _payload(
    revision: int,
    protected_preloads: dict[str, dict[str, str]] | None = None,
) -> bytes:
    raw = yaml.safe_load(SOURCE.read_text(encoding="utf-8")) or {}
    devices = raw.get("devices")
    if not isinstance(devices, dict) or not devices:
        raise ValueError("devices.yaml has no device records")
    # JSON canonicalization sorts object keys. Convert environment maps to an
    # explicit pair list first so model-specific launch order is preserved.
    for record in devices.values():
        if not isinstance(record, dict):
            raise ValueError("devices.yaml contains an invalid device record")
        resources = record.get("resources", {})
        if not isinstance(resources, dict):
            raise ValueError("devices.yaml contains invalid resources")
        environment = resources.pop("preload_env", None)
        if environment is not None:
            if not isinstance(environment, dict):
                raise ValueError("devices.yaml contains invalid preload_env")
            resources["preload_env_entries"] = [
                [str(name), str(value)]
                for name, value in environment.items()
            ]
    return canonical_json(
        {
            "schema": CATALOG_SCHEMA,
            "revision": revision,
            "minimum_client_version": CLIENT_VERSION,
            "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "devices": devices,
            "protected_preloads": protected_preloads or {},
        }
    )


def _pack(
    private: ed25519.Ed25519PrivateKey,
    payload: bytes,
    content_key: bytes,
) -> bytes:
    nonce = os.urandom(12)
    ciphertext = AESGCM(content_key).encrypt(nonce, payload, PACK_AAD)
    return canonical_json(
        {
            "schema": PACK_SCHEMA,
            "nonce": b64u_encode(nonce),
            "ciphertext": b64u_encode(ciphertext),
            "signature": b64u_encode(
                private.sign(PACK_AAD + nonce + ciphertext)
            ),
        }
    )


def _render_module(
    public_raw: bytes,
    content_key: bytes,
    pack: bytes,
) -> str:
    encoded = base64.b85encode(pack).decode("ascii")
    chunks = "\n".join(
        f'    "{part}"'
        for part in textwrap.wrap(encoded, width=100)
    )
    return (
        '"""Generated signed device catalog. Do not edit."""\n\n'
        f'PUBLIC_KEY_B64 = "{b64u_encode(public_raw)}"\n'
        f'CONTENT_KEY_B64 = "{b64u_encode(content_key)}"\n'
        "CATALOG_PACK_B85 = (\n"
        f"{chunks}\n"
        ")\n"
    )


def _install_preload_key(key: bytes) -> None:
    """Publish the model-pack preload key into the local material root."""
    MATERIAL_PRELOAD_KEY.parent.mkdir(parents=True, exist_ok=True)
    MATERIAL_PRELOAD_KEY.write_bytes(key)
    try:
        MATERIAL_PRELOAD_KEY.chmod(0o600)
    except OSError:
        pass


def regenerate(
    path: Path,
    content_key_path: Path,
    preload_key_path: Path,
    revision: int,
    pack_output: Path | None,
) -> None:
    private = _load_private(path)
    content_key = _load_content_key(content_key_path)
    preload_pack_key = _load_content_key(preload_key_path)
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    pack = _pack(private, _payload(revision), content_key)
    OUTPUT.write_text(
        _render_module(
            public_raw,
            content_key,
            pack,
        ),
        encoding="utf-8",
    )
    _install_preload_key(preload_pack_key)
    if pack_output is not None:
        pack_output.parent.mkdir(parents=True, exist_ok=True)
        pack_output.write_bytes(pack)


def _parse_preload_specs(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        payload_id, separator, raw_path = value.partition("=")
        payload_id = payload_id.strip()
        source = Path(raw_path.strip())
        if (
            not separator
            or not payload_id
            or len(payload_id) > 80
            or any(not (char.isalnum() or char in "-_.") for char in payload_id)
            or not source.is_file()
        ):
            raise ValueError(f"invalid --preload specification: {value}")
        if payload_id in parsed:
            raise ValueError(f"duplicate preload id: {payload_id}")
        parsed[payload_id] = source
    return parsed


def _build_external_preloads(
    sources: dict[str, Path],
    preload_pack_key: bytes,
) -> dict[str, dict[str, str]]:
    if not sources:
        return {}
    from tools.embed_preloads import (
        _build_payload_data,
        _compile_wrapper,
        _find_ndk,
    )

    records: dict[str, dict[str, str]] = {}
    ndk = _find_ndk()
    with tempfile.TemporaryDirectory(prefix="nebumtk-model-pack-") as raw:
        build_root = Path(raw)
        for payload_id, source in sources.items():
            cek = bytearray(os.urandom(32))
            try:
                c_source, plain_sha256 = _build_payload_data(
                    payload_id,
                    source,
                    cek,
                )
                payload_data = build_root / f"{payload_id}_payload.c"
                wrapper_path = build_root / f"{payload_id}_wrapper.so"
                payload_data.write_text(c_source, encoding="utf-8")
                _compile_wrapper(ndk, payload_data, wrapper_path)
                wrapper = wrapper_path.read_bytes()
                plain = source.read_bytes()
                if plain in wrapper:
                    raise RuntimeError(
                        f"plaintext preload found in wrapper: {payload_id}"
                    )
                key_nonce = os.urandom(12)
                encrypted_key = AESGCM(preload_pack_key).encrypt(
                    key_nonce,
                    cek,
                    PRELOAD_KEY_AAD_PREFIX + payload_id.encode("ascii"),
                )
                records[payload_id] = {
                    "wrapper": b64u_encode(wrapper),
                    "wrapper_sha256": hashlib.sha256(wrapper).hexdigest(),
                    "plain_sha256": plain_sha256,
                    "key_nonce": b64u_encode(key_nonce),
                    "encrypted_key": b64u_encode(encrypted_key),
                }
            finally:
                cek[:] = b"\0" * len(cek)
    return records


def build_external_pack(
    key_path: Path,
    content_key_path: Path,
    preload_key_path: Path,
    revision: int,
    output: Path,
    preload_specs: list[str],
) -> None:
    private = _load_private(key_path)
    content_key = _load_content_key(content_key_path)
    preload_pack_key = _load_content_key(preload_key_path)
    protected = _build_external_preloads(
        _parse_preload_specs(preload_specs),
        preload_pack_key,
    )
    pack = _pack(
        private,
        _payload(revision, protected),
        content_key,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pack)
    _install_preload_key(preload_pack_key)


def verify_existing() -> None:
    if not OUTPUT.is_file():
        raise FileNotFoundError("embedded device catalog is missing")
    namespace: dict[str, object] = {}
    exec(OUTPUT.read_text(encoding="utf-8"), namespace)
    legacy = [
        field
        for field in (
            "PRELOAD_PACK_KEY_B64",
            "PRELOAD_PACK_KEY_SALT_B64",
            "PRELOAD_PACK_KEY_NONCE_B64",
            "PRELOAD_PACK_KEY_CIPHERTEXT_B64",
        )
        if field in namespace
    ]
    if legacy:
        raise RuntimeError(
            "embedded catalog still carries a password-derived preload-pack "
            f"envelope ({', '.join(legacy)}); run --regenerate"
        )
    if not MATERIAL_PRELOAD_KEY.is_file():
        raise RuntimeError(
            f"local preload-pack key is missing: {MATERIAL_PRELOAD_KEY}; "
            "run --regenerate"
        )
    if len(MATERIAL_PRELOAD_KEY.read_bytes()) != 32:
        raise RuntimeError(
            f"local preload-pack key must be 32 bytes: {MATERIAL_PRELOAD_KEY}"
        )
    public_raw = __import__(
        "nebumtk_unlock_tools.resources.catalog",
        fromlist=["b64u_decode"],
    ).b64u_decode(str(namespace["PUBLIC_KEY_B64"]))
    content_key = __import__(
        "nebumtk_unlock_tools.resources.catalog",
        fromlist=["b64u_decode"],
    ).b64u_decode(str(namespace["CONTENT_KEY_B64"]))
    pack = base64.b85decode(
        "".join(namespace["CATALOG_PACK_B85"]).encode("ascii")
    )
    decoded = verify_pack(pack, public_raw, content_key)
    expected_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if decoded.get("source_sha256") != expected_hash:
        raise RuntimeError("embedded device catalog is stale")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", default="")
    parser.add_argument("--content-key", default="")
    parser.add_argument("--preload-key", default="")
    parser.add_argument("--init-key", action="store_true")
    parser.add_argument("--regenerate", action="store_true")
    parser.add_argument("--external-pack", action="store_true")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--pack-output", type=Path)
    parser.add_argument(
        "--preload",
        action="append",
        default=[],
        metavar="ID=PATH",
    )
    args = parser.parse_args()
    key_path = _key_path(args.private_key)
    content_key_path = _content_key_path(args.content_key)
    preload_key_path = _preload_key_path(args.preload_key)
    if args.init_key:
        _init_key(key_path)
        _init_content_key(content_key_path)
        _init_content_key(preload_key_path)
    if args.external_pack:
        if args.pack_output is None:
            raise ValueError("--external-pack requires --pack-output")
        if args.revision < 1:
            raise ValueError("catalog revision must be positive")
        build_external_pack(
            key_path,
            content_key_path,
            preload_key_path,
            args.revision,
            args.pack_output,
            args.preload,
        )
    elif args.regenerate:
        if args.preload:
            raise ValueError("--preload is valid only with --external-pack")
        if args.revision < 1:
            raise ValueError("catalog revision must be positive")
        regenerate(
            key_path,
            content_key_path,
            preload_key_path,
            args.revision,
            args.pack_output,
        )
    else:
        verify_existing()


if __name__ == "__main__":
    main()
