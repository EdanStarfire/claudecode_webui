"""
Tests for issue #938 — superseded by issue #952.

The _session_models lazy cache was removed in issue #952 when the context
usage source was migrated to ClaudeSDKClient.get_context_usage(). The SDK
now provides model and context window data directly, eliminating the need
for a per-session model cache.

See test_issue_952.py for the replacement tests.
"""
