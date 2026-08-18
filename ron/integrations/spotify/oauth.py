"""Spotify Authorization Code with PKCE for Ron's local desktop setup."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ron.integrations.spotify.client import MAX_RESPONSE_BYTES, TOKEN_URL, SpotifySettings
from ron.integrations.spotify.storage import WindowsProtectedTokenStore

SCOPES = "user-modify-playback-state user-read-playback-state"


class SpotifyAuthorizationError(RuntimeError):
    pass


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    expected_path = "/callback"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.expected_path:
            self.send_error(404)
            return
        values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        type(self).result = {
            key: entries[0] for key, entries in values.items() if entries
        }
        body = (
            b"<!doctype html><title>Ron Spotify setup</title>"
            b"<h1>Spotify is connected to Ron.</h1>"
            b"<p>You can close this tab and return to PowerShell.</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def authorise(
    settings: SpotifySettings,
    token_store: WindowsProtectedTokenStore,
    *,
    timeout_seconds: float = 180.0,
) -> None:
    parsed = urllib.parse.urlparse(settings.redirect_uri)
    assert parsed.hostname == "127.0.0.1" and parsed.port is not None
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    _CallbackHandler.result = {}
    _CallbackHandler.expected_path = parsed.path
    server = ThreadingHTTPServer((parsed.hostname, parsed.port), _CallbackHandler)
    server.timeout = 0.5
    authorization_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
        {
            "client_id": settings.client_id,
            "response_type": "code",
            "redirect_uri": settings.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    print("Opening Spotify in your browser for permission...")
    if not webbrowser.open(authorization_url):
        print(f"Open this URL manually:\n{authorization_url}")
    deadline = time.monotonic() + timeout_seconds
    while not _CallbackHandler.result and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
    result = _CallbackHandler.result
    if not result:
        raise SpotifyAuthorizationError("Spotify authorisation timed out")
    if not secrets.compare_digest(result.get("state", ""), state):
        raise SpotifyAuthorizationError("Spotify returned an invalid security state")
    if "error" in result:
        raise SpotifyAuthorizationError(f"Spotify authorisation was declined: {result['error']}")
    code = result.get("code")
    if not code:
        raise SpotifyAuthorizationError("Spotify did not return an authorisation code")
    token = _exchange_code(settings, code, verifier)
    token_store.save(token)


def _exchange_code(
    settings: SpotifySettings, code: str, verifier: str
) -> dict[str, object]:
    encoded = urllib.parse.urlencode(
        {
            "client_id": settings.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.redirect_uri,
            "code_verifier": verifier,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=12.0) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise SpotifyAuthorizationError(
            f"Spotify rejected the token exchange ({error.code})"
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SpotifyAuthorizationError("Spotify token exchange could not connect") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SpotifyAuthorizationError("Spotify returned an unexpectedly large token response")
    try:
        token = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SpotifyAuthorizationError("Spotify returned invalid token data") from error
    if not isinstance(token, dict):
        raise SpotifyAuthorizationError("Spotify returned invalid token data")
    if not isinstance(token.get("access_token"), str) or not isinstance(
        token.get("refresh_token"), str
    ):
        raise SpotifyAuthorizationError("Spotify did not return the required tokens")
    token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
    return token

