"""
SPDX-FileCopyrightText: 2025-2026 Roger Ortiz <me@r0rt1z2.com>
SPDX-License-Identifier: GPL-3.0-or-later
"""

__version__ = '3.2.0'

from nebumtk_unlock_tools.lk.vendor.liblk.constants import AddressingMode, Magic
from nebumtk_unlock_tools.lk.vendor.liblk.exceptions import (
    InvalidCertificate,
    InvalidLkPartition,
    NeedleNotFoundException,
)
from nebumtk_unlock_tools.lk.vendor.liblk.image import LkImage
from nebumtk_unlock_tools.lk.vendor.liblk.structures import Certificate, ImageHeader

__all__ = [
    'LkImage',
    'ImageHeader',
    'Certificate',
    'InvalidLkPartition',
    'InvalidCertificate',
    'NeedleNotFoundException',
    'Magic',
    'AddressingMode',
]
