from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from nebumtk_unlock_tools.core.device_grant import build_grant_aad
from nebumtk_unlock_tools.resources.offline_grant import OfflineGrantResponder


PAYLOAD_ID = "offline-protected-payload"


def _challenge(device_private: x25519.X25519PrivateKey, nonce: bytes) -> str:
    public = device_private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return f"NBCH1:{PAYLOAD_ID}:{nonce.hex()}:{public.hex()}"


def test_offline_grant_encrypts_cek_to_one_device_challenge_and_wipes_source() -> None:
    expected = bytes(range(32))
    unwrapped = bytearray(expected)
    responder = OfflineGrantResponder(PAYLOAD_ID, lambda _payload: unwrapped)
    device_private = x25519.X25519PrivateKey.generate()
    challenge_nonce = bytes(range(32, 64))
    device_public = device_private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )

    grant = responder(_challenge(device_private, challenge_nonce), "ADB-SERIAL")

    fields = grant.split(":")
    assert len(fields) == 6
    assert fields[0] == "NBGR1"
    operation_id = fields[1]
    expires_at = int(fields[2])
    responder_public_raw = bytes.fromhex(fields[3])
    nonce = bytes.fromhex(fields[4])
    ciphertext = bytes.fromhex(fields[5])
    aad = build_grant_aad(
        operation_id=operation_id,
        payload_id=PAYLOAD_ID,
        challenge_nonce=challenge_nonce,
        device_public_key=device_public,
        server_public_key=responder_public_raw,
        expires_at=expires_at,
    )
    shared = device_private.exchange(
        x25519.X25519PublicKey.from_public_bytes(responder_public_raw)
    )
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=challenge_nonce,
        info=aad,
    ).derive(shared)

    assert AESGCM(key).decrypt(nonce, ciphertext, aad) == expected
    assert unwrapped == bytearray(32)


def test_offline_grant_rejects_wrong_payload_before_unwrapping() -> None:
    called = False

    def unwrap(_payload: str) -> bytearray:
        nonlocal called
        called = True
        return bytearray(32)

    responder = OfflineGrantResponder(PAYLOAD_ID, unwrap)
    private = x25519.X25519PrivateKey.generate()
    challenge = _challenge(private, bytes(range(32, 64))).replace(
        PAYLOAD_ID,
        "wrong-payload",
        1,
    )

    with pytest.raises(ValueError, match="payload mismatch"):
        responder(challenge, "ADB-SERIAL")
    assert called is False


def test_offline_grant_requires_selected_adb_target() -> None:
    responder = OfflineGrantResponder(PAYLOAD_ID, lambda _payload: bytearray(32))
    private = x25519.X25519PrivateKey.generate()

    with pytest.raises(ValueError, match="selected ADB target"):
        responder(_challenge(private, bytes(range(32, 64))), "")


def test_production_sources_have_no_plaintext_preload_key_transport() -> None:
    root = Path(__file__).resolve().parent.parent
    client = (root / "nebumtk_unlock_tools" / "core" / "root.py").read_text(
        encoding="utf-8"
    )
    wrapper = (root / "native" / "preload_wrapper" / "wrapper_main.c").read_text(
        encoding="utf-8"
    )

    assert "payload_key" not in client
    assert "key_hex" not in client
    assert "parse_hex_key" not in wrapper
    assert "<64-hex-key>" not in wrapper
    assert "!parse_device_grant(" in wrapper
