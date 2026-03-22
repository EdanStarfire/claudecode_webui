"""Unit tests for src/template_variables.py (issue #917)."""

from pathlib import Path

import pytest

from src.template_variables import build_variables, resolve_path, resolve_path_list


@pytest.fixture
def vars_full():
    return build_variables(
        session_id="abc123-def456",
        session_dir=Path("/data/sessions/abc123-def456"),
        working_directory="/home/user/project",
    )


@pytest.fixture
def vars_no_workdir():
    return build_variables(
        session_id="abc123-def456",
        session_dir=Path("/data/sessions/abc123-def456"),
        working_directory=None,
    )


# ---------------------------------------------------------------------------
# resolve_path — basic cases
# ---------------------------------------------------------------------------


def test_resolve_path_none_input(vars_full):
    assert resolve_path(None, vars_full) is None


def test_resolve_path_no_tokens(vars_full):
    assert resolve_path("/absolute/path", vars_full) == "/absolute/path"


def test_resolve_path_session_data(vars_full):
    result = resolve_path("{session_data}/memory", vars_full)
    assert result == "/data/sessions/abc123-def456/memory"


def test_resolve_path_session_id(vars_full):
    result = resolve_path("{session_id}", vars_full)
    assert result == "abc123-def456"


def test_resolve_path_working_dir(vars_full):
    result = resolve_path("{working_dir}/cache", vars_full)
    assert result == "/home/user/project/cache"


def test_resolve_path_multiple_tokens(vars_full):
    result = resolve_path("{session_data}:{session_id}", vars_full)
    assert result == "/data/sessions/abc123-def456:abc123-def456"


def test_resolve_path_unknown_var_passthrough(vars_full, caplog):
    with caplog.at_level("WARNING"):
        result = resolve_path("{unknown_var}/path", vars_full)
    assert result == "{unknown_var}/path"
    assert "unknown variable" in caplog.text.lower() or "unknown" in caplog.text


def test_resolve_path_working_dir_none(vars_no_workdir, caplog):
    with caplog.at_level("WARNING"):
        result = resolve_path("{working_dir}/cache", vars_no_workdir)
    assert result == "{working_dir}/cache"
    assert "cannot be resolved" in caplog.text or "None" in caplog.text


def test_resolve_path_malformed_no_match(vars_full):
    # Lone brace — not matched by the \{(\w+)\} pattern
    assert resolve_path("{bad", vars_full) == "{bad"
    assert resolve_path("bad}", vars_full) == "bad}"


def test_resolve_path_mount_spec(vars_full):
    """Mount specs like host:container:opts must be handled; : separators don't interfere."""
    result = resolve_path("{session_data}/db:/app/db:rw", vars_full)
    assert result == "/data/sessions/abc123-def456/db:/app/db:rw"


# ---------------------------------------------------------------------------
# resolve_path_list
# ---------------------------------------------------------------------------


def test_resolve_path_list_none(vars_full):
    assert resolve_path_list(None, vars_full) is None


def test_resolve_path_list_empty(vars_full):
    assert resolve_path_list([], vars_full) == []


def test_resolve_path_list_mixed(vars_full):
    result = resolve_path_list(["{session_data}/a", "/b"], vars_full)
    assert result == ["/data/sessions/abc123-def456/a", "/b"]


def test_resolve_path_list_all_substituted(vars_full):
    result = resolve_path_list(
        ["{session_data}/x", "{session_id}/y", "{working_dir}/z"],
        vars_full,
    )
    assert result == [
        "/data/sessions/abc123-def456/x",
        "abc123-def456/y",
        "/home/user/project/z",
    ]


# ---------------------------------------------------------------------------
# build_variables
# ---------------------------------------------------------------------------


def test_build_variables_structure():
    v = build_variables("id-1", Path("/sessions/id-1"), "/work")
    assert v["session_id"] == "id-1"
    assert v["session_data"] == "/sessions/id-1"
    assert v["working_dir"] == "/work"


def test_build_variables_none_workdir():
    v = build_variables("id-1", Path("/sessions/id-1"), None)
    assert v["working_dir"] is None
