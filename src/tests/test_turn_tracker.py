"""Tests for TurnTracker."""
from src.analytics.turn_tracker import TurnTracker


def test_open_and_close_turn():
    t = TurnTracker()
    turn_id = t.on_user_message("s1")
    assert turn_id == "s1:1"
    assert t.current_turn_id("s1") == "s1:1"
    closed = t.on_result("s1")
    assert closed == "s1:1"
    assert t.current_turn_id("s1") is None


def test_sequential_turns():
    t = TurnTracker()
    t1 = t.on_user_message("s1")
    t.on_result("s1")
    t2 = t.on_user_message("s1")
    assert t1 == "s1:1"
    assert t2 == "s1:2"


def test_multi_session_isolation():
    t = TurnTracker()
    t.on_user_message("s1")
    t.on_user_message("s2")
    assert t.current_turn_id("s1") == "s1:1"
    assert t.current_turn_id("s2") == "s2:1"
    t.on_result("s1")
    assert t.current_turn_id("s1") is None
    assert t.current_turn_id("s2") == "s2:1"


def test_result_without_open_turn():
    t = TurnTracker()
    closed = t.on_result("s1")
    assert closed is None


def test_clear_session():
    t = TurnTracker()
    t.on_user_message("s1")
    t.clear_session("s1")
    assert t.current_turn_id("s1") is None
    # Counter resets
    turn_id = t.on_user_message("s1")
    assert turn_id == "s1:1"


def test_current_turn_id_no_turn():
    t = TurnTracker()
    assert t.current_turn_id("unknown") is None
