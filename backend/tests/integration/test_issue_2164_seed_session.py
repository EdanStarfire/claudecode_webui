"""
Issue #2164: "New session from this configuration" — pins the POST /api/sessions
contract this frontend-only feature depends on. No backend code change was made for
this feature; this test exists so a future refactor of create_session() can't
silently break the contract: passing an existing session's template_id + config
dict as extra payload fields produces a new session whose own template_id/config
match, with a fresh session_id, no message history, and (for a minion source) no
parent_overseer_id/role carried over.
"""

from backend.session_config import SessionConfig


class TestSeedSessionFromConfiguration:
    async def test_seed_from_templated_session_preserves_linkage(self, api_integration_env):
        client = api_integration_env["client"]
        coordinator = api_integration_env["coordinator"]
        create_project = api_integration_env["create_test_project"]

        project = await create_project("Seed Source Project")
        pid = project["project_id"]

        template = await coordinator.template_manager.create_template(
            name="Seed Template",
            config=SessionConfig(permission_mode="plan"),
        )

        resp = await client.post("/api/sessions", json={
            "project_id": pid,
            "name": "Source Session",
            "template_id": template.template_id,
            "model": "opus",
        })
        assert resp.status_code == 200
        source_id = resp.json()["session_id"]

        resp = await client.get(f"/api/sessions/{source_id}")
        assert resp.status_code == 200
        source = resp.json()["session"]
        assert source["template_id"] == template.template_id
        assert source["config"].get("model") == "opus"

        # Mirrors the frontend's seeded-creation payload: template_id + the source's
        # explicit CONFIG_FIELDS overrides, spread under a fresh identity.
        resp = await client.post("/api/sessions", json={
            "project_id": pid,
            "name": "Seeded Session",
            "template_id": source["template_id"],
            **source["config"],
        })
        assert resp.status_code == 200
        new_id = resp.json()["session_id"]
        assert new_id != source_id

        resp = await client.get(f"/api/sessions/{new_id}")
        assert resp.status_code == 200
        new_session = resp.json()["session"]
        assert new_session["template_id"] == template.template_id
        assert new_session["config"] == source["config"]
        assert new_session["is_ephemeral"] is False

        resp = await client.get(f"/api/sessions/{new_id}/messages")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

        # Source session is untouched.
        resp = await client.get(f"/api/sessions/{source_id}")
        assert resp.json()["session"]["config"] == source["config"]

    async def test_seed_from_minion_source_produces_plain_top_level_session(
        self, api_integration_env
    ):
        client = api_integration_env["client"]
        create_test_legion_project = api_integration_env["create_test_legion_project"]
        create_test_minion = api_integration_env["create_test_minion"]

        legion = await create_test_legion_project("Seed Source Legion")
        legion_id = legion["project_id"]

        minion = await create_test_minion(
            legion_id,
            name="Source Minion",
            role="Code Reviewer",
            model="haiku",
        )
        minion_id = minion["session_id"]

        resp = await client.get(f"/api/sessions/{minion_id}")
        assert resp.status_code == 200
        source = resp.json()["session"]
        assert source["role"] == "Code Reviewer"
        assert source["config"].get("model") == "haiku"

        # Same seeded-creation payload shape — template_id (none here) + config only,
        # deliberately NOT role/capabilities/working_directory/parent_overseer_id.
        resp = await client.post("/api/sessions", json={
            "project_id": legion_id,
            "name": "Seeded From Minion",
            **source["config"],
        })
        assert resp.status_code == 200
        new_id = resp.json()["session_id"]
        assert new_id != minion_id

        resp = await client.get(f"/api/sessions/{new_id}")
        assert resp.status_code == 200
        new_session = resp.json()["session"]
        assert new_session["config"] == source["config"]
        # The source's role must NOT be carried over — it's identity, not config, and
        # the user supplies a fresh one via the form. No role in the payload falls back
        # to create_session()'s pre-existing "assistant" default, same as any other
        # role-less session creation.
        assert new_session["role"] == "assistant"
        assert new_session["role"] != source["role"]
        assert new_session["parent_overseer_id"] is None
        assert new_session["is_overseer"] is False
        assert new_session["child_minion_ids"] == []
