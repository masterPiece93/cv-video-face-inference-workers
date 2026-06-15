"""Unit tests for GCPStorageService.download_bytes_from_url."""
import io
from unittest.mock import MagicMock, patch

import pytest

from common.services.cloud.gcp.storage import GCPStorageService


@pytest.fixture()
def storage():
    """Return a GCPStorageService with a mocked GCS client."""
    with patch("common.services.cloud.gcp.storage.storage") as mock_gcs:
        mock_client = MagicMock()
        mock_gcs.Client.return_value = mock_client
        svc = GCPStorageService()
    return svc


class TestDownloadBytesFromUrl:
    def test_success_returns_bytesio(self, storage):
        fake_content = b"fake-image-bytes"
        mock_response = MagicMock()
        mock_response.content = fake_content
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response) as mock_get:
            result = storage.download_bytes_from_url("https://example.com/photo.jpg")

        mock_get.assert_called_once_with("https://example.com/photo.jpg", timeout=30)
        assert isinstance(result, io.BytesIO)
        assert result.read() == fake_content

    def test_http_error_returns_none(self, storage):
        import requests

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404")

        with patch("requests.get", return_value=mock_response):
            result = storage.download_bytes_from_url("https://example.com/missing.jpg")

        assert result is None

    def test_connection_error_returns_none(self, storage):
        import requests

        with patch("requests.get", side_effect=requests.ConnectionError("timeout")):
            result = storage.download_bytes_from_url("https://example.com/photo.jpg")

        assert result is None

    def test_returned_bytesio_is_seeked_to_zero(self, storage):
        mock_response = MagicMock()
        mock_response.content = b"data"
        mock_response.raise_for_status.return_value = None

        with patch("requests.get", return_value=mock_response):
            result = storage.download_bytes_from_url("https://example.com/photo.jpg")

        assert result.tell() == 0
