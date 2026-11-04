# SPDX-License-Identifier: AGPL-3.0-only
"""Payload-source lookup for tests.

Device payloads are operator material: they live under ``preloads/<id>/preload.so``
and are never part of the repository. A test that needs one skips when it is not
present instead of failing a fresh checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRELOAD_ROOT = ROOT / "preloads"


def payload_source(payload_id: str) -> Path:
    source = PRELOAD_ROOT / payload_id / "preload.so"
    if not source.is_file():
        pytest.skip(f"payload source not present: preloads/{payload_id}/preload.so")
    return source


def payload_sources() -> dict[str, Path]:
    if not PRELOAD_ROOT.is_dir():
        return {}
    return {
        entry.name: entry / "preload.so"
        for entry in sorted(PRELOAD_ROOT.iterdir())
        if (entry / "preload.so").is_file()
    }


def compile_or_skip(catalog_path: Path):
    """Compile a model catalog, skipping when a payload source is absent."""
    from tools.model_catalog import CatalogError, compile_catalog

    try:
        return compile_catalog(catalog_path)
    except CatalogError as exc:
        if "payload source is missing" in str(exc):
            pytest.skip(f"operator payload material not present: {exc}")
        raise


def xingc_catalog_or_skip() -> Path:
    """Path to the operator's Xingc patch catalog, skipping when absent."""
    path = ROOT / "xingc_sources" / "xingc_vivo.json"
    if not path.is_file():
        pytest.skip("operator Xingc material not present: xingc_sources/xingc_vivo.json")
    return path


def require_payload(payload_id: str) -> None:
    payload_source(payload_id)
