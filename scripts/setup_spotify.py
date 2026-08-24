"""One-time Spotify Web API authorisation for named-song playback."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ron.integrations.spotify import SpotifySettings, WindowsProtectedTokenStore
from ron.integrations.spotify.oauth import SpotifyAuthorizationError, authorise
from ron.integrations.spotify.storage import TokenStorageError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Authorise Ron to search and control Spotify playback."
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("RON_SPOTIFY_CLIENT_ID", ""),
        help="Client ID from your Spotify developer app (not a client secret)",
    )
    parser.add_argument(
        "--redirect-uri",
        default="http://127.0.0.1:8765/callback",
        help="Exact loopback redirect URI registered in the Spotify app",
    )
    args = parser.parse_args()
    try:
        settings = SpotifySettings(args.client_id.strip(), args.redirect_uri.strip())
    except ValueError as error:
        parser.error(str(error))
    token_store = WindowsProtectedTokenStore(
        PROJECT_ROOT / "runtime" / "data" / "spotify_token.dat"
    )
    if not token_store.supported:
        print("Secure Spotify setup is available on Windows only.")
        return 1

    config_path = PROJECT_ROOT / "runtime" / "data" / "spotify_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {"client_id": settings.client_id, "redirect_uri": settings.redirect_uri},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        authorise(settings, token_store)
    except (SpotifyAuthorizationError, TokenStorageError) as error:
        print(f"Spotify setup failed safely: {error}")
        return 1
    print("Spotify setup complete. The OAuth token is encrypted for your Windows account.")
    print('Try: "Play Galway Girl by Ed Sheeran"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
