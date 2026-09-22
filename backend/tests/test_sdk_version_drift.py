"""SDK-version-drift test (issue #1998, AC5).

A committed fixture's provenance.json records the claude-agent-sdk version that was
installed when it was recorded. If the installed version has since moved on, the
fixture may no longer be representative of real SDK output shapes — this must fail
loudly at test time (CI-only pytest gate, per the plan's Testing Strategy) rather
than let a stale fixture silently keep passing.
"""

import importlib.metadata
import json
from pathlib import Path

FIXTURES_RAW_DIR = Path(__file__).parent / "fixtures" / "raw"


def installed_sdk_version() -> str:
    return importlib.metadata.version("claude-agent-sdk")


def check_sdk_version_drift(provenance: dict, current_version: str) -> str | None:
    """Return a description of the drift if provenance's sdk_version doesn't match
    current_version, else None."""
    recorded = provenance.get("sdk_version")
    if recorded != current_version:
        return (
            f"Fixture was recorded against claude-agent-sdk=={recorded}, but "
            f"claude-agent-sdk=={current_version} is currently installed."
        )
    return None


class TestDriftDetectionLogic:
    """Unit-level: the drift-detection function itself, independent of whether any
    real recorded fixtures exist yet (owner populates fixtures/raw/ post-merge)."""

    def test_no_drift_when_versions_match(self):
        provenance = {"sdk_version": "0.2.152"}
        assert check_sdk_version_drift(provenance, "0.2.152") is None

    def test_drift_detected_when_versions_differ(self):
        provenance = {"sdk_version": "0.2.100"}
        drift = check_sdk_version_drift(provenance, "0.2.152")
        assert drift is not None
        assert "0.2.100" in drift
        assert "0.2.152" in drift

    def test_drift_detected_when_sdk_version_missing(self):
        drift = check_sdk_version_drift({}, "0.2.152")
        assert drift is not None


class TestRealFixturesAgainstInstalledSDK:
    """CI gate: every committed raw fixture's provenance must match the currently
    installed claude-agent-sdk version. No fixtures exist yet at the time this
    plan was implemented (owner populates backend/tests/fixtures/raw/ post-merge),
    so this collects zero cases until then — that's expected, not a hole: it starts
    enforcing the instant a real fixture is committed.
    """

    def _provenance_files(self) -> list[Path]:
        if not FIXTURES_RAW_DIR.exists():
            return []
        return sorted(FIXTURES_RAW_DIR.glob("*/provenance.json"))

    def test_no_version_drift_in_committed_fixtures(self):
        current = installed_sdk_version()
        drifted = []
        for provenance_path in self._provenance_files():
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            drift = check_sdk_version_drift(provenance, current)
            if drift:
                drifted.append(f"{provenance_path.parent.name}: {drift}")
        assert not drifted, "\n".join(drifted)
