"""Video Face Encoding Service.

Core pipeline:
    1. Download vid            frame_sample_rate: Process every Nth frame.
            face_tolerance: Duplicate face distance threshold.
            max_workers: Thread pool size for parallel frame encoding. Only
                used when the active encoder declares it is thread-safe
                (``encoder.supports_parallel``); dlib/face_recognition always
                runs sequentially regardless of this value.s) from GCS
    2. Sample frames at configured rate
    3. Detect and encode faces in each frame (pluggable encoder)
    4. Deduplicate encodings using distance threshold
    5. Cluster with DBSCAN → keep dominant person's encodings
    6. Save encodings as .npy + sampled frames as .png to GCS
    7. Write stage tracking JSON to GCS
    8. Publish downstream message to verification topic
"""
import io
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from sklearn.cluster import DBSCAN

from common.services.cloud.gcp.storage import GCPStorageService
from common.services.encoding import BaseEncoder
from common.services.errors import NonRecoverableError, RecoverableError
from common.utils.helpers import (
    build_candidate_base_path,
    build_stage_path,
    frame_to_bytesio,
    now_utc,
    numpy_to_bytesio,
)
from workers.face_encoding_worker.src.handlers.output_handler import EncodingOutputHandler

logger = logging.getLogger(__name__)

ENCODING_BASE = "video_face_encodings"
FRAMES_BASE = "sampled_frames"
SNIPPETS_BASE = "video_snippets"
_VIDEO_SIGNATURES = {
    b"\x1a\x45\xdf\xa3": ".webm",
    b"\x00\x00\x00": ".mp4",
    b"\x00\x00\x01": ".mp4",
}


class VideoFaceEncodingService:
    """End-to-end video face encoding pipeline.

    Pluggable encoder backend + configurable frame sampling + DBSCAN clustering.
    """

    def __init__(
        self,
        encoder: BaseEncoder,
        storage: GCPStorageService,
        output_handler: EncodingOutputHandler,
        frame_sample_rate: int = 30,
        face_tolerance: float = 0.5,
        max_workers: int = 4,
    ):
        """Initialize the encoding service.

        Args:
            encoder: Face encoder backend (face_recognition or fdetect).
            storage: GCP storage service.
            output_handler: Downstream PubSub publisher.
            frame_sample_rate: Process every Nth frame.
            face_tolerance: Duplicate face distance threshold.
            max_workers: Thread pool size for parallel frame processing.
        """
        self.encoder = encoder
        self.storage = storage
        self.output_handler = output_handler
        self.frame_sample_rate = frame_sample_rate
        self.face_tolerance = face_tolerance
        self.max_workers = max_workers

    def process(self, payload: dict) -> None:
        """Run the full encoding pipeline for a message payload.

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

        base_path = build_candidate_base_path(org_alias, org_id, candidate_email, candidate_uid)
        encoding_base = f"{base_path}/{ENCODING_BASE}"
        frames_base = f"{base_path}/{FRAMES_BASE}"
        snippets_base = f"{base_path}/{SNIPPETS_BASE}"

        # --- Write processing start stage ---
        started_at = now_utc()
        self._write_stage(bucket, base_path, event_id, "encoding.json", {
            "message_id": event_id,
            "event_id": event_id,
            "started": started_at,
            "completed": None,
        })

        sampled_frames: Dict = {"profile": [], "interviews": {}}

        try:
            # --- Encode profile slot ---
            # lookup_map.profile is a folder name under video_snippets/.
            # All video blobs inside video_snippets/{profile}/ are downloaded and
            # their encodings are aggregated into a single video_face_encodings/profile/profile.npy.
            profile_loc = lookup_map.get("profile")
            if profile_loc:
                profile_prefix = f"{snippets_base}/{profile_loc}"
                profile_blobs = self._list_video_blobs(bucket, profile_prefix)
                if profile_blobs:
                    saved_frames, enc_path = self._encode_video_folder(
                        event_id=event_id,
                        bucket=bucket,
                        video_blobs=profile_blobs,
                        encoding_blob=f"{encoding_base}/profile/profile.npy",
                        frames_prefix=f"{frames_base}/profile",
                    )
                    if enc_path:
                        sampled_frames["profile"] = saved_frames
                else:
                    logger.warning(
                        f"[{event_id}] No video blobs found under {profile_prefix}"
                    )

            # --- Encode interview slots ---
            # lookup_map.interviews is a list of folder names under video_snippets/.
            # All snippet blobs inside each folder are processed and aggregated
            # into a single video_face_encodings/{interview_N}/{interview_N}.npy.
            interviews = lookup_map.get("interviews", [])
            for interview in interviews:
                interview_prefix = f"{snippets_base}/{interview}"
                interview_blobs = self._list_video_blobs(bucket, interview_prefix)
                if not interview_blobs:
                    logger.warning(
                        f"[{event_id}] No video blobs found under {interview_prefix}"
                    )
                    continue
                saved_frames, enc_path = self._encode_video_folder(
                    event_id=event_id,
                    bucket=bucket,
                    video_blobs=interview_blobs,
                    encoding_blob=f"{encoding_base}/{interview}/{interview}.npy",
                    frames_prefix=f"{frames_base}/{interview}",
                )
                if enc_path:
                    sampled_frames["interviews"][interview] = saved_frames

        except RecoverableError:
            raise
        except Exception as e:
            self._write_stage(bucket, base_path, event_id, "encoding.json", {
                "message_id": event_id,
                "event_id": event_id,
                "started": started_at,
                "completed": now_utc(),
                "status": "ERROR",
            })
            raise NonRecoverableError(f"[{event_id}] Encoding pipeline failed: {e}") from e

        # --- Write completion stage ---
        self._write_stage(bucket, base_path, event_id, "encoding.json", {
            "message_id": event_id,
            "event_id": event_id,
            "started": started_at,
            "completed": now_utc(),
            "status": "OK",
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
            "lookup_map": lookup_map,
            "sampled_frames": sampled_frames,
        }
        self.output_handler.publish(output_payload)

    def _list_video_blobs(self, bucket: str, prefix: str) -> List[str]:
        """List all video blobs under a GCS prefix, sorted by name.

        Args:
            bucket: GCS bucket name.
            prefix: Blob path prefix (folder path).

        Returns:
            Sorted list of full blob paths for video files under the prefix.
        """
        prefix = prefix.rstrip("/") + "/"
        blobs = self.storage.list_blobs(bucket, prefix=prefix)
        video_exts = {".mp4", ".webm", ".avi", ".mov", ".mkv"}
        return sorted(
            b for b in blobs
            if any(b.lower().endswith(ext) for ext in video_exts)
        )

    def _encode_video_folder(
        self,
        event_id: str,
        bucket: str,
        video_blobs: List[str],
        encoding_blob: str,
        frames_prefix: str,
    ) -> Tuple[List[str], Optional[str]]:
        """Encode all video snippets in a folder and aggregate their encodings.

        Downloads each video blob, extracts frames, encodes faces, and merges
        all unique encodings across all snippets into a single .npy file.

        Args:
            event_id: Tracking ID.
            bucket: GCS bucket name.
            video_blobs: Ordered list of blob paths for each snippet video.
            encoding_blob: Destination blob path for the aggregated .npy.
            frames_prefix: Destination prefix for sampled frame PNGs.

        Returns:
            Tuple of (saved_frame_names, encoding_blob_path).
            Returns ([], None) if no faces found across any snippet.
        """
        all_encodings: List[np.ndarray] = []
        all_unique_frames: List[np.ndarray] = []

        for blob_path in video_blobs:
            logger.info(f"[{event_id}] Processing snippet: gs://{bucket}/{blob_path}")
            video_data = self.storage.download_bytes(bucket, blob_path)
            if video_data is None:
                logger.warning(f"[{event_id}] Could not download {blob_path} — skipping")
                continue

            frames = self._extract_frames(video_data)
            if not frames:
                logger.warning(f"[{event_id}] No frames extracted from {blob_path}")
                continue

            # Encode this snippet's frames — concurrently for thread-safe
            # backends (e.g. fdetect gRPC), sequentially for dlib — then fold
            # the unique faces into the running aggregate. Deduplication stays
            # sequential and order-preserving so output is backend-independent.
            frame_encodings = self._encode_frames(frames, event_id, blob_path)
            self._collect_unique(frame_encodings, all_encodings, all_unique_frames)

        if not all_encodings:
            logger.warning(f"[{event_id}] No faces found in any snippet for {encoding_blob}")
            return [], None

        # DBSCAN cluster across all snippets → keep dominant person
        dominant_encodings = self._cluster_dominant(all_encodings)
        logger.info(
            f"[{event_id}] {len(dominant_encodings)} dominant encodings → {encoding_blob}"
        )

        # Save aggregated encodings as .npy
        npy_data = numpy_to_bytesio(np.array(dominant_encodings))
        self.storage.upload_bytes(npy_data, bucket, encoding_blob, "application/octet-stream")

        # Save representative sampled frames
        saved_names: List[str] = []
        for idx, frame in enumerate(all_unique_frames[: len(dominant_encodings)], start=1):
            frame_name = f"{idx}.png"
            frame_blob = f"{frames_prefix}/{frame_name}"
            frame_bio = frame_to_bytesio(frame)
            if frame_bio:
                self.storage.upload_bytes(frame_bio, bucket, frame_blob, "image/png")
                saved_names.append(frame_name)

        return saved_names, encoding_blob

    def _encode_video(
        self,
        event_id: str,
        bucket: str,
        video_blob: str,
        encoding_blob: str,
        frames_prefix: str,
    ) -> Tuple[List[str], Optional[str]]:
        """Download, sample, encode and store a single video.

        Args:
            event_id: Tracking ID.
            bucket: GCS bucket name.
            video_blob: Blob path of the source video.
            encoding_blob: Destination blob path for .npy encoding.
            frames_prefix: Destination prefix for sampled frame PNGs.

        Returns:
            Tuple of (saved_frame_names, encoding_blob_path).
            Returns ([], None) if no faces found.
        """
        logger.info(f"[{event_id}] Downloading video: gs://{bucket}/{video_blob}")
        video_data = self.storage.download_bytes(bucket, video_blob)
        if video_data is None:
            raise NonRecoverableError(
                f"[{event_id}] Failed to download video: gs://{bucket}/{video_blob}"
            )

        frames = self._extract_frames(video_data)
        if not frames:
            logger.warning(f"[{event_id}] No frames extracted from {video_blob}")
            return [], None

        logger.info(f"[{event_id}] Extracted {len(frames)} frames from {video_blob}")

        all_encodings: List[np.ndarray] = []
        unique_frames: List[np.ndarray] = []

        # Encode frames — concurrently for thread-safe backends, sequentially
        # for dlib — then deduplicate. Dedup runs single-threaded and in frame
        # order so the result is identical to the sequential path.
        frame_encodings = self._encode_frames(frames, event_id, video_blob)
        self._collect_unique(frame_encodings, all_encodings, unique_frames)

        if not all_encodings:
            logger.warning(f"[{event_id}] No faces found in {video_blob}")
            return [], None

        # DBSCAN cluster → keep dominant person
        dominant_encodings = self._cluster_dominant(all_encodings)
        logger.info(
            f"[{event_id}] {len(dominant_encodings)} dominant face encodings from {video_blob}"
        )

        # Save encodings as .npy
        npy_data = numpy_to_bytesio(np.array(dominant_encodings))
        self.storage.upload_bytes(npy_data, bucket, encoding_blob, "application/octet-stream")
        logger.info(f"[{event_id}] Saved encodings to gs://{bucket}/{encoding_blob}")

        # Save sampled frames as PNG
        saved_names: List[str] = []
        for idx, frame in enumerate(unique_frames[: len(dominant_encodings)], start=1):
            frame_name = f"{idx}.png"
            frame_blob = f"{frames_prefix}/{frame_name}"
            frame_bio = frame_to_bytesio(frame)
            if frame_bio:
                self.storage.upload_bytes(frame_bio, bucket, frame_blob, "image/png")
                saved_names.append(frame_name)

        return saved_names, encoding_blob

    def _encode_frames(
        self,
        frames: List[np.ndarray],
        event_id: str,
        source: str,
    ) -> List[Tuple[np.ndarray, List[np.ndarray]]]:
        """Encode faces in a batch of frames, parallel or sequential.

        The execution mode is chosen from the active encoder's capability:

        - ``encoder.supports_parallel`` is True **and** ``max_workers > 1`` →
          frames are encoded concurrently in a thread pool. Ideal for the
          fdetect gRPC backend, whose calls are network I/O-bound and
          thread-safe.
        - otherwise → frames are encoded one at a time. Required for the
          face_recognition/dlib backend, whose native code is NOT thread-safe
          (concurrent calls segfault).

        Only the stateless encode step is parallelised. Results are returned in
        the original frame order so the caller's deduplication is deterministic
        and identical across both modes.

        Args:
            frames: Sampled BGR frames from a single video.
            event_id: Tracking ID (for logs).
            source: Source blob path, used only in log messages.

        Returns:
            Ordered list of ``(frame, encodings)`` tuples. Frames whose encoding
            raised are skipped (logged and omitted from the result).
        """
        if self.encoder.supports_parallel and self.max_workers > 1:
            return self._encode_frames_parallel(frames, event_id, source)
        return self._encode_frames_sequential(frames, event_id, source)

    def _encode_frames_sequential(
        self,
        frames: List[np.ndarray],
        event_id: str,
        source: str,
    ) -> List[Tuple[np.ndarray, List[np.ndarray]]]:
        """Encode frames one-by-one (thread-unsafe backends, e.g. dlib)."""
        logger.info(f"[{event_id}] Encoding frames in sequentially")
        results: List[Tuple[np.ndarray, List[np.ndarray]]] = []
        for frame in frames:
            try:
                results.append((frame, self._process_frame(frame)))
            except Exception as e:
                logger.warning(f"[{event_id}] Frame skipped ({source}): {e}")
        return results

    def _encode_frames_parallel(
        self,
        frames: List[np.ndarray],
        event_id: str,
        source: str,
    ) -> List[Tuple[np.ndarray, List[np.ndarray]]]:
        """Encode frames concurrently (thread-safe, I/O-bound backends).

        Each frame is submitted to a thread pool; the per-frame results are
        reassembled in their original order before returning so the downstream
        deduplication produces output identical to the sequential path.
        """
        logger.info(f"[{event_id}] Encoding frames in parallel ( with {self.max_workers} workers )")
        ordered: List[Optional[List[np.ndarray]]] = [None] * len(frames)
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_idx = {
                executor.submit(self._process_frame, frame): idx
                for idx, frame in enumerate(frames)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    ordered[idx] = future.result()
                except Exception as e:
                    logger.warning(f"[{event_id}] Frame skipped ({source}): {e}")
        return [
            (frames[idx], encodings)
            for idx, encodings in enumerate(ordered)
            if encodings is not None
        ]

    def _collect_unique(
        self,
        frame_encodings: List[Tuple[np.ndarray, List[np.ndarray]]],
        all_encodings: List[np.ndarray],
        all_unique_frames: List[np.ndarray],
    ) -> None:
        """Fold non-duplicate encodings (and their frames) into the aggregates.

        Deduplication is stateful — each candidate is compared against the
        encodings accepted so far — so it must run sequentially on a single
        thread regardless of how the encodings were produced.

        Args:
            frame_encodings: Ordered ``(frame, encodings)`` pairs.
            all_encodings: Running list of accepted encodings (mutated in place).
            all_unique_frames: Running list of frames for accepted encodings
                (mutated in place, index-aligned with ``all_encodings``).
        """
        for frame, encodings in frame_encodings:
            for enc in encodings:
                if not self.encoder.is_duplicate(
                    enc, all_encodings, self.face_tolerance
                ):
                    all_encodings.append(enc)
                    all_unique_frames.append(frame)

    def _process_frame(self, frame: np.ndarray) -> List[np.ndarray]:
        """Encode faces in a single frame.

        Args:
            frame: BGR numpy frame array.

        Returns:
            List of face encoding vectors.
        """
        bio = frame_to_bytesio(frame)
        if bio is None:
            return []
        return self.encoder.encode_frame(bio)

    def _extract_frames(self, video_data: io.BytesIO) -> List[np.ndarray]:
        """Extract frames from a video BytesIO at the configured sample rate.

        Args:
            video_data: Video content as BytesIO.

        Returns:
            List of BGR numpy frame arrays.
        """
        suffix = self._detect_format(video_data)
        frames = []
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
                video_data.seek(0)
                tmp.write(video_data.read())
                tmp.flush()
                cap = cv2.VideoCapture(tmp.name)
                if not cap.isOpened():
                    return []
                fps = int(cap.get(cv2.CAP_PROP_FPS)) or 30
                interval = max(1, fps // max(1, 30 // self.frame_sample_rate))
                frame_count = 0
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    frame_count += 1
                    if frame_count % interval == 0 and frame is not None and frame.size > 0:
                        # Downscale for performance (mirrors old implementation)
                        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                        frames.append(small)
                cap.release()
        except Exception as e:
            logger.error(f"Frame extraction failed: {e}")
        return frames

    @staticmethod
    def _detect_format(video_data: io.BytesIO) -> str:
        """Detect video format from magic bytes.

        Args:
            video_data: Video BytesIO.

        Returns:
            File extension string (e.g., ".mp4").
        """
        video_data.seek(0)
        magic = video_data.read(8)
        video_data.seek(0)
        for sig, ext in _VIDEO_SIGNATURES.items():
            if magic.startswith(sig):
                return ext
        return ".mp4"  # fallback

    @staticmethod
    def _cluster_dominant(encodings: List[np.ndarray]) -> List[np.ndarray]:
        """Use DBSCAN to identify the dominant face cluster.

        Args:
            encodings: All unique face encodings from the video.

        Returns:
            Encodings belonging to the largest cluster (dominant person).
            Falls back to all encodings if clustering fails.
        """
        if len(encodings) < 3:
            return encodings
        try:
            X = np.array(encodings)
            labels = DBSCAN(eps=0.5, min_samples=2, metric="euclidean").fit_predict(X)
            unique, counts = np.unique(labels[labels >= 0], return_counts=True)
            if len(unique) == 0:
                return encodings
            dominant_label = unique[np.argmax(counts)]
            return [enc for enc, lbl in zip(encodings, labels) if lbl == dominant_label]
        except Exception:
            return encodings

    def _write_stage(
        self, bucket: str, base_path: str, event_id: str, filename: str, data: dict
    ) -> None:
        blob_path = build_stage_path(base_path, filename)
        self.storage.upload_json(data, bucket, blob_path)

