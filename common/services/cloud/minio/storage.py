"""MinIO (S3-compatible) Storage Service."""
import io
import json
import logging
import os
from typing import Optional, Union

import boto3

logger = logging.getLogger(__name__)

__all__ = ["MinioStorageService"]


class MinioStorageService:
    """Handles MinIO object storage operations via S3-compatible API."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool = False,
        region_name: str = "us-east-1",
    ):
        scheme = "https" if secure else "http"
        endpoint = endpoint.replace("http://", "").replace("https://", "")
        endpoint_url = f"{scheme}://{endpoint}"
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name,
        )

    @classmethod
    def from_env(cls) -> "MinioStorageService":
        """Build service from environment variables."""
        endpoint = os.environ["MINIO_ENDPOINT"]
        access_key = os.environ["MINIO_ACCESS_KEY"]
        secret_key = os.environ["MINIO_SECRET_KEY"]
        secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
        region_name = os.getenv("MINIO_REGION", "us-east-1")
        return cls(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region_name=region_name,
        )

    def download_bytes(self, bucket_name: str, blob_path: str) -> Optional[io.BytesIO]:
        try:
            response = self._client.get_object(Bucket=bucket_name, Key=blob_path)
            data = response["Body"].read()
            out = io.BytesIO(data)
            out.seek(0)
            logger.debug(f"Downloaded s3://{bucket_name}/{blob_path}")
            return out
        except Exception as e:
            logger.error(f"Failed to download s3://{bucket_name}/{blob_path}: {e}")
            return None

    def upload_bytes(
        self,
        data: Union[bytes, io.BytesIO],
        bucket_name: str,
        blob_path: str,
        content_type: str = "application/octet-stream",
    ) -> bool:
        try:
            if isinstance(data, io.BytesIO):
                data.seek(0)
                body = data.read()
            else:
                body = data

            self._client.put_object(
                Bucket=bucket_name,
                Key=blob_path,
                Body=body,
                ContentType=content_type,
            )
            logger.debug(f"Uploaded to s3://{bucket_name}/{blob_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload to s3://{bucket_name}/{blob_path}: {e}")
            return False

    def upload_json(
        self,
        data: Union[dict, list],
        bucket_name: str,
        blob_path: str,
    ) -> bool:
        return self.upload_bytes(
            json.dumps(data, indent=2, default=str).encode("utf-8"),
            bucket_name,
            blob_path,
            content_type="application/json",
        )

    def download_json(
        self, bucket_name: str, blob_path: str
    ) -> Optional[Union[dict, list]]:
        data = self.download_bytes(bucket_name, blob_path)
        if data is None:
            return None
        try:
            return json.loads(data.read().decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse JSON from s3://{bucket_name}/{blob_path}: {e}")
            return None

    def list_blobs(self, bucket_name: str, prefix: str) -> list[str]:
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket_name, Prefix=prefix)
            keys: list[str] = []
            for page in pages:
                for item in page.get("Contents", []):
                    key = item.get("Key")
                    if key:
                        keys.append(key)
            return keys
        except Exception as e:
            logger.error(f"Failed to list objects at s3://{bucket_name}/{prefix}: {e}")
            return []

    def download_bytes_from_url(self, url: str) -> Optional[io.BytesIO]:
        """Download image bytes from an HTTPS URL.

        Kept provider-agnostic to preserve existing onboarding flow where
        signed URLs may be used directly.
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
        try:
            self._client.head_object(Bucket=bucket_name, Key=blob_path)
            return True
        except Exception:
            return False
