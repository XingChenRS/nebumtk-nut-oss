Monocypher 4.0.3

- Source: https://monocypher.org/download/monocypher-4.0.3.tar.gz
- Archive verification: official `.sha512` sidecar from the same download page
- `monocypher.c` SHA-256: `57eb914fc88136119bd41655cccb8c250048bf54d470540625186f8ab16f64be`
- `monocypher.h` SHA-256: `c494da712122da7ff679fdcf318a5317e84972b6c950fe9d896212947797facd`
- License: BSD-2-Clause OR CC0-1.0; see `LICENCE.md`

Only the X25519 functions are referenced by the preload wrapper. Link-time
garbage collection removes unused Monocypher sections.
