"""Unit tests for MinioStorageService."""
import io
from unittest.mock import MagicMock, patch

from common.services.cloud.minio.storage import MinioStorageService


class TestMinioStorageService:
    def test_from_env_builds_service(self):
        with patch.dict(
            "os.environ",
            {
                "MINIO_ENDPOINT": "localhost:9000",
                "MINIO_ACCESS_KEY": "minio",
                "MINIO_SECRET_KEY": "secret",
                "MINIO_SECURE": "false",
                "MINIO_REGION": "us-east-1",
            },
            clear=False,
        ):
            with patch("common.services.cloud.minio.storage.boto3.client") as mock_client:
                svc = MinioStorageService.from_env()
        assert isinstance(svc, MinioStorageService)
        mock_client.assert_called_once()

    def test_download_bytes_success(self):
        with patch("common.services.cloud.minio.storage.boto3.client") as mock_client:
            mock_s3 = MagicMock()
            mock_client.return_value = mock_s3
            mock_s3.get_object.return_value = {"Body": io.BytesIO(b"abc")}
            svc = MinioStorageService("localhost:9000", "k", "s")

        out = svc.download_bytes("bucket", "path/file")
        assert isinstance(out, io.BytesIO)
        assert out.read() == b"abc"

    def test_upload_bytes_forwards_body(self):
        with patch("common.services.cloud.minio.storage.boto3.client") as mock_client:
            mock_s3 = MagicMock()
            mock_client.return_value = mock_s3
            svc = MinioStorageService("localhost:9000", "k", "s")

        ok = svc.upload_bytes(b"data", "bucket", "path/file", "application/octet-stream")
        assert ok is True
        mock_s3.put_object.assert_called_once()

    def test_list_blobs_returns_keys(self):
        with patch("common.services.cloud.minio.storage.boto3.client") as mock_client:
            mock_s3 = MagicMock()
            mock_client.return_value = mock_s3
            paginator = MagicMock()
            paginator.paginate.return_value = [
                {"Contents": [{"Key": "a.txt"}, {"Key": "b/c.txt"}]}
            ]
            mock_s3.get_paginator.return_value = paginator
            svc = MinioStorageService("localhost:9000", "k", "s")

        keys = svc.list_blobs("bucket", "")
        assert keys == ["a.txt", "b/c.txt"]

    def test_blob_exists_true_and_false(self):
        with patch("common.services.cloud.minio.storage.boto3.client") as mock_client:
            mock_s3 = MagicMock()
            mock_client.return_value = mock_s3
            svc = MinioStorageService("localhost:9000", "k", "s")

        mock_s3.head_object.return_value = {}
        assert svc.blob_exists("bucket", "path") is True

        mock_s3.head_object.side_effect = Exception("missing")
        assert svc.blob_exists("bucket", "path") is False
