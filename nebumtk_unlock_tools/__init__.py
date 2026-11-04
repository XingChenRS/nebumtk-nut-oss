# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""NEBUMTK Bootloader unlock tools.

Provenance marks live here on purpose. They are part of the licensed work:
AGPL-3.0-only section 5 requires every notice to stay intact, so a publication
that strips or rewrites them is not a licensed copy of this program.
"""

__version__ = "1.1.0"
__author__ = "雾月星辰 & MLXC"
__copyright__ = "Copyright (c) 2026 雾月星辰 & MLXC <github@XingChenRS>"
__license__ = "AGPL-3.0-only"
__upstream__ = "https://github.com/XingChenRS"

# Greppable provenance marks. They survive compilation, so a shipped binary, a
# repackaged source tree or a "cleaned" fork can be traced back here. Keep each
# one on a single line: a split literal is not searchable in the source itself.
PROVENANCE_MARK = "NEBUMTK-PROVENANCE::AGPL-3.0-only::(c)2026 雾月星辰 & MLXC::github/XingChenRS"
# A deliberately odd, unique token. Searching for it finds copied code even
# after the headers have been removed; it is repeated in the entry point, the
# provenance tool, README, LICENSE and docs/PROVENANCE.md for that reason.
PROVENANCE_TOKEN = "NBMTK-canary-7f3a91c4e8b2d05f"

_LICENSE_BANNER = """
{mark}
{copyright}
This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License, version 3 only.
It comes with ABSOLUTELY NO WARRANTY; see the LICENSE file.
Provenance token: {token}
""".strip()


def license_notice() -> str:
    """The banner every build prints, and the string every binary carries."""
    return _LICENSE_BANNER.format(
        mark=PROVENANCE_MARK,
        copyright=__copyright__,
        token=PROVENANCE_TOKEN,
    )


def check_provenance(text: str) -> bool:
    """True when a candidate source tree still carries the required notices."""
    if not isinstance(text, str):
        return False
    return PROVENANCE_MARK in text and PROVENANCE_TOKEN in text
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
