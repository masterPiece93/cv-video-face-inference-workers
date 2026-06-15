"""Extended unit tests for OnboardingVerificationService — covering uncovered branches."""
import io
from unittest.mock import MagicMock, call

import numpy as np
import pytest

from common.services.errors import NonRecoverableError
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


def _fake_image() -> io.BytesIO:
    return io.BytesIO(b"\xff\xd8\xff" + b"\x00" * 64)


def _make_service(mock_encoder, mock_storage, mock_output_handler, tolerance=0.6):
    return OnboardingVerificationService(
        encoder=mock_encoder,
        storage=mock_storage,
        output_handler=mock_output_handler,
        tolerance=tolerance,
    )


# ---------------------------------------------------------------------------
# process() — ref_encodings empty after filtering all non-image blobs
# (line 96: raise NonRecoverableError "Could not encode")
# ---------------------------------------------------------------------------

class TestRefEncodingsEmptyAfterFiltering:
    def test_all_blobs_are_non_images_raises(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """All listed blobs have unsupported extensions → ref_encodings stays empty."""
        mock_storage.list_blobs.return_value = [
            "path/document.pdf",
            "path/video.mp4",
            "path/archive.zip",
        ]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="Could not encode"):
            svc.process(onboarding_payload)

    def test_image_download_returns_none_skipped(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """Blob with image extension but download returns None → skipped → empty encodings."""
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.return_value = None  # download failure
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="Could not encode"):
            svc.process(onboarding_payload)


# ---------------------------------------------------------------------------
# process() — match_against location not in lookup_map (lines 138-143)
# ---------------------------------------------------------------------------

class TestMatchAgainstUnknownLocation:
    def test_unknown_location_logged_and_not_in_matches(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """Location in match_against but absent from lookup_map is logged and skipped."""
        payload = {
            **onboarding_payload,
            "match_against": ["profile", "interview_1", "unknown_location"],
        }
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.side_effect = [
            _fake_image(),   # reference image
            _npy_bytes(),    # profile npy
            _npy_bytes(),    # interview_1 npy
            # unknown_location → no npy call expected
        ]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)

        published = mock_output_handler.publish.call_args[0][0]
        matches = published["status"]["matches"]
        assert "unknown_location" not in matches


# ---------------------------------------------------------------------------
# process() — video_encodings is None (line 129: match[location] = False)
# ---------------------------------------------------------------------------

class TestMatchAgainstNpyNotFound:
    def test_missing_npy_sets_match_to_false(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """When _load_npy returns None, the location is recorded as False."""
        mock_storage.list_blobs.return_value = ["path/photo.jpg"]
        mock_storage.download_bytes.side_effect = [
            _fake_image(),  # reference image
            None,           # profile npy → not found
            None,           # interview_1 npy → not found
        ]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(onboarding_payload)

        published = mock_output_handler.publish.call_args[0][0]
        matches = published["status"]["matches"]
        assert matches.get("profile") is False or matches.get("interview_1") is False


# ---------------------------------------------------------------------------
# process() — generic Exception re-raised as NonRecoverableError (lines 174-175)
# ---------------------------------------------------------------------------

class TestGenericExceptionWrapped:
    def test_generic_exception_raises_non_recoverable(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """An unexpected exception inside try-block is wrapped as NonRecoverableError."""
        mock_storage.list_blobs.side_effect = RuntimeError("unexpected crash")

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="Onboarding verification failed"):
            svc.process(onboarding_payload)

    def test_generic_exception_writes_error_stage(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        mock_storage.list_blobs.side_effect = RuntimeError("crash")

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        try:
            svc.process(onboarding_payload)
        except NonRecoverableError:
            pass

        stages = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert any(s.get("status") == "ERROR" for s in stages)


# ---------------------------------------------------------------------------
# process() — NonRecoverableError propagated directly (lines 117-118)
# ---------------------------------------------------------------------------

class TestNonRecoverableErrorPropagated:
    def test_non_recoverable_error_is_re_raised_unchanged(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        mock_storage.list_blobs.return_value = []  # triggers NonRecoverableError inside try

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError):
            svc.process(onboarding_payload)

    def test_non_recoverable_error_writes_error_stage(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        mock_storage.list_blobs.return_value = []

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        try:
            svc.process(onboarding_payload)
        except NonRecoverableError:
            pass

        stages = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert any(s.get("status") == "ERROR" for s in stages)


# ---------------------------------------------------------------------------
# process() — onboarding_reference_path is a gs:// URI
# ---------------------------------------------------------------------------

class TestGcsUriPath:
    """Tests for the absolute ``gs://`` URI branch in process()."""

    def test_gs_uri_single_image_blob_downloads_from_named_bucket(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """gs:// URI ending in .jpg → single download_bytes call with the named bucket."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://other-bucket/photos/alice.jpg",
            "match_against": [],
        }
        mock_storage.download_bytes.return_value = _fake_image()
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)

        mock_storage.download_bytes.assert_called_once_with("other-bucket", "photos/alice.jpg")
        mock_storage.list_blobs.assert_not_called()

    def test_gs_uri_single_png_blob_downloads_from_named_bucket(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """gs:// URI ending in .png is also treated as a single image."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://ref-bucket/faces/bob.png",
            "match_against": [],
        }
        mock_storage.download_bytes.return_value = _fake_image()
        mock_encoder.encode_frame.return_value = [_enc(1)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)

        mock_storage.download_bytes.assert_called_once_with("ref-bucket", "faces/bob.png")

    def test_gs_uri_single_blob_download_failure_raises_non_recoverable(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """download_bytes returns None for a gs:// single-image URI → NonRecoverableError."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://other-bucket/photos/alice.jpg",
        }
        mock_storage.download_bytes.return_value = None

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="Failed to download"):
            svc.process(payload)

    def test_gs_uri_prefix_lists_blobs_from_named_bucket(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """gs:// URI not ending in an image ext → list_blobs called on the named bucket."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://other-bucket/photos/alice/",
            "match_against": [],
        }
        # _parse_gcs_uri strips trailing slash → src_path="photos/alice"
        mock_storage.list_blobs.return_value = ["photos/alice/face1.jpg", "photos/alice/face2.png"]
        mock_storage.download_bytes.return_value = _fake_image()
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)

        mock_storage.list_blobs.assert_called_once_with("other-bucket", "photos/alice")
        assert mock_storage.download_bytes.call_count == 2

    def test_gs_uri_prefix_no_blobs_raises_non_recoverable(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """list_blobs returns [] for a gs:// prefix URI → NonRecoverableError."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://other-bucket/empty-folder/",
        }
        mock_storage.list_blobs.return_value = []

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="No onboarding reference images"):
            svc.process(payload)

    def test_gs_uri_prefix_all_non_image_blobs_raises_non_recoverable(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """All listed blobs under gs:// prefix are non-images → 'Could not encode' error."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://other-bucket/docs/",
        }
        mock_storage.list_blobs.return_value = ["docs/file.pdf", "docs/readme.txt"]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with pytest.raises(NonRecoverableError, match="Could not encode"):
            svc.process(payload)

    def test_gs_uri_prefix_blob_download_none_skipped(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """A blob that returns None from download_bytes is silently skipped."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://other-bucket/photos/",
            "match_against": [],
        }
        mock_storage.list_blobs.return_value = ["photos/ok.jpg", "photos/fail.jpg"]
        # First blob → None (skipped), second blob → valid image data
        mock_storage.download_bytes.side_effect = [None, _fake_image()]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)  # should succeed — second blob is valid

        assert mock_encoder.encode_frame.call_count == 1

    def test_gs_uri_uses_payload_bucket_for_npy_not_gs_bucket(
        self, mock_encoder, mock_storage, mock_output_handler, onboarding_payload
    ):
        """NPY files are always fetched from the payload bucket_name, not the gs:// bucket."""
        payload = {
            **onboarding_payload,
            "onboarding_reference_path": "gs://other-bucket/photos/alice.jpg",
            "match_against": ["profile"],
        }
        mock_storage.download_bytes.side_effect = [
            _fake_image(),  # reference image from "other-bucket"
            _npy_bytes(),   # profile.npy from payload bucket "test-bucket"
        ]
        mock_encoder.encode_frame.return_value = [_enc(0)]

        svc = _make_service(mock_encoder, mock_storage, mock_output_handler)
        svc.process(payload)

        calls = mock_storage.download_bytes.call_args_list
        # First call: reference image from gs:// bucket
        assert calls[0] == call("other-bucket", "photos/alice.jpg")
        # Second call: npy from the payload's bucket_name
        assert calls[1][0][0] == "test-bucket"


# ---------------------------------------------------------------------------
# _parse_gcs_uri — static method unit tests
# ---------------------------------------------------------------------------

class TestParseGcsUri:
    """Unit tests for the _parse_gcs_uri static helper."""

    def test_single_image_blob(self):
        bucket, path = OnboardingVerificationService._parse_gcs_uri(
            "gs://my-bucket/path/to/image.jpg"
        )
        assert bucket == "my-bucket"
        assert path == "path/to/image.jpg"

    def test_prefix_trailing_slash_stripped(self):
        bucket, path = OnboardingVerificationService._parse_gcs_uri(
            "gs://my-bucket/path/to/folder/"
        )
        assert bucket == "my-bucket"
        assert path == "path/to/folder"

    def test_top_level_prefix(self):
        bucket, path = OnboardingVerificationService._parse_gcs_uri(
            "gs://my-bucket/top-level/"
        )
        assert bucket == "my-bucket"
        assert path == "top-level"

    def test_bucket_root_no_path(self):
        bucket, path = OnboardingVerificationService._parse_gcs_uri("gs://my-bucket/")
        assert bucket == "my-bucket"
        assert path == ""

    def test_bucket_name_extracted_correctly(self):
        bucket, _ = OnboardingVerificationService._parse_gcs_uri(
            "gs://special-bucket-123/some/path.png"
        )
        assert bucket == "special-bucket-123"

    def test_deep_nested_path_preserved(self):
        bucket, path = OnboardingVerificationService._parse_gcs_uri(
            "gs://bucket/a/b/c/d/image.jpeg"
        )
        assert bucket == "bucket"
        assert path == "a/b/c/d/image.jpeg"
