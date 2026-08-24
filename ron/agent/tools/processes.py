"""Tools for processes that Ron itself started and tracks."""

from __future__ import annotations

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.processes import ManagedProcessManager
from ron.agent.registry import ToolSpec


def build_managed_processes_tool(manager: ManagedProcessManager) -> ToolSpec:
    def get_processes(arguments: dict[str, str | int]) -> ToolResult:
        process_id = arguments.get("process_id")
        if isinstance(process_id, int):
            snapshot = manager.snapshot(process_id)
            snapshots = (snapshot,) if snapshot is not None else ()
        else:
            snapshots = manager.snapshots()[-10:]
        if not snapshots:
            return ToolResult(
                "get_managed_processes",
                ToolStatus.SUCCESS,
                "Ron has not started any tracked local processes yet.",
                data={"processes": []},
            )
        message = " ".join(
            f"Process {item.process_id} ({item.label}) is {item.status}."
            for item in snapshots
        )
        return ToolResult(
            "get_managed_processes",
            ToolStatus.SUCCESS,
            message,
            data={
                "processes": [
                    {
                        "process_id": item.process_id,
                        "label": item.label,
                        "pid": item.pid,
                        "status": item.status,
                        "exit_code": item.exit_code,
                        "log_path": item.log_path,
                    }
                    for item in snapshots
                ]
            },
        )

    return ToolSpec(
        name="get_managed_processes",
        description="Read status for local processes that Ron deliberately started.",
        arguments={
            "process_id": ToolArgument(
                ToolArgumentKind.INTEGER,
                minimum=1,
                maximum=999_999,
                required=False,
            )
        },
        risk=ToolRisk.READ_ONLY,
        handler=get_processes,
        timeout_seconds=2.0,
    )


def build_stop_managed_process_tool(manager: ManagedProcessManager) -> ToolSpec:
    def stop_process(arguments: dict[str, str | int]) -> ToolResult:
        process_id = arguments.get("process_id")
        snapshot = manager.stop(process_id if isinstance(process_id, int) else None)
        if snapshot is None:
            return ToolResult(
                "stop_managed_process",
                ToolStatus.FAILED,
                "I don't have a tracked process to stop.",
            )
        return ToolResult(
            "stop_managed_process",
            ToolStatus.SUCCESS,
            f"Process {snapshot.process_id} ({snapshot.label}) is now {snapshot.status}.",
            data={
                "process_id": snapshot.process_id,
                "status": snapshot.status,
                "changed": True,
            },
        )

    return ToolSpec(
        name="stop_managed_process",
        description="Stop one local process that Ron previously started and is tracking.",
        arguments={
            "process_id": ToolArgument(
                ToolArgumentKind.INTEGER,
                minimum=1,
                maximum=999_999,
                required=False,
            )
        },
        risk=ToolRisk.REVERSIBLE,
        handler=stop_process,
        timeout_seconds=4.0,
    )
