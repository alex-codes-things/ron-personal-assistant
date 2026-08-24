"""Human-readable permission tiers layered over Ron's existing tool risks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ron.agent.models import ToolResult, ToolRisk, ToolStatus
from ron.agent.registry import ToolRegistry, ToolSpec


class PermissionLevel(StrEnum):
    """The amount of authority a tool action needs."""

    SAFE = "safe"
    MODERATE = "moderate"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    level: PermissionLevel
    requires_confirmation: bool
    reason: str


class PermissionPolicy:
    """Translate low-level tool risks into simple user-facing permission levels."""

    _RISK_LEVELS = {
        ToolRisk.READ_ONLY: PermissionLevel.SAFE,
        ToolRisk.REVERSIBLE: PermissionLevel.SAFE,
        ToolRisk.EXTERNAL: PermissionLevel.MODERATE,
        ToolRisk.DESTRUCTIVE: PermissionLevel.SENSITIVE,
    }

    def decide(self, tool: ToolSpec) -> PermissionDecision:
        level = self._RISK_LEVELS.get(tool.risk, PermissionLevel.SENSITIVE)
        needs_confirmation = tool.requires_confirmation or level is PermissionLevel.SENSITIVE
        if level is PermissionLevel.SAFE:
            reason = "Routine read-only or easily reversible local action."
        elif level is PermissionLevel.MODERATE:
            reason = "Action affects an app, service, process, or other external state."
        else:
            reason = "High-impact action that can remove data or significantly change state."
        return PermissionDecision(level, needs_confirmation, reason)

    def summary(self, registry: ToolRegistry) -> str:
        counts = {level: 0 for level in PermissionLevel}
        for name in registry.names():
            spec = registry.spec(name)
            if spec is not None:
                counts[self.decide(spec).level] += 1
        return (
            f"permissions: {counts[PermissionLevel.SAFE]} safe, "
            f"{counts[PermissionLevel.MODERATE]} moderate, "
            f"{counts[PermissionLevel.SENSITIVE]} sensitive"
        )


class PermissionAwareRegistry(ToolRegistry):
    """Tool registry that enforces sensitive confirmation even if a tool forgets."""

    def __init__(self, policy: PermissionPolicy | None = None) -> None:
        super().__init__()
        self.permission_policy = policy or PermissionPolicy()

    def preflight(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        result = super().preflight(tool_name, arguments, confirmed=confirmed)
        if result.status is not ToolStatus.READY:
            return result
        spec = self.spec(tool_name)
        if spec is None:
            return result
        decision = self.permission_policy.decide(spec)
        if decision.requires_confirmation and not confirmed:
            return ToolResult(
                tool_name=tool_name,
                status=ToolStatus.CONFIRMATION_REQUIRED,
                message=(
                    f"The {tool_name} action is {decision.level.value} and requires "
                    "your confirmation before it can run."
                ),
                data={"permission_level": decision.level.value},
            )
        return result

    def schemas(self) -> list[dict[str, object]]:
        schemas = super().schemas()
        for schema in schemas:
            name = schema.get("name")
            spec = self.spec(str(name)) if isinstance(name, str) else None
            if spec is not None:
                decision = self.permission_policy.decide(spec)
                schema["permission_level"] = decision.level.value
                schema["requires_confirmation"] = decision.requires_confirmation
        return schemas
