"""Public monitor row: the 3d-i progress fields are plain numbers, never content."""

from backend.relay.inbound.public import public_monitor


def row(**extra):
    rec = {"id": "m1", "kind": "monitor_channel", "session_id": "s1", "status": "active",
           "recent_keys": ["k1"], **extra}
    return public_monitor(rec, session_name="S", handle="h", channel_label="#c", user_labels={})


def test_progress_fields_present_and_numeric():
    out = row(last_seen_ts="1800000000.000001", last_activity_at=1800000001.5, failures=2, flood_windows=1)
    assert out["last_seen_ts"] == "1800000000.000001" and out["last_activity_at"] == 1800000001.5
    assert out["failures"] == 2 and out["flood_windows"] == 1


def test_defaults_and_no_delivered_keys():
    out = row()
    assert out["last_seen_ts"] is None and out["last_activity_at"] is None
    assert out["failures"] == 0 and out["flood_windows"] == 0
    assert "recent_keys" not in out
