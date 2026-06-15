"""Extended unit tests for GCPStorageService — covering uncovered branches."""
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from common.services.cloud.gcp.storage import GCPStorageService


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def storage():
    with patch("common.services.cloud.gcp.storage.storage") as mock_gcs:
        mock_client = MagicMock()
        mock_gcs.Client.return_value = mock_client
        svc = GCPStorageService()
        svc._client = mock_client
    return svc


@pytest.fixture()
def mock_client(storage):
    return storage._client


# ---------------------------------------------------------------------------
# __init__ — sa_path branch
# ---------------------------------------------------------------------------

class TestGCPStorageServiceInit:
    def test_default_uses_adc_client(self):
        with patch("common.services.cloud.gcp.storage.storage") as mock_gcs:
            mock_gcs.Client.return_value = MagicMock()
            svc = GCPStorageService()
        mock_gcs.Client.assert_called_once_with()

    def test_sa_path_uses_from_service_account_json(self, tmp_path):
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text(json.dumps({"type": "service_account"}))
        with patch("common.services.cloud.gcp.storage.storage") as mock_gcs:
            mock_gcs.Client.from_service_account_json.return_value = MagicMock()
            svc = GCPStorageService(sa_path=str(fake_sa))
        mock_gcs.Client.from_service_account_json.assert_called_once_with(str(fake_sa))


# ---------------------------------------------------------------------------
# download_bytes() — exception path
# ---------------------------------------------------------------------------

class TestDownloadBytesError:
    def test_exception_returns_none(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_blob.download_to_file.side_effect = Exception("network error")

        result = storage.download_bytes("my-bucket", "path/to/blob")
        assert result is None

    def test_success_returns_bytesio_seeked_to_zero(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        def _fill(buf):
            buf.write(b"file-content")
        mock_blob.download_to_file.side_effect = _fill

        result = storage.download_bytes("my-bucket", "path/file.bin")
        assert isinstance(result, io.BytesIO)
        assert result.read() == b"file-content"


# ---------------------------------------------------------------------------
# upload_bytes() — bytes branch and exception path
# ---------------------------------------------------------------------------

class TestUploadBytesExtended:
    def test_bytes_input_uses_upload_from_string(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        result = storage.upload_bytes(b"raw bytes", "bucket", "path/file.bin")
        mock_blob.upload_from_string.assert_called_once_with(
            b"raw bytes", content_type="application/octet-stream"
        )
        assert result is True

    def test_bytesio_input_uses_upload_from_file(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        buf = io.BytesIO(b"stream data")
        result = storage.upload_bytes(buf, "bucket", "path/file.bin")
        mock_blob.upload_from_file.assert_called_once()
        assert result is True

    def test_exception_returns_false(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_blob.upload_from_string.side_effect = Exception("upload failed")

        result = storage.upload_bytes(b"data", "bucket", "path/file.bin")
        assert result is False

    def test_content_type_forwarded(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        storage.upload_bytes(b"data", "bucket", "path/file.json", content_type="application/json")
        mock_blob.upload_from_string.assert_called_once_with(
            b"data", content_type="application/json"
        )


# ---------------------------------------------------------------------------
# upload_json() — delegates to upload_bytes
# ---------------------------------------------------------------------------

class TestUploadJson:
    def test_upload_json_serialises_dict(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        payload = {"status": "OK", "count": 3}
        result = storage.upload_json(payload, "bucket", "path/result.json")

        assert result is True
        call_args = mock_blob.upload_from_string.call_args
        uploaded_bytes = call_args[0][0]
        parsed = json.loads(uploaded_bytes.decode("utf-8"))
        assert parsed == payload

    def test_upload_json_content_type_is_json(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        storage.upload_json({"k": "v"}, "bucket", "path/result.json")
        _, kwargs = mock_blob.upload_from_string.call_args
        assert kwargs["content_type"] == "application/json"


# ---------------------------------------------------------------------------
# download_json() — bad JSON path
# ---------------------------------------------------------------------------

class TestDownloadJsonError:
    def test_valid_json_returns_parsed(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob

        def _fill(buf):
            buf.write(json.dumps({"k": "v"}).encode())
        mock_blob.download_to_file.side_effect = _fill

        result = storage.download_json("bucket", "path/data.json")
        assert result == {"k": "v"}

    def test_bad_json_returns_none(self, storage):
        with patch.object(storage, "download_bytes", return_value=io.BytesIO(b"NOT JSON!!!")):
            result = storage.download_json("bucket", "path/data.json")
        assert result is None

    def test_download_failure_returns_none(self, storage):
        with patch.object(storage, "download_bytes", return_value=None):
            result = storage.download_json("bucket", "path/data.json")
        assert result is None


# ---------------------------------------------------------------------------
# list_blobs() — exception path
# ---------------------------------------------------------------------------

class TestListBlobsError:
    def test_exception_returns_empty_list(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_bucket.list_blobs.side_effect = Exception("access denied")

        result = storage.list_blobs("bucket", "prefix/")
        assert result == []

    def test_success_returns_blob_names(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        b1, b2 = MagicMock(name="blob1"), MagicMock(name="blob2")
        b1.name = "prefix/file1.mp4"
        b2.name = "prefix/file2.mp4"
        mock_bucket.list_blobs.return_value = [b1, b2]

        result = storage.list_blobs("bucket", "prefix/")
        assert result == ["prefix/file1.mp4", "prefix/file2.mp4"]


# ---------------------------------------------------------------------------
# blob_exists() — exception path
# ---------------------------------------------------------------------------

class TestBlobExists:
    def test_exception_returns_false(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_blob.exists.side_effect = Exception("iam error")

        result = storage.blob_exists("bucket", "path/file")
        assert result is False

    def test_true_when_blob_exists(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = True

        assert storage.blob_exists("bucket", "path/file") is True

    def test_false_when_blob_absent(self, storage, mock_client):
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = False

        assert storage.blob_exists("bucket", "path/missing") is False
