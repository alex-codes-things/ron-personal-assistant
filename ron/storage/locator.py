"""Locate Ron's external memory drive without trusting a drive letter."""

from __future__ import annotations

import ctypes
import json
import os
from collections.abc import Iterable
from pathlib import Path

IDENTITY_FILENAME = ".ron-storage.json"
DEFAULT_VOLUME_LABEL = "RON_STORAGE"


def _windows_drive_roots() -> Iterable[Path]:
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
    for index in range(26):
        if bitmask & (1 << index):
            yield Path(f"{chr(65 + index)}:/")


def _windows_volume_label(root: Path) -> str | None:
    volume_name = ctypes.create_unicode_buffer(261)
    filesystem_name = ctypes.create_unicode_buffer(261)
    serial_number = ctypes.c_ulong()
    max_component = ctypes.c_ulong()
    flags = ctypes.c_ulong()
    success = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
        str(root),
        volume_name,
        len(volume_name),
        ctypes.byref(serial_number),
        ctypes.byref(max_component),
        ctypes.byref(flags),
        filesystem_name,
        len(filesystem_name),
    )
    return volume_name.value if success else None


def read_identity(root: Path) -> dict[str, object] | None:
    path = root / IDENTITY_FILENAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def locate_storage_root(
    volume_label: str = DEFAULT_VOLUME_LABEL,
    *,
    expected_storage_id: str | None = None,
) -> Path | None:
    """Return the configured drive, exact bound drive, or initial labelled drive."""
    explicit = os.getenv("RON_STORAGE_PATH", "").strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.exists() and candidate.is_dir() else None

    if os.name != "nt":
        return None

    labelled: Path | None = None
    marked: Path | None = None
    for root in _windows_drive_roots():
        identity = read_identity(root)
        if identity is not None:
            storage_id = str(identity.get("storage_id", ""))
            if expected_storage_id and storage_id == expected_storage_id:
                return root
            if (
                not expected_storage_id
                and str(identity.get("display_name", "")).casefold()
                == volume_label.casefold()
            ):
                marked = marked or root
        if not expected_storage_id:
            label = _windows_volume_label(root)
            if label and label.casefold() == volume_label.casefold():
                labelled = labelled or root
    return marked or labelled
