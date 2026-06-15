"""Integration tests for face_encoding_worker.

Tests the encoding pipeline against real GCS emulator:
- Input handler message parsing + ack/nack behavior
- VideoFaceEncodingService pipeline writing correct artifacts to GCS
- Output handler publishing downstream messages
- Stage tracking JSON written correctly
- Error scenarios (missing blobs, invalid data, etc.)
"""
import io
import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.integration.conftest import (
    BUCKET_NAME,
    PROJECT_ID,
    TEST_CANDIDATE_EMAIL,
    TEST_CANDIDATE_UID,
    TEST_EVENT_ID,
    TEST_ORG_ALIAS,
    TEST_ORG_ID,
    VERIFICATION_TOPIC,
    VERIFICATION_SUB,
    publish_message,
    pull_messages,
    seed_fake_video,
)


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestEncodingInputHandler:
    """Integration tests for the encoding worker input handler."""

    def _make_message(self, payload: dict, ack=None, nack=None):
        """Create a mock PubSub message."""
        msg = MagicMock()
        msg.data = json.dumps(payload).encode("utf-8")
        msg.ack = ack or MagicMock()
        msg.nack = nack or MagicMock()
        return msg

    def test_valid_message_acks(
        self, storage_client, pubsub_setup, base_encoding_payload
    ):
        """Valid message is processed and acked."""
        from workers.face_encoding_worker.src.handlers.input_handler import handle_message

        # Create a mock encoding service that succeeds
        mock_service = MagicMock()
        mock_service.process.return_value = None

        msg = self._make_message(base_encoding_payload)
        handle_message(msg, mock_service)

        mock_service.process.assert_called_once_with(base_encoding_payload)
        msg.ack.assert_called_once()

    def test_invalid_json_message_acks(self, storage_client, pubsub_setup):
        """Malformed JSON message is acked (not retried)."""
        from workers.face_encoding_worker.src.handlers.input_handler import handle_message

        msg = MagicMock()
        msg.data = b"not valid json{{"
        msg.ack = MagicMock()
        msg.nack = MagicMock()

        mock_service = MagicMock()
        handle_message(msg, mock_service)

        msg.ack.assert_called_once()
        mock_service.process.assert_not_called()

    def test_missing_required_fields_acks(
        self, storage_client, pubsub_setup
    ):
        """Message missing required fields raises schema violation → ack."""
        from workers.face_encoding_worker.src.handlers.input_handler import handle_message

        payload = {"event_id": "test-123"}  # Missing most fields
        msg = self._make_message(payload)
        mock_service = MagicMock()

        handle_message(msg, mock_service)
        # Schema violation is NonRecoverable → ack
        msg.ack.assert_called_once()

    def test_recoverable_error_nacks(
        self, storage_client, pubsub_setup, base_encoding_payload
    ):
        """RecoverableError from service causes nack (retry)."""
        from common.services.errors import RecoverableError
        from workers.face_encoding_worker.src.handlers.input_handler import handle_message

        mock_service = MagicMock()
        mock_service.process.side_effect = RecoverableError("Transient failure")

        msg = self._make_message(base_encoding_payload)
        handle_message(msg, mock_service)

        msg.nack.assert_called_once()

    def test_non_recoverable_error_acks(
        self, storage_client, pubsub_setup, base_encoding_payload
    ):
        """NonRecoverableError from service causes ack (no retry)."""
        from common.services.errors import NonRecoverableError
        from workers.face_encoding_worker.src.handlers.input_handler import handle_message

        mock_service = MagicMock()
        mock_service.process.side_effect = NonRecoverableError("Fatal")

        msg = self._make_message(base_encoding_payload)
        handle_message(msg, mock_service)

        msg.ack.assert_called_once()


class TestEncodingServicePipeline:
    """Integration tests for VideoFaceEncodingService with real GCS."""

    @pytest.fixture
    def mock_encoder(self):
        """A mock encoder that returns deterministic encodings."""
        from common.services.encoding.base import BaseEncoder

        class FakeEncoder(BaseEncoder):
            """Returns fixed encodings for any frame."""

            @property
            def name(self) -> str:
                return "fake_encoder"

            def encode_frame(self, frame_bytes):
                # Return 2 fake 128-d encodings per frame
                return [
                    np.random.rand(128).astype(np.float64),
                    np.random.rand(128).astype(np.float64),
                ]

            def calculate_distance(self, known_encodings, candidate):
                return np.linalg.norm(
                    np.array(known_encodings) - candidate, axis=1
                )

            def is_duplicate(self, encoding, existing, tolerance):
                if not existing:
                    return False
                distances = self.calculate_distance(existing, encoding)
                return bool(np.any(distances <= tolerance))

        return FakeEncoder()

    @pytest.fixture
    def encoding_service(
        self, storage_client, mock_encoder, pubsub_setup
    ):
        """Create a VideoFaceEncodingService with real GCS + mock encoder."""
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
        from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService
        from workers.face_encoding_worker.src.handlers.output_handler import EncodingOutputHandler

        publisher = GCPPublisher(
            project_id=PROJECT_ID,
            topic_name=VERIFICATION_TOPIC,
        )
        output_handler = EncodingOutputHandler(publisher)

        return VideoFaceEncodingService(
            encoder=mock_encoder,
            storage=storage_client,
            output_handler=output_handler,
            frame_sample_rate=30,
            face_tolerance=0.5,
            max_workers=2,
        )

    def _seed_video_snippets(self, storage_client, base_path: str, locations: list[str]):
        """Seed minimal video files in GCS for each location."""
        for loc in locations:
            blob = f"{base_path}/video_snippets/{loc}/{loc}_000.mp4"
            seed_fake_video(storage_client, BUCKET_NAME, blob)

    def test_process_writes_stage_tracking(
        self, storage_client, encoding_service, base_encoding_payload
    ):
        """Processing writes stage JSON to GCS even if no faces found."""
        from common.utils.helpers import build_candidate_base_path, build_stage_path

        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )
        # Seed video snippets (they won't have real frames)
        self._seed_video_snippets(storage_client, base_path, ["profile", "interview_1"])

        # Patch _extract_frames to return a dummy frame so encoder runs
        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(
            encoding_service, "_extract_frames", return_value=[dummy_frame]
        ):
            encoding_service.process(base_encoding_payload)

        # Check stage file exists
        stage_blob = build_stage_path(base_path, "encoding.json")
        stage_data = storage_client.download_json(BUCKET_NAME, stage_blob)
        assert stage_data is not None
        assert stage_data["status"] == "OK"
        assert stage_data["event_id"] == TEST_EVENT_ID

    def test_process_uploads_npy_encodings(
        self, storage_client, encoding_service, base_encoding_payload
    ):
        """Processing uploads .npy encoding files to correct GCS paths."""
        from common.utils.helpers import build_candidate_base_path

        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )
        self._seed_video_snippets(storage_client, base_path, ["profile", "interview_1"])

        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(
            encoding_service, "_extract_frames", return_value=[dummy_frame]
        ):
            encoding_service.process(base_encoding_payload)

        # Verify .npy files were uploaded
        profile_npy = f"{base_path}/video_face_encodings/profile/profile.npy"
        data = storage_client.download_bytes(BUCKET_NAME, profile_npy)
        assert data is not None
        arr = np.load(data)
        assert arr.shape[1] == 128  # 128-d face encodings

        interview_npy = f"{base_path}/video_face_encodings/interview_1/interview_1.npy"
        data = storage_client.download_bytes(BUCKET_NAME, interview_npy)
        assert data is not None

    def test_process_publishes_downstream_message(
        self, storage_client, encoding_service, subscriber_client, base_encoding_payload
    ):
        """Processing publishes a correctly shaped message to the verification topic."""
        from common.utils.helpers import build_candidate_base_path

        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )
        self._seed_video_snippets(storage_client, base_path, ["profile", "interview_1"])

        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(
            encoding_service, "_extract_frames", return_value=[dummy_frame]
        ):
            encoding_service.process(base_encoding_payload)

        # Pull message from verification subscription
        messages = pull_messages(subscriber_client, VERIFICATION_SUB)
        assert len(messages) >= 1
        msg = messages[0]
        assert msg["candidate_email"] == TEST_CANDIDATE_EMAIL
        assert msg["event_id"] == TEST_EVENT_ID
        assert "sampled_frames" in msg
        assert "lookup_map" in msg

    def test_process_no_video_blobs_still_completes(
        self, storage_client, encoding_service, base_encoding_payload
    ):
        """When no video blobs exist, pipeline still writes stage + publishes."""
        from common.utils.helpers import build_candidate_base_path, build_stage_path

        # Use a fresh event to avoid collisions
        payload = {**base_encoding_payload, "event_id": "evt-no-blobs-001"}
        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )

        # Don't seed any video blobs — the prefix will be empty
        encoding_service.process(payload)

        stage_blob = build_stage_path(base_path, "encoding.json")
        stage_data = storage_client.download_json(BUCKET_NAME, stage_blob)
        assert stage_data is not None
        assert stage_data["status"] == "OK"

    def test_process_uploads_sampled_frame_pngs(
        self, storage_client, encoding_service, base_encoding_payload
    ):
        """Processing saves sampled frame PNGs to GCS."""
        from common.utils.helpers import build_candidate_base_path

        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )
        self._seed_video_snippets(storage_client, base_path, ["profile"])

        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(
            encoding_service, "_extract_frames", return_value=[dummy_frame]
        ):
            encoding_service.process(base_encoding_payload)

        # Check at least one PNG was uploaded
        frames_prefix = f"{base_path}/sampled_frames/profile/"
        blobs = storage_client.list_blobs(BUCKET_NAME, frames_prefix)
        png_blobs = [b for b in blobs if b.endswith(".png")]
        assert len(png_blobs) >= 1


class TestEncodingServiceErrorScenarios:
    """Error handling integration tests for encoding service."""

    @pytest.fixture
    def failing_encoder(self):
        """An encoder that always raises."""
        from common.services.encoding.base import BaseEncoder

        class FailingEncoder(BaseEncoder):
            @property
            def name(self) -> str:
                return "failing_encoder"

            def encode_frame(self, frame_bytes):
                raise RuntimeError("Encoder crash!")

            def calculate_distance(self, known, candidate):
                return np.array([])

            def is_duplicate(self, encoding, existing, tolerance):
                return False

        return FailingEncoder()

    def test_encoder_failure_writes_ok_stage_with_no_faces(
        self, storage_client, failing_encoder, pubsub_setup, base_encoding_payload
    ):
        """When encoder crashes on every frame, pipeline completes (no faces) with OK status.

        The encoding loop catches per-frame exceptions and logs a warning.
        If ALL frames fail, the result is "no faces found" → still completes.
        """
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
        from common.utils.helpers import build_candidate_base_path, build_stage_path
        from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService
        from workers.face_encoding_worker.src.handlers.output_handler import EncodingOutputHandler

        publisher = GCPPublisher(project_id=PROJECT_ID, topic_name=VERIFICATION_TOPIC)
        output_handler = EncodingOutputHandler(publisher)

        service = VideoFaceEncodingService(
            encoder=failing_encoder,
            storage=storage_client,
            output_handler=output_handler,
            frame_sample_rate=30,
            face_tolerance=0.5,
            max_workers=2,
        )

        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )
        # Seed a video so _extract_frames is called
        blob = f"{base_path}/video_snippets/profile/profile_000.mp4"
        seed_fake_video(storage_client, BUCKET_NAME, blob)

        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(service, "_extract_frames", return_value=[dummy_frame]):
            # Should NOT raise — per-frame errors are caught
            service.process(base_encoding_payload)

        stage_blob = build_stage_path(base_path, "encoding.json")
        stage_data = storage_client.download_json(BUCKET_NAME, stage_blob)
        assert stage_data is not None
        assert stage_data["status"] == "OK"
