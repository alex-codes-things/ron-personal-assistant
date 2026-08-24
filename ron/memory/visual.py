"""Visual-memory foundation for future screenshot-assisted problem solving."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ron.memory.catalog import MemoryCatalog
from ron.memory.models import ScreenshotMode, VisualCategory, VisualMemoryRecord
from ron.storage import StorageManager
from ron.storage.atomic import atomic_write_json

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class VisualMemoryService:
    """Store screenshots and analysis safely without depending on capture technology."""

    def __init__(
        self,
        project_root: Path,
        storage: StorageManager,
        catalog: MemoryCatalog,
    ) -> None:
        self.storage = storage
        self.catalog = catalog
        self._settings_path = (
            Path(project_root) / "runtime" / "memory" / "core" / "visual_settings.json"
        )
        self.mode = _load_mode(self._settings_path)

    def store_screenshot(
        self,
        image: bytes,
        *,
        extension: str = ".png",
        category: VisualCategory = VisualCategory.GENERAL,
        summary: str | None = None,
        application: str | None = None,
        window_title: str | None = None,
        project: str | None = None,
        session_id: str | None = None,
        analysis: dict[str, Any] | None = None,
        tags: tuple[str, ...] = (),
        resolved: bool | None = None,
    ) -> VisualMemoryRecord:
        if self.mode is ScreenshotMode.OFF:
            raise RuntimeError("Visual memory is disabled")
        if not image:
            raise ValueError("Screenshot data cannot be empty")
        extension = extension.casefold()
        if extension not in _ALLOWED_EXTENSIONS:
            raise ValueError("Visual memory supports PNG, JPEG and WebP screenshots")

        created = datetime.now(UTC)
        visual_id = uuid.uuid4().hex
        category_directory = {
            VisualCategory.CODING: "Coding",
            VisualCategory.APPLICATION: "Applications",
            VisualCategory.ERROR: "Errors",
            VisualCategory.GENERAL: "General",
        }[category]
        date_path = f"{created:%Y}/{created:%m}/{created:%d}"
        image_path = (
            f"Visual_Memory/Screenshots/{category_directory}/{date_path}/"
            f"VM_{visual_id}{extension}"
        )
        analysis_path = f"Visual_Memory/Analysis/{created:%Y}/{created:%m}/VM_{visual_id}.json"

        stored_image = self.storage.save_bytes(image_path, image)
        analysis_payload = {
            "schema": 1,
            "visual_id": visual_id,
            "created_utc": created.isoformat(),
            "category": category.value,
            "application": application,
            "window_title": window_title,
            "project": project,
            "session_id": session_id,
            "summary": summary,
            "tags": list(tags),
            "resolved": resolved,
            "image_path": image_path,
            "analysis": analysis or {},
        }
        stored_analysis = self.storage.save_json(analysis_path, analysis_payload)
        record = VisualMemoryRecord(
            visual_id=visual_id,
            category=category,
            image_path=image_path,
            analysis_path=analysis_path,
            created_utc=created.isoformat(),
            summary=summary,
            application=application,
            project=project,
            queued=stored_image.queued or stored_analysis.queued,
            image_sha256=stored_image.sha256,
            analysis_sha256=stored_analysis.sha256,
        )
        self.catalog.upsert_visual(record)
        return record

    def load_image(self, visual_id: str) -> bytes:
        record = self.catalog.get_visual(visual_id)
        if record is None:
            raise KeyError(f"Unknown visual memory: {visual_id}")
        payload = self.storage.read_bytes(record.image_path)
        digest = hashlib.sha256(payload).hexdigest()
        if record.image_sha256 and digest != record.image_sha256:
            raise OSError(f"Visual memory checksum verification failed: {visual_id}")
        return payload

    def load_analysis(self, visual_id: str) -> dict[str, Any]:
        record = self.catalog.get_visual(visual_id)
        if record is None:
            raise KeyError(f"Unknown visual memory: {visual_id}")
        payload = self.storage.read_bytes(record.analysis_path)
        digest = hashlib.sha256(payload).hexdigest()
        if record.analysis_sha256 and digest != record.analysis_sha256:
            raise OSError(f"Visual analysis checksum verification failed: {visual_id}")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Visual analysis is invalid: {visual_id}")
        return value

    def set_mode(self, mode: ScreenshotMode) -> None:
        # The capture layer will call this. We intentionally do not implement
        # continuous background capture in the memory subsystem.
        self.mode = mode
        atomic_write_json(self._settings_path, {"schema": 1, "mode": mode.value})

    def refresh_queue_flag(self, record: VisualMemoryRecord) -> None:
        pending = self.storage.is_pending(record.image_path) or self.storage.is_pending(
            record.analysis_path
        )
        self.catalog.set_visual_queued(record.visual_id, pending)


def _load_mode(settings_path: Path) -> ScreenshotMode:
    environment = os.getenv("RON_VISUAL_MEMORY_MODE")
    if environment is not None and environment.strip():
        raw = environment.strip().casefold()
    else:
        try:
            value = json.loads(settings_path.read_text(encoding="utf-8"))
            raw = str(value.get("mode", "")).strip().casefold()
        except (OSError, ValueError, TypeError, AttributeError):
            raw = ""
    try:
        return ScreenshotMode(raw)
    except ValueError:
        return ScreenshotMode.ON_REQUEST
