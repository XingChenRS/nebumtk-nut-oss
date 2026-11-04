# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Build-time product identity.

This is the open-source 1.1 offline baseline: one product, no channel switch.
"""

from __future__ import annotations

import sys

BUILD_CHANNEL = "offline"
CLIENT_VERSION = "1.1.0"
PROTOCOL_VERSION = 2


def is_compiled() -> bool:
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))


def channel() -> str:
    return BUILD_CHANNEL


def is_online() -> bool:
    return False
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
