"""Tests for MinionTemplate data model (issue #1059 schema additions)."""


from ..models.minion_template import MinionTemplate


class TestMinionTemplateProfileId:
    """Tests for profile_id field added in issue #1059."""

    def test_minion_template_profile_id_default(self):
        """profile_id defaults to None when not provided."""
        template = MinionTemplate(
            template_id="tmpl-001",
            name="Test Template",
            permission_mode="acceptEdits",
        )
        assert template.profile_id is None

    def test_minion_template_from_dict_backward_compat(self):
        """from_dict with missing profile_id defaults to None."""
        data = {
            "template_id": "tmpl-002",
            "name": "Legacy Template",
            "permission_mode": "default",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        }
        template = MinionTemplate.from_dict(data)
        assert template.profile_id is None

    def test_minion_template_round_trip_profile_id(self):
        """to_dict() → from_dict() preserves profile_id."""
        template = MinionTemplate(
            template_id="tmpl-003",
            name="Profile Template",
            permission_mode="acceptEdits",
            profile_id="profile-xyz",
        )
        restored = MinionTemplate.from_dict(template.to_dict())
        assert restored.profile_id == "profile-xyz"
