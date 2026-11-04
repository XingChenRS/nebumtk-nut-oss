# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import struct
from dataclasses import dataclass


AVB_RSA2048_PUBLIC_KEY_BLOB_SIZE = 520
RSA2048_BITS = 2048
RSA_PUBLIC_EXPONENT = 65537


@dataclass(frozen=True)
class AvbPublicKeyBlob:
    data: bytes
    num_bits: int
    n0inv: int
    modulus: bytes
    rr: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "AvbPublicKeyBlob":
        if len(data) != AVB_RSA2048_PUBLIC_KEY_BLOB_SIZE:
            raise ValueError("AVB RSA-2048 public key blob must be exactly 520 bytes")
        num_bits, n0inv = struct.unpack(">II", data[:8])
        if num_bits != RSA2048_BITS:
            raise ValueError(f"expected RSA-2048 AVB key, got {num_bits} bits")
        modulus_len = num_bits // 8
        modulus = data[8 : 8 + modulus_len]
        rr = data[8 + modulus_len : 8 + modulus_len * 2]
        if len(modulus) != 256 or len(rr) != 256:
            raise ValueError("malformed AVB RSA-2048 key body")
        modulus_int = int.from_bytes(modulus, "big")
        if modulus_int.bit_length() != RSA2048_BITS or modulus_int % 2 == 0:
            raise ValueError("invalid AVB RSA-2048 modulus")
        expected_n0inv = (-pow(modulus_int, -1, 1 << 32)) & 0xFFFFFFFF
        if n0inv != expected_n0inv:
            raise ValueError("invalid AVB RSA n0inv")
        expected_rr = pow(2, RSA2048_BITS * 2, modulus_int).to_bytes(
            modulus_len,
            "big",
        )
        if rr != expected_rr:
            raise ValueError("invalid AVB RSA Montgomery rr")
        return cls(data=data, num_bits=num_bits, n0inv=n0inv, modulus=modulus, rr=rr)


def private_key_pem_to_avb_blob(private_pem: bytes) -> bytes:
    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(private_pem, password=None)
    return public_key_to_avb_blob(private_key.public_key())


def public_key_to_avb_blob(public_key: object) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import rsa

    if not isinstance(public_key, rsa.RSAPublicKey):
        raise TypeError("expected an RSA public key")
    numbers = public_key.public_numbers()
    modulus = numbers.n
    if modulus.bit_length() != RSA2048_BITS:
        raise ValueError("expected an RSA-2048 public key")
    modulus_len = RSA2048_BITS // 8
    n0inv = ((1 << 32) - pow(modulus, -1, 1 << 32)) & 0xFFFFFFFF
    rr = pow(2, RSA2048_BITS * 2, modulus)
    blob = (
        struct.pack(">II", RSA2048_BITS, n0inv)
        + modulus.to_bytes(modulus_len, "big")
        + rr.to_bytes(modulus_len, "big")
    )
    AvbPublicKeyBlob.from_bytes(blob)
    return blob
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
