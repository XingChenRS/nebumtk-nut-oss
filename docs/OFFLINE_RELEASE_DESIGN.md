# Offline Release Design

This document is the current implementation contract for the offline release. It defines product and security boundaries; it is not a development history.

## 1. Product boundary

The offline release contains the Nuitka client (no access password), signed model/privilege-runtime catalog, authenticated preload wrappers, offline Xingc material, and the SakiSU host patcher supplied by the operator. There is no packer stage: the audited standalone directory is transported in a plain RAR5 SFX, and every protected resource comes from the operator's local material root (see `docs/MATERIAL.md`).

Persistent Root management is not part of the product. Do not bundle or expose KernelSU, SakiSU, ReSuki, Magisk, KMI selection, manager APKs, boot-image patching or persistent-root flashing. `init_boot` and `vendor_boot` remain valid original-device backup entries where a profile declares them; they are never patch/install inputs. scrcpy is not a release component.

Temporary privilege escalation remains an internal prerequisite for verified backup and Android system-side partition writes. Its protected preload and short-lived `su` channel must not be described or reused as a persistent Root feature.

Offline and online are separate build targets. Online work is frozen and has no KSU/manager artifact route; an offline build must not contain a switch, configuration or fallback that enables the online channel.

## 2. Protected preload execution

Each maintained SO/ELF is encrypted with AES-256-GCM and embedded in an authenticated Android arm64 wrapper. Plain payloads and reusable CEKs are build inputs, never loose release files.

Every attempt uses this lifecycle:

1. Push an encrypted wrapper to a randomized device path and start a new process with `-s -`.
2. The wrapper creates a fresh X25519 key and nonce and emits an `NBCH1` bound to the expected payload ID.
3. The client unwraps that payload's CEK and creates one HKDF-SHA256/AES-GCM `NBGR1` bound to the challenge, both ephemeral keys, operation ID and grant metadata.
4. Only `NBGR1` crosses ADB. Raw CEKs and legacy 64-hex-key inputs are forbidden.
5. The wrapper authenticates and decrypts the payload into an anonymous fd/inode, applies the typed runtime and executes once. A named plaintext fallback is forbidden.
6. Success, error and cancellation paths remove the remote wrapper and exact temporary logs, close plaintext mappings/descriptors and wipe mutable sensitive buffers.

A retry is a new authorization attempt: new process, X25519 key, nonce, challenge and grant. Challenge, grant, CEK, private material, full trigger command and sensitive remote paths must not enter UI activity text or persistent logs.

The CEK necessarily exists briefly on an authorized Windows endpoint because there is no server. Keep its lifetime inside the grant callback and but do not claim protection against an administrator/debugger that controls the endpoint or a root operator that instruments the device.

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

Test mode is persisted but offline-only, visibly warned and confirmed before device work. It may decouple the manually selected signed device profile from the signed protected runtime and relax brand/model/firmware/payload compatibility. The complete Vivo workflow remains strict/disabled; compatibility work uses explicit component flows.

Test mode never bypasses exact-device selection, ADB/Fastboot state, slot and partition checks, live capacity, file structure and hash, cancellation, final-boundary revalidation, operation-specific unlock requirements, or Xingc/LK cryptographic and structural checks. It must not become a plaintext arbitrary-payload loader.

## 6. Release pipeline

The accepted order is:

1. Run focused tests and the complete `tests/` suite.
2. Build the Offline Nuitka standalone distribution and audit its trusted release manifest, protected-resource isolation, forbidden files and channel marker.
3. Audit the standalone directory: no loose payload, no Xingc private material, no editable catalog, no debug symbols, no packer intermediate (`.vmp`, `.map`, `.pdb`, `*.vmp.exe`), no symlink or reparse point in the audited paths.
4. Build the plain RAR5 SFX into a random temporary file; archive-test, actually extract and repeat the distribution audit before atomically replacing the requested output.

Nuitka and SFX are packaging convenience, not trusted execution: no document may claim they prevent dumping or tampering.

No release is complete merely because an older file exists under `dist/`. Record fresh test/build/audit output for the exact source revision being delivered; do not carry forward old hashes or artifact claims.
