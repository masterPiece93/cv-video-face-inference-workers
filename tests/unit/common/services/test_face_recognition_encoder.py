"""Unit tests for FaceRecognitionEncoder.

The `face_recognition` library (dlib-based) is an optional dependency and
may not be installed in CI. All tests mock it entirely so the suite is
always portable.
"""
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Module-level patch: ensure `face_recognition` module can be imported
# even when dlib is absent.
# ---------------------------------------------------------------------------

_FAKE_FR = MagicMock()


def _make_encoder(model="hog", num_jitters=1):
    """Return a FaceRecognitionEncoder with face_recognition fully mocked."""
    with patch.dict("sys.modules", {"face_recognition": _FAKE_FR}):
        from common.services.encoding.face_recognition_encoder import FaceRecognitionEncoder
        enc = FaceRecognitionEncoder(model=model, num_jitters=num_jitters)
        enc._fr = MagicMock()  # replace per-instance reference with fresh mock
    return enc


# ---------------------------------------------------------------------------
# __init__ — deferred import
# ---------------------------------------------------------------------------

class TestFaceRecognitionEncoderInit:
    def test_imports_face_recognition_on_init(self):
        mock_fr = MagicMock()
        with patch.dict("sys.modules", {"face_recognition": mock_fr}):
            from common.services.encoding.face_recognition_encoder import FaceRecognitionEncoder
            import importlib, common.services.encoding.face_recognition_encoder as _mod
            importlib.reload(_mod)
            enc = _mod.FaceRecognitionEncoder()
        assert enc._fr is mock_fr

    def test_stores_model_and_num_jitters(self):
        enc = _make_encoder(model="cnn", num_jitters=2)
        assert enc.model == "cnn"
        assert enc.num_jitters == 2


# ---------------------------------------------------------------------------
# supports_parallel — dlib is NOT thread-safe
# ---------------------------------------------------------------------------

class TestSupportsParallel:
    def test_supports_parallel_is_false_on_instance(self):
        enc = _make_encoder()
        assert enc.supports_parallel is False

    def test_supports_parallel_is_false_class_attribute(self):
        from common.services.encoding.face_recognition_encoder import FaceRecognitionEncoder
        assert FaceRecognitionEncoder.supports_parallel is False


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------

class TestNameProperty:
    def test_name_includes_model(self):
        enc = _make_encoder(model="hog")
        assert enc.name == "face_recognition_hog"

    def test_name_cnn_model(self):
        enc = _make_encoder(model="cnn")
        assert enc.name == "face_recognition_cnn"


# ---------------------------------------------------------------------------
# encode_frame()
# ---------------------------------------------------------------------------

class TestEncodeFrame:
    def _frame_bytes(self) -> io.BytesIO:
        return io.BytesIO(b"\xff\xd8\xff" + b"\x00" * 64)

    def test_returns_empty_list_when_no_face_locations(self):
        enc = _make_encoder()
        enc._fr.load_image_file.return_value = MagicMock()
        enc._fr.face_locations.return_value = []  # no faces

        result = enc.encode_frame(self._frame_bytes())
        assert result == []

    def test_returns_encodings_when_faces_detected(self):
        enc = _make_encoder()
        fake_img = MagicMock()
        fake_encoding = np.random.default_rng(0).random(128)

        enc._fr.load_image_file.return_value = fake_img
        enc._fr.face_locations.return_value = [(10, 50, 60, 20)]  # one face
        enc._fr.face_encodings.return_value = [fake_encoding]

        result = enc.encode_frame(self._frame_bytes())
        assert len(result) == 1
        np.testing.assert_array_equal(result[0], fake_encoding)

    def test_returns_multiple_encodings_for_multiple_faces(self):
        enc = _make_encoder()
        enc._fr.load_image_file.return_value = MagicMock()
        enc._fr.face_locations.return_value = [(10, 50, 60, 20), (80, 120, 130, 90)]
        enc._fr.face_encodings.return_value = [
            np.zeros(128), np.ones(128)
        ]

        result = enc.encode_frame(self._frame_bytes())
        assert len(result) == 2

    def test_exception_returns_empty_list(self):
        enc = _make_encoder()
        enc._fr.load_image_file.side_effect = Exception("corrupt image")

        result = enc.encode_frame(self._frame_bytes())
        assert result == []

    def test_seeks_to_zero_before_decoding(self):
        enc = _make_encoder()
        enc._fr.face_locations.return_value = []

        buf = self._frame_bytes()
        buf.seek(10)  # position mid-way
        enc.encode_frame(buf)

        # load_image_file should have received the seeked-to-zero buffer
        enc._fr.load_image_file.assert_called_once()

    def test_face_encodings_called_with_locations(self):
        enc = _make_encoder(num_jitters=2)
        fake_img = MagicMock()
        locations = [(10, 50, 60, 20)]
        enc._fr.load_image_file.return_value = fake_img
        enc._fr.face_locations.return_value = locations
        enc._fr.face_encodings.return_value = [np.zeros(128)]

        enc.encode_frame(self._frame_bytes())
        enc._fr.face_encodings.assert_called_once_with(
            fake_img,
            known_face_locations=locations,
            num_jitters=2,
            model="small",
        )


# ---------------------------------------------------------------------------
# calculate_distance()
# ---------------------------------------------------------------------------

class TestCalculateDistance:
    def _enc(self, seed=0) -> np.ndarray:
        rng = np.random.default_rng(seed)
        v = rng.random(128)
        return v / np.linalg.norm(v)

    def test_empty_known_encodings_returns_empty_array(self):
        enc = _make_encoder()
        result = enc.calculate_distance([], self._enc(0))
        assert isinstance(result, np.ndarray)
        assert len(result) == 0

    def test_single_known_encoding_returns_single_distance(self):
        enc = _make_encoder()
        ref = self._enc(0)
        result = enc.calculate_distance([ref], ref)
        assert len(result) == 1
        assert result[0] == pytest.approx(0.0, abs=1e-9)

    def test_distance_is_euclidean(self):
        enc = _make_encoder()
        ref = self._enc(1)
        candidate = self._enc(2)
        result = enc.calculate_distance([ref], candidate)
        expected = np.linalg.norm(ref - candidate)
        assert result[0] == pytest.approx(expected, abs=1e-9)

    def test_multiple_known_encodings_returns_correct_count(self):
        enc = _make_encoder()
        knowns = [self._enc(i) for i in range(5)]
        candidate = self._enc(99)
        result = enc.calculate_distance(knowns, candidate)
        assert len(result) == 5
