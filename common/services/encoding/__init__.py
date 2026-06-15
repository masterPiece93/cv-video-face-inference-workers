"""Encoding service factory.

Usage:
    from common.services.encoding import get_encoder

    encoder = get_encoder("face_recognition")   # default in-house
    encoder = get_encoder("fdetect", channel="localhost:50051")
"""
from common.services.encoding.base import BaseEncoder, FaceEncoding
from common.services.encoding.face_recognition_encoder import FaceRecognitionEncoder
from common.services.encoding.fdetect_encoder import FdetectEncoder

__all__ = ["BaseEncoder", "FaceEncoding", "FaceRecognitionEncoder", "FdetectEncoder", "get_encoder"]

_BACKENDS = {
    "face_recognition": FaceRecognitionEncoder,
    "fdetect": FdetectEncoder,
}


def get_encoder(backend: str = "face_recognition", **kwargs) -> BaseEncoder:
    """Factory function to instantiate an encoder backend.

    Args:
        backend: Encoder backend name — "face_recognition" or "fdetect".
        **kwargs: Backend-specific constructor arguments.
            - face_recognition: model="hog"|"cnn", num_jitters=1
            - fdetect: channel_address="host:port"

    Returns:
        Configured BaseEncoder instance.

    Raises:
        ValueError: If backend name is not recognized.
    """
    if backend not in _BACKENDS:
        raise ValueError(
            f"Unknown encoder backend '{backend}'. "
            f"Available: {list(_BACKENDS.keys())}"
        )
    return _BACKENDS[backend](**kwargs)
