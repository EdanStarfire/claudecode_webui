"""
Tests for issue #944 — superseded by issue #952.

The halving workaround (input_tokens / 2) was removed in issue #952 when
the context usage source was migrated to ClaudeSDKClient.get_context_usage().
The SDK now returns authoritative percentage data directly, eliminating both
the double-counting bug and the workaround.

See test_issue_952.py for the replacement tests.
"""
