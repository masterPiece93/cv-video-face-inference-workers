"""Abstract base encoder interface.

All encoder implementations must subclass BaseEncoder.
This ensures the encoding service is fully pluggable —
swap face_recognition, fdetect gRPC, or any future vendor
without changing the consuming code.
"""
from abc import ABC, abstractmethod
from io import BytesIO
from typing import Optional, List

import numpy as np

__all__ = ["BaseEncoder", "FaceEncoding"]


# Type alias for a single face encoding vector
FaceEncoding = np.ndarray


class BaseEncoder(ABC):
    """Abstract face encoder.

    Implementations must:
    - Accept a frame as BytesIO (JPEG/PNG)
    - Return a list of face encoding arrays found in that frame

    Thread-safety is declared per backend via :attr:`supports_parallel`.
    The encoding service reads that flag to decide whether frames may be
    encoded concurrently. Backends that wrap thread-unsafe native code
    (e.g. dlib via ``face_recognition``) MUST keep it ``False``; I/O-bound,
    thread-safe backends (e.g. the fdetect gRPC client) may set it ``True``.
    """

    #: Whether :meth:`encode_frame` is safe to invoke concurrently from
    #: multiple threads. Defaults to ``False`` (the safe choice). Override to
    #: ``True`` only in backends whose ``encode_frame`` is genuinely
    #: thread-safe.
    supports_parallel: bool = False

    @abstractmethod
    def encode_frame(self, frame_bytes: BytesIO) -> List[FaceEncoding]:
        """Encode all faces found in a single frame.

        Args:
            frame_bytes: Frame image as BytesIO (JPEG or PNG).

        Returns:
            List of face encoding arrays (128-d vectors).
            Empty list if no faces found.
        """
        ...

    @abstractmethod
    def calculate_distance(
        self,
        known_encodings: List[FaceEncoding],
        candidate: FaceEncoding,
    ) -> np.ndarray:
        """Calculate distance between candidate and a list of known encodings.

        Args:
            known_encodings: Reference encoding vectors.
            candidate: Encoding to compare.

        Returns:
            Array of distances (one per known encoding).
        """
        ...

    def is_duplicate(
        self,
        encoding: FaceEncoding,
        existing: List[FaceEncoding],
        tolerance: float = 0.6,
    ) -> bool:
        """Check if an encoding is a duplicate of any in the existing list.

        Args:
            encoding: Candidate encoding.
            existing: List of known encodings.
            tolerance: Distance threshold below which faces are considered same.

        Returns:
            True if a match found within tolerance.
        """
        if not existing:
            return False
        distances = self.calculate_distance(existing, encoding)
        return bool(np.any(distances <= tolerance))

    def close(self) -> None:
        """Release any resources held by this encoder.

        Default no-op; override in subclasses that hold connections (e.g. gRPC).
        Must be idempotent and never raise.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this encoder backend."""
        ...
