"""Application assembly: create Ron's systems and connect them together."""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path

from ron.core import Coordinator, EventType, FaceExpression, RonEvent
from ron.display import TabletFaceDisplay


class RonApplication:
    """Own Ron's lifecycle while individual systems remain independent."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.coordinator = Coordinator()
        self.face = TabletFaceDisplay(self.coordinator, self.project_root)
        self._shutdown = threading.Event()
        self._started = False
        self._logger = logging.getLogger(__name__)

        self.coordinator.subscribe(EventType.SHUTDOWN, self._handle_shutdown)

    def run(self) -> int:
        """Start Ron and block until Ctrl+C or a shutdown event."""
        self._configure_logging()
        self._install_signal_handlers()
        self.start()
        self._logger.info("Ron is running. Press Ctrl+C to stop him safely.")

        try:
            self._shutdown.wait()
        except KeyboardInterrupt:
            self._logger.info("Keyboard interrupt received")
        finally:
            self.stop()
        return 0

    def start(self) -> None:
        """Start each independent system once."""
        if self._started:
            return
        self.face.start()
        self.coordinator.publish(
            RonEvent(
                EventType.FACE_EXPRESSION,
                {"expression": FaceExpression.IDLE.value},
            )
        )
        self._started = True

    def stop(self) -> None:
        """Stop systems in reverse order and tolerate repeated calls."""
        if not self._started:
            return
        self.face.stop()
        self._started = False
        self._logger.info("Ron stopped safely")

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def _handle_shutdown(self, event: RonEvent) -> None:
        del event
        self.request_shutdown()

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def stop_from_signal(signum: int, frame: object) -> None:
            del signum, frame
            self.request_shutdown()

        for signal_name in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(signal_name, stop_from_signal)
            except (OSError, ValueError):
                continue

    @staticmethod
    def _configure_logging() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        )
