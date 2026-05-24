"""Tests for video resource support in ResourceMCPTools (issue #1546)."""

import pytest

from ..mcp.resource_mcp_tools import MIME_TYPES, VIDEO_EXTENSIONS, ResourceMCPTools

# Minimal WebM bytes: EBML header magic + padding
VALID_WEBM_BYTES = b'\x1a\x45\xdf\xa3' + b'\x00' * 100

# Minimal MP4 bytes: ftyp box at offset 4
VALID_MP4_BYTES = b'\x00\x00\x00\x18' + b'ftyp' + b'isom' + b'\x00' * 100

# Invalid bytes (text file renamed to .mp4)
INVALID_VIDEO_BYTES = b'This is not a video file.\n' + b'x' * 100


# ---- Constants ----

def test_video_extensions_constant():
    assert '.webm' in VIDEO_EXTENSIONS
    assert '.mp4' in VIDEO_EXTENSIONS


def test_video_mime_types():
    assert MIME_TYPES['.webm'] == 'video/webm'
    assert MIME_TYPES['.mp4'] == 'video/mp4'


# ---- _detect_video_format ----

class TestDetectVideoFormat:
    def setup_method(self):
        # ResourceMCPTools needs session_coordinator but _detect_video_format doesn't use it
        self.tools = ResourceMCPTools.__new__(ResourceMCPTools)

    def test_webm_magic_bytes(self):
        result = self.tools._detect_video_format(VALID_WEBM_BYTES)
        assert result == "webm"

    def test_mp4_ftyp_box(self):
        result = self.tools._detect_video_format(VALID_MP4_BYTES)
        assert result == "mp4"

    def test_invalid_bytes_returns_none(self):
        result = self.tools._detect_video_format(INVALID_VIDEO_BYTES)
        assert result is None

    def test_too_short_bytes_returns_none(self):
        result = self.tools._detect_video_format(b'\x1a\x45')
        assert result is None

    def test_empty_bytes_returns_none(self):
        result = self.tools._detect_video_format(b'')
        assert result is None


# ---- _handle_register_resource integration (via mocked storage) ----

class FakeStorageManager:
    def __init__(self):
        self.saved_files = {}
        self.appended_resources = []

    async def save_resource_file(self, resource_id, file_bytes):
        self.saved_files[resource_id] = file_bytes

    async def append_resource(self, metadata):
        self.appended_resources.append(metadata)


class FakeSessionCoordinator:
    def __init__(self, storage):
        self._active_sdks = {}
        self._storage = storage
        self.session_manager = self

    async def get_session_info(self, session_id):
        return {"session_id": session_id}


@pytest.fixture
def storage():
    return FakeStorageManager()


@pytest.fixture
def tools(tmp_path, storage):
    coordinator = FakeSessionCoordinator(storage)
    t = ResourceMCPTools.__new__(ResourceMCPTools)
    t.session_coordinator = coordinator
    t.broadcast_callback = None
    # Monkeypatch _get_storage_manager to return our fake
    async def _get_storage_manager(session_id):
        return storage
    t._get_storage_manager = _get_storage_manager
    return t


@pytest.mark.asyncio
async def test_register_valid_webm(tmp_path, tools, storage):
    webm_file = tmp_path / "recording.webm"
    webm_file.write_bytes(VALID_WEBM_BYTES)

    result = await tools._handle_register_resource("sess1", {"file_path": str(webm_file)})

    assert not result["is_error"]
    assert storage.appended_resources
    meta = storage.appended_resources[0]
    assert meta["is_video"] is True
    assert meta["is_image"] is False
    assert meta["mime_type"] == "video/webm"
    assert meta["format"] == "webm"
    assert "Markdown" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_register_valid_mp4(tmp_path, tools, storage):
    mp4_file = tmp_path / "video.mp4"
    mp4_file.write_bytes(VALID_MP4_BYTES)

    result = await tools._handle_register_resource("sess1", {"file_path": str(mp4_file)})

    assert not result["is_error"]
    meta = storage.appended_resources[0]
    assert meta["is_video"] is True
    assert meta["mime_type"] == "video/mp4"
    assert meta["format"] == "mp4"


@pytest.mark.asyncio
async def test_register_invalid_video_bytes_rejected(tmp_path, tools):
    bad_file = tmp_path / "fake.mp4"
    bad_file.write_bytes(INVALID_VIDEO_BYTES)

    result = await tools._handle_register_resource("sess1", {"file_path": str(bad_file)})

    assert result["is_error"]
    assert "valid video" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_register_oversized_video_rejected(tmp_path, tools):
    big_file = tmp_path / "big.mp4"
    # 11 MB — just over the 10 MB limit
    big_file.write_bytes(b'x' * (11 * 1024 * 1024))

    result = await tools._handle_register_resource("sess1", {"file_path": str(big_file)})

    assert result["is_error"]
    assert "too large" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_register_mov_rejected(tmp_path, tools):
    mov_file = tmp_path / "video.mov"
    mov_file.write_bytes(VALID_MP4_BYTES)

    result = await tools._handle_register_resource("sess1", {"file_path": str(mov_file)})

    assert result["is_error"]
    assert "Unsupported file extension" in result["content"][0]["text"]
