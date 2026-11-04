# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import nebumtk_unlock_tools
from nebumtk_unlock_tools.build_profile import CLIENT_VERSION
from tools import audit_provenance


ROOT = Path(__file__).resolve().parent.parent


def test_release_version_is_consistent_at_110() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert nebumtk_unlock_tools.__version__ == "1.1.0"
    assert CLIENT_VERSION == "1.1.0"
    assert metadata["project"]["version"] == "1.1.0"
    assert metadata["project"]["license"]["text"] == "AGPL-3.0-only"


def test_every_first_party_file_carries_the_authorship_headers() -> None:
    assert audit_provenance.audit_self(ROOT) == []

    for path in audit_provenance._first_party_python(ROOT)[:5]:
        head = path.read_text(encoding="utf-8").splitlines()[:2]
        assert audit_provenance.COPYRIGHT_LINE in head, path
        assert audit_provenance.LICENSE_LINE in head, path


def test_the_licence_and_the_package_carry_the_provenance_marks() -> None:
    assert nebumtk_unlock_tools.__copyright__ == (
        "Copyright (c) 2026 雾月星辰 & MLXC <github@XingChenRS>"
    )
    assert nebumtk_unlock_tools.__license__ == "AGPL-3.0-only"
    assert nebumtk_unlock_tools.check_provenance(
        (ROOT / "nebumtk_unlock_tools" / "__init__.py").read_text(encoding="utf-8")
    )

    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in licence
    assert nebumtk_unlock_tools.PROVENANCE_TOKEN in licence
    assert "雾月星辰 & MLXC" in licence


def test_license_notice_is_printable_and_complete() -> None:
    notice = nebumtk_unlock_tools.license_notice()
    assert nebumtk_unlock_tools.PROVENANCE_MARK in notice
    assert nebumtk_unlock_tools.PROVENANCE_TOKEN in notice
    assert "GNU Affero General Public License" in notice


def test_provenance_scan_finds_a_stripped_copy(tmp_path: Path) -> None:
    copied = tmp_path / "fork" / "app.py"
    copied.parent.mkdir()
    copied.write_text(
        "import os\n"
        f"mark = {nebumtk_unlock_tools.PROVENANCE_MARK!r}\n"
        "def main():\n    return 1\n",
        encoding="utf-8",
    )

    report = audit_provenance.scan(tmp_path)
    assert report["evidence"], "verbatim copy without notices must be detected"


def test_provenance_scan_reports_a_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "unrelated.py").write_text("print('hello')\n", encoding="utf-8")

    report = audit_provenance.scan(tmp_path)
    assert report["evidence"] == []
    assert report["files_scanned"] == 1


def test_provenance_gate_rejects_a_file_that_lost_its_header(tmp_path: Path) -> None:
    stripped = tmp_path / "tests" / "test_stripped.py"
    stripped.parent.mkdir()
    stripped.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    violations = audit_provenance.audit_self(tmp_path)
    assert any("missing copyright header" in item for item in violations)
    assert any("missing licence header" in item for item in violations)


@pytest.mark.parametrize("name", ("LICENSE", "docs/PROVENANCE.md"))
def test_provenance_gate_rejects_a_missing_document(tmp_path: Path, name: str) -> None:
    (tmp_path / "nebumtk_unlock_tools").mkdir()
    (tmp_path / "nebumtk_unlock_tools" / "__init__.py").write_text(
        "PROVENANCE_MARK = "
        f"{nebumtk_unlock_tools.PROVENANCE_MARK!r}\n"
        f"PROVENANCE_TOKEN = {nebumtk_unlock_tools.PROVENANCE_TOKEN!r}\n",
        encoding="utf-8",
    )
    for relative in ("README.md", "NOTICE.md"):
        path = tmp_path / relative
        path.write_text(nebumtk_unlock_tools.PROVENANCE_TOKEN, encoding="utf-8")

    violations = audit_provenance.audit_self(tmp_path)
    assert any(name in item for item in violations)


def test_structural_scan_catches_a_renamed_copy_without_notices(tmp_path: Path) -> None:
    """Stripping headers, the watermark and the filename still leaves the code."""

    source = ROOT / "nebumtk_unlock_tools" / "lk" / "ubl" / "patcher.py"
    stripped = [
        line
        for line in source.read_text(encoding="utf-8").splitlines()
        if "SPDX" not in line and "provenance:" not in line
    ]
    copied = tmp_path / "generic_patch.py"
    copied.write_text("\n".join(stripped) + "\n", encoding="utf-8")

    report = audit_provenance.scan(tmp_path)
    assert report["evidence"], "structural overlap must catch a renamed copy"
    assert "structural_overlap" in report["evidence"][0]


def test_every_first_party_file_ends_with_the_watermark_line() -> None:
    for path in audit_provenance._first_party_python(ROOT):
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            continue
        assert text.rstrip().endswith(nebumtk_unlock_tools.PROVENANCE_TOKEN), path
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
