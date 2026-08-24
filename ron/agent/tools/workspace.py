"""Bounded developer-workspace actions for Ron's own repository."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolExecutionContext,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.processes import ManagedProcessManager
from ron.agent.registry import ToolSpec

if TYPE_CHECKING:
    from ron.network import NetworkService


def _find_adb() -> str | None:
    direct = shutil.which("adb")
    if direct:
        return direct
    candidates = []
    for variable in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os.getenv(variable)
        if value:
            candidates.append(Path(value) / "platform-tools" / "adb.exe")
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    return next((str(path) for path in candidates if path.exists()), None)


def _run_checked(
    command: list[str],
    *,
    cwd: Path,
    context: ToolExecutionContext,
    timeout: float = 3.0,
) -> subprocess.CompletedProcess[str] | None:
    context.checkpoint()
    available = max(0.2, min(timeout, context.remaining_seconds))
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=available,
            shell=False,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    context.checkpoint()
    return result


def _workspace_snapshot(
    project_root: Path,
    context: ToolExecutionContext,
    network: NetworkService | None,
) -> dict[str, object]:
    git = shutil.which("git")
    git_status = "git unavailable"
    git_clean: bool | None = None
    branch = None
    if git:
        result = _run_checked(
            [git, "-C", str(project_root), "status", "--short", "--branch"],
            cwd=project_root,
            context=context,
        )
        if result is not None and result.returncode == 0:
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            branch = lines[0].removeprefix("## ").strip() if lines else "unknown"
            git_clean = len(lines) <= 1
            git_status = "clean" if git_clean else f"{len(lines) - 1} changed path(s)"

    adb_path = _find_adb()
    adb_devices: int | None = None
    if adb_path:
        result = _run_checked(
            [adb_path, "devices"],
            cwd=project_root,
            context=context,
            timeout=2.0,
        )
        if result is not None and result.returncode == 0:
            adb_devices = sum(
                1
                for line in result.stdout.splitlines()[1:]
                if line.strip().endswith("\tdevice")
            )

    face_state = "unknown"
    if network is not None:
        face = network.registry.get("ron-face")
        if face is not None:
            face_state = face.connection_state.value

    return {
        "workspace": str(project_root),
        "git_status": git_status,
        "git_clean": git_clean,
        "branch": branch,
        "adb_devices": adb_devices,
        "face_state": face_state,
        "vscode_available": shutil.which("code") is not None,
    }


def _snapshot_message(data: dict[str, object]) -> str:
    branch = data.get("branch") or "unknown branch"
    adb = data.get("adb_devices")
    adb_text = "ADB unavailable" if adb is None else f"ADB sees {adb} device(s)"
    return (
        f"Git is {data['git_status']} on {branch}; {adb_text}; "
        f"Ron Face is {data['face_state']}."
    )


def build_workspace_status_tool(
    project_root: Path,
    network: NetworkService | None,
) -> ToolSpec:
    def get_status(
        arguments: dict[str, str | int],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del arguments
        data = _workspace_snapshot(project_root, context, network)
        return ToolResult(
            "get_workspace_status",
            ToolStatus.SUCCESS,
            "Ron workspace status: " + _snapshot_message(data),
            data=data,
        )

    return ToolSpec(
        name="get_workspace_status",
        description="Read Git, Nexus/ADB and editor readiness for Ron's development workspace.",
        arguments={},
        risk=ToolRisk.READ_ONLY,
        handler=get_status,
        timeout_seconds=6.0,
    )


def build_workspace_action_tool(
    project_root: Path,
    processes: ManagedProcessManager,
    network: NetworkService | None,
) -> ToolSpec:
    def action(
        arguments: dict[str, str | int],
        context: ToolExecutionContext,
    ) -> ToolResult:
        name = str(arguments["action"])
        if name == "tests":
            try:
                snapshot, started = processes.start(
                    key="ron-tests",
                    label="Ron test suite",
                    command=(sys.executable, "-m", "pytest", "-q"),
                    cwd=project_root,
                )
            except (OSError, ValueError):
                return ToolResult(
                    "workspace_action",
                    ToolStatus.FAILED,
                    "I couldn't start Ron's test suite safely.",
                )
            verb = "Started" if started else "Reused"
            return ToolResult(
                "workspace_action",
                ToolStatus.SUCCESS,
                f"{verb} tracked process {snapshot.process_id} for Ron's test suite.",
                data={
                    "workspace": str(project_root),
                    "process_id": snapshot.process_id,
                    "process_status": snapshot.status,
                    "changed": started,
                },
            )

        data = _workspace_snapshot(project_root, context, network)
        editor_opened = False
        code = shutil.which("code")
        try:
            if code:
                subprocess.Popen(
                    [code, str(project_root)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    close_fds=True,
                )
                editor_opened = True
            elif os.name == "nt":
                start_file = getattr(os, "startfile", None)
                if start_file is not None:
                    start_file(str(project_root))
                    editor_opened = True
        except OSError:
            editor_opened = False

        data["editor_opened"] = editor_opened
        if name == "open":
            message = (
                "Opening Ron's project in VS Code."
                if editor_opened and code
                else "Opening Ron's project folder."
                if editor_opened
                else "I checked the workspace, but couldn't open the editor."
            )
        else:
            editor = (
                "VS Code is opening"
                if editor_opened and code
                else "the project folder is opening"
                if editor_opened
                else "the editor could not be opened"
            )
            message = f"Workspace prepared: {editor}. {_snapshot_message(data)}"
        return ToolResult(
            "workspace_action",
            ToolStatus.SUCCESS if editor_opened else ToolStatus.FAILED,
            message,
            data={**data, "changed": editor_opened},
        )

    return ToolSpec(
        name="workspace_action",
        description="Prepare/open Ron's dev workspace or start its tracked test suite.",
        arguments={
            "action": ToolArgument(
                ToolArgumentKind.ENUM,
                choices=("prepare", "open", "tests"),
            )
        },
        risk=ToolRisk.EXTERNAL,
        handler=action,
        timeout_seconds=8.0,
    )
