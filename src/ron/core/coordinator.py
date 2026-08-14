"""Small thread-safe event coordinator for Ron's independent systems."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from threading import RLock

from ron.core.events import EventType, RonEvent

EventHandler = Callable[[RonEvent], None]


class Coordinator:
    """Connect systems without making them import or own one another."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._lock = RLock()
        self._logger = logging.getLogger(__name__)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register a handler once for an event type."""
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a handler without failing when it is already absent."""
        with self._lock:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                return

    def publish(self, event: RonEvent) -> None:
        """Deliver an event while isolating failures in individual systems."""
        with self._lock:
            handlers = tuple(self._handlers[event.type])

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                self._logger.exception("A handler failed while processing %s", event.type)
