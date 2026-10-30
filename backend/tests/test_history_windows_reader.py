"""Issue #2030 (stage 3 PR 3A): ``read_around`` / ``read_after`` / ``resolve_message_id`` on the
file reader — windows are exact slices of the full read, pages tile the file, id lookups confirm by
parsing."""

from __future__ import annotations

import json
import random
import threading
from pathlib import Path

import pytest

from backend.message_history_reader import (
    AfterBudget,
    AroundBudget,
    CursorInvalidError,
    build_after_budget,
    build_around_budget,
    decode_cursor,
    encode_cursor,
)
from backend.tests.test_message_history_reader import (
    _CONTINUATION,
    _compaction_group,
    _conversation,
    _ids,
    _is_turn_boundary,
    _reader,
    _rec,
    _write,
)


def _boundary_idx(records: list[dict]) -> set[int]:
    return {i for i, r in enumerate(records) if _is_turn_boundary(r)}


def _setup(tmp_path: Path, records: list[dict]):
    path = tmp_path / "messages.jsonl"
    offsets = _write(path, records)
    return path, offsets, _reader(path)


def _span(window, offsets, total) -> tuple[int, int]:
    """(first record index, one past the last record index) of a window, from its cursors."""
    ends = [*offsets, total]
    return offsets.index(decode_cursor(window.start_cursor)), ends.index(
        decode_cursor(window.end_cursor)
    )


# --------------------------------------------------------------------------- around


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_around_window_is_a_slice_of_the_full_read_at_random_cursors(tmp_path, seed):
    rnd = random.Random(seed)
    records = _conversation(60, per_turn=rnd.choice([3, 4, 6]), pad=rnd.randint(0, 400))
    path, offsets, reader = _setup(tmp_path, records)
    total = path.stat().st_size
    boundaries = _boundary_idx(records)
    for _ in range(40):
        i = rnd.randrange(len(records))
        budget = AroundBudget(
            before_bytes=rnd.choice([0, 500, 4000, 20000]),
            after_bytes=rnd.choice([0, 500, 4000, 20000]),
        )
        w = await reader.read_around(encode_cursor(offsets[i]), budget)
        lo, hi = _span(w, offsets, total)
        assert _ids(w.records) == _ids(records[lo:hi])
        assert lo <= i < hi  # the anchor record is inside
        assert w.anchor_cursor == encode_cursor(offsets[i])
        assert (lo == 0) or (lo in boundaries) or not w.snapped
        assert w.has_more_before == (lo > 0)
        assert w.has_more_after == (hi < len(records))
        assert w.total_bytes == total
        # the forward half respects its budget (always at least the anchor record)
        fwd = sum(len(json.dumps(r, ensure_ascii=False).encode()) + 1 for r in records[i:hi])
        assert hi == i + 1 or fwd <= max(1, budget.after_bytes)
        # the backward half: whole turns within budget, or the anchor's own turn at least
        back = sum(len(json.dumps(r, ensure_ascii=False).encode()) + 1 for r in records[lo:i])
        if w.snapped and lo not in (0, i) and back > budget.before_bytes:
            # (a turn start is always reached; an anchor that starts a turn reaches the one above)
            reach = max((b for b in boundaries if b < i or (b == i and i not in boundaries)), default=0)
            assert lo == reach  # only that overshoot beyond the budget is allowed


@pytest.mark.asyncio
async def test_around_default_budget_and_file_edges(tmp_path):
    records = _conversation(30, pad=8000)
    path, offsets, reader = _setup(tmp_path, records)
    total = path.stat().st_size
    first = await reader.read_around(encode_cursor(0), AroundBudget())
    assert first.start_cursor == "b:0" and first.has_more_before is False
    assert first.has_more_after is True
    last = await reader.read_around(encode_cursor(offsets[-1]), AroundBudget())
    assert last.has_more_after is False and last.end_cursor == encode_cursor(total)
    assert last.records[-1]["message_id"] == records[-1]["message_id"]
    assert last.has_more_before is True
    # at EOF: nothing at/after the anchor
    eof = await reader.read_around(encode_cursor(total), AroundBudget(before_bytes=3000))
    assert eof.end_cursor == encode_cursor(total) and eof.has_more_after is False
    assert eof.records and eof.records[-1]["message_id"] == records[-1]["message_id"]


@pytest.mark.asyncio
async def test_around_mid_turn_anchor_includes_its_whole_turn(tmp_path):
    records = _conversation(10, per_turn=6, pad=100)
    path, offsets, reader = _setup(tmp_path, records)
    i = 6 * 4 + 3  # inside turn 4
    w = await reader.read_around(encode_cursor(offsets[i]), AroundBudget(before_bytes=10))
    assert w.records[0]["message_id"] == records[6 * 4]["message_id"]  # the turn's user record
    assert w.snapped is True


@pytest.mark.asyncio
async def test_around_anchor_on_turn_start_reaches_a_turn_start_above_it(tmp_path):
    records = _conversation(10, per_turn=6, pad=100)
    path, offsets, reader = _setup(tmp_path, records)
    i = 6 * 4
    w = await reader.read_around(encode_cursor(offsets[i]), AroundBudget(before_bytes=0))
    assert w.records[0]["message_id"] == records[i - 6]["message_id"]  # previous turn's user record
    assert w.snapped is True and w.has_more_before is True


@pytest.mark.asyncio
async def test_around_single_huge_record(tmp_path):
    records = [_rec(0, "user"), _rec(1, "assistant", pad=700_000), _rec(2, "user")]
    path, offsets, reader = _setup(tmp_path, records)
    w = await reader.read_around(encode_cursor(offsets[1]), AroundBudget())
    assert _ids(w.records) == ["m0", "m1"]  # the forward half stops after the over-budget record
    assert w.has_more_before is False and w.has_more_after is True


@pytest.mark.asyncio
async def test_around_hard_ceiling_cuts_unsnapped(tmp_path):
    records = [_rec(0, "user")] + [_rec(i, "assistant", pad=300_000) for i in range(1, 12)]
    path, offsets, reader = _setup(tmp_path, records)
    w = await reader.read_around(encode_cursor(offsets[10]), AroundBudget(before_bytes=1000))
    assert w.snapped is False and w.has_more_before is True
    lo, _ = _span(w, offsets, path.stat().st_size)
    assert 0 < lo < 10


@pytest.mark.asyncio
@pytest.mark.parametrize("with_init", [False, True])
async def test_around_never_starts_inside_a_compaction_group(tmp_path, with_init):
    before = _conversation(3)
    group = _compaction_group(len(before), with_init=with_init)
    after = [dict(r, message_id=f"a{r['message_id']}") for r in _conversation(3)]
    records = before + group + after
    path, offsets, reader = _setup(tmp_path, records)
    cont = len(before) + len(group) - 1
    # anchor on / right after the continuation record, tiny backward budget
    for i in (cont, cont + 1, cont + 2):
        w = await reader.read_around(encode_cursor(offsets[i]), AroundBudget(before_bytes=1))
        first = w.records[0]
        assert not str(first.get("content", "")).startswith(_CONTINUATION)
        lo, _ = _span(w, offsets, path.stat().st_size)
        assert lo <= len(before) - 4 + 0 or lo >= len(before) + len(group)  # never mid-group


@pytest.mark.asyncio
async def test_around_cursor_validation(tmp_path):
    records = _conversation(5)
    path, offsets, reader = _setup(tmp_path, records)
    total = path.stat().st_size
    for bad in (offsets[2] + 3, total + 1, 10**9):
        with pytest.raises(CursorInvalidError):
            await reader.read_around(encode_cursor(bad), AroundBudget())
    with pytest.raises(CursorInvalidError):
        await reader.read_around("junk", AroundBudget())
    with pytest.raises(CursorInvalidError):  # missing file: only offset 0 is a valid cursor
        await _reader(tmp_path / "nope.jsonl").read_around("b:5", AroundBudget())
    empty = await _reader(tmp_path / "nope.jsonl").read_around("b:0", AroundBudget())
    assert empty.records == [] and empty.has_more_after is False


@pytest.mark.asyncio
async def test_around_ignores_partial_trailing_line_and_reports_history_id_change(tmp_path):
    records = _conversation(4)
    path, offsets, reader = _setup(tmp_path, records)
    before_id = (await reader.read_around(encode_cursor(offsets[3]), AroundBudget())).history_id
    with open(path, "ab") as f:
        f.write(b'{"type": "assistant", "mess')  # writer mid-append
    w = await reader.read_around(encode_cursor(offsets[3]), AroundBudget())
    assert _ids(w.records) == _ids(records) and w.history_id == before_id
    path.unlink()
    _write(path, [_rec(99, "user")] + records)  # rewritten file: first record differs
    assert (await reader.read_around("b:0", AroundBudget())).history_id != before_id


@pytest.mark.asyncio
@pytest.mark.parametrize("sep", [" ", " ", "\u0085"])
async def test_around_handles_unicode_line_separators(tmp_path, sep):
    records = _conversation(6)
    records[7]["content"] = f"a{sep}b"
    path, offsets, reader = _setup(tmp_path, records)
    w = await reader.read_around(encode_cursor(offsets[7]), AroundBudget())
    assert _ids(w.records) == _ids(records)


@pytest.mark.asyncio
async def test_around_last_line_without_newline(tmp_path):
    records = _conversation(4)
    path = tmp_path / "messages.jsonl"
    offsets = _write(path, records, trailing_newline=False)
    w = await _reader(path).read_around(encode_cursor(offsets[-1]), AroundBudget())
    assert w.records[-1]["message_id"] == records[-1]["message_id"]
    assert w.end_cursor == encode_cursor(path.stat().st_size)


# --------------------------------------------------------------------------- after / tiling


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [700, 3000, 50_000])
async def test_after_pages_tile_the_file_forward(tmp_path, page):
    records = _conversation(30, pad=200)
    path, offsets, reader = _setup(tmp_path, records)
    seen, cursor, pages = [], "b:0", 0
    while True:
        w = await reader.read_after(cursor, AfterBudget(page))
        assert w.start_cursor == cursor
        seen.extend(_ids(w.records))
        pages += 1
        assert w.records or not w.has_more_after
        cursor = w.end_cursor
        if not w.has_more_after:
            break
    assert seen == _ids(records) and cursor == encode_cursor(path.stat().st_size)
    assert pages > 1 or page == 50_000


@pytest.mark.asyncio
async def test_around_plus_after_plus_before_tile_without_gap_or_overlap(tmp_path):
    from backend.message_history_reader import WindowBudget, build_budget

    records = _conversation(40, pad=150)
    path, offsets, reader = _setup(tmp_path, records)
    w = await reader.read_around(
        encode_cursor(offsets[80]), AroundBudget(before_bytes=3000, after_bytes=2000)
    )
    got = _ids(w.records)
    cursor = w.end_cursor
    while True:  # forward to EOF
        nxt = await reader.read_after(cursor, AfterBudget(4000))
        got = got + _ids(nxt.records)
        cursor = nxt.end_cursor
        if not nxt.has_more_after:
            break
    cursor = w.start_cursor
    while True:  # backward to the start
        prev = await reader.read_before(cursor, build_budget(before=True, max_bytes=4000))
        got = _ids(prev.records) + got
        cursor = prev.start_cursor
        if not prev.has_more_before:
            break
    assert got == _ids(records)
    assert isinstance(WindowBudget(), WindowBudget)


@pytest.mark.asyncio
async def test_after_until_stops_exactly(tmp_path):
    records = _conversation(20, pad=100)
    path, offsets, reader = _setup(tmp_path, records)
    for lo, hi in [(0, 5), (3, 4), (7, 40), (10, 10), (0, len(records) - 1)]:
        w = await reader.read_after(
            encode_cursor(offsets[lo]), AfterBudget(10**6), until=encode_cursor(offsets[hi])
        )
        assert _ids(w.records) == _ids(records[lo:hi]) and w.end_cursor == encode_cursor(offsets[hi])
        assert w.has_more_after is False  # the requested range is complete (``until`` reached)
    # until beyond the page budget: the page ends early at a record boundary, not at `until`
    w = await reader.read_after("b:0", AfterBudget(1500), until=encode_cursor(offsets[30]))
    assert decode_cursor(w.end_cursor) < offsets[30] and w.records and w.has_more_after is True
    # until == EOF
    total = path.stat().st_size
    w = await reader.read_after(
        encode_cursor(offsets[-2]), AfterBudget(10**6), until=encode_cursor(total)
    )
    assert _ids(w.records) == _ids(records[-2:]) and w.has_more_after is False


@pytest.mark.asyncio
async def test_after_validation(tmp_path):
    records = _conversation(5)
    path, offsets, reader = _setup(tmp_path, records)
    total = path.stat().st_size
    with pytest.raises(CursorInvalidError):
        await reader.read_after(encode_cursor(offsets[2] + 1), AfterBudget())
    with pytest.raises(CursorInvalidError):
        await reader.read_after(encode_cursor(total + 5), AfterBudget())
    with pytest.raises(CursorInvalidError):  # until before the cursor
        await reader.read_after(encode_cursor(offsets[3]), AfterBudget(), encode_cursor(offsets[1]))
    with pytest.raises(CursorInvalidError):  # until mid-line
        await reader.read_after(encode_cursor(offsets[1]), AfterBudget(), encode_cursor(offsets[3] + 2))
    with pytest.raises(CursorInvalidError):
        await reader.read_after("x", AfterBudget())
    at_eof = await reader.read_after(encode_cursor(total), AfterBudget())
    assert at_eof.records == [] and at_eof.has_more_after is False


@pytest.mark.asyncio
async def test_after_always_makes_progress_with_a_tiny_budget(tmp_path):
    records = [_rec(0, "user", pad=5000), _rec(1, "assistant", pad=5000)]
    path, offsets, reader = _setup(tmp_path, records)
    w = await reader.read_after("b:0", AfterBudget(1))
    assert _ids(w.records) == ["m0"] and w.has_more_after is True


def test_budget_builders_clamp():
    assert build_around_budget(None, None) == AroundBudget()
    b = build_around_budget(-5, 10**9)
    assert b.before_bytes == 0 and b.after_bytes == 1024 * 1024
    assert build_after_budget(None) == AfterBudget()
    assert build_after_budget(1).max_bytes == 1024
    assert build_after_budget(10**9).max_bytes == 4 * 1024 * 1024
    d = AroundBudget()
    assert d.before_bytes + d.after_bytes == 512 * 1024


# --------------------------------------------------------------------------- resolve


def _tool_conversation() -> list[dict]:
    return [
        _rec(0, "user", content="hello"),
        _rec(1, "assistant", metadata={"tool_uses": [{"id": "toolu_A", "name": "Read"}]}),
        _rec(2, "tool_call", tool_use_id="toolu_A", name="Read", content=""),
        _rec(3, "user", metadata={"has_tool_results": True}),
        _rec(4, "assistant", content='mentions "m9" and "toolu_A" in text'),
        _rec(5, "user", content="quote \"m4\" \\ and   sep"),
        _rec(6, "tool_call", tool_use_id="toolu_orphan", name="Bash", content=""),
        _rec(7, "assistant", id="legacy-id", message_id=None),
    ]


def test_resolve_message_id_found_not_found_and_content_lookalikes(tmp_path):
    records = _tool_conversation()
    path, offsets, reader = _setup(tmp_path, records)
    assert reader.resolve_message_id("m0") == encode_cursor(offsets[0])
    assert reader.resolve_message_id("m5") == encode_cursor(offsets[5])
    assert reader.resolve_message_id("m9") is None  # only inside record 4's content
    assert reader.resolve_message_id("m4") == encode_cursor(offsets[4])  # its own id wins
    assert reader.resolve_message_id("nope") is None
    assert reader.resolve_message_id("") is None
    assert reader.resolve_message_id('a"b') is None and reader.resolve_message_id("a\\b") is None
    assert reader.resolve_message_id("x" * 300) is None


def test_resolve_tool_use_id_prefers_the_launching_message(tmp_path):
    records = _tool_conversation()
    path, offsets, reader = _setup(tmp_path, records)
    assert reader.resolve_message_id("toolu_A") == encode_cursor(offsets[1])  # not the tool_call
    assert reader.resolve_message_id("toolu_orphan") == encode_cursor(offsets[6])  # fallback
    assert reader.resolve_message_id("m2") == encode_cursor(offsets[2])  # a tool_call's own id


def test_resolve_matches_legacy_id_field_and_first_duplicate(tmp_path):
    records = _tool_conversation() + [_rec(1, "user", content="dup of m1")]
    path, offsets, reader = _setup(tmp_path, records)
    assert reader.resolve_message_id("legacy-id") == encode_cursor(offsets[7])
    assert reader.resolve_message_id("m1") == encode_cursor(offsets[1])  # earliest wins


def test_resolve_across_chunk_boundaries_and_unterminated_tail(tmp_path):
    records = _conversation(400, pad=3000)  # > 1 MiB so ids straddle read batches
    path, offsets, reader = _setup(tmp_path, records)
    assert path.stat().st_size > 3 * 1024 * 1024
    for i in (0, 349, 350, 351, 399 * 4, len(records) - 1):
        assert reader.resolve_message_id(f"m{i}") == encode_cursor(offsets[i])
    path2 = tmp_path / "nonl.jsonl"
    offs2 = _write(path2, records[:5], trailing_newline=False)
    assert _reader(path2).resolve_message_id("m4") == encode_cursor(offs2[4])


def test_resolve_ids_are_ascii_and_missing_file_is_not_found(tmp_path):
    records = [_rec(0, "user", message_id="id-é-😀"), _rec(1, "assistant")]
    path, offsets, reader = _setup(tmp_path, records)
    assert reader.resolve_message_id("id-é-😀") is None  # (ids are uuids / toolu_*: ASCII only)
    assert reader.resolve_message_id("m1") == encode_cursor(offsets[1])
    assert _reader(tmp_path / "missing.jsonl").resolve_message_id("m0") is None


def test_resolve_stops_when_cancelled(tmp_path):
    records = _conversation(400, pad=3000)
    path, offsets, reader = _setup(tmp_path, records)
    cancel = threading.Event()
    cancel.set()
    assert reader.resolve_message_id(f"m{len(records) - 1}", cancel=cancel) is None


def test_resolve_finds_a_search_result_parent_id(tmp_path):
    """A search hit names its PARENT message; the id lookup finds that message (not the tool_call
    record whose cursor the hit carries)."""
    records = _tool_conversation()
    records[2]["name"] = "Read"
    records[2]["input"] = {"file": "needle.txt"}
    path, offsets, reader = _setup(tmp_path, records)
    page = reader.search("needle.txt", None, __import__("backend.history_search", fromlist=["x"]).SearchCaps())
    hit = page.results[0]
    assert hit["message_id"] == "m1"
    assert reader.resolve_message_id(hit["message_id"]) == encode_cursor(offsets[1])
    assert decode_cursor(hit["cursor"]) >= offsets[1]


def test_resolve_skips_a_legacy_format_candidate_instead_of_failing(tmp_path):
    records = [_rec(0, "user", _type="legacy", content="mentions m5 as \"m5\""), _rec(5, "assistant")]
    path, offsets, reader = _setup(tmp_path, records)
    assert reader.resolve_message_id("m5") == encode_cursor(offsets[1])


def test_resolve_ignores_lookalikes_in_content_with_escaped_quotes_and_line_separators(tmp_path):
    lookalike = 'text \\"m7\\" and "m7" and \u2028"m7"\u2029 and {"message_id": "m7"}'
    records = [
        _rec(0, "user", content=lookalike),
        _rec(1, "assistant", content=lookalike, metadata={"note": {"message_id": "m7"}}),
        _rec(7, "assistant"),
    ]
    path, offsets, reader = _setup(tmp_path, records)
    assert reader.resolve_message_id("m7") == encode_cursor(offsets[2])  # top-level fields only


def test_tool_use_id_returns_the_parent_in_either_file_order(tmp_path):
    parent = _rec(1, "assistant", metadata={"tool_uses": [{"id": "toolu_X", "name": "Read"}]})
    call = _rec(2, "tool_call", tool_use_id="toolu_X", name="Read", content="")
    # normal order: launching message first
    path, offsets, reader = _setup(tmp_path, [_rec(0, "user"), parent, call])
    assert reader.lookup_id("toolu_X") == (encode_cursor(offsets[1]), False)
    # tool_call written before its parent (documented 3B limit): the bounded parent scan finds it
    sub = tmp_path / "rev"
    sub.mkdir()
    path, offsets, reader = _setup(sub, [_rec(0, "user"), call, parent])
    assert reader.lookup_id("toolu_X") == (encode_cursor(offsets[2]), False)
    assert reader.resolve_message_id("toolu_X") == encode_cursor(offsets[2])


def test_tool_use_id_without_a_parent_falls_back_flagged(tmp_path):
    call = _rec(2, "tool_call", tool_use_id="toolu_Y", name="Read", content="")
    path, offsets, reader = _setup(tmp_path, [_rec(0, "user"), call, _rec(3, "assistant")])
    assert reader.lookup_id("toolu_Y") == (encode_cursor(offsets[1]), True)


def test_parent_scan_is_bounded(tmp_path, monkeypatch):
    import backend.message_history_reader as mhr

    monkeypatch.setattr(mhr, "PARENT_SCAN_BYTES", 3 * 1024 * 1024)
    call = _rec(2, "tool_call", tool_use_id="toolu_Z", name="Read", content="")
    filler = [_rec(10 + i, "assistant", pad=3000) for i in range(2500)]  # ~7.5 MB
    parent = _rec(1, "assistant", metadata={"tool_uses": [{"id": "toolu_Z", "name": "Read"}]})
    path, offsets, reader = _setup(tmp_path, [call, *filler, parent])
    found = reader.lookup_id("toolu_Z")
    assert found == (encode_cursor(0), True)  # parent beyond the bound: tool_call, flagged


def test_tool_call_on_an_unterminated_last_line_is_the_flagged_fallback(tmp_path):
    path = tmp_path / "messages.jsonl"
    call = _rec(2, "tool_call", tool_use_id="toolu_W", name="Read", content="")
    offsets = _write(path, [_rec(0, "user"), call], trailing_newline=False)
    assert _reader(path).lookup_id("toolu_W") == (encode_cursor(offsets[1]), True)
