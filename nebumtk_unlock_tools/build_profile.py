# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Build-time product channel.

This repository is a fixed Offline Utility product. There is no online
channel, no runtime switch, and no environment override that can turn this
build into another product.
"""

from __future__ import annotations

BUILD_CHANNEL = "offline"
CLIENT_VERSION = "2.0.0"
