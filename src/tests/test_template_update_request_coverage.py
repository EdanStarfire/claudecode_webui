"""
Regression guard: TemplateUpdateRequest must declare every SessionConfig field
that is applicable to templates.

If a field is added to SessionConfig but not to TemplateUpdateRequest, Pydantic's
extra="ignore" (the default) silently drops it from PUT /api/templates/{id} payloads,
causing saves to appear successful while the field change is never persisted.

This test fails immediately when such a gap is introduced, making the regression
impossible to ship undetected.
"""


from src.routers._models import TemplateUpdateRequest
from src.session_config import SessionConfig

# Fields that are intentionally NOT editable via TemplateUpdateRequest.
# Each exclusion must be justified:
#   working_directory  — session-runtime field; templates don't own a working directory
#   template_id        — template primary key; not a per-session config value on the request
#   system_prompt      — TemplateUpdateRequest already declares system_prompt explicitly;
#                        no gap here, but SessionConfig also has it — exclude from diff check
#                        because it IS present in TemplateUpdateRequest
_SESSION_ONLY_FIELDS = {
    "working_directory",  # not applicable to templates
    "template_id",        # template PK, not a config field in the request
}


def test_issue_1116_template_update_request_covers_all_session_config_fields():
    """Every SessionConfig field (minus session-only exclusions) must appear in TemplateUpdateRequest.

    Failure means a field was added to SessionConfig but not wired through
    TemplateUpdateRequest, which causes silent data loss on template saves.
    """
    session_config_fields = set(SessionConfig.model_fields.keys())
    update_request_fields = set(TemplateUpdateRequest.model_fields.keys())

    applicable = session_config_fields - _SESSION_ONLY_FIELDS
    missing = applicable - update_request_fields

    assert missing == set(), (
        f"SessionConfig fields missing from TemplateUpdateRequest "
        f"(will be silently dropped on template save): {sorted(missing)}\n"
        f"Add them to TemplateUpdateRequest in src/web_server.py, wire through "
        f"the update_template endpoint handler, and add to TemplateManager.update_template()."
    )
