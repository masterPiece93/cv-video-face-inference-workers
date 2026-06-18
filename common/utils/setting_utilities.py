"""Setting utilities module for validators and mixins."""
import os
import json
import re
from typing import Annotated, Any, Literal, Optional
from collections.abc import Iterable, Sized

from pydantic import BeforeValidator, field_validator
from pydantic_settings import BaseSettings

__all__ = [
    "DecoderMixin",
    "CommonMeta",
    "Validators",
    "StorageProvider",
    "StorageProviderField",
    "MinioSettings",
    "validate_storage_provider_settings",
]


def _prepare_list_of_string(v: str) -> list[str]:
    """Prepare list of strings from comma-separated values."""
    return [str(x).strip() for x in v.split(",")]


class Validators:
    """Validators for various field types."""

    @staticmethod
    def not_empty(v: Any) -> Any:
        """Validate if any python ADT (Abstract Data Type) is not empty."""
        if not v:
            raise ValueError("Field cannot be empty")
        if isinstance(v, Sized) and len(v) == 0:
            raise ValueError("Field cannot be empty")
        return v

    @staticmethod
    def file_exists(v: str) -> str:
        """Validate that the file exists."""
        if not os.path.isfile(v):
            raise ValueError("File should exist")
        return v

    @staticmethod
    def is_json_file(v: str) -> bool:
        """Validate that the file is a valid JSON file."""
        try:
            with open(v, "r", encoding="utf-8") as f:
                json.load(f)
            return True
        except (json.JSONDecodeError, FileNotFoundError, IOError):
            return False

    @staticmethod
    def validate_server_address(v: str) -> str:
        """Validate server address format (ip:port)."""
        ip_port_pattern = re.compile(
            r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$|"
            r"^\[(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\]:[0-9]{1,5}$"
        )
        if not ip_port_pattern.match(v):
            raise ValueError("Invalid server address format. Expected 'ip:port'.")
        return v


class DecoderMixin:
    """Mixin for decoding comma-separated env values into lists."""

    @field_validator("*", mode="before")
    @classmethod
    def decode_csv_fields(cls, v: Any) -> Any:
        """Override in subclass to target specific fields."""
        return v


class CommonMeta:
    """Common Config class arguments for pydantic-settings models."""

    validate_default = True
    str_strip_whitespace = True


# ── Storage provider selection ────────────────────────────────────────────────

StorageProvider = Literal["gcp", "gcs", "minio"]
"""Supported object-storage backends. ``gcp``/``gcs`` are aliases for GCS."""


def _normalize_storage_provider(value: Any) -> Any:
    """Normalize a provider string to lowercase (e.g. ``"GCS"`` → ``"gcs"``)."""
    if isinstance(value, str):
        return value.strip().lower()
    return value


StorageProviderField = Annotated[
    StorageProvider, BeforeValidator(_normalize_storage_provider)
]
"""``StorageProvider`` that accepts any letter-casing (normalized to lowercase)."""


class MinioSettings(BaseSettings):
    """MinIO (S3-compatible) connection settings.

    These are only required when ``STORAGE_PROVIDER=minio``. They are configured
    via ``MINIO__`` prefixed environment variables, consistent with the
    ``GCP__`` nested settings convention used elsewhere::

        MINIO__ENDPOINT=localhost:9000
        MINIO__ACCESS_KEY=minioadmin
        MINIO__SECRET_KEY=minioadmin123
        MINIO__SECURE=false
        MINIO__REGION=us-east-1
    """

    endpoint: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    secure: bool = False
    region: str = "us-east-1"

    class Config(CommonMeta):
        env_prefix = "MINIO__"


def validate_storage_provider_settings(
    storage_provider: str, minio: "MinioSettings"
) -> None:
    """Ensure provider-specific settings are present for the chosen provider.

    GCS settings (project/bucket) are already enforced as required fields on the
    GCP settings model, so this only needs to guard the MinIO case.

    Args:
        storage_provider: The selected provider (``gcp`` / ``gcs`` / ``minio``).
        minio: The resolved MinIO settings model.

    Raises:
        ValueError: When ``storage_provider`` is ``minio`` but one or more
            required MinIO connection settings is missing.
    """
    if storage_provider == "minio":
        required = {
            "MINIO__ENDPOINT": minio.endpoint,
            "MINIO__ACCESS_KEY": minio.access_key,
            "MINIO__SECRET_KEY": minio.secret_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                "STORAGE_PROVIDER=minio requires the following settings: "
                + ", ".join(missing)
            )
