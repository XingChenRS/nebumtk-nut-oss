# SPDX-License-Identifier: AGPL-3.0-only
"""Build protected Android preload wrappers into the local material store.

The closed release embedded the wrappers and the RSA unwrap key inside the
Python package and gated the key behind an access password. This tool keeps the
wrapper bytes and the protocol identical but writes them where the open-source
build reads them:

``material/payloads/<id>/wrapper.so``   pushed to the device
``material/payloads/manifest.json``     hashes, OAEP label, wrapped CEK
``material/wrap-private.pem``           RSA key that unwraps a payload CEK

Input payloads come from ``preloads/<id>/preload.so`` (or ``--payload``).

    python tools/embed_preloads.py --regenerate
    python tools/embed_preloads.py            # verify the store against sources
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NATIVE_ROOT = ROOT / "native" / "preload_wrapper"
PAYLOAD_SOURCE_ROOT = ROOT / "preloads"
MATERIAL_ROOT = ROOT / "material"
PAYLOAD_STORE = MATERIAL_ROOT / "payloads"
MANIFEST = PAYLOAD_STORE / "manifest.json"
PRIVATE_KEY_FILE = MATERIAL_ROOT / "wrap-private.pem"
ANDROID_NDK_ENV = "ANDROID_NDK_HOME"
NDK_VERSION = "29.0.14206865"
ANDROID_API = 35
ARCH_ID = 1
MAGIC = b"NBWRAP1"
VERSION = 1
HEADER_FORMAT = "<7sBBHHHI32s12s"


def discover_payload_sources() -> dict[str, Path]:
    """Every ``preloads/<id>/preload.so`` in the tree, by payload id."""
    found: dict[str, Path] = {}
    if not PAYLOAD_SOURCE_ROOT.is_dir():
        return found
    for entry in sorted(PAYLOAD_SOURCE_ROOT.iterdir()):
        source = entry / "preload.so"
        if entry.is_dir() and source.is_file():
            found[entry.name] = source
    return found


def _native_build_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(b"NEBUMTK-WRAPPER-BUILD-v1\0")
    digest.update(f"ndk={NDK_VERSION};api={ANDROID_API}".encode("ascii"))
    inputs = [Path(__file__).resolve()]
    inputs.extend(
        path.resolve()
        for path in NATIVE_ROOT.rglob("*")
        if path.is_file()
    )
    for path in sorted(inputs, key=lambda item: item.as_posix().lower()):
        try:
            label = path.relative_to(ROOT).as_posix()
        except ValueError:
            label = path.name
        digest.update(b"\0")
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _find_ndk() -> Path:
    candidates: list[Path] = []
    configured = os.environ.get(ANDROID_NDK_ENV, "").strip()
    if configured:
        candidates.append(Path(configured))
    android_home = os.environ.get("ANDROID_HOME", "").strip()
    if android_home:
        candidates.append(Path(android_home) / "ndk" / NDK_VERSION)
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / "Android" / "Sdk" / "ndk" / NDK_VERSION)

    for candidate in candidates:
        compiler = _compiler_path(candidate)
        if compiler.is_file():
            return candidate
    checked = ", ".join(str(item) for item in candidates) or "(none)"
    raise RuntimeError(f"Android NDK {NDK_VERSION} not found; checked: {checked}")


def _toolchain_bin(ndk: Path) -> Path:
    prebuilt = ndk / "toolchains" / "llvm" / "prebuilt"
    candidates = (
        prebuilt / "windows-x86_64" / "bin",
        prebuilt / "linux-x86_64" / "bin",
        prebuilt / "darwin-x86_64" / "bin",
        prebuilt / "darwin-arm64" / "bin",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise RuntimeError(f"unsupported Android NDK host layout: {prebuilt}")


def _compiler_path(ndk: Path) -> Path:
    bin_dir = _toolchain_bin(ndk)
    command = bin_dir / f"aarch64-linux-android{ANDROID_API}-clang.cmd"
    if command.is_file():
        return command
    return bin_dir / f"aarch64-linux-android{ANDROID_API}-clang"


def _c_array(name: str, data: bytes) -> str:
    lines = []
    for offset in range(0, len(data), 12):
        chunk = data[offset : offset + 12]
        lines.append("    " + ", ".join(f"0x{value:02x}" for value in chunk) + ",")
    body = "\n".join(lines)
    return f"const uint8_t {name}[] = {{\n{body}\n}};"


def _payload_label(payload_id: str) -> bytes:
    return f"NEBUMTK-WRAP-v1:{payload_id}:arm64-v8a:{ANDROID_API}".encode("ascii")


def _build_payload_data(payload_id: str, source: Path, cek: bytes) -> tuple[str, str]:
    plain = source.read_bytes()
    plain_sha = hashlib.sha256(plain).digest()
    nonce = os.urandom(12)
    payload_id_bytes = payload_id.encode("ascii")
    if not payload_id_bytes or len(payload_id_bytes) > 96:
        raise ValueError(f"invalid payload id: {payload_id}")
    header = struct.pack(
        HEADER_FORMAT,
        MAGIC,
        VERSION,
        ARCH_ID,
        ANDROID_API,
        len(payload_id_bytes),
        0,
        len(plain),
        plain_sha,
        nonce,
    )
    encrypted = AESGCM(cek).encrypt(nonce, plain, header + payload_id_bytes)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    header_fields = ",\n".join(
        (
            f'    .magic = {{ {", ".join(str(value) for value in MAGIC)} }}',
            f"    .version = {VERSION}",
            f"    .arch = {ARCH_ID}",
            f"    .api_level = {ANDROID_API}",
            f"    .payload_id_len = {len(payload_id_bytes)}",
            "    .reserved = 0",
            f"    .plain_size = {len(plain)}",
            f'    .plain_sha256 = {{ {", ".join(f"0x{value:02x}" for value in plain_sha)} }}',
            f'    .nonce = {{ {", ".join(f"0x{value:02x}" for value in nonce)} }}',
        )
    )
    c_source = "\n\n".join(
        (
            '#include "wrapper_protocol.h"\n#include <stddef.h>\n#include <stdint.h>',
            f"const NebumtkWrapHeader g_nebumtk_header = {{\n{header_fields}\n}};",
            _c_array("g_nebumtk_payload_id", payload_id_bytes),
            _c_array("g_nebumtk_ciphertext", ciphertext),
            _c_array("g_nebumtk_tag", tag),
            f"const size_t g_nebumtk_ciphertext_len = {len(ciphertext)}U;",
        )
    )
    return c_source + "\n", hashlib.sha256(plain).hexdigest()


def _compile_wrapper(ndk: Path, payload_data: Path, output: Path) -> None:
    vendor = NATIVE_ROOT / "vendor" / "pycryptodome" / "src"
    command = [
        str(_compiler_path(ndk)),
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-parameter",
        "-fPIE",
        "-ffunction-sections",
        "-fdata-sections",
        "-fvisibility=hidden",
        "-fstack-protector-strong",
        "-D_FORTIFY_SOURCE=2",
        "-DNO_CPYTHON_MODULE",
        "-DHAVE_STDINT_H",
        "-DHAVE_POSIX_MEMALIGN",
        "-Wl,-pie",
        "-Wl,-z,relro",
        "-Wl,-z,now",
        "-Wl,--gc-sections",
        "-Wl,--build-id=none",
        "-I",
        str(NATIVE_ROOT),
        "-I",
        str(vendor),
        str(NATIVE_ROOT / "wrapper_main.c"),
        str(NATIVE_ROOT / "wrapper_crypto.c"),
        str(NATIVE_ROOT / "vendor" / "monocypher" / "monocypher.c"),
        str(vendor / "AES.c"),
        str(vendor / "ghash_portable.c"),
        str(vendor / "SHA256.c"),
        str(payload_data),
        "-o",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise RuntimeError(
            "wrapper build failed:\n"
            + " ".join(command)
            + "\n"
            + (completed.stdout or "")
            + (completed.stderr or "")
        )


def _write_private_key(private_key: rsa.RSAPrivateKey) -> None:
    MATERIAL_ROOT.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY_FILE.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        PRIVATE_KEY_FILE.chmod(0o600)
    except OSError:
        pass


def _load_or_create_private_key() -> rsa.RSAPrivateKey:
    """Reuse the operator's unwrap key; generate one on first build."""
    if PRIVATE_KEY_FILE.is_file():
        key = serialization.load_pem_private_key(
            PRIVATE_KEY_FILE.read_bytes(), password=None
        )
        if not isinstance(key, rsa.RSAPrivateKey):
            raise RuntimeError(f"unwrap key must be RSA: {PRIVATE_KEY_FILE}")
        return key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    _write_private_key(private_key)
    return private_key


def regenerate() -> None:
    sources = discover_payload_sources()
    if not sources:
        raise FileNotFoundError(
            f"no payload sources under {PAYLOAD_SOURCE_ROOT}/*/preload.so"
        )
    ndk = _find_ndk()
    private_key = _load_or_create_private_key()
    public_key = private_key.public_key()
    PAYLOAD_STORE.mkdir(parents=True, exist_ok=True)

    records: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="nebumtk-wrapper-build-") as tmp:
        build_dir = Path(tmp)
        for payload_id, source in sources.items():
            cek = os.urandom(32)
            payload_data_source, plain_sha256 = _build_payload_data(payload_id, source, cek)
            payload_data = build_dir / f"{payload_id}_payload.c"
            wrapper = build_dir / f"{payload_id}_wrapper.so"
            payload_data.write_text(payload_data_source, encoding="utf-8")
            _compile_wrapper(ndk, payload_data, wrapper)
            label = _payload_label(payload_id)
            wrapped_key = public_key.encrypt(
                cek,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=label,
                ),
            )
            wrapper_bytes = wrapper.read_bytes()
            if source.read_bytes() in wrapper_bytes:
                raise RuntimeError(f"plaintext preload found inside wrapper: {payload_id}")
            target_dir = PAYLOAD_STORE / payload_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "wrapper.so"
            target.write_bytes(wrapper_bytes)
            records[payload_id] = {
                "plain_sha256": plain_sha256,
                "wrapper_sha256": hashlib.sha256(wrapper_bytes).hexdigest(),
                "oaep_label": base64.b64encode(label).decode("ascii"),
                "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
            }
            mutable_cek = bytearray(cek)
            for index in range(len(mutable_cek)):
                mutable_cek[index] = 0

    MANIFEST.write_text(
        json.dumps(
            {
                "version": 1,
                "native_build_sha256": _native_build_fingerprint(),
                "payloads": records,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def verify_existing() -> None:
    if not MANIFEST.is_file():
        raise FileNotFoundError(
            f"payload store manifest missing: {MANIFEST}; run with --regenerate"
        )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    stored = manifest.get("payloads")
    if not isinstance(stored, dict) or not stored:
        raise RuntimeError("payload store manifest lists no payloads")
    if manifest.get("native_build_sha256") != _native_build_fingerprint():
        raise RuntimeError(
            "protected preload native wrapper is stale; run with --regenerate"
        )
    sources = discover_payload_sources()
    for payload_id, record in stored.items():
        source = sources.get(payload_id)
        if source is not None:
            if record.get("plain_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
                raise RuntimeError(
                    f"protected preload is stale for {payload_id}; run with --regenerate"
                )
        wrapper_path = PAYLOAD_STORE / payload_id / "wrapper.so"
        if not wrapper_path.is_file():
            raise RuntimeError(f"missing wrapper: {wrapper_path}")
        wrapper = wrapper_path.read_bytes()
        if not wrapper.startswith(b"\x7fELF"):
            raise RuntimeError(f"protected wrapper is not ELF: {payload_id}")
        if hashlib.sha256(wrapper).hexdigest() != record.get("wrapper_sha256"):
            raise RuntimeError(f"protected wrapper hash mismatch: {payload_id}")
    if not PRIVATE_KEY_FILE.is_file():
        raise RuntimeError(f"missing unwrap key: {PRIVATE_KEY_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="build wrappers for every preloads/<id>/preload.so into the store",
    )
    args = parser.parse_args()
    if args.regenerate:
        regenerate()
    else:
        verify_existing()


if __name__ == "__main__":
    main()
