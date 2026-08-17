"""Immutable result produced for every user prompt."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
