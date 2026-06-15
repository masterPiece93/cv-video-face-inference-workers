"""Unit tests for VideoFaceVerificationService."""
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from common.services.errors import NonRecoverableError, RecoverableError
from common.utils.helpers import numpy_to_bytesio
from workers.face_verification_worker.services.verification import VideoFaceVerificationService
from workers.face_verification_worker.services.strategies.base import VerificationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enc(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(128)
    return v / np.linalg.norm(v)


def _npy_bytes(n: int = 3) -> io.BytesIO:
    arr = np.stack([_enc(i) for i in range(n)])
    return numpy_to_bytesio(arr)


def _mock_strategy(result: VerificationResult = None) -> MagicMock:
    strategy = MagicMock()
    strategy.verify.return_value = result or VerificationResult(
        similar_face_count=2, profile_match=True, matches={"interview_1": True}
    )
    return strategy


def _make_service(storage, output_handler, strategy=None):
    return VideoFaceVerificationService(
        strategy=strategy or _mock_strategy(),
        storage=storage,
        output_handler=output_handler,
    )


# ---------------------------------------------------------------------------
# process() — happy path
# ---------------------------------------------------------------------------

class TestProcessHappyPath:
    def test_publishes_on_success(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        svc = _make_service(mock_storage, mock_output_handler)
        svc.process(verification_payload)
        mock_output_handler.publish.assert_called_once()

    def test_published_payload_contains_status(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        svc = _make_service(mock_storage, mock_output_handler)
        svc.process(verification_payload)
        published = mock_output_handler.publish.call_args[0][0]
        assert "status" in published
        assert "similar_face_count" in published["status"]

    def test_writes_ok_stage(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        svc = _make_service(mock_storage, mock_output_handler)
        svc.process(verification_payload)
        last_stage = mock_storage.upload_json.call_args_list[-1][0][0]
        assert last_stage["status"] == "OK"

    def test_writes_start_stage(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        svc = _make_service(mock_storage, mock_output_handler)
        svc.process(verification_payload)
        first_stage = mock_storage.upload_json.call_args_list[0][0][0]
        assert first_stage["completed"] is None

    def test_stage_path_is_flat(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        svc = _make_service(mock_storage, mock_output_handler)
        svc.process(verification_payload)
        blob_path = mock_storage.upload_json.call_args_list[0][0][2]
        assert "/stages/" in blob_path
        assert "verification.json" in blob_path

    def test_stage_data_event_id_matches(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        svc = _make_service(mock_storage, mock_output_handler)
        svc.process(verification_payload)
        last_stage = mock_storage.upload_json.call_args_list[-1][0][0]
        assert last_stage["event_id"] == verification_payload["event_id"]


# ---------------------------------------------------------------------------
# process() — missing interview encodings
# ---------------------------------------------------------------------------

class TestProcessNoInterviewEncodings:
    def test_raises_non_recoverable_when_no_npy(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = None  # all .npy missing
        svc = _make_service(mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError):
            svc.process(verification_payload)

    def test_writes_error_stage_when_no_npy(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = None
        svc = _make_service(mock_storage, mock_output_handler)
        try:
            svc.process(verification_payload)
        except NonRecoverableError:
            pass
        stage_calls = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert any(c.get("status") == "ERROR" for c in stage_calls)

    def test_does_not_publish_on_failure(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = None
        svc = _make_service(mock_storage, mock_output_handler)
        try:
            svc.process(verification_payload)
        except NonRecoverableError:
            pass
        mock_output_handler.publish.assert_not_called()


# ---------------------------------------------------------------------------
# process() — recoverable error propagation
# ---------------------------------------------------------------------------

class TestProcessRecoverableError:
    def test_recoverable_error_propagates(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        strategy = _mock_strategy()
        strategy.verify.side_effect = RecoverableError("transient")
        svc = _make_service(mock_storage, mock_output_handler, strategy)
        with pytest.raises(RecoverableError):
            svc.process(verification_payload)

    def test_recoverable_error_writes_error_stage(self, mock_storage, mock_output_handler, verification_payload):
        mock_storage.download_bytes.return_value = _npy_bytes()
        strategy = _mock_strategy()
        strategy.verify.side_effect = RecoverableError("transient")
        svc = _make_service(mock_storage, mock_output_handler, strategy)
        try:
            svc.process(verification_payload)
        except RecoverableError:
            pass
        stages = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert any(s.get("status") == "ERROR" for s in stages)


# ---------------------------------------------------------------------------
# _load_npy
# ---------------------------------------------------------------------------

class TestLoadNpy:
    def test_returns_none_when_download_fails(self, mock_storage, mock_output_handler):
        mock_storage.download_bytes.return_value = None
        svc = _make_service(mock_storage, mock_output_handler)
        result = svc._load_npy("bucket", "path/enc.npy", "evt")
        assert result is None

    def test_returns_array_on_valid_npy(self, mock_storage, mock_output_handler):
        mock_storage.download_bytes.return_value = _npy_bytes(5)
        svc = _make_service(mock_storage, mock_output_handler)
        result = svc._load_npy("bucket", "path/enc.npy", "evt")
        assert isinstance(result, np.ndarray)
        assert result.shape == (5, 128)

    def test_returns_none_on_corrupt_npy(self, mock_storage, mock_output_handler):
        mock_storage.download_bytes.return_value = io.BytesIO(b"not-numpy-data")
        svc = _make_service(mock_storage, mock_output_handler)
        result = svc._load_npy("bucket", "path/enc.npy", "evt")
        assert result is None
