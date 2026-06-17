"""Unit tests for storage provider factory selection."""
from unittest.mock import patch

import pytest

from common.services.cloud.storage.factory import get_storage_service


class TestStorageFactory:
    def test_default_provider_is_gcp(self):
        with patch("common.services.cloud.storage.factory.GCPStorageService") as mock_gcp:
            expected = object()
            mock_gcp.return_value = expected
            with patch.dict("os.environ", {}, clear=False):
                result = get_storage_service(sa_path="/tmp/sa.json")
        mock_gcp.assert_called_once_with(sa_path="/tmp/sa.json")
        assert result is expected

    def test_gcs_alias_maps_to_gcp(self):
        with patch("common.services.cloud.storage.factory.GCPStorageService") as mock_gcp:
            expected = object()
            mock_gcp.return_value = expected
            with patch.dict("os.environ", {"STORAGE_PROVIDER": "gcs"}, clear=False):
                result = get_storage_service(sa_path=None)
        mock_gcp.assert_called_once_with(sa_path=None)
        assert result is expected

    def test_minio_provider_uses_minio_service(self):
        with patch("common.services.cloud.storage.factory.MinioStorageService.from_env") as mock_from_env:
            expected = object()
            mock_from_env.return_value = expected
            with patch.dict("os.environ", {"STORAGE_PROVIDER": "minio"}, clear=False):
                result = get_storage_service(sa_path="ignored")
        mock_from_env.assert_called_once_with()
        assert result is expected

    def test_invalid_provider_raises_value_error(self):
        with patch.dict("os.environ", {"STORAGE_PROVIDER": "azure"}, clear=False):
            with pytest.raises(ValueError, match="Unsupported STORAGE_PROVIDER"):
                get_storage_service()
