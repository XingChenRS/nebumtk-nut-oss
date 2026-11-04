# Offline Release Design

This document is the current implementation contract for the offline release. It defines product and security boundaries; it is not a development history.

## 1. Product boundary

The offline release contains the Nuitka client (no access password), the signed model/privilege-runtime catalog, authenticated preload wrappers supplied by the operator from `material/payloads/`, offline Xingc material read from `material/xingc/`, and the reviewed SakiSU Windows host patcher shipped plainly under `sakisu/`. There is no packer stage: the audited standalone directory is transported in a plain RAR5 SFX.

Every protected resource is operator material, resolved by `nebumtk_unlock_tools/keymaterial.py` from an explicit root, `NUT_MATERIAL_ROOT`, or `<project>/material`. Nothing secret ships in the repository, and a missing file fails with `MaterialError` naming the path.

Persistent Root management is not part of the product. Do not bundle or expose KernelSU, a SakiSU/ReSuki/Magisk manager, manager APKs, `libksud.so`, or a persistent-root flashing or install flow. The one approved exception is the documented SakiSU `init_boot` image patch boundary: it ships its reviewed Windows host patcher under `sakisu/` (hash-pinned at load) and only produces a verified local candidate image. `init_boot` and `vendor_boot` remain valid original-device backup entries where a profile declares them. scrcpy is not a release component.

Temporary privilege escalation remains an internal prerequisite for verified backup and Android system-side partition writes. Its protected preload and short-lived `su` channel must not be described or reused as a persistent Root feature.

Offline and online are separate build targets. Online work is frozen and has no KSU/manager artifact route; an offline build must not contain a switch, configuration or fallback that enables the online channel.

## 2. Protected preload execution

Each maintained SO/ELF is encrypted with AES-256-GCM and packed into an authenticated Android arm64 wrapper. Plain payloads and reusable CEKs are operator inputs, never loose release files; the wrapper and its wrapped CEK live in `material/payloads/`.

Every attempt uses this lifecycle:

1. Push an encrypted wrapper to a randomized device path and start a new process with `-s -`.
2. The wrapper creates a fresh X25519 key and nonce and emits an `NBCH1` bound to the expected payload ID.
3. The client unwraps that payload's CEK with the operator's local RSA key (`material/wrap-private.pem`) and creates one HKDF-SHA256/AES-GCM `NBGR1` bound to the challenge, both ephemeral keys, operation ID and grant metadata.
4. Only `NBGR1` crosses ADB. Raw CEKs and legacy 64-hex-key inputs are forbidden.
5. The wrapper authenticates and decrypts the payload into an anonymous fd/inode, applies the typed runtime and executes once. A named plaintext fallback is forbidden.
6. Success, error and cancellation paths remove the remote wrapper and exact temporary logs, close plaintext mappings/descriptors and wipe mutable sensitive buffers.

A retry is a new authorization attempt: new process, X25519 key, nonce, challenge and grant. Challenge, grant, CEK, private material, full trigger command and sensitive remote paths must not enter UI activity text or persistent logs.

The CEK necessarily exists briefly on the operator's Windows endpoint because there is no server. Keep its lifetime inside the grant callback and wipe it, but do not claim protection against an administrator or debugger that controls the endpoint, or a root operator that instruments the device. There is no packer layer to raise that cost.

## 3. Signed catalog and runtime schema

`devices.yaml` is maintainer-side input. A release loads an encrypted, Ed25519-signed built-in catalog and may import only signed, version-compatible, non-colliding `.nbmodel` updates. Edited loose YAML/JSON is never executable release policy.

Each signed device profile currently contains identity, partition and runtime source fields. Catalog loading converts the runtime portion to an immutable typed `PrivilegeRuntime`; identical complete values are deduplicated in the test-mode selector, so one reviewed runtime value can serve several profiles without duplicating UI choices. This is object-level separation, not yet a separate top-level `runtimes` table in the signed catalog.

The implemented runtime contract contains only these typed fields:

```text
preload_ref = builtin:<payload_id>
remote_wrapper_path
execution_mode = preload | exec
trigger_argv[]
payload_argv[]
environment_mode = inherit | clean
environment[]
su_strategy = shell_c
su_path
```

Arbitrary shell fragments are not accepted as executable catalog policy. Trigger commands are parsed into argv, executables use absolute paths, and environment names, remote paths, arguments and the single implemented `shell_c` strategy pass fixed validation. Retry/reconnect policy remains bounded client code rather than remotely editable catalog data. A new model currently requires coordinated edits to `devices.yaml` and the protected payload registry, followed by signed-catalog regeneration/verification, protected-resource hash/fingerprint tests and real-device runtime acceptance; the presence of a payload file is insufficient.

Offline signed packs support maintainable local model updates, not remote revocation. A user can retain an older offline executable or pack, so rollback cannot be made absolute without resuming the online service.

## 4. User flows and destructive boundaries

The five UI task groups are Device Overview, Preparation, Fastboot Partitions, Brand Functions and Advanced Tools.

- Preparation contains temporary elevation, backup and system-side inactive-slot LK writing.
- Fastboot Partitions contains patched-LK dual-slot synchronization and manifest-bound original-LK restore.
- Brand Functions contains Vivo complete unlock, local Vivo LK patch/Xingc operations and OPPO unlock.
- Advanced Tools exposes diagnostics and, only in test mode, explicit signed runtime selection.

Vivo complete unlock is one strict worker: temporary elevation, durable backup export, patch the just-backed-up LK, inactive-slot write/switch, Xingc unlock, second-slot synchronization, restore the original active slot and wait for Android. It has no persistent Root tail. A durable desktop export failure stops the workflow before its first device write.

Every destructive boundary revalidates the exact serial, transport/submode, active slot, target partition, live capacity, cancellation and frozen source size/SHA-256. Manual Vivo writes also validate the reviewed patched-LK/Xingc structure. A raw LK's file length is not partition capacity; only structurally verified cert2 growth within the bounded allowance is accepted, followed by a live target-capacity check. Partial dual-slot flash forbids reboot.

Desktop exports contain `backup-manifest.json` with schema/version, profile, ADB serial, initial slot and per-partition filename/size/SHA-256. Fastboot restore requires the current profile and serial to match and the chosen LK to match the manifest. The manifest records provenance and integrity; it does not authorize a later worker.

## 5. Test mode

Test mode is persisted but offline-only, visibly warned and confirmed before device work. It may decouple the manually selected signed device profile from the signed protected runtime, relax brand/model/firmware/payload compatibility, and run the complete Vivo workflow — which is the only path that performs the finishing steps, and therefore the reason test mode exists. While test mode is on, an automatic scan match does not overrule a template the operator picked by hand.

Test mode never bypasses exact-device selection, ADB/Fastboot state, slot and partition checks, live capacity, file structure and hash, cancellation, final-boundary revalidation, operation-specific unlock requirements, or Xingc/LK cryptographic and structural checks. It must not become a plaintext arbitrary-payload loader.

## 6. Release pipeline

The accepted order is:

1. Run focused tests and the complete `tests/` suite.
2. Build the Offline Nuitka standalone distribution and audit its trusted release manifest, protected-resource isolation, forbidden files and channel marker.
3. Audit the standalone directory: no loose payload, no Xingc private material, no editable catalog, no debug symbols, no packer intermediate (`.vmp`, `.map`, `.pdb`, `*.vmp.exe`), no symlink or reparse point in the audited paths.
4. Build the plain RAR5 SFX into a random temporary file; archive-test, actually extract and repeat the distribution audit before atomically replacing the requested output.

Nuitka and SFX raise nothing except packaging convenience: they are not trusted execution, and no document may claim they prevent dumping or tampering. Production redistribution may warrant code signing if SmartScreen reputation matters.

No release is complete merely because an older file exists under `dist/`. Record fresh test/build/audit output for the exact source revision being delivered; do not carry forward old hashes or artifact claims.
