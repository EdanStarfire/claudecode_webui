"""Unit tests for src/secret_usage.py (issue #1772)."""

from types import SimpleNamespace

from backend.secret_usage import compute_secret_usage


def _secret(name: str, secret_type: str = "generic", refresh: dict | None = None) -> dict:
    return {"name": name, "type": secret_type, "refresh": refresh}


def _config_holder(assigned_secrets: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(config={"assigned_secrets": assigned_secrets} if assigned_secrets else {})


def _mcp_config(
    env: dict | None = None,
    headers: dict | None = None,
    url: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        env=env or {},
        headers=headers or {},
        url=url,
        command=command,
        args=args or [],
    )


def test_secret_assigned_to_multiple_sessions_counts_n():
    secrets = [_secret("github-token")]
    sessions = [
        _config_holder(["github-token"]),
        _config_holder(["github-token"]),
        _config_holder(["other-secret"]),
    ]
    usage = compute_secret_usage(secrets, sessions, [], [], [])
    assert usage["github-token"]["sessions"] == 2
    assert usage["github-token"]["total"] == 2


def test_secret_assigned_across_session_template_profile():
    secrets = [_secret("shared-secret")]
    sessions = [_config_holder(["shared-secret"])]
    templates = [_config_holder(["shared-secret"])]
    profiles = [_config_holder(["shared-secret"])]
    usage = compute_secret_usage(secrets, sessions, templates, profiles, [])
    entry = usage["shared-secret"]
    assert entry["sessions"] == 1
    assert entry["templates"] == 1
    assert entry["profiles"] == 1
    assert entry["mcp_servers"] == 0
    assert entry["total"] == 3


def test_mcp_config_refs_detected_in_all_fields():
    secrets = [_secret("api-key")]
    mcp_configs = [
        _mcp_config(env={"E": "${secret:api-key}"}),
        _mcp_config(headers={"H": "Bearer ${secret:api-key}"}),
        _mcp_config(url="https://example.com/${secret:api-key}"),
        _mcp_config(command="run --token=${secret:api-key}"),
        _mcp_config(args=["--key", "${secret:api-key}"]),
    ]
    usage = compute_secret_usage(secrets, [], [], [], mcp_configs)
    assert usage["api-key"]["mcp_servers"] == 5
    assert usage["api-key"]["total"] == 5


def test_mcp_config_same_secret_referenced_twice_counts_once():
    secrets = [_secret("dup-secret")]
    mcp_configs = [
        _mcp_config(
            headers={"H1": "${secret:dup-secret}", "H2": "${secret:dup-secret}"},
            env={"E": "${secret:dup-secret}"},
        ),
    ]
    usage = compute_secret_usage(secrets, [], [], [], mcp_configs)
    assert usage["dup-secret"]["mcp_servers"] == 1


def test_oauth2_dependents_appear_with_empty_assigned_secrets_everywhere():
    secrets = [
        _secret("client-secret-sibling"),
        _secret("refresh-token-sibling"),
        _secret(
            "github-oauth",
            secret_type="oauth2",
            refresh={
                "client_secret_secret_name": "client-secret-sibling",
                "refresh_token_secret_name": "refresh-token-sibling",
            },
        ),
    ]
    usage = compute_secret_usage(secrets, [], [], [], [])
    assert usage["client-secret-sibling"]["oauth2_dependents"] == ["github-oauth"]
    assert usage["client-secret-sibling"]["total"] == 1
    assert usage["refresh-token-sibling"]["oauth2_dependents"] == ["github-oauth"]
    assert usage["refresh-token-sibling"]["total"] == 1
    assert usage["github-oauth"]["total"] == 0


def test_two_oauth2_secrets_sharing_client_secret_sibling():
    secrets = [
        _secret("shared-client-secret"),
        _secret(
            "oauth-a",
            secret_type="oauth2",
            refresh={
                "refresh_token_secret_name": "refresh-a",
                "client_secret_secret_name": "shared-client-secret",
            },
        ),
        _secret(
            "oauth-b",
            secret_type="oauth2",
            refresh={
                "refresh_token_secret_name": "refresh-b",
                "client_secret_secret_name": "shared-client-secret",
            },
        ),
        _secret("refresh-a"),
        _secret("refresh-b"),
    ]
    usage = compute_secret_usage(secrets, [], [], [], [])
    assert set(usage["shared-client-secret"]["oauth2_dependents"]) == {"oauth-a", "oauth-b"}
    assert usage["shared-client-secret"]["total"] == 2


def test_issue_1772_direct_assignment_only_no_inheritance_cascade():
    """Locks in confirmed design decision #3 from the plan: each session/
    template/profile's own config["assigned_secrets"] is counted
    independently — there is no cascade through profile_ids/template
    inheritance. compute_secret_usage() never receives inheritance-linkage
    data (only flat .config dicts), so it structurally cannot cascade;
    this test names the scenario explicitly so the semantics aren't
    mistaken for a bug by a future reader.

    Profile P has the secret directly assigned (profiles=1). Two templates
    conceptually "inherit" from P (no profile_ids wiring is passed here —
    they simply have no assigned_secrets entry of their own) and must NOT
    be counted. A third template has its own direct assignment (templates=1).
    Two sessions have their own direct assignment (sessions=2).
    """
    secrets = [_secret("shared-secret")]
    profiles = [_config_holder(["shared-secret"])]
    templates = [
        _config_holder(),  # "inherits" from the profile — contributes 0
        _config_holder(),  # "inherits" from the profile — contributes 0
        _config_holder(["shared-secret"]),  # direct assignment — contributes 1
    ]
    sessions = [
        _config_holder(["shared-secret"]),
        _config_holder(["shared-secret"]),
    ]
    usage = compute_secret_usage(secrets, sessions, templates, profiles, [])
    entry = usage["shared-secret"]
    assert entry["sessions"] == 2
    assert entry["templates"] == 1
    assert entry["profiles"] == 1
    assert entry["mcp_servers"] == 0
    assert entry["oauth2_dependents"] == []
    assert entry["total"] == 4


def test_issue_1772_comma_separated_assigned_secrets_string_not_iterated_as_chars():
    """Profile/template config may store assigned_secrets as a comma-separated
    string rather than a list (see _LIST_FIELDS/_coerce_list in
    config_resolution.py) — must not be iterated character by character."""
    secrets = [_secret("secret-a"), _secret("secret-b")]
    profiles = [SimpleNamespace(config={"assigned_secrets": "secret-a, secret-b"})]
    usage = compute_secret_usage(secrets, [], [], profiles, [])
    assert usage["secret-a"]["profiles"] == 1
    assert usage["secret-b"]["profiles"] == 1


def test_case_insensitive_name_matching():
    secrets = [_secret("mysecret")]
    sessions = [_config_holder(["MySecret"])]
    usage = compute_secret_usage(secrets, sessions, [], [], [])
    assert usage["mysecret"]["sessions"] == 1


def test_zero_usage_secret_all_buckets_zero():
    secrets = [_secret("unused-secret")]
    usage = compute_secret_usage(secrets, [], [], [], [])
    entry = usage["unused-secret"]
    assert entry["sessions"] == 0
    assert entry["templates"] == 0
    assert entry["profiles"] == 0
    assert entry["mcp_servers"] == 0
    assert entry["oauth2_dependents"] == []
    assert entry["total"] == 0
