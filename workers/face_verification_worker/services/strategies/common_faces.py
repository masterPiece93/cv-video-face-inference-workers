"""Common Faces strategy: count unique faces common across all interview videos."""
import logging
from typing import Dict, Optional

import numpy as np

from workers.face_verification_worker.services.strategies.base import (
    BaseVerificationStrategy,
    VerificationResult,
)

logger = logging.getLogger(__name__)


class CommonFacesStrategy(BaseVerificationStrategy):
    """Count faces that appear in ALL interview videos.

    Does not use profile encoding. Useful when you just want to know
    how many common individuals appear across all interview sessions.
    """

    @property
    def name(self) -> str:
        return "common_faces"

    def verify(
        self,
        profile_encodings: Optional[np.ndarray],
        interview_encodings: Dict[str, np.ndarray],
    ) -> VerificationResult:
        """Count common faces across all interview encoding sets.

        Args:
            profile_encodings: Unused in this strategy.
            interview_encodings: Dict of interview_name → encodings array.

        Returns:
            VerificationResult with similar_face_count.
        """
        if not interview_encodings:
            return VerificationResult(similar_face_count=0, profile_match=None)

        groups = list(interview_encodings.values())
        if len(groups) == 1:
            return VerificationResult(
                similar_face_count=len(groups[0]), profile_match=None
            )

        # Count how many faces from the first group appear in all others
        reference = groups[0]
        count = 0
        for enc in reference:
            found_in_all = all(
                self._encodings_match(enc, other, self.tolerance)
                for other in groups[1:]
            )
            if found_in_all:
                count += 1

        logger.info(f"CommonFacesStrategy: {count} common face(s) found")
        return VerificationResult(similar_face_count=count, profile_match=None)
