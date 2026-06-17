"""Factory for selecting cloud storage provider implementation."""
import os
from typing import Optional

from common.services.cloud.gcp.storage import GCPStorageService
from common.services.cloud.minio import MinioStorageService
from common.services.cloud.storage.base import StorageService


def get_storage_service(sa_path: Optional[str] = None) -> StorageService:
    """Build storage service from ``STORAGE_PROVIDER`` env var.

    Supported values:
    - ``gcp`` / ``gcs`` (default)
    - ``minio``
    """
    provider = os.getenv("STORAGE_PROVIDER", "gcp").strip().lower()
    if provider in {"gcp", "gcs"}:
        return GCPStorageService(sa_path=sa_path)
    if provider == "minio":
        return MinioStorageService.from_env()
    raise ValueError(
        "Unsupported STORAGE_PROVIDER: "
        f"{provider}. Expected one of: gcp, gcs, minio"
    )
