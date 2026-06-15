"""Unit tests for face_verification_worker input_handler.handle_message."""
import json
from unittest.mock import MagicMock

import pytest

from common.services.errors import NonRecoverableError, RecoverableError
from workers.face_verification_worker.src.handlers.input_handler import handle_message


VALID_PAYLOAD = {
    "candidate_email": "a@b.com",
    "candidate_uid": "uid-1",
    "org_id": "org-1",
    "org_alias": "test",
    "bucket_name": "bucket",
    "event_id": "evt-2",
    "lookup_map": {"profile": "profile", "interviews": ["i1"]},
    "sampled_frames": {},
    "extra_info": None,
}


def _message(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(payload).encode()
    msg.ack = MagicMock()
    msg.nack = MagicMock()
    return msg


def _service(side_effect=None) -> MagicMock:
    svc = MagicMock()
    if side_effect:
        svc.process.side_effect = side_effect
    return svc


class TestFaceVerificationHandler:
    def test_valid_message_acks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _service())
        msg.ack.assert_called_once()
        msg.nack.assert_not_called()

    def test_non_recoverable_acks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _service(NonRecoverableError("bad")))
        msg.ack.assert_called_once()

    def test_recoverable_nacks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _service(RecoverableError("retry")))
        msg.nack.assert_called_once()
        msg.ack.assert_not_called()

    def test_invalid_json_acks(self):
        msg = MagicMock()
        msg.data = b"not-json"
        msg.ack = MagicMock()
        msg.nack = MagicMock()
        handle_message(msg, _service())
        msg.ack.assert_called_once()

    def test_schema_violation_acks(self):
        msg = _message({"event_id": "x"})
        handle_message(msg, _service())
        msg.ack.assert_called_once()

    def test_unexpected_exception_acks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _service(RuntimeError("unexpected")))
        msg.ack.assert_called_once()

    def test_service_process_called_with_correct_event_id(self):
        msg = _message(VALID_PAYLOAD)
        svc = _service()
        handle_message(msg, svc)
        called_payload = svc.process.call_args[0][0]
        assert called_payload["event_id"] == "evt-2"
