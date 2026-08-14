"""High-level tablet face system exposed to the rest of Ron."""

from __future__ import annotations

from pathlib import Path

from ron.core.coordinator import Coordinator
from ron.core.events import EventType, FaceExpression, RonEvent
from ron.display.tablet_client import TabletClientConfig, TabletFaceClient


class TabletFaceDisplay:
    """Translate Ron events into semantic tablet-face commands."""

    def __init__(self, coordinator: Coordinator, project_root: Path) -> None:
        self._coordinator = coordinator
        self.client = TabletFaceClient(
            TabletClientConfig.from_environment(project_root)
        )
        coordinator.subscribe(EventType.FACE_EXPRESSION, self._handle_expression)
        coordinator.subscribe(EventType.SPEECH_STARTED, self._handle_speech_started)
        coordinator.subscribe(EventType.SPEECH_LEVEL, self._handle_speech_level)
        coordinator.subscribe(EventType.SPEECH_ENDED, self._handle_speech_ended)

    def start(self) -> None:
        self.client.start()

    def stop(self) -> None:
        self.client.stop()

    def _handle_expression(self, event: RonEvent) -> None:
        value = event.payload.get("expression", FaceExpression.IDLE)
        self.client.set_expression(FaceExpression(value))

    def _handle_speech_started(self, event: RonEvent) -> None:
        del event
        self.client.speech_started()

    def _handle_speech_level(self, event: RonEvent) -> None:
        self.client.set_speech_level(float(event.payload.get("level", 0.0)))

    def _handle_speech_ended(self, event: RonEvent) -> None:
        del event
        self.client.speech_ended()
