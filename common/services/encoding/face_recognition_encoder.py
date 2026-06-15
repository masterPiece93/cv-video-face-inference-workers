"""Face Recognition encoder backend.

Uses the `face_recognition` library (dlib-based) for local CPU/GPU encoding.
This is the default in-house encoder — zero external service dependency.
"""
import logging
from io import BytesIO
from typing import List

import numpy as np

from common.services.encoding.base import BaseEncoder, FaceEncoding

logger = logging.getLogger(__name__)

__all__ = ["FaceRecognitionEncoder"]


class FaceRecognitionEncoder(BaseEncoder):
    """Encoder backed by the `face_recognition` library (dlib).

    Supports HOG (CPU) and CNN (GPU) detection models.
    Uses DBSCAN clustering to deduplicate faces across frames.

    dlib's native code is NOT thread-safe — concurrent ``encode_frame``
    calls segfault — so this backend is always encoded sequentially.
    """

    #: dlib is NOT thread-safe; frames must be encoded one at a time.
    supports_parallel: bool = False

    def __init__(
        self,
        model: str = "hog",
        num_jitters: int = 1,
    ):
        """Initialize the encoder.

        Args:
            model: Detection model — "hog" (fast, CPU) or "cnn" (accurate, GPU).
            num_jitters: Number of jitters for encoding (higher = more accurate, slower).
        """
        import face_recognition  # deferred import — optional dependency
        self._fr = face_recognition
        self.model = model
        self.num_jitters = num_jitters

    def encode_frame(self, frame_bytes: BytesIO) -> List[FaceEncoding]:
        """Encode all faces in a frame image.

        Args:
            frame_bytes: Frame as BytesIO (JPEG/PNG).

        Returns:
            List of 128-d encoding arrays.
        """
        try:
            frame_bytes.seek(0)
            img = self._fr.load_image_file(frame_bytes)
            locations = self._fr.face_locations(img, model=self.model)
            if not locations:
                return []
            encodings = self._fr.face_encodings(
                img,
                known_face_locations=locations,
                num_jitters=self.num_jitters,
                model="small",
            )
            return list(encodings)
        except Exception as e:
            logger.warning(f"encode_frame failed: {e}")
            return []

    def calculate_distance(
        self,
        known_encodings: List[FaceEncoding],
        candidate: FaceEncoding,
    ) -> np.ndarray:
        """Euclidean distance between candidate and each known encoding.

        Args:
            known_encodings: List of reference encodings.
            candidate: Encoding to compare.

        Returns:
            Array of distances.
        """
        if not known_encodings:
            return np.array([])
        return np.linalg.norm(np.array(known_encodings) - candidate, axis=1)

    @property
    def name(self) -> str:
        return f"face_recognition_{self.model}"
