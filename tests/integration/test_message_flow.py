"""Integration tests for pub/sub message flow between workers.

Tests the full message routing:
- Encoding worker publishes → Verification subscription receives
- Verification worker publishes → Onboarding subscription receives
- DLQ topic receives messages on unrecoverable errors
"""
import io
import json
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.integration.conftest import (
    BUCKET_NAME,
    ENCODING_TOPIC,
    ENCODING_SUB,
    ONBOARDING_SUB,
    ONBOARDING_TOPIC,
    PROJECT_ID,
    TEST_CANDIDATE_EMAIL,
    TEST_CANDIDATE_UID,
    TEST_EVENT_ID,
    TEST_ORG_ALIAS,
    TEST_ORG_ID,
    VERIFICATION_SUB,
    VERIFICATION_TOPIC,
    publish_message,
    pull_messages,
)


pytestmark = pytest.mark.integration


class TestWorkerMessageFlow:
    """Tests the message-passing contract between workers."""

    def test_encoding_output_is_valid_verification_input(
        self, storage_client, pubsub_setup, publisher_client, subscriber_client
    ):
        """Output from encoding worker is valid input for verification worker."""
        from workers.face_verification_worker.src.handlers.schema.input_schema import (
            VerificationInputSchema,
        )

        # Simulate encoding worker output
        encoding_output = {
            "candidate_email": TEST_CANDIDATE_EMAIL,
            "candidate_uid": TEST_CANDIDATE_UID,
            "extra_info": {"env": "integration"},
            "org_id": TEST_ORG_ID,
            "org_alias": TEST_ORG_ALIAS,
            "bucket_name": BUCKET_NAME,
            "event_id": TEST_EVENT_ID,
            "lookup_map": {
                "profile": "profile",
                "interviews": ["interview_1"],
            },
            "sampled_frames": {
                "profile": ["1.png"],
                "interviews": {"interview_1": ["1.png", "2.png"]},
            },
        }

        # Publish to verification topic (simulating encoding worker output)
        publish_message(publisher_client, VERIFICATION_TOPIC, encoding_output)

        # Pull from verification subscription
        messages = pull_messages(subscriber_client, VERIFICATION_SUB)
        assert len(messages) >= 1

        # Validate against verification worker's input schema
        schema = VerificationInputSchema()
        # Should NOT raise
        schema.validate(
            messages[0],
            logger_func=lambda msg, level="info": None,
            message_wrapper=lambda m: m,
        )

    def test_verification_output_is_valid_onboarding_input(
        self, storage_client, pubsub_setup, publisher_client, subscriber_client
    ):
        """Output from verification worker + onboarding fields is valid onboarding input.

        NOTE: In production, the onboarding-specific fields (onboarding_reference_path,
        match_against) are added by an upstream orchestrator, not by the verification worker
        itself. This test validates the combined payload shape.
        """
        from workers.onboarding_verification_worker.src.handlers.schema.input_schema import (
            OnboardingInputSchema,
        )

        unique_event = "evt-onboarding-schema-validation-001"

        # Simulate a message that the orchestrator composes from verification output +
        # onboarding context
        onboarding_input = {
            "candidate_email": TEST_CANDIDATE_EMAIL,
            "candidate_uid": TEST_CANDIDATE_UID,
            "extra_info": {"env": "integration"},
            "org_id": TEST_ORG_ID,
            "org_alias": TEST_ORG_ALIAS,
            "bucket_name": BUCKET_NAME,
            "event_id": unique_event,
            "lookup_map": {
                "profile": "profile",
                "interviews": ["interview_1"],
            },
            "onboarding_reference_path": "onboarding_reference/",
            "match_against": ["profile", "interview_1"],
        }

        publish_message(publisher_client, ONBOARDING_TOPIC, onboarding_input)

        # Pull messages and find ours by event_id (skip stale messages)
        messages = pull_messages(subscriber_client, ONBOARDING_SUB, max_messages=20, timeout=5.0)
        our_msg = next((m for m in messages if m.get("event_id") == unique_event), None)
        assert our_msg is not None, f"Did not find message with event_id={unique_event}"

        # Validate against onboarding input schema
        schema = OnboardingInputSchema()
        schema.validate(
            our_msg,
            logger_func=lambda msg, level="info": None,
            message_wrapper=lambda m: m,
        )

    def test_full_pipeline_encoding_to_verification(
        self, storage_client, pubsub_setup, subscriber_client
    ):
        """Encoding service → publishes → verification sub receives valid message."""
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
        from common.utils.helpers import build_candidate_base_path
        from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService
        from workers.face_encoding_worker.src.handlers.output_handler import EncodingOutputHandler
        from common.services.encoding.base import BaseEncoder

        class FakeEncoder(BaseEncoder):
            @property
            def name(self) -> str:
                return "fake_encoder"

            def encode_frame(self, frame_bytes):
                return [np.random.rand(128).astype(np.float64)]

            def calculate_distance(self, known, candidate):
                return np.linalg.norm(np.array(known) - candidate, axis=1)

            def is_duplicate(self, encoding, existing, tolerance):
                return False

        publisher = GCPPublisher(project_id=PROJECT_ID, topic_name=VERIFICATION_TOPIC)
        output_handler = EncodingOutputHandler(publisher)
        encoder = FakeEncoder()

        service = VideoFaceEncodingService(
            encoder=encoder,
            storage=storage_client,
            output_handler=output_handler,
            frame_sample_rate=30,
            face_tolerance=0.5,
            max_workers=2,
        )

        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )

        # Seed a video snippet
        blob = f"{base_path}/video_snippets/profile/profile_000.mp4"
        storage_client.upload_bytes(
            b"\x00\x00\x00\x1c\x66\x74\x79\x70", BUCKET_NAME, blob, "video/mp4"
        )

        payload = {
            "candidate_email": TEST_CANDIDATE_EMAIL,
            "candidate_uid": TEST_CANDIDATE_UID,
            "extra_info": None,
            "org_id": TEST_ORG_ID,
            "org_alias": TEST_ORG_ALIAS,
            "bucket_name": BUCKET_NAME,
            "event_id": "evt-pipeline-001",
            "lookup_map": {"profile": "profile", "interviews": []},
        }

        dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(service, "_extract_frames", return_value=[dummy_frame]):
            service.process(payload)

        # Verify message arrived at verification subscription
        messages = pull_messages(subscriber_client, VERIFICATION_SUB)
        assert len(messages) >= 1
        assert messages[0]["event_id"] == "evt-pipeline-001"
        assert "sampled_frames" in messages[0]

    def test_full_pipeline_verification_to_onboarding(
        self, storage_client, pubsub_setup, subscriber_client
    ):
        """Verification service → publishes → onboarding sub receives."""
        from common.services.cloud.gcp.pubsub.publisher import GCPPublisher
        from common.utils.helpers import build_candidate_base_path
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

        service = VideoFaceVerificationService(
            strategy=strategy,
            storage=storage_client,
            output_handler=output_handler,
        )

        base_path = build_candidate_base_path(
            TEST_ORG_ALIAS, TEST_ORG_ID, TEST_CANDIDATE_EMAIL, TEST_CANDIDATE_UID
        )

        # Seed encodings
        enc = np.random.rand(3, 128).astype(np.float64)
        for loc in ["profile", "interview_1"]:
            buf = io.BytesIO()
            np.save(buf, enc)
            buf.seek(0)
            storage_client.upload_bytes(
                buf, BUCKET_NAME,
                f"{base_path}/video_face_encodings/{loc}/{loc}.npy",
                "application/octet-stream",
            )

        payload = {
            "candidate_email": TEST_CANDIDATE_EMAIL,
            "candidate_uid": TEST_CANDIDATE_UID,
            "extra_info": None,
            "org_id": TEST_ORG_ID,
            "org_alias": TEST_ORG_ALIAS,
            "bucket_name": BUCKET_NAME,
            "event_id": "evt-pipeline-002",
            "lookup_map": {"profile": "profile", "interviews": ["interview_1"]},
            "sampled_frames": {"profile": ["1.png"], "interviews": {"interview_1": ["1.png"]}},
        }

        service.process(payload)

        messages = pull_messages(subscriber_client, ONBOARDING_SUB)
        assert len(messages) >= 1
        assert messages[0]["event_id"] == "evt-pipeline-002"
        assert "status" in messages[0]
