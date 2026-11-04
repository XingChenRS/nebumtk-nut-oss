from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class FastbootError(RuntimeError):
    pass


class FastbootFail(FastbootError):
    pass


class FastbootTimeout(FastbootError):
    pass


class FastbootTransport(Protocol):
    def write(self, data: bytes, timeout_ms: int) -> int:
        ...

    def read(self, size: int, timeout_ms: int) -> bytes:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class FastbootResult:
    status: str
    message: str = ""
    infos: tuple[str, ...] = ()
    data: bytes = b""
    expected_size: int | None = None
    raw_packets: tuple[bytes, ...] = field(default_factory=tuple)


# Cap INFO spam so a broken device/firmware cannot hang the host forever.
_MAX_INFO_PACKETS = 64


class FastbootProtocol:
    def __init__(self, transport: FastbootTransport, timeout_ms: int = 5000) -> None:
        self.transport = transport
        self.timeout_ms = timeout_ms

    def close(self) -> None:
        self.transport.close()

    def command(self, command: str, data: bytes | None = None) -> FastbootResult:
        encoded = command.encode("ascii")
        if len(encoded) > 64:
            raise ValueError("fastboot command must fit in one 64-byte packet")
        written = self.transport.write(encoded, self.timeout_ms)
        if written != len(encoded):
            raise FastbootError(f"short USB write: sent {written} of {len(encoded)} bytes")
        return self._read_response(data=data)

    def download(self, data: bytes) -> FastbootResult:
        return self.command(f"download:{len(data):08x}", data=data)

    def get_unlock_token(self) -> str:
        response = self.command("getinfo unlock")
        if response.status == "FAIL":
            raise FastbootFail(f"getinfo unlock failed: {response.message}")
        token = response.data
        if not token and response.expected_size:
            token = self._try_dumpinfo(response.expected_size)
        if not token:
            raise FastbootError("getinfo unlock did not return token data")
        if response.expected_size is not None and len(token) < response.expected_size:
            raise FastbootError(
                "getinfo unlock returned a truncated token: "
                f"got {len(token)} of {response.expected_size} bytes"
            )
        return token.decode("ascii").rstrip("\x00\r\n")

    def _read_response(
        self,
        data: bytes | None = None,
        *,
        ignore_vendor_size: bool = False,
    ) -> FastbootResult:
        infos: list[str] = []
        raw_packets: list[bytes] = []
        while True:
            packet = self._read_packet()
            raw_packets.append(packet)
            status = packet[:4].decode("ascii", errors="replace")
            payload = packet[4:].rstrip(b"\x00")
            text = payload.decode("ascii", errors="replace")
            if status == "INFO":
                infos.append(text)
                if len(infos) > _MAX_INFO_PACKETS:
                    raise FastbootError(
                        f"too many INFO packets (>{_MAX_INFO_PACKETS}); device may be stuck"
                    )
                continue
            if status == "OKAY":
                return FastbootResult(status, text, tuple(infos), raw_packets=tuple(raw_packets))
            if status == "FAIL":
                return FastbootResult(status, text, tuple(infos), raw_packets=tuple(raw_packets))
            if status == "DATA":
                if text.startswith("SIZE"):
                    # During download handshake / post-write wait: SIZE is stale noise.
                    # During getinfo (data is None, ignore_vendor_size=False): SIZE is the upload size.
                    if data is not None or ignore_vendor_size:
                        infos.append(f"ignored stale vendor DATA/SIZE: {text}")
                        continue
                    size = _parse_hex_size(text[4:12], "vendor DATA/SIZE response")
                    try:
                        upload = self._read_exact(size)
                    except FastbootTimeout:
                        upload = b""
                    return FastbootResult(
                        "DATA",
                        text,
                        tuple(infos),
                        data=upload,
                        expected_size=size,
                        raw_packets=tuple(raw_packets),
                    )
                return self._handle_data_response(text, data, tuple(infos), raw_packets)
            raise FastbootError(f"unknown fastboot status {status!r}: {packet!r}")

    def _handle_data_response(
        self,
        text: str,
        data: bytes | None,
        infos: tuple[str, ...],
        raw_packets: list[bytes],
    ) -> FastbootResult:
        size = _parse_hex_size(text[:8], "DATA response")
        if data is None:
            # Standard DATA%08x upload: drain bulk IN so the next command is not desynced.
            payload = self._read_exact(size)
            return FastbootResult(
                "DATA",
                text,
                infos,
                data=payload,
                expected_size=size,
                raw_packets=tuple(raw_packets),
            )
        if len(data) != size:
            raise FastbootError(f"download size mismatch: device wants {size}, host has {len(data)}")
        written = self.transport.write(data, self.timeout_ms)
        if written != len(data):
            raise FastbootError(f"short USB write during download: sent {written} of {len(data)} bytes")
        # After payload write, any further DATASIZE is stale; wait for OKAY/FAIL only.
        final = self._read_response(data=None, ignore_vendor_size=True)
        return FastbootResult(
            final.status,
            final.message,
            infos + final.infos,
            expected_size=size,
            raw_packets=tuple(raw_packets) + final.raw_packets,
        )

    def _try_dumpinfo(self, size: int) -> bytes:
        command = f"dumpinfo {size:08x}".encode("ascii")
        self.transport.write(command, self.timeout_ms)
        first = self._read_packet()
        status = first[:4].decode("ascii", errors="replace")
        if status == "FAIL":
            raise FastbootFail(first[4:].decode("ascii", errors="replace").rstrip("\x00"))
        if status != "OKAY":
            if status == "DATA" and first[4:].decode("ascii", errors="replace").startswith("SIZE"):
                # Stale SIZE on dumpinfo: ignore and keep waiting for OKAY/data is unsafe;
                # treat as unexpected unless we already have an OKAY path.
                raise FastbootError(f"dumpinfo returned unexpected status {status!r}: {first!r}")
            raise FastbootError(f"dumpinfo returned unexpected status {status!r}: {first!r}")
        return self._read_exact(size)

    def _read_packet(self) -> bytes:
        try:
            packet = self.transport.read(64, self.timeout_ms)
        except TimeoutError as exc:
            raise FastbootTimeout("timed out waiting for fastboot response") from exc
        if not packet:
            raise FastbootTimeout("empty fastboot response")
        return bytes(packet)

    def _read_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            try:
                chunk = self.transport.read(max(64, remaining), self.timeout_ms)
            except TimeoutError as exc:
                got = size - remaining
                raise FastbootTimeout(
                    f"timed out waiting for {size} bytes of fastboot data (got {got})"
                ) from exc
            if not chunk:
                got = size - remaining
                raise FastbootTimeout(
                    f"empty read while waiting for {size} bytes of fastboot data (got {got})"
                )
            chunk = bytes(chunk)
            chunks.append(chunk[:remaining])
            remaining -= min(len(chunk), remaining)
        return b"".join(chunks)


class PyUsbFastbootTransport:
    def __init__(
        self,
        vid: int | None = None,
        pid: int | None = None,
        serial: str | None = None,
    ) -> None:
        usb_core, usb_util, backend = _load_pyusb()
        self._usb_util = usb_util
        self.device = _find_fastboot_device(usb_core, usb_util, backend, vid, pid, serial)
        self.interface, self.ep_in, self.ep_out = _claim_fastboot_interface(self.device, usb_util)

    def write(self, data: bytes, timeout_ms: int) -> int:
        return int(self.ep_out.write(data, timeout=timeout_ms))

    def read(self, size: int, timeout_ms: int) -> bytes:
        try:
            data = self.ep_in.read(size, timeout=timeout_ms)
        except Exception as exc:
            if exc.__class__.__name__ == "USBTimeoutError":
                raise TimeoutError(str(exc)) from exc
            raise
        return bytes(data)

    def close(self) -> None:
        try:
            self._usb_util.release_interface(self.device, self.interface.bInterfaceNumber)
        finally:
            self._usb_util.dispose_resources(self.device)


def _parse_hex_size(text: str, context: str) -> int:
    if len(text) < 8:
        raise FastbootError(f"{context} has no 8-digit hex size: {text!r}")
    try:
        return int(text[:8], 16)
    except ValueError as exc:
        raise FastbootError(f"{context} has invalid hex size: {text[:8]!r}") from exc


def _load_pyusb():
    try:
        import usb.core
        import usb.util
    except Exception as exc:  # pragma: no cover - depends on host packages
        raise RuntimeError("PyUSB is required for native fastboot USB access") from exc
    from ..usb_backend import get_libusb_backend

    return usb.core, usb.util, get_libusb_backend()


def _find_fastboot_device(
    usb_core,
    usb_util,
    backend,
    vid: int | None,
    pid: int | None,
    serial: str | None,
):
    kwargs = {}
    if vid is not None:
        kwargs["idVendor"] = vid
    if pid is not None:
        kwargs["idProduct"] = pid
    devices = list(usb_core.find(find_all=True, backend=backend, **kwargs))
    candidates = []
    for device in devices:
        if serial:
            try:
                current = usb_util.get_string(device, device.iSerialNumber)
            except Exception:
                current = None
            if current != serial:
                continue
        if _has_fastboot_interface(device):
            candidates.append(device)
    if not candidates:
        raise FastbootError("no USB fastboot device found")
    if len(candidates) > 1 and (vid is None or pid is None or serial is None):
        names = ", ".join(f"{dev.idVendor:04x}:{dev.idProduct:04x}" for dev in candidates)
        raise FastbootError(f"multiple fastboot devices found; specify --vid/--pid/--serial ({names})")
    return candidates[0]


def _has_fastboot_interface(device) -> bool:
    try:
        for cfg in device:
            for intf in cfg:
                if _is_fastboot_interface(intf):
                    return True
    except Exception:
        return False
    return False


def _claim_fastboot_interface(device, usb_util):
    try:
        device.set_configuration()
    except Exception as exc:
        raise FastbootError(
            f"无法配置 USB 设备（可能被 adb/fastboot/其他进程占用）: {exc}"
        ) from exc
    for cfg in device:
        for intf in cfg:
            if not _is_fastboot_interface(intf):
                continue
            try:
                if device.is_kernel_driver_active(intf.bInterfaceNumber):
                    device.detach_kernel_driver(intf.bInterfaceNumber)
            except (NotImplementedError, AttributeError):
                pass
            try:
                usb_util.claim_interface(device, intf.bInterfaceNumber)
            except Exception as exc:
                raise FastbootError(
                    f"无法占用 fastboot 接口（请关闭 adb/fastboot.exe 后重试）: {exc}"
                ) from exc
            ep_in = usb_util.find_descriptor(
                intf,
                custom_match=lambda ep: usb_util.endpoint_direction(ep.bEndpointAddress)
                == usb_util.ENDPOINT_IN
                and usb_util.endpoint_type(ep.bmAttributes) == usb_util.ENDPOINT_TYPE_BULK,
            )
            ep_out = usb_util.find_descriptor(
                intf,
                custom_match=lambda ep: usb_util.endpoint_direction(ep.bEndpointAddress)
                == usb_util.ENDPOINT_OUT
                and usb_util.endpoint_type(ep.bmAttributes) == usb_util.ENDPOINT_TYPE_BULK,
            )
            if ep_in is None or ep_out is None:
                usb_util.release_interface(device, intf.bInterfaceNumber)
                continue
            return intf, ep_in, ep_out
    raise FastbootError("USB device has no usable fastboot bulk endpoints")


def _is_fastboot_interface(interface) -> bool:
    return (
        int(interface.bInterfaceClass) == 0xFF
        and int(interface.bInterfaceSubClass) == 0x42
        and int(interface.bInterfaceProtocol) == 0x03
    )
