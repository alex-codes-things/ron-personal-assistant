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


def test_accent_tolerant_wake_is_only_allowed_after_kws_confirmation() -> None:
    accepted = normalizer().normalize(
        "Hey rown open Spotify", require_wake=True, wake_detected=True
    )
    rejected = normalizer().normalize(
        "Hey rown open Spotify", require_wake=True, wake_detected=False
    )

    assert accepted.accepted is True
    assert accepted.text == "open Spotify"
    assert accepted.correction_notes
    assert rejected.accepted is False


def test_fuzzy_application_matching_is_conservative() -> None:
    corrected = normalizer().normalize(
        "Hey Ron open file explorah", require_wake=True, wake_detected=True
    )
    unrelated = normalizer().normalize(
        "Hey Ron open settings", require_wake=True, wake_detected=True
    )

    assert corrected.text == "open File Explorer"
    assert unrelated.text == "open settings"


def test_fuzzy_wake_does_not_turn_hey_john_into_hey_ron() -> None:
    result = normalizer().normalize(
        "Hey John open Spotify", require_wake=True, wake_detected=True
    )

    assert result.accepted is False


def test_calibrated_wake_aliases_require_acoustic_kws_gate() -> None:
    tuned = VoiceNormalizer(
        VoiceSettings(
            enabled=True,
            project_root=Path("."),
            wake_kws_aliases=("peron", "here on"),
        )
    )

    merged = tuned.normalize(
        "Peron set the volume to 22%", require_wake=True, wake_detected=True
    )
    spaced = tuned.normalize(
        "Here on open File Explorer", require_wake=True, wake_detected=True
    )
    no_kws = tuned.normalize(
        "Peron open Spotify", require_wake=True, wake_detected=False
    )
    continuous = tuned.normalize(
        "Here on the desk", require_wake=False, wake_detected=False
    )

    assert merged.accepted is True
    assert merged.text == "set the volume to 22%"
    assert spaced.accepted is True
    assert spaced.text == "open File Explorer"
    assert merged.correction_notes
    assert no_kws.accepted is False
    assert continuous.text == "Here on the desk"


def test_personal_kws_wake_variants_from_calibration_are_gated() -> None:
    tuned = VoiceNormalizer(
        VoiceSettings(
            enabled=True,
            project_root=Path("."),
            wake_kws_aliases=("tehran", "aaron"),
        )
    )

    volume = tuned.normalize(
        "Tehran, sit volume to 20%.", require_wake=True, wake_detected=True
    )
    spotify = tuned.normalize(
        "Aaron, open Spotify.", require_wake=True, wake_detected=True
    )
    no_kws = tuned.normalize(
        "Aaron, open Spotify.", require_wake=True, wake_detected=False
    )

    assert volume.accepted is True
    assert volume.text == "set volume to 20%."
    assert spotify.accepted is True
    assert spotify.text == "open Spotify."
    assert volume.correction_notes
    assert spotify.correction_notes
    assert no_kws.accepted is False


def test_personal_command_corrections_are_narrow() -> None:
    tuned = normalizer()

    galway = tuned.normalize(
        "Hey Ron, play Galway Girlbot on Spotify.",
        require_wake=True,
        wake_detected=True,
    )
    unrelated = tuned.normalize(
        "Hey Ron, tell me about a girlbot project.",
        require_wake=True,
        wake_detected=True,
    )

    assert galway.text == "play Galway Girl on Spotify."
    assert any("Galway Girl" in note for note in galway.correction_notes)
    assert unrelated.text == "tell me about a girlbot project."


def test_joined_heyron_variant_is_derived_from_wake_phrase_and_kws_gated() -> None:
    tuned = normalizer()

    accepted = tuned.normalize(
        "Heyron, open File Explorer.", require_wake=True, wake_detected=True
    )
    rejected = tuned.normalize(
        "Heyron, open File Explorer.", require_wake=True, wake_detected=False
    )

    assert accepted.accepted is True
    assert accepted.text == "open File Explorer."
    assert accepted.correction_notes
    assert rejected.accepted is False


def test_calibrated_galway_grohl_title_is_corrected_only_in_music_command() -> None:
    tuned = normalizer()

    music = tuned.normalize(
        "Hey Ron, play Galway Grohl on Spotify.",
        require_wake=True,
        wake_detected=True,
    )
    ordinary = tuned.normalize(
        "Hey Ron, tell me about Dave Grohl.",
        require_wake=True,
        wake_detected=True,
    )

    assert music.text == "play Galway Girl on Spotify."
    assert ordinary.text == "tell me about Dave Grohl."
