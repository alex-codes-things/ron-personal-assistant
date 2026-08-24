from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from ron.health import HealthMonitor, PerformanceArchive
from ron.latency import LatencyTracker
from ron.storage import StorageManager
from ron.voice.models import VoiceState


def test_performance_archive_queues_private_safe_timings_when_drive_is_offline(
    tmp_path: Path,
) -> None:
    storage = StorageManager(tmp_path, locator=lambda: None)
    storage.refresh_once()
    archive = PerformanceArchive(storage)
    archive.start()

    archive.record(
        {
            "turn_id": 7,
            "source": "voice",
            "marks_seconds": {"first_audio": 1.25},
            "durations_seconds": {"asr": 0.4},
        }
    )
    deadline = time.monotonic() + 2.0
    while storage.pending_stats()[0] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    archive.stop()

    queued = tuple(storage.queue_objects.rglob("*.json"))
    assert len(queued) == 1
    record = json.loads(queued[0].read_text(encoding="utf-8"))
    assert record["turn_id"] == 7
    assert record["source"] == "voice"
    assert record["marks_seconds"] == {"first_audio": 1.25}
    assert "prompt" not in record
    assert "transcript" not in record
    assert archive.status_label() == "1 performance record(s) archived"


def test_health_report_treats_disconnected_optional_face_as_a_note(
    tmp_path: Path,
) -> None:
    storage = StorageManager(tmp_path, locator=lambda: None)
    storage.refresh_once()
    latency = LatencyTracker()
    trace = latency.start("voice")
    trace.duration("asr", 0.2)
    trace.mark("first_audio")
    latency.finish(trace)
    archive = PerformanceArchive(storage)

    class Diagnostics:
        @staticmethod
        def snapshot():
            return SimpleNamespace(
                state=VoiceState.READY,
                device="test microphone",
                microphone_overflows=0,
            )

    class Registry:
        @staticmethod
        def capability_report():
            return (("ready_tool", True, "ready"),)

    monitor = HealthMonitor(
        ai_label=lambda: "Groq ready",
        voice=SimpleNamespace(diagnostics=Diagnostics()),
        speech=SimpleNamespace(
            status_label=lambda: "speech output configured (Groq streaming)"
        ),
        face=SimpleNamespace(connection_label=lambda: "offline—optional"),
        storage=storage,
        agent=SimpleNamespace(registry=Registry()),
        latency=latency,
        archive=archive,
    )

    report = monitor.report()

    assert report.startswith("Health DEGRADED:")
    assert "external storage is degraded" in report
    assert "tablet face disconnected (optional)" in report
    assert "warnings: tablet face" not in report
