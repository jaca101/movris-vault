"""Detectar el iPhone, emparejarlo y leer sus datos."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError, NotPairedError
from pymobiledevice3.lockdown import LockdownClient, create_using_usbmux
from pymobiledevice3.usbmux import list_devices

SYS_USB = Path("/sys/bus/usb/devices")
APPLE_VENDOR_ID = "05ac"


class DeviceState(enum.Enum):
    ABSENT = "absent"          # no hay ningún iPhone enchufado
    RECONNECT = "reconnect"    # está en el USB pero usbmuxd no lo ve (pasa tras una suspensión)
    UNTRUSTED = "untrusted"    # hay que pulsar «Confiar» en el iPhone
    READY = "ready"


@dataclass(frozen=True)
class DeviceInfo:
    udid: str
    name: str
    product_type: str
    ios_version: str
    capacity: int
    used: int
    encrypted: bool


@dataclass(frozen=True)
class DeviceStatus:
    state: DeviceState
    info: Optional[DeviceInfo] = None


def iphone_on_usb(sys_usb: Path = SYS_USB) -> bool:
    """Mira directamente en el kernel si hay un iPhone o iPad enchufado, aunque usbmuxd no lo vea."""
    for dev in sys_usb.glob("*"):
        try:
            if (dev / "idVendor").read_text().strip() != APPLE_VENDOR_ID:
                continue
            product = (dev / "product").read_text().strip()
        except OSError:
            continue
        if product.startswith(("iPhone", "iPad")):
            return True
    return False


async def usb_udids() -> list[str]:
    try:
        devices = await list_devices()
    except ConnectionFailedToUsbmuxdError:
        # usbmuxd se apaga solo cuando no hay ningún iPhone conectado
        return []
    return [d.serial for d in devices if d.is_usb]


async def connect(udid: Optional[str] = None, pair: bool = False, pair_timeout: Optional[float] = None) -> LockdownClient:
    """Conecta con el iPhone. Con `pair=True` pide «Confiar» si hace falta y espera la respuesta."""
    if udid is None:
        udids = await usb_udids()
        if not udids:
            raise ConnectionFailedToUsbmuxdError()
        udid = udids[0]
    lockdown = await create_using_usbmux(serial=udid, autopair=pair, pair_timeout=pair_timeout, connection_type="USB")
    if not lockdown.paired:
        await lockdown.close()
        raise NotPairedError()
    return lockdown


async def read_info(lockdown: LockdownClient) -> DeviceInfo:
    disk = await lockdown.get_value(domain="com.apple.disk_usage")
    capacity = int(disk.get("TotalDataCapacity", 0))
    encrypted = await lockdown.get_value(domain="com.apple.mobile.backup", key="WillEncrypt")
    return DeviceInfo(
        udid=lockdown.udid,
        name=await lockdown.get_value(key="DeviceName"),
        product_type=await lockdown.get_value(key="ProductType"),
        ios_version=await lockdown.get_value(key="ProductVersion"),
        capacity=capacity,
        used=capacity - int(disk.get("AmountDataAvailable", 0)),
        encrypted=bool(encrypted),
    )


async def probe() -> DeviceStatus:
    """Estado actual del iPhone, sin pedir nada en su pantalla."""
    udids = await usb_udids()
    if not udids:
        return DeviceStatus(DeviceState.RECONNECT if iphone_on_usb() else DeviceState.ABSENT)
    try:
        lockdown = await connect(udids[0])
    except NotPairedError:
        return DeviceStatus(DeviceState.UNTRUSTED)
    try:
        return DeviceStatus(DeviceState.READY, await read_info(lockdown))
    finally:
        await lockdown.close()
