"""Canonical device challenge and one-shot preload grant wire format."""

from __future__ import annotations

import re
from dataclasses import dataclass


CHALLENGE_VERSION = "NBCH1"
GRANT_VERSION = "NBGR1"
_PAYLOAD_ID = r"[A-Za-z0-9_.-]{1,80}"
_CHALLENGE_RE = re.compile(
    rf"^{CHALLENGE_VERSION}:({_PAYLOAD_ID}):([0-9a-f]{{64}}):([0-9a-f]{{64}})$"
)


@dataclass(frozen=True)
class DeviceChallenge:
    payload_id: str
    nonce: bytes
    public_key: bytes
    line: str


def parse_device_challenge(line: str, *, expected_payload_id: str) -> DeviceChallenge:
    normalized = str(line).strip()
    match = _CHALLENGE_RE.fullmatch(normalized)
    if match is None:
        raise ValueError("invalid device challenge")
    payload_id, nonce_hex, public_hex = match.groups()
    if payload_id != expected_payload_id:
        raise ValueError("device challenge payload mismatch")
    nonce = bytes.fromhex(nonce_hex)
    public_key = bytes.fromhex(public_hex)
    if nonce == bytes(32) or public_key == bytes(32):
        raise ValueError("invalid device challenge entropy")
    return DeviceChallenge(payload_id, nonce, public_key, normalized)


def build_grant_aad(
    *,
    operation_id: str,
    payload_id: str,
    challenge_nonce: bytes,
    device_public_key: bytes,
    server_public_key: bytes,
    expires_at: int,
) -> bytes:
    return "|".join(
        (
            "NEBULA-DEVICE-GRANT-v1",
            operation_id,
            payload_id,
            challenge_nonce.hex(),
            device_public_key.hex(),
            server_public_key.hex(),
            str(expires_at),
        )
    ).encode("ascii")


def format_device_grant(
    *,
    operation_id: str,
    expires_at: int,
    server_public_key: bytes,
    nonce: bytes,
    ciphertext: bytes,
) -> str:
    if (
        re.fullmatch(r"[0-9a-f]{32}", operation_id) is None
        or not 0 < expires_at < 10**20
        or len(server_public_key) != 32
        or len(nonce) != 12
        or len(ciphertext) != 48
    ):
        raise ValueError("invalid device grant")
    return ":".join(
        (
            GRANT_VERSION,
            operation_id,
            str(expires_at),
            server_public_key.hex(),
            nonce.hex(),
            ciphertext.hex(),
        )
    )
