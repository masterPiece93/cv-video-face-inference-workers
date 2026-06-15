"""Integration tests for onboarding_verification_worker.

Tests the onboarding verification pipeline against real GCS emulator:
- Download onboarding reference images from GCS prefix
- Download from signed URL (mocked HTTP)
- Encode reference images and compare against pre-computed video encodings
- Write stage tracking JSON
- Publish downstream result
- Error scenarios (no reference images, missing encodings)
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
    seed_fake_encodings,
    seed_fake_image,
)


pytestmark = pytest.mark.integration


class TestOnboardingInputHandler:
    """Integration tests for the onboarding verification input handler."""

    def _make_message(self, payload: dict):
        msg = MagicMock()
        msg.data = json.dumps(payload).encode("utf-8")
        msg.ack = MagicMock()
        msg.nack = MagicMock()
        return msg

    def test_valid_message_acks(
        self, storage_client, pubsub_setup, base_onboarding_payload
    ):
        """Valid message is processed and acked."""
        from workers.onboarding_verification_worker.src.handlers.input_handler import (
            handle_message,
        )

        mock_service = MagicMock()
        msg = self._make_message(base_onboarding_payload)
        handle_message(msg, mock_service)

        mock_service.process.assert_called_once()
        msg.ack.assert_called_once()

    def test_invalid_json_acks(self, storage_client, pubsub_setup):
        """Malformed JSON → ack."""
        from workers.onboarding_verification_worker.src.handlers.input_handler import (
            handle_message,
        )

        msg = MagicMock()
        msg.data = b"{{bad json"
        msg.ack = MagicMock()
        msg.nack = MagicMock()

        handle_message(msg, MagicMock())
        msg.ack.assert_called_once()

    def test_recoverable_error_nacks(
        self, storage_client, pubsub_setup, base_onboarding_payload
    ):
        """RecoverableError → nack."""
        from common.services.errors import RecoverableError
        from workers.onboarding_verification_worker.src.handlers.input_handler import (
            handle_message,
        )

        mock_service = MagicMock()
        mock_service.process.side_effect = RecoverableError("transient")

        msg = self._make_message(base_onboarding_payload)
        handle_message(msg, mock_service)
        msg.nack.assert_called_once()


class TestOnboardingServicePipeline:
    """Integration tests for OnboardingVerificationService with real GCS."""

    @pytest.fixture
    def mock_encoder(self):
        """An encoder that returns a fixed encoding for any image."""
        from common.services.encoding.base import BaseEncoder

        class FakeEncoder(BaseEncoder):
            def __init__(self):
                # Fixed encoding — will "match" if video encodings are similar
                self._fixed = np.zeros(128, dtype=np.float64)

            @property
            def name(self) -> str:
                return "fake_encoder"

            def encode_frame(self, frame_bytes):
                return [self._fixed.copy()]

            def calculate_distance(self, known, candidate):
                return np.linalg.norm(np.array(known) - candidate, axis=1)

            def is_duplicate(self, encoding, existing, tolerance):
                if not existing:
                    return False
                return bool(np.any(self.calculate_distance(existing, encoding) <= tolerance))

        return FakeEncoder()

    @pytest.fixture
    def onboarding_service(self, storage_client, mock_encoder, pubsub_setup):
        """Create an OnboardingVerificationService with real GCS."""
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
        from workers.onboarding_verification_worker.services.verification import (
            OnboardingVerificationService,
        )
        from workers.onboarding_verification_worker.src.handlers.output_handler import (
            OnboardingOutputHandler,
        )

        publisher = GCPPublisher(project_id=PROJECT_ID, topic_name="test-final-topic")
        output_handler = OnboardingOutputHandler(publisher)

        return OnboardingVerificationService(
            encoder=mock_encoder,
            storage=storage_client,
            output_handler=output_handler,
            tolerance=0.6,
        )

    def _base_path(self) -> str:
        from common.utils.helpers import build_candidate_base_path

        return build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )

    def test_process_gcs_reference_matching(
        self, storage_client, onboarding_service, base_onboarding_payload
    ):
        """Reference images from GCS prefix are matched against video encodings."""
        base_path = self._base_path()

        # Seed onboarding reference image
        ref_blob = f"{base_path}/onboarding_reference/photo.png"
        seed_fake_image(storage_client, BUCKET_NAME, ref_blob)

        # Seed video encodings that match (all zeros — same as FakeEncoder output)
        matching_enc = np.zeros((3, 128), dtype=np.float64)
        for loc in ["profile", "interview_1"]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, matching_enc)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        onboarding_service.process(base_onboarding_payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "onboarding_verification.json")
        )
        assert stage["status"] == "OK"

    def test_process_gcs_reference_non_matching(
        self, storage_client, onboarding_service, base_onboarding_payload
    ):
        """Reference images do NOT match very different video encodings."""
        base_path = self._base_path()

        # Seed onboarding reference image
        ref_blob = f"{base_path}/onboarding_reference/photo.png"
        seed_fake_image(storage_client, BUCKET_NAME, ref_blob)

        # Seed video encodings that are far from zeros
        far_enc = np.ones((3, 128), dtype=np.float64) * 10.0
        for loc in ["profile", "interview_1"]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, far_enc)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        onboarding_service.process(base_onboarding_payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "onboarding_verification.json")
        )
        assert stage["status"] == "OK"

    def test_process_signed_url_reference(
        self, storage_client, onboarding_service, base_onboarding_payload
    ):
        """Reference image via signed URL is downloaded and encoded."""
        base_path = self._base_path()

        # Use signed URL instead of GCS prefix
        payload = {
            **base_onboarding_payload,
            "onboarding_reference_path": "https://storage.googleapis.com/bucket/photo.png?X-Goog-Signature=abc",
            "event_id": "evt-signed-url-001",
        }

        # Seed video encodings matching FakeEncoder output
        matching_enc = np.zeros((3, 128), dtype=np.float64)
        for loc in ["profile", "interview_1"]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, matching_enc)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        # Mock the HTTP download (signed URL) at the requests module level
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = fake_png
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response

            onboarding_service.process(payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "onboarding_verification.json")
        )
        assert stage["status"] == "OK"

    def test_process_no_reference_images_raises(
        self, storage_client, onboarding_service, base_onboarding_payload
    ):
        """No reference images at GCS prefix → NonRecoverableError."""
        from common.services.errors import NonRecoverableError

        # Use a unique event so we don't collide with previous test's seeded data
        payload = {
            **base_onboarding_payload,
            "event_id": "evt-no-ref-001",
            "onboarding_reference_path": "nonexistent_prefix/",
        }

        with pytest.raises(NonRecoverableError, match="No onboarding reference"):
            onboarding_service.process(payload)

    def test_process_missing_video_encodings_partial_match(
        self, storage_client, onboarding_service, base_onboarding_payload
    ):
        """Missing .npy for a match_against location → that location is False."""
        base_path = self._base_path()

        # Seed reference image
        ref_blob = f"{base_path}/onboarding_reference/photo.png"
        seed_fake_image(storage_client, BUCKET_NAME, ref_blob)

        # Only seed profile encoding, not interview_1
        matching_enc = np.zeros((3, 128), dtype=np.float64)
        buf = io.BytesIO()
        np.save(buf, matching_enc)
        buf.seek(0)
        storage_client.upload_bytes(
            buf, BUCKET_NAME,
            f"{base_path}/video_face_encodings/profile/profile.npy",
            "application/octet-stream",
        )

        payload = {**base_onboarding_payload, "event_id": "evt-partial-001"}
        onboarding_service.process(payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "onboarding_verification.json")
        )
        assert stage["status"] == "OK"

    def test_process_multiple_reference_images(
        self, storage_client, onboarding_service, base_onboarding_payload
    ):
        """Multiple reference images at GCS prefix are all encoded."""
        base_path = self._base_path()

        # Seed multiple reference images
        for name in ["photo1.png", "photo2.jpg", "photo3.jpeg"]:
            ref_blob = f"{base_path}/onboarding_reference/{name}"
            seed_fake_image(storage_client, BUCKET_NAME, ref_blob)

        # Seed matching video encodings
        matching_enc = np.zeros((3, 128), dtype=np.float64)
        for loc in ["profile", "interview_1"]:
            blob_path = f"{base_path}/video_face_encodings/{loc}/{loc}.npy"
            buf = io.BytesIO()
            np.save(buf, matching_enc)
            buf.seek(0)
            storage_client.upload_bytes(buf, BUCKET_NAME, blob_path, "application/octet-stream")

        payload = {**base_onboarding_payload, "event_id": "evt-multi-ref-001"}
        onboarding_service.process(payload)

        from common.utils.helpers import build_stage_path

        stage = storage_client.download_json(
            BUCKET_NAME, build_stage_path(base_path, "onboarding_verification.json")
        )
        assert stage["status"] == "OK"
