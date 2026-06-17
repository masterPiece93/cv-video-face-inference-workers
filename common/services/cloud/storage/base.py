"""Storage service protocol used by workers.

All storage providers (GCS, MinIO, etc.) should implement this shape.
"""
import io
from typing import Optional, Protocol, Union


class StorageService(Protocol):
    """Provider-agnostic storage operations required by worker pipelines."""

    def download_bytes(self, bucket_name: str, blob_path: str) -> Optional[io.BytesIO]:
        ...

    def upload_bytes(
        self,
        data: Union[bytes, io.BytesIO],
        bucket_name: str,
        blob_path: str,
        content_type: str = "application/octet-stream",
    ) -> bool:
        ...

    def upload_json(
        self,
        data: Union[dict, list],
        bucket_name: str,
        blob_path: str,
    ) -> bool:
        ...

    def download_json(self, bucket_name: str, blob_path: str) -> Optional[Union[dict, list]]:
        ...

    def list_blobs(self, bucket_name: str, prefix: str) -> list[str]:
        ...

    def download_bytes_from_url(self, url: str) -> Optional[io.BytesIO]:
        ...

    def blob_exists(self, bucket_name: str, blob_path: str) -> bool:
        ...
