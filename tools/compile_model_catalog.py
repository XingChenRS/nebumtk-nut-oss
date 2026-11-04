# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Compile the reviewed maintainer catalog into the legacy runtime YAML."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.model_catalog import compile_catalog_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    compile_catalog_file(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
