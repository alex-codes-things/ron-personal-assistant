"""Routing additions for v0.9 Agent Core requests."""

from __future__ import annotations

import re

from ron.routing import (
    PromptRouter,
    RouteDestination,
    RouteSource,
    RoutingDecision,
)

CORE_AGENT_REQUEST = re.compile(
    r"\b(?:prepare|set up|get ready)\b.*\b(?:workspace|project|ron)\b"
    r"|\b(?:open|check)\b.*\b(?:ron project|ron repo|workspace|repository)\b"
    r"|\b(?:run|start)\b.*\b(?:tests|pytest|test suite)\b"
    r"|\b(?:nexus|ron network|ron face|devices?)\b.*"
    r"\b(?:online|offline|connected|status|health)\b"
    r"|\b(?:what|show|list|check|how(?:'s| is| are))\b.*"
    r"\b(?:processes?|servers?|test run|tests?|scripts?)\b"
    r"|\b(?:stop|cancel)\b.*\b(?:process|server|test run|tests|script)\b"
    r"|\bwhy\b.*\bfans?\b.*\b(?:loud|fast)\b"
    r"|\bcan i\b.*\b(?:run|play)\b.*\bgames?\b",
    re.IGNORECASE,
)


class AgentCoreRouter(PromptRouter):
    """Route known v0.9 live-state and workspace requests without model latency."""

    def route(self, prompt: str) -> RoutingDecision:
        if CORE_AGENT_REQUEST.search(prompt.strip()):
            return RoutingDecision(
                destination=RouteDestination.AGENT,
                confidence=0.99,
                reason="The request needs Ron's live Agent Core skills or workspace state.",
                source=RouteSource.DETERMINISTIC,
            )
        return super().route(prompt)
