"""Approved Spotify search, disambiguation and playback controls."""

from __future__ import annotations

import re
from collections.abc import Callable

from ron.agent.models import (
    ToolArgument,
    ToolArgumentKind,
    ToolExecutionContext,
    ToolResult,
    ToolRisk,
    ToolStatus,
)
from ron.agent.registry import ToolSpec
from ron.integrations.spotify import SpotifyClient, SpotifyError, SpotifyTrack

type SpotifyFactory = Callable[[], SpotifyClient | None]


def _availability(client_factory: SpotifyFactory) -> tuple[bool, str]:
    client = client_factory()
    if client is None:
        return False, "Spotify is not configured. Follow the README Spotify setup steps first."
    return client.availability()


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _confident_match(query: str, tracks: tuple[SpotifyTrack, ...]) -> bool:
    if len(tracks) == 1:
        return True
    match = re.fullmatch(r"(.+?)\s+by\s+(.+)", query.strip(), re.IGNORECASE)
    if match is None:
        return False
    title = _normalise(match.group(1))
    artist = _normalise(match.group(2))
    top = tracks[0]
    return _normalise(top.name) == title and any(
        _normalise(candidate) == artist for candidate in top.artists
    )


def build_spotify_tool(client_factory: SpotifyFactory) -> ToolSpec:
    def play_track(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        query = str(arguments["query"])
        choice_value = arguments.get("choice")
        choice = int(choice_value) if isinstance(choice_value, int) else None
        client = client_factory()
        if client is None:
            return ToolResult(
                "spotify_play_track",
                ToolStatus.UNSUPPORTED,
                "Spotify is not configured.",
            )
        try:
            tracks = client.search_tracks(query, context)
            if choice is None and not _confident_match(query, tracks):
                candidates = [
                    {
                        "choice": index,
                        "name": track.name,
                        "artists": list(track.artists),
                        "album": track.album,
                    }
                    for index, track in enumerate(tracks[:3], start=1)
                ]
                labels = "; ".join(
                    f"{item['choice']}) {item['name']} by {', '.join(item['artists'])}"
                    for item in candidates
                )
                return ToolResult(
                    "spotify_play_track",
                    ToolStatus.CLARIFICATION_REQUIRED,
                    f"I found several possible matches: {labels}. Which number did you mean?",
                    data={"kind": "spotify_track", "query": query, "candidates": candidates},
                )
            selected = tracks[(choice or 1) - 1]
            client.play_track(selected, context)
        except IndexError:
            return ToolResult(
                "spotify_play_track",
                ToolStatus.FAILED,
                "That Spotify choice is no longer available; please search again.",
            )
        except SpotifyError as error:
            return ToolResult(
                "spotify_play_track",
                ToolStatus.FAILED,
                f"I couldn't start that Spotify track: {error}",
            )
        artists = ", ".join(selected.artists)
        return ToolResult(
            "spotify_play_track",
            ToolStatus.SUCCESS,
            f"Playing {selected.name} by {artists} on Spotify.",
            data={"name": selected.name, "artists": list(selected.artists)},
        )

    return ToolSpec(
        "spotify_play_track",
        "Search for a Spotify track, clarify ambiguous matches and play the selection.",
        {
            "query": ToolArgument(ToolArgumentKind.TEXT, maximum_length=200),
            "choice": ToolArgument(
                ToolArgumentKind.INTEGER,
                minimum=1,
                maximum=5,
                required=False,
            ),
        },
        ToolRisk.EXTERNAL,
        play_track,
        timeout_seconds=30.0,
        availability=lambda: _availability(client_factory),
        run_in_background=True,
    )


def build_spotify_control_tool(client_factory: SpotifyFactory) -> ToolSpec:
    def control(
        arguments: dict[str, str | int], context: ToolExecutionContext
    ) -> ToolResult:
        action = str(arguments["action"])
        client = client_factory()
        if client is None:
            return ToolResult(
                "spotify_control_playback", ToolStatus.UNSUPPORTED, "Spotify is not configured."
            )
        try:
            client.control_playback(action, context)
        except SpotifyError as error:
            return ToolResult(
                "spotify_control_playback",
                ToolStatus.FAILED,
                f"I couldn't control Spotify playback: {error}",
            )
        labels = {
            "pause": "Spotify paused.",
            "resume": "Spotify resumed.",
            "next": "Skipped to the next Spotify track.",
            "previous": "Returned to the previous Spotify track.",
        }
        return ToolResult(
            "spotify_control_playback",
            ToolStatus.SUCCESS,
            labels[action],
            data={"action": action},
        )

    return ToolSpec(
        "spotify_control_playback",
        "Pause, resume, skip or go back using Spotify's supported playback API.",
        {
            "action": ToolArgument(
                ToolArgumentKind.ENUM,
                choices=("pause", "resume", "next", "previous"),
            )
        },
        ToolRisk.EXTERNAL,
        control,
        timeout_seconds=15.0,
        availability=lambda: _availability(client_factory),
    )
