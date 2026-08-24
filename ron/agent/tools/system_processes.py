"""Read-only process awareness for diagnosing local Windows load."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from ron.agent.models import ToolExecutionContext, ToolResult, ToolRisk, ToolStatus
from ron.agent.registry import ToolSpec


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def build_top_processes_tool() -> ToolSpec:
    """Report a bounded list of heavyweight processes without arbitrary shell input."""

    executable = _powershell()

    def availability() -> tuple[bool, str]:
        if os.name != "nt":
            return False, "Process awareness is available on Windows only."
        if executable is None:
            return False, "PowerShell is unavailable, so process awareness is offline."
        return True, "Windows process awareness is ready."

    def get_top_processes(
        arguments: dict[str, str | int],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del arguments
        context.checkpoint()
        if executable is None:
            return ToolResult(
                "get_top_processes",
                ToolStatus.UNSUPPORTED,
                "PowerShell is unavailable, so I can't inspect running processes.",
            )
        script = (
            "Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 "
            "ProcessName,Id,@{N='CPUSeconds';E={[math]::Round($_.CPU,1)}},"
            "@{N='MemoryMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | "
            "ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=max(0.5, min(4.0, context.remaining_seconds)),
                check=False,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return ToolResult(
                "get_top_processes",
                ToolStatus.FAILED,
                "I couldn't inspect the running Windows processes safely.",
            )
        context.checkpoint()
        if completed.returncode != 0:
            return ToolResult(
                "get_top_processes",
                ToolStatus.FAILED,
                "Windows did not return a usable process summary.",
            )
        try:
            payload = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError:
            return ToolResult(
                "get_top_processes",
                ToolStatus.FAILED,
                "Windows returned malformed process information.",
            )
        raw_items = payload if isinstance(payload, list) else [payload]
        processes: list[dict[str, object]] = []
        for item in raw_items[:8]:
            if not isinstance(item, dict):
                continue
            name = item.get("ProcessName")
            pid = item.get("Id")
            cpu = item.get("CPUSeconds")
            memory = item.get("MemoryMB")
            if not isinstance(name, str) or not isinstance(pid, int):
                continue
            processes.append(
                {
                    "name": name,
                    "pid": pid,
                    "cpu_seconds": cpu if isinstance(cpu, (int, float)) else None,
                    "memory_mb": memory if isinstance(memory, (int, float)) else None,
                }
            )
        if not processes:
            message = "I couldn't find any process information to report."
        else:
            visible = ", ".join(
                f"{item['name']} ({item['memory_mb']} MB)"
                for item in processes[:5]
            )
            message = (
                "The heavier running processes include "
                f"{visible}. CPU time here is cumulative, so combine this with "
                "the live system-performance reading when diagnosing fan noise."
            )
        return ToolResult(
            "get_top_processes",
            ToolStatus.SUCCESS,
            message,
            data={"processes": processes},
        )

    return ToolSpec(
        "get_top_processes",
        "Read a bounded list of high CPU-time Windows processes and their memory use.",
        {},
        ToolRisk.READ_ONLY,
        get_top_processes,
        timeout_seconds=5.0,
        availability=availability,
    )
