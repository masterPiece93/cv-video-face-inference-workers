"""Unit tests for VideoFaceEncodingService internals and common.services.encoding factory."""
import io
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from common.services.errors import NonRecoverableError
from workers.face_encoding_worker.services.encoding import VideoFaceEncodingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_encoding(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.random(128).astype(np.float64)
    return v / np.linalg.norm(v)


def _make_service(mock_encoder=None, mock_storage=None, mock_output_handler=None):
    return VideoFaceEncodingService(
        encoder=mock_encoder or MagicMock(),
        storage=mock_storage or MagicMock(),
        output_handler=mock_output_handler or MagicMock(),
        frame_sample_rate=30,
        face_tolerance=0.5,
    )


# ---------------------------------------------------------------------------
# common.services.encoding.get_encoder — unknown backend
# ---------------------------------------------------------------------------

class TestGetEncoderFactory:
    def test_unknown_backend_raises_value_error(self):
        from common.services.encoding import get_encoder
        with pytest.raises(ValueError, match="Unknown encoder backend"):
            get_encoder("nonexistent_backend")

    def test_error_message_lists_available_backends(self):
        from common.services.encoding import get_encoder
        with pytest.raises(ValueError) as exc_info:
            get_encoder("bad_name")
        assert "face_recognition" in str(exc_info.value) or "fdetect" in str(exc_info.value)

    def test_face_recognition_backend_accepted(self):
        """Known backend name must not raise ValueError (even if import fails later)."""
        from common.services.encoding import _BACKENDS
        assert "face_recognition" in _BACKENDS

    def test_fdetect_backend_accepted(self):
        from common.services.encoding import _BACKENDS
        assert "fdetect" in _BACKENDS


# ---------------------------------------------------------------------------
# _encode_video() — download failure
# ---------------------------------------------------------------------------

class TestEncodeVideoDownloadFailure:
    def test_raises_non_recoverable_when_download_returns_none(self):
        mock_storage = MagicMock()
        mock_storage.download_bytes.return_value = None

        svc = _make_service(mock_storage=mock_storage)
        with pytest.raises(NonRecoverableError, match="Failed to download"):
            svc._encode_video(
                event_id="evt",
                bucket="bucket",
                video_blob="clips/vid.mp4",
                encoding_blob="enc/profile.npy",
                frames_prefix="frames/profile",
            )


# ---------------------------------------------------------------------------
# _encode_video() — no frames extracted
# ---------------------------------------------------------------------------

class TestEncodeVideoNoFrames:
    def test_returns_empty_list_and_none_when_no_frames(self):
        mock_storage = MagicMock()
        mock_storage.download_bytes.return_value = io.BytesIO(b"fake-video-data")

        svc = _make_service(mock_storage=mock_storage)
        with patch.object(svc, "_extract_frames", return_value=[]):
            result = svc._encode_video(
                event_id="evt",
                bucket="bucket",
                video_blob="clips/vid.mp4",
                encoding_blob="enc/profile.npy",
                frames_prefix="frames/profile",
            )

        assert result == ([], None)


# ---------------------------------------------------------------------------
# _encode_video() — no faces found in any frame
# ---------------------------------------------------------------------------

class TestEncodeVideoNoFaces:
    def test_returns_empty_list_and_none_when_no_faces(self):
        mock_storage = MagicMock()
        mock_storage.download_bytes.return_value = io.BytesIO(b"fake-video-data")

        mock_encoder = MagicMock()
        mock_encoder.is_duplicate.return_value = False

        svc = _make_service(mock_encoder=mock_encoder, mock_storage=mock_storage)
        fake_frame = np.zeros((50, 50, 3), dtype=np.uint8)

        with patch.object(svc, "_extract_frames", return_value=[fake_frame]), \
             patch.object(svc, "_process_frame", return_value=[]):  # no encodings
            result = svc._encode_video(
                event_id="evt",
                bucket="bucket",
                video_blob="clips/vid.mp4",
                encoding_blob="enc/profile.npy",
                frames_prefix="frames/profile",
            )

        assert result == ([], None)


# ---------------------------------------------------------------------------
# _process_frame() — bio is None
# ---------------------------------------------------------------------------

class TestProcessFrame:
    def test_returns_empty_list_when_frame_to_bytesio_returns_none(self):
        svc = _make_service()
        fake_frame = np.zeros((50, 50, 3), dtype=np.uint8)

        with patch("workers.face_encoding_worker.services.encoding.frame_to_bytesio",
                   return_value=None):
            result = svc._process_frame(fake_frame)

        assert result == []

    def test_calls_encoder_encode_frame_when_bio_available(self):
        mock_encoder = MagicMock()
        mock_encoder.encode_frame.return_value = [_make_encoding(0)]
        svc = _make_service(mock_encoder=mock_encoder)

        fake_bio = io.BytesIO(b"\xff\xd8\xff")
        with patch("workers.face_encoding_worker.services.encoding.frame_to_bytesio",
                   return_value=fake_bio):
            result = svc._process_frame(np.zeros((50, 50, 3), dtype=np.uint8))

        mock_encoder.encode_frame.assert_called_once_with(fake_bio)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _extract_frames() — cap.isOpened() False path
# ---------------------------------------------------------------------------

class TestExtractFrames:
    def test_returns_empty_list_when_video_cannot_be_opened(self):
        svc = _make_service()
        video_data = io.BytesIO(b"garbage-data-not-a-video")

        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False

        with patch("workers.face_encoding_worker.services.encoding.cv2.VideoCapture",
                   return_value=mock_cap):
            result = svc._extract_frames(video_data)

        assert result == []

    def test_returns_empty_list_on_exception(self):
        svc = _make_service()
        video_data = io.BytesIO(b"garbage")

        with patch("workers.face_encoding_worker.services.encoding.cv2.VideoCapture",
                   side_effect=Exception("cv2 error")):
            result = svc._extract_frames(video_data)

        assert result == []


# ---------------------------------------------------------------------------
# _detect_format() — magic bytes
# ---------------------------------------------------------------------------

class TestDetectFormat:
    def test_webm_magic_returns_webm(self):
        data = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 4)
        assert VideoFaceEncodingService._detect_format(data) == ".webm"

    def test_mp4_magic_returns_mp4(self):
        data = io.BytesIO(b"\x00\x00\x00\x00" + b"\x00" * 4)
        assert VideoFaceEncodingService._detect_format(data) == ".mp4"

    def test_unknown_magic_returns_mp4_fallback(self):
        data = io.BytesIO(b"\xde\xad\xbe\xef" + b"\x00" * 4)
        assert VideoFaceEncodingService._detect_format(data) == ".mp4"

    def test_does_not_consume_stream(self):
        data = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"\x00" * 4)
        VideoFaceEncodingService._detect_format(data)
        assert data.tell() == 0


# ---------------------------------------------------------------------------
# _cluster_dominant() — small encoding count (< 3)
# ---------------------------------------------------------------------------

class TestClusterDominant:
    def test_empty_list_returns_empty(self):
        result = VideoFaceEncodingService._cluster_dominant([])
        assert result == []

    def test_single_encoding_returned_as_is(self):
        encs = [_make_encoding(0)]
        result = VideoFaceEncodingService._cluster_dominant(encs)
        assert result == encs

    def test_two_encodings_returned_as_is(self):
        encs = [_make_encoding(0), _make_encoding(1)]
        result = VideoFaceEncodingService._cluster_dominant(encs)
        assert result == encs

    def test_three_or_more_triggers_dbscan(self):
        """With >= 3 encodings clustering path is taken; result must be non-empty."""
        encs = [_make_encoding(i) for i in range(5)]
        result = VideoFaceEncodingService._cluster_dominant(encs)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# process() — profile blobs empty warning (line 129)
# ---------------------------------------------------------------------------

class TestProcessProfileBlobsEmpty:
    def test_no_profile_blobs_logs_warning_and_continues(
        self, mock_encoder, mock_storage, mock_output_handler, encoding_payload
    ):
        """When profile video folder is empty, a warning is logged but no exception is raised."""
        mock_storage.list_blobs.return_value = []  # no blobs for any prefix

        svc = VideoFaceEncodingService(
            encoder=mock_encoder,
            storage=mock_storage,
            output_handler=mock_output_handler,
            frame_sample_rate=30,
            face_tolerance=0.5,
        )
        svc.process(encoding_payload)  # must NOT raise
        mock_output_handler.publish.assert_called_once()


# ---------------------------------------------------------------------------
# process() — interview no blobs (continue branch line 156)
# ---------------------------------------------------------------------------

class TestProcessInterviewNoBlobsContinue:
    def test_interview_with_no_blobs_is_skipped(
        self, mock_encoder, mock_storage, mock_output_handler, encoding_payload
    ):
        """When interview blob list is empty, that interview is skipped (continue)."""
        call_count = [0]

        def _list_blobs(bucket, prefix):
            call_count[0] += 1
            return []  # always empty

        mock_storage.list_blobs.side_effect = _list_blobs

        svc = VideoFaceEncodingService(
            encoder=mock_encoder,
            storage=mock_storage,
            output_handler=mock_output_handler,
            frame_sample_rate=30,
            face_tolerance=0.5,
        )
        svc.process(encoding_payload)  # must not raise
        # list_blobs called once for profile + once per interview
        interviews = encoding_payload["lookup_map"]["interviews"]
        assert call_count[0] >= 1 + len(interviews)


# ---------------------------------------------------------------------------
# Frame-encoding parallelism — backend-capability dispatch
# ---------------------------------------------------------------------------

def _service_with(supports_parallel: bool, max_workers: int = 4):
    """Build a service whose encoder advertises a given parallel capability."""
    encoder = MagicMock()
    encoder.supports_parallel = supports_parallel
    encoder.is_duplicate.return_value = False
    return VideoFaceEncodingService(
        encoder=encoder,
        storage=MagicMock(),
        output_handler=MagicMock(),
        frame_sample_rate=30,
        face_tolerance=0.5,
        max_workers=max_workers,
    )


def _frame(i: int) -> np.ndarray:
    """A tiny frame whose pixel value encodes its index for later assertions."""
    return np.full((2, 2, 3), i, dtype=np.uint8)


class TestEncodeFramesDispatch:
    def test_sequential_backend_uses_sequential_path(self):
        svc = _service_with(supports_parallel=False, max_workers=4)
        frames = [_frame(0), _frame(1)]
        with patch.object(svc, "_encode_frames_sequential", return_value=[]) as seq, \
             patch.object(svc, "_encode_frames_parallel", return_value=[]) as par:
            svc._encode_frames(frames, "evt", "clip.mp4")
        seq.assert_called_once()
        par.assert_not_called()

    def test_parallel_backend_uses_parallel_path(self):
        svc = _service_with(supports_parallel=True, max_workers=4)
        frames = [_frame(0), _frame(1)]
        with patch.object(svc, "_encode_frames_sequential", return_value=[]) as seq, \
             patch.object(svc, "_encode_frames_parallel", return_value=[]) as par:
            svc._encode_frames(frames, "evt", "clip.mp4")
        par.assert_called_once()
        seq.assert_not_called()

    def test_parallel_backend_with_single_worker_stays_sequential(self):
        """max_workers == 1 → no benefit, avoid thread overhead."""
        svc = _service_with(supports_parallel=True, max_workers=1)
        frames = [_frame(0)]
        with patch.object(svc, "_encode_frames_sequential", return_value=[]) as seq, \
             patch.object(svc, "_encode_frames_parallel", return_value=[]) as par:
            svc._encode_frames(frames, "evt", "clip.mp4")
        seq.assert_called_once()
        par.assert_not_called()


class TestEncodeFramesSequential:
    def test_returns_ordered_frame_encoding_pairs(self):
        svc = _service_with(supports_parallel=False)
        frames = [_frame(0), _frame(1), _frame(2)]
        with patch.object(svc, "_process_frame",
                          side_effect=lambda f: [np.array([float(f[0, 0, 0])])]):
            result = svc._encode_frames_sequential(frames, "evt", "clip.mp4")
        assert [int(encs[0][0]) for _, encs in result] == [0, 1, 2]

    def test_skips_frame_that_raises(self):
        svc = _service_with(supports_parallel=False)
        frames = [_frame(0), _frame(1), _frame(2)]

        def _proc(f):
            if int(f[0, 0, 0]) == 1:
                raise RuntimeError("boom")
            return [np.array([float(f[0, 0, 0])])]

        with patch.object(svc, "_process_frame", side_effect=_proc):
            result = svc._encode_frames_sequential(frames, "evt", "clip.mp4")
        assert [int(encs[0][0]) for _, encs in result] == [0, 2]


class TestEncodeFramesParallel:
    def test_preserves_input_order_despite_out_of_order_completion(self):
        """Earlier frames finish later, but results must come back in order."""
        svc = _service_with(supports_parallel=True, max_workers=4)
        n = 4
        frames = [_frame(i) for i in range(n)]

        def _slow(f):
            i = int(f[0, 0, 0])
            time.sleep((n - i) * 0.01)  # frame 0 is slowest → completes last
            return [np.array([float(i)])]

        with patch.object(svc, "_process_frame", side_effect=_slow):
            result = svc._encode_frames_parallel(frames, "evt", "clip.mp4")

        assert [int(f[0, 0, 0]) for f, _ in result] == [0, 1, 2, 3]
        assert [int(encs[0][0]) for _, encs in result] == [0, 1, 2, 3]

    def test_skips_frame_that_raises(self):
        svc = _service_with(supports_parallel=True, max_workers=4)
        frames = [_frame(0), _frame(1), _frame(2)]

        def _proc(f):
            if int(f[0, 0, 0]) == 1:
                raise RuntimeError("boom")
            return [np.array([float(f[0, 0, 0])])]

        with patch.object(svc, "_process_frame", side_effect=_proc):
            result = svc._encode_frames_parallel(frames, "evt", "clip.mp4")
        assert [int(encs[0][0]) for _, encs in result] == [0, 2]

    def test_all_frames_encoded_exactly_once(self):
        svc = _service_with(supports_parallel=True, max_workers=4)
        frames = [_frame(i) for i in range(6)]
        seen = []

        def _proc(f):
            seen.append(int(f[0, 0, 0]))
            return [np.array([float(f[0, 0, 0])])]

        with patch.object(svc, "_process_frame", side_effect=_proc):
            svc._encode_frames_parallel(frames, "evt", "clip.mp4")
        assert sorted(seen) == [0, 1, 2, 3, 4, 5]


class TestCollectUnique:
    def test_keeps_non_duplicates_with_aligned_frames(self):
        svc = _service_with(supports_parallel=False)
        svc.encoder.is_duplicate.return_value = False  # nothing is a duplicate

        f0, f1 = _frame(0), _frame(1)
        pairs = [(f0, [np.array([0.0])]), (f1, [np.array([1.0])])]
        all_encodings, all_frames = [], []
        svc._collect_unique(pairs, all_encodings, all_frames)

        assert len(all_encodings) == 2
        assert len(all_frames) == 2
        assert int(all_frames[0][0, 0, 0]) == 0
        assert int(all_frames[1][0, 0, 0]) == 1

    def test_drops_duplicates(self):
        svc = _service_with(supports_parallel=False)
        # First accepted, everything after flagged duplicate
        svc.encoder.is_duplicate.side_effect = [False, True, True]

        f0, f1 = _frame(0), _frame(1)
        pairs = [
            (f0, [np.array([0.0])]),
            (f1, [np.array([1.0]), np.array([2.0])]),
        ]
        all_encodings, all_frames = [], []
        svc._collect_unique(pairs, all_encodings, all_frames)

        assert len(all_encodings) == 1  # only the first survived

    def test_empty_input_is_noop(self):
        svc = _service_with(supports_parallel=False)
        all_encodings, all_frames = [], []
        svc._collect_unique([], all_encodings, all_frames)
        assert all_encodings == []
        assert all_frames == []


class TestParallelSequentialEquivalence:
    """Both execution modes must produce identical, deterministic output."""

    def test_same_ordered_results_regardless_of_mode(self):
        frames = [_frame(i) for i in range(5)]

        def _proc(f):
            return [np.array([float(f[0, 0, 0])])]

        seq_svc = _service_with(supports_parallel=False, max_workers=4)
        par_svc = _service_with(supports_parallel=True, max_workers=4)

        with patch.object(seq_svc, "_process_frame", side_effect=_proc):
            seq_result = seq_svc._encode_frames(frames, "evt", "clip.mp4")
        with patch.object(par_svc, "_process_frame", side_effect=_proc):
            par_result = par_svc._encode_frames(frames, "evt", "clip.mp4")

        seq_markers = [int(encs[0][0]) for _, encs in seq_result]
        par_markers = [int(encs[0][0]) for _, encs in par_result]
        assert seq_markers == par_markers == [0, 1, 2, 3, 4]

