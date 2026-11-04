# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Entry point.

`--license` and `--version` exist so the provenance marks are part of the
product's observable behaviour, not only of its source: a repackaged build that
strips them stops being able to answer.

Provenance token: NBMTK-canary-7f3a91c4e8b2d05f
"""

from __future__ import annotations

import sys


def _print_license() -> int:
    import nebumtk_unlock_tools as nut

    print(nut.license_notice())
    print()
    print(f"version: {nut.__version__}")
    print(f"upstream: {nut.__upstream__}")
    print(f"license: {nut.__license__}")
    return 0


def main() -> int:
    args = [argument.strip().lower() for argument in sys.argv[1:]]
    if args and args[0] in {"--license", "-l", "--licence"}:
        return _print_license()
    if args and args[0] in {"--version", "-v"}:
        import nebumtk_unlock_tools as nut

        print(f"{nut.__version__} ({nut.__license__})")
        return 0
    from nebumtk_unlock_tools.app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
