"""GCP Cloud Storage Service."""
import io
import json
import logging
from typing import Optional, Union

from google.cloud import storage

logger = logging.getLogger(__name__)

__all__ = ["GCPStorageService"]


class GCPStorageService:
    """Handles all GCP Cloud Storage operations.

    Provides download, upload, and listing operations used
    across all workers.
    """

    def __init__(self, sa_path: Optional[str] = None):
        """Initialize GCS client.

        Args:
            sa_path: Path to service account JSON. If None, uses ADC.
        """
        if sa_path:
            self._client = storage.Client.from_service_account_json(sa_path)
        else:
            self._client = storage.Client()

    def download_bytes(self, bucket_name: str, blob_path: str) -> Optional[io.BytesIO]:
        """Download a blob as BytesIO.

        Args:
            bucket_name: GCS bucket name.
            blob_path: Blob path within the bucket.

        Returns:
            BytesIO object or None on failure.
        """
        try:
            bucket = self._client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            data = io.BytesIO()
            blob.download_to_file(data)
            data.seek(0)
            logger.debug(f"Downloaded gs://{bucket_name}/{blob_path}")
            return data
        except Exception as e:
            logger.error(f"Failed to download gs://{bucket_name}/{blob_path}: {e}")
            return None

    def upload_bytes(
        self,
        data: Union[bytes, io.BytesIO],
        bucket_name: str,
        blob_path: str,
        content_type: str = "application/octet-stream",
    ) -> bool:
        """Upload bytes or BytesIO to a GCS blob.

        Args:
            data: Bytes or BytesIO to upload.
            bucket_name: GCS bucket name.
            blob_path: Destination blob path.
            content_type: MIME type of the content.

        Returns:
            True on success, False on failure.
        """
        try:
            bucket = self._client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            if isinstance(data, io.BytesIO):
                data.seek(0)
                blob.upload_from_file(data, content_type=content_type)
            else:
                blob.upload_from_string(data, content_type=content_type)
            logger.debug(f"Uploaded to gs://{bucket_name}/{blob_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload to gs://{bucket_name}/{blob_path}: {e}")
            return False

    def upload_json(
        self,
        data: Union[dict, list],
        bucket_name: str,
        blob_path: str,
    ) -> bool:
        """Upload a dict or list as JSON to a GCS blob.

        Args:
            data: Python dict or list to serialize.
            bucket_name: GCS bucket name.
            blob_path: Destination blob path.

        Returns:
            True on success, False on failure.
        """
        return self.upload_bytes(
            json.dumps(data, indent=2, default=str).encode("utf-8"),
            bucket_name,
            blob_path,
            content_type="application/json",
        )

    def download_json(
        self, bucket_name: str, blob_path: str
    ) -> Optional[Union[dict, list]]:
        """Download and parse a JSON blob.

        Args:
            bucket_name: GCS bucket name.
            blob_path: Blob path.

        Returns:
            Parsed Python object or None on failure.
        """
        data = self.download_bytes(bucket_name, blob_path)
        if data is None:
            return None
        try:
            return json.loads(data.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse JSON from gs://{bucket_name}/{blob_path}: {e}")
            return None

    def list_blobs(self, bucket_name: str, prefix: str) -> list[str]:
        """List all blob names under a prefix.

        Args:
            bucket_name: GCS bucket name.
            prefix: Prefix to filter blobs.

        Returns:
            List of blob names.
        """
        try:
            bucket = self._client.bucket(bucket_name)
            blobs = bucket.list_blobs(prefix=prefix)
            return [b.name for b in blobs]
        except Exception as e:
            logger.error(f"Failed to list blobs at gs://{bucket_name}/{prefix}: {e}")
            return []

    def download_bytes_from_url(self, url: str) -> Optional[io.BytesIO]:
        """Download image bytes from a signed HTTPS URL.

        Used when ``onboarding_reference_path`` is a signed URL rather than
        a GCS blob prefix.

        Args:
            url: Signed HTTPS URL pointing to a single image file.

        Returns:
            BytesIO of the downloaded content, or None on failure.
        """
        try:
            import requests

            response = requests.get(url, timeout=30)
            response.raise_for_status()
            logger.debug(f"Downloaded from signed URL: {url[:80]}...")
            return io.BytesIO(response.content)
        except Exception as e:
            logger.error(f"Failed to download from signed URL: {e}")
            return None

    def blob_exists(self, bucket_name: str, blob_path: str) -> bool:
        """Check if a blob exists.

        Args:
            bucket_name: GCS bucket name.
            blob_path: Blob path.

        Returns:
            True if exists, False otherwise.
        """
        try:
            bucket = self._client.bucket(bucket_name)
            return bucket.blob(blob_path).exists()
        except Exception as e:
            logger.error(f"Failed to check blob existence gs://{bucket_name}/{blob_path}: {e}")
            return False
