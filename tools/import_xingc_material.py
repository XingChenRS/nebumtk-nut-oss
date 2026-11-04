# SPDX-License-Identifier: AGPL-3.0-only
"""Import local Xingc key material into the material root.

The closed release encrypted four resources under a password-derived key and
generated ``lk/embedded_xingc.py``. The open-source build reads plaintext files
from ``material/xingc/`` instead, so this tool only validates and copies:

    xingc_sources/verify_private.pem    -> material/xingc/verify-private.pem
    xingc_sources/verify_public.avbpub  -> material/xingc/verify-public.avbpub
    xingc_sources/xingc_vivo.json       -> material/xingc/patch-catalog.json
    (marker)                            -> material/xingc/protocol-permit

Source directory override: ``NEBUMTK_XINGC_SOURCE_DIR``.

    python tools/import_xingc_material.py
    python tools/import_xingc_material.py --check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nebumtk_unlock_tools.lk.avbkey import private_key_pem_to_avb_blob

SOURCE_DIR = Path(os.environ.get("NEBUMTK_XINGC_SOURCE_DIR", ROOT / "xingc_sources"))
TARGET_DIR = ROOT / "material" / "xingc"
PROTOCOL_PERMIT = b"NEBUMTK-XINGC-PROTOCOL-PERMIT-v1\n"
IMPORTS = {
    "verify-private.pem": "verify-private.pem",
    "verify_public.avbpub": "verify-public.avbpub",
    "xingc_vivo.json": "patch-catalog.json",
}


def _source_bytes(name: str) -> bytes:
    source = SOURCE_DIR / name
    if not source.is_file():
        raise FileNotFoundError(f"missing Xingc source: {source}")
    data = source.read_bytes()
    if name == "verify_public.avbpub" and len(data) != 520:
        raise RuntimeError("Xingc AVB public blob must be exactly 520 bytes")
    if name == "xingc_vivo.json":
        raw = json.loads(data.decode("utf-8"))
        if not isinstance(raw.get("patches"), list):
            raise RuntimeError("invalid Xingc patch catalog")
        data = json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    return data


def _validate_sources() -> dict[str, bytes]:
    material = {target: _source_bytes(source) for source, target in IMPORTS.items()}
    if private_key_pem_to_avb_blob(material["verify-private.pem"]) != material[
        "verify-public.avbpub"
    ]:
        raise RuntimeError("Xingc private key and AVB public blob do not match")
    return material


def _check() -> None:
    material = _validate_sources()
    for target, data in material.items():
        path = TARGET_DIR / target
        if not path.is_file():
            raise FileNotFoundError(f"material not imported: {path}")
        if path.read_bytes() != data:
            raise RuntimeError(f"imported material is stale: {path}")
    permit = TARGET_DIR / "protocol-permit"
    if not permit.is_file() or permit.read_bytes().strip() != PROTOCOL_PERMIT.strip():
        raise RuntimeError(f"protocol permit marker missing or invalid: {permit}")


def _import() -> None:
    material = _validate_sources()
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    for target, data in material.items():
        path = TARGET_DIR / target
        path.write_bytes(data)
        if target == "verify-private.pem":
            try:
                path.chmod(0o600)
            except OSError:
                pass
        print(f"imported {path}")
    permit = TARGET_DIR / "protocol-permit"
    permit.write_bytes(PROTOCOL_PERMIT)
    print(f"imported {permit}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the material root already matches the sources",
    )
    args = parser.parse_args()
    if args.check:
        _check()
    else:
        _import()


if __name__ == "__main__":
    main()
