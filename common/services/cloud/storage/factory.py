"""Factory for selecting cloud storage provider implementation."""
from typing import Optional

from common.services.cloud.gcp.storage import GCPStorageService
from common.services.cloud.storage.base import StorageService


def get_storage_service(
    provider: str,
    *,
    sa_path: Optional[str] = None,
    minio_endpoint: Optional[str] = None,
    minio_access_key: Optional[str] = None,
    minio_secret_key: Optional[str] = None,
    minio_secure: bool = False,
    minio_region: str = "us-east-1",
) -> StorageService:
    """Build a storage service for the requested provider.

    The provider name is supplied by the caller (resolved from the
    ``STORAGE_PROVIDER`` setting). This function deliberately does **not** read
    any environment variables itself — all configuration is passed in.

    Args:
        provider: Storage backend. One of ``gcp`` / ``gcs`` (Google Cloud
            Storage) or ``minio``.
        sa_path: GCS service-account path (``None`` → ADC). Used for gcp/gcs.
        minio_endpoint: MinIO ``host:port``. Required when ``provider='minio'``.
        minio_access_key: MinIO access key. Required when ``provider='minio'``.
        minio_secret_key: MinIO secret key. Required when ``provider='minio'``.
        minio_secure: Use HTTPS for the MinIO endpoint.
        minio_region: MinIO region (default ``us-east-1``).

    Returns:
        A provider-specific implementation of :class:`StorageService`.

    Raises:
        ValueError: If the provider is unsupported, or if MinIO is selected but
            required connection parameters are missing.
    """
    normalized = provider.strip().lower()

    if normalized in {"gcp", "gcs"}:
        return GCPStorageService(sa_path=sa_path)

    if normalized == "minio":
        if not (minio_endpoint and minio_access_key and minio_secret_key):
            raise ValueError(
                "MinIO storage requires endpoint, access_key and secret_key."
            )
        # Lazy import: only require boto3 when MinIO is actually selected.
        from common.services.cloud.minio import MinioStorageService

        return MinioStorageService(
            endpoint=minio_endpoint,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            secure=minio_secure,
            region_name=minio_region,
        )

    raise ValueError(
        f"Unsupported storage provider: {provider!r}. "
        "Expected one of: gcp, gcs, minio"
    )
