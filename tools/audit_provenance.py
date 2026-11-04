# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Provenance audit: verify our notices, and recognise them in someone else's tree.

Two modes:

``--self <tree>``
    Verify this repository still carries its notices: both SPDX header lines on
    every first-party Python file, the provenance marks in the package root, the
    copyright line and the AGPL text in LICENSE, and the token in the documents.
    Exit 1 on any violation. This is what a release gate runs.

``--scan <path>``
    Look for our marks inside an arbitrary tree or binary (a published fork, a
    repackaged wheel, a shipped .exe). Exit 0 when evidence is found, 2 when the
    path is clean, 1 on a usage error. It never modifies anything.

Provenance token: NBMTK-canary-7f3a91c4e8b2d05f

"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nebumtk_unlock_tools as nut  # noqa: E402

COPYRIGHT_LINE = "# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>"
LICENSE_LINE = "# SPDX-License-Identifier: AGPL-3.0-only"
SKIP_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "material",
    "preloads",
    "xingc_sources",
    "local-dev",
    ".private",
    "native/preload_wrapper/vendor",
    "nebumtk_unlock_tools/lk/vendor",
    "platform-tools",
}
SKIP_FILES = {"nebumtk_unlock_tools/resources/embedded_catalog.py"}

# Distinctive literals a copy is very unlikely to reproduce by accident. They are
# searched verbatim, so renaming a module does not hide a lifted file.
FINGERPRINTS = (
    nut.PROVENANCE_MARK,
    nut.PROVENANCE_TOKEN,
    "NEBUMTK-XINGC-PROTOCOL-PERMIT-v1",
    "NEBUMTK-CATALOG-PRELOAD-v1:",
    "NEBUMTK-WRAP-v1:",
    "访问密码未解锁，禁止解密内置资源",
    "MUST NOT relax exact-device selection",
    "SakiSU patch-only operation cancelled",
)
SCAN_READ_TYPES = {".py", ".pyi", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ps1", ".sh"}
MAX_SCAN_BYTES = 96 * 1024 * 1024


def _skipped(relative: Path) -> bool:
    posix = relative.as_posix()
    if posix in SKIP_FILES:
        return True
    return any(part in SKIP_PARTS for part in relative.parts) or any(
        marker in posix for marker in SKIP_PARTS
    )


def _first_party_python(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in ("nebumtk_unlock_tools/**/*.py", "tools/*.py", "tests/*.py"):
        for path in sorted(root.glob(pattern)):
            if not _skipped(path.relative_to(root)):
                candidates.append(path)
    return candidates


def audit_self(root: Path) -> list[str]:
    violations: list[str] = []

    for path in _first_party_python(root):
        head = path.read_text(encoding="utf-8").splitlines()[:4]
        if COPYRIGHT_LINE not in head:
            violations.append(f"missing copyright header: {path.relative_to(root)}")
        if LICENSE_LINE not in head:
            violations.append(f"missing licence header: {path.relative_to(root)}")

    package = root / "nebumtk_unlock_tools" / "__init__.py"
    try:
        source = package.read_text(encoding="utf-8")
    except OSError:
        violations.append("package root is missing")
        return violations
    if not nut.check_provenance(source):
        violations.append("package root lost its provenance marks")

    licence_path = root / "LICENSE"
    try:
        licence = licence_path.read_text(encoding="utf-8")
    except OSError:
        violations.append("LICENSE is missing")
        licence = ""
    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in licence:
        violations.append("LICENSE no longer carries the AGPL text")
    if "雾月星辰 & MLXC" not in licence:
        violations.append("LICENSE lost the copyright notice")
    if nut.PROVENANCE_TOKEN not in licence:
        violations.append("LICENSE lost the provenance token")

    for relative in ("README.md", "NOTICE.md", "docs/PROVENANCE.md"):
        path = root / relative
        if not path.is_file():
            violations.append(f"{relative} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        if nut.PROVENANCE_TOKEN not in text:
            violations.append(f"{relative} lost the provenance token")

    return violations


def _read_maybe_binary(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


STRUCTURAL_MIN_LINE = 24
STRUCTURAL_MIN_MATCHES = 8
STRUCTURAL_RATIO = 0.5


def _normalise(line: str) -> str:
    return " ".join(line.split())


def _line_digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def reference_lines(root: Path) -> set[str]:
    """Digests of our own substantive code lines, for structural comparison."""
    digests: set[str] = set()
    for path in _first_party_python(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            normalised = _normalise(line)
            if len(normalised) < STRUCTURAL_MIN_LINE or normalised.startswith("#"):
                continue
            digests.add(_line_digest(normalised))
    return digests


def structural_overlap(path: Path, reference: set[str]) -> tuple[int, int]:
    """(matching substantive lines, distinct substantive lines) for one file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, 0
    lines = {
        _normalise(line)
        for line in text.splitlines()
        if len(_normalise(line)) >= STRUCTURAL_MIN_LINE
        and not _normalise(line).startswith("#")
    }
    if not lines:
        return 0, 0
    matched = sum(1 for line in lines if _line_digest(line) in reference)
    return matched, len(lines)


def scan(target: Path, *, structural: bool = True) -> dict[str, object]:
    evidence: list[dict[str, object]] = []
    reference = reference_lines(ROOT) if structural else set()
    files = [target] if target.is_file() else [
        path for path in target.rglob("*") if path.is_file()
    ]
    scanned = 0
    for path in files:
        relative = path.name if target.is_file() else path.relative_to(target).as_posix()
        if target.is_dir() and _skipped(path.relative_to(target)):
            continue
        data = _read_maybe_binary(path)
        if data is None:
            continue
        scanned += 1
        matched: list[str] = []
        for fingerprint in FINGERPRINTS:
            needle = fingerprint.encode("utf-8")
            if needle in data:
                matched.append(fingerprint)
        if not matched and path.suffix.lower() in SCAN_READ_TYPES:
            text = data.decode("utf-8", errors="replace")
            if re.search(r"SPDX-License-Identifier:\s*(AGPL-3\.0|GPL-3\.0)", text):
                matched.append("SPDX header")
        entry: dict[str, object] = {"path": relative, "markers": matched}
        if reference and path.suffix.lower() in SCAN_READ_TYPES:
            hits, total = structural_overlap(path, reference)
            ratio = hits / total if total else 0.0
            if hits >= STRUCTURAL_MIN_MATCHES and ratio >= STRUCTURAL_RATIO:
                entry["structural_overlap"] = f"{hits}/{total} lines ({ratio:.0%})"
            elif not matched:
                continue
        if not matched and "structural_overlap" not in entry:
            continue
        evidence.append(entry)
    return {"target": str(target), "files_scanned": scanned, "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit project provenance marks")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self", dest="self_root", type=Path, help="verify this repository")
    group.add_argument("--scan", dest="scan_root", type=Path, help="look for our marks elsewhere")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.self_root is not None:
        violations = audit_self(Path(args.self_root).resolve())
        if args.json:
            print(json.dumps({"violations": violations}, ensure_ascii=False, indent=2))
        elif violations:
            print("provenance audit failed:", file=sys.stderr)
            for violation in violations:
                print(f"  - {violation}", file=sys.stderr)
        else:
            print(
                "provenance ok: headers, marks, LICENSE and documents intact "
                f"({nut.PROVENANCE_TOKEN})"
            )
        return 1 if violations else 0

    report = scan(Path(args.scan_root).resolve())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        evidence = report["evidence"]
        print(f"scanned {report['files_scanned']} files under {report['target']}")
        if not evidence:
            print("no provenance evidence found")
        else:
            print(f"provenance evidence in {len(evidence)} file(s):")
            for entry in evidence:
                markers = ", ".join(entry["markers"]) or "-"
                extra = entry.get("structural_overlap")
                suffix = f" | structural overlap {extra}" if extra else ""
                print(f"  {entry['path']}: {markers}{suffix}")
    return 0 if report["evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
