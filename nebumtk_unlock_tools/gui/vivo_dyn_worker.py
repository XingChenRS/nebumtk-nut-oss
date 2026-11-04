"""Typed worker adapter for local Vivo dyn operations."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..core.errors import FlowCancelledError
from ..flows.vivo_dyn import DynCaptureRequest, DynRestoreRequest, VivoDynFacade


class VivoDynWorker(QThread):
    result_ready = Signal(object)
    log_message = Signal(str)
    finished_error = Signal(str)
    cancelled_signal = Signal()

    def __init__(
        self,
        facade: VivoDynFacade,
        request: DynCaptureRequest | DynRestoreRequest,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.request = request
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            if self._cancelled:
                raise FlowCancelledError("Vivo dyn operation cancelled")
            callbacks = getattr(self.facade, "callbacks", None)
            if callbacks is not None:
                callbacks.log = self.log_message.emit
            if isinstance(self.request, DynCaptureRequest):
                self.log_message.emit("Dyn 备份开始")
                result = self.facade.capture(
                    self.request,
                    cancelled=lambda: self._cancelled,
                )
                completion = "Dyn 备份完成"
            elif isinstance(self.request, DynRestoreRequest):
                self.log_message.emit("Dyn 恢复开始")
                result = self.facade.restore(
                    self.request,
                    cancelled=lambda: self._cancelled,
                )
                completion = "Dyn 恢复完成"
            else:
                raise TypeError("unsupported Vivo dyn request")
            if self._cancelled:
                raise FlowCancelledError("Vivo dyn operation cancelled")
            self.log_message.emit(completion)
            self.result_ready.emit(result)
        except FlowCancelledError:
            self.cancelled_signal.emit()
        except Exception as exc:
            self.finished_error.emit(str(exc))
