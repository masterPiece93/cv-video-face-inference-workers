"""Unit tests for OnboardingVerificationService."""
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from common.services.errors import NonRecoverableError, RecoverableError
from common.utils.helpers import numpy_to_bytesio
from workers.onboarding_verification_worker.services.verification import OnboardingVerificationService


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


def _fake_image_bytesio() -> io.BytesIO:
    """Minimal valid JPEG bytes stand-in."""
    return io.BytesIO(b"\xff\xd8\xff" + b"\x00" * 64)


def _make_service(mock_encoder, mock_storage, mock_output_handler, tolerance=0.6):
    return OnboardingVerificationService(
        encoder=mock_encoder,
        storage=mock_storage,
        output_handler=mock_output_handler,
        tolerance=tolerance,
    )


# ---------------------------------------------------------------------------
# process() — GCS path
# ---------------------------------------------------------------------------

class TestProcessGcsPath:
    def test_gcs_path_lists_blobs(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = ["base/onboarding_reference/photo.jpg"]
        mock_storage.download_bytes.side_effect = [
            _fake_image_bytesio(),   # reference image
            _npy_bytes(),            # profile npy
            _npy_bytes(),            # interview_1 npy
        ]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(onboarding_payload)
        mock_storage.list_blobs.assert_called()

    def test_gcs_no_blobs_raises_non_recoverable(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = []
        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="No onboarding reference"):
            svc.process(onboarding_payload)

    def test_gcs_no_encodable_images_raises(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.return_value = _fake_image_bytesio()
        mock_encoder.encode_frame.return_value = []  # no faces found

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="Could not encode"):
            svc.process(onboarding_payload)

    def test_gcs_skips_non_image_blobs(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = [
            "path/doc.pdf",
            "path/photo.jpg",
        ]
        mock_storage.download_bytes.side_effect = [
            _fake_image_bytesio(),  # photo.jpg
            _npy_bytes(),           # profile npy
            _npy_bytes(),           # interview_1 npy
        ]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(onboarding_payload)
        # encode_frame called once (only the .jpg)
        assert mock_encoder.encode_frame.call_count == 1


# ---------------------------------------------------------------------------
# process() — signed URL path
# ---------------------------------------------------------------------------

class TestProcessSignedUrl:
    def _url_payload(self, onboarding_payload):
        return {
            **onboarding_payload,
            "onboarding_reference_path": "https://storage.googleapis.com/bucket/photo.jpg?X-Goog-Signature=abc",
        }

    def test_signed_url_does_not_call_list_blobs(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        payload = self._url_payload(onboarding_payload)
        mock_storage.download_bytes_from_url.return_value = _fake_image_bytesio()
        mock_storage.download_bytes.return_value = _npy_bytes()
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)
        mock_storage.list_blobs.assert_not_called()

    def test_signed_url_calls_download_bytes_from_url(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        payload = self._url_payload(onboarding_payload)
        mock_storage.download_bytes_from_url.return_value = _fake_image_bytesio()
        mock_storage.download_bytes.return_value = _npy_bytes()
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)
        mock_storage.download_bytes_from_url.assert_called_once_with(payload["onboarding_reference_path"])

    def test_signed_url_download_failure_raises(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        payload = self._url_payload(onboarding_payload)
        mock_storage.download_bytes_from_url.return_value = None  # failure

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="Failed to download"):
            svc.process(payload)

    def test_http_url_also_treated_as_url(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        payload = {**onboarding_payload, "onboarding_reference_path": "http://cdn.example.com/photo.jpg"}
        mock_storage.download_bytes_from_url.return_value = _fake_image_bytesio()
        mock_storage.download_bytes.return_value = _npy_bytes()
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)
        mock_storage.list_blobs.assert_not_called()
        mock_storage.download_bytes_from_url.assert_called_once()


# ---------------------------------------------------------------------------
# process() — stage writing
# ---------------------------------------------------------------------------

class TestProcessStageWriting:
    def test_writes_start_stage(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.side_effect = [_fake_image_bytesio(), _npy_bytes(), _npy_bytes()]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(onboarding_payload)
        first_stage = mock_storage.upload_json.call_args_list[0][0][0]
        assert first_stage["completed"] is None

    def test_writes_ok_stage_on_success(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.side_effect = [_fake_image_bytesio(), _npy_bytes(), _npy_bytes()]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(onboarding_payload)
        last_stage = mock_storage.upload_json.call_args_list[-1][0][0]
        assert last_stage["status"] == "OK"

    def test_writes_error_stage_on_failure(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = []  # triggers NonRecoverableError

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        try:
            svc.process(onboarding_payload)
        except NonRecoverableError:
            pass
        stages = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert any(s.get("status") == "ERROR" for s in stages)

    def test_stage_path_contains_stages_segment(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.side_effect = [_fake_image_bytesio(), _npy_bytes(), _npy_bytes()]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(onboarding_payload)
        blob_path = mock_storage.upload_json.call_args_list[0][0][2]
        assert "/stages/" in blob_path


# ---------------------------------------------------------------------------
# process() — output publishing
# ---------------------------------------------------------------------------

class TestProcessPublishing:
    def test_publishes_matches_dict(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.side_effect = [_fake_image_bytesio(), _npy_bytes(), _npy_bytes()]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(onboarding_payload)
        published = mock_output_handler.publish.call_args[0][0]
        assert "status" in published
        assert "matches" in published["status"]

    def test_does_not_publish_on_error(self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload):
        mock_storage.list_blobs.return_value = []
        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        try:
            svc.process(onboarding_payload)
        except NonRecoverableError:
            pass
        mock_output_handler.publish.assert_not_called()


# ---------------------------------------------------------------------------
# _match_any
# ---------------------------------------------------------------------------

class TestMatchAny:
    def test_match_within_tolerance(self, mock_encoder, mock_storage, mock_output_handler):
        svc = _make_service(mock_encoder, mock_storage, mock_output_handler, tolerance=0.6)
        ref = _enc(0)
        close = ref + np.random.default_rng(0).random(128) * 0.001
        close = close / np.linalg.norm(close)
        video_encs = np.array([close])
        assert svc._match_any(ref, video_encs) is True

    def test_no_match_beyond_tolerance(self, mock_encoder, mock_storage, mock_output_handler):
        svc = _make_service(mock_encoder, mock_storage, mock_output_handler, tolerance=0.001)
        ref = _enc(0)
        far = _enc(99)
        video_encs = np.array([far])
        assert svc._match_any(ref, video_encs) is False
