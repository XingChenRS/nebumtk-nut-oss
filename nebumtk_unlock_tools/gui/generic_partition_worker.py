"""Typed worker for generic partition operations."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ..core.errors import FlowCancelledError
from ..core.partition import PartitionInventory
from ..flows.generic_partition import (
    BackupSelectedRequest,
    ChecklistRequest,
    InventoryRequest,
)


GenericRequest = InventoryRequest | BackupSelectedRequest | ChecklistRequest


class GenericPartitionWorker(QThread):
    result_ready = Signal(object)
    finished_error = Signal(object)
    cancelled_signal = Signal()

    def __init__(
        self,
        facade,
        request: GenericRequest,
        *,
        inventory: PartitionInventory | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.facade = facade
        self.request = request
        self.inventory = inventory
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()

    def run(self) -> None:
        try:
            if self._cancelled:
                raise FlowCancelledError("generic partition operation cancelled")
            if isinstance(self.request, ChecklistRequest):
                result = self.facade.checklist(
                    self.request,
                    cancelled=lambda: self._cancelled,
                    inventory=self.inventory,
                )
            elif isinstance(self.request, BackupSelectedRequest):
                result = self.facade.backup_selected(
                    self.request, cancelled=lambda: self._cancelled
                )
            elif isinstance(self.request, InventoryRequest):
                result = self.facade.inventory(
                    self.request, cancelled=lambda: self._cancelled
                )
            else:
                raise TypeError("unsupported typed generic partition request")
            if self._cancelled:
                raise FlowCancelledError("generic partition operation cancelled")
            self.result_ready.emit(result)
        except FlowCancelledError:
            self.cancelled_signal.emit()
        except Exception as exc:
            self.finished_error.emit(exc)
