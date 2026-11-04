# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Off-GUI-thread adapter for the typed SakiSU patch-only flow."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..core.errors import FlowCancelledError
from ..sakisu.flow import SakisuPatchFlow, SakisuPatchRequest


class SakisuPatchWorker(QThread):
    result_ready = Signal(object)
    log_message = Signal(str)
    finished_error = Signal(str)
    cancelled_signal = Signal()

    def __init__(
        self,
        flow: SakisuPatchFlow,
        request: SakisuPatchRequest,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.flow = flow
        self.request = request
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()

    def run(self) -> None:
        try:
            self.log_message.emit("SakiSU 本地修补开始")
            result = self.flow.run(
                self.request,
                cancelled=lambda: self._cancelled,
            )
            self.log_message.emit("SakiSU 本地修补完成")
            self.result_ready.emit(result)
        except FlowCancelledError:
            self.cancelled_signal.emit()
        except Exception as exc:
            self.finished_error.emit(str(exc))
