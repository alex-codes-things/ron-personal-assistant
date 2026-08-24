"""Read-only Ron Network device status tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ron.agent.models import ToolResult, ToolRisk, ToolStatus
from ron.agent.registry import ToolSpec

if TYPE_CHECKING:
    from ron.network import NetworkService


def build_network_devices_tool(network: NetworkService | None) -> ToolSpec:
    def availability() -> tuple[bool, str]:
        if network is None:
            return False, "Ron Network is not connected to the agent tool layer."
        if not network.settings.enabled:
            return False, "Ron Network is disabled."
        return True, "Ron Network device registry is ready."

    def get_devices(arguments: dict[str, str | int]) -> ToolResult:
        del arguments
        if network is None:
            return ToolResult(
                "get_network_devices",
                ToolStatus.UNSUPPORTED,
                "Ron Network is not available.",
            )
        devices = sorted(network.registry.all(), key=lambda item: item.device_id)
        data = {
            "devices": [
                {
                    "device_id": device.device_id,
                    "friendly_name": device.friendly_name,
                    "device_type": device.device_type,
                    "state": device.connection_state.value,
                    "trust": device.trust_state.value,
                    "capabilities": sorted(device.capabilities),
                }
                for device in devices
            ]
        }
        if not devices:
            message = "Ron Network is available, but no devices are registered yet."
        else:
            visible = ", ".join(
                f"{device.friendly_name} is {device.connection_state.value}"
                for device in devices
            )
            message = f"Ron Network: {visible}."
        return ToolResult(
            "get_network_devices",
            ToolStatus.SUCCESS,
            message,
            data=data,
        )

    return ToolSpec(
        name="get_network_devices",
        description="Read the health and capabilities of devices in Ron Network.",
        arguments={},
        risk=ToolRisk.READ_ONLY,
        handler=get_devices,
        timeout_seconds=2.0,
        availability=availability,
    )
