"""Onboarding Verification Service.

Pipeline:
    1. Download onboarding reference images from a GCS blob prefix or a signed HTTPS URL
    2. Encode them using pluggable encoder
    3. Download .npy video encodings from GCS (for match_against locations)
    4. Compare each onboarding image against each video encoding set
    5. Write stage tracking JSON
    6. Publish result
"""
import io
import logging
from typing import Dict, List, Optional

import numpy as np

from common.services.cloud.gcp.storage import GCPStorageService
from common.services.encoding import BaseEncoder
from common.services.errors import NonRecoverableError, RecoverableError
from common.utils.helpers import (
    build_candidate_base_path,
    build_stage_path,
    bytesio_to_numpy,
    now_utc,
)
from workers.onboarding_verification_worker.src.handlers.output_handler import (
    OnboardingOutputHandler,
)

logger = logging.getLogger(__name__)

ENCODING_BASE = "video_face_encodings"

class OnboardingVerificationService:
    """Verify onboarding reference images against pre-computed video encodings."""

    def __init__(
        self,
        encoder: BaseEncoder,
        storage: GCPStorageService,
        output_handler: OnboardingOutputHandler,
        tolerance: float = 0.6,
    ):
        self.encoder = encoder
        self.storage = storage
        self.output_handler = output_handler
        self.tolerance = tolerance

    def process(self, payload: dict) -> None:
        """Run the onboarding verification pipeline.

        Args:
            payload: Validated input message dict.
        """
        event_id: str = payload["event_id"]
        bucket: str = payload["bucket_name"]
        candidate_email: str = payload["candidate_email"]
        candidate_uid: str = payload["candidate_uid"]
        org_alias: str = payload["org_alias"]
        org_id: str = payload["org_id"]
        lookup_map: dict = payload["lookup_map"]
        ob_ref_path: str = payload["onboarding_reference_path"]
        match_against: list[str] = payload["match_against"]

        base_path: str = build_candidate_base_path(org_alias, org_id, candidate_email, candidate_uid)
        encoding_base: str = f"{base_path}/{ENCODING_BASE}"

        self._write_stage(bucket, base_path, event_id, "onboarding_verification.json", {
            "message_id": event_id, "event_id": event_id, "started": now_utc(), "completed": None,
        })

        started_at = now_utc()
        try:
            # --- Encode onboarding reference images ---
            ref_encodings: List[np.ndarray] = []

            if ob_ref_path.startswith(("https://", "http://")):
                # Signed URL — single image download
                img_data = self.storage.download_bytes_from_url(ob_ref_path)
                if img_data is None:
                    raise NonRecoverableError(
                        f"[{event_id}] Failed to download onboarding reference from signed URL"
                    )
                encs = self.encoder.encode_frame(img_data)
                ref_encodings.extend(encs)

            elif ob_ref_path.startswith("gs://"):
                # Absolute GCS URI — may point to a different bucket.
                # Supports both a single image file and a blob prefix (folder).
                # e.g. "gs://other-bucket/photos/alice.jpg"
                #      "gs://other-bucket/photos/alice/"
                src_bucket, src_path = self._parse_gcs_uri(ob_ref_path)
                _img_exts = (".jpg", ".jpeg", ".png")
                if src_path.lower().endswith(_img_exts):
                    # Single image blob
                    img_data = self.storage.download_bytes(src_bucket, src_path)
                    if img_data is None:
                        raise NonRecoverableError(
                            f"[{event_id}] Failed to download onboarding reference "
                            f"from gs://{src_bucket}/{src_path}"
                        )
                    encs = self.encoder.encode_frame(img_data)
                    ref_encodings.extend(encs)
                else:
                    # Blob prefix — list and download all image blobs under it
                    ref_blobs = self.storage.list_blobs(src_bucket, src_path)
                    if not ref_blobs:
                        raise NonRecoverableError(
                            f"[{event_id}] No onboarding reference images at "
                            f"gs://{src_bucket}/{src_path}"
                        )
                    for blob_path in ref_blobs:
                        if not blob_path.lower().endswith(_img_exts):
                            continue
                        img_data = self.storage.download_bytes(src_bucket, blob_path)
                        if img_data is None:
                            continue
                        encs = self.encoder.encode_frame(img_data)
                        ref_encodings.extend(encs)

            else:
                # GCS blob prefix — list and download each image
                ref_blob_prefix = f"{base_path}/{ob_ref_path.rstrip('/')}"
                ref_blobs = self.storage.list_blobs(bucket, ref_blob_prefix)
                if not ref_blobs:
                    raise NonRecoverableError(
                        f"[{event_id}] No onboarding reference images at gs://{bucket}/{ref_blob_prefix}"
                    )
                for blob_path in ref_blobs:
                    if not blob_path.lower().endswith((".jpg", ".jpeg", ".png")):
                        continue
                    img_data = self.storage.download_bytes(bucket, blob_path)
                    if img_data is None:
                        continue
                    encs = self.encoder.encode_frame(img_data)
                    ref_encodings.extend(encs)

            if not ref_encodings:
                raise NonRecoverableError(
                    f"[{event_id}] Could not encode any onboarding reference images"
                )

            logger.info(
                f"[{event_id}] Encoded {len(ref_encodings)} onboarding reference face(s)"
            )

            # --- Match against selected video encodings ---
            matches: Dict[str, bool] = {}
            for location in match_against:   # TODO : make it parallel
                if location in lookup_map.get("interviews", []) or \
                    location == lookup_map.get("profile", ""):
                    npy_blob = f"{encoding_base}/{location}/{location}.npy"
                    video_encodings = self._load_npy(bucket, npy_blob, event_id)
                    if video_encodings is None:
                        matches[location] = False
                        continue

                    matched = any(
                        self._match_any(ref_enc, video_encodings)
                        for ref_enc in ref_encodings
                    )
                    matches[location] = matched
                    logger.info(
                        f"[{event_id}] {location}: matched={matched}"
                    )
                else:
                    logger.info(
                        f"[{event_id}] Match Against Value `{location}` not Found in lookup map provided : {lookup_map}"
                    )
        except (NonRecoverableError, RecoverableError):
            self._write_stage(bucket, base_path, event_id, "onboarding_verification.json", {
                "message_id": event_id, "event_id": event_id, "started": started_at,
                "completed": now_utc(), "status": "ERROR",
            })
            raise
        except Exception as e:
            self._write_stage(bucket, base_path, event_id, "onboarding_verification.json", {
                "message_id": event_id, "event_id": event_id, "started": started_at,
                "completed": now_utc(), "status": "ERROR",
            })
            raise NonRecoverableError(
                f"[{event_id}] Onboarding verification failed: {e}"
            ) from e

        self._write_stage(bucket, base_path, event_id, "onboarding_verification.json", {
            "message_id": event_id, "event_id": event_id, "started": started_at,
            "completed": now_utc(), "status": "OK",
        })

        output_payload = {
            "candidate_email": candidate_email,
            "candidate_uid": candidate_uid,
            "extra_info": payload.get("extra_info"),
            "org_id": org_id,
            "org_alias": org_alias,
            "bucket_name": bucket,
            "event_id": event_id,
            "status": {"matches": matches},
            "lookup_map": lookup_map,
        }
        self.output_handler.publish(output_payload)

    @staticmethod
    def _parse_gcs_uri(uri: str) -> tuple[str, str]:
        """Parse a ``gs://`` URI into a ``(bucket, path)`` pair.

        Args:
            uri: A GCS URI such as ``"gs://my-bucket/path/to/prefix/"``
                 or ``"gs://my-bucket/path/to/image.jpg"``.

        Returns:
            Tuple of ``(bucket_name, blob_path_or_prefix)`` with no leading
            or trailing slashes on the path component.
        """
        without_scheme = uri[len("gs://"):]
        bucket, _, path = without_scheme.partition("/")
        return bucket, path.strip("/")

    def _match_any(self, ref_enc: np.ndarray, video_encodings: np.ndarray) -> bool:
        distances = np.linalg.norm(video_encodings - ref_enc, axis=1)
        return bool(np.any(distances <= self.tolerance))

    def _load_npy(
        self, bucket: str, blob_path: str, event_id: str
    ) -> Optional[np.ndarray]:
        data = self.storage.download_bytes(bucket, blob_path)
        if data is None:
            logger.warning(f"[{event_id}] .npy not found: gs://{bucket}/{blob_path}")
            return None
        return bytesio_to_numpy(data)

    def _write_stage(
        self, bucket: str, base_path: str, event_id: str, filename: str, data: dict
    ) -> None:
        self.storage.upload_json(data, bucket, build_stage_path(base_path, filename))
