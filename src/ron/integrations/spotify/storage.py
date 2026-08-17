"""Windows DPAPI storage for Spotify OAuth tokens."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class TokenStorageError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    )
    return blob, buffer


class WindowsProtectedTokenStore:
    """Encrypt tokens for the current Windows user; never write plaintext tokens."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def supported(self) -> bool:
        return os.name == "nt"

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, payload: dict[str, object]) -> None:
        if not self.supported:
            raise TokenStorageError("Secure Spotify token storage requires Windows")
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        input_blob, input_buffer = _input_blob(raw)
        output_blob = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            ctypes.c_wchar_p("Ron Spotify OAuth"),
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        ):
            raise TokenStorageError("Windows could not encrypt the Spotify token")
        del input_buffer
        try:
            encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(base64.b64encode(encrypted))

    def load(self) -> dict[str, object]:
        if not self.supported:
            raise TokenStorageError("Secure Spotify token storage requires Windows")
        try:
            encrypted = base64.b64decode(self.path.read_bytes(), validate=True)
        except (OSError, ValueError) as error:
            raise TokenStorageError("The Spotify token file is missing or invalid") from error
        input_blob, input_buffer = _input_blob(encrypted)
        output_blob = _DataBlob()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        ):
            raise TokenStorageError("Windows could not decrypt the Spotify token")
        del input_buffer
        try:
            raw = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TokenStorageError("The decrypted Spotify token is invalid") from error
        if not isinstance(payload, dict):
            raise TokenStorageError("The decrypted Spotify token is invalid")
        return payload
