from pathlib import Path

from ron.voice.normalizer import VoiceNormalizer
from ron.voice.settings import VoiceSettings


def normalizer() -> VoiceNormalizer:
    return VoiceNormalizer(VoiceSettings(enabled=True, project_root=Path(".")))


def test_wake_phrase_is_verified_and_removed() -> None:
    result = normalizer().normalize(
        "Hey, Ron, what time is it?", require_wake=True, wake_detected=True
    )

    assert result.accepted is True
    assert result.wake_phrase == "hey ron"
    assert result.text == "what time is it?"


def test_false_wake_is_rejected_without_a_command() -> None:
    result = normalizer().normalize(
        "We were only talking about dinner", require_wake=True, wake_detected=True
    )

    assert result.accepted is False
    assert result.text == ""


def test_known_split_application_and_volume_phrases_are_corrected() -> None:
    spotify = normalizer().normalize(
        "Hey Ron open spot the fi", require_wake=True, wake_detected=True
    )
    volume = normalizer().normalize(
        "Hey Ron set colume two twenty", require_wake=True, wake_detected=True
    )

    assert spotify.text == "open Spotify"
    assert volume.text == "set volume to twenty"
    assert spotify.correction_notes
    assert volume.correction_notes


def test_ambiguous_volume_two_requires_clarification() -> None:
    result = normalizer().normalize(
        "Hey Ron set volume two", require_wake=True, wake_detected=True
    )

    assert result.accepted is True
    assert result.clarification is not None


def test_wake_only_opens_command_window() -> None:
    result = normalizer().normalize("Hey Ron", require_wake=True, wake_detected=True)

    assert result.accepted is True
    assert result.waiting_for_command is True
