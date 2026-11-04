# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

from .catalog import PatchCatalog
from .certs import inspect_cert_status
from .lk_image import LkImage
from .patcher import PatchError, apply_plan, build_plan
from .validator import validate_output
from .xingc_locator import locate_patch

from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage as LibLkImage
from nebumtk_unlock_tools.lk.vendor.liblk.structures.certificate import Certificate


class UblError(RuntimeError):
    pass


def _context_override_end(raw: bytes) -> int:
    if len(raw) < 2 or raw[0] != 0xA0:
        raise UblError("LK cert2 override block missing")
    first = raw[1]
    if first < 0x80:
        header_size = 2
        content_size = first
    else:
        length_size = first & 0x7F
        if length_size == 0 or length_size > 4 or len(raw) < 2 + length_size:
            raise UblError("LK cert2 override length invalid")
        encoded = raw[2 : 2 + length_size]
        if encoded[0] == 0:
            raise UblError("LK cert2 override length is not canonical")
        header_size = 2 + length_size
        content_size = int.from_bytes(encoded, "big")
        if content_size < 0x80:
            raise UblError("LK cert2 override length is not canonical")
    end = header_size + content_size
    if end >= len(raw):
        raise UblError("LK cert2 override has no original certificate")
    return end


def validate_patched_image(
    path: str | Path,
    key_blob: bytes,
    catalog: PatchCatalog,
    *,
    image_profile: str | None = None,
) -> tuple[bool, list[str]]:
    try:
        image = LkImage.from_path(path)
        rows = site_statuses(image, key_blob, catalog, image_profile=image_profile)
        steps = [f"patch:{patch_id}:{state}" for patch_id, state, _ in rows]
        if any(state != "patched" for _, state, _ in rows):
            return False, steps

        parsed = LibLkImage(image.data)
        lk = parsed.partitions.get("lk")
        if lk is None or lk.cert2 is None:
            return False, [*steps, "cert:lk:missing"]
        cert2_data = bytes(lk.cert2.data)
        override_end = _context_override_end(cert2_data)
        original_cert_data = cert2_data[override_end:]
        original_cert = Certificate.from_bytes(original_cert_data)
        header_hash, image_hash = lk.compute_hashes()
        expected = (
            original_cert.build_hash_override_block(header_hash, image_hash)
            + original_cert_data
        )
        if cert2_data != expected:
            return False, [*steps, "cert:lk:override-mismatch"]

        for status in inspect_cert_status(image.data):
            if status.partition == "lk":
                continue
            if status.state not in {"valid", "missing-cert2"}:
                return False, [*steps, f"cert:{status.partition}:{status.state}"]
        return True, [*steps, "cert:lk:override-exact"]
    except Exception as exc:
        return False, [f"patched-lk:{exc}"]


def site_statuses(
    image: LkImage,
    key_blob: bytes,
    catalog: PatchCatalog,
    *,
    image_profile: str | None = None,
) -> list[tuple[str, str, int | None]]:
    ranges = image.partition_search_ranges()
    rows: list[tuple[str, str, int | None]] = []
    for patch in catalog.patches:
        replacement = key_blob if patch.dynamic else patch.patch_bytes
        st = locate_patch(
            image.data,
            patch,
            replacement=replacement,
            search_ranges=ranges,
            image_profile=image_profile,
        )
        rows.append((patch.id, st.state, st.offset))
    return rows


def preflight_check(
    path: str | Path,
    key_blob: bytes,
    catalog: PatchCatalog,
    *,
    image_profile: str | None = None,
) -> tuple[bool, str]:
    try:
        image = LkImage.from_path(path)
    except Exception as exc:
        return False, f"parse failed: {exc}"

    for patch_id, state, _ in site_statuses(
        image,
        key_blob,
        catalog,
        image_profile=image_profile,
    ):
        if state != "original":
            return False, f"{patch_id}: {state}"

    for cert in inspect_cert_status(image.data):
        if cert.partition == "lk" and cert.state != "valid":
            return False, f"lk cert must be valid, got {cert.state}"
        if cert.partition != "lk" and cert.state not in {"valid", "missing-cert2"}:
            return False, f"{cert.partition} cert must be valid, got {cert.state}"
    return True, "ready"


def run_patch(
    input_path: str | Path,
    output_path: str | Path,
    key_blob: bytes,
    catalog: PatchCatalog,
    *,
    cert_bypass_mode: str = "override",
    image_profile: str | None = None,
) -> tuple[int, int, tuple[str, ...], list[str]]:
    ok, message = preflight_check(
        input_path,
        key_blob,
        catalog,
        image_profile=image_profile,
    )
    if not ok:
        raise UblError(message)

    image = LkImage.from_path(input_path)
    plan = build_plan(
        image,
        catalog,
        key_blob,
        image_profile=image_profile,
    )
    try:
        output_data, signed = apply_plan(
            plan,
            output_path,
            cert_bypass_mode=cert_bypass_mode,
        )
    except PatchError as exc:
        raise UblError(str(exc)) from exc

    report = validate_output(
        image,
        output_data,
        key_blob,
        catalog,
        image_profile=image_profile,
    )
    if not report.ok:
        Path(output_path).unlink(missing_ok=True)
        raise UblError("; ".join(report.errors) or "validation failed")
    return len(image.data), len(output_data), signed, report.steps


def verify_files(
    original: str | Path,
    output: str | Path,
    key_blob: bytes,
    catalog: PatchCatalog,
    *,
    image_profile: str | None = None,
) -> tuple[bool, list[str]]:
    original_image = LkImage.from_path(original)
    output_data = Path(output).read_bytes()
    report = validate_output(
        original_image,
        output_data,
        key_blob,
        catalog,
        image_profile=image_profile,
    )
    return report.ok, report.steps
