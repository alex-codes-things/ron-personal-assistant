from ron.display.quick_actions import DesktopQuickActions


def test_allowlisted_quick_actions_use_only_their_fixed_launchers() -> None:
    opened: list[str] = []
    actions = DesktopQuickActions(
        spotify_launcher=lambda: opened.append("spotify"),
        youtube_launcher=lambda: opened.append("youtube"),
    )

    spotify = actions.handle("open_spotify")
    youtube = actions.handle("open_youtube")
    rejected = actions.handle("open_command_prompt")

    assert spotify.success is True
    assert youtube.success is True
    assert rejected.success is False
    assert opened == ["spotify", "youtube"]


def test_repeated_quick_action_is_debounced() -> None:
    opened: list[str] = []
    now = [10.0]
    actions = DesktopQuickActions(
        spotify_launcher=lambda: opened.append("spotify"),
        youtube_launcher=lambda: opened.append("youtube"),
        clock=lambda: now[0],
    )

    first = actions.handle("open_spotify")
    duplicate = actions.handle("open_spotify")
    now[0] += 0.5
    later = actions.handle("open_spotify")

    assert first.success is True
    assert duplicate.success is True
    assert later.success is True
    assert opened == ["spotify", "spotify"]


def test_launcher_failure_returns_a_safe_result() -> None:
    def fail() -> None:
        raise OSError("private machine detail")

    actions = DesktopQuickActions(
        spotify_launcher=fail,
        youtube_launcher=lambda: None,
    )

    result = actions.handle("open_spotify")

    assert result.success is False
    assert result.message == "I couldn't open Spotify"

