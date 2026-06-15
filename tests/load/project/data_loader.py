"""Data loader — loads/generates test payloads for the load publisher.

Extensible: supports loading from local fixtures, GCS, or generating synthetic data.
Add new data sources by subclassing DataLoader.
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class DataLoader(ABC):
    """Abstract data loader — extend for custom data sources."""

    @abstractmethod
    def load_payloads(self) -> List[Dict[str, Any]]:
        """Load all available test payloads.

        Returns:
            List of message payload dicts.
        """
        ...

    def get_payload_generator(self, total_messages: int) -> Callable[[int], Dict[str, Any]]:
        """Return a callable(index) → payload for use with LoadPublisher.

        Cycles through loaded payloads if total_messages > available payloads.
        """
        payloads = self.load_payloads()
        if not payloads:
            raise ValueError("No payloads available from data source")

        def generator(index: int) -> Dict[str, Any]:
            base = payloads[index % len(payloads)].copy()
            # Ensure unique event_id per message
            base["event_id"] = f"loadtest-{uuid.uuid4().hex[:12]}"
            return base

        return generator


class FixturesDataLoader(DataLoader):
    """Load payloads from local JSON fixture files.

    Reads all .json files from the configured fixtures directory.
    Each file should contain either a single payload dict or a list of payloads.
    """

    def __init__(self, fixtures_dir: Path):
        self._dir = Path(fixtures_dir)

    def load_payloads(self) -> List[Dict[str, Any]]:
        payloads = []
        if not self._dir.exists():
            logger.warning(f"Fixtures directory not found: {self._dir}")
            return payloads

        for json_file in sorted(self._dir.glob("*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    payloads.extend(data)
                elif isinstance(data, dict):
                    payloads.append(data)
                logger.info(f"Loaded {json_file.name}: {len(payloads)} total payloads")
            except Exception as e:
                logger.warning(f"Failed to load {json_file}: {e}")

        return payloads


class SyntheticDataLoader(DataLoader):
    """Generate synthetic payloads for load testing.

    Useful when no real test data is available yet.
    Generates payloads matching the EncodingInputSchema.
    """

    def __init__(
        self,
        count: int = 100,
        bucket_name: str = "tdx-dev-external-sheet-candidature-records",
        org_id: str = "org-loadtest",
        org_alias: str = "loadtest-org",
    ):
        self._count = count
        self._bucket_name = bucket_name
        self._org_id = org_id
        self._org_alias = org_alias

    def load_payloads(self) -> List[Dict[str, Any]]:
        payloads = []
        for i in range(self._count):
            uid = f"cand-loadtest-{uuid.uuid4().hex[:8]}"
            payloads.append({
                "candidate_email": f"loadtest-{i}@example.com",
                "candidate_uid": uid,
                "org_id": self._org_id,
                "org_alias": self._org_alias,
                "bucket_name": self._bucket_name,
                "event_id": f"loadtest-{uuid.uuid4().hex[:12]}",
                "lookup_map": {
                    "profile": "profile",
                    "interviews": ["interview_1"],
                },
                "extra_info": {"load_test": True, "index": i},
            })
        return payloads


class GCSDataLoader(DataLoader):
    """Load payloads from a GCS bucket (future extensibility).

    Reads JSON payload files from a configured GCS prefix.
    """

    def __init__(self, bucket: str, prefix: str, sa_path: Optional[str] = None):
        self._bucket = bucket
        self._prefix = prefix
        self._sa_path = sa_path

    def load_payloads(self) -> List[Dict[str, Any]]:
        from google.cloud import storage

        if self._sa_path:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(self._sa_path)
            client = storage.Client(credentials=creds)
        else:
            client = storage.Client()

        bucket = client.bucket(self._bucket)
        blobs = bucket.list_blobs(prefix=self._prefix)

        payloads = []
        for blob in blobs:
            if blob.name.endswith(".json"):
                data = json.loads(blob.download_as_text())
                if isinstance(data, list):
                    payloads.extend(data)
                elif isinstance(data, dict):
                    payloads.append(data)

        logger.info(f"Loaded {len(payloads)} payloads from gs://{self._bucket}/{self._prefix}")
        return payloads


def get_data_loader(
    source: str,
    fixtures_dir: Optional[Path] = None,
    gcs_bucket: Optional[str] = None,
    gcs_prefix: Optional[str] = None,
    sa_path: Optional[str] = None,
    synthetic_count: int = 100,
    **kwargs,
) -> DataLoader:
    """Factory for data loaders.

    Args:
        source: "fixtures", "gcs", or "generator"
        fixtures_dir: Path for fixtures loader
        gcs_bucket: Bucket for GCS loader
        gcs_prefix: Prefix for GCS loader
        sa_path: SA path for GCS loader
        synthetic_count: Number of synthetic payloads to generate

    Returns:
        Configured DataLoader instance.
    """
    if source == "fixtures":
        return FixturesDataLoader(fixtures_dir or Path("tests/load/fixtures"))
    elif source == "gcs":
        if not gcs_bucket or not gcs_prefix:
            raise ValueError("gcs_bucket and gcs_prefix required for GCS data source")
        return GCSDataLoader(bucket=gcs_bucket, prefix=gcs_prefix, sa_path=sa_path)
    elif source == "generator":
        return SyntheticDataLoader(count=synthetic_count, **kwargs)
    else:
        raise ValueError(f"Unknown data source: {source}. Use: fixtures, gcs, generator")
