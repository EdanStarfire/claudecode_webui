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

import pytest

FIXTURES_RAW_DIR = Path(__file__).parent / "fixtures" / "raw"

# Known, tracked exceptions to the drift gate — mirrors KNOWN_DIVERGENT_FIXTURES in
# frontend/src/stores/__tests__/equivalence.test.js (#1999/#2007): an honest, tracked
# exception, not a silent skip or a provenance edit. Applied via strict xfail below, so
# the check still runs every time and still fails loudly if a listed fixture's
# provenance.json starts matching the installed SDK version again without actually
# updating the entry (e.g. after a real re-capture) — forcing removal of the stale
# entry rather than letting it drift unnoticed either direction.
KNOWN_SDK_VERSION_DRIFT_FIXTURES = {
    "2026-09-23-primary": (
        "issue #2017",
        "captured against claude-agent-sdk==0.2.152 (#1998); frozen ahead of the "
        "0.2.152->0.2.159 bump (#2008) because re-capturing requires a live SDK "
        "session with real Anthropic credentials (see SCENARIO_CHECKLIST.md), which "
        "is an owner-gated manual task tracked in #2017. CHANGELOG review of "
        "claude-agent-sdk 0.2.153-0.2.159 found no message-shape-affecting changes in "
        "that range (only two opt-in, unadopted features: SystemPromptPreset.snapshot, "
        "ClaudeAgentOptions.verbatim_prompts), so this fixture is still considered "
        "representative in the interim.",
    ),
}


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


def _provenance_files() -> list[Path]:
    if not FIXTURES_RAW_DIR.exists():
        return []
    return sorted(FIXTURES_RAW_DIR.glob("*/provenance.json"))


def _fixture_params():
    params = []
    for path in _provenance_files():
        name = path.parent.name
        marks = []
        if name in KNOWN_SDK_VERSION_DRIFT_FIXTURES:
            issue_ref, reason = KNOWN_SDK_VERSION_DRIFT_FIXTURES[name]
            marks.append(pytest.mark.xfail(reason=f"{issue_ref}: {reason}", strict=True))
        params.append(pytest.param(path, id=name, marks=marks))
    return params


class TestRealFixturesAgainstInstalledSDK:
    """CI gate: every committed raw fixture's provenance must match the currently
    installed claude-agent-sdk version. No fixtures exist yet at the time this
    plan was implemented (owner populates backend/tests/fixtures/raw/ post-merge),
    so this collects zero cases until then — that's expected, not a hole: it starts
    enforcing the instant a real fixture is committed.
    """

    @pytest.mark.parametrize("provenance_path", _fixture_params())
    def test_no_version_drift_in_committed_fixture(self, provenance_path):
        current = installed_sdk_version()
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        drift = check_sdk_version_drift(provenance, current)
        assert drift is None, drift
