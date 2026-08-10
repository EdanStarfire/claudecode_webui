"""Unit tests for RFC 7233 single-range parsing (issue #1716)."""

import pytest

from ..http_range import parse_range_header


class TestParseRangeHeader:
    def test_full_range(self):
        assert parse_range_header("bytes=0-99", 100) == (0, 99)

    def test_mid_range(self):
        assert parse_range_header("bytes=10-19", 100) == (10, 19)

    def test_open_ended_range(self):
        assert parse_range_header("bytes=50-", 100) == (50, 99)

    def test_suffix_range(self):
        assert parse_range_header("bytes=-10", 100) == (90, 99)

    def test_suffix_range_larger_than_file_clamps_to_start(self):
        assert parse_range_header("bytes=-1000", 100) == (0, 99)

    def test_single_byte_range(self):
        assert parse_range_header("bytes=0-0", 100) == (0, 0)

    def test_range_at_eof(self):
        assert parse_range_header("bytes=99-99", 100) == (99, 99)

    def test_end_beyond_file_size_is_clamped(self):
        assert parse_range_header("bytes=0-999", 100) == (0, 99)

    def test_first_range_of_multi_range_used(self):
        assert parse_range_header("bytes=0-9,20-29", 100) == (0, 9)

    @pytest.mark.parametrize(
        "header",
        [
            "bytes=-",
            "bytes=abc-def",
            "bytes=10",
            "items=0-99",
            "bytes=-0",
        ],
    )
    def test_malformed_spec_raises(self, header):
        with pytest.raises(ValueError):
            parse_range_header(header, 100)

    def test_reversed_range_raises(self):
        with pytest.raises(ValueError):
            parse_range_header("bytes=50-10", 100)

    def test_negative_start_raises(self):
        with pytest.raises(ValueError):
            parse_range_header("bytes=-5-10", 100)

    def test_start_at_or_beyond_file_size_raises(self):
        with pytest.raises(ValueError):
            parse_range_header("bytes=100-199", 100)

    def test_empty_file_raises(self):
        with pytest.raises(ValueError):
            parse_range_header("bytes=0-0", 0)
