# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Off-GUI immutable local-file snapshot worker."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.errors import FlowCancelledError
from ..core.operation_files import snapshot_regular_file


class FileSnapshotWorker(QThread):
    result_ready = Signal(object)
    finished_error = Signal(object)
    cancelled_signal = Signal()

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()

    def _is_cancelled(self) -> bool:
        return self._cancelled or self.isInterruptionRequested()

    def run(self) -> None:
        try:
            snapshot = snapshot_regular_file(
                self.path,
                cancelled=self._is_cancelled,
            )
            if self._is_cancelled():
                self.cancelled_signal.emit()
                return
            self.result_ready.emit(snapshot)
        except FlowCancelledError:
            self.cancelled_signal.emit()
        except Exception as exc:
            self.finished_error.emit(exc)
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
