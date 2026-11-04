# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from nebumtk_unlock_tools.core.dyn_backup import (
    DYN_REMOTE_DIRECTORY,
    capture_dyn_backup,
    restore_dyn_backup,
)


class _DynAdb:
    def __init__(self, files: dict[str, bytes], *, root_su: str | None = "su") -> None:
        self.files = dict(files)
        self.remote_files: dict[str, bytes] = {}
        self.root_su = root_su
        self.shell_calls: list[tuple[str, bool, str | None]] = []
        self.pushed: list[tuple[Path, str]] = []
        self.reboots = 0

    def shell(self, command: str, *, root: bool = False, su_path: str | None = None, **_kwargs):
        self.shell_calls.append((command, root, su_path))
        if command == "id -u":
            return SimpleNamespace(
                returncode=0 if su_path == self.root_su else 1,
                stdout="0\n" if su_path == self.root_su else "2000\n",
                output="",
            )
        if "for file in" in command:
            rows = [f"{name}\t600:1000:1000" for name in sorted(self.files)]
            return SimpleNamespace(returncode=0, stdout="\n".join(rows) + "\n", output="")
        if command.startswith("dd if=") and " of=" in command:
            source = command.split("dd if=", 1)[1].split(" of=", 1)[0].strip("'")
            target = command.split(" of=", 1)[1].split(" conv=", 1)[0].strip("'")
            payload = self.remote_files.get(source)
            if payload is None:
                payload = self.files[source.rsplit("/", 1)[-1]]
            if "/data/vivo-common/dyn/" in target:
                self.files[target.rsplit("/", 1)[-1]] = payload
            else:
                self.remote_files[target] = payload
            return SimpleNamespace(returncode=0, stdout="", output="")
        return SimpleNamespace(returncode=0, stdout="", output="")

    def remote_file_size(self, remote: str, *, root: bool = True) -> int:
        return len(self.remote_files.get(remote, b""))

    def remote_sha256(self, remote: str, *, root: bool = True) -> str:
        payload = self.remote_files.get(remote)
        return hashlib.sha256(payload).hexdigest() if payload is not None else ""

    def pull(self, remote: str, local: Path, **_kwargs) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.remote_files[remote])

    def push(self, local: Path, remote: str) -> None:
        self.pushed.append((Path(local), remote))
        self.remote_files[remote] = Path(local).read_bytes()

    def remove_remote(self, remote: str, **_kwargs) -> None:
        self.remote_files.pop(remote, None)

    def reboot(self) -> None:
        self.reboots += 1


def test_capture_keeps_direct_dyn_files_as_verified_operation_snapshots(tmp_path: Path) -> None:
    adb = _DynAdb({"a.bin": b"dyn-a", "b.bin": b"dyn-b"})

    backup = capture_dyn_backup(adb, tmp_path)

    assert backup is not None
    assert [item.name for item in backup.files] == ["a.bin", "b.bin"]
    assert (tmp_path / "dyn-a.bin").read_bytes() == b"dyn-a"
    assert (tmp_path / "dyn-b.bin").read_bytes() == b"dyn-b"
    assert all(item.sha256 == hashlib.sha256(item.path.read_bytes()).hexdigest() for item in backup.files)
    assert any(DYN_REMOTE_DIRECTORY in command for command, root, _ in adb.shell_calls if root)


def test_restore_uses_existing_su_only_and_reboots_after_verified_copy(tmp_path: Path) -> None:
    source = _DynAdb({"vivo_a": b"A", "vivo_b": b"B"})
    backup = capture_dyn_backup(source, tmp_path)
    assert backup is not None
    target = _DynAdb({}, root_su="su")

    outcome = restore_dyn_backup(target, backup, su_candidates=("/data/local/tmp/su", "su"))

    assert outcome.restored is True
    assert outcome.reason == "restored"
    assert target.files == {"vivo_a": b"A", "vivo_b": b"B"}
    assert target.reboots == 1
    assert target.pushed
    assert all(root for _command, root, _su_path in target.shell_calls)
    assert [su_path for command, _root, su_path in target.shell_calls if command == "id -u"] == [
        "/data/local/tmp/su",
        "su",
    ]


def test_restore_does_not_escalate_or_reboot_when_no_real_root_is_available(tmp_path: Path) -> None:
    source = _DynAdb({"vivo_a": b"A"})
    backup = capture_dyn_backup(source, tmp_path)
    assert backup is not None
    target = _DynAdb({}, root_su=None)

    outcome = restore_dyn_backup(target, backup, su_candidates=("su",))

    assert outcome.restored is False
    assert outcome.reason == "root-unavailable"
    assert target.files == {}
    assert target.pushed == []
    assert target.reboots == 0
