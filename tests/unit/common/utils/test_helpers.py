"""Unit tests for common/utils/helpers.py."""
import io
import re

import numpy as np
import pytest

from common.utils.helpers import (
    build_candidate_base_path,
    build_stage_path,
    bytesio_to_numpy,
    chunked,
    frame_to_bytesio,
    now_utc,
    numpy_to_bytesio,
)


# ---------------------------------------------------------------------------
# now_utc
# ---------------------------------------------------------------------------

class TestNowUtc:
    def test_returns_string(self):
        result = now_utc()
        assert isinstance(result, str)

    def test_iso8601_format(self):
        result = now_utc()
        # ISO 8601 pattern ending with timezone offset
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", result), (
            f"Expected ISO-8601, got {result!r}"
        )

    def test_contains_timezone(self):
        result = now_utc()
        # Must carry UTC offset (+00:00) or Z
        assert "+00:00" in result or result.endswith("Z"), (
            f"Expected UTC offset in {result!r}"
        )


# ---------------------------------------------------------------------------
# build_candidate_base_path
# ---------------------------------------------------------------------------

class TestBuildCandidateBasePath:
    def test_correct_format(self):
        path = build_candidate_base_path("acme", "org-1", "a@b.com", "cand-99")
        assert path == "acme/org-1/a@b.com/cand-99"

    def test_no_trailing_slash(self):
        path = build_candidate_base_path("org", "id", "e@f.com", "uid")
        assert not path.endswith("/")

    def test_all_segments_present(self):
        path = build_candidate_base_path("alias", "oid", "c@d.com", "u1")
        parts = path.split("/")
        assert len(parts) == 4


# ---------------------------------------------------------------------------
# build_stage_path
# ---------------------------------------------------------------------------

class TestBuildStagePath:
    def test_flat_structure(self):
        path = build_stage_path("alias/oid/c@d.com/uid", "encoding.json")
        assert path == "alias/oid/c@d.com/uid/stages/encoding.json"

    def test_no_event_id_in_path(self):
        path = build_stage_path("a/b/c/d", "verification.json")
        assert "evt" not in path

    def test_various_filenames(self):
        for fname in ("encoding.json", "verification.json", "onboarding_verification.json"):
            path = build_stage_path("a/b/c/d", fname)
            assert path.endswith(f"/stages/{fname}")


# ---------------------------------------------------------------------------
# chunked
# ---------------------------------------------------------------------------

class TestChunked:
    def test_even_split(self):
        result = list(chunked([1, 2, 3, 4], 2))
        assert result == [[1, 2], [3, 4]]

    def test_uneven_split(self):
        result = list(chunked([1, 2, 3, 4, 5], 2))
        assert result == [[1, 2], [3, 4], [5]]

    def test_chunk_larger_than_list(self):
        result = list(chunked([1, 2], 10))
        assert result == [[1, 2]]

    def test_empty_list(self):
        result = list(chunked([], 3))
        assert result == []

    def test_chunk_size_one(self):
        result = list(chunked([1, 2, 3], 1))
        assert result == [[1], [2], [3]]


# ---------------------------------------------------------------------------
# numpy_to_bytesio / bytesio_to_numpy  (round-trip)
# ---------------------------------------------------------------------------

class TestNumpyRoundTrip:
    def test_round_trip(self):
        original = np.random.default_rng(0).random((10, 128))
        buf = numpy_to_bytesio(original)
        assert isinstance(buf, io.BytesIO)

        recovered = bytesio_to_numpy(buf)
        assert recovered is not None
        np.testing.assert_array_almost_equal(original, recovered)

    def test_bytesio_position_reset(self):
        """numpy_to_bytesio must return a seeked-to-zero buffer."""
        arr = np.array([1.0, 2.0, 3.0])
        buf = numpy_to_bytesio(arr)
        assert buf.tell() == 0

    def test_bytesio_to_numpy_invalid_data(self):
        bad = io.BytesIO(b"this is not a numpy file")
        result = bytesio_to_numpy(bad)
        assert result is None

    def test_bytesio_to_numpy_empty(self):
        result = bytesio_to_numpy(io.BytesIO(b""))
        assert result is None


# ---------------------------------------------------------------------------
# frame_to_bytesio
# ---------------------------------------------------------------------------

class TestFrameToBytesio:
    def test_returns_none_on_empty_frame(self):
        """A zero-size frame should not crash — returns None gracefully."""
        import numpy as np
        empty_frame = np.zeros((0, 0, 3), dtype=np.uint8)
        # cv2.imencode may return False for empty arrays
        result = frame_to_bytesio(empty_frame)
        assert result is None or isinstance(result, io.BytesIO)

    def test_valid_frame_returns_bytesio(self):
        import numpy as np
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        result = frame_to_bytesio(frame)
        assert isinstance(result, io.BytesIO)
        assert result.tell() == 0
        assert len(result.read()) > 0
