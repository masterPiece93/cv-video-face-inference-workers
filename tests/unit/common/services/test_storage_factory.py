"""Unit tests for storage provider factory selection."""
from unittest.mock import patch

import pytest

from common.services.cloud.storage.factory import get_storage_service


class TestStorageFactory:
    def test_gcp_provider_returns_gcp_service(self):
        with patch("common.services.cloud.storage.factory.GCPStorageService") as mock_gcp:
            expected = object()
            mock_gcp.return_value = expected
            result = get_storage_service("gcp", sa_path="/tmp/sa.json")
        mock_gcp.assert_called_once_with(sa_path="/tmp/sa.json")
        assert result is expected

    def test_gcs_alias_maps_to_gcp(self):
        with patch("common.services.cloud.storage.factory.GCPStorageService") as mock_gcp:
            expected = object()
            mock_gcp.return_value = expected
            result = get_storage_service("gcs", sa_path=None)
        mock_gcp.assert_called_once_with(sa_path=None)
        assert result is expected

    def test_provider_is_case_insensitive(self):
        with patch("common.services.cloud.storage.factory.GCPStorageService") as mock_gcp:
            expected = object()
            mock_gcp.return_value = expected
            result = get_storage_service("GCS")
        mock_gcp.assert_called_once_with(sa_path=None)
        assert result is expected

    def test_minio_provider_builds_minio_service(self):
        with patch("common.services.cloud.minio.MinioStorageService") as mock_minio:
            expected = object()
            mock_minio.return_value = expected
            result = get_storage_service(
                "minio",
                minio_endpoint="localhost:9000",
                minio_access_key="k",
                minio_secret_key="s",
                minio_secure=True,
                minio_region="us-west-1",
            )
        mock_minio.assert_called_once_with(
            endpoint="localhost:9000",
            access_key="k",
            secret_key="s",
            secure=True,
            region_name="us-west-1",
        )
        assert result is expected

    def test_minio_missing_config_raises_value_error(self):
        with pytest.raises(ValueError, match="MinIO storage requires"):
            get_storage_service("minio", minio_endpoint="localhost:9000")

    def test_invalid_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported storage provider"):
            get_storage_service("azure")
