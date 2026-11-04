# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from pathlib import Path

import pytest

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.adb import AdbClient
from nebumtk_unlock_tools.core.fastboot import FastbootClient
from nebumtk_unlock_tools.core import operation_files
from nebumtk_unlock_tools.flows.factory import create_flow
from nebumtk_unlock_tools.flows.stages import (
    DualSlotSyncFlow,
    RestoreBackupLkFlow,
    VivoCompleteUnlockFlow,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_persistent_root_sources_and_resources_are_removed() -> None:
    assert not (PROJECT_ROOT / "nebumtk_unlock_tools" / "flows" / "ksu.py").exists()
    assert not (PROJECT_ROOT / "nebumtk_unlock_tools" / "ksu").exists()
    assert not (PROJECT_ROOT / "apk").exists()
    assert not (PROJECT_ROOT / "tools" / "embed_ksu_resources.py").exists()


def test_persistent_root_transport_and_boot_image_apis_are_removed() -> None:
    for name in (
        "install_replace",
        "package_installed_exact",
        "start_activity",
        "force_stop",
    ):
        assert not hasattr(AdbClient, name)
    for name in (
        "resolve_init_boot_partition",
        "flash_init_boot",
        "flash_init_boot_and_reboot",
    ):
        assert not hasattr(FastbootClient, name)
    for name in (
        "VerifiedBootImage",
        "copy_boot_image",
        "snapshot_boot_image",
    ):
        assert not hasattr(operation_files, name)


@pytest.mark.parametrize("flow_kind", ["kernelsu_root", "auto_prepare_unlock"])
def test_removed_persistent_root_routes_are_not_accepted(
    tmp_path: Path,
    flow_kind: str,
) -> None:
    with pytest.raises(ValueError, match="未知 flow_kind"):
        create_flow(flow_kind, PROJECT_ROOT, tmp_path / "workspace", Settings())


def test_reclassified_flow_routes_have_one_explicit_responsibility(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source = tmp_path / "verified_lk.bin"
    patched = tmp_path / "patched_lk.img"
    source.write_bytes(b"original")
    patched.write_bytes(b"patched")

    complete = create_flow(
        "vivo_complete_unlock",
        PROJECT_ROOT,
        workspace,
        Settings(),
    )
    restore = create_flow(
        "restore_backup_lk",
        PROJECT_ROOT,
        workspace,
        Settings(),
        source_lk=source,
    )
    sync = create_flow(
        "dual_slot_sync",
        PROJECT_ROOT,
        workspace,
        Settings(),
        patched_lk=patched,
    )

    assert isinstance(complete, VivoCompleteUnlockFlow)
    assert isinstance(restore, RestoreBackupLkFlow)
    assert isinstance(sync, DualSlotSyncFlow)
