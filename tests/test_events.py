from ron.core import Coordinator, EventType, RonEvent


def test_coordinator_routes_events() -> None:
    coordinator = Coordinator()
    received: list[RonEvent] = []
    coordinator.subscribe(EventType.SPEECH_STARTED, received.append)

    event = RonEvent(EventType.SPEECH_STARTED)
    coordinator.publish(event)

    assert received == [event]


def test_coordinator_isolates_a_broken_handler() -> None:
    coordinator = Coordinator()
    received: list[RonEvent] = []

    def fail(_: RonEvent) -> None:
        raise RuntimeError("simulated add-on failure")

    coordinator.subscribe(EventType.SPEECH_ENDED, fail)
    coordinator.subscribe(EventType.SPEECH_ENDED, received.append)
    event = RonEvent(EventType.SPEECH_ENDED)

    coordinator.publish(event)

    assert received == [event]
