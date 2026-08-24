from pathlib import Path

from ron.memory import MemoryKind, MemoryService, VisualCategory, VisualMemoryService
from ron.storage import StorageManager


def test_memory_is_indexed_locally_and_stored_externally(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    storage = StorageManager(project, locator=lambda: external)
    storage.refresh_once()
    memory = MemoryService(project, storage)

    record = memory.remember(
        MemoryKind.PROJECT,
        "The storage manager safely queues writes when the HDD disappears.",
        project="Ron",
        importance=80,
    )

    assert record.queued is False
    assert (external / Path(record.relative_path)).exists()
    found = memory.catalog.search("storage HDD")
    assert found and found[0].memory_id == record.memory_id


def test_memory_write_queues_when_external_drive_is_missing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    storage = StorageManager(project, locator=lambda: None)
    storage.refresh_once()
    memory = MemoryService(project, storage)

    record = memory.remember_conversation("Hello", "Hi there")

    assert record.queued is True
    assert storage.is_pending(record.relative_path)
    assert memory.catalog.counts()[0] == 1


def test_visual_memory_stores_image_and_analysis_separately(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    storage = StorageManager(project, locator=lambda: external)
    storage.refresh_once()
    memory = MemoryService(project, storage)
    visual = VisualMemoryService(project, storage, memory.catalog)

    record = visual.store_screenshot(
        b"not-a-real-png-but-valid-storage-bytes",
        category=VisualCategory.CODING,
        summary="Python import error in VS Code",
        application="Visual Studio Code",
        project="Ron",
        analysis={"error": "ModuleNotFoundError"},
        tags=("python", "import"),
    )

    assert (external / Path(record.image_path)).read_bytes().startswith(b"not-a-real-png")
    assert (external / Path(record.analysis_path)).exists()
    assert memory.catalog.counts() == (0, 1)


def test_memory_load_verifies_long_term_checksum(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    storage = StorageManager(project, locator=lambda: external)
    storage.refresh_once()
    memory = MemoryService(project, storage)
    record = memory.remember(MemoryKind.KNOWLEDGE, "Known-good memory")

    assert memory.load(record.memory_id)["content"] == "Known-good memory"
    (external / Path(record.relative_path)).write_bytes(b"tampered")

    import pytest

    with pytest.raises(OSError, match="checksum"):
        memory.load(record.memory_id)


def test_visual_memory_mode_persists_locally(tmp_path: Path, monkeypatch) -> None:
    from ron.memory import ScreenshotMode

    monkeypatch.delenv("RON_VISUAL_MEMORY_MODE", raising=False)
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    storage = StorageManager(project, locator=lambda: external)
    storage.refresh_once()
    memory = MemoryService(project, storage)

    visual = VisualMemoryService(project, storage, memory.catalog)
    visual.set_mode(ScreenshotMode.OFF)
    reloaded = VisualMemoryService(project, storage, memory.catalog)

    assert reloaded.mode is ScreenshotMode.OFF


def test_visual_memory_can_be_loaded_and_linked_to_experience(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    storage = StorageManager(project, locator=lambda: external)
    storage.refresh_once()
    memory = MemoryService(project, storage)
    visual = VisualMemoryService(project, storage, memory.catalog)

    screenshot = visual.store_screenshot(
        b"coding-screen",
        category=VisualCategory.ERROR,
        summary="Import failed in storage manager",
        application="Visual Studio Code",
        window_title="manager.py - Ron",
        project="Ron",
        session_id="debug-session-1",
        analysis={"error": "ModuleNotFoundError"},
    )
    experience = memory.remember_experience(
        "A Python import failed while running Ron.",
        "Install the missing dependency in Ron's active virtual environment.",
        project="Ron",
        visual_ids=(screenshot.visual_id,),
    )

    assert visual.load_image(screenshot.visual_id) == b"coding-screen"
    analysis = visual.load_analysis(screenshot.visual_id)
    assert analysis["session_id"] == "debug-session-1"
    loaded_experience = memory.load(experience.memory_id)
    assert loaded_experience["metadata"]["visual_ids"] == [screenshot.visual_id]
