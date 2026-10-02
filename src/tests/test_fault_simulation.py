"""Fault-simulation harness tests (issue #1999, US2/AC3/AC4).

Drives the real EventQueue/PollRelay code (src/tests/simulation/fault_harness.py)
through AC4's four named fault types plus the "none" baseline, injecting each
against a recorded fixture's queue_event stream as payload, and asserts
exactly-once delivery.

Payload comes from backend/tests/fixtures/raw/ — read directly as plain JSON
files (never imported as Python), since src/ is structurally forbidden from
importing backend/ (see src/tests/test_import_boundary.py).
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from src.tests.simulation.fault_harness import (
    _DESTRUCTIVE_FAULTS,
    FAULT_TYPES,
    BrowserClient,
    FakeBackend,
    FakeFrontend,
    resolve_delivered_seqs,
    run_fault_scenario,
    session_path,
    wait_until,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW_FIXTURES_ROOT = _REPO_ROOT / "backend" / "tests" / "fixtures" / "raw"


@pytest.fixture(autouse=True)
def _fast_poll_timing(monkeypatch):
    """Scoped-down determinism pattern (see PLAN_1999's Risks section, and
    src/tests/test_poll_relay.py's existing use of the same technique):
    PollRelay's own upstream long-poll timeout defaults to 30s
    (src/poll_relay.py's _POLL_TIMEOUT_SECONDS) — far longer than this test
    file's settle budget. Shrinking it (and the client-side margin above it,
    and the error backoff) makes a relay recovering from a stale/orphaned
    queue reference (e.g. after backend_restart) resolve in well under a
    second instead of up to 30s of real wall-clock time."""
    monkeypatch.setattr("src.poll_relay._POLL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("src.poll_relay._POLL_CLIENT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr("src.poll_relay._ERROR_BACKOFF_SECONDS", 0.02)


def _list_raw_fixture_names() -> list[str]:
    """Every raw-fixture directory with a raw_log.jsonl — discovered dynamically,
    not hardcoded. Raises (does not return an empty list) if none are found, so
    a misconfigured checkout fails this suite loudly rather than silently
    reporting zero tests (mirrors US1's AC1 fail-not-skip guard)."""
    if not _RAW_FIXTURES_ROOT.exists():
        raise RuntimeError(f"Raw fixtures directory not found: {_RAW_FIXTURES_ROOT}")
    names = sorted(
        p.name for p in _RAW_FIXTURES_ROOT.iterdir()
        if p.is_dir() and (p / "raw_log.jsonl").exists()
    )
    if not names:
        raise RuntimeError(f"No raw fixtures with raw_log.jsonl found under {_RAW_FIXTURES_ROOT}")
    return names


def _load_queue_events(name: str) -> list[dict]:
    raw_log_path = _RAW_FIXTURES_ROOT / name / "raw_log.jsonl"
    events = []
    for line in raw_log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("kind") == "queue_event":
            events.append(record["event"])
    if not events:
        raise RuntimeError(f"No queue_event records found in {raw_log_path}")
    return events


# One fixture is enough payload for the transport-layer fault matrix (unlike US1's
# equivalence harness, this isn't validating message-pipeline content — just that
# the queue/relay layer delivers whatever payload it's given exactly once under
# each fault). Uses the first discovered fixture so it still fails loudly (via
# _list_raw_fixture_names()) rather than silently skipping if none exist.
_FIXTURE_NAME = _list_raw_fixture_names()[0]
_EVENTS = _load_queue_events(_FIXTURE_NAME)
# Bounded slice: the fault matrix runs many scenarios: full-fixture replay (~3800
# events) per scenario would make the suite slow without adding coverage — the
# transport layer doesn't care about payload content, only volume/ordering, and
# US5 separately covers the 20,000+-event scale case.
_SAMPLE_EVENTS = _EVENTS[:200]


@pytest.mark.asyncio
async def test_fixture_discovery_is_dynamic_and_fails_loudly(monkeypatch):
    """AC3's fail-not-skip guard, mirrored from US1: an empty/missing raw fixtures
    directory must raise, not silently produce zero scenarios."""
    monkeypatch.setattr(
        "src.tests.test_fault_simulation._RAW_FIXTURES_ROOT", _REPO_ROOT / "does-not-exist"
    )
    with pytest.raises(RuntimeError, match="not found"):
        _list_raw_fixture_names()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", FAULT_TYPES)
async def test_fault_matrix_delivers_exactly_once(fault):
    """T2 (AC3/AC4): the fault-interleaving matrix. For every named fault type
    (plus the "none" baseline): no event is ever delivered more than once, and
    the transport has recovered by the tail of the stream — every event
    appended once things are well past the fault point is eventually
    delivered. (A destructive fault may still legitimately drop events that
    were genuinely in flight at the exact moment it fired — see
    test_non_destructive_faults_lose_nothing for the stronger "nothing lost
    at all" property, which only holds for the non-destructive fault types.)
    """
    result = await run_fault_scenario(fault, _SAMPLE_EVENTS)
    client = result["client"]

    received = client.received
    deduped = client.received_deduped()
    assert len(received) == len(deduped), (
        f"fault={fault}: {len(received) - len(deduped)} event(s) delivered more than once"
    )

    delivered_seqs = resolve_delivered_seqs(deduped, result["seq_identity"])
    missing_tail = set(result["tail_seqs"]) - delivered_seqs
    assert not missing_tail, (
        f"fault={fault}: {len(missing_tail)} tail event(s) never delivered — transport did not recover"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", sorted(set(FAULT_TYPES) - _DESTRUCTIVE_FAULTS))
async def test_non_destructive_faults_lose_nothing(fault):
    """AC4: frontend_restart and freeze (and the "none" baseline) must not lose
    any pre-fault history either — only backend_restart/eviction are allowed to
    (the same real-world tradeoff #1889 documents: a Backend restart or buffer
    eviction genuinely discards undelivered history; a Frontend restart or a
    frozen tab must not)."""
    result = await run_fault_scenario(fault, _SAMPLE_EVENTS)
    client = result["client"]

    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    missing = set(result["expected_seqs"]) - delivered_seqs
    assert not missing, f"fault={fault}: {len(missing)} event(s) lost despite being non-destructive"


@pytest.mark.asyncio
async def test_backend_restart_is_visible_as_a_reset_to_the_browser():
    """AC4: a Backend restart mid-poll produces a lower cursor than the client
    last saw — EventQueue.append()'s reset branch (shared/event_queue.py) — and
    the browser client observes at least one reset signal, distinct from a
    plain connection error."""
    result = await run_fault_scenario("backend_restart", _SAMPLE_EVENTS)
    assert result["client"].reset_count >= 1


@pytest.mark.asyncio
async def test_eviction_is_visible_to_the_browser():
    """AC4: forcing Backend's retained window down below what the client still
    needs must surface as an explicit evicted signal (not a silent gap)."""
    result = await run_fault_scenario("eviction", _SAMPLE_EVENTS, fault_at_fraction=0.1)
    assert result["client"].evicted_count >= 1 or result["client"].reset_count >= 1


@pytest.mark.asyncio
async def test_freeze_client_resumes_with_no_loss_and_no_duplicates():
    """AC4: a paused (frozen-tab) browser client simply stops polling — once
    resumed, it must catch up from its own last cursor with no gap and no
    redelivery, since nothing about the queue/relay itself changed while it
    was paused."""
    result = await run_fault_scenario("freeze", _SAMPLE_EVENTS)
    client = result["client"]
    assert len(client.received) == len(client.received_deduped())
    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    assert set(result["expected_seqs"]) <= delivered_seqs


@pytest.mark.slow
@pytest.mark.asyncio
async def test_scale_fixture_completes_in_bounded_time_with_no_event_loss():
    """US5 (edge case, not a numbered AC): a 20,000+-event synthetic fixture,
    generated at test run time (never committed — see
    backend/tests/fixtures/scale_fixture.py for the reusable generator
    counterpart), confirms bounded run time and no event loss through the
    fault harness. Performance and no-loss only, no content assertions, per
    the issue's own scoping.

    Builds its own scaled event list here (a plain cycle of the source
    fixture's events) rather than importing backend.tests.fixtures.
    scale_fixture — src/ is structurally forbidden from importing backend/
    (see src/tests/test_import_boundary.py). Unlike that module (which
    rewrites identity fields for consumers that dedup by content, e.g. the
    frontend's message_id-keyed store), this harness's own exactly-once
    tracking is purely cursor/seq-based (see BrowserClient.received_deduped()
    and run_fault_scenario()'s `seq_identity` out-of-band identity map) —
    verbatim repetition is sufficient here.
    """
    target_count = 20_000
    scaled_events = [
        _EVENTS[i % len(_EVENTS)] for i in range(target_count)
    ]

    start = time.monotonic()
    result = await run_fault_scenario("none", scaled_events, settle_timeout=60.0)
    elapsed = time.monotonic() - start

    client = result["client"]
    assert len(client.received) == len(client.received_deduped()), "duplicate delivery at scale"
    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    assert set(result["expected_seqs"]) <= delivered_seqs, "event loss at scale"

    # Bounded, not tight: this is a regression guard against the harness going
    # quadratic or otherwise pathological at scale, not a strict perf budget.
    assert elapsed < 45.0, f"scale scenario took {elapsed:.1f}s — expected well under 45s"


@pytest.mark.asyncio
async def test_backend_down_recovers_with_no_loss():
    """AC6: a Backend that stops answering polls entirely (connection refused)
    must not lose any events — once it comes back up, the relay catches up
    from exactly where it left off. First confirms go_down() genuinely
    produces a connection failure directly against FakeBackend (BrowserClient
    itself never observes this: FakeFrontend/PollRelay mediates and retries
    internally, catching httpx.RequestError in its own loop — the same
    shielding a real browser gets from polling.js never seeing a raw
    connection error either), then confirms the full scenario loses nothing.
    """
    probe_backend = FakeBackend()
    probe_backend.go_down()
    with pytest.raises(httpx.ConnectError):
        await probe_backend.get_json(session_path("probe-session"))

    result = await run_fault_scenario("backend_down", _SAMPLE_EVENTS)
    client = result["client"]

    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    missing = set(result["expected_seqs"]) - delivered_seqs
    assert not missing, f"backend_down: {len(missing)} event(s) lost despite recovery"
    assert len(client.received) == len(client.received_deduped())


@pytest.mark.asyncio
async def test_slow_ready_recovers_with_no_loss_and_no_spurious_signal():
    """AC6: a Backend that's up but slow to answer (e.g. mid-startup) must not
    lose any events and must not raise a spurious reset/evicted signal — only
    timing is affected, nothing about queue state changed."""
    result = await run_fault_scenario("slow_ready", _SAMPLE_EVENTS)
    client = result["client"]

    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    missing = set(result["expected_seqs"]) - delivered_seqs
    assert not missing, f"slow_ready: {len(missing)} event(s) lost despite recovery"
    assert len(client.received) == len(client.received_deduped())
    assert client.reset_count == 0
    assert client.evicted_count == 0


@pytest.mark.asyncio
async def test_eviction_during_backend_restart_surfaces_both_signals():
    """AC6: the combined fault (a Backend restart immediately followed by an
    eviction, no yield in between) exercises both hazards compounding — fresh
    cursor space AND a tiny retained window. Mirrors
    test_backend_restart_is_visible_as_a_reset_to_the_browser/
    test_eviction_is_visible_to_the_browser's style: at least one of the two
    signals must surface, never a silent gap.

    fault_at_fraction=0.15 (not 0.1, unlike the plain "eviction" test):
    0.1 puts the fault at exactly 20 pre-fault events — the same value as
    _EVICTION_BURST_LEN, a coincidental round-number match that (like the
    collision item 4's dedicated test deliberately engineers for
    backend_restart alone) can land the post-restart burst exactly on the
    relay's stale cursor and mask the signal despite genuine data loss.
    0.15 avoids that coincidence.
    """
    result = await run_fault_scenario(
        "eviction_during_backend_restart", _SAMPLE_EVENTS, fault_at_fraction=0.15
    )
    client = result["client"]
    assert client.reset_count >= 1 or client.evicted_count >= 1


@pytest.mark.asyncio
async def test_frontend_restart_reseeds_from_backends_real_cursor_space():
    """US2's Technical Approach: FakeFrontend.restart() discards PollRelay/local
    EventQueue and rebuilds against the SAME, unaffected FakeBackend — the
    browser client's own cursor state survives the restart (as a real browser's
    Pinia state would, being unaffected by a server-side process restart), and
    its next poll resumes seamlessly from Backend's still-intact history."""
    result = await run_fault_scenario("frontend_restart", _SAMPLE_EVENTS)
    client = result["client"]
    delivered_seqs = resolve_delivered_seqs(client.received_deduped(), result["seq_identity"])
    assert set(result["expected_seqs"]) <= delivered_seqs
    assert len(client.received) == len(client.received_deduped())


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="epic #1990 stage 5: EventQueue has no epoch/generation concept, so a same-valued cursor across a Backend-restart boundary reads as 'already caught up' instead of signaling a new epoch, silently dropping the colliding event(s) with no reset signal — fix is EventQueue epoch/generation tracking, out of scope until stage 5",
)
async def test_cursor_collision_silently_drops_colliding_post_restart_events():
    """AC6 item 4: deliberately engineers the exact "round event counts"
    cursor collision that `_inject_fault()`'s `backend_restart` branch's sleep
    workaround exists to avoid — bypassing `run_fault_scenario()`'s general
    helper (and that sleep) entirely, constructing the harness directly.

    With NO settle delay after `backend.restart()`, a post-restart burst
    sized to exactly match the client's already-observed pre-restart cursor
    recreates the collision: the relay's stale `since` equals the fresh
    queue's current cursor once the burst completes, so `events_since()`
    reads "already caught up" rather than "these are N entirely different
    events from a new epoch" — silently dropping every colliding event with
    no reset/evicted signal.

    This asserts the CORRECT behavior (every post-restart event eventually
    delivered, or at minimum an explicit reset/evicted signal raised for the
    gap) — which fails today because of the hazard above, giving the
    intended "xfail" result. `strict=True` means the day `EventQueue` gains
    epoch/generation tracking (stage 5) and this assertion starts passing,
    the test flips to an unexpected xpass and fails the suite, forcing this
    marker's removal instead of letting it go stale.
    """
    session_id = "cursor-collision-session"
    backend = FakeBackend()
    frontend = FakeFrontend(backend)
    client = BrowserClient(frontend, session_id=session_id)

    stop_event = asyncio.Event()
    poll_task = asyncio.create_task(client.run_until(stop_event, poll_timeout=0.05))

    pre_restart_count = 10
    for i in range(pre_restart_count):
        backend.queue_for(session_id).append({"phase": "pre", "i": i})
        await asyncio.sleep(0)  # let the relay observe each pre-restart append promptly

    await wait_until(lambda: len(client.received) >= pre_restart_count, timeout=2.0, interval=0.01)
    assert len(client.received) == pre_restart_count, "setup failed: client never caught up pre-restart"

    # The collision itself: restart with no settle delay, then a same-sized
    # burst with no yield in between — the fresh queue's cursor lands on
    # exactly the value the relay's stale `since` already holds.
    backend.restart()
    for i in range(pre_restart_count):
        backend.queue_for(session_id).append({"phase": "post", "i": i})

    # Fixed wait, not a predicate loop: this is the collision itself under test — there
    # is no "done" condition to poll for, since the whole point is that nothing should
    # (but today, nothing does) visibly happen for these colliding events.
    await asyncio.sleep(2.0)

    stop_event.set()
    await poll_task
    await frontend.stop()

    post_restart_received = [e for _, e in client.received if e.data.get("phase") == "post"]
    assert (
        len(post_restart_received) == pre_restart_count
        or client.reset_count >= 1
        or client.evicted_count >= 1
    )
