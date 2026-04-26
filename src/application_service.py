"""ApplicationService: service layer between HTTP routes and SessionCoordinator.

Routes call only this class. This class calls coordinator public API
and coordinator sub-managers. No route handler should access
coordinator attributes directly.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.session_coordinator import SessionCoordinator


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
        config = await self.coordinator.mcp_config_manager.update_config(config_id, **kwargs)
        return config.to_dict() if config else None

    async def delete_mcp_config(self, config_id: str) -> bool:
        return await self.coordinator.mcp_config_manager.delete_config(config_id)

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
    ) -> dict:
        """Create a new secret. Returns metadata only (value not in response)."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
            ScrubSpec,
            SecretRecord,
            SecretType,
        )

        now = datetime.now(UTC)
        inj_file = InjectFileSpec.from_dict(inject_file) if inject_file else None
        scrub_spec = ScrubSpec.from_dict(scrub) if scrub else None
        record = SecretRecord(
            name=name,
            type=SecretType(secret_type),
            target_hosts=target_hosts,
            inject_env=inject_env,
            inject_file=inj_file,
            scrub=scrub_spec,
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
    ) -> dict | None:
        """Update secret metadata and/or value. Returns updated metadata or None if not found."""
        from datetime import UTC, datetime

        from .models.secret_record import (
            InjectFileSpec,
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

        record = SecretRecord(
            name=name,
            type=SecretType(secret_type or existing.get("type", "generic")),
            target_hosts=target_hosts if target_hosts is not None else existing.get("target_hosts", []),
            inject_env=inject_env if inject_env is not None else existing.get("inject_env"),
            inject_file=inj_file,
            scrub=scrub_spec,
            created_at=datetime.fromisoformat(existing["created_at"]),
            updated_at=now,
        )
        return await self.coordinator.credential_vault.update_secret(name, record, value)

    async def delete_secret(self, name: str) -> bool:
        """Delete a secret by name."""
        return await self.coordinator.credential_vault.delete_secret(name)

    async def resolve_secrets_for_session(self, session_id: str) -> dict:
        """Return resolved secrets (including values) for a session's assigned_secrets list."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"secrets": []}

        assigned = getattr(session, "assigned_secrets", None) or []
        secrets = await self.coordinator.credential_vault.resolve_secrets_for_assignment(assigned)
        return {"secrets": secrets}

    async def get_proxy_status(self, session_id: str) -> dict:
        """Return effective allowlist + active credential names + proxy state for a session."""
        session = await self.coordinator.session_manager.get_session_info(session_id)
        if session is None:
            return {"proxy_enabled": False, "effective_domains": [], "active_credentials": [], "sidecar_running": False}

        proxy_enabled = getattr(session, "docker_proxy_enabled", False)
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
        extra_domains = getattr(session, "docker_proxy_allowlist_domains", None) or []
        all_domains = sorted(set(effective_domains + extra_domains))

        active_secrets = getattr(session, "assigned_secrets", None) or []

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
