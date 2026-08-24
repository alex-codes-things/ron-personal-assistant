"""Build small, fully bounded plans from deterministic rules first."""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from time import monotonic

from ron.agent.models import AgentPlan, AgentPlanSource, AgentTaskPlan
from ron.agent.registry import ToolRegistry
from ron.ai import AIClient, AIError

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
    r"unmute|play|pause|resume|unpause|continue|next|skip|previous|what|tell|get|check|create|"
    r"search|find|remind)\b)",
    re.IGNORECASE,
)
NAMED_TRACK_PATTERN = re.compile(r"^(?:please\s+)?play\s+(.+?)\s*[.!?]*$", re.IGNORECASE)
MAX_PLAN_STEPS = 4
MAX_CONTEXT_ACTIONS = 12
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
Interpret the user's ordinary meaning instead of requiring an exact command phrase. For
example, unpause, carry on or continue the current song means resume playback. Resolve only
to the approved tools and their exact arguments.
Use the recent verified action context only for natural references such as it, that,
the same one, or do that again. Prefer tools marked available and never select a tool
whose availability is false.
Return one JSON object only: {"tool":"tool_name","arguments":{...}}.
If no listed tool can fully and safely perform the request, return
{"tool":null,"arguments":{}}. Never invent a tool or argument. Approved tools:"""


@dataclass(frozen=True, slots=True)
class _RecentAction:
    plans: tuple[AgentPlan, ...]
    prompt: str
    recorded_at: float


class AgentPlanner:
    """Produce plans only; this class has no execution authority."""

    def __init__(
        self,
        client: AIClient,
        registry: ToolRegistry,
        *,
        context_ttl_seconds: float = 15 * 60,
    ) -> None:
        if not 60 <= context_ttl_seconds <= 24 * 60 * 60:
            raise ValueError("Agent context lifetime must be between 1 minute and 24 hours")
        self.client = client
        self.registry = registry
        self.context_ttl_seconds = context_ttl_seconds
        self._prepared: OrderedDict[str, AgentTaskPlan] = OrderedDict()
        self._prepared_lock = threading.RLock()
        self._prepared_limit = 16
        self._context_version = 0
        self._last_successful_plans: tuple[AgentPlan, ...] = ()
        self._recent_successes: deque[_RecentAction] = deque(maxlen=MAX_CONTEXT_ACTIONS)

    def record_success(self, plans: tuple[AgentPlan, ...], *, prompt: str = "") -> None:
        """Remember a tiny, safe action context for conversational references."""
        if not plans:
            return
        with self._prepared_lock:
            self._last_successful_plans = plans[-MAX_PLAN_STEPS:]
            self._purge_expired_context_locked()
            self._recent_successes.append(
                _RecentAction(
                    plans[-MAX_PLAN_STEPS:],
                    " ".join(prompt.strip().split())[:240],
                    monotonic(),
                )
            )
            self._context_version += 1
            self._prepared.clear()

    def context_summary(self) -> dict[str, object]:
        with self._prepared_lock:
            self._purge_expired_context_locked()
            recent = tuple(self._recent_successes)
            version = self._context_version
        return {
            "version": version,
            "recent_actions": [
                {
                    "tool": plan.tool_name,
                    "arguments": dict(plan.arguments),
                    "age_seconds": round(max(0.0, monotonic() - item.recorded_at), 1),
                }
                for item in recent
                for plan in item.plans
                if plan.tool_name is not None
            ],
        }

    def _purge_expired_context_locked(self) -> None:
        cutoff = monotonic() - self.context_ttl_seconds
        while self._recent_successes and self._recent_successes[0].recorded_at < cutoff:
            self._recent_successes.popleft()

    def _cache_key(self, prompt: str) -> str:
        with self._prepared_lock:
            version = self._context_version
        return f"{version}:{prompt.casefold()}"

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
        key = self._cache_key(clean_prompt)
        with self._prepared_lock:
            prepared = self._prepared.pop(key, None)
        if prepared is not None:
            return prepared
        return self._build_plan_steps(clean_prompt)

    def can_handle(self, prompt: str) -> bool:
        """Resolve one action-shaped prompt once and reuse the plan after routing."""
        clean_prompt = prompt.strip().replace("’", "'").replace("`", "'")
        if not clean_prompt:
            return False
        key = self._cache_key(clean_prompt)
        with self._prepared_lock:
            prepared = self._prepared.get(key)
        if prepared is None:
            prepared = self._build_plan_steps(clean_prompt)
            with self._prepared_lock:
                self._prepared[key] = prepared
                self._prepared.move_to_end(key)
                while len(self._prepared) > self._prepared_limit:
                    self._prepared.popitem(last=False)
        return bool(prepared.steps)

    def _build_plan_steps(self, clean_prompt: str) -> AgentTaskPlan:
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
        contextual = self._contextual_plan(prompt)
        if contextual is not None:
            return contextual
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
            return self._plan("control_volume", {"action": "unmute"}, "Matched an unmute request.")
        if re.search(r"\b(?:mute|silence)\b", prompt, re.IGNORECASE):
            return self._plan("control_volume", {"action": "mute"}, "Matched a mute request.")
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
            return self._plan("get_system_performance", {}, "Matched a system-performance request.")

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
            query = search.group(1).strip(' .?!"')
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
            return self._plan("control_media", {"action": "stop"}, "Matched a stop-media request.")
        resume_playback = re.search(
            r"^(?:please\s+)?(?:unpause|resume|continue)(?:\s+(?:this|it|the\s+)?"
            r"(?:song|track|music|media|playback|spotify)?)?[.!?]*$|"
            r"\b(?:unpause|resume|continue|carry\s+on|keep\s+playing|pick\s+back\s+up)\b"
            r".*\b(?:song|track|music|media|playback|spotify)\b",
            prompt,
            re.IGNORECASE,
        )
        if resume_playback is not None:
            if "spotify" in prompt.casefold():
                return self._plan(
                    "spotify_control_playback",
                    {"action": "resume"},
                    "Understood an ordinary resume-playback request.",
                )
            return self._plan(
                "control_media",
                {"action": "resume"},
                "Understood an ordinary resume-playback request.",
            )

        playback = re.search(
            r"^(?:play|pause)(?:\s+(?:this|it|spotify))?$|"
            r"\b(?:play|pause)\b.*\b(?:media|music|playback|spotify)\b",
            prompt,
            re.IGNORECASE,
        )
        if playback is not None:
            verb = re.search(r"\b(play|pause)\b", prompt, re.IGNORECASE)
            action = verb.group(1).casefold() if verb is not None else "pause"
            if "spotify" in prompt.casefold():
                return self._plan(
                    "spotify_control_playback",
                    {"action": "resume" if action == "play" else "pause"},
                    "Matched a Spotify control request.",
                )
            return self._plan(
                "control_media", {"action": action}, "Matched an explicit play/pause request."
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

    def _contextual_plan(self, prompt: str) -> AgentPlan | None:
        """Resolve pronouns against the newest relevant verified session action."""
        clean = prompt.casefold().strip(" .!?")
        with self._prepared_lock:
            self._purge_expired_context_locked()
            recent_plans = tuple(
                plan
                for item in reversed(self._recent_successes)
                for plan in reversed(item.plans)
                if plan.tool_name is not None
            )
        if not recent_plans:
            return None

        if clean in {"open it again", "open that again", "open the same one again"}:
            previous = next(
                (
                    plan
                    for plan in recent_plans
                    if plan.tool_name in {"open_application", "open_folder"}
                ),
                None,
            )
            if previous is not None:
                return self._plan(
                    previous.tool_name,
                    dict(previous.arguments),
                    "Resolved the reference from the last successful open action.",
                )

        media_tools = {"control_media", "spotify_control_playback", "spotify_play_track"}
        previous = next(
            (plan for plan in recent_plans if plan.tool_name in media_tools),
            None,
        )
        if previous is None:
            return None
        provider = (
            "spotify_control_playback"
            if previous.tool_name.startswith("spotify_")
            else "control_media"
        )
        if re.fullmatch(r"(?:pause|stop) (?:it|that|the song|the music)", clean):
            action = "pause" if provider == "spotify_control_playback" else "pause"
            return self._plan(
                provider,
                {"action": action},
                "Resolved the media reference from the last successful action.",
            )
        if re.fullmatch(r"(?:play|resume|unpause|continue) (?:it|that|the song|the music)", clean):
            return self._plan(
                provider,
                {"action": "resume"},
                "Resolved the media reference from the last successful action.",
            )
        if clean in {"next one", "the next one", "skip that", "play the next one"}:
            return self._plan(
                provider,
                {"action": "next"},
                "Resolved the track reference from the last successful media action.",
            )
        if clean in {"previous one", "the previous one", "go back to the last one"}:
            return self._plan(
                provider,
                {"action": "previous"},
                "Resolved the track reference from the last successful media action.",
            )
        return None

    @staticmethod
    def _plan(tool: str, arguments: dict[str, object], reason: str) -> AgentPlan:
        return AgentPlan(tool, arguments, reason, AgentPlanSource.DETERMINISTIC)

    def _model_plan(self, prompt: str) -> AgentPlan:
        schemas = json.dumps(self.registry.planner_schemas(), separators=(",", ":"))
        context = json.dumps(self.context_summary(), separators=(",", ":"))
        try:
            result = self.client.stream_chat(
                [
                    {
                        "role": "system",
                        "content": (
                            f"{PLANNER_INSTRUCTION}\n{schemas}\n"
                            f"Recent verified action context: {context}"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                think=False,
                max_output_tokens=160,
                temperature=0.0,
            )
        except AIError:
            return self._no_plan("The configured AI planner was unavailable.")

        payload = self._first_json_object(result.text)
        if payload is None:
            return self._no_plan("The AI planner returned invalid structured data.")
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
            "The AI planner selected an approved tool.",
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
                re.search(rf"\b{re.escape(choice)}\b", prompt, re.IGNORECASE) for choice in choices
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
