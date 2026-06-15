"""Extended unit tests for VideoFaceVerificationService — covering uncovered branches.

Missing lines 111-116:
    NonRecoverableError / RecoverableError re-raised after writing ERROR stage,
    and generic Exception wrapped as NonRecoverableError.
"""
import io
from unittest.mock import MagicMock

import numpy as np
import pytest

from common.services.errors import NonRecoverableError, RecoverableError
from common.utils.helpers import numpy_to_bytesio
from workers.face_verification_worker.services.verification import VideoFaceVerificationService


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


def _make_service(mock_storage, mock_strategy=None, mock_output_handler=None):
    strategy = mock_strategy or MagicMock()
    output_handler = mock_output_handler or MagicMock()
    return VideoFaceVerificationService(
        strategy=strategy,
        storage=mock_storage,
        output_handler=output_handler,
    )


# ---------------------------------------------------------------------------
# process() — NonRecoverableError & RecoverableError re-raised (lines 111-116)
# ---------------------------------------------------------------------------

class TestVerificationErrorPropagation:
    def test_non_recoverable_from_strategy_is_re_raised(
        self, mock_storage, verification_payload
    ):
        """NonRecoverableError raised inside try-block is caught, ERROR stage written, re-raised."""
        mock_storage.download_bytes.return_value = _npy_bytes()  # interviews load OK

        mock_strategy = MagicMock()
        mock_strategy.verify.side_effect = NonRecoverableError("strategy exploded")

        svc = _make_service(mock_storage, mock_strategy=mock_strategy)
        with pytest.raises(NonRecoverableError, match="strategy exploded"):
            svc.process(verification_payload)

    def test_non_recoverable_from_strategy_writes_error_stage(
        self, mock_storage, verification_payload
    ):
        mock_storage.download_bytes.return_value = _npy_bytes()

        mock_strategy = MagicMock()
        mock_strategy.verify.side_effect = NonRecoverableError("crash")

        svc = _make_service(mock_storage, mock_strategy=mock_strategy)
        try:
            svc.process(verification_payload)
        except NonRecoverableError:
            pass

        stages = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert any(s.get("status") == "ERROR" for s in stages)

    def test_recoverable_error_is_re_raised(
        self, mock_storage, verification_payload
    ):
        mock_storage.download_bytes.return_value = _npy_bytes()

        mock_strategy = MagicMock()
        mock_strategy.verify.side_effect = RecoverableError("transient")

        svc = _make_service(mock_storage, mock_strategy=mock_strategy)
        with pytest.raises(RecoverableError, match="transient"):
            svc.process(verification_payload)

    def test_generic_exception_wrapped_as_non_recoverable(
        self, mock_storage, verification_payload
    ):
        """An unexpected exception inside try-block is wrapped as NonRecoverableError."""
        mock_storage.download_bytes.return_value = _npy_bytes()

        mock_strategy = MagicMock()
        mock_strategy.verify.side_effect = RuntimeError("unexpected boom")

        svc = _make_service(mock_storage, mock_strategy=mock_strategy)
        with pytest.raises(NonRecoverableError, match="Verification pipeline failed"):
            svc.process(verification_payload)

    def test_generic_exception_writes_error_stage(
        self, mock_storage, verification_payload
    ):
        mock_storage.download_bytes.return_value = _npy_bytes()

        mock_strategy = MagicMock()
        mock_strategy.verify.side_effect = RuntimeError("boom")

        svc = _make_service(mock_storage, mock_strategy=mock_strategy)
        try:
            svc.process(verification_payload)
        except NonRecoverableError:
            pass

        stages = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert any(s.get("status") == "ERROR" for s in stages)

    def test_no_interview_encodings_raises_non_recoverable(
        self, mock_storage, verification_payload
    ):
        """When all interview .npy downloads fail, NonRecoverableError is raised."""
        mock_storage.download_bytes.return_value = None  # all downloads fail

        svc = _make_service(mock_storage)
        with pytest.raises(NonRecoverableError, match="No interview encodings"):
            svc.process(verification_payload)
