"""Issue #2283: sandbox ignoreViolations normalization."""

from backend.claude_sdk import _normalize_ignore_violations as norm


def test_new_shape_passthrough():
    assert norm({"*": ["a", "b"], "npm": ["c"]}) == {"*": ["a", "b"], "npm": ["c"]}


def test_legacy_file_network_migrated_to_star():
    assert norm({"file": "/etc/hosts, /tmp", "network": "x.com"}) == {
        "*": ["/etc/hosts", "/tmp", "x.com"]
    }


def test_legacy_lists_migrated():
    assert norm({"file": ["a"]}) == {"*": ["a"]}


def test_comma_string_values_split():
    assert norm({"git": "a, b,,"}) == {"git": ["a", "b"]}


def test_malformed_and_empty_dropped():
    assert norm({"git": 5, "": ["x"], "ls": []}) == {}
    assert norm("nope") == {}
    assert norm({"file": ""}) == {}


def test_file_mixed_with_real_command_not_treated_as_legacy():
    assert norm({"file": ["a"], "npm": ["b"]}) == {"file": ["a"], "npm": ["b"]}
