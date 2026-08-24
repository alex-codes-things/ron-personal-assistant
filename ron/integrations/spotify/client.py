"""Small bounded Spotify Web API client for named-track playback."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ron.agent.models import ToolExecutionContext
from ron.integrations.spotify.storage import TokenStorageError, WindowsProtectedTokenStore

API_ROOT = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
MAX_RESPONSE_BYTES = 1_000_000


class SpotifyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SpotifySettings:
    client_id: str
    redirect_uri: str = "http://127.0.0.1:8765/callback"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9]{10,80}", self.client_id):
            raise ValueError("The Spotify client ID is invalid")
        parsed = urllib.parse.urlparse(self.redirect_uri)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.path != "/callback"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Spotify redirect URI must be a 127.0.0.1 HTTP callback")

    @classmethod
    def load(cls, project_root: Path) -> SpotifySettings | None:
        client_id = os.getenv("RON_SPOTIFY_CLIENT_ID", "").strip()
        redirect_uri = os.getenv("RON_SPOTIFY_REDIRECT_URI", "").strip()
        config_path = project_root / "runtime" / "data" / "spotify_config.json"
        if config_path.is_file():
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            if isinstance(payload, dict):
                if not client_id and isinstance(payload.get("client_id"), str):
                    client_id = payload["client_id"].strip()
                if not redirect_uri and isinstance(payload.get("redirect_uri"), str):
                    redirect_uri = payload["redirect_uri"].strip()
        if not client_id:
            return None
        return cls(client_id, redirect_uri or "http://127.0.0.1:8765/callback")


@dataclass(frozen=True, slots=True)
class SpotifyTrack:
    name: str
    artists: tuple[str, ...]
    uri: str
    album: str = ""


class SpotifyClient:
    def __init__(
        self,
        settings: SpotifySettings,
        token_store: WindowsProtectedTokenStore,
        *,
        timeout_seconds: float = 8.0,
    ) -> None:
        if not 1.0 <= timeout_seconds <= 20.0:
            raise ValueError("Spotify timeout must be between 1 and 20 seconds")
        self.settings = settings
        self.token_store = token_store
        self.timeout_seconds = timeout_seconds

    def availability(self) -> tuple[bool, str]:
        if not self.token_store.supported:
            return False, "Spotify playback is supported on Ron's Windows computer only."
        if not self.token_store.exists:
            return (
                False,
                "Spotify is not authorised yet. Run: python scripts\\setup_spotify.py "
                "--client-id YOUR_CLIENT_ID",
            )
        try:
            token = self.token_store.load()
            self._validate_token(token, require_refresh=True)
        except (SpotifyError, TokenStorageError):
            return False, "Spotify authorisation is missing or damaged; run the setup helper again."
        return True, "Spotify is authorised."

    def search_tracks(
        self, query: str, context: ToolExecutionContext | None = None
    ) -> tuple[SpotifyTrack, ...]:
        search_query = self._structured_query(query)
        payload = self._api_json(
            "GET",
            f"/search?{urllib.parse.urlencode({'q': search_query, 'type': 'track', 'limit': 5})}",
            context=context,
        )
        tracks = payload.get("tracks")
        items = tracks.get("items") if isinstance(tracks, dict) else None
        if not isinstance(items, list) or not items:
            raise SpotifyError(f"Spotify could not find a track matching {query!r}")
        results = tuple(
            track for item in items if (track := self._parse_track(item)) is not None
        )
        if not results:
            raise SpotifyError("Spotify returned no playable matching track")
        return results

    def play_track(
        self, track: SpotifyTrack, context: ToolExecutionContext | None = None
    ) -> None:
        device_id = self._active_device_id(context)
        if device_id is None:
            self._open_spotify()
            for _ in range(3):
                if context is None:
                    time.sleep(0.6)
                elif context.cancel_event.wait(min(0.6, context.remaining_seconds)):
                    context.checkpoint()
                if context is not None:
                    context.checkpoint()
                device_id = self._active_device_id(context)
                if device_id is not None:
                    break
        if device_id is None:
            raise SpotifyError(
                "Spotify has no available playback device. Open Spotify and play any track once."
            )
        query = urllib.parse.urlencode({"device_id": device_id})
        self._api_json(
            "PUT",
            f"/me/player/play?{query}",
            payload={"uris": [track.uri]},
            allow_empty=True,
            context=context,
        )

    def control_playback(
        self, action: str, context: ToolExecutionContext | None = None
    ) -> None:
        methods = {
            "pause": ("PUT", "/me/player/pause"),
            "resume": ("PUT", "/me/player/play"),
            "next": ("POST", "/me/player/next"),
            "previous": ("POST", "/me/player/previous"),
        }
        try:
            method, path = methods[action]
        except KeyError as error:
            raise SpotifyError("Unsupported Spotify playback action") from error
        self._api_json(method, path, allow_empty=True, context=context)

    def _active_device_id(
        self, context: ToolExecutionContext | None = None
    ) -> str | None:
        payload = self._api_json("GET", "/me/player/devices", context=context)
        devices = payload.get("devices")
        if not isinstance(devices, list):
            return None
        usable = [
            item
            for item in devices
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and not item.get("is_restricted", False)
        ]
        active = next((item for item in usable if item.get("is_active") is True), None)
        selected = active or (usable[0] if usable else None)
        return str(selected["id"]) if selected is not None else None

    def _api_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        allow_empty: bool = False,
        retry_auth: bool = True,
        retry_rate_limit: bool = True,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise SpotifyError("An invalid Spotify API path was rejected")
        if context is not None:
            context.checkpoint()
        token = self._access_token()
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{API_ROOT}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            timeout = self.timeout_seconds
            if context is not None:
                timeout = max(0.1, min(timeout, context.remaining_seconds))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 401 and retry_auth:
                self._refresh_access_token(force=True)
                return self._api_json(
                    method,
                    path,
                    payload=payload,
                    allow_empty=allow_empty,
                    retry_auth=False,
                    retry_rate_limit=retry_rate_limit,
                    context=context,
                )
            if error.code == 429 and retry_rate_limit:
                retry_after = (
                    error.headers.get("Retry-After", "1")
                    if error.headers is not None
                    else "1"
                )
                try:
                    delay = min(5.0, max(0.0, float(retry_after)))
                except ValueError:
                    delay = 1.0
                if context is None:
                    time.sleep(delay)
                elif context.cancel_event.wait(min(delay, context.remaining_seconds)):
                    context.checkpoint()
                return self._api_json(
                    method,
                    path,
                    payload=payload,
                    allow_empty=allow_empty,
                    retry_auth=retry_auth,
                    retry_rate_limit=False,
                    context=context,
                )
            detail = self._http_error_detail(error)
            raise SpotifyError(f"Spotify rejected the request ({error.code}): {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SpotifyError("Spotify is offline or did not respond in time") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SpotifyError("Spotify returned an unexpectedly large response")
        if not raw and allow_empty:
            return {}
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SpotifyError("Spotify returned invalid data") from error
        if not isinstance(result, dict):
            raise SpotifyError("Spotify returned invalid data")
        if context is not None:
            context.checkpoint()
        return result

    def _access_token(self) -> str:
        try:
            token = self.token_store.load()
        except TokenStorageError as error:
            raise SpotifyError("Spotify authorisation could not be loaded") from error
        self._validate_token(token, require_refresh=True)
        expires_at = token.get("expires_at")
        if not isinstance(expires_at, (int, float)) or expires_at <= time.time() + 30:
            token = self._refresh_access_token()
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise SpotifyError("Spotify access token is invalid")
        return access_token

    def _refresh_access_token(self, *, force: bool = False) -> dict[str, object]:
        del force
        try:
            current = self.token_store.load()
        except TokenStorageError as error:
            raise SpotifyError("Spotify authorisation could not be loaded") from error
        refresh_token = current.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise SpotifyError("Spotify needs to be authorised again")
        encoded = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.settings.client_id,
            }
        ).encode("ascii")
        request = urllib.request.Request(
            TOKEN_URL,
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SpotifyError("Spotify token refresh failed") from error
        try:
            refreshed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SpotifyError("Spotify returned an invalid token refresh") from error
        if not isinstance(refreshed, dict):
            raise SpotifyError("Spotify returned an invalid token refresh")
        refreshed.setdefault("refresh_token", refresh_token)
        refreshed["expires_at"] = time.time() + int(refreshed.get("expires_in", 3600))
        self._validate_token(refreshed, require_refresh=True)
        try:
            self.token_store.save(refreshed)
        except TokenStorageError as error:
            raise SpotifyError("Spotify's refreshed token could not be saved securely") from error
        return refreshed

    @staticmethod
    def _validate_token(token: dict[str, object], *, require_refresh: bool) -> None:
        if not isinstance(token.get("access_token"), str):
            raise SpotifyError("Spotify access token is invalid")
        if require_refresh and not isinstance(token.get("refresh_token"), str):
            raise SpotifyError("Spotify refresh token is invalid")

    @staticmethod
    def _parse_track(item: object) -> SpotifyTrack | None:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        uri = item.get("uri")
        artists_data = item.get("artists")
        album_data = item.get("album")
        if (
            not isinstance(name, str)
            or not isinstance(uri, str)
            or not uri.startswith("spotify:track:")
        ):
            return None
        if not isinstance(artists_data, list):
            return None
        artists = tuple(
            artist["name"]
            for artist in artists_data
            if isinstance(artist, dict) and isinstance(artist.get("name"), str)
        )
        album = album_data.get("name", "") if isinstance(album_data, dict) else ""
        return (
            SpotifyTrack(
                name=name,
                artists=artists,
                uri=uri,
                album=album if isinstance(album, str) else "",
            )
            if artists
            else None
        )

    @staticmethod
    def _structured_query(query: str) -> str:
        clean = query.strip()
        match = re.fullmatch(r"(.+?)\s+by\s+(.+)", clean, re.IGNORECASE)
        if match is None:
            return clean
        return f"track:{match.group(1).strip()} artist:{match.group(2).strip()}"

    @staticmethod
    def _http_error_detail(error: urllib.error.HTTPError) -> str:
        try:
            raw = error.read(4096)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return "request failed"
        if isinstance(payload, dict):
            detail = payload.get("error")
            if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                return detail["message"][:200]
            if isinstance(detail, str):
                return detail[:200]
        return "request failed"

    @staticmethod
    def _open_spotify() -> None:
        if os.name != "nt":
            return
        try:
            os.startfile("spotify:")
        except OSError:
            subprocess.Popen(
                ["explorer.exe", "spotify:"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
