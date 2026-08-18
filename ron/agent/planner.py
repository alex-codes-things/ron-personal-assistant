"""Build small, fully bounded plans from deterministic rules first."""

from __future__ import annotations

import json
import re

from ron.agent.models import AgentPlan, AgentPlanSource, AgentTaskPlan
from ron.agent.registry import ToolRegistry
from ron.ai import OllamaClient, OllamaError

TIME_PATTERN = re.compile(
    r"\b(?:what(?:'?s| is) (?:the )?(?:current )?time|what time is it|"
    r"tell me (?:the )?(?:current )?time|current time|time right now)\b",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"\b(?:what(?:'?s| is) (?:the )?(?:current )?date|"
    r"what(?:'?s| is) today'?s date|what day is it|current date|today'?s date)\b",
    re.IGNORECASE,
)
VOLUME_SET_PATTERN = re.compile(
    r"\b(?:set|change|turn|adjust|put)\b.*\bvolume\b"
    r"(?:\s+(?:to|at|around))?\s+([a-z-]+|\d{1,3})\s*%?",
    re.IGNORECASE,
)
VOLUME_GET_PATTERN = re.compile(
    r"\b(?:what(?:'?s| is)|check|show|get)\b.*\bvolume\b", re.IGNORECASE
)
APP_ALIASES = {
    "notepad": ("notepad", "text editor"),
    "calculator": ("calculator", "calc"),
    "file_explorer": ("file explorer", "explorer"),
    "settings": ("windows settings", "settings"),
    "spotify": ("spotify",),
    "browser": ("browser", "internet"),
}
ACTION_SPLIT_PATTERN = re.compile(
    r"(?:\s*,\s*(?:then\s+)?|\s*;\s*|\s+(?:and\s+then|then|and)\s+)"
    r"(?=(?:please\s+)?(?:open|launch|start|set|put|change|turn|adjust|mute|"
    r"unmute|play|pause|resume|next|skip|previous|what|tell|get|check|create|"
    r"search|find|remind)\b)",
    re.IGNORECASE,
)
NAMED_TRACK_PATTERN = re.compile(r"^(?:please\s+)?play\s+(.+?)\s*[.!?]*$", re.IGNORECASE)
MAX_PLAN_STEPS = 4
NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}

PLANNER_INSTRUCTION = """Choose at most one approved tool for the user's request.
Return one JSON object only: {"tool":"tool_name","arguments":{...}}.
If no listed tool can fully and safely perform the request, return
{"tool":null,"arguments":{}}. Never invent a tool or argument. Approved tools:"""


class AgentPlanner:
    """Produce plans only; this class has no execution authority."""

    def __init__(self, client: OllamaClient, registry: ToolRegistry) -> None:
        self.client = client
        self.registry = registry

    def plan(self, prompt: str) -> AgentPlan:
        """Compatibility entry point for a single step only."""
        task_plan = self.plan_steps(prompt)
        if len(task_plan.steps) == 1:
            return task_plan.steps[0]
        if len(task_plan.steps) > 1:
            return self._no_plan(
                "The single-step interface will not partially execute this multi-step request; "
                "use the bounded task runner."
            )
        return self._no_plan(task_plan.reason)

    def plan_steps(self, prompt: str) -> AgentTaskPlan:
        clean_prompt = prompt.strip().replace("’", "'").replace("`", "'")
        segments = tuple(
            segment.strip(" ,")
            for segment in ACTION_SPLIT_PATTERN.split(clean_prompt)
            if segment.strip(" ,")
        )
        if len(segments) > MAX_PLAN_STEPS:
            return AgentTaskPlan((), f"A task can contain at most {MAX_PLAN_STEPS} steps.")

        if len(segments) > 1:
            steps: list[AgentPlan] = []
            for segment in segments:
                step = self._deterministic_plan(segment)
                if step is None or step.tool_name is None:
                    return AgentTaskPlan(
                        (),
                        "I could not safely map every requested step, so no step was run.",
                    )
                steps.append(step)
            return AgentTaskPlan(
                tuple(steps),
                f"Mapped all {len(steps)} requested steps using deterministic rules.",
            )

        deterministic = self._deterministic_plan(clean_prompt)
        if deterministic is not None:
            return AgentTaskPlan(
                (deterministic,) if deterministic.tool_name is not None else (),
                deterministic.reason,
            )
        model_plan = self._model_plan(clean_prompt)
        return AgentTaskPlan(
            (model_plan,) if model_plan.tool_name is not None else (), model_plan.reason
        )

    def _deterministic_plan(self, prompt: str) -> AgentPlan | None:
        if TIME_PATTERN.search(prompt) and DATE_PATTERN.search(prompt):
            return self._no_plan("Time and date must be requested as explicit steps.")
        if TIME_PATTERN.search(prompt):
            return self._plan("get_local_time", {}, "Matched a local-clock request.")
        if DATE_PATTERN.search(prompt):
            return self._plan("get_local_date", {}, "Matched a local-date request.")

        volume_set = VOLUME_SET_PATTERN.search(prompt)
        if volume_set is not None:
            level = self._number(volume_set.group(1))
            if level is None:
                return self._no_plan("I could not understand the requested volume percentage.")
            return self._plan(
                "control_volume",
                {"action": "set", "level": level},
                "Matched an exact volume percentage.",
            )
        volume_down = re.search(
            r"\b(?:turn|put|bring|lower)\b.*\b(?:volume|it)\b.*"
            r"\b(?:down|quieter)\b|\bturn it down\b",
            prompt,
            re.IGNORECASE,
        )
        if volume_down:
            return self._plan(
                "control_volume",
                {"action": "adjust", "delta": -5},
                "Matched a small volume decrease.",
            )
        volume_up = re.search(
            r"\b(?:turn|put|bring|raise)\b.*\b(?:volume|it)\b.*"
            r"\b(?:up|louder)\b|\bturn it up\b",
            prompt,
            re.IGNORECASE,
        )
        if volume_up:
            return self._plan(
                "control_volume",
                {"action": "adjust", "delta": 5},
                "Matched a small volume increase.",
            )
        if re.search(r"\bunmute\b", prompt, re.IGNORECASE):
            return self._plan(
                "control_volume", {"action": "unmute"}, "Matched an unmute request."
            )
        if re.search(r"\b(?:mute|silence)\b", prompt, re.IGNORECASE):
            return self._plan(
                "control_volume", {"action": "mute"}, "Matched a mute request."
            )
        if VOLUME_GET_PATTERN.search(prompt):
            return self._plan(
                "control_volume", {"action": "get"}, "Matched a volume-status request."
            )

        brightness_set = re.search(
            r"\b(?:set|change|put|turn)\b.*\bbrightness\b"
            r"(?:\s+(?:to|at|around))?\s+([a-z-]+|\d{1,3})\s*%?",
            prompt,
            re.IGNORECASE,
        )
        if brightness_set is not None:
            level = self._number(brightness_set.group(1))
            if level is not None:
                return self._plan(
                    "control_brightness",
                    {"action": "set", "level": level},
                    "Matched a brightness percentage.",
                )
        brightness_down = re.search(
            r"\b(?:turn|put|bring|lower|decrease|dim)\b.*\bbrightness\b.*"
            r"\b(?:down|lower|dimmer)?\b|\bdim (?:the )?screen\b",
            prompt,
            re.IGNORECASE,
        )
        if brightness_down:
            return self._plan(
                "control_brightness",
                {"action": "adjust", "delta": -5},
                "Matched a small brightness decrease.",
            )
        brightness_up = re.search(
            r"\b(?:turn|put|bring|raise|increase|brighten)\b.*\bbrightness\b.*"
            r"\b(?:up|higher|brighter)?\b|\bbrighten (?:the )?screen\b",
            prompt,
            re.IGNORECASE,
        )
        if brightness_up:
            return self._plan(
                "control_brightness",
                {"action": "adjust", "delta": 5},
                "Matched a small brightness increase.",
            )
        if re.search(r"\b(?:what|check|show|get)\b.*\bbrightness\b", prompt, re.IGNORECASE):
            return self._plan(
                "control_brightness", {"action": "get"}, "Matched a brightness-status request."
            )

        if re.search(r"\b(?:battery|charging|charge level)\b", prompt, re.IGNORECASE):
            return self._plan("get_battery_status", {}, "Matched a battery-status request.")
        if re.search(
            r"\b(?:performance|cpu|processor|memory|ram|disk|storage)\b.*"
            r"\b(?:status|usage|used|available|free|doing|summary)\b",
            prompt,
            re.IGNORECASE,
        ):
            return self._plan(
                "get_system_performance", {}, "Matched a system-performance request."
            )

        if re.search(
            r"\b(?:create|open|make)\b.*\bblank\b.*\b(?:text|notepad)\b",
            prompt,
            re.IGNORECASE,
        ):
            return self._plan(
                "create_blank_text_document", {}, "Matched a blank-text-document request."
            )

        folder = self._folder_name(prompt)
        if folder is not None and re.search(r"\b(?:open|show)\b", prompt, re.IGNORECASE):
            return self._plan("open_folder", {"folder": folder}, "Matched an approved folder.")
        search = re.search(
            r"\b(?:search|find)\b(?:\s+(?:my|the))?\s+"
            r"(?:documents|downloads|desktop)(?:\s+(?:for|containing|named))\s+(.+)",
            prompt,
            re.IGNORECASE,
        )
        if search is not None:
            folder = self._folder_name(prompt)
            query = search.group(1).strip(" .?!\"")
            if folder is not None and query:
                return self._plan(
                    "search_approved_folder",
                    {"folder": folder, "query": query},
                    "Matched an approved-folder name search.",
                )

        reminder = self._reminder_plan(prompt)
        if reminder is not None:
            return reminder

        if re.search(r"\b(?:open|launch|start)\b", prompt, re.IGNORECASE):
            for application, aliases in APP_ALIASES.items():
                if any(
                    re.search(rf"\b{re.escape(alias)}\b", prompt, re.IGNORECASE)
                    for alias in aliases
                ):
                    return self._plan(
                        "open_application",
                        {"application": application},
                        "Matched an allowlisted application.",
                    )

        if re.search(
            r"^(?:next|skip)$|\b(?:next|skip)\b.*\b(?:track|song|music)\b",
            prompt,
            re.IGNORECASE,
        ):
            tool = "spotify_control_playback" if "spotify" in prompt.casefold() else "control_media"
            return self._plan(tool, {"action": "next"}, "Matched a next-track request.")
        if re.search(r"\b(?:previous|last|back)\b.*\b(?:track|song)\b", prompt, re.IGNORECASE):
            tool = "spotify_control_playback" if "spotify" in prompt.casefold() else "control_media"
            return self._plan(tool, {"action": "previous"}, "Matched a previous-track request.")
        if re.search(r"\bstop\b.*\b(?:track|song|music|media|playback)\b", prompt, re.IGNORECASE):
            return self._plan(
                "control_media", {"action": "stop"}, "Matched a stop-media request."
            )
        playback = re.search(
            r"^(?:play|pause|resume)(?:\s+(?:this|it|spotify))?$|"
            r"\b(?:play|pause|resume)\b.*\b(?:media|music|playback|spotify)\b",
            prompt,
            re.IGNORECASE,
        )
        if playback is not None:
            verb = re.search(r"\b(play|pause|resume)\b", prompt, re.IGNORECASE)
            action = verb.group(1).casefold() if verb is not None else "pause"
            if "spotify" in prompt.casefold() and action in {"pause", "resume"}:
                return self._plan(
                    "spotify_control_playback",
                    {"action": action},
                    "Matched a Spotify control request.",
                )
            return self._plan(
                "control_media", {"action": "play_pause"}, "Matched a play/pause request."
            )

        named_track = NAMED_TRACK_PATTERN.match(prompt)
        if named_track is not None:
            query = named_track.group(1).strip()
            if query.casefold() not in {"music", "media", "playback", "spotify"}:
                return self._plan(
                    "spotify_play_track",
                    {"query": query},
                    "Matched a named-track playback request.",
                )
        return None

    @staticmethod
    def _plan(tool: str, arguments: dict[str, object], reason: str) -> AgentPlan:
        return AgentPlan(tool, arguments, reason, AgentPlanSource.DETERMINISTIC)

    def _model_plan(self, prompt: str) -> AgentPlan:
        schemas = json.dumps(self.registry.schemas(), separators=(",", ":"))
        try:
            result = self.client.stream_chat(
                [
                    {"role": "system", "content": f"{PLANNER_INSTRUCTION}\n{schemas}"},
                    {"role": "user", "content": prompt},
                ],
                think=False,
                max_output_tokens=160,
                temperature=0.0,
            )
        except OllamaError:
            return self._no_plan("The local planner was unavailable.")

        payload = self._first_json_object(result.text)
        if payload is None:
            return self._no_plan("The local planner returned invalid structured data.")
        tool_name = payload.get("tool")
        arguments = payload.get("arguments")
        if tool_name is None:
            return self._no_plan("No approved tool can safely complete the request.")
        if not isinstance(tool_name, str) or tool_name not in self.registry.names():
            return self._no_plan("The planner requested a tool outside the allowlist.")
        if not isinstance(arguments, dict):
            return self._no_plan("The planner returned invalid tool arguments.")
        return AgentPlan(
            tool_name,
            arguments,
            "The local planner selected an approved tool.",
            AgentPlanSource.LOCAL_MODEL,
        )

    @staticmethod
    def _first_json_object(text: str) -> dict[str, object] | None:
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                payload, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            return payload if isinstance(payload, dict) else None
        return None

    @staticmethod
    def _no_plan(reason: str) -> AgentPlan:
        return AgentPlan(None, {}, reason, AgentPlanSource.NONE)

    @staticmethod
    def _number(value: str) -> int | None:
        clean = value.casefold().strip()
        if clean.isdecimal():
            number = int(clean)
            return number if 0 <= number <= 100 else None
        parts = clean.replace("-", " ").split()
        if len(parts) == 1:
            return NUMBER_WORDS.get(parts[0])
        values = [NUMBER_WORDS.get(part) for part in parts]
        if any(value is None for value in values):
            return None
        number = sum(int(value) for value in values if value is not None)
        return number if 0 <= number <= 100 else None

    @staticmethod
    def _folder_name(prompt: str) -> str | None:
        aliases = {
            "documents": ("documents", "document folder"),
            "downloads": ("downloads", "download folder"),
            "desktop": ("desktop",),
        }
        for name, choices in aliases.items():
            if any(
                re.search(rf"\b{re.escape(choice)}\b", prompt, re.IGNORECASE)
                for choice in choices
            ):
                return name
        return None

    def _reminder_plan(self, prompt: str) -> AgentPlan | None:
        match = re.search(
            r"\b(?:set\s+(?:a\s+)?timer\s+for|remind\s+me\s+in)\s+"
            r"([a-z-]+|\d+)\s+(seconds?|minutes?|hours?|days?)\b"
            r"(?:\s+(?:to|for|that)\s+(.+))?",
            prompt,
            re.IGNORECASE,
        )
        if match is None:
            return None
        amount = self._number(match.group(1))
        if amount is None or amount <= 0:
            return self._no_plan("I could not understand the reminder duration.")
        unit = match.group(2).casefold()
        multiplier = 1 if unit.startswith("second") else 60
        if unit.startswith("hour"):
            multiplier = 3600
        elif unit.startswith("day"):
            multiplier = 86400
        message = (match.group(3) or "Timer finished").strip(" .?!")
        return self._plan(
            "set_reminder",
            {"seconds": amount * multiplier, "message": message},
            "Matched a local relative reminder.",
        )
