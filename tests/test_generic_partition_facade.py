from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import ast

import pytest

from nebumtk_unlock_tools.config.store import Settings
from nebumtk_unlock_tools.core.operation_files import snapshot_regular_file


def test_generic_partition_recursive_import_closure_excludes_protected_domains() -> None:
    package = Path(__file__).parents[1] / "nebumtk_unlock_tools"
    pending = ["flows.generic_partition"]
    visited: set[str] = set()
    forbidden = ("online", "resources", "lk", "preload", "ubl", "xingc")

    while pending:
        module = pending.pop()
        if module in visited:
            continue
        visited.add(module)
        source = package.joinpath(*module.split(".")).with_suffix(".py")
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        package_parts = module.split(".")[:-1]
        for node in ast.walk(tree):
            target = ""
            if isinstance(node, ast.ImportFrom) and node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                target = ".".join(base + ((node.module or "").split(".") if node.module else []))
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "nebumtk_unlock_tools."
            ):
                target = (node.module or "").removeprefix("nebumtk_unlock_tools.")
            if target:
                if target and package.joinpath(*target.split(".")).with_suffix(".py").is_file():
                    pending.append(target)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("nebumtk_unlock_tools."):
                        imported = alias.name.removeprefix("nebumtk_unlock_tools.")
                        if package.joinpath(*imported.split(".")).with_suffix(".py").is_file():
                            pending.append(imported)

    assert not any(any(part in forbidden for part in name.split(".")) for name in visited), visited


class FacadeContext:
    def __init__(self) -> None:
        self.adb = object()
        self.fastboot = object()
        self.operation_files = SimpleNamespace(directory=Path("operation"))
        self.callbacks = SimpleNamespace(
            log=lambda _message: None,
            cancelled=lambda: False,
        )
        self.calls: list[tuple[str, object]] = []

    def init_clients(self, serial: str, **kwargs) -> None:
        self.calls.append(("init", (serial, kwargs)))

    def ensure_root(self, profile, preload_override) -> None:
        self.calls.append(("root", (profile, preload_override)))

    def ensure_not_cancelled(self) -> None:
        return


def test_generic_inventory_facade_selects_explicit_provider_and_returns_typed_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from nebumtk_unlock_tools.flows import generic_partition as module
    from nebumtk_unlock_tools.flows.generic_partition import (
        GenericPartitionFacade,
        InventoryRequest,
    )

    class FakeAdb:
        def __init__(self, _binary, *, serial, su_path, **_kwargs) -> None:
            self.serial = serial
            self.su_path = su_path

        def devices(self):
            return [self.serial]

        def get_state(self):
            return "device"

    inventory = SimpleNamespace(entries=("typed",), skipped_entries=0)
    monkeypatch.setattr(
        module.EnvChecker,
        "validate_adb",
        lambda _self: SimpleNamespace(adb="adb"),
    )
    monkeypatch.setattr(module, "AdbClient", FakeAdb)
    monkeypatch.setattr(
        module.SafetyChecker,
        "check_device_profile",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        module,
        "probe_custom_su",
        lambda adb, executable: SimpleNamespace(
            uid=0,
            executable=executable,
            adb=adb,
        ),
    )
    monkeypatch.setattr(
        module.PartitionInventory,
        "from_adb",
        lambda adb: inventory if adb.su_path == "/data/local/tmp/operator-su" else None,
    )
    facade = GenericPartitionFacade(
        tmp_path,
        tmp_path / "workspace",
        Settings(custom_su="/data/local/tmp/operator-su"),
    )
    device = SimpleNamespace(serial="SERIAL", slot_suffix="_a")
    profile = SimpleNamespace(key="model", su_path="/data/local/tmp/profile-su")

    assert facade.inventory(
        InventoryRequest(device, profile, "/data/local/tmp/operator-su")
    ) is inventory


def test_checklist_write_requires_a_selected_image() -> None:
    from nebumtk_unlock_tools.core.errors import VerificationError
    from nebumtk_unlock_tools.core.partition_checklist import ChecklistItem

    with pytest.raises(VerificationError, match="需要先选择镜像"):
        ChecklistItem(partition="lk_a", action="write")


def test_checklist_request_rejects_actions_the_transport_cannot_do(tmp_path: Path) -> None:
    """ADB cannot erase and Fastboot cannot read; the list must not pretend."""

    from nebumtk_unlock_tools.core.partition_checklist import ChecklistItem
    from nebumtk_unlock_tools.flows.generic_partition import ChecklistRequest

    device = SimpleNamespace(serial="SERIAL", slot_suffix="_a")
    profile = SimpleNamespace(key="model")
    image = tmp_path / "x.img"
    image.write_bytes(b"x")

    with pytest.raises(ValueError, match="不符"):
        ChecklistRequest(device, profile, "adb", (ChecklistItem("misc", "erase"),))
    with pytest.raises(ValueError, match="不符"):
        ChecklistRequest(
            device, profile, "fastboot", (ChecklistItem("misc", "read"),)
        )


def test_fastboot_checklist_honors_cancellation(tmp_path: Path, monkeypatch) -> None:
    from nebumtk_unlock_tools.core import fastboot as fastboot_module
    from nebumtk_unlock_tools.core.errors import FlowCancelledError
    from nebumtk_unlock_tools.core.partition_checklist import ChecklistItem
    from nebumtk_unlock_tools.core.runner import CommandResult
    from nebumtk_unlock_tools.flows import generic_partition as module
    from nebumtk_unlock_tools.flows.generic_partition import (
        ChecklistRequest,
        GenericPartitionFacade,
    )

    cancelled = {"value": False}

    def fake_run_command(_args, **kwargs):
        if kwargs["cancelled"]():
            raise FlowCancelledError("cancelled")
        return CommandResult(0, "", "")

    monkeypatch.setattr(
        module.EnvChecker,
        "validate_fastboot",
        lambda _self: SimpleNamespace(fastboot="fastboot"),
    )
    monkeypatch.setattr(fastboot_module, "run_command", fake_run_command)
    image = tmp_path / "x.img"
    image.write_bytes(b"x")
    facade = GenericPartitionFacade(tmp_path, tmp_path / "workspace", Settings())
    cancelled["value"] = True

    with pytest.raises(FlowCancelledError):
        facade.checklist(
            ChecklistRequest(
                SimpleNamespace(serial="SERIAL", slot_suffix="_a"),
                SimpleNamespace(key="model"),
                "fastboot",
                (ChecklistItem("misc", "write", image),),
            ),
            cancelled=lambda: cancelled["value"],
        )
