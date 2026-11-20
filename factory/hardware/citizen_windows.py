"""Resolve Citizen's ctvrcom ports through the live Windows PnP devices."""
from __future__ import annotations

import ctypes
import re
from ctypes import wintypes


def _present_devices(cfgmgr, service: str) -> list[str]:
    # FILTER_SERVICE | FILTER_PRESENT | DONOTGENERATE: discovery must be read-only.
    flags = 0x2 | 0x100 | 0x10000040
    for _ in range(2):
        size = wintypes.ULONG()
        if cfgmgr.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), service, flags):
            break
        if not size.value:
            break
        ids = ctypes.create_unicode_buffer(size.value)
        result = cfgmgr.CM_Get_Device_ID_ListW(service, ids, size, flags)
        if result == 0:
            return [device.upper() for device in ids[:].split("\0") if device]
        if result != 0x1A:  # CR_BUFFER_SMALL: a device arrived after sizing.
            break
    return []


def _started_devnode(cfgmgr, device: str) -> int | None:
    instance = wintypes.DWORD()
    if cfgmgr.CM_Locate_DevNodeW(ctypes.byref(instance), device, 0):
        return None
    status = wintypes.ULONG()
    problem = wintypes.ULONG()
    if cfgmgr.CM_Get_DevNode_Status(ctypes.byref(status), ctypes.byref(problem),
                                   instance, 0):
        return None
    if status.value & 0x8 and not status.value & 0x400:  # DN_STARTED, DN_HAS_PROBLEM
        return instance.value
    return None


def _registry_strings(key: int, name: str) -> set[str]:
    import winreg

    value, kind = winreg.QueryValueEx(key, name)
    if kind == winreg.REG_SZ:
        return {value.upper()} if value else set()
    if kind == winreg.REG_MULTI_SZ:
        return {item.upper() for item in value if item}
    raise ValueError(f"{name} must contain strings")


def _port_mapping(cfgmgr, device: str, target: str, serial: str) -> str | None:
    import winreg

    instance = _started_devnode(cfgmgr, device)
    if instance is None:
        return None
    key = wintypes.HKEY()
    # Citizen's DDInstall.HW writes TargetDevice to the device hardware key.
    if cfgmgr.CM_Open_DevNode_Key(instance, winreg.KEY_QUERY_VALUE, 0, 1,
                                 ctypes.byref(key), 0):
        return None
    try:
        port, _ = winreg.QueryValueEx(key.value, "PortName")
        if target not in _registry_strings(key.value, "TargetDevice"):
            return None
        try:
            serials = _registry_strings(key.value, "TargetSerialNumber")
        except FileNotFoundError:
            serials = set()
        if serials and serial not in serials:
            return None
    except (OSError, ValueError):
        return None
    finally:
        winreg.CloseKey(key.value)
    if not isinstance(port, str) or not re.fullmatch(r"COM[1-9][0-9]*", port, re.I):
        return None
    return port.upper()


def find_ctvrcom_port(vid: int, pid: int) -> str | None:
    cfgmgr = ctypes.WinDLL("cfgmgr32")
    target = f"VID_{vid:04X}&PID_{pid:04X}"
    prefix = f"USB\\{target}\\"
    printers = [device for device in _present_devices(cfgmgr, "ctusbdr")
                if device.startswith(prefix)]
    if len(printers) != 1 or _started_devnode(cfgmgr, printers[0]) is None:
        return None

    serial = printers[0][len(prefix):]
    ports = set()
    for device in _present_devices(cfgmgr, "ctvrcom"):
        port = _port_mapping(cfgmgr, device, target, serial)
        if port:
            ports.add(port)
    return next(iter(ports)) if len(ports) == 1 else None
