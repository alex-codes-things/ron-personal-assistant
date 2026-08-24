from pathlib import Path

import pytest

from ron.storage import StorageManager, StorageQueueFullError, StorageState


def test_online_write_uses_external_storage(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    manager = StorageManager(project, locator=lambda: external)

    health = manager.refresh_once(sync=True)
    result = manager.save_bytes("Memory/Knowledge/test.txt", b"hello")

    assert health.state is StorageState.ONLINE
    assert result.queued is False
    assert (external / "Memory" / "Knowledge" / "test.txt").read_bytes() == b"hello"
    assert manager.pending_stats() == (0, 0)


def test_disconnect_queues_then_reconnect_syncs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    current: list[Path | None] = [None]
    manager = StorageManager(project, locator=lambda: current[0])

    assert manager.refresh_once().state is StorageState.DEGRADED
    queued = manager.save_bytes("Memory/Conversations/one.json", b"queued")
    assert queued.queued is True
    assert manager.pending_stats() == (1, 6)

    current[0] = external
    health = manager.refresh_once(sync=True)

    assert health.state is StorageState.ONLINE
    assert manager.pending_stats() == (0, 0)
    assert (external / "Memory" / "Conversations" / "one.json").read_bytes() == b"queued"


def test_queue_limit_protects_laptop(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manager = StorageManager(project, locator=lambda: None, queue_limit_bytes=5)
    manager.refresh_once()

    with pytest.raises(StorageQueueFullError):
        manager.save_bytes("Memory/Knowledge/too-big.bin", b"123456")


def test_bound_identity_rejects_different_drive(tmp_path: Path) -> None:
    project = tmp_path / "project"
    first = tmp_path / "first"
    second = tmp_path / "second"
    project.mkdir()
    first.mkdir()
    second.mkdir()
    current = [first]
    manager = StorageManager(project, locator=lambda: current[0])
    assert manager.refresh_once().state is StorageState.ONLINE

    # A separately initialized drive gets a different persistent ID.
    other_project = tmp_path / "other_project"
    other_project.mkdir()
    other = StorageManager(other_project, locator=lambda: second)
    assert other.refresh_once().state is StorageState.ONLINE

    current[0] = second
    health = manager.refresh_once()
    assert health.state is StorageState.ERROR
    assert health.external_root is None


def test_bound_storage_never_adopts_an_unmarked_replacement(tmp_path: Path) -> None:
    project = tmp_path / "project"
    original = tmp_path / "original"
    blank = tmp_path / "blank"
    project.mkdir()
    original.mkdir()
    blank.mkdir()
    current = [original]
    manager = StorageManager(project, locator=lambda: current[0])
    assert manager.refresh_once().state is StorageState.ONLINE

    current[0] = blank
    health = manager.refresh_once()

    assert health.state is StorageState.ERROR
    assert not (blank / ".ron-storage.json").exists()


def test_corrupt_local_binding_disables_automatic_rebinding(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    binding = project / "runtime" / "memory" / "core" / "storage_binding.json"
    binding.parent.mkdir(parents=True)
    binding.write_text("not-json", encoding="utf-8")
    manager = StorageManager(project, locator=lambda: external)

    health = manager.refresh_once()

    assert health.state is StorageState.ERROR
    assert not (external / ".ron-storage.json").exists()


def test_storage_rejects_path_traversal(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manager = StorageManager(project, locator=lambda: None)

    with pytest.raises(ValueError):
        manager.save_bytes("../outside.txt", b"nope")
    with pytest.raises(ValueError):
        manager.save_bytes("C:/outside.txt", b"nope")


def test_low_capacity_drive_is_not_bound(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()
    manager = StorageManager(project, locator=lambda: external)
    manager.external_reserve_bytes = 10
    monkeypatch.setattr(
        "ron.storage.manager.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=5),
    )

    health = manager.refresh_once()

    assert health.state is StorageState.ERROR
    assert not (external / ".ron-storage.json").exists()
    assert not manager.binding_path.exists()


def test_low_laptop_space_refuses_fallback_growth(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    project = tmp_path / "project"
    project.mkdir()
    manager = StorageManager(project, locator=lambda: None)
    manager.local_reserve_bytes = 10
    monkeypatch.setattr(
        "ron.storage.manager.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=5),
    )

    with pytest.raises(StorageQueueFullError, match="low on free space"):
        manager.save_bytes("Memory/Knowledge/test.json", b"data")


def test_startup_recovery_syncs_in_background(tmp_path: Path) -> None:
    import time

    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()

    offline = StorageManager(project, locator=lambda: None)
    offline.refresh_once()
    offline.save_bytes("Memory/Knowledge/background.json", b"recover-me")

    recovering = StorageManager(
        project,
        locator=lambda: external,
        check_interval_seconds=60,
    )
    recovering.start()
    try:
        deadline = time.monotonic() + 2.0
        destination = external / "Memory" / "Knowledge" / "background.json"
        while time.monotonic() < deadline:
            # The external file appears just before the queue entry is removed.
            # Wait until the entire recovery operation has completed.
            if destination.exists() and recovering.pending_stats() == (0, 0):
                break
            time.sleep(0.01)
        assert destination.read_bytes() == b"recover-me"
        assert recovering.pending_stats() == (0, 0)
    finally:
        recovering.stop()


def test_offline_deletion_is_queued_and_applied_after_reconnect(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()

    online = StorageManager(project, locator=lambda: external)
    online.refresh_once()
    online.save_bytes("Memory/Knowledge/delete-me.json", b"old-memory")
    assert (external / "Memory" / "Knowledge" / "delete-me.json").exists()

    offline = StorageManager(project, locator=lambda: None)
    offline.refresh_once()
    result = offline.delete("Memory/Knowledge/delete-me.json")

    assert result.queued is True
    assert offline.pending_stats() == (1, 0)

    reconnect = StorageManager(project, locator=lambda: external)
    reconnect.refresh_once()

    assert reconnect.pending_stats() == (0, 0)
    assert not (external / "Memory" / "Knowledge" / "delete-me.json").exists()


def test_new_write_cancels_pending_deletion_for_same_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    project.mkdir()
    external.mkdir()

    offline = StorageManager(project, locator=lambda: None)
    offline.refresh_once()
    offline.delete("Memory/Knowledge/reused.json")
    offline.save_bytes("Memory/Knowledge/reused.json", b"new-memory")

    assert offline.pending_stats() == (1, len(b"new-memory"))

    reconnect = StorageManager(project, locator=lambda: external)
    reconnect.refresh_once()

    assert (external / "Memory" / "Knowledge" / "reused.json").read_bytes() == b"new-memory"
