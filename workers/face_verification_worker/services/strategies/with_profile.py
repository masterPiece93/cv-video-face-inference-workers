"""With-Profile strategy: verify interviews and check against profile."""
import logging
from typing import Dict, Optional

import numpy as np

from workers.face_verification_worker.services.strategies.base import (
    BaseVerificationStrategy,
    VerificationResult,
)

logger = logging.getLogger(__name__)


class WithProfileStrategy(BaseVerificationStrategy):
    """Count common faces across interviews and verify if profile matches.

    Case 2/3 from the architecture spec:
    - Count common faces across interview videos
    - For each interview, determine if the profile face also appears there
    """

    @property
    def name(self) -> str:
        return "with_profile"

    def verify(
        self,
        profile_encodings: Optional[np.ndarray],
        interview_encodings: Dict[str, np.ndarray],
    ) -> VerificationResult:
        """Verify interviews and match profile.

        Args:
            profile_encodings: Encodings from the profile video.
            interview_encodings: Dict of interview_name → encodings array.

        Returns:
            VerificationResult with common count, profile_match, and per-interview matches.
        """
        matches: Dict[str, bool] = {}

        # --- Count common faces across interviews ---
        groups = list(interview_encodings.values())
        similar_count = 0
        if len(groups) >= 2:
            reference = groups[0]
            for enc in reference:
                if all(
                    self._encodings_match(enc, other, self.tolerance)
                    for other in groups[1:]
                ):
                    similar_count += 1
        elif len(groups) == 1:
            similar_count = len(groups[0])

        # --- Profile match per interview ---
        profile_match: Optional[bool] = None
        if profile_encodings is not None and len(profile_encodings) > 0:
            for interview_name, enc_array in interview_encodings.items():
                matched = any(
                    self._encodings_match(prof_enc, enc_array, self.tolerance)
                    for prof_enc in profile_encodings
                )
                matches[interview_name] = matched

            profile_match = any(matches.values()) if matches else False

        logger.info(
            f"WithProfileStrategy: similar_count={similar_count}, "
            f"profile_match={profile_match}, per_interview={matches}"
        )
        return VerificationResult(
            similar_face_count=similar_count,
            profile_match=profile_match,
            matches=matches,
        )
