"""Tests for minion name slugification (issue #546)."""

import json
from datetime import UTC, datetime

from ..session_manager import SessionInfo, SessionState, slugify_name


class TestSlugifyName:
    """Test slugify_name() utility function."""

    def test_basic_space_conversion(self):
        assert slugify_name("Database Optimizer") == "database-optimizer"

    def test_already_slugified(self):
        assert slugify_name("frontend-dev") == "frontend-dev"

    def test_underscore_conversion(self):
        assert slugify_name("my_minion_name") == "my-minion-name"

    def test_mixed_case(self):
        assert slugify_name("MyMinion") == "myminion"

    def test_special_characters_stripped(self):
        assert slugify_name("hello!@#world") == "helloworld"

    def test_multiple_spaces(self):
        assert slugify_name("too   many  spaces") == "too-many-spaces"

    def test_leading_trailing_spaces(self):
        assert slugify_name("  padded  ") == "padded"

    def test_leading_trailing_hyphens(self):
        assert slugify_name("-dashed-") == "dashed"

    def test_empty_string(self):
        assert slugify_name("") == ""

    def test_only_special_chars(self):
        assert slugify_name("!@#$%") == ""

    def test_numbers_preserved(self):
        assert slugify_name("worker-42") == "worker-42"

    def test_unicode_stripped(self):
        assert slugify_name("café-worker") == "caf-worker"

    def test_consecutive_hyphens_collapsed(self):
        assert slugify_name("a--b---c") == "a-b-c"

    def test_mixed_separators(self):
        assert slugify_name("hello_world test") == "hello-world-test"

    def test_collision_detection_same_slug(self):
        """Two different display names producing the same slug."""
        assert slugify_name("DB--Opt") == slugify_name("DB Opt")

    def test_idempotent(self):
        """Slugifying an already-slugified name returns the same result."""
        name = "database-optimizer"
        assert slugify_name(name) == name
        assert slugify_name(slugify_name(name)) == name


class TestSessionInfoSlug:
    """Test slug field on SessionInfo."""

    def test_to_dict_includes_slug(self):
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-1",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            name="Database Optimizer",
            slug="database-optimizer",
        )
        data = info.to_dict()
        assert data["slug"] == "database-optimizer"

    def test_from_dict_with_slug(self):
        now = datetime.now(UTC)
        data = {
            "session_id": "test-1",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "name": "Database Optimizer",
            "slug": "database-optimizer",
        }
        info = SessionInfo.from_dict(data)
        assert info.slug == "database-optimizer"

    def test_from_dict_without_slug_derives_from_name(self):
        """Backward compatibility: derive slug from name when missing."""
        now = datetime.now(UTC)
        data = {
            "session_id": "test-1",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "name": "My Worker",
        }
        info = SessionInfo.from_dict(data)
        assert info.slug == "my-worker"

    def test_from_dict_without_name_no_slug(self):
        """No name means no slug derivation."""
        now = datetime.now(UTC)
        data = {
            "session_id": "test-1",
            "state": "created",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        info = SessionInfo.from_dict(data)
        assert info.slug is None

    def test_roundtrip_serialization(self):
        now = datetime.now(UTC)
        info = SessionInfo(
            session_id="test-1",
            state=SessionState.CREATED,
            created_at=now,
            updated_at=now,
            name="Test Worker",
            slug="test-worker",
        )
        data = info.to_dict()
        json_str = json.dumps(data)
        restored = SessionInfo.from_dict(json.loads(json_str))
        assert restored.slug == "test-worker"
        assert restored.name == "Test Worker"
