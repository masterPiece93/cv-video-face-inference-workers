"""Common helper functions for workers."""
import io
from datetime import datetime, timezone
from typing import Any, Iterator, List, Optional

import numpy as np

__all__ = [
    "chunked",
    "now_utc",
    "build_candidate_base_path",
    "build_stage_path",
    "frame_to_bytesio",
    "numpy_to_bytesio",
    "bytesio_to_numpy",
]


def chunked(iterable: List[Any], size: int) -> Iterator[List[Any]]:
    """Split a list into chunks of the given size.

    Args:
        iterable: Input list.
        size: Maximum chunk size.

    Yields:
        Sub-lists of at most `size` elements.
    """
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def now_utc() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def build_candidate_base_path(
    org_alias: str,
    org_id: str,
    candidate_email: str,
    candidate_uid: str,
) -> str:
    """Build the GCS base path for a candidate's records.

    Args:
        org_alias: Organisation alias.
        org_id: Organisation ID.
        candidate_email: Candidate email.
        candidate_uid: Candidate UID.

    Returns:
        Base path string (no trailing slash).
    """
    return f"{org_alias}/{org_id}/{candidate_email}/{candidate_uid}"


def build_stage_path(base_path: str, filename: str) -> str:
    """Build the GCS path for a stage tracking file.

    Stage files are stored flat under ``stages/`` — the event_id is recorded
    inside the JSON payload rather than encoded in the path.

    Args:
        base_path: Candidate base path.
        filename: e.g., "processing.json", "encoding.json".

    Returns:
        Full blob path.
    """
    return f"{base_path}/stages/{filename}"


def frame_to_bytesio(frame: "np.ndarray") -> Optional[io.BytesIO]:
    """Convert an OpenCV BGR frame to JPEG BytesIO.

    Args:
        frame: NumPy BGR image array.

    Returns:
        BytesIO of JPEG-encoded image, or None on failure.
    """
    try:
        import cv2
        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            return None
        bio = io.BytesIO(buffer.tobytes())
        bio.seek(0)
        return bio
    except Exception:
        return None


def numpy_to_bytesio(array: np.ndarray) -> io.BytesIO:
    """Serialize a numpy array to BytesIO using np.save.

    Args:
        array: NumPy array to serialize.

    Returns:
        BytesIO containing the .npy bytes.
    """
    bio = io.BytesIO()
    np.save(bio, array)
    bio.seek(0)
    return bio


def bytesio_to_numpy(data: io.BytesIO) -> Optional[np.ndarray]:
    """Deserialize a BytesIO .npy blob into a numpy array.

    Args:
        data: BytesIO of .npy data.

    Returns:
        NumPy array or None on failure.
    """
    try:
        data.seek(0)
        return np.load(data, allow_pickle=False)
    except Exception:
        return None