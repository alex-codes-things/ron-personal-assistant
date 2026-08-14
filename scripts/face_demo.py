"""Exercise every tablet-face state without starting Ron's future systems."""

from __future__ import annotations

import math
import time
from pathlib import Path

from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.display import TabletFaceDisplay
from ron.display.tablet_client import ConnectionStatus


def publish_expression(coordinator: Coordinator, expression: FaceExpression) -> None:
    coordinator.publish(
        RonEvent(EventType.FACE_EXPRESSION, {"expression": expression.value})
    )


def wait_for_connection(face: TabletFaceDisplay, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if face.client.status is ConnectionStatus.READY:
            return
        time.sleep(0.1)
    raise TimeoutError(
        f"Tablet did not become ready; last status was {face.client.status.value!r}"
    )


def demonstrate_speech(coordinator: Coordinator, seconds: float = 2.5) -> None:
    coordinator.publish(RonEvent(EventType.SPEECH_STARTED))
    started = time.monotonic()
    try:
        while time.monotonic() - started < seconds:
            elapsed = time.monotonic() - started
            level = 0.12 + abs(math.sin(elapsed * 8.3)) * 0.68
            coordinator.publish(RonEvent(EventType.SPEECH_LEVEL, {"level": level}))
            time.sleep(0.04)
    finally:
        coordinator.publish(RonEvent(EventType.SPEECH_ENDED))


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    coordinator = Coordinator()
    face = TabletFaceDisplay(coordinator, project_root)
    face.start()

    try:
        print("Waiting for Ron's tablet face...")
        wait_for_connection(face)
        print(f"Connected to tablet {face.client.serial}.")

        for expression in (
            FaceExpression.IDLE,
            FaceExpression.LISTENING,
            FaceExpression.THINKING,
            FaceExpression.HAPPY,
            FaceExpression.CONFUSED,
        ):
            print(f"Showing {expression.value}...")
            publish_expression(coordinator, expression)
            time.sleep(1.8)

        print("Demonstrating speech...")
        demonstrate_speech(coordinator)
        publish_expression(coordinator, FaceExpression.IDLE)
        time.sleep(1.5)
        print("Face demonstration completed safely.")
        return 0
    except KeyboardInterrupt:
        print("Face demonstration cancelled.")
        return 130
    finally:
        coordinator.publish(RonEvent(EventType.SPEECH_ENDED))
        face.stop()


if __name__ == "__main__":
    raise SystemExit(main())
