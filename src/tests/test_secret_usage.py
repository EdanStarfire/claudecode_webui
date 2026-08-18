"""Tests for src/secret_usage.py — issue #1772."""

from types import SimpleNamespace

from src.secret_usage import compute_secret_usage


def make_secret(name, secret_type="generic", refresh=None):
    return {"name": name, "type": secret_type, "refresh": refresh}


def make_session(assigned_secrets):
    return SimpleNamespace(config={"assigned_secrets": assigned_secrets})


def make_template(assigned_secrets):
    return SimpleNamespace(config={"assigned_secrets": assigned_secrets})


def make_profile(assigned_secrets, area="isolation"):
    return SimpleNamespace(area=area, config={"assigned_secrets": assigned_secrets})


def make_mcp_config(*, env=None, headers=None, url=None, command=None, args=None):
    return SimpleNamespace(
        env=env or {}, headers=headers or {}, url=url, command=command, args=args or []
    )


def test_secret_assigned_to_multiple_sessions():
    secrets = [make_secret("github-token")]
    sessions = [make_session(["github-token"]), make_session(["github-token"]), make_session([])]
    usage = compute_secret_usage(secrets, sessions, [], [], [])
    assert usage["github-token"]["sessions"] == 2
    assert usage["github-token"]["total"] == 2


def test_usage_across_session_template_profile():
    secrets = [make_secret("api-key")]
    sessions = [make_session(["api-key"])]
    templates = [make_template(["api-key"])]
    profiles = [make_profile(["api-key"])]
    usage = compute_secret_usage(secrets, sessions, templates, profiles, [])
    entry = usage["api-key"]
    assert entry["sessions"] == 1
    assert entry["templates"] == 1
    assert entry["profiles"] == 1
    assert entry["total"] == 3


def test_profile_wrong_area_not_counted():
    secrets = [make_secret("api-key")]
    profiles = [make_profile(["api-key"], area="model")]
    usage = compute_secret_usage(secrets, [], [], profiles, [])
    assert usage["api-key"]["profiles"] == 0
    assert usage["api-key"]["total"] == 0


def test_mcp_ref_detected_in_header_env_url_args_dedup_within_config():
    secrets = [make_secret("mcp-secret")]
    mcp_configs = [
        make_mcp_config(
            headers={"Authorization": "Bearer ${secret:mcp-secret}"},
            env={"TOKEN": "${secret:mcp-secret}"},
            url="https://example.com?key=${secret:mcp-secret}",
            args=["--token", "${secret:mcp-secret}"],
        )
    ]
    usage = compute_secret_usage(secrets, [], [], [], mcp_configs)
    # Same secret referenced 4 times in one config counts once.
    assert usage["mcp-secret"]["mcp_servers"] == 1
    assert usage["mcp-secret"]["total"] == 1


def test_mcp_ref_counted_once_per_config_across_multiple_configs():
    secrets = [make_secret("mcp-secret")]
    mcp_configs = [
        make_mcp_config(env={"TOKEN": "${secret:mcp-secret}"}),
        make_mcp_config(env={"TOKEN": "${secret:mcp-secret}"}),
    ]
    usage = compute_secret_usage(secrets, [], [], [], mcp_configs)
    assert usage["mcp-secret"]["mcp_servers"] == 2


def test_oauth2_dependents_show_up_on_sibling_generic_secrets():
    secrets = [
        make_secret(
            "github-oauth",
            secret_type="oauth2",
            refresh={
                "refresh_token_secret_name": "github-refresh-token",
                "client_secret_secret_name": "github-client-secret",
            },
        ),
        make_secret("github-refresh-token"),
        make_secret("github-client-secret"),
    ]
    usage = compute_secret_usage(secrets, [], [], [], [])
    assert usage["github-refresh-token"]["oauth2_dependents"] == ["github-oauth"]
    assert usage["github-refresh-token"]["total"] == 1
    assert usage["github-client-secret"]["oauth2_dependents"] == ["github-oauth"]
    assert usage["github-client-secret"]["total"] == 1
    # The oauth2 secret itself has no assignments anywhere — the issue's exact scenario.
    assert usage["github-oauth"]["total"] == 0


def test_two_oauth2_secrets_sharing_one_client_secret_sibling():
    secrets = [
        make_secret(
            "oauth-a",
            secret_type="oauth2",
            refresh={
                "refresh_token_secret_name": "refresh-a",
                "client_secret_secret_name": "shared-client-secret",
            },
        ),
        make_secret(
            "oauth-b",
            secret_type="oauth2",
            refresh={
                "refresh_token_secret_name": "refresh-b",
                "client_secret_secret_name": "shared-client-secret",
            },
        ),
        make_secret("refresh-a"),
        make_secret("refresh-b"),
        make_secret("shared-client-secret"),
    ]
    usage = compute_secret_usage(secrets, [], [], [], [])
    assert set(usage["shared-client-secret"]["oauth2_dependents"]) == {"oauth-a", "oauth-b"}
    assert usage["shared-client-secret"]["total"] == 2


def test_case_insensitive_name_matching():
    secrets = [make_secret("mysecret")]
    sessions = [make_session(["MySecret"])]
    usage = compute_secret_usage(secrets, sessions, [], [], [])
    assert usage["mysecret"]["sessions"] == 1


def test_zero_usage_secret():
    secrets = [make_secret("unused-secret")]
    usage = compute_secret_usage(secrets, [], [], [], [])
    entry = usage["unused-secret"]
    assert entry["sessions"] == 0
    assert entry["templates"] == 0
    assert entry["profiles"] == 0
    assert entry["mcp_servers"] == 0
    assert entry["oauth2_dependents"] == []
    assert entry["total"] == 0
