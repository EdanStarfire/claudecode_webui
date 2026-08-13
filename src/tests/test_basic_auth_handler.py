"""Unit tests for BasicAuthHandler.inject() (issue #1740)."""

import base64

import pytest

from src.secret_types.basic_auth import BasicAuthHandler


class _FakeHeaders(dict):
    def items(self):
        return list(super().items())


class _FakeRequest:
    def __init__(self, headers: dict):
        self.headers = _FakeHeaders(headers)


class _FakeFlow:
    def __init__(self, headers: dict):
        self.request = _FakeRequest(headers)


@pytest.mark.parametrize(
    ("username", "password", "expect_result", "expect_credentials"),
    [
        pytest.param("user", "", True, b"user:", id="username_only"),
        pytest.param("", "pass", True, b":pass", id="password_only_regression_guard"),
        pytest.param("user", "pass", True, b"user:pass", id="both_regression_guard"),
        pytest.param("", "", False, None, id="neither"),
    ],
)
def test_basic_auth_handler_inject(username, password, expect_result, expect_credentials):
    """Header is set whenever username or password is present; untouched when both are empty."""
    placeholder = "CC_SECRET_basic_auth_abcd1234"
    flow = _FakeFlow({"Authorization": placeholder})
    record = {"username": username, "value": password}
    result = BasicAuthHandler().inject(flow, record, placeholder)
    assert result is expect_result
    if expect_result:
        expected = "Basic " + base64.b64encode(expect_credentials).decode()
        assert flow.request.headers["Authorization"] == expected
    else:
        assert flow.request.headers["Authorization"] == placeholder
