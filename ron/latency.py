"""Small per-turn timing traces for finding real conversational bottlenecks."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import monotonic


@dataclass(slots=True)
class TurnTrace:
    turn_id: int
    source: str
    started_at: float = field(default_factory=monotonic)
    marks: dict[str, float] = field(default_factory=dict)
    durations: dict[str, float] = field(default_factory=dict)

    def mark(self, stage: str) -> None:
        self.marks.setdefault(stage, monotonic())

    def duration(self, stage: str, seconds: float) -> None:
        self.durations[stage] = max(0.0, float(seconds))

    def snapshot(self) -> dict[str, object]:
        """Return privacy-safe timings without prompts, transcripts, or model text."""
        return {
            "turn_id": self.turn_id,
            "source": self.source,
            "marks_seconds": {
                name: round(max(0.0, value - self.started_at), 4)
                for name, value in self.marks.items()
            },
            "durations_seconds": {
                name: round(max(0.0, value), 4)
                for name, value in self.durations.items()
            },
        }


class LatencyTracker:
    """Keep recent bounded traces and associate progress callbacks by thread."""

    def __init__(self, maximum_turns: int = 20) -> None:
        self._lock = threading.RLock()
        self._recent: deque[TurnTrace] = deque(maxlen=maximum_turns)
        self._next_id = 1
        self._local = threading.local()
        self._finish_listeners: list[Callable[[dict[str, object]], None]] = []

    def add_finish_listener(
        self, listener: Callable[[dict[str, object]], None]
    ) -> None:
        with self._lock:
            self._finish_listeners.append(listener)

    def start(self, source: str) -> TurnTrace:
        with self._lock:
            trace = TurnTrace(self._next_id, source)
            self._next_id += 1
        return trace

    @contextmanager
    def activate(self, trace: TurnTrace) -> Iterator[TurnTrace]:
        previous = getattr(self._local, "trace", None)
        self._local.trace = trace
        try:
            yield trace
        finally:
            self._local.trace = previous

    def on_progress(self, message: str) -> None:
        trace = getattr(self._local, "trace", None)
        if trace is None:
            return
        clean = message.casefold()
        stages = (
            ("understanding", "understand"),
            ("planning", "plan"),
            ("preflight", "checking"),
            ("tool", "running"),
            ("verification", "verified"),
            ("generation", "thinking"),
        )
        for stage, marker in stages:
            if marker in clean:
                trace.mark(stage)
                return

    def finish(self, trace: TurnTrace) -> None:
        trace.mark("complete")
        with self._lock:
            self._recent.append(trace)
            listeners = tuple(self._finish_listeners)
        snapshot = trace.snapshot()
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                continue

    def recent_snapshots(self, limit: int = 5) -> tuple[dict[str, object], ...]:
        bounded = max(1, min(20, int(limit)))
        with self._lock:
            traces = tuple(self._recent)[-bounded:]
        return tuple(trace.snapshot() for trace in traces)

    def health_summary(self) -> str:
        snapshots = self.recent_snapshots(5)
        if not snapshots:
            return "latency has no completed voice turns yet"

        def values(stage: str) -> list[float]:
            result: list[float] = []
            for snapshot in snapshots:
                marks = snapshot.get("marks_seconds", {})
                if isinstance(marks, dict) and isinstance(marks.get(stage), (int, float)):
                    result.append(float(marks[stage]))
            return result

        first_audio = values("first_audio")
        complete = values("complete")
        asr_values = [
            float(snapshot["durations_seconds"]["asr"])
            for snapshot in snapshots
            if isinstance(snapshot.get("durations_seconds"), dict)
            and isinstance(snapshot["durations_seconds"].get("asr"), (int, float))
        ]
        warnings: list[str] = []
        if asr_values and sum(asr_values) / len(asr_values) > 3.0:
            warnings.append("ASR is averaging over 3s")
        if first_audio and sum(first_audio) / len(first_audio) > 5.0:
            warnings.append("first audio is averaging over 5s")
        if complete and sum(complete) / len(complete) > 15.0:
            warnings.append("turn completion is averaging over 15s")
        if warnings:
            return "; ".join(warnings)
        return f"latency is within target across {len(snapshots)} recent turn(s)"

    def latest_summary(self) -> str:
        with self._lock:
            trace = self._recent[-1] if self._recent else None
        if trace is None:
            return "No completed turn timing is available yet."

        def elapsed(name: str) -> float:
            return trace.marks[name] - trace.started_at

        parts: list[str] = []
        if "asr" in trace.durations:
            parts.append(f"ASR {trace.durations['asr']:.2f}s")
        for stage, label in (
            ("first_token", "first token"),
            ("first_audio_byte", "first audio byte"),
            ("first_audio", "first audio"),
            ("assistant_complete", "answer ready"),
            ("complete", "total"),
        ):
            if stage in trace.marks:
                parts.append(f"{label} {elapsed(stage):.2f}s")
        progress = [
            stage
            for stage in (
                "understanding",
                "planning",
                "preflight",
                "tool",
                "verification",
                "generation",
            )
            if stage in trace.marks
        ]
        route = f"; stages: {', '.join(progress)}" if progress else ""
        return f"Turn {trace.turn_id} ({trace.source}): {', '.join(parts)}{route}."

    def report(self) -> str:
        return self.latest_summary()
