"""Project-specific configuration for ECI workers load testing."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ECILoadTestSettings:
    """Configuration for ECI workers load testing.

    Loaded from a YAML/JSON config file or CLI arguments.
    """

    # GCP
    project_id: str = "tdx-is-dev-gta-01"
    topic_name: str = "face-encoding-ingestion"
    subscription_name: str = "face-encoding-ingestion-sub"
    sa_path: Optional[str] = None
    emulator_host: Optional[str] = "localhost:8085"

    # Worker
    worker_name: str = "face_encoding_worker"
    max_messages: int = 1
    encoder_backend: str = "fdetect"

    # Data source — extensible location for test payloads
    fixtures_dir: Path = field(
        default_factory=lambda: Path("tests/load/fixtures")
    )
    data_source: str = "fixtures"  # "fixtures" | "gcs" | "generator"

    # GCS data source (for future use)
    gcs_fixtures_bucket: Optional[str] = None
    gcs_fixtures_prefix: Optional[str] = None

    # Load profile defaults
    default_total_messages: int = 10
    default_duration_seconds: float = 60.0

    # Output
    results_dir: Path = field(
        default_factory=lambda: Path("tests/load/results")
    )

    @classmethod
    def from_yaml(cls, path: Path) -> "ECILoadTestSettings":
        """Load settings from a YAML config file."""
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_json(cls, path: Path) -> "ECILoadTestSettings":
        """Load settings from a JSON config file."""
        import json
        with open(path) as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
