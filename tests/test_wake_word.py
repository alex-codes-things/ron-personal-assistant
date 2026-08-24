from pathlib import Path

from ron.voice.settings import VoiceSettings
from ron.voice.wake_word import SherpaWakeWordDetector


def test_high_sensitivity_profile_improves_old_balanced_env_values() -> None:
    cfg = VoiceSettings(
        enabled=True,
        project_root=Path("."),
        wake_threshold=0.35,
        wake_score=1.5,
        wake_sensitivity="high",
    )
    detector = SherpaWakeWordDetector(cfg)

    threshold, score, paths, trailing_blanks, threads = detector._sensitivity_profile()

    assert threshold == 0.20
    assert score == 2.00
    assert paths == 8
    assert trailing_blanks == 1
    assert threads == 2


def test_balanced_sensitivity_preserves_explicit_threshold_and_score() -> None:
    cfg = VoiceSettings(
        enabled=True,
        project_root=Path("."),
        wake_threshold=0.31,
        wake_score=1.4,
        wake_sensitivity="balanced",
    )
    detector = SherpaWakeWordDetector(cfg)

    threshold, score, *_ = detector._sensitivity_profile()

    assert threshold == 0.31
    assert score == 1.4
