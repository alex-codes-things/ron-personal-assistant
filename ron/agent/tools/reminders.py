"""Approved persistent local timer/reminder tool."""

from __future__ import annotations

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolExecutionContext,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.registry import ToolSpec
from ron.reminders import ReminderManager


def build_reminder_tool(manager: ReminderManager) -> ToolSpec:
    def set_reminder(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        context.checkpoint()
        seconds = int(arguments["seconds"])
        message = str(arguments.get("message", "Timer finished"))
        try:
            reminder = manager.create(seconds, message)
        except ValueError as error:
            return ToolResult("set_reminder", ToolStatus.FAILED, str(error))
        return ToolResult(
            "set_reminder",
            ToolStatus.SUCCESS,
            f"Reminder {reminder.reminder_id} is set for {reminder.due_local}: {message}.",
            data={
                "reminder_id": reminder.reminder_id,
                "message": reminder.message,
                "due_at": reminder.due_at,
            },
        )

    def cancel_created_reminder(
        result: ToolResult, context: ToolExecutionContext
    ) -> ToolResult:
        context.checkpoint()
        reminder_id = result.data.get("reminder_id")
        if not isinstance(reminder_id, int):
            return ToolResult(
                "set_reminder", ToolStatus.FAILED, "The reminder rollback data was missing."
            )
        reminder = manager.cancel(reminder_id)
        if reminder is None or reminder.status != "cancelled":
            return ToolResult(
                "set_reminder", ToolStatus.FAILED, "The reminder could not be cancelled."
            )
        return ToolResult(
            "set_reminder",
            ToolStatus.SUCCESS,
            f"reminder {reminder_id} cancelled",
        )

    return ToolSpec(
        "set_reminder",
        "Create a persistent local timer or relative reminder.",
        {
            "seconds": ToolArgument(
                ToolArgumentKind.INTEGER,
                minimum=1,
                maximum=31_536_000,
            ),
            "message": ToolArgument(
                ToolArgumentKind.TEXT,
                maximum_length=240,
                required=False,
            ),
        },
        ToolRisk.REVERSIBLE,
        set_reminder,
        timeout_seconds=2.0,
        compensator=cancel_created_reminder,
    )
