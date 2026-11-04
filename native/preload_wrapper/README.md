# preload wrapper

This directory contains the Android arm64 launcher for protected online and
offline `preload.so` payloads.

The payload remains AES-256-GCM encrypted inside the ELF wrapper. Plaintext is
written first to an anonymous sealed `memfd`. Reviewed `preload` profiles point
`LD_PRELOAD` to `/proc/self/fd/<n>` immediately before the trigger is executed;
reviewed `exec` profiles replace an exact `__NEBUMTK_PAYLOAD__` placeholder and
execute the anonymous ELF directly with fixed typed arguments. If an Android
SELinux policy rejects direct `memfd` execution with `EACCES`, `exec` mode copies
the verified image to an `O_TMPFILE` inode under `/data/local/tmp`, reopens that
unnamed inode read-only, and invokes `execveat(AT_EMPTY_PATH)`. The fallback has
no directory entry and is destroyed when its final descriptor closes. It never
falls back to a named plaintext payload. Profiles using `env -i` pass an exact
`LD_PRELOAD=__NEBUMTK_MEMFD__` placeholder, and the wrapper replaces only that
argument.

## Device-grant protocol

Each wrapper process creates a fresh X25519 key pair and challenge nonce and
emits one `NBCH1` line. Online builds relay the challenge to the server; offline
builds locally unwrap the CEK only after receiving the challenge. Both return an
`NBGR1` grant whose CEK is bound to the operation, payload, challenge and both
ephemeral public keys using X25519, HKDF-SHA256 and AES-GCM.

Only the live wrapper process has the ephemeral private key needed to unwrap the
CEK. It decrypts and executes once, wipes temporary secrets and exits. A retry
must launch a new process and obtain a new operation and grant. The device
deliberately does not trust its wall clock. Online expiry/replay is enforced by
the server; offline replay resistance comes from the new per-process challenge
and ephemeral private key. Production wrappers do not accept a raw CEK through
arguments or stdin.

## Vendored crypto sources

AES/GHASH sources are vendored from PyCryptodome 3.23.0:

- source archive SHA-256:
  `447700a657182d60338bab09fdb27518f8856aecd80ae4c6bdddb67ff5da44ef`
- required AES-256 and GHASH files only, with upstream license text

X25519 is vendored from official Monocypher 4.0.3:

- upstream archive and verification notes:
  `vendor/monocypher/UPSTREAM.md`
- license: BSD-2-Clause OR CC0-1.0
- only X25519 functions are referenced; link-time garbage collection removes
  unused sections

Vendoring avoids Android private `libcrypto` ABI and runtime network
dependencies.
