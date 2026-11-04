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
