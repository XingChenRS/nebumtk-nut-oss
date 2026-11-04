# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

from .catalog import PatchCatalog
from .lk_image import LkImage
from .size_policy import inspect_output_size
from .xingc_locator import locate_patch

from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage as LibLkImage
from nebumtk_unlock_tools.lk.vendor.liblk.structures.certificate import Certificate


def _header_without_data_size(header: object) -> bytes:
    raw = bytearray(bytes(header))
    header_type = type(header)
    for field_name in ("_dsize", "_dsize_extend"):
        field = getattr(header_type, field_name)
        raw[field.offset : field.offset + field.size] = bytes(field.size)
    return bytes(raw)


@dataclass
class ValidationReport:
    ok: bool
    steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        line = f"[{'OK' if passed else 'FAIL'}] {name}"
        if detail:
            line += f": {detail}"
        self.steps.append(line)
        if not passed:
            self.errors.append(line)
            self.ok = False


def validate_output(
    original: LkImage,
    output_data: bytes,
    key_blob: bytes,
    catalog: PatchCatalog,
    *,
    image_profile: str | None = None,
) -> ValidationReport:
    report = ValidationReport(ok=True)
    search_ranges = original.partition_search_ranges()
    expected_ranges: list[tuple[int, int]] = []

    for patch in catalog.patches:
        replacement = key_blob if patch.dynamic else patch.patch_bytes
        before = locate_patch(
            original.data,
            patch,
            replacement=replacement,
            search_ranges=search_ranges,
            image_profile=image_profile,
        )
        after = locate_patch(
            output_data,
            patch,
            replacement=replacement,
            search_ranges=search_ranges,
            image_profile=image_profile,
        )
        exact_site = (
            before.state == "original"
            and after.state == "patched"
            and before.offset is not None
            and before.offset == after.offset
            and before.replace_len is not None
            and before.replace_len == after.replace_len
        )
        report.add(
            f"patch:{patch.id}",
            exact_site,
            f"{before.state}->{after.state}",
        )
        if exact_site:
            expected_ranges.append(
                (before.offset, before.offset + before.replace_len)
            )

    try:
        orig_lib = LibLkImage(original.data)
        out_lib = LibLkImage(output_data)
        orig_names = list(orig_lib.partitions.keys())
        out_names = list(out_lib.partitions.keys())
        structure_ok = out_names == orig_names and "lk" in out_lib.partitions
        report.add("structure:parse", structure_ok, ",".join(out_names))
    except Exception as exc:
        report.add("structure:parse", False, str(exc))
        return report

    diff_ok = True
    lk_part = original.partition("lk")
    lk_start, lk_end = lk_part.payload_range
    for index in range(lk_start, lk_end):
        if original.data[index] != output_data[index]:
            if not any(start <= index < end for start, end in expected_ranges):
                diff_ok = False
                break
    report.add("structure:diff", diff_ok, "only xingc sites changed in lk payload")

    for name in orig_names:
        if name not in out_lib.partitions:
            continue
        orig_part = orig_lib.partitions[name]
        out_part = out_lib.partitions[name]
        report.add(
            f"header:partition:{name}",
            bytes(orig_part.header) == bytes(out_part.header),
            "exact",
        )
        report.add(
            f"size:partition:{name}",
            len(orig_part.data) == len(out_part.data),
            str(len(out_part.data)),
        )
        if name != "lk":
            report.add(
                f"data:partition:{name}",
                bytes(orig_part.data) == bytes(out_part.data),
                "exact",
            )
        report.add(
            f"cert-count:{name}",
            len(orig_part.certs) == len(out_part.certs),
            f"{len(out_part.certs)}/{len(orig_part.certs)}",
        )
        for cert_index, (orig_cert, out_cert) in enumerate(
            zip(orig_part.certs, out_part.certs),
            start=1,
        ):
            if name == "lk" and cert_index == 2:
                continue
            report.add(
                f"cert-header:{name}:{cert_index}",
                bytes(orig_cert.header) == bytes(out_cert.header),
                "exact",
            )
            report.add(
                f"cert-data:{name}:{cert_index}",
                bytes(orig_cert.data) == bytes(out_cert.data),
                "exact",
            )

    orig_lk = orig_lib.partitions.get("lk")
    out_lk = out_lib.partitions.get("lk")
    if orig_lk is None or out_lk is None:
        report.add("cert:lk", False, "missing lk")
    elif orig_lk.cert2 is None or out_lk.cert2 is None:
        report.add("cert:lk", False, "missing cert2")
    else:
        try:
            original_cert = Certificate.from_bytes(orig_lk.cert2.data)
            original_hashes = orig_lk.compute_hashes()
            original_valid = original_cert.matches(*original_hashes)
            output_hashes = out_lk.compute_hashes()
            expected_cert2 = (
                original_cert.build_hash_override_block(*output_hashes)
                + bytes(orig_lk.cert2.data)
            )
            exact_cert2 = bytes(out_lk.cert2.data) == expected_cert2
            exact_header = (
                _header_without_data_size(orig_lk.cert2.header)
                == _header_without_data_size(out_lk.cert2.header)
                and out_lk.cert2.header.data_size == len(expected_cert2)
            )
            report.add("cert:lk-original", original_valid, "valid signed cert2")
            report.add(
                "cert:lk-override",
                exact_cert2,
                f"{len(out_lk.cert2.data)}/{len(expected_cert2)}",
            )
            report.add("cert:lk-header", exact_header, "only data size may change")
        except Exception as exc:
            report.add("cert:lk", False, str(exc))

    size_policy = inspect_output_size(original.data, output_data)
    report.add(
        "size:output",
        size_policy.allowed
        and len(output_data) == size_policy.expected_size,
        size_policy.detail,
    )
    if (
        size_policy.output_used <= len(original.data)
        and len(output_data) >= size_policy.output_used
    ):
        report.add(
            "padding:trailing",
            output_data[size_policy.output_used:]
            == original.data[size_policy.output_used:len(output_data)],
            "preserved at absolute offsets",
        )

    hedgehog = catalog.patch("xingc-replace-hedgehog-public-key")
    st = locate_patch(
        output_data,
        hedgehog,
        replacement=key_blob,
        search_ranges=search_ranges,
        image_profile=image_profile,
    )
    if st.offset is not None:
        blob = output_data[st.offset : st.offset + 520]
        report.add(
            "patch:hedgehog-sha256",
            hashlib.sha256(blob).hexdigest() == hashlib.sha256(key_blob).hexdigest(),
            hashlib.sha256(blob).hexdigest()[:16],
        )
    return report
