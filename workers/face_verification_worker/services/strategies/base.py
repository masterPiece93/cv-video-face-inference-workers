"""Pluggable verification strategies for Face Verification Worker.

Each strategy receives the loaded numpy encodings and returns a result dict.
Add new strategies by subclassing BaseVerificationStrategy.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import numpy as np

__all__ = ["BaseVerificationStrategy", "VerificationResult"]


class VerificationResult:
    """Structured result from a verification strategy."""

    def __init__(
        self,
        similar_face_count: int,
        profile_match: Optional[bool],
        matches: Optional[Dict[str, bool]] = None,
    ):
        self.similar_face_count = similar_face_count
        self.profile_match = profile_match
        self.matches = matches or {}

    def to_dict(self) -> dict:
        return {
            "similar_face_count": self.similar_face_count,
            "profile_match": self.profile_match,
            "matches": self.matches,
        }


class BaseVerificationStrategy(ABC):
    """Abstract verification strategy.

    Implement this to define custom face matching logic.
    """

    def __init__(self, tolerance: float = 0.6):
        """Initialize strategy.

        Args:
            tolerance: Euclidean distance threshold for a face match.
        """
        self.tolerance = tolerance

    @abstractmethod
    def verify(
        self,
        profile_encodings: Optional[np.ndarray],
        interview_encodings: Dict[str, np.ndarray],
    ) -> VerificationResult:
        """Run the verification strategy.

        Args:
            profile_encodings: Encodings from the profile video (may be None).
            interview_encodings: Dict of interview_name → numpy encodings array.

        Returns:
            VerificationResult with counts and match flags.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this strategy."""
        ...

    @staticmethod
    def _encodings_match(
        a: np.ndarray,
        b_list: np.ndarray,
        tolerance: float,
    ) -> bool:
        """Check if encoding `a` matches any encoding in `b_list`.

        Args:
            a: Single encoding vector.
            b_list: Array of candidate encodings.
            tolerance: Distance threshold.

        Returns:
            True if any distance ≤ tolerance.
        """
        if b_list is None or len(b_list) == 0:
            return False
        distances = np.linalg.norm(b_list - a, axis=1)
        return bool(np.any(distances <= tolerance))
