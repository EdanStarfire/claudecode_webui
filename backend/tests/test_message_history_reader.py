"""Issue #2030 (stage 1a): MessageHistoryReader — window reads over messages.jsonl."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from backend.message_history_reader import (
    CHUNK_SIZE,
    CursorInvalidError,
    FileMessageHistoryReader,
    WindowBudget,
    _is_turn_boundary,
    build_budget,
    decode_cursor,
    encode_cursor,
    find_latest_init,
    read_history_payload,
)
from backend.models.messages import LegacyMessageFormatError


def _rec(n: int, kind: str = "assistant", pad: int = 0, **extra) -> dict:
    rec = {
        "type": kind, "timestamp": 1000.0 + n, "session_id": "s", "message_id": f"m{n}",
        "content": f"c{n}" + ("x" * pad), "metadata": extra.pop("metadata", {}),
    }
    rec.update(extra)
    return rec


def _tool_result_user(n: int) -> dict:
    return _rec(n, "user", metadata={"has_tool_results": True})


def _write(path: Path, records: list[dict], *, trailing_newline: bool = True) -> list[int]:
    """Write records; return each record's line-start byte offset."""
    offsets = []
    data = b""
    for rec in records:
        offsets.append(len(data))
        data += json.dumps(rec, ensure_ascii=False).encode() + b"\n"
    if not trailing_newline:
        data = data[:-1]
    path.write_bytes(data)
    return offsets


def _conversation(turns: int, per_turn: int = 4, pad: int = 0) -> list[dict]:
    """user, then (assistant, tool_call-ish, tool-result-user)* — realistic turn shape."""
    out = []
    n = 0
    for _ in range(turns):
        out.append(_rec(n, "user", pad=pad))
        n += 1
        for _ in range(per_turn - 2):
            out.append(_rec(n, "assistant", pad=pad))
            n += 1
        out.append(_tool_result_user(n))
        n += 1
    return out


def _ids(records: list[dict]) -> list[str]:
    return [r["message_id"] for r in records]


def _reader(path: Path) -> FileMessageHistoryReader:
    return FileMessageHistoryReader(path, label="sess")


# --------------------------------------------------------------------------- cursor


def test_cursor_roundtrip_and_validation():
    assert decode_cursor(encode_cursor(1234)) == 1234
    for bad in ["", "x:1", "b:", "b:-1", "b:1.5", "b: 3", "b:١٢", None, 5]:
        with pytest.raises(CursorInvalidError):
            decode_cursor(bad)


# --------------------------------------------------------------------------- windows


@pytest.mark.asyncio
async def test_tail_window_snaps_to_user_turn_boundary(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(6)
    offsets = _write(path, records)
    window = await _reader(path).read_tail(WindowBudget(min_turns=2))
    # last 2 turns = last 2*4 records, starting at a (non-tool-result) user record
    assert _ids(window.records) == _ids(records[-8:])
    assert window.records[0]["type"] == "user"
    assert window.snapped is True
    assert window.has_more_before is True
    assert window.start_cursor == encode_cursor(offsets[-8])
    assert window.total_bytes == path.stat().st_size


@pytest.mark.asyncio
async def test_session_shorter_than_window_returns_everything(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(1)
    _write(path, records)
    window = await _reader(path).read_tail(WindowBudget(min_turns=5))
    assert _ids(window.records) == _ids(records)
    assert window.has_more_before is False
    assert window.start_cursor == "b:0"
    assert window.snapped is True


@pytest.mark.asyncio
async def test_empty_and_missing_file(tmp_path):
    path = tmp_path / "messages.jsonl"
    reader = _reader(path)
    missing = await reader.read_tail(WindowBudget())
    assert missing.records == [] and missing.has_more_before is False
    path.write_bytes(b"")
    empty = await reader.read_tail(WindowBudget())
    assert empty.records == [] and empty.has_more_before is False
    assert empty.total_bytes == 0 and empty.start_cursor == "b:0"
    assert (await reader.read_before("b:0", WindowBudget())).records == []


@pytest.mark.asyncio
async def test_record_cap_trims_to_oldest_included_boundary(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(10)  # 4 records / turn
    _write(path, records)
    window = await _reader(path).read_tail(WindowBudget(min_turns=10, max_records=10))
    # cap of 10 records admits 2 whole turns (8 records) + 2 stray records -> trimmed to 8
    assert _ids(window.records) == _ids(records[-8:])
    assert window.records[0]["type"] == "user" and window.snapped is True


@pytest.mark.asyncio
async def test_never_fewer_than_last_turn_even_over_soft_caps(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(3, per_turn=6)
    _write(path, records)
    window = await _reader(path).read_tail(WindowBudget(min_turns=2, max_records=2, max_bytes=10))
    assert _ids(window.records) == _ids(records[-6:])  # whole last turn
    assert window.snapped is True


@pytest.mark.asyncio
async def test_single_huge_turn_cut_at_record_boundary_unsnapped(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = [_rec(0, "user")] + [_rec(i, "assistant", pad=40_000) for i in range(1, 30)]
    _write(path, records)
    budget = WindowBudget(min_turns=1, hard_ceiling_bytes=300_000)
    window = await _reader(path).read_tail(budget)
    assert window.snapped is False
    assert window.has_more_before is True
    assert 0 < len(window.records) < len(records)
    assert _ids(window.records) == _ids(records[-len(window.records):])


@pytest.mark.asyncio
async def test_tool_result_user_records_are_not_turn_boundaries(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = [
        _rec(0, "user"), _rec(1, "assistant"), _tool_result_user(2), _rec(3, "assistant"),
        _tool_result_user(4), _rec(5, "assistant"),
    ]
    _write(path, records)
    window = await _reader(path).read_tail(WindowBudget(min_turns=1))
    assert _ids(window.records) == _ids(records)  # only user turn starts at 0


@pytest.mark.asyncio
async def test_thinking_tokens_records_do_not_count_toward_caps(tmp_path):
    path = tmp_path / "messages.jsonl"
    noise = [_rec(100 + i, "system", metadata={"subtype": "thinking_tokens"}) for i in range(50)]
    records = [_rec(0, "user"), _rec(1, "assistant")] + noise + [_rec(2, "assistant")]
    _write(path, records)
    window = await _reader(path).read_tail(WindowBudget(min_turns=1, max_records=3))
    assert len(window.records) == len(records)


@pytest.mark.asyncio
async def test_legacy_type_record_raises(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_bytes(json.dumps({"_type": "assistant", "data": {}}).encode() + b"\n")
    with pytest.raises(LegacyMessageFormatError):
        await _reader(path).read_tail(WindowBudget())


# --------------------------------------------------------------------------- property


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_window_equals_slice_of_full_read_at_every_start_position(tmp_path, seed):
    """For every line-start cursor k: the `before` page is a contiguous slice ending just
    before k, and paging backward from the tail reconstructs the full read exactly."""
    rng = random.Random(seed)
    records = []
    n = 0
    for _ in range(rng.randint(8, 14)):
        records.append(_rec(n, "user", pad=rng.randint(0, 200)))
        n += 1
        for _ in range(rng.randint(0, 6)):
            kind = rng.choice(["assistant", "system", "tool_call"])
            records.append(_rec(n, kind, pad=rng.randint(0, 3000)))
            n += 1
            if rng.random() < 0.3:
                records.append(_tool_result_user(n))
                n += 1
    path = tmp_path / "messages.jsonl"
    offsets = _write(path, records)
    reader = _reader(path)
    full_ids = _ids(records)
    budget = WindowBudget(min_turns=rng.randint(1, 3), max_records=rng.randint(5, 30),
                          max_bytes=rng.choice([2_000, 8_000, 50_000]))

    for k, offset in enumerate(offsets):
        page = await reader.read_before(encode_cursor(offset), budget)
        ids = _ids(page.records)
        if k == 0:
            assert ids == [] and page.has_more_before is False
            continue
        assert ids, f"empty page at k={k}"
        # contiguous slice immediately preceding k
        assert ids == full_ids[k - len(ids): k]
        start = k - len(ids)
        assert page.start_cursor == encode_cursor(offsets[start])
        assert page.has_more_before == (start > 0)
        if start > 0 and page.snapped:
            assert page.records[0]["type"] == "user"
            assert not page.records[0]["metadata"].get("has_tool_results")

    # walk backward from the tail: concatenation == full read
    window = await reader.read_tail(budget)
    collected = _ids(window.records)
    while window.has_more_before:
        window = await reader.read_before(window.start_cursor, budget)
        assert window.records
        collected = _ids(window.records) + collected
    assert collected == full_ids


@pytest.mark.asyncio
async def test_chunk_boundaries_do_not_corrupt_lines(tmp_path):
    """Records bigger than / straddling the 256 KiB read chunk are reassembled intact."""
    records = [_rec(i, "user" if i % 5 == 0 else "assistant",
                    pad=[10, CHUNK_SIZE - 150, CHUNK_SIZE + 77, 3 * CHUNK_SIZE][i % 4])
               for i in range(25)]
    path = tmp_path / "messages.jsonl"
    _write(path, records)
    reader = _reader(path)
    budget = WindowBudget(min_turns=1000, max_records=7, max_bytes=4 * 1024 * 1024)
    window = await reader.read_tail(budget)
    collected = _ids(window.records)
    while window.has_more_before:
        window = await reader.read_before(window.start_cursor, budget)
        collected = _ids(window.records) + collected
    assert collected == _ids(records)


# --------------------------------------------------------------------------- bytes read


@pytest.mark.asyncio
async def test_tail_bytes_read_bounded_by_budget_not_file_size(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(400, per_turn=5, pad=2000)  # ~4.1 MB, 2000 records
    _write(path, records)
    size = path.stat().st_size
    assert size > 3_000_000
    budget = WindowBudget(min_turns=2, max_bytes=64 * 1024)
    reader = _reader(path)
    window = await reader.read_tail(budget)
    assert len(window.records) == 10
    assert window.bytes_read <= budget.max_bytes + 3 * CHUNK_SIZE
    assert window.bytes_read < size / 3
    # a mid-file page reads proportionally too, independent of its position
    middle = await reader.read_before(window.start_cursor, build_budget(before=True, max_bytes=64 * 1024))
    assert middle.bytes_read <= 64 * 1024 + 3 * CHUNK_SIZE
    # latest_init scan on a file without any init is bounded by max_bytes
    assert await reader.scan_backward('"init"', 300_000) is None


# --------------------------------------------------------------------------- hazards


@pytest.mark.asyncio
@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\u0085", "\x1c", "\x0b", "\x0c", "\r"])
async def test_unicode_line_separators_inside_records(tmp_path, sep):
    path = tmp_path / "messages.jsonl"
    records = _conversation(4)
    records[5]["content"] = f"before{sep}after"
    records[6]["content"] = f"{sep}{sep}"
    _write(path, records)
    reader = _reader(path)
    window = await reader.read_tail(WindowBudget(min_turns=100, max_records=1000))
    assert _ids(window.records) == _ids(records)
    assert window.records[5]["content"] == f"before{sep}after"
    # iter_matching (forward) agrees
    got = list(reader.iter_matching(('"user"',)))
    assert _ids(got) == _ids([r for r in records if r["type"] == "user"])


@pytest.mark.asyncio
async def test_trailing_partial_line_is_ignored(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(3)
    _write(path, records)
    with open(path, "ab") as f:
        f.write(b'{"type": "assistant", "message_id": "partial", "content": "cut o')
    window = await _reader(path).read_tail(WindowBudget(min_turns=100, max_records=1000))
    assert _ids(window.records) == _ids(records)


@pytest.mark.asyncio
async def test_complete_last_line_without_newline_is_included(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(3)
    _write(path, records, trailing_newline=False)
    window = await _reader(path).read_tail(WindowBudget(min_turns=100, max_records=1000))
    assert _ids(window.records) == _ids(records)


@pytest.mark.asyncio
async def test_blank_lines_and_garbage_lines_are_skipped(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_bytes(
        b"\n" + json.dumps(_rec(0, "user")).encode() + b"\n\n   \nnot json\n"
        + json.dumps(_rec(1, "assistant")).encode() + b"\n"
    )
    window = await _reader(path).read_tail(WindowBudget(min_turns=100))
    assert _ids(window.records) == ["m0", "m1"]


# --------------------------------------------------------------------------- cursor safety


@pytest.mark.asyncio
async def test_cursor_mid_line_is_invalid(tmp_path):
    path = tmp_path / "messages.jsonl"
    offsets = _write(path, _conversation(3))
    reader = _reader(path)
    with pytest.raises(CursorInvalidError):
        await reader.read_before(encode_cursor(offsets[3] + 5), WindowBudget())
    with pytest.raises(CursorInvalidError):
        await reader.read_before(encode_cursor(path.stat().st_size + 10), WindowBudget())
    with pytest.raises(CursorInvalidError):
        await reader.read_before("garbage", WindowBudget())


@pytest.mark.asyncio
async def test_append_during_read_does_not_disturb_older_pages(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(6)
    _write(path, records)
    reader = _reader(path)
    first = await reader.read_tail(WindowBudget(min_turns=1))
    with open(path, "ab") as f:
        for i in range(100, 105):
            f.write(json.dumps(_rec(i, "assistant")).encode() + b"\n")
    older = await reader.read_before(first.start_cursor, WindowBudget(min_turns=1))
    assert _ids(older.records) == _ids(records[-8:-4])
    assert older.history_id == first.history_id
    again = await reader.read_tail(WindowBudget(min_turns=1))
    assert _ids(again.records)[-5:] == [f"m{i}" for i in range(100, 105)]
    assert again.history_id == first.history_id


@pytest.mark.asyncio
async def test_rewrite_changes_history_id(tmp_path):
    path = tmp_path / "messages.jsonl"
    _write(path, _conversation(3))
    reader = _reader(path)
    before = (await reader.read_tail(WindowBudget())).history_id
    # cleanup/reset rewrite: same inode (truncate in place), different first record
    new_records = [dict(r, message_id="new" + r["message_id"]) for r in _conversation(3)]
    with open(path, "r+b") as f:
        f.truncate(0)
    _write(path, new_records)
    after = (await reader.read_tail(WindowBudget())).history_id
    assert after != before
    # replaced file (new inode) with identical content also changes the id
    clone = tmp_path / "clone.jsonl"
    clone.write_bytes(path.read_bytes())
    path.unlink()
    clone.rename(path)
    assert (await reader.read_tail(WindowBudget())).history_id != after
    # truncation to empty then a fresh record changes it too
    with open(path, "r+b") as f:
        f.truncate(0)
    _write(path, [_rec(900, "user")])
    assert (await reader.read_tail(WindowBudget())).history_id != after


@pytest.mark.asyncio
async def test_cursor_after_truncation_is_rejected_or_stale_detectable(tmp_path):
    path = tmp_path / "messages.jsonl"
    _write(path, _conversation(6))
    reader = _reader(path)
    window = await reader.read_tail(WindowBudget(min_turns=1))
    with open(path, "r+b") as f:
        f.truncate(10)
    with pytest.raises(CursorInvalidError):
        await reader.read_before(window.start_cursor, WindowBudget())


# --------------------------------------------------------------------------- latest_init


def _init(n: int, tools: list[str]) -> dict:
    return _rec(n, "system", subtype="init",
                metadata={"subtype": "init", "init_data": {"type": "system", "tools": tools}})


@pytest.mark.asyncio
async def test_latest_init_found_not_found_and_bounded(tmp_path):
    path = tmp_path / "messages.jsonl"
    reader = _reader(path)
    path.write_bytes(b"")
    assert await find_latest_init(reader) is None

    records = [_init(0, ["A"]), *_conversation(2), _init(50, ["B"]), *[
        _rec(100 + i, "assistant", pad=20_000) for i in range(5)]]
    _write(path, records)
    assert (await find_latest_init(reader))["tools"] == ["B"]

    _write(path, [_init(0, ["A"])] + [_rec(i + 1, "assistant", pad=20_000) for i in range(30)])
    # init sits ~600 KB back: a 300 KB bound misses it, the default 8 MiB bound finds it
    assert await reader.scan_backward('"init"', 300_000) is None
    assert (await find_latest_init(reader))["tools"] == ["A"]

    # an init record without init_data is skipped in favour of an older one
    bad = _rec(60, "system", subtype="init", metadata={"subtype": "init"})
    _write(path, [_init(0, ["A"]), bad])
    assert (await find_latest_init(reader))["tools"] == ["A"]


@pytest.mark.asyncio
async def test_read_history_payload_shapes(tmp_path):
    path = tmp_path / "messages.jsonl"
    _write(path, [_init(0, ["A"]), *_conversation(5)])
    reader = _reader(path)
    tail = await read_history_payload(reader, before=None, budget=build_budget(before=False))
    assert set(tail) == {
        "messages", "start_cursor", "has_more_before", "history_id", "snapped",
        "total_bytes", "latest_init",
    }
    assert tail["latest_init"]["tools"] == ["A"]
    older = await read_history_payload(
        reader, before=tail["start_cursor"], budget=build_budget(before=True)
    )
    assert "latest_init" not in older
    assert older["messages"] and older["has_more_before"] is False  # init + earlier turns


def test_build_budget_clamps_and_defaults():
    tail = build_budget(before=False)
    assert (tail.min_turns, tail.max_records, tail.max_bytes) == (2, 400, 512 * 1024)
    older = build_budget(before=True)
    assert older.min_turns > 1000 and older.max_bytes == 256 * 1024
    big = build_budget(before=False, max_records=10**9, max_bytes=10**12, min_turns=0)
    assert big.max_records == 5000 and big.max_bytes == 4 * 1024 * 1024 and big.min_turns == 1


# --------------------------------------------------------------------------- iter_matching


def test_iter_matching_filters_without_parsing_other_lines(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_bytes(
        b'{"type": "system", "metadata": {"subtype": "task_started"}}\n'
        b"{broken json without the needle\n"
        b'{"type": "assistant", "content": "x"}\n'
        b'{"type": "system", "metadata": {"subtype": "task_updated"}}'  # no final newline
    )
    got = list(_reader(path).iter_matching(('"task_started"', '"task_updated"')))
    assert [r["metadata"]["subtype"] for r in got] == ["task_started", "task_updated"]
    assert list(_reader(tmp_path / "nope.jsonl").iter_matching(('"x"',))) == []


@pytest.mark.asyncio
async def test_before_page_is_strictly_bounded_even_without_a_user_turn(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = [_rec(0, "user")] + [_rec(i, "assistant", pad=1000) for i in range(1, 400)]
    offsets = _write(path, records)
    reader = _reader(path)
    page = await reader.read_before(
        encode_cursor(path.stat().st_size), build_budget(before=True, max_records=50)
    )
    assert len(page.records) == 50
    assert page.snapped is False and page.has_more_before is True
    assert page.start_cursor == encode_cursor(offsets[-50])
    # a tail window still guarantees the last turn regardless of the soft caps
    tail = await reader.read_tail(WindowBudget(min_turns=1, max_records=10, max_bytes=100))
    assert len(tail.records) == 400 and tail.snapped is True


@pytest.mark.asyncio
async def test_archive_reader_tolerates_legacy_type_records(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_bytes(json.dumps({"_type": "assistant", "message_id": "old"}).encode() + b"\n")
    reader = FileMessageHistoryReader(path, label="archive", strict_legacy=False)
    assert _ids((await reader.read_tail(WindowBudget())).records) == ["old"]


_CONTINUATION = (
    "This session is being continued from a previous conversation that ran out of context. "
    "The summary below covers the earlier portion of the conversation."
)


def _compaction_group(n: int, *, with_init: bool) -> list[dict]:
    """status, status(null), [init], compact_boundary, continuation — the serialized group."""
    sub = ["status", "status"] + (["init"] if with_init else []) + ["compact_boundary"]
    out = [
        _rec(n + i, "system", metadata={"subtype": s}) for i, s in enumerate(sub)
    ]
    out.append(_rec(n + len(sub), "user", content=_CONTINUATION + "\n\nSummary: ..."))
    return out


def _with_group(with_init: bool) -> tuple[list[dict], int]:
    """3 turns, a compaction group, then 3 more turns. Returns (records, group start index)."""
    before = _conversation(3)
    group = _compaction_group(len(before), with_init=with_init)
    after = [dict(r, message_id=f"a{r['message_id']}") for r in _conversation(3)]
    return before + group + after, len(before)


def test_continuation_record_is_not_a_turn_boundary():
    assert _is_turn_boundary(_rec(0, "user", content=_CONTINUATION + "\n\nSummary")) is False
    assert _is_turn_boundary(_rec(0, "user", content="hello")) is True
    # non-string content and a mid-string mention are unaffected
    assert _is_turn_boundary(_rec(0, "user", content=[{"type": "text", "text": "x"}])) is True
    assert _is_turn_boundary(_rec(0, "user", content="re: " + _CONTINUATION)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("with_init", [False, True])
async def test_issue_2030_tail_window_never_starts_at_continuation_record(tmp_path, with_init):
    path = tmp_path / "messages.jsonl"
    records, group_at = _with_group(with_init)
    _write(path, records)
    group_len = len(records) - group_at - 12
    cont_idx = group_at + group_len - 1
    # Window sized so the walk reaches the continuation record exactly at its turn target:
    # the records after it hold 3 turns, so min_turns=4 would stop on it pre-fix.
    window = await _reader(path).read_tail(WindowBudget(min_turns=4))
    assert window.records[0]["message_id"] != records[cont_idx]["message_id"]
    assert not str(window.records[0].get("content", "")).startswith(_CONTINUATION)
    # snapped to the previous real turn: the whole group is inside the window, unsplit
    assert _ids(window.records) == _ids(records[group_at - 4:])
    assert window.records[0]["type"] == "user" and window.snapped is True


@pytest.mark.asyncio
async def test_issue_2030_before_page_never_starts_at_continuation_record(tmp_path):
    path = tmp_path / "messages.jsonl"
    records, group_at = _with_group(with_init=True)
    offsets = _write(path, records)
    cont_idx = group_at + 4
    # Page ends right before the 3 trailing turns; target 1 turn so the walk would stop at
    # the continuation record, which now snaps to the preceding real turn.
    page = await _reader(path).read_before(
        encode_cursor(offsets[cont_idx + 1]), build_budget(before=True, min_turns=1)
    )
    assert page.records[0]["type"] == "user"
    assert not str(page.records[0].get("content", "")).startswith(_CONTINUATION)
    assert _ids(page.records) == _ids(records[group_at - 4 : cont_idx + 1])


@pytest.mark.asyncio
async def test_issue_2030_non_compaction_user_turns_unchanged(tmp_path):
    path = tmp_path / "messages.jsonl"
    records = _conversation(6)
    _write(path, records)
    window = await _reader(path).read_tail(WindowBudget(min_turns=2))
    assert _ids(window.records) == _ids(records[-8:])


@pytest.mark.asyncio
async def test_issue_2030_real_fixture_windows_never_start_at_continuation(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "raw" / "primary" / "messages.jsonl"
    reader = _reader(fixture)
    total = len(fixture.read_text().splitlines())
    saw_group = False
    for turns in range(1, 40):
        window = await reader.read_tail(WindowBudget(min_turns=turns, max_records=total))
        first = window.records[0]
        assert not str(first.get("content", "")).startswith(_CONTINUATION)
        saw_group = saw_group or any(
            str(r.get("content", "")).startswith(_CONTINUATION) for r in window.records
        )
    assert saw_group
