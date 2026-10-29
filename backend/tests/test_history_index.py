"""Issue #2030 (stage 2 PR A): history index reducer — shared case file, prefilter equivalence,
fixture-level equivalence with the browser's predicate (see the vitest counterpart)."""

import json
import re
from pathlib import Path

import pytest

from backend.history_index import (
    INDEX_PREFILTERS,
    HistoryIndexState,
    reduce_records,
    scan_file_into,
)
from backend.message_history_reader import FileMessageHistoryReader

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "history_index_cases.json").read_text(encoding="utf-8"))["cases"]
FIXTURE_EXPECTED = FIXTURES / "history_index_fixture_expected.json"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _scan_file(path: Path, *, archive: bool = False) -> HistoryIndexState:
    state = HistoryIndexState(archive=archive)
    scan_file_into(FileMessageHistoryReader(path, strict_legacy=False), state)
    return state


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_case(case, tmp_path):
    state = reduce_records(case["records"], archive=case["archive"])
    assert state.turn_ids() == case["expected"]["turns"]
    assert state.compactions == case["expected"]["compactions"]
    # The prefiltered file scan must equal the in-memory full reduce.
    path = tmp_path / "messages.jsonl"
    _write_jsonl(path, case["records"])
    scanned = _scan_file(path, archive=case["archive"])
    assert scanned.turn_ids() == case["expected"]["turns"]
    assert scanned.compactions == case["expected"]["compactions"]
    assert scanned.end == path.stat().st_size


def test_turn_cursors_are_record_start_offsets(tmp_path):
    records = [
        {"type": "user", "message_id": "u1", "content": "héllo ✓"},
        {"type": "assistant", "message_id": "a1", "content": "x" * 5000},
        {"type": "user", "message_id": "u2", "content": "again"},
    ]
    path = tmp_path / "messages.jsonl"
    _write_jsonl(path, records)
    raw = path.read_bytes()
    state = _scan_file(path)
    turns = state.turns()
    assert [t[1] for t in turns] == ["u1", "u2"]
    for cursor, message_id, hint in turns:
        offset = int(cursor.removeprefix("b:"))
        assert hint == offset
        assert raw[offset:].startswith(b'{"type": "user", "message_id": "%s"' % message_id.encode())
    assert state.end == len(raw)


def test_partial_trailing_line_is_not_consumed(tmp_path):
    path = tmp_path / "messages.jsonl"
    complete = json.dumps({"type": "user", "message_id": "u1", "content": "a"}) + "\n"
    path.write_text(complete + '{"type": "user", "message_id": "u2", "cont', encoding="utf-8")
    state = _scan_file(path)
    assert state.turn_ids() == ["u1"]
    assert state.end == len(complete.encode())


def test_unterminated_last_record_is_left_to_the_live_feed(tmp_path):
    path = tmp_path / "messages.jsonl"
    first = json.dumps({"type": "user", "message_id": "u1", "content": "a"}) + "\n"
    last = json.dumps({"type": "user", "message_id": "u2", "content": "b"})
    path.write_text(first + last, encoding="utf-8")
    state = _scan_file(path)
    # the unterminated tail may be a record a writer is still finishing: left to the live feed
    assert state.turn_ids() == ["u1"]
    assert state.end == len(first.encode())


def test_scan_end_covers_trailing_non_matching_lines(tmp_path):
    path = tmp_path / "messages.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "user", "message_id": "u1", "content": "a"},
            {"type": "tool_call", "message_id": "t1", "name": "Bash"},
            {"type": "assistant", "message_id": "a1", "content": "zzz"},
        ],
    )
    assert _scan_file(path).end == path.stat().st_size


def test_line_separators_inside_content_do_not_split_records(tmp_path):
    path = tmp_path / "messages.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "user", "message_id": "u1", "content": "a b\u0085c"},
            {"type": "user", "message_id": "u2", "content": "d"},
        ],
    )
    assert _scan_file(path).turn_ids() == ["u1", "u2"]


def _fixture_record_files() -> dict[str, Path]:
    return {
        p.parent.name: p
        for p in sorted(FIXTURES.glob("*/messages.jsonl")) + sorted(FIXTURES.glob("raw/*/messages.jsonl"))
    }


def _fixture_result(path: Path) -> dict:
    state = _scan_file(path)
    full = reduce_records(
        [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    )
    assert state.turn_ids() == full.turn_ids()  # prefiltered scan == full parse
    assert state.compactions == full.compactions
    return {"turns": state.turn_ids(), "compactions": state.compactions}


def test_fixture_equivalence_with_committed_expected():
    """The committed expected file is the contract the vitest test checks the browser
    predicate against: server result == browser result on every committed fixture."""
    expected = json.loads(FIXTURE_EXPECTED.read_text(encoding="utf-8"))["fixtures"]
    actual = {name: _fixture_result(path) for name, path in _fixture_record_files().items()}
    assert actual == expected, (
        "history index drifted from history_index_fixture_expected.json — if the change is "
        "intended, regenerate it with `uv run python backend/tests/test_history_index.py --regen`"
    )


def test_scale_tiling_is_per_repetition_independent():
    """A fixture tiled N times (the scale fixture technique) indexes to N copies of the
    single-repetition result — at 20k+ records."""
    path = FIXTURES / "raw" / "primary" / "messages.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    single = reduce_records(records)
    reps = max(1, 20000 // len(records) + 1)
    tiled = []
    for rep in range(reps):
        for record in records:
            copy = dict(record)
            copy["message_id"] = f"{record['message_id']}-scale-rep{rep}"
            tiled.append(copy)
    state = reduce_records(tiled)
    assert len(tiled) >= 20000
    assert state.turn_ids() == [f"{i}-scale-rep{r}" for r in range(reps) for i in single.turn_ids()]
    assert state.compactions == [f"{i}-scale-rep{r}" for r in range(reps) for i in single.compactions]


if __name__ == "__main__":  # pragma: no cover - maintenance helper
    import sys

    if "--regen" in sys.argv:
        out = {
            "_comment": "Server history-index result per committed fixture; asserted by "
            "backend/tests/test_history_index.py and by the vitest browser-predicate check.",
            "fixtures": {n: _fixture_result(p) for n, p in _fixture_record_files().items()},
        }
        FIXTURE_EXPECTED.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


def test_regex_scan_matches_naive_line_scan_across_chunk_boundaries(tmp_path):
    """Offsets/records from the C-speed regex scan equal a naive per-line scan, including lines
    that straddle the 1 MiB read boundary, lines holding several needles and an unterminated
    last line."""
    import random

    rng = random.Random(11)
    needles = INDEX_PREFILTERS
    lines = []
    for i in range(400):
        kind = rng.choice(["user", "assistant", "tool_call", "status", "other"])
        pad = "x" * rng.choice([0, 10, 5_000, 300_000, 1_100_000]) if rng.random() < 0.1 else ""
        rec = {"type": kind, "message_id": f"id{i}", "content": f"c{i}{pad}"}
        if kind == "status":
            rec["metadata"] = {"subtype": "status", "init_data": {"status": None}}
        if rng.random() < 0.2:
            rec["extra"] = "has a user and status and init word"  # not quoted: no match
        lines.append(json.dumps(rec, ensure_ascii=False).encode("utf-8"))
    raw = b"\n".join(lines)  # no trailing newline: last record is unterminated but complete
    path = tmp_path / "messages.jsonl"
    path.write_bytes(raw)

    expected = []
    offset = 0
    for line in lines:
        if re.search(b"|".join(n.encode() for n in needles), line):
            expected.append((offset, offset + len(line) + 1 if offset + len(line) < len(raw) else offset + len(line), json.loads(line)))
        offset += len(line) + 1
    reader = FileMessageHistoryReader(path, strict_legacy=False)
    progress = [0]
    got = list(reader.iter_records_with_offsets(needles, progress=progress, regex=True))
    assert got == expected
    assert progress[0] == len(raw)


def test_read_range_is_lazy_and_exact(tmp_path):
    path = tmp_path / "messages.jsonl"
    lines = [json.dumps({"type": "assistant", "message_id": f"a{i}", "content": "x" * 400_000})
             for i in range(6)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reader = FileMessageHistoryReader(path, strict_legacy=False)
    gen = reader.read_range(0, path.stat().st_size)
    first = next(gen)
    assert first[0] == 0 and first[2]["message_id"] == "a0"
    gen.close()  # stopping early must be possible (no whole-gap read)
    offset = len(lines[0]) + 1
    got = list(reader.read_range(offset, offset + len(lines[1]) + 1))
    assert [(g[0], g[2]["message_id"]) for g in got] == [(offset, "a1")]


def test_pending_group_gap_resolution_stops_when_prefix_breaks(tmp_path):
    path = tmp_path / "messages.jsonl"
    rows = [
        {"type": "system", "message_id": "c", "metadata": {"subtype": "status", "init_data": {"status": "compacting"}}},
        {"type": "assistant", "message_id": "a", "content": "breaks the prefix"},
    ] + [{"type": "assistant", "message_id": f"z{i}", "content": "y" * 1000} for i in range(500)] + [
        {"type": "user", "message_id": "u", "content": "later"}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    reader = FileMessageHistoryReader(path, strict_legacy=False)
    parsed = []
    real = reader._parse
    reader._parse = lambda line: (parsed.append(1), real(line))[1]
    state = HistoryIndexState()
    scan_file_into(reader, state)
    assert state.turn_ids() == ["u"]
    assert len(parsed) < 10  # the 500 unrelated lines after the break were never parsed
