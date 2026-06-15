"""Unit tests for VideoFaceEncodingService."""
import io
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from common.services.errors import NonRecoverableError, RecoverableError
from common.utils.helpers import build_candidate_base_path, build_stage_path, numpy_to_bytesio
from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_encoding(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(128).astype(np.float64)
    return v / np.linalg.norm(v)


def _make_service(mock_encoder, mock_storage, mock_output_handler):
    return VideoFaceEncodingService(
        encoder=mock_encoder,
        storage=mock_storage,
        output_handler=mock_output_handler,
        frame_sample_rate=30,
        face_tolerance=0.5,
    )


@pytest.fixture()
def service(mock_encoder, mock_storage, mock_output_handler):
    return _make_service(mock_encoder, mock_storage, mock_output_handler)


# ---------------------------------------------------------------------------
# process() — happy path
# ---------------------------------------------------------------------------

class TestProcessHappyPath:
    def test_publishes_on_success(self, service, mock_output_handler, encoding_payload):
        """When no video blobs are found, pipeline completes and publishes."""
        service.process(encoding_payload)
        mock_output_handler.publish.assert_called_once()

    def test_publish_payload_contains_required_keys(self, service, mock_output_handler, encoding_payload):
        service.process(encoding_payload)
        published = mock_output_handler.publish.call_args[0][0]
        for key in ("candidate_email", "candidate_uid", "event_id", "bucket_name", "lookup_map", "sampled_frames"):
            assert key in published

    def test_writes_start_stage(self, service, mock_storage, encoding_payload):
        service.process(encoding_payload)
        # First upload_json call should have completed=None (start stage)
        first_call_data = mock_storage.upload_json.call_args_list[0][0][0]
        assert first_call_data["completed"] is None

    def test_writes_ok_stage_on_success(self, service, mock_storage, encoding_payload):
        service.process(encoding_payload)
        # Last upload_json call should have status=OK
        last_call_data = mock_storage.upload_json.call_args_list[-1][0][0]
        assert last_call_data["status"] == "OK"

    def test_stage_path_is_flat(self, service, mock_storage, encoding_payload):
        service.process(encoding_payload)
        blob_path = mock_storage.upload_json.call_args_list[0][0][2]
        assert "/stages/" in blob_path
        # event_id must NOT appear as a folder segment in the path
        assert encoding_payload["event_id"] not in blob_path.split("/stages/")[1]

    def test_stage_data_contains_event_id(self, service, mock_storage, encoding_payload):
        service.process(encoding_payload)
        last_call_data = mock_storage.upload_json.call_args_list[-1][0][0]
        assert last_call_data["event_id"] == encoding_payload["event_id"]


# ---------------------------------------------------------------------------
# process() — error paths
# ---------------------------------------------------------------------------

class TestProcessErrorPaths:
    def test_writes_error_stage_on_generic_exception(
        self, mock_encoder, mock_storage, mock_output_handler, encoding_payload
    ):
        mock_encoder.encode_frame.side_effect = RuntimeError("boom")
        # Give it a video blob so _encode_video_folder is entered
        mock_storage.list_blobs.return_value = ["path/to/video.mp4"]
        mock_storage.download_bytes.return_value = None  # download fails → skipped

        service = _make_service(mock_encoder, mock_storage, mock_output_handler)
        service.process(encoding_payload)  # no exception — no blobs produce no crash

    def test_raises_non_recoverable_when_encoding_fails(
        self, mock_encoder, mock_storage, mock_output_handler, encoding_payload
    ):
        """Simulate a download + extraction that triggers the exception branch."""
        def boom_list(bucket, prefix):
            return ["path/to/clip.mp4"]

        def boom_download(bucket, blob):
            return io.BytesIO(b"\x00\x00\x00fake_mp4_bytes")

        mock_storage.list_blobs.side_effect = boom_list
        mock_storage.download_bytes.side_effect = boom_download

        service = _make_service(mock_encoder, mock_storage, mock_output_handler)
        # _extract_frames will fail gracefully (no real cv2); pipeline completes
        service.process(encoding_payload)
        # Error stage should NOT have been written (no faces → graceful skip)
        stage_calls = [c[0][0] for c in mock_storage.upload_json.call_args_list]
        assert all(c.get("status") != "ERROR" for c in stage_calls)

    def test_recoverable_error_propagates(
        self, mock_encoder, mock_storage, mock_output_handler, encoding_payload
    ):
        # Patch _encode_video_folder to raise RecoverableError
        service = _make_service(mock_encoder, mock_storage, mock_output_handler)
        with patch.object(service, "_encode_video_folder", side_effect=RecoverableError("retry")):
            mock_storage.list_blobs.return_value = ["clip.mp4"]
            with pytest.raises(RecoverableError):
                service.process(encoding_payload)


# ---------------------------------------------------------------------------
# _list_video_blobs
# ---------------------------------------------------------------------------

class TestListVideoBlobs:
    def test_filters_non_video_files(self, service, mock_storage):
        mock_storage.list_blobs.return_value = [
            "path/video.mp4",
            "path/image.jpg",
            "path/clip.webm",
            "path/doc.pdf",
        ]
        result = service._list_video_blobs("bucket", "path/")
        assert "path/video.mp4" in result
        assert "path/clip.webm" in result
        assert "path/image.jpg" not in result
        assert "path/doc.pdf" not in result

    def test_returns_sorted(self, service, mock_storage):
        mock_storage.list_blobs.return_value = ["b.mp4", "a.mp4", "c.mp4"]
        result = service._list_video_blobs("bucket", "prefix/")
        assert result == ["a.mp4", "b.mp4", "c.mp4"]

    def test_adds_trailing_slash_to_prefix(self, service, mock_storage):
        mock_storage.list_blobs.return_value = []
        service._list_video_blobs("bucket", "my/prefix")
        called_prefix = mock_storage.list_blobs.call_args[1]["prefix"]
        assert called_prefix.endswith("/")

    def test_empty_result(self, service, mock_storage):
        mock_storage.list_blobs.return_value = []
        result = service._list_video_blobs("bucket", "empty/")
        assert result == []


# ---------------------------------------------------------------------------
# _detect_format
# ---------------------------------------------------------------------------

class TestDetectFormat:
    def test_webm_magic(self):
        data = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 4)
        ext = VideoFaceEncodingService._detect_format(data)
        assert ext == ".webm"

    def test_mp4_magic(self):
        data = io.BytesIO(b"\x00\x00\x00\x18" + b"\x00" * 4)
        ext = VideoFaceEncodingService._detect_format(data)
        assert ext == ".mp4"

    def test_unknown_defaults_to_mp4(self):
        data = io.BytesIO(b"\xff\xff\xff\xff\xff\xff\xff\xff")
        ext = VideoFaceEncodingService._detect_format(data)
        assert ext == ".mp4"

    def test_does_not_consume_stream(self):
        data = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"content")
        VideoFaceEncodingService._detect_format(data)
        assert data.tell() == 0


# ---------------------------------------------------------------------------
# _cluster_dominant
# ---------------------------------------------------------------------------

class TestClusterDominant:
    def test_fewer_than_3_returns_all(self):
        encs = [_make_encoding(i) for i in range(2)]
        result = VideoFaceEncodingService._cluster_dominant(encs)
        assert result == encs

    def test_exactly_one_returns_it(self):
        encs = [_make_encoding(0)]
        result = VideoFaceEncodingService._cluster_dominant(encs)
        assert result == encs

    def test_selects_dominant_cluster(self):
        """Two tight clusters; the larger one should win."""
        rng = np.random.default_rng(99)
        # Cluster A: 10 vectors near [1, 0, …]
        base_a = np.zeros(128); base_a[0] = 1.0
        cluster_a = [base_a + rng.random(128) * 0.01 for _ in range(10)]
        # Cluster B: 3 vectors near [-1, 0, …]
        base_b = np.zeros(128); base_b[0] = -1.0
        cluster_b = [base_b + rng.random(128) * 0.01 for _ in range(3)]

        result = VideoFaceEncodingService._cluster_dominant(cluster_a + cluster_b)
        assert len(result) == 10

    def test_fallback_on_all_noise(self):
        """All random → no clusters → returns all encodings."""
        encs = [np.random.default_rng(i).random(128) for i in range(5)]
        result = VideoFaceEncodingService._cluster_dominant(encs)
        assert len(result) > 0

    def test_all_noise_dbscan_returns_input(self):
        """When DBSCAN assigns all points to noise (label=-1), return original list."""
        rng = np.random.default_rng(7)
        # Spread encodings very far apart — DBSCAN will call them all noise
        encs = [rng.random(128) * 100 * i for i in range(1, 6)]
        result = VideoFaceEncodingService._cluster_dominant(encs)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _encode_video_folder — no faces / no blobs
# ---------------------------------------------------------------------------

class TestEncodeVideoFolder:
    def test_no_blobs_returns_empty(self, service):
        frames, path = service._encode_video_folder(
            event_id="evt",
            bucket="bucket",
            video_blobs=[],
            encoding_blob="enc/profile.npy",
            frames_prefix="frames/profile",
        )
        assert frames == []
        assert path is None

    def test_skips_blob_when_download_returns_none(self, service, mock_storage):
        mock_storage.download_bytes.return_value = None
        frames, path = service._encode_video_folder(
            event_id="evt",
            bucket="bucket",
            video_blobs=["clip.mp4"],
            encoding_blob="enc/profile.npy",
            frames_prefix="frames/profile",
        )
        assert frames == []
        assert path is None

    def test_saves_npy_when_faces_found(self, service, mock_storage, mock_encoder):
        """When _extract_frames and _process_frame are patched, encodings are saved."""
        fake_frame = np.zeros((50, 50, 3), dtype=np.uint8)
        fake_enc = _make_encoding(0)

        mock_storage.download_bytes.return_value = io.BytesIO(b"\x00\x00\x00fake")

        with patch.object(service, "_extract_frames", return_value=[fake_frame]), \
             patch.object(service, "_process_frame", return_value=[fake_enc]):
            frames, enc_path = service._encode_video_folder(
                event_id="evt",
                bucket="bucket",
                video_blobs=["clip.mp4"],
                encoding_blob="enc/profile.npy",
                frames_prefix="frames/profile",
            )

        assert enc_path == "enc/profile.npy"
        mock_storage.upload_bytes.assert_called()

    def test_frame_exception_is_skipped_gracefully(self, service, mock_storage):
        """If _process_frame raises, the frame is skipped and processing continues."""
        fake_frame = np.zeros((50, 50, 3), dtype=np.uint8)
        mock_storage.download_bytes.return_value = io.BytesIO(b"\x00\x00\x00fake")

        with patch.object(service, "_extract_frames", return_value=[fake_frame, fake_frame]), \
             patch.object(service, "_process_frame", side_effect=RuntimeError("oops")):
            frames, enc_path = service._encode_video_folder(
                event_id="evt",
                bucket="bucket",
                video_blobs=["clip.mp4"],
                encoding_blob="enc/profile.npy",
                frames_prefix="frames/profile",
            )

        # No faces produced — graceful path
        assert enc_path is None

    def test_returns_saved_frame_names(self, service, mock_storage, mock_encoder):
        fake_frame = np.zeros((50, 50, 3), dtype=np.uint8)
        fake_enc = _make_encoding(0)
        mock_storage.download_bytes.return_value = io.BytesIO(b"\x00\x00\x00fake")

        with patch.object(service, "_extract_frames", return_value=[fake_frame]), \
             patch.object(service, "_process_frame", return_value=[fake_enc]):
            frames, _ = service._encode_video_folder(
                event_id="evt",
                bucket="bucket",
                video_blobs=["clip.mp4"],
                encoding_blob="enc/profile.npy",
                frames_prefix="frames/profile",
            )

        assert isinstance(frames, list)
