# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Partition inventory, selection backup, and an operator-built operation list.

The page is deliberately one screen with two tables:

* **分区清单** — what the device actually has, read over ADB. Ticking a row
  selects it for backup.
* **操作清单** — one row per partition with the actions that transport can do.
  ADB can read and write; Fastboot can write and erase. Ticking an action adds
  it to the list; a write also needs an image, which the operator picks per row.

Running the list hands it to a background worker, so the window stays usable and
the operator can interrupt between steps. Importing a backup manifest fills the
write rows in and ticks them, and every one of those can still be unticked or
pointed at a different file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.partition_checklist import ACTION_LABELS, actions_for_mode
from ..theme import UiRole
from ..widgets import TaskSection, action_button
from .common import page_layout

ACTION_COLUMNS = ("read", "write", "erase")


class PartitionToolsPage(QWidget):
    # Above this, a device read is long enough that the operator gets asked
    # first even though the operation itself is cancellable.
    LARGE_PARTITION_BYTES = 4 * 1024**3

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        page, layout = page_layout(
            "分区与备份", "先读清单，再按分区勾选要做的操作"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(page)

        toolbar = QHBoxLayout()
        self.su_provider_label = QLabel("等待读取分区")
        self.su_provider_label.setObjectName("mutedText")
        self.inventory_button = action_button(
            "刷新清单", "partitionInventoryAction", role=UiRole.SECONDARY
        )
        self.backup_selected_button = action_button(
            "备份所选", "partitionBackupAction", role=UiRole.SECONDARY
        )
        toolbar.addWidget(self.su_provider_label, 1)
        toolbar.addWidget(self.backup_selected_button)
        toolbar.addWidget(self.inventory_button)
        layout.addLayout(toolbar)

        inventory_section = TaskSection("分区清单", "来自设备的 by-name 目录；勾选后备份")
        inventory_section.setObjectName("inventorySection")
        self.inventory_table = self._make_table(
            ("", "分区", "基名", "槽位", "对槽", "容量")
        )
        inventory_section.content_layout.addWidget(self.inventory_table)
        layout.addWidget(inventory_section)

        checklist_section = TaskSection(
            "操作清单", "勾选要执行的动作；写入需要选择镜像。执行前可取消，执行中可中断"
        )
        checklist_section.setObjectName("checklistSection")
        self.mode_label = QLabel("当前模式：未连接")
        self.mode_label.setObjectName("mutedText")
        checklist_section.content_layout.addWidget(self.mode_label)
        self.checklist_table = self._make_table(
            ("分区", "读取", "写入", "擦除", "镜像")
        )
        self.checklist_table.cellDoubleClicked.connect(self._pick_row_image)
        checklist_section.content_layout.addWidget(self.checklist_table)
        checklist_actions = QHBoxLayout()
        self.pick_image_button = action_button(
            "为选中行选择镜像", "partitionPickImageAction", role=UiRole.SECONDARY
        )
        self.pick_image_button.clicked.connect(
            lambda: self._pick_row_image(self.checklist_table.currentRow(), 0)
        )
        self.import_manifest_button = action_button(
            "导入备份 manifest", "partitionImportManifestAction", role=UiRole.SECONDARY
        )
        self.run_checklist_button = action_button(
            "执行清单", "partitionRunChecklistAction", role=UiRole.DANGER
        )
        checklist_actions.addWidget(self.pick_image_button)
        checklist_actions.addWidget(self.import_manifest_button)
        checklist_actions.addStretch(1)
        checklist_actions.addWidget(self.run_checklist_button)
        checklist_section.content_layout.addLayout(checklist_actions)
        layout.addWidget(checklist_section)

        self.validation_status = QLabel("尚未运行分区任务。")
        self.validation_status.setObjectName("inlineStatus")
        layout.addWidget(self.validation_status)

        progress_row = QHBoxLayout()
        self.progress_label = QLabel("等待任务")
        self.progress_label.setObjectName("mutedText")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_label, 1)
        progress_row.addWidget(self.progress_bar)
        layout.addLayout(progress_row)

        self._mode = ""
        self._apply_mode_columns()

    # -- construction helpers ---------------------------------------------

    @staticmethod
    def _make_table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        return table

    # -- inventory ---------------------------------------------------------

    def set_inventory(self, entries) -> None:
        table = self.inventory_table
        table.setRowCount(0)
        for entry in entries:
            row = table.rowCount()
            table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            size = QTableWidgetItem(f"{entry.size:,} bytes")
            size.setData(Qt.ItemDataRole.UserRole, entry.size)
            for column, item in enumerate(
                (
                    check,
                    QTableWidgetItem(entry.name),
                    QTableWidgetItem(entry.base_name),
                    QTableWidgetItem(entry.slot_suffix or "—"),
                    QTableWidgetItem(entry.partner or "—"),
                    size,
                )
            ):
                table.setItem(row, column, item)

    def selected_inventory_names(self) -> tuple[str, ...]:
        names: list[str] = []
        table = self.inventory_table
        for row in range(table.rowCount()):
            check = table.item(row, 0)
            name = table.item(row, 1)
            if (
                check is not None
                and name is not None
                and check.checkState() == Qt.CheckState.Checked
            ):
                names.append(name.text())
        return tuple(names)

    # -- checklist ---------------------------------------------------------

    def set_checklist_partitions(self, names) -> None:
        table = self.checklist_table
        existing = {
            table.item(row, 0).text()
            for row in range(table.rowCount())
            if table.item(row, 0) is not None
        }
        for name in names:
            if name in existing:
                continue
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(name))
            for column, action in enumerate(ACTION_COLUMNS, start=1):
                item = QTableWidgetItem()
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, action)
                table.setItem(row, column, item)
            image = QTableWidgetItem("")
            image.setToolTip("双击选择镜像；或在选中行后点「为选中行选择镜像」")
            table.setItem(row, 4, image)
        self._apply_mode_columns()

    def apply_mode(self, mode: str) -> None:
        self._mode = str(mode or "").strip().casefold()
        label = {"adb": "ADB（可读可写）", "fastboot": "Fastboot（可写可擦）"}.get(
            self._mode, "未连接"
        )
        self.mode_label.setText(f"当前模式：{label}")
        self._apply_mode_columns()

    def _apply_mode_columns(self) -> None:
        try:
            allowed = set(actions_for_mode(self._mode)) if self._mode else set()
        except Exception:
            allowed = set()
        table = self.checklist_table
        for column, action in enumerate(ACTION_COLUMNS, start=1):
            header = ACTION_LABELS[action]
            table.setHorizontalHeaderItem(
                column, QTableWidgetItem(header if action in allowed else f"{header}（不可用）")
            )
            for row in range(table.rowCount()):
                item = table.item(row, column)
                if item is None:
                    continue
                if action in allowed:
                    item.setFlags(
                        Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                    )
                else:
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    item.setCheckState(Qt.CheckState.Unchecked)

    def checklist_items(self):
        """Build the typed list from the table, or raise on an incomplete row."""

        from ...core.errors import VerificationError
        from ...core.partition_checklist import ChecklistItem

        items = []
        table = self.checklist_table
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            if name_item is None:
                continue
            partition = name_item.text()
            image_text = (table.item(row, 4).text() if table.item(row, 4) else "").strip()
            for column, action in enumerate(ACTION_COLUMNS, start=1):
                check = table.item(row, column)
                if check is None or check.checkState() != Qt.CheckState.Checked:
                    continue
                image = Path(image_text) if image_text else None
                if action == "write" and image is None:
                    raise VerificationError(f"{partition} 勾选了写入，但没有选择镜像")
                items.append(
                    ChecklistItem(partition=partition, action=action, image=image)
                )
        if not items:
            raise VerificationError("操作清单为空：请先勾选要执行的动作")
        return items

    def set_row_image(self, row: int, path: Path) -> None:
        item = self.checklist_table.item(row, 4)
        if item is not None:
            item.setText(str(path))
            self.checklist_table.setCurrentCell(row, 4)

    def _pick_row_image(self, row: int, _column: int = 0) -> None:
        if row < 0 or row >= self.checklist_table.rowCount():
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择该分区的镜像")
        if path:
            self.set_row_image(row, Path(path))

    def mark_write_row(self, partition: str, image: Path) -> None:
        """Tick the write action and set the image for one partition."""

        table = self.checklist_table
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            if name_item is None or name_item.text() != partition:
                continue
            check = table.item(row, 2)
            if check is not None and check.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                check.setCheckState(Qt.CheckState.Checked)
            self.set_row_image(row, image)
            return
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
