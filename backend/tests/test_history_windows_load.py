"""Issue #2030 (stage 3 PR 3A): quick-mode run of the windowed-reads load harness
(``backend/tests/load/history_windows_load.py``) against a REAL Backend (real GZipMiddleware) with
small fixtures. Asserts only the deterministic invariants (no errors, no request past its cap, no
leaked flights / cached state, every window contains its target); latency and loop-lag numbers at
the owner-relevant sizes are reported by the full-scale run, not asserted here."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_windows_load_quick_mode_invariants(tmp_path):
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "backend.tests.load.history_windows_load", "--phase", "all",
         "--scale", "0.05", "--duration", "8", "--browsers", "3", "--port", "8098",
         "--out", str(out)],
        cwd=REPO, capture_output=True, text=True, timeout=540,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(out.read_text())
    deterministic = (
        "no_request_beyond_cap", "no_leaked_flights", "no_leaked_state", "no_unexpected_errors",
        "windows_contain_their_target",
    )
    for label, arm in report["load"].items():
        for check in deterministic:
            assert arm["checks"][check], (label, check, arm["final_state"])
    windows = report["load"]["with_windows"]["ops"]
    assert windows["around"] > 0 and windows["around_id"] > 0 and windows["after"] > 0
    assert report["isolation"]["checks"]["no_request_beyond_cap"]
    single = report["single"]
    assert set(single) == {"small", "medium", "huge"}
    for row in single.values():
        assert row["around_warm"]["n"] and row["after_page"]["n"] and row["resolve_found_mid"]["n"]
        assert row["resolve_not_found_warm"]["n"]
    sat = report["saturation"]
    assert all(sat["checks"].values()), sat
    assert sat["lookups"]["ok"] > 0
