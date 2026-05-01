"""
Tests for archive_manager helpers — covering issue #1244.

Step 1: scrub_state_for_archive()
Step 2+: snapshot_artifacts(), reset cleanup helpers (added in later steps)
"""

from src.legion.archive_manager import scrub_state_for_archive


# ---------------------------------------------------------------------------
# scrub_state_for_archive tests
# ---------------------------------------------------------------------------


class TestScrubStateForArchive:
    def test_drops_secret_fetch_token(self):
        state = {"session_id": "abc", "secret_fetch_token": "tok-live", "name": "Agent"}
        result = scrub_state_for_archive(state)
        assert "secret_fetch_token" not in result
        assert result["session_id"] == "abc"
        assert result["name"] == "Agent"

    def test_keeps_secret_placeholders(self):
        state = {"secret_placeholders": {"API_KEY": "placeholder"}, "secret_fetch_token": "live"}
        result = scrub_state_for_archive(state)
        assert result["secret_placeholders"] == {"API_KEY": "placeholder"}
        assert "secret_fetch_token" not in result

    def test_drops_runtime_keys(self):
        state = {
            "session_id": "s1",
            "is_processing": True,
            "latest_message": "hi",
            "latest_message_type": "user",
            "latest_message_time": 9999,
            "claude_code_session_id": "ccs-123",
        }
        result = scrub_state_for_archive(state)
        assert "is_processing" not in result
        assert "latest_message" not in result
        assert "latest_message_type" not in result
        assert "latest_message_time" not in result
        assert "claude_code_session_id" not in result
        assert result["session_id"] == "s1"

    def test_truncates_error_message_long(self):
        long_err = "x" * 300
        state = {"error_message": long_err}
        result = scrub_state_for_archive(state)
        assert "error_message" not in result
        assert "error_summary" in result
        assert len(result["error_summary"]) == 201  # 200 chars + "…"

    def test_keeps_short_error_message_verbatim(self):
        state = {"error_message": "short error"}
        result = scrub_state_for_archive(state)
        assert result["error_summary"] == "short error"

    def test_no_error_key_when_none(self):
        state = {"session_id": "s1"}
        result = scrub_state_for_archive(state)
        assert "error_message" not in result
        assert "error_summary" not in result

    def test_original_state_unmodified(self):
        state = {"secret_fetch_token": "tok", "session_id": "s1"}
        scrub_state_for_archive(state)
        assert "secret_fetch_token" in state
