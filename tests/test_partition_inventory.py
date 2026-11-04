from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from nebumtk_unlock_tools.core.errors import VerificationError
from nebumtk_unlock_tools.core.runner import CommandResult


@dataclass
class InventoryAdb:
    output: str
    returncode: int = 0
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def shell(self, command: str, **kwargs) -> CommandResult:
        self.calls.append((command, kwargs))
        return CommandResult(self.returncode, self.output, "")


def test_inventory_returns_bounded_typed_paired_and_unpaired_entries() -> None:
    from nebumtk_unlock_tools.core.partition import PartitionInventory

    adb = InventoryAdb(
        "lk_a\t/dev/block/sda1\t4096\n"
        "lk_b\t/dev/block/sda2\t8192\n"
        "misc\t/dev/block/sda3\t16384\n"
    )

    inventory = PartitionInventory.from_adb(adb)

    assert [entry.name for entry in inventory.entries] == ["lk_a", "lk_b", "misc"]
    lk_a = inventory.by_name("lk_a")
    assert (lk_a.base_name, lk_a.slot_suffix, lk_a.partner, lk_a.size) == (
        "lk",
        "_a",
        "lk_b",
        4096,
    )
    assert inventory.by_name("misc").partner is None
    assert inventory.resolve_pair("lk", "_b").name == "lk_b"
    assert len(adb.calls) == 1
    command, kwargs = adb.calls[0]
    assert command == PartitionInventory.INVENTORY_COMMAND
    assert kwargs["root"] is True
    assert kwargs["sensitive"] is True
    assert kwargs["check"] is False


@pytest.mark.parametrize(
    "output, error",
    [
        ("", "empty"),
        ("lk_a /dev/block/sda1 4096\n", "malformed"),
        ("lk_a\t/dev/block/sda1\t4096\nLK_A\t/dev/block/sda2\t4096\n", "duplicate"),
        ("../lk_a\t/dev/block/sda1\t4096\n", "name"),
        ("lk_a\t/dev/block/../data/file\t4096\n", "target"),
        ("lk_a\t/data/local/tmp/file\t4096\n", "target"),
        ("lk_a\t/dev/block/sda1\tnot-a-size\n", "size"),
        ("lk_a\t/dev/block/sda1\t0\n", "size"),
        (f"lk_a\t/dev/block/sda1\t{'9' * 5000}\n", "size"),
    ],
)
def test_inventory_rejects_empty_malformed_duplicate_traversal_and_resolution_failures(
    output: str,
    error: str,
) -> None:
    from nebumtk_unlock_tools.core.partition import PartitionInventory

    with pytest.raises(VerificationError, match=error):
        PartitionInventory.from_adb(InventoryAdb(output))


def test_inventory_runs_without_a_pty() -> None:
    """A PTY merges the device's stderr into the data and wraps long rows.

    Either one turns a good row into a malformed one, which is how the listing
    started failing on a real device while the backup path kept working.
    """

    from nebumtk_unlock_tools.core.partition import PartitionInventory

    adb = InventoryAdb("lk_a\t/dev/block/sda1\t4096\n")
    PartitionInventory.from_adb(adb)

    _command, kwargs = adb.calls[0]
    assert kwargs["no_pty"] is True


def test_a_malformed_row_names_the_row() -> None:
    """Without the row this failure cannot be diagnosed from a screenshot."""

    from nebumtk_unlock_tools.core.partition import PartitionInventory

    with pytest.raises(VerificationError) as excinfo:
        PartitionInventory.from_adb(InventoryAdb("lk_a\t/dev/block/sda1\n"))

    assert "lk_a" in str(excinfo.value), str(excinfo.value)


def test_inventory_skips_unresolvable_entries_and_counts_them() -> None:
    """A dangling `by-name` entry must not fail the whole listing.

    A slot that was never populated leaves a symlink whose node does not exist.
    Failing on it made the partition table unreadable on that device, and the
    skip has to stay visible or the partition simply looks absent.
    """

    from nebumtk_unlock_tools.core.partition import PartitionInventory

    adb = InventoryAdb(
        "lk_a\t/dev/block/sda1\t4096\n"
        + PartitionInventory.SKIPPED_MARKER
        + "\t3\n"
    )

    inventory = PartitionInventory.from_adb(adb)

    assert [entry.name for entry in inventory.entries] == ["lk_a"]
    assert inventory.skipped_entries == 3


def test_inventory_command_keeps_diagnostics_out_of_the_data() -> None:
    """`adb shell` gives the remote command one stream, so stderr must be dropped.

    A `blockdev:` line arriving as a row is what produced "1 tab-separated
    fields", which is the failure the operator actually reported.
    """

    from nebumtk_unlock_tools.core.partition import PartitionInventory

    command = PartitionInventory.INVENTORY_COMMAND

    assert command.count("2>/dev/null") >= 2, command
    assert PartitionInventory.SKIPPED_MARKER in command, command


def test_inventory_rejects_failed_or_oversized_listing() -> None:
    from nebumtk_unlock_tools.core.partition import PartitionInventory

    with pytest.raises(VerificationError, match="inventory command"):
        PartitionInventory.from_adb(InventoryAdb("", returncode=1))

    oversized = "x" * (PartitionInventory.MAX_OUTPUT_BYTES + 1)
    with pytest.raises(VerificationError, match="too large"):
        PartitionInventory.from_adb(InventoryAdb(oversized))


def test_inventory_does_not_treat_unconfirmed_ab_name_as_a_pair() -> None:
    from nebumtk_unlock_tools.core.partition import PartitionInventory

    inventory = PartitionInventory.from_adb(
        InventoryAdb(
            "vendor_boot_a\t/dev/block/sda4\t4096\n"
            "init_boot_b\t/dev/block/sda5\t4096\n"
        )
    )

    assert inventory.by_name("vendor_boot_a").partner is None
    assert inventory.by_name("init_boot_b").partner is None
    with pytest.raises(VerificationError, match="confirmed A/B pair"):
        inventory.resolve_pair("vendor_boot", "_b")
