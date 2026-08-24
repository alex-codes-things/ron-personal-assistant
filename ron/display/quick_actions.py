"""Allowlisted desktop actions requested by Ron's authenticated tablet."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

SUPPORTED_QUICK_ACTIONS = frozenset({"open_spotify", "open_youtube"})

type ActionLauncher = Callable[[], None]
type Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class QuickActionResult:
    """Small response returned to the tablet after a quick action."""

    success: bool
    message: str


class DesktopQuickActions:
    """Open fixed Windows targets without accepting commands or arbitrary URLs."""

    def __init__(
        self,
        *,
        spotify_launcher: ActionLauncher | None = None,
        youtube_launcher: ActionLauncher | None = None,
        cooldown_seconds: float = 0.45,
        clock: Clock = monotonic,
    ) -> None:
        if not 0.1 <= cooldown_seconds <= 5.0:
            raise ValueError("Quick-action cooldown must be between 0.1 and 5 seconds")
        self._launchers: dict[str, tuple[str, ActionLauncher]] = {
            "open_spotify": ("Spotify", spotify_launcher or _open_spotify),
            "open_youtube": (
                "YouTube in Brave",
                youtube_launcher or _open_youtube_in_brave,
            ),
        }
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._last_triggered: dict[str, float] = {}
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)

    def handle(self, action: str) -> QuickActionResult:
        """Run one known action and return a bounded user-facing result."""
        target = self._launchers.get(action)
        if target is None or action not in SUPPORTED_QUICK_ACTIONS:
            self._logger.warning("Rejected unknown tablet quick action: %r", action)
            return QuickActionResult(False, "That tablet action is not allowed")

        label, launcher = target
        now = self._clock()
        with self._lock:
            previous = self._last_triggered.get(action)
            if previous is not None and now - previous < self._cooldown_seconds:
                return QuickActionResult(True, f"{label} is already opening")
            self._last_triggered[action] = now

        try:
            launcher()
        except Exception as error:
            self._logger.warning("Tablet quick action %s failed: %s", action, error)
            return QuickActionResult(False, f"I couldn't open {label}")

        self._logger.info("Tablet quick action completed: %s", action)
        return QuickActionResult(True, f"Opening {label}")


def _open_spotify() -> None:
    if os.name != "nt":
        raise OSError("Spotify quick launch is available on Windows only")
    start_file = getattr(os, "startfile", None)
    if start_file is None:
        raise OSError("Windows URI launching is unavailable")
    start_file("spotify:")


def _open_youtube_in_brave() -> None:
    if os.name != "nt":
        raise OSError("Brave quick launch is available on Windows only")

    executable = _find_brave_executable()
    if executable is None:
        raise FileNotFoundError("Brave Browser was not found")

    subprocess.Popen(
        [str(executable), "--new-tab", "https://www.youtube.com/"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _find_brave_executable() -> Path | None:
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.getenv(variable)
        if root:
            candidates.append(
                Path(root)
                / "BraveSoftware"
                / "Brave-Browser"
                / "Application"
                / "brave.exe"
            )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    discovered = shutil.which("brave.exe") or shutil.which("brave")
    return Path(discovered) if discovered else None

