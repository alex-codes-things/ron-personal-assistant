"""Fast deterministic-first routing between Ron's conversation and agent sides."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ron.ai import OllamaClient, OllamaError

class RouteDestination(StrEnum):
    CHAT = "chat"
    AGENT = "agent"


class RouteSource(StrEnum):
    DETERMINISTIC = "deterministic"
    LOCAL_MODEL = "local_model"
    SAFE_FALLBACK = "safe_fallback"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Explainable routing metadata with no authority to execute a tool."""

    destination: RouteDestination
    confidence: float
    reason: str
    source: RouteSource
    requires_confirmation: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Routing confidence must be between 0 and 1")
        if not self.reason or len(self.reason) > 240:
            raise ValueError("Routing reason is invalid")


@dataclass(frozen=True, slots=True)
class RoutingRule:
    pattern: re.Pattern[str]
    reason: str
    requires_confirmation: bool = False


def _rules(items: tuple[tuple[str, str, bool], ...]) -> tuple[RoutingRule, ...]:
    return tuple(
        RoutingRule(re.compile(pattern, re.IGNORECASE), reason, confirmation)
        for pattern, reason, confirmation in items
    )


EXPLANATION_RULES = _rules(
    (
        (
            r"^(?:how (?:do|can|could|should) i|teach me|explain|what happens if)\b",
            "The user is asking for knowledge or guidance, not execution.",
            False,
        ),
        (
            r"^(?:write|draft|rewrite|summari[sz]e|brainstorm|plan|recommend)\b",
            "The request can be fulfilled by generating conversational content.",
            False,
        ),
    )
)

EXTERNAL_STATE_RULES = _rules(
    (
        (
            r"\b(?:status(?: of)?|how is|how's|check|list|show|cancel|stop)\b"
            r".*\b(?:task|tasks|job|jobs)\b",
            "The prompt reads or changes Ron's live agent-task state.",
            False,
        ),
        (
            r"\b(?:show|list|cancel|delete|remove|what are)\b.*\breminders?\b",
            "The prompt reads or changes Ron's live reminder state.",
            False,
        ),
        (
            r"\b(?:what(?:'?s| is) (?:the )?(?:current )?time|"
            r"what time is it(?: right now)?|tell me (?:the )?(?:current )?time|"
            r"current time|time right now)\b",
            "Current time requires trusted computer state.",
            False,
        ),
        (
            r"\b(?:what(?:'?s| is) (?:the )?(?:current )?date|"
            r"what(?:'?s| is) today'?s date|what day is it|"
            r"tell me (?:today'?s|the current) date|current date|"
            r"today'?s date|date today)\b",
            "The current date requires trusted computer state.",
            False,
        ),
        (
            r"\b(?:weather|forecast|traffic|latest news|current news)\b",
            "Live information requires an external data tool.",
            False,
        ),
        (
            r"\b(?:battery|cpu|memory|disk|storage|network|wi-?fi)\b.*\b"
            r"(?:status|usage|left|available|connected|speed|level|percent|percentage|charge)\b",
            "The answer depends on live computer state.",
            False,
        ),
        (
            r"\b(?:what|which|show|list)\b.*\b(?:files|folders|downloads|documents)\b",
            "The answer requires reading the live file system.",
            False,
        ),
        (
            r"\b(?:search|find)\b.*\b(?:desktop|downloads|documents)\b",
            "The request searches an approved live folder.",
            False,
        ),
        (
            r"\b(?:what|current|check)\b.*\b(?:volume|brightness)\b",
            "The answer depends on a live computer setting.",
            False,
        ),
        (
            r"\b(?:battery|charging|charge level|cpu|processor|memory|ram|disk|"
            r"storage|performance)\b",
            "The answer depends on live computer hardware state.",
            False,
        ),
        (
            r"\b(?:what|which)\b.*\b(?:apps|applications|windows|processes)\b.*\b"
            r"(?:open|running|active)\b",
            "The answer depends on live application state.",
            False,
        ),
    )
)

DESTRUCTIVE_RULES = _rules(
    (
        (
            r"^(?:please\s+)?(?:(?:can|could|would) you\s+)?"
            r"(?:delete|remove|erase|uninstall|format|wipe|overwrite|empty)\b",
            "The request changes or removes user data or software.",
            True,
        ),
        (
            r"^(?:please\s+)?(?:(?:can|could|would) you\s+)?"
            r"(?:shut ?down|restart|reboot|log ?out)\b",
            "The request changes the computer session or power state.",
            True,
        ),
    )
)

DIRECT_ACTION_RULES = _rules(
    (
        (
            r"^(?:do|run|try|repeat)\s+(?:that|it)\s+again\b",
            "The user asks Ron to repeat a verified previous action.",
            False,
        ),
        (
            r"^(?:set\s+(?:a\s+)?timer|remind\s+me)\b",
            "The prompt creates a local timer or reminder.",
            False,
        ),
        (
            r"^(?:please\s+)?(?:open|launch|close|start|stop)\b.*",
            "The prompt directly requests an application or computer action.",
            False,
        ),
        (
            r"^(?:please\s+)?(?:can|could|would) you\s+"
            r"(?:open|launch|close|start|stop|click|press|type|scroll|drag)\b",
            "The user asks Ron to operate an interface.",
            False,
        ),
        (
            r"\b(?:set|change|adjust|turn|mute|unmute)\b.*\b"
            r"(?:volume|brightness|wi-?fi|bluetooth|setting)\b",
            "The prompt requests a computer setting change.",
            False,
        ),
        (
            r"^(?:turn it (?:up|down)|make it (?:louder|quieter))$",
            "The prompt requests a small relative volume change.",
            False,
        ),
        (
            r"^(?:please\s+)?(?:click|press|type|scroll|drag|download|upload)\b",
            "The prompt directly requests interface or transfer work.",
            False,
        ),
        (
            r"\b(?:play|pause|resume|skip)\b.*\b"
            r"(?:song|music|track|spotify|video|youtube)\b",
            "The prompt requests media control.",
            False,
        ),
        (
            r"^(?:play|pause|resume|next|skip|previous)(?:\s+.*)?$",
            "The prompt requests media playback or selection.",
            False,
        ),
        (
            r"^(?:mute|unmute)(?:\s+(?:audio|sound|volume))?$",
            "The prompt requests an audio setting change.",
            False,
        ),
        (
            r"\b(?:create|edit|save|rename|move|copy)\b.*\b"
            r"(?:file|folder|document|spreadsheet|presentation)\b",
            "The prompt requests a file or application change.",
            False,
        ),
        (
            r"\b(?:send|post|publish)\b.*\b"
            r"(?:email|message|reply|comment|attachment)\b",
            "The prompt requests an external communication action.",
            True,
        ),
    )
)

OBVIOUS_CHAT_RULES = _rules(
    (
        (
            r"^(?:hi|hello|hey|good (?:morning|afternoon|evening)|how are you)\b",
            "The prompt is ordinary social conversation.",
            False,
        ),
        (
            r"^(?:what|who|why|when|where)\b",
            "The prompt is answerable as a conversational question.",
            False,
        ),
        (
            r"^(?:tell me|help me understand|give me|could you explain|can you explain)\b",
            "The prompt asks for conversational knowledge or content.",
            False,
        ),
    )
)

AMBIGUOUS_REQUEST = re.compile(
    r"\b(?:can you|could you|would you|please|find|search|check|make|create|run)\b",
    re.IGNORECASE,
)

CONFIRMATION_SENSITIVE = re.compile(
    r"\b(?:delete|remove|erase|uninstall|format|wipe|overwrite|empty|shut ?down|"
    r"restart|reboot|log ?out|send|post|publish|purchase|buy|pay)\b",
    re.IGNORECASE,
)

CLASSIFIER_INSTRUCTION = """Classify the user's request for a personal assistant.
Reply with exactly CHAT or AGENT and nothing else.
CHAT: conversation, knowledge, explanations, advice, planning, writing, or generated content.
AGENT: reading live external state or operating apps, files, devices, media, websites,
messages, settings, or the computer. If the request requires any tool, choose AGENT."""


class PromptRouter:
    """Classify prompts quickly without granting execution permission."""

    def __init__(self, client: OllamaClient, max_prompt_characters: int = 6_000) -> None:
        if not 100 <= max_prompt_characters <= 64_000:
            raise ValueError("Router prompt limit is invalid")
        self.client = client
        self.max_prompt_characters = max_prompt_characters

    def route(self, prompt: str) -> RoutingDecision:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("The prompt cannot be empty")
        if len(clean_prompt) > self.max_prompt_characters:
            raise ValueError(
                f"That prompt is over Ron's {self.max_prompt_characters:,}-character limit"
            )

        route_text = clean_prompt.replace("’", "'").replace("`", "'")

        explanation = self._first_match(EXPLANATION_RULES, route_text)
        if explanation is not None:
            return self._decision(RouteDestination.CHAT, explanation, 0.98)

        for rules in (EXTERNAL_STATE_RULES, DESTRUCTIVE_RULES, DIRECT_ACTION_RULES):
            match = self._first_match(rules, route_text)
            if match is not None:
                return self._decision(RouteDestination.AGENT, match, 0.98)

        obvious_chat = self._first_match(OBVIOUS_CHAT_RULES, route_text)
        if obvious_chat is not None:
            return self._decision(RouteDestination.CHAT, obvious_chat, 0.96)

        if AMBIGUOUS_REQUEST.search(route_text):
            return self._classify_with_local_model(clean_prompt)

        return RoutingDecision(
            destination=RouteDestination.CHAT,
            confidence=0.80,
            reason="No external action or live-state requirement was detected.",
            source=RouteSource.DETERMINISTIC,
        )

    @staticmethod
    def _first_match(rules: tuple[RoutingRule, ...], prompt: str) -> RoutingRule | None:
        return next((rule for rule in rules if rule.pattern.search(prompt)), None)

    @staticmethod
    def _decision(
        destination: RouteDestination, rule: RoutingRule, confidence: float
    ) -> RoutingDecision:
        return RoutingDecision(
            destination=destination,
            confidence=confidence,
            reason=rule.reason,
            source=RouteSource.DETERMINISTIC,
            requires_confirmation=rule.requires_confirmation,
        )

    def _classify_with_local_model(self, prompt: str) -> RoutingDecision:
        try:
            result = self.client.stream_chat(
                [
                    {"role": "system", "content": CLASSIFIER_INSTRUCTION},
                    {"role": "user", "content": prompt},
                ],
                think=False,
                max_output_tokens=6,
                temperature=0.0,
            )
        except OllamaError:
            return RoutingDecision(
                destination=RouteDestination.CHAT,
                confidence=0.35,
                reason="The classifier was unavailable, so Ron chose the non-executing path.",
                source=RouteSource.SAFE_FALLBACK,
            )

        first_label = re.search(r"\b(CHAT|AGENT)\b", result.text.upper())
        if first_label is None:
            return RoutingDecision(
                destination=RouteDestination.CHAT,
                confidence=0.35,
                reason="The classifier response was invalid, so Ron chose the non-executing path.",
                source=RouteSource.SAFE_FALLBACK,
            )
        destination = RouteDestination(first_label.group(1).lower())
        return RoutingDecision(
            destination=destination,
            confidence=0.82,
            reason="A local classifier resolved an ambiguous request.",
            source=RouteSource.LOCAL_MODEL,
            requires_confirmation=(
                destination is RouteDestination.AGENT
                and CONFIRMATION_SENSITIVE.search(prompt) is not None
            ),
        )
