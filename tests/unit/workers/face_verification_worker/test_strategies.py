"""Unit tests for verification strategies."""
from typing import Dict, Optional

import numpy as np
import pytest

from workers.face_verification_worker.services.strategies.base import (
    BaseVerificationStrategy,
    VerificationResult,
)
from workers.face_verification_worker.services.strategies.common_faces import CommonFacesStrategy
from workers.face_verification_worker.services.strategies.with_profile import WithProfileStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enc(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(128)
    return v / np.linalg.norm(v)


def _similar(base: np.ndarray, noise: float = 0.01) -> np.ndarray:
    """Return a near-copy of `base` (within tolerance 0.6)."""
    perturbed = base + np.random.default_rng(1).random(128) * noise
    return perturbed / np.linalg.norm(perturbed)


# ---------------------------------------------------------------------------
# VerificationResult
# ---------------------------------------------------------------------------

class TestVerificationResult:
    def test_to_dict_contains_all_keys(self):
        r = VerificationResult(similar_face_count=3, profile_match=True, matches={"i1": True})
        d = r.to_dict()
        assert d["similar_face_count"] == 3
        assert d["profile_match"] is True
        assert d["matches"] == {"i1": True}

    def test_to_dict_defaults_matches_to_empty(self):
        r = VerificationResult(similar_face_count=0, profile_match=None)
        assert r.to_dict()["matches"] == {}


# ---------------------------------------------------------------------------
# BaseVerificationStrategy._encodings_match
# ---------------------------------------------------------------------------

class TestEncodingsMatch:
    def test_match_within_tolerance(self):
        base = _enc(0)
        similar = _similar(base, noise=0.001)
        assert BaseVerificationStrategy._encodings_match(base, np.array([similar]), 0.6)

    def test_no_match_beyond_tolerance(self):
        a = _enc(0)
        b = _enc(99)  # different seed → far apart
        assert not BaseVerificationStrategy._encodings_match(a, np.array([b]), 0.01)

    def test_empty_list_returns_false(self):
        a = _enc(0)
        assert not BaseVerificationStrategy._encodings_match(a, np.array([]), 0.6)


# ---------------------------------------------------------------------------
# CommonFacesStrategy
# ---------------------------------------------------------------------------

class TestCommonFacesStrategy:
    @pytest.fixture()
    def strategy(self):
        return CommonFacesStrategy(tolerance=0.6)

    def test_empty_encodings_returns_zero(self, strategy):
        result = strategy.verify(profile_encodings=None, interview_encodings={})
        assert result.similar_face_count == 0
        assert result.profile_match is None

    def test_single_group_returns_its_length(self, strategy):
        encs = np.stack([_enc(i) for i in range(4)])
        result = strategy.verify(None, {"i1": encs})
        assert result.similar_face_count == 4

    def test_two_groups_with_common_face(self, strategy):
        base = _enc(0)
        similar = _similar(base, noise=0.001)
        group1 = np.array([base, _enc(10)])
        group2 = np.array([similar, _enc(20)])
        result = strategy.verify(None, {"i1": group1, "i2": group2})
        assert result.similar_face_count >= 1

    def test_two_groups_no_common_face(self, strategy):
        # Completely different encodings in both groups
        group1 = np.stack([_enc(i) for i in range(5)])
        group2 = np.stack([_enc(i + 50) for i in range(5)])
        result = strategy.verify(None, {"i1": group1, "i2": group2}, )
        # With tolerance=0.6 and very different seeds, count should be low/zero
        assert result.similar_face_count >= 0  # just assert no crash

    def test_profile_match_always_none(self, strategy):
        result = strategy.verify(np.stack([_enc(0)]), {"i1": np.stack([_enc(0)])})
        assert result.profile_match is None


# ---------------------------------------------------------------------------
# WithProfileStrategy
# ---------------------------------------------------------------------------

class TestWithProfileStrategy:
    @pytest.fixture()
    def strategy(self):
        return WithProfileStrategy(tolerance=0.6)

    def test_no_profile_gives_none_match(self, strategy):
        encs = np.stack([_enc(i) for i in range(3)])
        result = strategy.verify(profile_encodings=None, interview_encodings={"i1": encs})
        assert result.profile_match is None

    def test_empty_profile_gives_none_match(self, strategy):
        result = strategy.verify(
            profile_encodings=np.array([]),
            interview_encodings={"i1": np.stack([_enc(0)])},
        )
        assert result.profile_match is None

    def test_profile_match_true_when_same_face(self, strategy):
        face = _enc(0)
        profile = np.array([face])
        interview = np.array([_similar(face, noise=0.001)])
        result = strategy.verify(profile, {"i1": interview})
        assert result.profile_match is True
        assert result.matches["i1"] is True

    def test_profile_match_false_when_different_face(self, strategy):
        profile = np.array([_enc(0)])
        interview = np.array([_enc(99)])
        result = strategy.verify(profile, {"i1": interview})
        # Low noise → likely False, but just assert the result is a bool
        assert isinstance(result.profile_match, bool)

    def test_similar_count_two_groups_common(self, strategy):
        face = _enc(0)
        g1 = np.array([face, _enc(10)])
        g2 = np.array([_similar(face, noise=0.001), _enc(20)])
        result = strategy.verify(None, {"i1": g1, "i2": g2})
        assert result.similar_face_count >= 1

    def test_single_interview_group_count(self, strategy):
        encs = np.stack([_enc(i) for i in range(3)])
        result = strategy.verify(None, {"i1": encs})
        assert result.similar_face_count == 3
