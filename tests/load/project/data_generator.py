"""Load test data generator — transforms raw seed data into worker-ready format.

Takes raw candidate videos (profile + interviews) and processes them through
the pipeline to produce correctly formatted GCS data for any target worker.

Supported targets:
- face_encoding_worker: Needs video snippets in GCS under video_snippets/
- face_verification_worker: Needs .npy encodings in video_face_encodings/
- onboarding_verification_worker: Same as verification

Seed sources:
- Local directory: ``/path/to/candidate_data``
- GCS bucket/prefix: ``gs://my-bucket/candidate_data`` (videos are downloaded
  to a temporary file on demand, since OpenCV cannot stream from GCS).

Data flow:
    Raw seed videos → (split/copy) → video_snippets/ in GCS
                    → (encode)     → video_face_encodings/ in GCS
                    → (generate)   → Pub/Sub message payloads

This module works in collaboration with data_loader.py to prepare fixtures
or upload directly to GCS (live or fake).
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

GCS_URI_SCHEME = "gs://"


def is_gcs_uri(value: Any) -> bool:
    """Return True if ``value`` looks like a ``gs://bucket/prefix`` URI."""
    return isinstance(value, str) and value.startswith(GCS_URI_SCHEME)


def parse_gcs_uri(uri: str) -> Tuple[str, str]:
    """Split a ``gs://bucket/prefix`` URI into ``(bucket, prefix)``.

    The prefix is returned without a leading or trailing slash. A bare
    ``gs://bucket`` yields an empty prefix.
    """
    without_scheme = uri[len(GCS_URI_SCHEME):]
    parts = without_scheme.split("/", 1)
    bucket = parts[0]
    prefix = parts[1].strip("/") if len(parts) > 1 else ""
    return bucket, prefix



class SeedVideo:
    """A reference to a single seed video, either local or in GCS.

    OpenCV cannot read directly from GCS, so a GCS-backed video is downloaded
    to a temporary file on first access via :meth:`ensure_local`. Callers must
    invoke :meth:`cleanup` when finished to remove any temporary file.
    """

    def __init__(
        self,
        name: str,
        *,
        local_path: Optional[Path] = None,
        gcs_bucket: Optional[str] = None,
        gcs_blob: Optional[str] = None,
        storage=None,
    ):
        self.name = name  # filename, e.g. "interview_1.mp4"
        self._local_path = Path(local_path) if local_path is not None else None
        self._gcs_bucket = gcs_bucket
        self._gcs_blob = gcs_blob
        self._storage = storage
        self._temp_path: Optional[Path] = None

    @property
    def stem(self) -> str:
        return Path(self.name).stem

    @property
    def is_remote(self) -> bool:
        return self._gcs_blob is not None

    def ensure_local(self) -> Optional[Path]:
        """Return a local filesystem path for this video.

        For a local seed this is the original file. For a GCS seed the blob is
        downloaded to a temporary file (once) and that path is returned.
        Returns ``None`` if the video could not be materialized.
        """
        if self._local_path is not None:
            return self._local_path
        if self._temp_path is not None:
            return self._temp_path
        if self._storage is None or self._gcs_blob is None:
            return None

        data = self._storage.download_bytes(self._gcs_bucket, self._gcs_blob)
        if data is None:
            logger.warning(
                f"    Failed to download seed video gs://{self._gcs_bucket}/{self._gcs_blob}"
            )
            return None

        suffix = Path(self.name).suffix or ".mp4"
        fd, tmp = tempfile.mkstemp(prefix="seedvid_", suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.getbuffer())
        self._temp_path = Path(tmp)
        logger.debug(
            f"    Downloaded gs://{self._gcs_bucket}/{self._gcs_blob} → {self._temp_path}"
        )
        return self._temp_path

    def cleanup(self) -> None:
        """Remove the temporary download, if any."""
        if self._temp_path is not None:
            try:
                self._temp_path.unlink(missing_ok=True)
            except OSError as e:
                logger.debug(f"    Could not remove temp file {self._temp_path}: {e}")
            self._temp_path = None


class SeedCandidate:
    """Represents a single candidate from the seed data (local dir or GCS)."""

    def __init__(self, email: str, videos: Dict[str, List[SeedVideo]]):
        self.email = email
        self.uid = f"cand-{uuid.uuid4().hex[:8]}"
        self.videos: Dict[str, List[SeedVideo]] = videos

    @staticmethod
    def _classify(stem_lower: str) -> str:
        """Return the slot ('profile' or 'interviews') for a video stem."""
        if "profile" in stem_lower:
            return "profile"
        # Both "interview*" and anything else default to interviews.
        return "interviews"

    @classmethod
    def from_local_dir(cls, candidate_dir: Path) -> "SeedCandidate":
        """Build a candidate by scanning a local directory for *.mp4 files."""
        videos: Dict[str, List[SeedVideo]] = {"profile": [], "interviews": []}
        for video_file in sorted(candidate_dir.glob("*.mp4")):
            video = SeedVideo(name=video_file.name, local_path=video_file)
            videos[cls._classify(video_file.stem.lower())].append(video)
        return cls(email=candidate_dir.name, videos=videos)

    @classmethod
    def from_gcs(
        cls, email: str, blob_names: List[str], bucket: str, storage
    ) -> "SeedCandidate":
        """Build a candidate from a list of GCS *.mp4 blob names."""
        videos: Dict[str, List[SeedVideo]] = {"profile": [], "interviews": []}
        for blob in sorted(blob_names):
            filename = blob.rsplit("/", 1)[-1]
            if not filename.lower().endswith(".mp4"):
                continue
            video = SeedVideo(
                name=filename, gcs_bucket=bucket, gcs_blob=blob, storage=storage
            )
            videos[cls._classify(Path(filename).stem.lower())].append(video)
        return cls(email=email, videos=videos)

    @property
    def interview_names(self) -> List[str]:
        return [v.stem for v in self.videos["interviews"]]

    @property
    def has_videos(self) -> bool:
        return bool(self.videos["profile"] or self.videos["interviews"])

    def cleanup(self) -> None:
        """Remove any temporary files created for this candidate's videos."""
        for slot_videos in self.videos.values():
            for video in slot_videos:
                video.cleanup()

    def __repr__(self):
        return (
            f"SeedCandidate(email={self.email}, "
            f"profile={len(self.videos['profile'])} videos, "
            f"interviews={len(self.videos['interviews'])} videos)"
        )


class LoadTestDataGenerator:
    """Generates load-test-ready data from raw seed videos.

    Processes seed data and uploads to the target storage (fake-GCS or live GCS)
    in the exact structure expected by each worker.
    """

    def __init__(
        self,
        seed_dir,
        target: str = "face_encoding_worker",
        bucket_name: str = "loadtest-bucket",
        org_alias: str = "loadtest-org",
        org_id: str = "org-loadtest",
        storage_service=None,
        encoder=None,
        frame_sample_rate: int = 30,
        face_tolerance: float = 0.5,
        max_frames_per_video: int = 30,
        max_encodings_per_slot: int = 20,
        seed_storage=None,
    ):
        """Initialize the data generator.

        Args:
            seed_dir: Path to the candidate_data directory, or a ``gs://`` URI
                pointing at a bucket/prefix that contains the candidate folders.
            target: Target worker ("face_encoding_worker" | "face_verification_worker").
            bucket_name: GCS bucket to upload data to.
            org_alias: Organization alias for path construction.
            org_id: Organization ID for path construction.
            storage_service: GCPStorageService instance for the DESTINATION
                (uses emulator if env set). Used to upload worker-ready data.
            encoder: BaseEncoder instance (for generating encodings).
            frame_sample_rate: Frames to sample per second.
            face_tolerance: Deduplication distance threshold.
            max_frames_per_video: Cap on frames decoded per video (perf).
            max_encodings_per_slot: Cap on encodings collected per slot (perf).
            seed_storage: GCPStorageService used to READ a ``gs://`` seed source.
                Defaults to ``storage_service`` (i.e. same storage system).
        """
        seed_str = str(seed_dir)
        self._seed_is_gcs = is_gcs_uri(seed_str)
        if self._seed_is_gcs:
            self._seed_dir = seed_str
            self._seed_gcs_bucket, self._seed_gcs_prefix = parse_gcs_uri(seed_str)
        else:
            self._seed_dir = Path(seed_dir)
            self._seed_gcs_bucket = None
            self._seed_gcs_prefix = None
        self._target = target
        self._bucket_name = bucket_name
        self._org_alias = org_alias
        self._org_id = org_id
        self._storage = storage_service
        self._seed_storage = seed_storage if seed_storage is not None else storage_service
        self._encoder = encoder
        self._frame_sample_rate = frame_sample_rate
        self._face_tolerance = face_tolerance
        self._max_frames_per_video = max_frames_per_video
        self._max_encodings_per_slot = max_encodings_per_slot
        self._candidates: List[SeedCandidate] = []

    def scan_seed_data(self) -> List[SeedCandidate]:
        """Scan the seed source (local dir or GCS) for candidate folders."""
        if self._seed_is_gcs:
            self._candidates = self._scan_gcs_seed()
        else:
            self._candidates = self._scan_local_seed()

        logger.info(f"Found {len(self._candidates)} candidates in seed data")
        for c in self._candidates:
            logger.info(f"  {c}")
        return self._candidates

    def _scan_local_seed(self) -> List[SeedCandidate]:
        """Scan a local directory for candidate sub-folders."""
        if not self._seed_dir.exists():
            raise FileNotFoundError(f"Seed directory not found: {self._seed_dir}")

        candidates: List[SeedCandidate] = []
        for entry in sorted(self._seed_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                candidate = SeedCandidate.from_local_dir(entry)
                if candidate.has_videos:
                    candidates.append(candidate)
        return candidates

    def _scan_gcs_seed(self) -> List[SeedCandidate]:
        """Scan a ``gs://bucket/prefix`` source for candidate folders.

        Expects the layout ``<prefix>/<candidate_email>/<video>.mp4`` — i.e.
        the same structure as the local ``candidate_data`` directory.
        """
        if self._seed_storage is None:
            raise ValueError(
                "A storage service is required to read seed data from GCS. "
                "Pass seed_storage= (or storage_service=) to LoadTestDataGenerator."
            )

        norm_prefix = f"{self._seed_gcs_prefix}/" if self._seed_gcs_prefix else ""
        blob_names = self._seed_storage.list_blobs(self._seed_gcs_bucket, norm_prefix)

        # Group .mp4 blobs by the candidate folder (first path segment after prefix).
        groups: Dict[str, List[str]] = {}
        for name in blob_names:
            if not name.lower().endswith(".mp4"):
                continue
            rel = name[len(norm_prefix):] if norm_prefix and name.startswith(norm_prefix) else name
            parts = rel.split("/")
            if len(parts) < 2:
                # Video sits directly under the prefix with no candidate folder.
                logger.debug(f"  Skipping seed blob without candidate folder: {name}")
                continue
            groups.setdefault(parts[0], []).append(name)

        candidates: List[SeedCandidate] = []
        for email in sorted(groups):
            candidate = SeedCandidate.from_gcs(
                email, groups[email], self._seed_gcs_bucket, self._seed_storage
            )
            if candidate.has_videos:
                candidates.append(candidate)
        return candidates

    def generate(self) -> List[Dict[str, Any]]:
        """Generate load test data for the configured target worker.

        Returns:
            List of Pub/Sub message payloads ready for publishing.
        """
        if not self._candidates:
            self.scan_seed_data()

        if self._target == "face_encoding_worker":
            return self._generate_for_encoding_worker()
        elif self._target in ("face_verification_worker", "onboarding_verification_worker"):
            return self._generate_for_verification_worker()
        else:
            raise ValueError(f"Unknown target worker: {self._target}")

    # ─── Face Encoding Worker ─────────────────────────────────────────────────

    def _generate_for_encoding_worker(self) -> List[Dict[str, Any]]:
        """Generate data for face_encoding_worker.

        The encoding worker expects:
        - Video snippets in GCS: {base_path}/video_snippets/{slot}/{files}.mp4
        - Input message with lookup_map pointing to those slots
        """
        payloads = []
        for candidate in self._candidates:
            try:
                base_path = self._build_base_path(candidate)
                snippets_base = f"{base_path}/video_snippets"

                # Upload profile videos
                if candidate.videos["profile"]:
                    for video in candidate.videos["profile"]:
                        blob_path = f"{snippets_base}/profile/{video.name}"
                        self._upload_video(video, blob_path)

                # Upload interview videos
                for video in candidate.videos["interviews"]:
                    slot_name = video.stem  # e.g., "interview_1"
                    blob_path = f"{snippets_base}/{slot_name}/{video.name}"
                    self._upload_video(video, blob_path)

                # Build payload
                payload = {
                    "candidate_email": candidate.email,
                    "candidate_uid": candidate.uid,
                    "org_id": self._org_id,
                    "org_alias": self._org_alias,
                    "bucket_name": self._bucket_name,
                    "event_id": f"loadtest-{uuid.uuid4().hex[:12]}",
                    "lookup_map": {
                        "profile": "profile",
                        "interviews": candidate.interview_names,
                    },
                    "extra_info": {"load_test": True, "source": "seed_data"},
                }
                payloads.append(payload)
                logger.info(f"  Prepared encoding payload for {candidate.email}")
            finally:
                candidate.cleanup()

        return payloads

    # ─── Face Verification Worker ─────────────────────────────────────────────

    def _generate_for_verification_worker(self) -> List[Dict[str, Any]]:
        """Generate data for face_verification_worker.

        The verification worker expects:
        - .npy encoding files in GCS: {base_path}/video_face_encodings/{slot}/{slot}.npy
        - Input message with lookup_map + sampled_frames

        This FIRST processes the videos through encoding to produce .npy files,
        then generates the verification payloads.
        """
        if self._encoder is None:
            raise ValueError(
                "An encoder instance is required to generate verification worker data. "
                "Pass encoder= to LoadTestDataGenerator or use --encoder-backend in CLI."
            )

        payloads = []
        for candidate in self._candidates:
            try:
                base_path = self._build_base_path(candidate)
                encodings_base = f"{base_path}/video_face_encodings"
                frames_base = f"{base_path}/sampled_frames"
                sampled_frames: Dict[str, Any] = {"profile": [], "interviews": {}}

                # Process profile videos → encodings
                if candidate.videos["profile"]:
                    profile_encodings = self._encode_videos(candidate.videos["profile"])
                    if profile_encodings is not None:
                        npy_blob = f"{encodings_base}/profile/profile.npy"
                        self._upload_npy(profile_encodings, npy_blob)
                        # Generate dummy frame references
                        frame_names = [f"{i+1}.png" for i in range(min(5, len(profile_encodings)))]
                        sampled_frames["profile"] = frame_names
                        self._upload_dummy_frames(frame_names, f"{frames_base}/profile")

                # Process interview videos → encodings
                for video in candidate.videos["interviews"]:
                    slot_name = video.stem
                    interview_encodings = self._encode_videos([video])
                    if interview_encodings is not None:
                        npy_blob = f"{encodings_base}/{slot_name}/{slot_name}.npy"
                        self._upload_npy(interview_encodings, npy_blob)
                        frame_names = [f"{i+1}.png" for i in range(min(5, len(interview_encodings)))]
                        sampled_frames["interviews"][slot_name] = frame_names
                        self._upload_dummy_frames(frame_names, f"{frames_base}/{slot_name}")

                # Build payload
                payload = {
                    "candidate_email": candidate.email,
                    "candidate_uid": candidate.uid,
                    "org_id": self._org_id,
                    "org_alias": self._org_alias,
                    "bucket_name": self._bucket_name,
                    "event_id": f"loadtest-{uuid.uuid4().hex[:12]}",
                    "lookup_map": {
                        "profile": "profile",
                        "interviews": candidate.interview_names,
                    },
                    "sampled_frames": sampled_frames,
                    "extra_info": {"load_test": True, "source": "seed_data_encoded"},
                }
                payloads.append(payload)
                logger.info(f"  Prepared verification payload for {candidate.email}")
            finally:
                candidate.cleanup()

        return payloads

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _build_base_path(self, candidate: SeedCandidate) -> str:
        return f"{self._org_alias}/{self._org_id}/{candidate.email}/{candidate.uid}"

    def _upload_video(self, video: "SeedVideo", blob_path: str) -> None:
        """Upload a seed video to the destination GCS bucket.

        The video is materialized to a local file first (downloaded from the
        seed bucket if the source is GCS), then uploaded to ``blob_path``.
        """
        if self._storage is None:
            logger.warning(f"No storage service — skipping upload of {blob_path}")
            return
        local_path = video.ensure_local()
        if local_path is None:
            logger.warning(f"Could not read seed video {video.name} — skipping {blob_path}")
            return
        with open(local_path, "rb") as f:
            data = io.BytesIO(f.read())
        self._storage.upload_bytes(data, self._bucket_name, blob_path, "video/mp4")
        logger.debug(f"  Uploaded: gs://{self._bucket_name}/{blob_path}")

    def _upload_npy(self, encodings: np.ndarray, blob_path: str) -> None:
        """Upload a numpy array as .npy to GCS."""
        if self._storage is None:
            logger.warning(f"No storage service — skipping upload of {blob_path}")
            return
        bio = io.BytesIO()
        np.save(bio, encodings)
        bio.seek(0)
        self._storage.upload_bytes(bio, self._bucket_name, blob_path, "application/octet-stream")
        logger.debug(f"  Uploaded encodings: gs://{self._bucket_name}/{blob_path}")

    def _upload_dummy_frames(self, frame_names: List[str], prefix: str) -> None:
        """Upload placeholder frame PNGs (1x1 black pixel)."""
        if self._storage is None:
            return
        # Create a minimal valid PNG
        dummy_frame = np.zeros((64, 64, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".png", dummy_frame)
        for name in frame_names:
            blob_path = f"{prefix}/{name}"
            self._storage.upload_bytes(
                io.BytesIO(buf.tobytes()),
                self._bucket_name,
                blob_path,
                "image/png",
            )

    def _encode_videos(self, videos: List["SeedVideo"]) -> Optional[np.ndarray]:
        """Extract frames from videos and encode faces.

        Returns:
            Numpy array of face encodings, or None if no faces found.
        """
        from sklearn.cluster import DBSCAN

        all_encodings: List[np.ndarray] = []

        for video in videos:
            logger.info(f"    Encoding: {video.name}")
            frames = self._extract_frames(video)
            for frame in frames:
                try:
                    _, buf = cv2.imencode(".jpg", frame)
                    bio = io.BytesIO(buf.tobytes())
                    bio.seek(0)
                    encodings = self._encoder.encode_frame(bio)
                    for enc in encodings:
                        # Deduplication
                        if not all_encodings or not self._is_duplicate(enc, all_encodings):
                            all_encodings.append(enc)
                except Exception as e:
                    logger.debug(f"    Frame skipped: {e}")
                # Stop once we have enough distinct encodings for this slot.
                if len(all_encodings) >= self._max_encodings_per_slot:
                    break
            if len(all_encodings) >= self._max_encodings_per_slot:
                break

        if not all_encodings:
            return None

        # DBSCAN clustering for dominant face
        if len(all_encodings) >= 3:
            try:
                X = np.array(all_encodings)
                labels = DBSCAN(eps=0.5, min_samples=2, metric="euclidean").fit_predict(X)
                unique, counts = np.unique(labels[labels >= 0], return_counts=True)
                if len(unique) > 0:
                    dominant = unique[np.argmax(counts)]
                    all_encodings = [e for e, l in zip(all_encodings, labels) if l == dominant]
            except Exception:
                pass

        return np.array(all_encodings)

    def _extract_frames(self, video: "SeedVideo") -> List[np.ndarray]:
        """Extract sampled frames from a seed video."""
        frames: List[np.ndarray] = []
        local_path = video.ensure_local()
        if local_path is None:
            logger.warning(f"    Cannot read seed video: {video.name}")
            return frames

        cap = cv2.VideoCapture(str(local_path))
        if not cap.isOpened():
            logger.warning(f"    Cannot open video: {video.name}")
            return frames

        fps = int(cap.get(cv2.CAP_PROP_FPS)) or 30
        interval = max(1, fps // max(1, 30 // self._frame_sample_rate))
        count = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            count += 1
            if count % interval == 0 and frame is not None and frame.size > 0:
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                frames.append(small)
                # Stop early once we have enough sampled frames — avoids
                # decoding entire multi-hundred-MB videos for a load test.
                if len(frames) >= self._max_frames_per_video:
                    break

        cap.release()
        logger.info(f"    Extracted {len(frames)} frames from {video.name}")
        return frames

    def _is_duplicate(self, enc: np.ndarray, existing: List[np.ndarray]) -> bool:
        """Check if encoding is a duplicate."""
        for e in existing:
            dist = np.linalg.norm(np.array(e) - np.array(enc))
            if dist <= self._face_tolerance:
                return True
        return False

    # ─── Save payloads to fixtures ────────────────────────────────────────────

    def save_payloads_to_fixtures(
        self, payloads: List[Dict[str, Any]], fixtures_dir: Path
    ) -> Path:
        """Save generated payloads as a JSON fixture file.

        Works with data_loader.py's FixturesDataLoader.
        """
        fixtures_dir = Path(fixtures_dir)
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        output_path = fixtures_dir / f"seed_{self._target}_{int(uuid.uuid4().int % 1e8)}.json"
        with open(output_path, "w") as f:
            json.dump(payloads, f, indent=2)
        logger.info(f"Saved {len(payloads)} payloads to {output_path}")
        return output_path
