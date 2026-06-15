"""Unit tests for face_encoding_worker input_handler.handle_message."""
import json
from unittest.mock import MagicMock, patch

import pytest

from common.services.errors import NonRecoverableError, RecoverableError
from workers.face_encoding_worker.src.handlers.input_handler import handle_message


VALID_PAYLOAD = {
    "candidate_email": "a@b.com",
    "candidate_uid": "uid-1",
    "org_id": "org-1",
    "org_alias": "test",
    "bucket_name": "bucket",
    "event_id": "evt-1",
    "lookup_map": {"profile": "profile", "interviews": []},
}


def _message(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(payload).encode()
    msg.ack = MagicMock()
    msg.nack = MagicMock()
    return msg


def _mock_service(side_effect=None) -> MagicMock:
    svc = MagicMock()
    if side_effect:
        svc.process.side_effect = side_effect
    return svc


class TestHandleMessageAck:
    def test_valid_message_acks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _mock_service())
        msg.ack.assert_called_once()
        msg.nack.assert_not_called()

    def test_non_recoverable_error_acks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _mock_service(NonRecoverableError("bad")))
        msg.ack.assert_called_once()
        msg.nack.assert_not_called()

    def test_invalid_json_acks(self):
        msg = MagicMock()
        msg.data = b"{ not valid json }"
        msg.ack = MagicMock()
        msg.nack = MagicMock()
        handle_message(msg, _mock_service())
        msg.ack.assert_called_once()

    def test_unexpected_exception_acks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _mock_service(ValueError("unexpected")))
        msg.ack.assert_called_once()

    def test_schema_violation_acks(self):
        """Message missing required fields triggers SchemaViolation → ack."""
        msg = _message({"event_id": "x"})
        handle_message(msg, _mock_service())
        msg.ack.assert_called_once()


class TestHandleMessageNack:
    def test_recoverable_error_nacks(self):
        msg = _message(VALID_PAYLOAD)
        handle_message(msg, _mock_service(RecoverableError("retry")))
        msg.nack.assert_called_once()
        msg.ack.assert_not_called()


class TestHandleMessagePayload:
    def test_service_process_called_with_valid_payload(self):
        msg = _message(VALID_PAYLOAD)
        svc = _mock_service()
        handle_message(msg, svc)
        svc.process.assert_called_once()
        called_payload = svc.process.call_args[0][0]
        assert called_payload["event_id"] == "evt-1"

    def test_bytes_data_is_decoded(self):
        """Ensure bytes data is decoded to str before JSON parsing."""
        msg = _message(VALID_PAYLOAD)
        msg.data = json.dumps(VALID_PAYLOAD).encode("utf-8")
        svc = _mock_service()
        handle_message(msg, svc)
        svc.process.assert_called_once()
