"""High-level tablet face system exposed to the rest of Ron."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from math import isfinite
from pathlib import Path
from time import monotonic

from ron.core import Coordinator
from ron.core import EventType, FaceExpression, RonEvent
from ron.display.tablet_client import (
    ConnectionStatus,
    FaceConnectionUpdate,
    TabletClientConfig,
    TabletFaceClient,
)

NoticeListener = Callable[[str], None]


def _reminder_interval_from_environment() -> float:
    raw = os.getenv("RON_FACE_REMINDER_MINUTES", "20").strip()
    try:
        minutes = float(raw)
    except ValueError:
        minutes = 20.0
    if not isfinite(minutes):
        minutes = 20.0
    return max(5.0, min(240.0, minutes)) * 60.0


class TabletFaceDisplay:
    """Translate Ron events into semantic tablet-face commands."""

    def __init__(
        self,
        coordinator: Coordinator,
        project_root: Path,
        *,
        client: TabletFaceClient | None = None,
        reminder_interval_seconds: float | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._logger = logging.getLogger(__name__)
        self.client = client or TabletFaceClient(
            TabletClientConfig.from_environment(project_root),
        )
        self._notice_listeners: list[NoticeListener] = []
        self._notice_lock = threading.RLock()
        self._offline_announced = False
        self._last_offline_notice = 0.0
        self._last_notice_kind = ""
        self._reminder_interval = (
            max(1.0, reminder_interval_seconds)
            if reminder_interval_seconds is not None
            else _reminder_interval_from_environment()
        )
        self.client.add_status_listener(self._handle_connection_update)
        coordinator.subscribe(EventType.FACE_EXPRESSION, self._handle_expression)
        coordinator.subscribe(EventType.SPEECH_STARTED, self._handle_speech_started)
        coordinator.subscribe(EventType.SPEECH_LEVEL, self._handle_speech_level)
        coordinator.subscribe(EventType.SPEECH_ENDED, self._handle_speech_ended)

    def start(self) -> None:
        try:
            self.client.start()
        except Exception:
            self._logger.debug(
                "Tablet face could not start; Ron is continuing without it",
                exc_info=True,
            )
            self._announce_offline(force=True)

    def stop(self) -> None:
        try:
            self.client.stop()
        except Exception:
            self._logger.debug("Tablet face did not stop cleanly", exc_info=True)

    def add_notice_listener(self, listener: NoticeListener) -> None:
        with self._notice_lock:
            if listener not in self._notice_listeners:
                self._notice_listeners.append(listener)

    def connection_label(self) -> str:
        status = self.client.status
        if status is ConnectionStatus.READY:
            return "connected"
        if status is ConnectionStatus.UNAUTHORIZED:
            return "permission needed—unlock the tablet and approve USB debugging"
        if status in {
            ConnectionStatus.STARTING_APP,
            ConnectionStatus.CONNECTING,
            ConnectionStatus.HANDSHAKING,
        }:
            return "connecting"
        if status is ConnectionStatus.STOPPED:
            return "stopped"
        return "offline—plug in the Nexus 7 when convenient"

    def _handle_connection_update(self, update: FaceConnectionUpdate) -> None:
        if update.status is ConnectionStatus.READY:
            with self._notice_lock:
                should_announce = self._offline_announced
                self._offline_announced = False
                self._last_notice_kind = ""
            if should_announce:
                self._emit_notice("[FACE CONNECTED] Ron's tablet face is back online.")
            return
        if update.status in {
            ConnectionStatus.RETRYING,
            ConnectionStatus.UNAUTHORIZED,
        }:
            self._announce_offline(
                force=False,
                unauthorized=update.status is ConnectionStatus.UNAUTHORIZED,
            )

    def _announce_offline(
        self,
        *,
        force: bool,
        unauthorized: bool = False,
    ) -> None:
        now = monotonic()
        notice_kind = "unauthorized" if unauthorized else "offline"
        with self._notice_lock:
            due = (
                force
                or not self._offline_announced
                or notice_kind != self._last_notice_kind
                or now - self._last_offline_notice >= self._reminder_interval
            )
            if not due:
                return
            self._offline_announced = True
            self._last_offline_notice = now
            self._last_notice_kind = notice_kind
        if unauthorized:
            message = (
                "[FACE NEEDS PERMISSION] Ron is still fully working. Unlock the "
                "Nexus 7 and approve USB debugging when convenient."
            )
        else:
            message = (
                "[FACE OFFLINE] Ron is still fully working. Plug in the Nexus 7 "
                "when convenient."
            )
        self._emit_notice(message)

    def _emit_notice(self, message: str) -> None:
        with self._notice_lock:
            listeners = tuple(self._notice_listeners)
        for listener in listeners:
            try:
                listener(message)
            except Exception:
                self._logger.exception("Tablet face notice listener failed")

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
