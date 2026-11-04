# Vendored PyCryptodome snippets

This directory contains a minimal subset of upstream PyCryptodome 3.23.0 C sources
used for AES block operations, SHA-256, and portable GHASH inside the Android preload wrapper.

Upstream archive:
- `pycryptodome-3.23.0.tar.gz`
- SHA-256: `447700a657182d60338bab09fdb27518f8856aecd80ae4c6bdddb67ff5da44ef`

Files copied here:
- `src/AES.c`
- `src/SHA256.c`
- `src/ghash_portable.c`
- `src/hash_SHA2_template.c`
- `src/block_base.h`
- `src/block_common.c`
- `src/common.h`
- `src/endianess.h`
- `src/errors.h`
- `licenses/LICENSE.rst`

No local modifications have been made yet.
