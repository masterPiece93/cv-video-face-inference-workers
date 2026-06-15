"""Integration tests for face_verification_worker.

Tests the verification pipeline against real GCS emulator:
- Loads .npy encodings from GCS
- Runs verification strategies (common_faces, with_profile)
- Writes stage tracking JSON
- Publishes downstream messages
- Error scenarios (missing encodings, corrupted .npy)
"""
import io
import json
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.integration.conftest import (
    BUCKET_NAME,
    ONBOARDING_TOPIC,
    ONBOARDING_SUB,
    PROJECT_ID,
    TEST_CANDIDATE_EMAIL,
    TEST_CANDIDATE_UID,
    TEST_EVENT_ID,
    TEST_ORG_ALIAS,
    TEST_ORG_ID,
    pull_messages,
    seed_fake_encodings,
)


pytestmark = pytest.mark.integration


class TestVerificationInputHandler:
    """Integration tests for the verification worker input handler."""

    def _make_message(self, payload: dict):
        msg = MagicMock()
        msg.data = json.dumps(payload).encode("utf-8")
        msg.ack = MagicMock()
        msg.nack = MagicMock()
        return msg

    def test_valid_message_acks(
        self, storage_client, pubsub_setup, base_verification_payload
    ):
        """Valid verification message is processed and acked."""
        from workers.face_verification_worker.src.handlers.input_handler import handle_message

        mock_service = MagicMock()
        msg = self._make_message(base_verification_payload)
        handle_message(msg, mock_service)

        mock_service.process.assert_called_once()
        msg.ack.assert_called_once()

    def test_invalid_json_acks(self, storage_client, pubsub_setup):
        """Malformed JSON → ack (no retry)."""
        from workers.face_verification_worker.src.handlers.input_handler import handle_message

        msg = MagicMock()
        msg.data = b"{{invalid"
        msg.ack = MagicMock()
        msg.nack = MagicMock()

        handle_message(msg, MagicMock())
        msg.ack.assert_called_once()

    def test_recoverable_error_nacks(
        self, storage_client, pubsub_setup, base_verification_payload
    ):
        """RecoverableError → nack for retry."""
        from common.services.errors import RecoverableError
        from workers.face_verification_worker.src.handlers.input_handler import handle_message

        mock_service = MagicMock()
        mock_service.process.side_effect = RecoverableError("Transient")

        msg = self._make_message(base_verification_payload)
        handle_message(msg, mock_service)
        msg.nack.assert_called_once()


class TestVerificationServicePipeline:
    """Integration tests for VideoFaceVerificationService with real GCS."""

    @pytest.fixture
    def verification_service(self, storage_client, pubsub_setup):
        """Create a verification service with common_faces strategy."""
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
        from workers.face_verification_worker.services.strategies import get_strategy
        from workers.face_verification_worker.services.verification import (
            VideoFaceVerificationService,
        )
        from workers.face_verification_worker.src.handlers.output_handler import (
            VerificationOutputHandler,
        )

        publisher = GCPPublisher(project_id=PROJECT_ID, topic_name=ONBOARDING_TOPIC)
        output_handler = VerificationOutputHandler(publisher)
        strategy = get_strategy("common_faces", tolerance=0.6)

        return VideoFaceVerificationService(
            strategy=strategy,
            storage=storage_client,
            output_handler=output_handler,
        )

    @pytest.fixture
    def with_profile_service(self, storage_client, pubsub_setup):
        """Verification service using with_profile strategy."""
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
        from workers.face_verification_worker.services.strategies import get_strategy
        from workers.face_verification_worker.services.verification import (
            VideoFaceVerificationService,
        )
        from workers.face_verification_worker.src.handlers.output_handler import (
            VerificationOutputHandler,
        )

        publisher = GCPPublisher(project_id=PROJECT_ID, topic_name=ONBOARDING_TOPIC)
        output_handler = VerificationOutputHandler(publisher)
        strategy = get_strategy("with_profile", tolerance=0.6)

        return VideoFaceVerificationService(
            strategy=strategy,
            storage=storage_client,
            output_handler=output_handler,
        )

    def _base_path(self) -> str:
        from common.utils.helpers import build_candidate_base_path

        return build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )

    def test_process_with_matching_encodings(
        self, storage_client, verification_service, subscriber_client, base_verification_payload
    ):
        """When profile and interview encodings are the same person → match."""
        base_path = self._base_path()

        # Seed IDENTICAL encodings for profile and interview (same person)
        same_encodings = np.random.rand(3, 128).astype(np.float64)
        for loc in ["profile", "interview_1"]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, same_encodings)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        verification_service.process(base_verification_payload)

        # Check stage
        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "verification.json")
        )
        assert stage["status"] == "OK"

        # Check downstream message
        messages = pull_messages(subscriber_client, ONBOARDING_SUB)
        assert len(messages) >= 1
        assert messages[0]["event_id"] == TEST_EVENT_ID
        assert "status" in messages[0]

    def test_process_with_different_encodings(
        self, storage_client, verification_service, base_verification_payload
    ):
        """When profile and interview are different people → verification still completes."""
        base_path = self._base_path()

        # Seed very different encodings (different people)
        profile_enc = np.ones((3, 128), dtype=np.float64) * 0.1
        interview_enc = np.ones((3, 128), dtype=np.float64) * 0.9

        for loc, enc in [("profile", profile_enc), ("interview_1", interview_enc)]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, enc)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        verification_service.process(base_verification_payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "verification.json")
        )
        assert stage["status"] == "OK"

    def test_process_with_profile_strategy(
        self, storage_client, with_profile_service, base_verification_payload
    ):
        """With_profile strategy produces valid results."""
        base_path = self._base_path()

        same_enc = np.random.rand(3, 128).astype(np.float64)
        for loc in ["profile", "interview_1"]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, same_enc)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        with_profile_service.process(base_verification_payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "verification.json")
        )
        assert stage["status"] == "OK"

    def test_process_missing_interview_encodings_raises(
        self, storage_client, verification_service, base_verification_payload
    ):
        """When no interview .npy exists → NonRecoverableError."""
        from common.services.errors import NonRecoverableError

        # Use a completely unique candidate so no data exists from prior tests
        payload = {
            **base_verification_payload,
            "event_id": "evt-missing-enc-001",
            "candidate_email": "no-data-candidate@example.com",
            "candidate_uid": "cand-no-data-999",
        }

        with pytest.raises(NonRecoverableError):
            verification_service.process(payload)

    def test_process_corrupted_npy_raises(
        self, storage_client, verification_service, base_verification_payload
    ):
        """Corrupted .npy file → NonRecoverableError."""
        from common.services.errors import NonRecoverableError

        base_path = self._base_path()
        payload = {**base_verification_payload, "event_id": "evt-corrupt-001"}

        # Seed profile with valid data
        valid_enc = np.random.rand(3, 128).astype(np.float64)
        buf = io.BytesIO()
        np.save(buf, valid_enc)
        buf.seek(0)
        storage_client.upload_bytes(
            buf, BUCKET_NAME,
            f"{base_path}/video_face_encodings/profile/profile.npy",
            "application/octet-stream",
        )

        # Seed interview_1 with garbage bytes
        storage_client.upload_bytes(
            b"not a valid npy file at all",
            BUCKET_NAME,
            f"{base_path}/video_face_encodings/interview_1/interview_1.npy",
            "application/octet-stream",
        )

        with pytest.raises(NonRecoverableError, match="No interview encodings"):
            verification_service.process(payload)

    def test_multiple_interviews_processed(
        self, storage_client, verification_service, base_verification_payload
    ):
        """Service processes multiple interview locations."""
        base_path = self._base_path()
        payload = {
            **base_verification_payload,
            "event_id": "evt-multi-interview-001",
            "lookup_map": {
                "profile": "profile",
                "interviews": ["interview_1", "interview_2"],
            },
        }

        enc = np.random.rand(3, 128).astype(np.float64)
        for loc in ["profile", "interview_1", "interview_2"]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, enc)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        verification_service.process(payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "verification.json")
        )
        assert stage["status"] == "OK"
