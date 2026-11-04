# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Unified exception types."""


class NebumtkError(Exception):
    """Base error for the unlock tool."""


class EnvError(NebumtkError):
    """adb/fastboot environment is not configured or invalid."""


class AdbError(NebumtkError):
    """adb command failed."""


class AdbOfflineError(AdbError):
    """Device is offline or disconnected during an adb command."""


class AdbTimeoutError(AdbError):
    """The local adb client exceeded its bounded command window."""


class FastbootError(NebumtkError):
    """fastboot command failed."""


class RootEscalationError(NebumtkError):
    """Failed to obtain root shell."""


class PartitionError(NebumtkError):
    """Partition backup or write failed."""


class VerificationError(NebumtkError):
    """Step verification failed."""


class DeviceMatchError(NebumtkError):
    """Device could not be matched to a supported model."""


class ResourceError(NebumtkError):
    """Required resource file is missing."""


class PatchNotAvailableError(NebumtkError):
    """Vivo LK patch provider is not available."""


class FlowCancelledError(NebumtkError):
    """Unlock flow was cancelled by the user."""
# provenance: NBMTK-canary-7f3a91c4e8b2d05f
