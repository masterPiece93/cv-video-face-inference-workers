"""fdetect gRPC encoder backend.

Uses the gta_ml fdetect gRPC service for face detection and encoding.
Drop-in replacement for FaceRecognitionEncoder when the gRPC service is available.
"""
import logging
import time
from io import BytesIO
from typing import List

import grpc
import numpy as np
from google.protobuf.json_format import MessageToDict

from common.services.encoding.base import BaseEncoder, FaceEncoding
from common.services.encoding.pbgen import safe_pb2, safe_pb2_grpc
from common.services.errors import NonRecoverableError, RecoverableError

logger = logging.getLogger(__name__)

__all__ = ["FdetectEncoder"]

_MAX_RETRIES = 5
_RETRY_SLEEP = 1
_HEALTH_TIMEOUT = 3  # seconds


class FdetectEncoder(BaseEncoder):
    """Encoder backed by the fdetect gRPC service (gta_ml).

    Connects to the fdetect service via an insecure gRPC channel.
    Implements retry logic for transient UNAVAILABLE errors.

    gRPC calls are network I/O-bound and thread-safe, so the encoding
    service may encode frames concurrently with this backend.
    """

    #: fdetect calls are network I/O — safe to parallelise across threads.
    supports_parallel: bool = True

    def __init__(self, channel_address: str):
        """Initialize the gRPC encoder.

        Args:
            channel_address: gRPC server address, e.g., "localhost:50051".
        """
        if not channel_address:
            raise ValueError("fdetect channel_address must not be empty")
        self._channel_address = channel_address
        self._channel = grpc.insecure_channel(channel_address)
        self._stub = safe_pb2_grpc.FaceDetectStub(self._channel)

    # ── Health-based error classification ──────────────────────────

    def ping(self) -> bool:
        """Ping the fdetect gRPC Health endpoint.

        Returns True if the service responds with Status == "OK", False otherwise.
        Short timeout — never raises.
        """
        try:
            request = safe_pb2.HealthRequest(RequestId="ping", Full=False)
            response = self._stub.Health(request, timeout=_HEALTH_TIMEOUT)
            return response.Status == "OK"
        except Exception as e:
            logger.warning(f"fdetect health ping failed: {e}")
            return False

    def encode_frame(self, frame_bytes: BytesIO) -> List[FaceEncoding]:
        """Send a frame to fdetect and return encodings.

        Args:
            frame_bytes: Frame as BytesIO (JPEG/PNG).

        Returns:
            List of 128-d encoding arrays.

        Raises:
            NonRecoverableError: If fdetect is unreachable (pre-request ping failed).
            RecoverableError: If fdetect is healthy but Detect RPC failed.
        """
        # fail fast if service is down
        if not self.ping():
            raise NonRecoverableError(
                f"fdetect service at {self._channel_address} is unreachable "
                "(pre-request health ping failed)"
            )

        frame_bytes.seek(0)
        request = safe_pb2.FaceDetectRequest(
            rid="1",
            returnEncoding=True,
        )
        request.images.append(safe_pb2.Image(image=frame_bytes.read()))
        response = self._call_with_retry(request)
        resp_dict = MessageToDict(response)
        encodings = [
            np.array(face.get("enc", []))
            for result in resp_dict.get("results", [])
            for face in result.get("faces", [])
            if face.get("enc")
        ]
        return encodings

    def calculate_distance(
        self,
        known_encodings: List[FaceEncoding],
        candidate: FaceEncoding,
    ) -> np.ndarray:
        """Euclidean distance between candidate and each known encoding.

        Args:
            known_encodings: Reference encodings.
            candidate: Encoding to compare.

        Returns:
            Array of distances.
        """
        if not known_encodings:
            return np.array([])
        return np.linalg.norm(np.array(known_encodings) - candidate, axis=1)

    def _call_with_retry(
        self, request: safe_pb2.FaceDetectRequest
    ) -> safe_pb2.FaceDetectResponse:
        """Call the gRPC Detect endpoint with retry on UNAVAILABLE.

        Args:
            request: FaceDetectRequest proto.

        Returns:
            FaceDetectResponse on success.

        Raises:
            RecoverableError: If retries exhausted but service is healthy.
            NonRecoverableError: If retries exhausted and service is unreachable.
        """
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return self._stub.Detect(request)
            except grpc.RpcError as err:
                if err.code() == grpc.StatusCode.UNAVAILABLE:
                    logger.warning(
                        f"fdetect UNAVAILABLE (attempt {attempt}/{_MAX_RETRIES}), "
                        f"retrying in {_RETRY_SLEEP}s..."
                    )
                    time.sleep(_RETRY_SLEEP)
                else:
                    logger.error(f"fdetect gRPC error: {err}")
                    raise

        # never return None, classify using health check
        logger.error(f"fdetect max retries ({_MAX_RETRIES}) reached")
        if self.ping():
            raise RecoverableError(
                "fdetect service is healthy but Detect RPC failed after max retries"
            )
        raise NonRecoverableError(
            f"fdetect service at {self._channel_address} is unreachable "
            "(health ping failed after retries exhausted)"
        )

    def close(self) -> None:
        """Close the underlying gRPC channel. Idempotent and never raises."""
        try:
            self._channel.close()
            logger.info(f"fdetect gRPC channel closed ({self._channel_address})")
        except Exception as e:
            logger.warning(f"Error closing fdetect channel: {e}")

    @property
    def name(self) -> str:
        return f"fdetect_grpc@{self._channel_address}"
