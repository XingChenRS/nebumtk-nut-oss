# SPDX-FileCopyrightText: 2026 雾月星辰 & MLXC <github@XingChenRS>
# SPDX-License-Identifier: AGPL-3.0-only
"""Internal Vivo Xingc authorization service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.errors import FlowCancelledError, VerificationError
from ..core.fastboot import FastbootClient
from .keys import load_private_pem, require_protocol_capability, verify_key_pair
from .xingc.fastboot import FastbootError, FastbootProtocol, PyUsbFastbootTransport


@dataclass(frozen=True)
class XingcUnlockResult:
    already_unlocked: bool = False


@dataclass(frozen=True)
class XingcAuthorizationResult:
    payload_size: int


class XingcUnlockService:
    """Execute the signed Xingc exchange against one exact USB Fastboot device."""

    def __init__(
        self,
        *,
        log: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._log = log or (lambda _message: None)
        self._cancelled = cancelled or (lambda: False)

    @staticmethod
    def readiness_check(*, signing: bool = True) -> None:
        try:
            require_protocol_capability()
            if signing:
                verify_key_pair()
            from .usb_backend import get_libusb_backend

            get_libusb_backend()
        except Exception as exc:
            raise VerificationError(f"Xingc 受保护环境不可用: {exc}") from exc

    def read_token(self, serial: str) -> str:
        if not serial:
            raise VerificationError("未指定 Xingc Fastboot serial")
        self.readiness_check(signing=False)
        protocol: FastbootProtocol | None = None
        try:
            protocol = FastbootProtocol(PyUsbFastbootTransport(serial=serial))
            token = protocol.get_unlock_token().strip()
            if not token:
                raise VerificationError("Xingc 未返回授权 token")
            return token
        except VerificationError:
            raise
        except (FastbootError, OSError, RuntimeError, ValueError) as exc:
            raise VerificationError(f"Xingc token 获取失败: {exc}") from exc
        finally:
            if protocol is not None:
                try:
                    protocol.close()
                except Exception as exc:
                    self._log(f"释放 Xingc USB 接口失败: {exc}")

    def authorize(self, serial: str, payload: bytes) -> XingcAuthorizationResult:
        if not serial:
            raise VerificationError("未指定 Xingc Fastboot serial")
        self.readiness_check(signing=False)
        if not payload:
            raise VerificationError("Xingc 授权 payload 为空")
        protocol: FastbootProtocol | None = None
        try:
            protocol = FastbootProtocol(PyUsbFastbootTransport(serial=serial))
            result = protocol.download(payload)
            if result.status != "OKAY":
                raise VerificationError(f"Xingc 授权 payload 传输失败: {result.message}")
            return XingcAuthorizationResult(payload_size=len(payload))
        except VerificationError:
            raise
        except (FastbootError, OSError, RuntimeError, ValueError) as exc:
            raise VerificationError(f"Xingc 授权失败: {exc}") from exc
        finally:
            if protocol is not None:
                try:
                    protocol.close()
                except Exception as exc:
                    self._log(f"释放 Xingc USB 接口失败: {exc}")

    def lock(self, serial: str, fastboot: FastbootClient) -> None:
        if not serial:
            raise VerificationError("未指定 Xingc Fastboot serial")
        self.readiness_check(signing=False)
        if not fastboot.is_unlocked():
            raise VerificationError("fastboot 已报告 bootloader 锁定，拒绝重复锁定")
        protocol: FastbootProtocol | None = None
        try:
            protocol = FastbootProtocol(PyUsbFastbootTransport(serial=serial))
            result = protocol.command("xingc lock")
            if result.status != "OKAY":
                raise VerificationError(f"Xingc lock 失败: {result.message}")
        except VerificationError:
            raise
        except (FastbootError, OSError, RuntimeError, ValueError) as exc:
            raise VerificationError(f"Xingc 锁定交换失败: {exc}") from exc
        finally:
            if protocol is not None:
                try:
                    protocol.close()
                except Exception as exc:
                    self._log(f"释放 Xingc USB 接口失败: {exc}")
        if fastboot.is_unlocked():
            raise VerificationError("Xingc 返回成功但 fastboot 仍报告已解锁")

    def unlock(self, serial: str, fastboot: FastbootClient) -> XingcUnlockResult:
        if not serial:
            raise VerificationError("未指定 Xingc Fastboot serial")
        self.readiness_check()
        self._ensure_not_cancelled()
        # The signed unlock token is always requested and delivered.  A stale
        # ``fastboot getvar unlocked`` reading must not short-circuit the flow,
        # because only the device's own ``xingc unlock`` response proves the
        # authorization actually applied.
        self._ensure_not_cancelled()
        protocol: FastbootProtocol | None = None
        committed = False
        try:
            transport = PyUsbFastbootTransport(serial=serial)
            protocol = FastbootProtocol(transport)
            self._ensure_not_cancelled()
            token = protocol.get_unlock_token()
            self._ensure_not_cancelled()
            payload = self.build_payload(token, target=serial)
            self._ensure_not_cancelled()
            download = protocol.download(payload)
            if download.status != "OKAY":
                raise VerificationError(f"Xingc 授权 payload 传输失败: {download.message}")
            committed = True
            unlock = protocol.command("xingc unlock")
            if unlock.status != "OKAY":
                raise VerificationError(f"Xingc unlock 失败: {unlock.message}")
        except VerificationError:
            raise
        except (FastbootError, OSError, RuntimeError, ValueError) as exc:
            raise VerificationError(f"Xingc USB 交换失败: {exc}") from exc
        finally:
            if protocol is not None:
                try:
                    protocol.close()
                except Exception as exc:
                    self._log(f"释放 Xingc USB 接口失败: {exc}")

        if committed and self._cancelled():
            self._log("Xingc 授权已完成；取消将在状态确认后生效")
        if not fastboot.is_unlocked():
            raise VerificationError("Xingc 返回成功但 fastboot 仍报告未解锁")
        self._ensure_not_cancelled()
        return XingcUnlockResult()

    def build_payload(self, token: str, *, target: str = "") -> bytes:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        self.readiness_check()
        if not token:
            raise VerificationError("Xingc token 为空")
        try:
            token_bytes = token.encode("ascii")
        except UnicodeEncodeError as exc:
            raise VerificationError("Xingc token 不是 ASCII") from exc
        private_key = serialization.load_pem_private_key(load_private_pem(), password=None)
        signature = private_key.sign(token_bytes, padding.PKCS1v15(), hashes.SHA256())
        if len(signature) != 256:
            raise VerificationError("Xingc 私钥不是预期的 RSA-2048 密钥")
        return (token + signature.hex()).encode("ascii")

    def _ensure_not_cancelled(self) -> None:
        if self._cancelled():
            raise FlowCancelledError("用户已取消")
