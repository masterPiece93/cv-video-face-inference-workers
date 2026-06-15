"""Root conftest — shared fixtures used across all worker test modules."""
import io
import json
from typing import List
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def make_encoding(seed: int = 0) -> np.ndarray:
    """Return a deterministic 128-d unit-normalised face encoding vector."""
    rng = np.random.default_rng(seed)
    v = rng.random(128).astype(np.float64)
    return v / np.linalg.norm(v)


def make_encodings(n: int, seed: int = 0) -> np.ndarray:
    """Return an (n, 128) array of distinct encodings."""
    return np.stack([make_encoding(seed + i) for i in range(n)])


def numpy_to_bytesio(arr: np.ndarray) -> io.BytesIO:
    buf = io.BytesIO()
    np.save(buf, arr)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_encoding():
    return make_encoding(42)


@pytest.fixture()
def fake_encodings():
    return make_encodings(5, seed=10)


# --- Mock GCPStorageService ------------------------------------------------

@pytest.fixture()
def mock_storage():
    storage = MagicMock()
    storage.upload_json.return_value = True
    storage.upload_bytes.return_value = True
    storage.download_bytes.return_value = None
    storage.list_blobs.return_value = []
    storage.blob_exists.return_value = False
    storage.download_bytes_from_url.return_value = None
    return storage


# --- Mock BaseEncoder ------------------------------------------------------

@pytest.fixture()
def mock_encoder():
    encoder = MagicMock()
    encoder.encode_frame.return_value = []
    encoder.is_duplicate.return_value = False
    # Default to the sequential (thread-unsafe) backend behaviour so existing
    # tests are deterministic. Parallel-path tests set this to True explicitly.
    encoder.supports_parallel = False
    return encoder


# --- Mock output handlers --------------------------------------------------

@pytest.fixture()
def mock_output_handler():
    handler = MagicMock()
    handler.publish.return_value = None
    return handler


# --- Mock PubSub message ---------------------------------------------------

def _make_message(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.data = json.dumps(payload).encode("utf-8")
    msg.ack = MagicMock()
    msg.nack = MagicMock()
    return msg


@pytest.fixture()
def make_message():
    """Factory fixture: make_message(payload_dict) → mock PubSub message."""
    return _make_message


# ---------------------------------------------------------------------------
# Canonical payloads
# ---------------------------------------------------------------------------

@pytest.fixture()
def encoding_payload():
    return {
        "candidate_email": "jane.doe@example.com",
        "candidate_uid": "cand-001",
        "org_id": "org-42",
        "org_alias": "acme-inc",
        "bucket_name": "test-bucket",
        "event_id": "evt-001",
        "lookup_map": {
            "profile": "profile",
            "interviews": ["interview_1", "interview_2"],
        },
        "extra_info": None,
    }


@pytest.fixture()
def verification_payload():
    return {
        "candidate_email": "jane.doe@example.com",
        "candidate_uid": "cand-001",
        "org_id": "org-42",
        "org_alias": "acme-inc",
        "bucket_name": "test-bucket",
        "event_id": "evt-002",
        "lookup_map": {
            "profile": "profile",
            "interviews": ["interview_1", "interview_2"],
        },
        "sampled_frames": {},
        "extra_info": None,
    }


@pytest.fixture()
def onboarding_payload():
    return {
        "candidate_email": "jane.doe@example.com",
        "candidate_uid": "cand-001",
        "org_id": "org-42",
        "org_alias": "acme-inc",
        "bucket_name": "test-bucket",
        "event_id": "evt-003",
        "lookup_map": {
            "profile": "profile",
            "interviews": ["interview_1"],
        },
        "onboarding_reference_path": "onboarding_reference/",
        "match_against": ["profile", "interview_1"],
        "extra_info": None,
    }
