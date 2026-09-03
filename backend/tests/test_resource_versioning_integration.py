"""Integration tests for resource versioning/grouping wiring (issue #1680).

Covers SessionCoordinator.get_session_resources()/get_session_resource_by_id()
end-to-end against a real DataStorageManager, including delete-promotes-next-version
and the byte-serving by-ID lookup bypassing grouping. Also covers the symmetric
archive-side path (get_archive_resources()/get_archive_resource_by_id()).
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.data_storage import DataStorageManager
from backend.legion.archive_manager import ArchiveManager
from backend.session_coordinator import SessionCoordinator


@pytest.fixture
def coordinator_and_session():
    tmp = tempfile.TemporaryDirectory()
    data_dir = Path(tmp.name)
    coord = SessionCoordinator(data_dir=data_dir)
    session_id = "sess-versioning"
    yield coord, session_id, data_dir
    tmp.cleanup()


async def _make_storage(coord, session_id, data_dir):
    session_dir = data_dir / "sessions" / session_id
    storage = DataStorageManager(session_dir)
    await storage.initialize()
    coord._storage_managers[session_id] = storage
    return storage


async def _register(storage, resource_id, original_name, timestamp, **extra):
    meta = {
        "resource_id": resource_id,
        "original_name": original_name,
        "title": original_name,
        "format": original_name.rsplit(".", 1)[-1],
        "mime_type": "text/plain",
        "is_image": False,
        "is_video": False,
        "size_bytes": 10,
        "timestamp": timestamp,
        **extra,
    }
    await storage.append_resource(meta)
    await storage.save_resource_file(resource_id, b"x" * 10)
    return meta


@pytest.mark.asyncio
async def test_get_session_resources_groups_same_filename(coordinator_and_session):
    coord, session_id, data_dir = coordinator_and_session
    storage = await _make_storage(coord, session_id, data_dir)

    await _register(storage, "r1", "report.md", 100)
    await _register(storage, "r2", "report.md", 200)
    await _register(storage, "r3", "other.md", 150)

    result = await coord.get_session_resources(session_id)

    assert result["total"] == 2
    by_name = {r["original_name"]: r for r in result["resources"]}
    assert by_name["report.md"]["version_count"] == 2
    assert by_name["report.md"]["resource_id"] == "r2"
    assert by_name["other.md"]["version_count"] == 1
    assert "versions" not in by_name["other.md"]


@pytest.mark.asyncio
async def test_get_session_resources_distinct_filenames_ungrouped(coordinator_and_session):
    coord, session_id, data_dir = coordinator_and_session
    storage = await _make_storage(coord, session_id, data_dir)

    await _register(storage, "r1", "a.txt", 100)
    await _register(storage, "r2", "b.txt", 200)

    result = await coord.get_session_resources(session_id)

    assert result["total"] == 2


@pytest.mark.asyncio
async def test_get_session_resource_by_id_resolves_older_version(coordinator_and_session):
    coord, session_id, data_dir = coordinator_and_session
    storage = await _make_storage(coord, session_id, data_dir)

    await _register(storage, "r1", "report.md", 100)
    await _register(storage, "r2", "report.md", 200)

    # Grouped list view only exposes the latest as a top-level resource_id...
    result = await coord.get_session_resources(session_id)
    assert result["resources"][0]["resource_id"] == "r2"

    # ...but the by-ID lookup used for byte-serving must still resolve the older version.
    older = await coord.get_session_resource_by_id(session_id, "r1")
    assert older is not None
    assert older["resource_id"] == "r1"

    newer = await coord.get_session_resource_by_id(session_id, "r2")
    assert newer is not None
    assert newer["resource_id"] == "r2"


@pytest.mark.asyncio
async def test_delete_latest_version_promotes_next_newest(coordinator_and_session):
    coord, session_id, data_dir = coordinator_and_session
    storage = await _make_storage(coord, session_id, data_dir)

    await _register(storage, "r1", "report.md", 100)
    await _register(storage, "r2", "report.md", 200)
    await _register(storage, "r3", "report.md", 300)

    result = await coord.get_session_resources(session_id)
    assert result["resources"][0]["resource_id"] == "r3"
    assert result["resources"][0]["version_count"] == 3

    # Soft-delete the current latest version.
    await storage.remove_resource_from_display("r3")

    result = await coord.get_session_resources(session_id)
    assert result["resources"][0]["resource_id"] == "r2"
    assert result["resources"][0]["version_count"] == 2
    assert [v["resource_id"] for v in result["resources"][0]["versions"]] == ["r2", "r1"]


@pytest.mark.asyncio
async def test_get_session_resources_no_storage_manager_returns_empty(coordinator_and_session):
    coord, session_id, data_dir = coordinator_and_session
    result = await coord.get_session_resources(session_id)
    assert result["total"] == 0
    assert result["resources"] == []


# ---------------------------------------------------------------------------
# Archive-side grouping/versioning (symmetric with the live-session path above)
# ---------------------------------------------------------------------------

@pytest.fixture
def coordinator_with_archive():
    tmp = tempfile.TemporaryDirectory()
    data_dir = Path(tmp.name)
    (data_dir / "archives" / "minions").mkdir(parents=True)

    system = Mock()
    system.session_coordinator = Mock()
    system.session_coordinator.session_manager = Mock()
    system.session_coordinator.session_manager.data_dir = data_dir
    archive_manager = ArchiveManager(system)

    coord = SessionCoordinator(data_dir=data_dir)
    coord.legion_system = Mock(archive_manager=archive_manager)

    session_id = "sess-archived"
    archive_id = "20260101-000000"
    yield coord, session_id, archive_id, data_dir
    tmp.cleanup()


def _write_archive_resources(data_dir, session_id, archive_id, entries):
    resources_dir = data_dir / "archives" / "minions" / session_id / archive_id / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    with open(resources_dir / "resources.jsonl", "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    for entry in entries:
        (resources_dir / f"{entry['resource_id']}.bin").write_bytes(b"x" * 10)


def _archive_entry(resource_id, original_name, timestamp):
    return {
        "resource_id": resource_id,
        "original_name": original_name,
        "title": original_name,
        "format": original_name.rsplit(".", 1)[-1],
        "mime_type": "text/plain",
        "is_image": False,
        "is_video": False,
        "size_bytes": 10,
        "timestamp": timestamp,
    }


@pytest.mark.asyncio
async def test_get_archive_resources_groups_same_filename(coordinator_with_archive):
    coord, session_id, archive_id, data_dir = coordinator_with_archive
    _write_archive_resources(data_dir, session_id, archive_id, [
        _archive_entry("r1", "report.md", 100),
        _archive_entry("r2", "report.md", 200),
        _archive_entry("r3", "other.md", 150),
    ])

    result = await coord.get_archive_resources(session_id, archive_id)

    assert result["total"] == 2
    by_name = {r["original_name"]: r for r in result["resources"]}
    assert by_name["report.md"]["version_count"] == 2
    assert by_name["report.md"]["resource_id"] == "r2"
    assert by_name["other.md"]["version_count"] == 1


@pytest.mark.asyncio
async def test_get_archive_resource_by_id_resolves_older_version(coordinator_with_archive):
    coord, session_id, archive_id, data_dir = coordinator_with_archive
    _write_archive_resources(data_dir, session_id, archive_id, [
        _archive_entry("r1", "report.md", 100),
        _archive_entry("r2", "report.md", 200),
    ])

    # Grouped list view only exposes the latest as a top-level resource_id...
    result = await coord.get_archive_resources(session_id, archive_id)
    assert result["resources"][0]["resource_id"] == "r2"

    # ...but the by-ID lookup used for archive byte-serving must still resolve the older version.
    older = await coord.get_archive_resource_by_id(session_id, archive_id, "r1")
    assert older is not None
    assert older["resource_id"] == "r1"


@pytest.mark.asyncio
async def test_get_archive_resources_no_legion_system_returns_empty():
    tmp = tempfile.TemporaryDirectory()
    try:
        coord = SessionCoordinator(data_dir=Path(tmp.name))
        result = await coord.get_archive_resources("sess-x", "archive-x")
        assert result["total"] == 0
        assert result["resources"] == []

        assert await coord.get_archive_resource_by_id("sess-x", "archive-x", "r1") is None
    finally:
        tmp.cleanup()
