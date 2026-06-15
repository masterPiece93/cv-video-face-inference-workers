"""Unit tests for FdetectEncoder — resilient RPC patterns."""
from io import BytesIO
from unittest.mock import MagicMock, patch

import grpc
import numpy as np
import pytest

from common.services.encoding.fdetect_encoder import FdetectEncoder, _MAX_RETRIES
from common.services.errors import NonRecoverableError, RecoverableError


class FakeRpcError(grpc.RpcError):
    """A proper grpc.RpcError subclass for testing."""

    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


@pytest.fixture
def encoder():
    """Create an FdetectEncoder with mocked gRPC channel and separate stubs."""
    with patch("common.services.encoding.fdetect_encoder.grpc.insecure_channel") as mock_ch:
        mock_channel = MagicMock()
        # Ensure channel.unary_unary returns a NEW mock each time so that
        # Detect and Health are different mock objects inside FaceDetectStub.
        mock_channel.unary_unary.side_effect = lambda *a, **kw: MagicMock()
        mock_ch.return_value = mock_channel
        enc = FdetectEncoder(channel_address="localhost:50051")
    return enc


class TestSupportsParallel:
    """fdetect is I/O-bound and thread-safe → parallel encoding is allowed."""

    def test_supports_parallel_is_true(self, encoder):
        assert encoder.supports_parallel is True

    def test_supports_parallel_is_class_attribute(self):
        # Capability must be declarable without instantiating the gRPC client.
        assert FdetectEncoder.supports_parallel is True


class TestPing:
    """Pattern 2: Health-based error classification."""

    def test_ping_returns_true_on_ok(self, encoder):
        mock_response = MagicMock()
        mock_response.Status = "OK"
        encoder._stub.Health.return_value = mock_response

        assert encoder.ping() is True

    def test_ping_returns_false_on_non_ok(self, encoder):
        mock_response = MagicMock()
        mock_response.Status = "ERROR"
        encoder._stub.Health.return_value = mock_response

        assert encoder.ping() is False

    def test_ping_returns_false_on_exception(self, encoder):
        encoder._stub.Health.side_effect = Exception("connection refused")

        assert encoder.ping() is False

    def test_ping_never_raises(self, encoder):
        encoder._stub.Health.side_effect = FakeRpcError(grpc.StatusCode.UNAVAILABLE)

        # Must not raise — always returns bool
        result = encoder.ping()
        assert result is False


class TestCallWithRetry:
    """Pattern 1: Never return silent None on failure."""

    def test_success_on_first_attempt(self, encoder):
        mock_response = MagicMock()
        encoder._stub.Detect.return_value = mock_response

        result = encoder._call_with_retry(MagicMock())
        assert result is mock_response

    @patch("common.services.encoding.fdetect_encoder.time.sleep")
    def test_retries_on_unavailable_then_succeeds(self, mock_sleep, encoder):
        mock_response = MagicMock()
        rpc_error = FakeRpcError(grpc.StatusCode.UNAVAILABLE)

        encoder._stub.Detect.side_effect = [rpc_error, rpc_error, mock_response]

        result = encoder._call_with_retry(MagicMock())
        assert result is mock_response
        assert mock_sleep.call_count == 2

    @patch("common.services.encoding.fdetect_encoder.time.sleep")
    def test_raises_non_recoverable_when_exhausted_and_unhealthy(self, mock_sleep, encoder):
        rpc_error = FakeRpcError(grpc.StatusCode.UNAVAILABLE)

        encoder._stub.Detect.side_effect = [rpc_error] * _MAX_RETRIES
        # ping returns False → NonRecoverableError
        encoder._stub.Health.side_effect = Exception("down")

        with pytest.raises(NonRecoverableError):
            encoder._call_with_retry(MagicMock())

    @patch("common.services.encoding.fdetect_encoder.time.sleep")
    def test_raises_recoverable_when_exhausted_and_healthy(self, mock_sleep, encoder):
        rpc_error = FakeRpcError(grpc.StatusCode.UNAVAILABLE)

        encoder._stub.Detect.side_effect = [rpc_error] * _MAX_RETRIES
        # ping returns True → RecoverableError
        mock_health_resp = MagicMock()
        mock_health_resp.Status = "OK"
        encoder._stub.Health.return_value = mock_health_resp

        with pytest.raises(RecoverableError):
            encoder._call_with_retry(MagicMock())

    def test_raises_immediately_on_non_unavailable_error(self, encoder):
        rpc_error = FakeRpcError(grpc.StatusCode.INTERNAL)

        encoder._stub.Detect.side_effect = rpc_error

        with pytest.raises(grpc.RpcError):
            encoder._call_with_retry(MagicMock())


class TestEncodeFrame:
    """Pattern 3: Pre-request health pinging."""

    def test_raises_non_recoverable_when_ping_fails(self, encoder):
        encoder._stub.Health.side_effect = Exception("down")

        with pytest.raises(NonRecoverableError, match="pre-request health ping failed"):
            encoder.encode_frame(BytesIO(b"fake-image"))

    @patch("common.services.encoding.fdetect_encoder.MessageToDict")
    def test_returns_encodings_on_success(self, mock_to_dict, encoder):
        # ping passes
        mock_health = MagicMock()
        mock_health.Status = "OK"
        encoder._stub.Health.return_value = mock_health

        # Detect returns a response
        encoder._stub.Detect.return_value = MagicMock()
        mock_to_dict.return_value = {
            "results": [{"faces": [{"enc": [0.1] * 128}]}]
        }

        result = encoder.encode_frame(BytesIO(b"fake-image"))
        assert len(result) == 1
        assert len(result[0]) == 128


class TestClose:
    """Pattern 5: Deterministic channel cleanup."""

    def test_close_calls_channel_close(self, encoder):
        encoder.close()
        encoder._channel.close.assert_called_once()

    def test_close_is_idempotent(self, encoder):
        encoder.close()
        encoder.close()
        assert encoder._channel.close.call_count == 2

    def test_close_never_raises(self, encoder):
        encoder._channel.close.side_effect = RuntimeError("already closed")
        # Must not raise
        encoder.close()
