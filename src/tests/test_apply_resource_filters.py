"""Unit tests for _apply_resource_filters() helper (issue #991)."""

import pytest

from ..session_coordinator import _apply_resource_filters


# Fixture data
@pytest.fixture
def resources():
    return [
        {
            "title": "Alpha Script",
            "original_name": "alpha.py",
            "format": "py",
            "is_image": False,
            "timestamp": 100,
        },
        {
            "title": "Beta Image",
            "original_name": "beta.png",
            "format": "png",
            "is_image": True,
            "timestamp": 200,
        },
        {
            "title": "Gamma Readme",
            "original_name": "gamma.md",
            "format": "md",
            "is_image": False,
            "timestamp": 50,
        },
        {
            "title": None,
            "original_name": "delta.json",
            "format": "json",
            "is_image": False,
            "timestamp": 300,
        },
    ]


# --- Search ---

def test_search_by_title(resources):
    result = _apply_resource_filters(resources, search="alpha", format_filter=None, sort="newest")
    assert len(result) == 1
    assert result[0]["original_name"] == "alpha.py"


def test_search_by_original_name(resources):
    result = _apply_resource_filters(resources, search="delta", format_filter=None, sort="newest")
    assert len(result) == 1
    assert result[0]["original_name"] == "delta.json"


def test_search_case_insensitive(resources):
    result = _apply_resource_filters(resources, search="GAMMA", format_filter=None, sort="newest")
    assert len(result) == 1
    assert result[0]["original_name"] == "gamma.md"


def test_search_none_returns_all(resources):
    result = _apply_resource_filters(resources, search=None, format_filter=None, sort="newest")
    assert len(result) == 4


def test_search_no_match(resources):
    result = _apply_resource_filters(resources, search="zzz", format_filter=None, sort="newest")
    assert result == []


# --- Format filter ---

def test_format_filter_image(resources):
    result = _apply_resource_filters(resources, search=None, format_filter="image", sort="newest")
    assert all(r["is_image"] for r in result)
    assert len(result) == 1
    assert result[0]["original_name"] == "beta.png"


def test_format_filter_text(resources):
    result = _apply_resource_filters(resources, search=None, format_filter="text", sort="newest")
    formats = {r["format"] for r in result}
    assert formats <= {"py", "md", "json"}
    assert len(result) == 3


def test_format_filter_exact(resources):
    result = _apply_resource_filters(resources, search=None, format_filter="md", sort="newest")
    assert len(result) == 1
    assert result[0]["original_name"] == "gamma.md"


def test_format_filter_exact_no_match(resources):
    result = _apply_resource_filters(resources, search=None, format_filter="svg", sort="newest")
    assert result == []


# --- Sort modes ---

def test_sort_newest(resources):
    result = _apply_resource_filters(resources, search=None, format_filter=None, sort="newest")
    timestamps = [r["timestamp"] for r in result]
    assert timestamps == sorted(timestamps, reverse=True)


def test_sort_oldest(resources):
    result = _apply_resource_filters(resources, search=None, format_filter=None, sort="oldest")
    timestamps = [r["timestamp"] for r in result]
    assert timestamps == sorted(timestamps)


def test_sort_name_asc(resources):
    result = _apply_resource_filters(resources, search=None, format_filter=None, sort="name-asc")
    names = [(r.get("title") or r.get("original_name") or "").lower() for r in result]
    assert names == sorted(names)


def test_sort_name_desc(resources):
    result = _apply_resource_filters(resources, search=None, format_filter=None, sort="name-desc")
    names = [(r.get("title") or r.get("original_name") or "").lower() for r in result]
    assert names == sorted(names, reverse=True)


# --- Immutability ---

def test_does_not_mutate_input(resources):
    original_order = [r["original_name"] for r in resources]
    _apply_resource_filters(resources, search=None, format_filter=None, sort="name-asc")
    assert [r["original_name"] for r in resources] == original_order
