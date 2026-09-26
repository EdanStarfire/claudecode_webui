"""Tests for shared/raw_log_rotator.py (issue #2027, T1)."""

from shared.raw_log_rotator import RotatingRawLogWriter


def test_write_below_cap_does_not_rotate(tmp_path):
    path = tmp_path / "combined.log"
    writer = RotatingRawLogWriter(path, max_bytes=1024, backup_count=3)

    writer.write(b"hello")

    assert path.read_bytes() == b"hello"
    assert not (tmp_path / "combined.log.1").exists()


def test_write_past_cap_rotates_current_file_to_backup_1(tmp_path):
    path = tmp_path / "combined.log"
    writer = RotatingRawLogWriter(path, max_bytes=10, backup_count=3)

    writer.write(b"0123456789ABCDEF")  # 16 bytes, past the 10-byte cap

    assert not path.exists() or path.read_bytes() == b""
    assert (tmp_path / "combined.log.1").read_bytes() == b"0123456789ABCDEF"


def test_rotation_bounds_backup_count(tmp_path):
    path = tmp_path / "combined.log"
    writer = RotatingRawLogWriter(path, max_bytes=5, backup_count=2)

    for chunk in [b"aaaaaa", b"bbbbbb", b"cccccc"]:
        writer.write(chunk)

    assert not (tmp_path / "combined.log.3").exists()
    assert (tmp_path / "combined.log.1").exists()
    assert (tmp_path / "combined.log.2").exists()


def test_rotate_if_oversized_handles_pre_existing_large_file(tmp_path):
    path = tmp_path / "combined.log"
    path.write_bytes(b"x" * 100)
    writer = RotatingRawLogWriter(path, max_bytes=10, backup_count=3)

    writer.rotate_if_oversized()

    assert not path.exists() or path.read_bytes() == b""
    assert (tmp_path / "combined.log.1").read_bytes() == b"x" * 100


def test_rotate_if_oversized_noop_when_under_cap(tmp_path):
    path = tmp_path / "combined.log"
    path.write_bytes(b"small")
    writer = RotatingRawLogWriter(path, max_bytes=1024, backup_count=3)

    writer.rotate_if_oversized()

    assert path.read_bytes() == b"small"
    assert not (tmp_path / "combined.log.1").exists()


def test_no_data_lost_across_rollover_boundary(tmp_path):
    """AC2: rotation never drops output. Write many chunks past the cap, then
    concatenate every rotation file back in chronological order and diff
    against everything that was ever written."""
    path = tmp_path / "combined.log"
    backup_count = 4
    writer = RotatingRawLogWriter(path, max_bytes=50, backup_count=backup_count)

    all_written = b""
    for i in range(20):
        chunk = f"line-{i:03d}\n".encode()
        writer.write(chunk)
        all_written += chunk

    # Oldest surviving generation first, newest (active file) last.
    reconstructed = b""
    for n in range(backup_count, 0, -1):
        backup = tmp_path / f"combined.log.{n}"
        if backup.exists():
            reconstructed += backup.read_bytes()
    if path.exists():
        reconstructed += path.read_bytes()

    # Total retained data is bounded by backup_count * max_bytes, so only the
    # tail of what was written is guaranteed to survive — but that tail must
    # be an exact, uninterrupted suffix of everything written (no gaps, no
    # duplication, no reordering).
    assert all_written.endswith(reconstructed)
    assert reconstructed == all_written[len(all_written) - len(reconstructed):]
