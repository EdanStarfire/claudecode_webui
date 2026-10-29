"""Issue #2030 (stage 3 PR 3B): quick-mode run of the search load harness
(``backend/tests/load/history_index_load.py --phase search``) against a REAL Backend (real
GZipMiddleware) with small fixtures. Asserts only the deterministic invariants (no errors, no
request past its cap, bounded pages, no leaked flights / cached state); latency and loop-lag
numbers at the owner-relevant sizes are reported by the full-scale run, not asserted here."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_search_load_quick_mode_invariants(tmp_path):
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, "-m", "backend.tests.load.history_index_load", "--phase", "search",
         "--scale", "0.05", "--duration", "8", "--browsers", "3", "--port", "8097",
         "--out", str(out)],
        cwd=REPO, capture_output=True, text=True, timeout=540,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(out.read_text())
    deterministic = (
        "no_request_beyond_cap", "page_size_bounded", "no_leaked_flights", "no_leaked_state",
        "no_unexpected_errors",
    )
    for label, arm in report["search_load"].items():
        for check in deterministic:
            assert arm["checks"][check], (label, check, arm["final_state"])
    with_search = report["search_load"]["with_search"]
    assert with_search["ops"]["searches"] > 0 and with_search["ops"]["supersede_pairs"] > 0
    assert with_search["server_search_counters"]["max_running"] <= with_search["params"]["search_workers"]
    single = report["search_single"]
    assert set(single) == {"small", "medium", "huge"}
    assert all(row["nohit"]["status"] == 200 and row["nohit"]["results"] == 0 for row in single.values())
    assert all(row["rare"]["results"] >= 1 for row in single.values())
