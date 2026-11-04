# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Local device-bound grants for protected offline preload execution."""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..core.device_grant import (
    build_grant_aad,
    format_device_grant,
    parse_device_challenge,
)


def _wipe(value: bytearray | None) -> None:
    if value is not None:
        value[:] = b"\0" * len(value)


class OfflineGrantResponder:
    """Unwrap one CEK only after a fresh Android process challenges us."""

    GRANT_LIFETIME_SECONDS = 60

    def __init__(
        self,
        payload_id: str,
        unwrap_payload_key: Callable[[str], bytearray],
    ) -> None:
        self.payload_id = payload_id
        self._unwrap_payload_key = unwrap_payload_key

    def __call__(self, challenge_line: str, target: str) -> str:
        if not str(target).strip():
            raise ValueError("missing selected ADB target")
        challenge = parse_device_challenge(
            challenge_line,
            expected_payload_id=self.payload_id,
        )
        device_public = x25519.X25519PublicKey.from_public_bytes(
            challenge.public_key
        )
        ephemeral = x25519.X25519PrivateKey.generate()
        responder_public = ephemeral.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        operation_id = secrets.token_hex(16)
        expires_at = int(time.time()) + self.GRANT_LIFETIME_SECONDS
        aad = build_grant_aad(
            operation_id=operation_id,
            payload_id=self.payload_id,
            challenge_nonce=challenge.nonce,
            device_public_key=challenge.public_key,
            server_public_key=responder_public,
            expires_at=expires_at,
        )

        content_key: bytearray | None = None
        shared: bytearray | None = None
        wrapping_key: bytearray | None = None
        try:
            content_key = self._unwrap_payload_key(self.payload_id)
            if not isinstance(content_key, bytearray) or len(content_key) != 32:
                raise ValueError("invalid protected preload key")
            shared = bytearray(ephemeral.exchange(device_public))
            wrapping_key = bytearray(
                HKDF(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=challenge.nonce,
                    info=aad,
                ).derive(shared)
            )
            nonce = os.urandom(12)
            ciphertext = AESGCM(wrapping_key).encrypt(
                nonce,
                content_key,
                aad,
            )
            return format_device_grant(
                operation_id=operation_id,
                expires_at=expires_at,
                server_public_key=responder_public,
                nonce=nonce,
                ciphertext=ciphertext,
            )
        finally:
            _wipe(content_key)
            _wipe(shared)
            _wipe(wrapping_key)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
