"""Template CRUD and import/export endpoints: /api/templates/*"""

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..exception_handlers import handle_exceptions
from ..template_manager import TemplateConflictError, TemplateInUseError
from ._models import TemplateCreateRequest, TemplateUpdateRequest


def build_router(webui) -> APIRouter:
    router = APIRouter()

    # ========== Template Endpoints ==========

    @router.get("/api/templates")
    @handle_exceptions("list templates")
    async def list_templates(limit: int = 100, offset: int = 0):
        """List minion templates, paginated"""
        return await webui.service.list_templates(limit=limit, offset=offset)

    @router.get("/api/templates/{template_id}")
    @handle_exceptions("get template")
    async def get_template(template_id: str):
        """Get specific template"""
        template = await webui.service.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        return template

    @router.post("/api/templates")
    @handle_exceptions("create template", value_error_status=400)
    async def create_template(request: TemplateCreateRequest):
        """Create new template"""
        config = request
        return await webui.service.create_template(
            name=request.name,
            config=config,
            role=request.role,
            system_prompt=request.system_prompt,
            description=request.description,
            capabilities=request.capabilities,
            profile_ids=request.profile_ids,
            template_overrides=request.template_overrides,
        )

    @router.put("/api/templates/{template_id}")
    @handle_exceptions("update template", value_error_status=400)
    async def update_template(template_id: str, request: TemplateUpdateRequest):
        """Update existing template"""
        return await webui.service.update_template(
            template_id,
            name=request.name,
            permission_mode=request.permission_mode,
            allowed_tools=request.allowed_tools,
            disallowed_tools=request.disallowed_tools,
            role=request.role,
            system_prompt=request.system_prompt,
            description=request.description,
            model=request.model,
            capabilities=request.capabilities,
            override_system_prompt=request.override_system_prompt,
            sandbox_enabled=request.sandbox_enabled,
            sandbox_config=request.sandbox_config,
            cli_path=request.cli_path,
            additional_directories=request.additional_directories,
            # Docker session isolation (issue #496)
            docker_enabled=request.docker_enabled,
            docker_image=request.docker_image,
            docker_extra_mounts=request.docker_extra_mounts,
            # Docker proxy configuration (issue #1116)
            docker_home_directory=request.docker_home_directory,
            docker_proxy_enabled=request.docker_proxy_enabled,
            docker_proxy_image=request.docker_proxy_image,
            assigned_secrets=request.assigned_secrets,
            docker_proxy_allowlist_domains=request.docker_proxy_allowlist_domains,
            # Thinking and effort configuration (issue #580)
            thinking_mode=request.thinking_mode,
            thinking_budget_tokens=request.thinking_budget_tokens,
            effort=request.effort,
            history_distillation_enabled=request.history_distillation_enabled,
            auto_memory_mode=request.auto_memory_mode,
            auto_memory_directory=request.auto_memory_directory,
            skill_creating_enabled=request.skill_creating_enabled,
            mcp_server_ids=request.mcp_server_ids,
            enable_claudeai_mcp_servers=request.enable_claudeai_mcp_servers,
            strict_mcp_config=request.strict_mcp_config,
            # Runtime feature flags (issue #1116)
            setting_sources=request.setting_sources,
            bare_mode=request.bare_mode,
            env_scrub_enabled=request.env_scrub_enabled,
            profile_ids=request.profile_ids,
            template_overrides=request.template_overrides,
        )

    @router.delete("/api/templates/{template_id}")
    @handle_exceptions("delete template")
    async def delete_template(template_id: str):
        """Delete template"""
        try:
            success = await webui.service.delete_template(template_id)
        except TemplateInUseError as e:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "template_in_use",
                    "message": str(e),
                    "blocking_sessions": [
                        {"session_id": sid, "name": name}
                        for sid, name in zip(e.session_ids, e.session_names, strict=True)
                    ],
                },
            )
        if not success:
            raise HTTPException(status_code=404, detail="Template not found")
        return {"deleted": True}

    @router.get("/api/templates/{template_id}/export")
    @handle_exceptions("export template")
    async def export_template(template_id: str):
        """Export template as a downloadable JSON envelope"""
        template = await webui.service.get_template(template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")
        envelope = {
            "version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "template": template,
        }
        slug = re.sub(r'[^a-z0-9]+', '_', template["name"].strip().lower()).strip('_')
        filename = f"{slug}.template.json"
        return Response(
            content=json.dumps(envelope, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/api/templates/import", status_code=201)
    @handle_exceptions("import template", value_error_status=400)
    async def import_template(request: Request):
        """Import a template from an export envelope"""
        body = await request.json()
        try:
            return await webui.service.import_template(
                data=body,
                overwrite=bool(body.get("overwrite", False)),
            )
        except TemplateConflictError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "name_conflict",
                    "existing_template_id": e.existing_id,
                    "name": e.name,
                },
            ) from e

    return router
