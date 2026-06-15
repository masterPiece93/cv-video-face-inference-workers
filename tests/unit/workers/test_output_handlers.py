"""Unit tests for output handlers (encoding, verification, onboarding)."""
from unittest.mock import MagicMock

import pytest

from workers.face_encoding_worker.src.handlers.output_handler import EncodingOutputHandler
from workers.face_verification_worker.src.handlers.output_handler import VerificationOutputHandler
from workers.onboarding_verification_worker.src.handlers.output_handler import OnboardingOutputHandler


# ---------------------------------------------------------------------------
# Shared valid payloads
# ---------------------------------------------------------------------------

ENCODING_PAYLOAD = {
    "candidate_email": "a@b.com",
    "candidate_uid": "uid-1",
    "org_id": "org-1",
    "org_alias": "test",
    "bucket_name": "bucket",
    "event_id": "evt-1",
    "lookup_map": {"profile": "profile", "interviews": []},
    "sampled_frames": {"profile": [], "interviews": {}},
    "extra_info": None,
}

VERIFICATION_PAYLOAD = {
    "candidate_email": "a@b.com",
    "candidate_uid": "uid-1",
    "org_id": "org-1",
    "org_alias": "test",
    "bucket_name": "bucket",
    "event_id": "evt-2",
    "lookup_map": {"profile": "profile", "interviews": []},
    "sampled_frames": {},
    "status": {"similar_face_count": 1, "profile_match": True, "matches": {}},
    "extra_info": None,
}

ONBOARDING_PAYLOAD = {
    "candidate_email": "a@b.com",
    "candidate_uid": "uid-1",
    "org_id": "org-1",
    "org_alias": "test",
    "bucket_name": "bucket",
    "event_id": "evt-3",
    "lookup_map": {"profile": "profile", "interviews": []},
    "status": {"matches": {"profile": True}},
    "extra_info": None,
}


def _publisher(return_value="msg-id-123"):
    pub = MagicMock()
    pub.publish.return_value = return_value
    return pub


# ---------------------------------------------------------------------------
# EncodingOutputHandler
# ---------------------------------------------------------------------------

class TestEncodingOutputHandler:
    def test_publish_calls_publisher(self):
        pub = _publisher()
        handler = EncodingOutputHandler(publisher=pub)
        handler.publish({**ENCODING_PAYLOAD})
        pub.publish.assert_called_once()

    def test_publish_returns_message_id(self):
        pub = _publisher("msg-abc")
        handler = EncodingOutputHandler(publisher=pub)
        result = handler.publish({**ENCODING_PAYLOAD})
        assert result == "msg-abc"

    def test_publish_propagates_publisher_exception(self):
        pub = _publisher()
        pub.publish.side_effect = RuntimeError("pubsub down")
        handler = EncodingOutputHandler(publisher=pub)
        with pytest.raises(RuntimeError):
            handler.publish({**ENCODING_PAYLOAD})

    def test_publish_schema_violation_propagates(self):
        """Missing required field should raise SchemaViolation before publishing."""
        pub = _publisher()
        handler = EncodingOutputHandler(publisher=pub)
        bad_payload = {k: v for k, v in ENCODING_PAYLOAD.items() if k != "event_id"}
        with pytest.raises(Exception):
            handler.publish(bad_payload)
        pub.publish.assert_not_called()


# ---------------------------------------------------------------------------
# VerificationOutputHandler
# ---------------------------------------------------------------------------

class TestVerificationOutputHandler:
    def test_publish_calls_publisher(self):
        pub = _publisher()
        handler = VerificationOutputHandler(publisher=pub)
        handler.publish({**VERIFICATION_PAYLOAD})
        pub.publish.assert_called_once()

    def test_publish_returns_message_id(self):
        pub = _publisher("msg-xyz")
        handler = VerificationOutputHandler(publisher=pub)
        result = handler.publish({**VERIFICATION_PAYLOAD})
        assert result == "msg-xyz"

    def test_publish_propagates_exception(self):
        pub = _publisher()
        pub.publish.side_effect = ConnectionError("timeout")
        handler = VerificationOutputHandler(publisher=pub)
        with pytest.raises(ConnectionError):
            handler.publish({**VERIFICATION_PAYLOAD})

    def test_schema_violation_before_publish(self):
        pub = _publisher()
        handler = VerificationOutputHandler(publisher=pub)
        bad_payload = {k: v for k, v in VERIFICATION_PAYLOAD.items() if k != "event_id"}
        with pytest.raises(Exception):
            handler.publish(bad_payload)
        pub.publish.assert_not_called()


# ---------------------------------------------------------------------------
# OnboardingOutputHandler
# ---------------------------------------------------------------------------

class TestOnboardingOutputHandler:
    def test_publish_calls_publisher(self):
        pub = _publisher()
        handler = OnboardingOutputHandler(publisher=pub)
        handler.publish({**ONBOARDING_PAYLOAD})
        pub.publish.assert_called_once()

    def test_publish_returns_message_id(self):
        pub = _publisher("msg-onb")
        handler = OnboardingOutputHandler(publisher=pub)
        result = handler.publish({**ONBOARDING_PAYLOAD})
        assert result == "msg-onb"

    def test_publish_propagates_exception(self):
        pub = _publisher()
        pub.publish.side_effect = RuntimeError("fail")
        handler = OnboardingOutputHandler(publisher=pub)
        with pytest.raises(RuntimeError):
            handler.publish({**ONBOARDING_PAYLOAD})

    def test_schema_violation_before_publish(self):
        pub = _publisher()
        handler = OnboardingOutputHandler(publisher=pub)
        bad_payload = {k: v for k, v in ONBOARDING_PAYLOAD.items() if k != "event_id"}
        with pytest.raises(Exception):
            handler.publish(bad_payload)
        pub.publish.assert_not_called()
