"""Common page layout helpers and compatibility presentation values."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget


OFFICIAL_KMIS = (
    "android12-5.10",
    "android13-5.10",
    "android13-5.15",
    "android14-5.15",
    "android14-6.1",
    "android15-6.6",
    "android16-6.12",
)


def page_layout(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    page.setObjectName("onlinePage")
    page.setMinimumWidth(0)
    page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 12)
    layout.setSpacing(10)
    heading = QLabel(title, page)
    heading.setObjectName("pageHeading")
    layout.addWidget(heading)
    if subtitle:
        description = QLabel(subtitle, page)
        description.setObjectName("sectionDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
    return page, layout


@dataclass(frozen=True, slots=True)
class DestructiveSummary:
    serial: str
    mode: str
    source: str
    source_size: int
    source_sha256: str
    targets: tuple[str, ...]
    slot_effect: str
    cancellation_boundary: str

    def render(self) -> str:
        return (
            f"设备序列号：{self.serial}\n"
            f"当前模式：{self.mode}\n"
            f"来源：{self.source}\n"
            f"来源大小：{self.source_size} bytes\n"
            f"来源 SHA-256：{self.source_sha256}\n"
            f"精确目标：{', '.join(self.targets)}\n"
            f"槽位影响：{self.slot_effect}\n"
            f"取消边界：{self.cancellation_boundary}"
        )
