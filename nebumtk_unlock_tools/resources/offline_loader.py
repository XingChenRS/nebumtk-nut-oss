# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Protected preload resolution for the offline product."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .offline_catalog import materialize_catalog_wrapper, unwrap_catalog_payload_key
from .offline_grant import OfflineGrantResponder
from .payload_store import materialize_wrapper, unwrap_payload_key


def resolve_offline_preload(
    preload_id: str,
) -> tuple[Path, Callable[[str, str], str]]:
    """Resolve one ``builtin:`` preload to a wrapper plus a grant responder.

    A signed catalog may carry the wrapper itself; otherwise the operator's
    local payload store supplies it. Both paths use local material only and
    neither needs an access password.
    """
    wrapper = materialize_catalog_wrapper(preload_id)
    unwrap = unwrap_catalog_payload_key
    if wrapper is None:
        wrapper = materialize_wrapper(preload_id)
        unwrap = unwrap_payload_key
    return wrapper, OfflineGrantResponder(preload_id, unwrap)
