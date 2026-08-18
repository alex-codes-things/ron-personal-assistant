"""Read-only Windows battery and performance summaries."""

from __future__ import annotations

import ctypes
import os
import shutil
from ctypes import wintypes
from pathlib import Path

from ron.agent.models import ToolExecutionContext, ToolResult, ToolRisk, ToolStatus
from ron.agent.registry import ToolSpec


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _filetime_value(value: wintypes.FILETIME) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


def _battery() -> dict[str, object]:
    if os.name != "nt":
        raise OSError("Battery status is available on Windows only")
    status = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        raise OSError("Windows did not return power status")
    percent = None if status.BatteryLifePercent == 255 else int(status.BatteryLifePercent)
    return {
        "percent": percent,
        "charging": bool(status.BatteryFlag & 8),
        "plugged_in": status.ACLineStatus == 1,
        "battery_present": status.BatteryFlag != 128,
    }


def _performance(context: ToolExecutionContext, root: Path) -> dict[str, object]:
    if os.name != "nt":
        raise OSError("Performance status is available on Windows only")
    idle_a = wintypes.FILETIME()
    kernel_a = wintypes.FILETIME()
    user_a = wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle_a), ctypes.byref(kernel_a), ctypes.byref(user_a)
    ):
        raise OSError("Windows did not return CPU status")
    if context.cancel_event.wait(min(0.15, context.remaining_seconds)):
        context.checkpoint()
    context.checkpoint()
    idle_b = wintypes.FILETIME()
    kernel_b = wintypes.FILETIME()
    user_b = wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle_b), ctypes.byref(kernel_b), ctypes.byref(user_b)
    )
    idle = _filetime_value(idle_b) - _filetime_value(idle_a)
    total = (
        _filetime_value(kernel_b)
        - _filetime_value(kernel_a)
        + _filetime_value(user_b)
        - _filetime_value(user_a)
    )
    cpu_percent = round(max(0.0, min(100.0, 100.0 * (total - idle) / total)), 1) if total else 0.0
    memory = _MemoryStatus()
    memory.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError("Windows did not return memory status")
    disk = shutil.disk_usage(root.anchor or root)
    return {
        "cpu_percent": cpu_percent,
        "memory_percent": int(memory.dwMemoryLoad),
        "memory_available_gb": round(memory.ullAvailPhys / (1024**3), 1),
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "disk_percent": round(100.0 * disk.used / disk.total, 1),
    }


def build_battery_tool() -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if os.name != "nt":
            return False, "Battery status is available on Windows only."
        return True, "Windows battery status is ready."

    def get_battery(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        del arguments
        context.checkpoint()
        try:
            data = _battery()
        except OSError:
            return ToolResult(
                "get_battery_status",
                ToolStatus.FAILED,
                "I couldn't read the battery status.",
            )
        if not data["battery_present"]:
            message = "Windows reports that this computer has no battery."
        else:
            level = data["percent"]
            power = "plugged in" if data["plugged_in"] else "on battery power"
            charging = " and charging" if data["charging"] else ""
            level_text = f"{level}%" if isinstance(level, int) else "an unknown level"
            message = (
                f"The battery is at {level_text} and the computer is "
                f"{power}{charging}."
            )
        return ToolResult("get_battery_status", ToolStatus.SUCCESS, message, data=data)

    return ToolSpec(
        "get_battery_status",
        "Read the Windows battery level and charging state.",
        {},
        ToolRisk.READ_ONLY,
        get_battery,
        timeout_seconds=2.0,
        availability=availability,
    )


def build_performance_tool(project_root: Path) -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if os.name != "nt":
            return False, "Performance status is available on Windows only."
        return True, "Windows performance status is ready."

    def get_performance(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        del arguments
        try:
            data = _performance(context, project_root)
        except OSError:
            return ToolResult(
                "get_system_performance",
                ToolStatus.FAILED,
                "I couldn't read Windows performance information.",
            )
        message = (
            f"CPU is around {data['cpu_percent']}%, memory is {data['memory_percent']}% "
            f"with {data['memory_available_gb']} GB available, and the main drive is "
            f"{data['disk_percent']}% full with {data['disk_free_gb']} GB free."
        )
        return ToolResult("get_system_performance", ToolStatus.SUCCESS, message, data=data)

    return ToolSpec(
        "get_system_performance",
        "Read a bounded CPU, memory and main-drive summary.",
        {},
        ToolRisk.READ_ONLY,
        get_performance,
        timeout_seconds=3.0,
        availability=availability,
    )
