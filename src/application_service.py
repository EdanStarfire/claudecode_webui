"""ApplicationService: service layer between HTTP routes and SessionCoordinator.

Routes call only this class. This class calls coordinator public API
and coordinator sub-managers. No route handler should access
coordinator attributes directly.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.session_coordinator import SessionCoordinator


def _compute_secret_health(refresh: dict | None) -> str:
    """Issue #1387: Derive token health state from refresh metadata.

    Returns one of: "valid", "expiring_soon", "expired", "refresh_failed".
    """
    if not refresh:
        return "valid"

    if refresh.get("last_refresh_error"):
        return "refresh_failed"

    expires_at_str = refresh.get("expires_at")
    if not expires_at_str:
        return "valid"

    try:
        expires_at = datetime.fromisoformat(expires_at_str)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        buffer = int(refresh.get("buffer_seconds", 300))
        now = datetime.now(tz=UTC)
        if now >= expires_at:
            return "expired"
        if now >= expires_at - timedelta(seconds=buffer):
            return "expiring_soon"
        return "valid"
    except Exception:
        return "valid"


class ApplicationService:
    """Service layer between HTTP routes and SessionCoordinator.

    Routes call only this class. This class calls coordinator public API
    and coordinator sub-managers. No route handler should access
    coordinator attributes directly.
    """

    def __init__(self, coordinator: SessionCoordinator) -> None:
        self.coordinator = coordinator

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _get_session_object(self, session_id: str):
        """Return raw SessionInfo object for attribute access."""
        return await self.coordinator.session_manager.get_session_info(session_id)

    # =========================================================================
    # Projects
    # =========================================================================

    async def create_project(
        self,
        name: str,
        working_directory: str,
        is_multi_agent: bool = False,
        max_concurrent_minions: int = 5,
        template_id: str | None = None,
    ) -> dict:
        project = await self.coordinator.project_manager.create_project(
            name=name,
            working_directory=working_directory,
            max_concurrent_minions=max_concurrent_minions,
        )
        return project.to_dict()

    async def list_projects(self, limit: int = 200, offset: int = 0) -> dict:
        projects = await self.coordinator.project_manager.list_projects()
        all_projects = [p.to_dict() for p in projects]
        total = len(all_projects)
        sliced = all_projects[offset : offset + limit]
        return {
            "projects": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_project(self, project_id: str) -> dict | None:
        project = await self.coordinator.project_manager.get_project(project_id)
        if not project:
            return None
        sessions = await self.coordinator.session_manager.get_sessions_by_ids(project.session_ids)
        result = project.to_dict()
        result["sessions"] = [s.to_dict() for s in sessions]
        return result

    async def reorder_projects(self, project_ids: list[str]) -> bool:
        return await self.coordinator.project_manager.reorder_projects(project_ids)

    async def update_project(self, project_id: str, **kwargs) -> dict | None:
        project = await self.coordinator.project_manager.get_project(project_id)
        if not project:
            return None
        success = await self.coordinator.project_manager.update_project(project_id, **kwargs)
        if not success:
            return None
        updated = await self.coordinator.project_manager.get_project(project_id)
        return updated.to_dict() if updated else None

    async def delete_project(self, project_id: str) -> dict:
        project = await self.coordinator.project_manager.get_project(project_id)
        if not project:
            return {"success": False, "error": "not_found"}
        success = await self.coordinator.project_manager.delete_project(project_id)
        return {"success": success}

    async def toggle_project_expansion(self, project_id: str) -> dict | None:
        success = await self.coordinator.project_manager.toggle_expansion(project_id)
        if not success:
            return None
        project = await self.coordinator.project_manager.get_project(project_id)
        return project.to_dict() if project else None

    async def reorder_project_sessions(self, project_id: str, session_ids: list[str]) -> dict | None:
        success = await self.coordinator.project_manager.reorder_project_sessions(
            project_id=project_id, session_ids=session_ids
        )
        if not success:
            return None
        await self.coordinator.session_manager.reorder_sessions(session_ids)
        project = await self.coordinator.project_manager.get_project(project_id)
        return project.to_dict() if project else None

    async def validate_project_exists(self, project_id: str) -> bool:
        project = await self.coordinator.project_manager.get_project(project_id)
        return project is not None

    # =========================================================================
    # Sessions
    # =========================================================================

    async def start_session(self, session_id: str, permission_callback: Any) -> bool:
        self.coordinator.clear_message_callbacks(session_id)
        return await self.coordinator.start_session(
            session_id, permission_callback=permission_callback
        )

    async def restart_session(self, session_id: str, permission_callback: Any) -> bool:
        self.coordinator.clear_message_callbacks(session_id)
        return await self.coordinator.restart_session(
            session_id, permission_callback=permission_callback
        )

    async def disconnect_session(self, session_id: str) -> dict:
        success = await self.coordinator.disconnect_sdk(session_id)
        return {"success": success}

    async def delete_session(self, session_id: str) -> dict:
        project = await self.coordinator.find_project_for_session(session_id)
        result = await self.coordinator.delete_session(session_id)
        if not result.get("success"):
            return result
        if project:
            updated_project = await self.coordinator.project_manager.get_project(project.project_id)
            result["project_id"] = project.project_id
            result["project_deleted"] = updated_project is None
            result["updated_project"] = updated_project.to_dict() if updated_project else None
        return result

    async def terminate_session(self, session_id: str) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return False
        return await self.coordinator.terminate_session(session_id)

    async def reset_session(self, session_id: str, permission_callback: Any) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return False
        return await self.coordinator.reset_session(
            session_id, permission_callback=permission_callback
        )

    async def update_session(self, session_id: str, **updates) -> bool:
        return await self.coordinator.session_manager.update_session(
            session_id,
            template_manager=self.coordinator.template_manager,
            **updates,
        )

    async def get_session_working_directory(self, session_id: str) -> str | None:
        """Return working_directory for file-serving and diff routes."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        return session.working_directory if session else None

    async def get_session_state(self, session_id: str) -> str | None:
        """Return the session state value, or None if not found."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        return session.state if session else None

    async def get_session_exists(self, session_id: str) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        return session is not None

    async def set_session_permission_mode(self, session_id: str, mode: str) -> bool:
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return False
        return await self.coordinator.set_permission_mode(session_id, mode)

    # =========================================================================
    # Diff helpers
    # =========================================================================

    async def get_session_diff_context(self, session_id: str) -> dict:
        """Return working_directory status for diff routes."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return {"exists": False}
        return {"exists": True, "working_directory": session.working_directory}

    async def get_session_messages_path(self, session_id: str) -> str | None:
        """Return absolute path to messages.jsonl for a session, or None if not found."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if not session:
            return None
        return str(self.coordinator.data_dir / "sessions" / session_id / "messages.jsonl")

    # =========================================================================
    # Legion
    # =========================================================================

    async def get_legion_project(self, legion_id: str) -> dict | None:
        project = await self.coordinator.project_manager.get_project(legion_id)
        return project.to_dict() if project else None

    async def get_minion_session(self, minion_id: str) -> dict | None:
        session = await self.coordinator.session_manager.get_session_info(minion_id)
        return session.to_dict() if session else None

    async def get_minion_name(self, minion_id: str) -> str | None:
        """Return session name for a minion, or None if not found."""
        session = await self.coordinator.session_manager.get_session_info(minion_id)
        return session.name if session else None

    # =========================================================================
    # MCP Configs
    # =========================================================================

    async def list_mcp_configs(self, limit: int = 100, offset: int = 0) -> dict:
        configs = await self.coordinator.mcp_config_manager.list_configs()
        all_configs = [c.to_dict() for c in configs]
        total = len(all_configs)
        sliced = all_configs[offset : offset + limit]
        return {
            "configs": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def create_mcp_config(
        self,
        name: str,
        server_type: Any,
        command: str | None = None,
        args: list | None = None,
        env: dict | None = None,
        url: str | None = None,
        headers: dict | None = None,
        enabled: bool = True,
        oauth_enabled: bool = False,
        oauth_client_id: str | None = None,
        oauth_callback_port: int | None = None,
        shared_connection: bool = False,
    ) -> dict:
        config = await self.coordinator.mcp_config_manager.create_config(
            name=name,
            server_type=server_type,
            command=command,
            args=args or [],
            env=env or {},
            url=url,
            headers=headers or {},
            enabled=enabled,
            oauth_enabled=oauth_enabled,
            oauth_client_id=oauth_client_id,
            oauth_callback_port=oauth_callback_port,
            shared_connection=shared_connection,
        )
        return config.to_dict()

    async def export_mcp_configs(self, ids: list[str] | None = None) -> list:
        configs = await self.coordinator.mcp_config_manager.list_configs()
        if ids is not None:
            id_set = set(ids)
            configs = [c for c in configs if c.id in id_set]
        return configs

    async def import_mcp_configs(self, servers: dict, dry_run: bool) -> dict:
        from src.mcp_config_manager import McpServerType

        manager = self.coordinator.mcp_config_manager
        existing_configs = await manager.list_configs()  # FIX: was manager.configs.values()
        existing_by_name = {c.name: c for c in existing_configs}

        preview = []
        imported = []

        for name, server_data in servers.items():
            name = name.strip()
            if not name:
                preview.append({"name": "", "action": "skip", "reason": "Missing name"})
                continue

            server_type_raw = server_data.get("type", "stdio")
            try:
                server_type = McpServerType(server_type_raw)
            except ValueError:
                preview.append(
                    {"name": name, "action": "skip", "reason": f"Invalid type: {server_type_raw}"}
                )
                continue

            if server_type == McpServerType.STDIO and not server_data.get("command"):
                preview.append(
                    {"name": name, "action": "skip", "reason": "Missing command for stdio server"}
                )
                continue

            if server_type in (McpServerType.SSE, McpServerType.HTTP) and not server_data.get("url"):
                preview.append(
                    {
                        "name": name,
                        "action": "skip",
                        "reason": f"Missing url for {server_type} server",
                    }
                )
                continue

            existing = existing_by_name.get(name)
            action = "update" if existing else "create"
            entry: dict = {
                "name": name,
                "action": action,
                "config": dict(server_data),
            }
            if existing:
                entry["existing_id"] = existing.id

            if not dry_run:
                try:
                    oauth_data = server_data.get("oauth") or {}
                    oauth_client_id = oauth_data.get("clientId")
                    oauth_callback_port = oauth_data.get("callbackPort")
                    if action == "create":
                        config = await manager.create_config(
                            name=name,
                            server_type=server_type,
                            command=server_data.get("command"),
                            args=server_data.get("args") or [],
                            env=server_data.get("env") or {},
                            url=server_data.get("url"),
                            headers=server_data.get("headers") or {},
                            enabled=server_data.get("enabled", True),
                            oauth_client_id=oauth_client_id,
                            oauth_callback_port=oauth_callback_port,
                        )
                    else:
                        config = await manager.update_config(
                            config_id=existing.id,
                            name=name,
                            server_type=server_type,
                            command=server_data.get("command"),
                            args=server_data.get("args") or [],
                            env=server_data.get("env") or {},
                            url=server_data.get("url"),
                            headers=server_data.get("headers") or {},
                            enabled=server_data.get("enabled", True),
                            oauth_client_id=oauth_client_id,
                            oauth_callback_port=oauth_callback_port,
                        )
                    entry["result"] = config.to_dict()
                    imported.append(config.to_dict())
                except Exception as e:
                    entry["action"] = "skip"
                    entry["reason"] = str(e)

            preview.append(entry)

        create_count = sum(1 for p in preview if p["action"] == "create")
        update_count = sum(1 for p in preview if p["action"] == "update")
        skip_count = sum(1 for p in preview if p["action"] == "skip")

        return {
            "dry_run": dry_run,
            "preview": preview,
            "summary": {"create": create_count, "update": update_count, "skip": skip_count},
            "imported": imported,
        }

    async def get_mcp_config(self, config_id: str) -> dict | None:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        return config.to_dict() if config else None

    async def update_mcp_config(self, config_id: str, **kwargs) -> dict | None:
        config = await self.coordinator.mcp_config_manager.update_config(
            config_id,
            shared_mcp_manager=self.coordinator.shared_mcp_manager,
            **kwargs,
        )
        return config.to_dict() if config else None

    async def delete_mcp_config(self, config_id: str) -> bool:
        return await self.coordinator.mcp_config_manager.delete_config(
            config_id,
            shared_mcp_manager=self.coordinator.shared_mcp_manager,
        )

    # =========================================================================
    # OAuth
    # =========================================================================

    async def oauth_complete_flow(self, state: str, code: str) -> str:
        return await self.coordinator.oauth_manager.complete_flow(state, code)

    async def oauth_initiate_flow(
        self,
        config_id: str,
        server_url: str,
        redirect_uri: str,
        client_name: str,
    ) -> str | None:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            return None
        return await self.coordinator.oauth_manager.start_flow(
            server_id=config_id,
            server_url=server_url,
            redirect_uri=redirect_uri,
            client_name=client_name,
        )

    async def oauth_disconnect(self, config_id: str) -> bool:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            return False
        await self.coordinator.oauth_manager.disconnect(config_id)
        return True

    async def oauth_get_status(self, config_id: str) -> dict | None:
        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            return None
        token = await self.coordinator.oauth_manager.get_stored_token(config_id)
        if token is None:
            return {"status": "unauthenticated", "expires_at": None, "has_refresh_token": False}

        store = self.coordinator.oauth_manager.get_token_store(config_id)
        expiry = await store.get_token_expiry()
        now = time.time()
        # Issue #976: Distinguish expiring_soon (within 5 min) from fully authenticated
        if expiry is not None and expiry < now:
            status = "expired"
        elif expiry is not None and expiry < (now + 300):
            status = "expiring_soon"
        else:
            status = "authenticated"
        return {
            "status": status,
            "expires_at": expiry,
            "has_refresh_token": bool(token.refresh_token),
        }

    async def import_oauth_as_secret(
        self, config_id: str, base_name: str, replace: bool = False
    ) -> dict:
        """Import stored OAuth 2.1 tokens as proxy-injectable vault secrets.

        Creates up to 3 vault secrets (primary oauth2, refresh, client_secret) and
        updates the MCP config's Authorization header to ${secret:<base_name>}.

        When replace=True (Reconnect flow): updates existing records in-place after
        verifying sibling ownership. Skips the 409 collision pre-check.

        Raises:
            ValueError: 400 — invalid base_name, STDIO config, or missing token_url.
            LookupError: 404 — config not found or no stored tokens.
            KeyError: 409 — vault name collision (replace=False) or sibling ownership
                           guard failure (replace=True).
        """
        import re
        from datetime import UTC, datetime
        from urllib.parse import urlparse

        from .models.secret_record import (
            RefreshSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        base_name_re = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
        if not base_name_re.match(base_name):
            raise ValueError(
                "base_name must match ^[a-z0-9][a-z0-9_-]{0,62}$"
            )

        config = await self.coordinator.mcp_config_manager.get_config(config_id)
        if not config:
            raise LookupError("404: MCP configuration not found")

        if not config.url:
            raise ValueError("Cannot import OAuth tokens from STDIO MCP server")

        store = self.coordinator.oauth_manager.get_token_store(config_id)
        tokens = await store.get_tokens()
        if tokens is None:
            raise LookupError("404: No OAuth tokens stored for this MCP server")

        expiry_ts = await store.get_token_expiry()
        client_info = await store.get_client_info()
        token_url = store.get_token_endpoint()
        if not token_url:
            raise ValueError("OAuth token endpoint not recorded; re-authenticate to refresh it")

        # Determine sibling secret names
        refresh_token_name = f"{base_name}_refresh" if tokens.refresh_token else None
        client_secret_value = client_info.client_secret if client_info else None
        client_secret_name = f"{base_name}_client_secret" if client_secret_value else None

        vault = self.coordinator.credential_vault
        now = datetime.now(UTC)
        host = urlparse(config.url).netloc
        token_path = urlparse(token_url).path
        client_id = (client_info.client_id if client_info else None) or config.oauth_client_id or ""
        expires_at_dt = datetime.fromtimestamp(expiry_ts, tz=UTC) if expiry_ts else None

        scrub = ScrubSpec(
            url_path=token_path,
            matcher_jsonpath="$.access_token",
            update_on_change=True,
        )

        refresh_spec = None
        if tokens.refresh_token and refresh_token_name:
            refresh_spec = RefreshSpec(
                token_url=token_url,
                client_id=client_id,
                refresh_token_secret_name=refresh_token_name,
                client_secret_secret_name=client_secret_name,
                expires_at=expires_at_dt,
                buffer_seconds=300,
            )

        expires_at_iso = expires_at_dt.isoformat() if expires_at_dt else None

        if replace:
            return await self._replace_oauth_secret_bundle(
                vault=vault,
                base_name=base_name,
                refresh_token_name=refresh_token_name,
                client_secret_name=client_secret_name,
                tokens=tokens,
                client_secret_value=client_secret_value,
                refresh_spec=refresh_spec,
                scrub=scrub,
                host=host,
                now=now,
                expires_at_iso=expires_at_iso,
                SecretRecord=SecretRecord,
                SecretType=SecretType,
            )

        # Pre-check vault collisions (replace=False only)
        for name in filter(None, [base_name, refresh_token_name, client_secret_name]):
            existing = await vault.get_secret(name)
            if existing is not None:
                raise KeyError(f"409: Secret '{name}' already exists; choose a different base_name")

        # Create secrets: siblings first, primary last; rollback on failure
        created: list[str] = []
        try:
            if refresh_token_name:
                refresh_record = SecretRecord(
                    name=refresh_token_name,
                    type=SecretType.GENERIC,
                    target_hosts=[host],
                    created_at=now,
                    updated_at=now,
                )
                await vault.create_secret(refresh_record, tokens.refresh_token)
                created.append(refresh_token_name)

            if client_secret_name and client_secret_value:
                cs_record = SecretRecord(
                    name=client_secret_name,
                    type=SecretType.GENERIC,
                    target_hosts=[host],
                    created_at=now,
                    updated_at=now,
                )
                await vault.create_secret(cs_record, client_secret_value)
                created.append(client_secret_name)

            primary_record = SecretRecord(
                name=base_name,
                type=SecretType.OAUTH2,
                target_hosts=[host],
                scrub=scrub,
                refresh=refresh_spec,
                created_at=now,
                updated_at=now,
            )
            await vault.create_secret(primary_record, tokens.access_token)
            created.append(base_name)
        except Exception:
            for n in created:
                await vault.delete_secret(n)
            raise

        # Update MCP config headers — rollback all secrets if this fails
        try:
            new_headers = {**(config.headers or {}), "Authorization": f"${{secret:{base_name}}}"}
            await self.coordinator.mcp_config_manager.update_config(config_id, headers=new_headers)
        except Exception:
            for n in created:
                await vault.delete_secret(n)
            raise

        # Issue #1387: schedule background refresh for the new oauth2 secret
        self.coordinator.vault_refresh_manager.schedule_secret(base_name)

        return {
            "secrets_created": created,
            "header_injected": f"Authorization: ${{secret:{base_name}}}",
            "expires_at": expires_at_iso,
            "auto_refresh_enabled": refresh_spec is not None,
        }

    async def _replace_oauth_secret_bundle(
        self,
        *,
        vault,
        base_name: str,
        refresh_token_name: str | None,
        client_secret_name: str | None,
        tokens,
        client_secret_value: str | None,
        refresh_spec,
        scrub,
        host: str,
        now,
        expires_at_iso: str | None,
    ) -> dict:
        """Replace existing vault oauth2 secret bundle in-place (Reconnect flow).

        Enforces sibling ownership guard: refuses to replace if the existing primary
        record's refresh metadata doesn't point to the expected sibling names.
        """
        from .models.secret_record import SecretRecord

        # Verify primary record exists
        primary_meta = await vault.get_secret(base_name)
        if primary_meta is None:
            raise LookupError(f"404: Primary secret '{base_name}' not found for replace")

        # Sibling ownership guard
        existing_refresh = primary_meta.get("refresh") or {}
        if refresh_token_name:
            expected = existing_refresh.get("refresh_token_secret_name")
            if expected and expected != refresh_token_name:
                raise KeyError(
                    f"409: Sibling ownership guard: refresh sibling '{expected}' "
                    f"doesn't match expected '{refresh_token_name}'"
                )
        if client_secret_name:
            expected_cs = existing_refresh.get("client_secret_secret_name")
            if expected_cs and expected_cs != client_secret_name:
                raise KeyError(
                    f"409: Sibling ownership guard: client_secret sibling '{expected_cs}' "
                    f"doesn't match expected '{client_secret_name}'"
                )

        updated: list[str] = []

        # Update refresh token sibling value
        if refresh_token_name and tokens.refresh_token:
            rt_meta = await vault.get_secret(refresh_token_name)
            if rt_meta:
                rt_record = SecretRecord.from_dict({**rt_meta, "updated_at": now.isoformat()})
                await vault.update_secret(refresh_token_name, rt_record, tokens.refresh_token)
                updated.append(refresh_token_name)

        # Update client_secret sibling value
        if client_secret_name and client_secret_value:
            cs_meta = await vault.get_secret(client_secret_name)
            if cs_meta:
                cs_record = SecretRecord.from_dict({**cs_meta, "updated_at": now.isoformat()})
                await vault.update_secret(client_secret_name, cs_record, client_secret_value)
                updated.append(client_secret_name)

        # Update primary record: new access_token value + reset refresh metadata
        primary_record = SecretRecord.from_dict({**primary_meta, "updated_at": now.isoformat()})
        if primary_record.refresh and refresh_spec:
            primary_record.refresh.expires_at = refresh_spec.expires_at
            primary_record.refresh.last_refresh_at = None
            primary_record.refresh.last_refresh_status = None
            primary_record.refresh.last_refresh_error = None
        elif refresh_spec:
            primary_record.refresh = refresh_spec
        await vault.update_secret(base_name, primary_record, tokens.access_token)
        updated.append(base_name)

        # Reschedule background refresh with fresh expiry
        self.coordinator.vault_refresh_manager.schedule_secret(base_name)

        return {
            "secrets_updated": updated,
            "header_injected": f"Authorization: ${{secret:{base_name}}}",
            "expires_at": expires_at_iso,
            "auto_refresh_enabled": refresh_spec is not None,
        }

    # =========================================================================
    # Templates
    # =========================================================================

    async def list_templates(self, limit: int = 100, offset: int = 0) -> dict:
        templates = await self.coordinator.template_manager.list_templates()
        all_templates = [t.to_dict() for t in templates]
        total = len(all_templates)
        sliced = all_templates[offset : offset + limit]
        return {
            "templates": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sliced) < total,
        }

    async def get_template(self, template_id: str) -> dict | None:
        template = await self.coordinator.template_manager.get_template(template_id)
        return template.to_dict() if template else None

    async def create_template(self, **kwargs) -> dict:
        template = await self.coordinator.template_manager.create_template(**kwargs)
        return template.to_dict()

    async def update_template(self, template_id: str, **kwargs) -> dict | None:
        template = await self.coordinator.template_manager.update_template(
            template_id=template_id, **kwargs
        )
        return template.to_dict() if template else None

    async def delete_template(self, template_id: str) -> bool:
        return await self.coordinator.template_manager.delete_template(
            template_id,
            session_manager=self.coordinator.session_manager,
        )

    async def import_template(self, **kwargs) -> dict:
        template = await self.coordinator.template_manager.import_template(**kwargs)
        return template.to_dict()

    # =========================================================================
    # Profiles (issue #1062)
    # =========================================================================

    async def list_profiles(self, area: str | None = None) -> dict:
        profiles = await self.coordinator.profile_manager.list_profiles(area=area)
        return {"profiles": [p.to_dict() for p in profiles]}

    async def get_profile(self, profile_id: str) -> dict | None:
        profile = await self.coordinator.profile_manager.get_profile(profile_id)
        return profile.to_dict() if profile else None

    async def create_profile(self, name: str, area: str, config: dict) -> dict:
        profile = await self.coordinator.profile_manager.create_profile(
            name=name, area=area, config=config
        )
        return profile.to_dict()

    async def update_profile(
        self,
        profile_id: str,
        name: str | None = None,
        config: dict | None = None,
    ) -> dict:
        profile = await self.coordinator.profile_manager.update_profile(
            profile_id=profile_id, name=name, config=config
        )
        return profile.to_dict()

    async def delete_profile(self, profile_id: str) -> bool:
        return await self.coordinator.profile_manager.delete_profile(
            profile_id,
            template_manager=self.coordinator.template_manager,
        )

    # =========================================================================
    # Secrets Vault (issue #827 — replaces issue #1053 CredentialVault)
    # =========================================================================

    async def list_secrets(self) -> dict:
        """List all secret metadata. Never includes secret values."""
        secrets = await self.coordinator.credential_vault.list_secrets()
        # Issue #1387: enrich OAUTH2 secrets with a computed health field
        for s in secrets:
            if s.get("type") == "oauth2":
                s["health"] = _compute_secret_health(s.get("refresh"))
        return {"secrets": secrets}

    async def create_secret(
        self,
        name: str,
        secret_type: str,
        target_hosts: list[str],
        value: str,
        inject_env: str | None = None,
        inject_file: dict | None = None,
        scrub: dict | None = None,
        username: str | None = None,
        injection: dict | None = None,
        refresh: dict | None = None,
    ) -> dict:
        """Create a new secret. Returns metadata only (value not in response)."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
            InjectionSpec,
            RefreshSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        now = datetime.now(UTC)
        inj_file = InjectFileSpec.from_dict(inject_file) if inject_file else None
        scrub_spec = ScrubSpec.from_dict(scrub) if scrub else None
        injection_spec = InjectionSpec.from_dict(injection) if injection else None
        refresh_spec = RefreshSpec.from_dict(refresh) if refresh else None
        record = SecretRecord(
            name=name,
            type=SecretType(secret_type),
            target_hosts=target_hosts,
            inject_env=inject_env,
            inject_file=inj_file,
            scrub=scrub_spec,
            username=username,
            injection=injection_spec,
            refresh=refresh_spec,
            created_at=now,
            updated_at=now,
        )
        return await self.coordinator.credential_vault.create_secret(record, value)

    async def update_secret(
        self,
        name: str,
        secret_type: str | None = None,
        target_hosts: list[str] | None = None,
        value: str | None = None,
        inject_env: str | None = None,
        inject_file: dict | None = None,
        scrub: dict | None = None,
        username: str | None = None,
        injection: dict | None = None,
        refresh: dict | None = None,
    ) -> dict | None:
        """Update secret metadata and/or value. Returns updated metadata or None if not found."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
            InjectionSpec,
            RefreshSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        existing = await self.coordinator.credential_vault.get_secret(name)
        if existing is None:
            return None

        now = datetime.now(UTC)
        inj_file = None
        if inject_file is not None:
            inj_file = InjectFileSpec.from_dict(inject_file)
        elif existing.get("inject_file"):
            inj_file = InjectFileSpec.from_dict(existing["inject_file"])

        scrub_spec = None
        if scrub is not None:
            scrub_spec = ScrubSpec.from_dict(scrub)
        elif existing.get("scrub"):
            scrub_spec = ScrubSpec.from_dict(existing["scrub"])

        injection_spec = None
        if injection is not None:
            injection_spec = InjectionSpec.from_dict(injection)
        elif existing.get("injection"):
            injection_spec = InjectionSpec.from_dict(existing["injection"])

        refresh_spec = None
        if refresh is not None:
            refresh_spec = RefreshSpec.from_dict(refresh)
        elif existing.get("refresh"):
            refresh_spec = RefreshSpec.from_dict(existing["refresh"])

        record = SecretRecord(
            name=existing.get("name", name),
            type=SecretType(secret_type or existing.get("type", "generic")),
            target_hosts=target_hosts if target_hosts is not None else existing.get("target_hosts", []),
            inject_env=inject_env if inject_env is not None else existing.get("inject_env"),
            inject_file=inj_file,
            scrub=scrub_spec,
            username=username if username is not None else existing.get("username"),
            injection=injection_spec,
            refresh=refresh_spec,
            created_at=datetime.fromisoformat(existing["created_at"]),
            updated_at=now,
        )
        return await self.coordinator.credential_vault.update_secret(name, record, value)

    async def delete_secret(self, name: str) -> bool:
        """Delete a secret by name."""
        return await self.coordinator.credential_vault.delete_secret(name)

    async def refresh_secret(self, name: str) -> dict | None:
        """Manually trigger an OAuth2 token refresh for an oauth2-typed secret.

        Fetches the current refresh_token and client_secret sibling values from the
        keyring, POSTs to token_url, writes the new access_token (and rotated
        refresh_token if present) back to the keyring, and updates expires_at.
        Returns updated metadata, or None if the secret does not exist / is not oauth2.
        Raises RuntimeError on refresh failure.

        Issue #1387: Acquires the VaultRefreshManager per-secret lock to prevent
        concurrent refresh races with the background task.
        """
        # Issue #1387: acquire per-secret lock to deduplicate concurrent refreshes
        mgr = getattr(self.coordinator, "vault_refresh_manager", None)
        if mgr:
            lock = mgr.get_lock(name)
            async with lock:
                return await self._refresh_secret_impl(name)
        return await self._refresh_secret_impl(name)

    async def _refresh_secret_impl(self, name: str) -> dict | None:
        """Core refresh logic — caller must hold per-secret lock if dedup is needed."""
        from .models.secret_record import SecretType
        from .secret_types.oauth2 import OAuth2Handler
        from .secrets_keyring import get_secret_value, set_secret_value

        meta = await self.coordinator.credential_vault.get_secret(name)
        if meta is None or meta.get("type") != SecretType.OAUTH2.value:
            return None

        record = dict(meta)
        record["value"] = get_secret_value(name) or ""

        async def _get_sibling(sibling_name: str) -> str:
            return get_secret_value(sibling_name) or ""

        handler = OAuth2Handler()
        updates = await handler.refresh(record, _get_sibling)

        new_value = updates.get("value")
        if new_value:
            set_secret_value(name, new_value)

        new_refresh_token = updates.get("refresh", {}).get("_new_refresh_token")
        new_refresh_name = (record.get("refresh") or {}).get("refresh_token_secret_name")
        if new_refresh_token and new_refresh_name:
            set_secret_value(new_refresh_name, new_refresh_token)

        refresh_updates = updates.get("refresh") or {}
        refresh_updates.pop("_new_refresh_token", None)

        return await self.update_secret(
            name=name,
            refresh=refresh_updates or None,
            value=None,
        )

    def _resolve_writeable_secret_names(self, session) -> list[str]:
        """Names the proxy can both resolve and write back for this session.

        Source order (deduped, preserves first occurrence):
          1. session.config["assigned_secrets"] — direct session-level overrides
          2. session.secret_placeholders.values() — template/profile-derived names captured at start_session()
        """
        names: list[str] = list(session.config.get("assigned_secrets") or [])
        for name in (session.secret_placeholders or {}).values():
            if name not in names:
                names.append(name)
        return names

    async def resolve_secrets_for_session(self, session_id: str) -> dict:
        """Return resolved secrets (including values) for a session's assigned_secrets list.

        Issue #1134: attaches placeholder from session.secret_placeholders; also
        walks transitive references (refresh_token_secret_name, client_secret_secret_name)
        so the proxy has everything it needs for OAuth2 refresh without extra requests.
        """
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"secrets": []}

        assigned = self._resolve_writeable_secret_names(session)

        # Collect transitive names (sibling refresh-token and client-secret records).
        all_names: list[str] = list(assigned)
        for secret in await self.coordinator.credential_vault.list_secrets():
            if secret.get("name") in assigned:
                refresh = secret.get("refresh") or {}
                for key in ("refresh_token_secret_name", "client_secret_secret_name"):
                    sibling = refresh.get(key)
                    if sibling and sibling not in all_names:
                        all_names.append(sibling)

        secrets = await self.coordinator.credential_vault.resolve_secrets_for_assignment(all_names)

        # Attach placeholder from session map (may be empty for sessions started before #1134).
        placeholder_map = session.secret_placeholders or {}
        reverse_map = {v: k for k, v in placeholder_map.items()}
        for s in secrets:
            name = s.get("name", "")
            s["placeholder"] = reverse_map.get(name)

        return {"secrets": secrets}

    async def update_secret_for_session(
        self, session_id: str, secret_name: str, **kwargs
    ) -> dict | None:
        """Update a secret, scoped to names in session.assigned_secrets (+ transitive siblings).

        Used by the proxy sidecar to write back refreshed token values.
        Returns None if session not found; raises PermissionError if name is out-of-scope.
        """
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return None
        assigned = self._resolve_writeable_secret_names(session)
        allowed: set[str] = set(assigned)
        # Also allow transitive sibling records referenced by assigned refresh specs.
        for secret in await self.coordinator.credential_vault.list_secrets():
            if secret.get("name") in assigned:
                refresh = secret.get("refresh") or {}
                for key in ("refresh_token_secret_name", "client_secret_secret_name"):
                    sibling = refresh.get(key)
                    if sibling:
                        allowed.add(sibling)
        if secret_name not in allowed:
            raise PermissionError(
                f"Secret '{secret_name}' is not in session {session_id}'s assigned_secrets"
            )
        return await self.update_secret(name=secret_name, **kwargs)

    async def get_proxy_status(self, session_id: str) -> dict:
        """Return effective allowlist + active credential names + proxy state for a session."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"proxy_enabled": False, "effective_domains": [], "active_credentials": [], "sidecar_running": False}

        proxy_enabled = session.config.get("docker_proxy_enabled", False)
        if not proxy_enabled:
            return {"proxy_enabled": False, "effective_domains": [], "active_credentials": [], "sidecar_running": False}

        # Build effective domains: static defaults + session config
        effective_domains: list[str] = []
        import json as _json
        from pathlib import Path as _Path
        static_allowlist_path = _Path("src/docker/proxy/allowlist.json")
        if static_allowlist_path.exists():
            try:
                static = _json.loads(static_allowlist_path.read_text())
                effective_domains.extend(static.get("domains", []))
            except Exception:
                pass
        extra_domains = session.config.get("docker_proxy_allowlist_domains") or []
        all_domains = sorted(set(effective_domains + extra_domains))

        active_secrets = session.config.get("assigned_secrets") or []

        return {
            "proxy_enabled": True,
            "effective_domains": all_domains,
            "active_credentials": active_secrets,
            "sidecar_running": False,  # Sidecar runtime state not tracked server-side
        }

    async def get_proxy_blocked_log(self, session_id: str, limit: int = 50) -> dict:
        """Return recent blocked connections from the sidecar access log."""
        import json as _json

        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"entries": []}

        # Access log is written to data/sessions/{id}/docker_claude_data/proxy/access.log
        session_dir = self.coordinator.data_dir / "sessions" / session_id
        log_path = session_dir / "docker_claude_data" / "proxy" / "access.log"
        if not log_path.exists():
            return {"entries": []}

        blocked = []
        try:
            lines = log_path.read_text().splitlines()
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    entry = _json.loads(line)
                    if not entry.get("allowed", True):
                        blocked.append(entry)
                        if len(blocked) >= limit:
                            break
                except Exception:
                    continue
        except Exception:
            pass

        return {"entries": blocked}
