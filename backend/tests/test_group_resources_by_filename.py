"""Unit tests for _group_resources_by_filename() helper (issue #1680)."""

import pytest

from backend.session_coordinator import _group_resources_by_filename


def test_empty_list():
    assert _group_resources_by_filename([]) == []


def test_single_entry_group_passes_through_unchanged():
    resources = [
        {"resource_id": "r1", "original_name": "alpha.py", "timestamp": 100},
    ]
    result = _group_resources_by_filename(resources)
    assert len(result) == 1
    assert result[0]["resource_id"] == "r1"
    assert result[0]["version_count"] == 1
    assert "versions" not in result[0]


def test_distinct_filenames_are_distinct_groups():
    resources = [
        {"resource_id": "r1", "original_name": "alpha.py", "timestamp": 100},
        {"resource_id": "r2", "original_name": "beta.png", "timestamp": 200},
    ]
    result = _group_resources_by_filename(resources)
    assert len(result) == 2
    names = {r["original_name"] for r in result}
    assert names == {"alpha.py", "beta.png"}
    assert all(r["version_count"] == 1 for r in result)


def test_same_filename_twice_forms_one_group():
    resources = [
        {"resource_id": "r1", "original_name": "report.md", "timestamp": 100},
        {"resource_id": "r2", "original_name": "report.md", "timestamp": 200},
    ]
    result = _group_resources_by_filename(resources)
    assert len(result) == 1
    group = result[0]
    assert group["version_count"] == 2
    # Latest entry represents the group
    assert group["resource_id"] == "r2"
    # versions newest-first
    assert [v["resource_id"] for v in group["versions"]] == ["r2", "r1"]
    assert [v["version_number"] for v in group["versions"]] == [2, 1]


def test_three_versions_oldest_to_newest_numbering():
    resources = [
        {"resource_id": "r1", "original_name": "report.md", "timestamp": 100},
        {"resource_id": "r2", "original_name": "report.md", "timestamp": 200},
        {"resource_id": "r3", "original_name": "report.md", "timestamp": 300},
    ]
    result = _group_resources_by_filename(resources)
    assert len(result) == 1
    group = result[0]
    assert group["version_count"] == 3
    assert group["resource_id"] == "r3"
    assert [v["resource_id"] for v in group["versions"]] == ["r3", "r2", "r1"]
    assert [v["version_number"] for v in group["versions"]] == [3, 2, 1]


def test_case_insensitive_grouping():
    resources = [
        {"resource_id": "r1", "original_name": "Screenshot.PNG", "timestamp": 100},
        {"resource_id": "r2", "original_name": "screenshot.png", "timestamp": 200},
    ]
    result = _group_resources_by_filename(resources)
    assert len(result) == 1
    assert result[0]["version_count"] == 2


def test_same_base_name_different_extension_not_grouped():
    resources = [
        {"resource_id": "r1", "original_name": "report.md", "timestamp": 100},
        {"resource_id": "r2", "original_name": "report.txt", "timestamp": 200},
    ]
    result = _group_resources_by_filename(resources)
    assert len(result) == 2
    assert all(r["version_count"] == 1 for r in result)


def test_does_not_mutate_input():
    resources = [
        {"resource_id": "r1", "original_name": "report.md", "timestamp": 100},
        {"resource_id": "r2", "original_name": "report.md", "timestamp": 200},
    ]
    original = [dict(r) for r in resources]
    _group_resources_by_filename(resources)
    assert resources == original


@pytest.mark.parametrize("unsorted_input", [True, False])
def test_group_representative_is_latest_regardless_of_input_order(unsorted_input):
    resources = [
        {"resource_id": "r1", "original_name": "report.md", "timestamp": 100},
        {"resource_id": "r3", "original_name": "report.md", "timestamp": 300},
        {"resource_id": "r2", "original_name": "report.md", "timestamp": 200},
    ]
    if unsorted_input:
        resources = list(reversed(resources))
    result = _group_resources_by_filename(resources)
    assert result[0]["resource_id"] == "r3"
