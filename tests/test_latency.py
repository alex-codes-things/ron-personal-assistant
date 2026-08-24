from ron.latency import LatencyTracker


def test_latency_tracker_reports_voice_stages() -> None:
    tracker = LatencyTracker()
    archived = []
    tracker.add_finish_listener(archived.append)
    trace = tracker.start("voice")
    trace.duration("asr", 0.31)
    with tracker.activate(trace):
        tracker.on_progress("Planning the safest approved action…")
    trace.mark("first_token")
    trace.mark("first_audio_byte")
    trace.mark("first_audio")
    tracker.finish(trace)

    summary = tracker.latest_summary()

    assert "ASR 0.31s" in summary
    assert "first token" in summary
    assert "first audio byte" in summary
    assert "first audio" in summary
    assert "planning" in summary
    assert archived[0]["turn_id"] == 1
    assert "prompt" not in archived[0]
