# SPDX-License-Identifier: AGPL-3.0-only
"""Derive the target-B Vivo wipe misc from the reviewed target-A seed."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nebumtk_unlock_tools.core.vivo_wipe_misc import (
    rewrite_vivo_wipe_misc_target,
    validate_vivo_wipe_misc,
)


SOURCE = ROOT / "misc_sources" / "vivo_wipe_data_to_a.img"
TARGET = ROOT / "misc_sources" / "vivo_wipe_data_to_b.img"
SOURCE_SHA256 = "d969f7165168f9836056c982378f6f94c75589654b28dc3f75391f004cdba489"


def main() -> None:
    source = validate_vivo_wipe_misc(SOURCE, "_a")
    if source.sha256 != SOURCE_SHA256:
        raise RuntimeError("Vivo wipe misc A seed hash is not the reviewed input")
    target = rewrite_vivo_wipe_misc_target(SOURCE.read_bytes(), "_b")
    TARGET.write_bytes(target)
    snapshot = validate_vivo_wipe_misc(TARGET, "_b")
    print(f"vivo_wipe_data_to_b_sha256={snapshot.sha256}")


if __name__ == "__main__":
    main()
