# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Internal validated UBL patch service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.errors import FlowCancelledError, VerificationError
from .keys import load_patch_catalog_bytes, load_public_blob, verify_key_pair
from .ubl.catalog import PatchCatalog
from .ubl.workflow import (
    UblError,
    preflight_check,
    run_patch,
    validate_patched_image,
    verify_files,
)


@dataclass(frozen=True)
class InternalUblPatchResult:
    output: Path
    output_size: int
    messages: tuple[str, ...]


class InternalUblPatcher:
    """Patch and independently validate an LK image using bundled code/resources."""

    def __init__(
        self,
        *,
        log: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        image_profile: str | None = None,
    ) -> None:
        self._log = log or (lambda _message: None)
        self._cancelled = cancelled or (lambda: False)
        self._image_profile = image_profile

    @staticmethod
    def availability_check() -> None:
        try:
            verify_key_pair()
            InternalUblPatcher._protected_resources()
        except Exception as exc:
            raise VerificationError(f"内部 LK key material 不可用: {exc}") from exc

    @staticmethod
    def _protected_resources() -> tuple[bytes, PatchCatalog]:
        key_blob = load_public_blob()
        catalog = PatchCatalog.from_bytes(load_patch_catalog_bytes())
        return key_blob, catalog

    def inspect(self, original: Path) -> tuple[bool, str]:
        if not original.is_file() or original.stat().st_size <= 0:
            raise VerificationError(f"原始 lk 不存在或为空: {original}")
        try:
            self.availability_check()
            key_blob, catalog = self._protected_resources()
            return preflight_check(
                original,
                key_blob,
                catalog,
                image_profile=self._image_profile,
            )
        except VerificationError:
            raise
        except Exception as exc:
            raise VerificationError(f"内部 LK 预检失败: {exc}") from exc

    def patch_and_verify(self, original: Path, output: Path) -> InternalUblPatchResult:
        if original.resolve() == output.resolve():
            raise VerificationError("原始 lk 与 patched lk 输出路径不能相同")
        if not original.is_file() or original.stat().st_size <= 0:
            raise VerificationError(f"原始 lk 不存在或为空: {original}")
        self.availability_check()
        key_blob, catalog = self._protected_resources()
        self._ensure_not_cancelled()
        output.unlink(missing_ok=True)
        try:
            self._log("内部 UBL 预检")
            _input_size, output_size, signed, steps = run_patch(
                original,
                output,
                key_blob,
                catalog,
                image_profile=self._image_profile,
            )
            self._ensure_not_cancelled()
            ok, verify_steps = verify_files(
                original,
                output,
                key_blob,
                catalog,
                image_profile=self._image_profile,
            )
            if not ok:
                raise VerificationError("内部 UBL 独立验证失败")
            if not output.is_file() or output.stat().st_size != output_size:
                raise VerificationError("内部 UBL 输出大小校验失败")
            messages = tuple((*steps, *verify_steps, f"signed={','.join(signed)}"))
            for message in messages:
                self._log(message)
            return InternalUblPatchResult(output=output, output_size=output_size, messages=messages)
        except FlowCancelledError:
            output.unlink(missing_ok=True)
            raise
        except (UblError, OSError, ValueError) as exc:
            output.unlink(missing_ok=True)
            raise VerificationError(f"内部 UBL 补丁失败: {exc}") from exc

    def validate_patched(self, image: Path) -> None:
        if not image.is_file() or image.stat().st_size <= 0:
            raise VerificationError("patched LK 不存在或为空")
        self.availability_check()
        key_blob, catalog = self._protected_resources()
        ok, steps = validate_patched_image(
            image,
            key_blob,
            catalog,
            image_profile=self._image_profile,
        )
        if not ok:
            detail = next(
                (step for step in steps if not step.endswith(":patched")),
                "invalid",
            )
            raise VerificationError(
                f"patched LK 结构或授权数据校验失败: {detail}"
            )

    def _ensure_not_cancelled(self) -> None:
        if self._cancelled():
            raise FlowCancelledError("用户已取消")
