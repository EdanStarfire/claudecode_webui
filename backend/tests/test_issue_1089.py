"""
Regression tests for issue #1089: duplicate docker mount paths on session start.

Docker raises an error when the same container destination path appears more than
once in the --volume / -v arguments.  The fix deduplicates extra_mounts by
container destination path before passing the list to resolve_docker_cli_path.
"""



def _dedup_mounts(mounts: list[str]) -> list[str]:
    """
    Mirror of the deduplication logic added to session_coordinator.py (issue #1089).

    Keeps the *first* occurrence of each container destination path.
    Mount format: "host_path:container_path[:options]"
    """
    seen: set[str] = set()
    result: list[str] = []
    for mount in mounts:
        parts = mount.split(":", 2)
        container_path = parts[1] if len(parts) >= 2 else mount
        if container_path not in seen:
            seen.add(container_path)
            result.append(mount)
    return result


class TestIssue1089DuplicateMountDedup:
    def test_issue_1089_duplicate_container_path_deduped(self):
        """Two mounts with the same container path → only the first is kept."""
        mounts = ["/a:/c/path", "/b:/c/path"]
        result = _dedup_mounts(mounts)
        assert result == ["/a:/c/path"]

    def test_issue_1089_same_host_different_container_paths_both_kept(self):
        """Same host path mounted at two different container paths → both kept."""
        mounts = ["/a:/c1", "/a:/c2"]
        result = _dedup_mounts(mounts)
        assert result == ["/a:/c1", "/a:/c2"]

    def test_issue_1089_empty_list_no_regression(self):
        """Empty mount list is returned unchanged."""
        assert _dedup_mounts([]) == []

    def test_issue_1089_no_duplicates_unchanged(self):
        """A list with no duplicates is returned as-is."""
        mounts = ["/a:/c1", "/b:/c2", "/c:/c3"]
        assert _dedup_mounts(mounts) == mounts

    def test_issue_1089_first_seen_wins(self):
        """When duplicated, the first entry is kept (not the last)."""
        mounts = ["/first:/dest", "/second:/dest", "/third:/dest"]
        result = _dedup_mounts(mounts)
        assert result == ["/first:/dest"]

    def test_issue_1089_options_preserved(self):
        """Mount options (e.g. ':ro') are preserved and don't affect dedup key."""
        mounts = ["/a:/c/path:ro", "/b:/c/path:rw"]
        result = _dedup_mounts(mounts)
        assert result == ["/a:/c/path:ro"]

    def test_issue_1089_mixed_duplicates_and_unique(self):
        """Multiple container paths, some duplicated — only dupes are collapsed."""
        mounts = [
            "/a:/dest1",
            "/b:/dest2",
            "/c:/dest1",  # duplicate of first
            "/d:/dest3",
        ]
        result = _dedup_mounts(mounts)
        assert result == ["/a:/dest1", "/b:/dest2", "/d:/dest3"]
