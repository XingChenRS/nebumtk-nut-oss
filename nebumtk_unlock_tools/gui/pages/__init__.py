"""Task-oriented pages and compatibility class exports."""

from .common import DestructiveSummary, OFFICIAL_KMIS
from .device import DevicePage
from .images_recovery import ImagesRecoveryPage
from .partitions import PartitionToolsPage
from .root_unlock import RootUnlockPage
from .tools_settings import ToolsSettingsPage

# Compatibility names keep the existing orchestration imports stable while the
# visible information architecture uses the new task names.
AuthorizedFeaturesPage = RootUnlockPage
ImagesBrandsPage = ImagesRecoveryPage
AdvancedPage = ToolsSettingsPage

__all__ = [
    "AdvancedPage",
    "AuthorizedFeaturesPage",
    "DestructiveSummary",
    "DevicePage",
    "ImagesBrandsPage",
    "ImagesRecoveryPage",
    "OFFICIAL_KMIS",
    "PartitionToolsPage",
    "RootUnlockPage",
    "ToolsSettingsPage",
]
