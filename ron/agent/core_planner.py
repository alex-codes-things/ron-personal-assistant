"""v0.9 planner extensions for skills, workspace macros and multi-tool model plans."""

from __future__ import annotations

import json
import re

from ron.agent.models import AgentPlan, AgentPlanSource, AgentTaskPlan
from ron.agent.planner import ACTION_SPLIT_PATTERN, AgentPlanner
from ron.agent.registry import ToolRegistry
from ron.ai import OllamaClient, OllamaError
from ron.skills import SkillCatalog

MAX_CORE_PLAN_STEPS = 4

CORE_PLANNER_INSTRUCTION = """Plan the user's request using only approved tools.
Return one JSON object only:
{"steps":[{"tool":"tool_name","arguments":{...}}]}
Use between zero and four steps. Return {"steps":[]} when approved tools cannot fully
and safely complete the request. Never invent a tool, argument, file path, command,
device, or application. Prefer the fewest steps needed."""


class AgentCorePlanner(AgentPlanner):
    """Keep the stable deterministic planner and add v0.9 orchestration."""

    def __init__(
        self,
        client: OllamaClient,
        registry: ToolRegistry,
        skills: SkillCatalog,
    ) -> None:
        super().__init__(client, registry)
        self.skills = skills

    def plan_steps(self, prompt: str) -> AgentTaskPlan:
        clean = prompt.strip().replace("’", "'").replace("`", "'")
        segments = tuple(
            segment.strip(" ,")
            for segment in ACTION_SPLIT_PATTERN.split(clean)
            if segment.strip(" ,")
        )
        if len(segments) > MAX_CORE_PLAN_STEPS:
            return AgentTaskPlan(
                (),
                f"A task can contain at most {MAX_CORE_PLAN_STEPS} executable steps.",
            )
        if re.search(
            r"\b(?:why|how come)\b.*\b(?:fans?|computer|laptop)\b.*"
            r"\b(?:loud|hot|slow|busy|fast)\b",
            clean,
            re.IGNORECASE,
        ):
            tools = set(self.registry.names())
            if {"get_system_performance", "get_top_processes"} <= tools:
                return AgentTaskPlan(
                    (
                        self._plan(
                            "get_system_performance",
                            {},
                            "Check live CPU, memory and disk load.",
                        ),
                        self._plan(
                            "get_top_processes",
                            {},
                            "Inspect heavyweight running processes.",
                        ),
                    ),
                    "Combined system-health and process diagnostics.",
                )
        if len(segments) > 1:
            steps: list[AgentPlan] = []
            for segment in segments:
                step = self._core_plan(segment) or self._deterministic_plan(segment)
                if step is None or step.tool_name is None:
                    return AgentTaskPlan(
                        (),
                        "I could not safely map every requested step, so no step was run.",
                    )
                steps.append(step)
            return AgentTaskPlan(
                tuple(steps),
                f"Mapped all {len(steps)} requested steps through Ron's skill system.",
            )

        core = self._core_plan(clean)
        if core is not None:
            return AgentTaskPlan(
                (core,) if core.tool_name is not None else (),
                core.reason,
            )

        deterministic = self._deterministic_plan(clean)
        if deterministic is not None:
            return AgentTaskPlan(
                (deterministic,) if deterministic.tool_name is not None else (),
                deterministic.reason,
            )
        return self._model_task_plan(clean)

    def _core_plan(self, prompt: str) -> AgentPlan | None:
        text = prompt.casefold()

        if re.search(
            r"\b(?:prepare|set up|get ready|ready)\b.*\b(?:workspace|project|ron)\b"
            r"|\b(?:work on|code on|develop)\s+ron\b"
            r"|\bget everything ready\b",
            text,
        ):
            return self._plan(
                "workspace_action",
                {"action": "prepare"},
                "Matched Ron's development-workspace preparation skill.",
            )

        if re.search(
            r"\b(?:open|launch)\b.*\b(?:ron project|ron repo|workspace|project)\b",
            text,
        ):
            return self._plan(
                "workspace_action",
                {"action": "open"},
                "Matched Ron's workspace-opening skill.",
            )

        if re.search(
            r"\b(?:run|start)\b.*\b(?:tests|test suite|pytest)\b"
            r"|^\s*run the tests\s*[.!]?$",
            text,
        ):
            return self._plan(
                "workspace_action",
                {"action": "tests"},
                "Matched Ron's tracked test-run skill.",
            )

        if re.search(
            r"\b(?:workspace|repo|repository|git)\b.*\b(?:status|ready|clean|dirty)\b"
            r"|\bcheck\b.*\b(?:workspace|repo|repository)\b",
            text,
        ):
            return self._plan(
                "get_workspace_status",
                {},
                "Matched Ron's workspace-status skill.",
            )

        if re.search(
            r"\b(?:nexus|tablet|ron face|ron network|devices?)\b.*"
            r"\b(?:online|offline|connected|status|health|available)\b"
            r"|\b(?:what|which|show|list)\b.*\bdevices?\b.*\bonline\b",
            text,
        ):
            return self._plan(
                "get_network_devices",
                {},
                "Matched Ron Network device awareness.",
            )

        if re.search(
            r"\b(?:what|which|show|list|check|how(?:'s| is| are))\b.*"
            r"\b(?:process|processes|server|servers|test run|tests|script|scripts)\b",
            text,
        ):
            return self._plan(
                "get_managed_processes",
                {},
                "Matched status for processes that Ron started.",
            )

        if re.search(
            r"\b(?:stop|cancel|end|kill)\b.*"
            r"\b(?:that|the|last|current)?\s*(?:process|server|test run|tests|script)\b",
            text,
        ):
            return self._plan(
                "stop_managed_process",
                {},
                "Matched a request to stop Ron's latest managed process.",
            )

        if re.search(
            r"\bcan i\b.*\b(?:run|play)\b.*\b(?:game|games)\b",
            text,
        ):
            return self._plan(
                "get_system_performance",
                {},
                "Matched a system-awareness question that needs live performance state.",
            )
        return None

    def _model_task_plan(self, prompt: str) -> AgentTaskPlan:
        schemas = json.dumps(self.registry.schemas(), separators=(",", ":"))
        system = (
            f"{CORE_PLANNER_INSTRUCTION}\n{self.skills.model_context()}\n"
            f"Approved tools: {schemas}"
        )
        try:
            result = self.client.stream_chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                think=False,
                max_output_tokens=320,
                temperature=0.0,
            )
        except OllamaError:
            return AgentTaskPlan((), "The local planner was unavailable.")

        payload = self._first_json_object(result.text)
        if payload is None:
            return AgentTaskPlan((), "The local planner returned invalid structured data.")
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return AgentTaskPlan((), "The local planner returned an invalid step list.")
        if len(raw_steps) > MAX_CORE_PLAN_STEPS:
            return AgentTaskPlan(
                (),
                f"The local planner exceeded the {MAX_CORE_PLAN_STEPS}-step safety limit.",
            )
        steps: list[AgentPlan] = []
        allowed = set(self.registry.names())
        for raw in raw_steps:
            if not isinstance(raw, dict):
                return AgentTaskPlan((), "The local planner returned an invalid step.")
            tool = raw.get("tool")
            arguments = raw.get("arguments")
            if not isinstance(tool, str) or tool not in allowed:
                return AgentTaskPlan((), "The planner requested a tool outside the allowlist.")
            if not isinstance(arguments, dict):
                return AgentTaskPlan((), "The planner returned invalid tool arguments.")
            steps.append(
                AgentPlan(
                    tool,
                    dict(arguments),
                    "The local planner selected this step from an approved skill.",
                    AgentPlanSource.LOCAL_MODEL,
                )
            )
        if not steps:
            return AgentTaskPlan((), "No approved skill can safely complete the request.")
        return AgentTaskPlan(
            tuple(steps),
            f"The local planner selected {len(steps)} approved skill step(s).",
        )
