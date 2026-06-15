"""Video Face Verification Service.

Core pipeline:
    1. Download .npy encoding files from GCS
    2. Load numpy arrays for profile + all interviews
    3. Apply pluggable verification strategy
    4. Write stage tracking JSON to GCS
    5. Publish result to downstream topic
"""
import logging
from typing import Dict, Optional

import numpy as np

from common.services.cloud.gcp.storage import GCPStorageService
from common.services.errors import NonRecoverableError, RecoverableError
from common.utils.helpers import (
    build_candidate_base_path,
    build_stage_path,
    bytesio_to_numpy,
    now_utc,
)
from workers.face_verification_worker.services.strategies import (
    BaseVerificationStrategy,
    VerificationResult,
)
from workers.face_verification_worker.src.handlers.output_handler import VerificationOutputHandler

logger = logging.getLogger(__name__)


class VideoFaceVerificationService:
    """Loads encodings from GCS and runs the configured verification strategy."""

    def __init__(
        self,
        strategy: BaseVerificationStrategy,
        storage: GCPStorageService,
        output_handler: VerificationOutputHandler,
    ):
        """Initialize the verification service.

        Args:
            strategy: Pluggable verification strategy.
            storage: GCS storage service.
            output_handler: Downstream PubSub publisher.
        """
        self.strategy = strategy
        self.storage = storage
        self.output_handler = output_handler

    def process(self, payload: dict) -> None:
        """Run the full verification pipeline.

        Args:
            payload: Validated input message dict.

        Raises:
            NonRecoverableError: On unrecoverable failures.
            RecoverableError: On transient failures.
        """
        event_id = payload["event_id"]
        bucket = payload["bucket_name"]
        candidate_email = payload["candidate_email"]
        candidate_uid = payload["candidate_uid"]
        org_alias = payload["org_alias"]
        org_id = payload["org_id"]
        lookup_map = payload["lookup_map"]
        sampled_frames = payload.get("sampled_frames", {})

        base_path = build_candidate_base_path(org_alias, org_id, candidate_email, candidate_uid)

        self._write_stage(bucket, base_path, event_id, "verification.json", {
            "message_id": event_id, "event_id": event_id, "started": now_utc(), "completed": None,
        })

        started_at = now_utc()
        try:
            # --- Load profile encodings ---
            profile_encodings: Optional[np.ndarray] = None
            profile_loc = lookup_map.get("profile")
            if profile_loc:
                npy_blob = f"{base_path}/video_face_encodings/{profile_loc}/profile.npy" # `video_face_encodings` shall be read from config
                profile_encodings = self._load_npy(bucket, npy_blob, event_id)

            # --- Load interview encodings ---
            interview_encodings: Dict[str, np.ndarray] = {}
            for name in lookup_map.get("interviews", []):
                npy_blob = f"{base_path}/video_face_encodings/{name}/{name}.npy" # `video_face_encodings` shall be read from config
                arr = self._load_npy(bucket, npy_blob, event_id)
                if arr is not None:
                    interview_encodings[name] = arr

            if not interview_encodings:
                raise NonRecoverableError(
                    f"[{event_id}] No interview encodings could be loaded"
                )

            # --- Run strategy ---
            result: VerificationResult = self.strategy.verify(
                profile_encodings=profile_encodings,
                interview_encodings=interview_encodings,
            )
            logger.info(f"[{event_id}] Verification result: {result.to_dict()}")

        except (NonRecoverableError, RecoverableError):
            self._write_stage(bucket, base_path, event_id, "verification.json", {
                "message_id": event_id, "event_id": event_id, "started": started_at,
                "completed": now_utc(), "status": "ERROR",
            })
            raise
        except Exception as e:
            self._write_stage(bucket, base_path, event_id, "verification.json", {
                "message_id": event_id, "event_id": event_id, "started": started_at,
                "completed": now_utc(), "status": "ERROR",
            })
            raise NonRecoverableError(
                f"[{event_id}] Verification pipeline failed: {e}"
            ) from e

        self._write_stage(bucket, base_path, event_id, "verification.json", {
            "message_id": event_id, "event_id": event_id, "started": started_at,
            "completed": now_utc(), "status": "OK",
        })

        # --- Publish downstream ---
        output_payload = {
            "candidate_email": candidate_email,
            "candidate_uid": candidate_uid,
            "extra_info": payload.get("extra_info"),
            "org_id": org_id,
            "org_alias": org_alias,
            "bucket_name": bucket,
            "event_id": event_id,
            "status": result.to_dict(),
            "sampled_frames": sampled_frames,
            "lookup_map": lookup_map,
        }
        self.output_handler.publish(output_payload)

    def _load_npy(
        self, bucket: str, blob_path: str, event_id: str
    ) -> Optional[np.ndarray]:
        """Download and deserialize a .npy file.

        Args:
            bucket: GCS bucket.
            blob_path: Blob path.
            event_id: Tracking ID.

        Returns:
            Numpy array or None if not found/parseable.
        """
        data = self.storage.download_bytes(bucket, blob_path)
        if data is None:
            logger.warning(f"[{event_id}] .npy not found: gs://{bucket}/{blob_path}")
            return None
        arr = bytesio_to_numpy(data)
        if arr is None:
            logger.warning(f"[{event_id}] Failed to parse .npy: gs://{bucket}/{blob_path}")
        return arr

    def _write_stage(
        self, bucket: str, base_path: str, event_id: str, filename: str, data: dict
    ) -> None:
        blob_path = build_stage_path(base_path, filename)
        self.storage.upload_json(data, bucket, blob_path)
