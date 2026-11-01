"""fixture_gen realism flag (issue #2030 stage 4 PR 4b): ``tool_results_metadata``."""

import hashlib
import json

from backend.tests.load.fixture_gen import generate_session_file

# sha256 of generate_session_file(records=600, target_bytes=1 MiB, session_id="s") before the flag
# existed — the stage 2/3 load tests rely on the default output staying byte-identical.
DEFAULT_SHA256 = "1302c7f3a9b0766d69ecb80acd6b0e4c1d48418cea66f758a609706bb2ac044d"


def _gen(tmp_path, **kwargs):
    path = tmp_path / "messages.jsonl"
    generate_session_file(path, records=600, target_bytes=1024 * 1024, session_id="s", **kwargs)
    return path


def _carriers(path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r.get("metadata", {}).get("has_tool_results")], rows


def test_default_output_is_byte_identical(tmp_path):
    assert hashlib.sha256(_gen(tmp_path).read_bytes()).hexdigest() == DEFAULT_SHA256


def test_default_carrier_rows_have_no_tool_results(tmp_path):
    carriers, _ = _carriers(_gen(tmp_path))
    assert carriers and all("tool_results" not in c["metadata"] for c in carriers)


def test_flag_adds_tool_results_matching_the_preceding_tool_call(tmp_path):
    carriers, rows = _carriers(_gen(tmp_path, tool_results_metadata=True))
    assert carriers
    for row_idx, row in enumerate(rows):
        if row not in carriers:
            continue
        prev_call = rows[row_idx - 1]
        assert prev_call["type"] == "tool_call"
        assert row["metadata"]["tool_results"] == [{"tool_use_id": prev_call["tool_use_id"]}]
