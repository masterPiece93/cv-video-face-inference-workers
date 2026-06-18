"""Settings module for Onboarding Verification Worker."""
import os
from typing import Final, Annotated, Optional, Literal

from pydantic import Field, AfterValidator, model_validator
from pydantic_settings import BaseSettings

from common.utils.setting_utilities import (
    CommonMeta,
    MinioSettings,
    StorageProviderField,
    Validators,
    validate_storage_provider_settings,
)


DEFAULT_SERVICE_NAME: Final[str] = "Onboarding Verification Worker"
_DEFAULT_ENV_FILE: Final[str] = os.path.join(os.path.dirname(__file__), "envs/.env")


class IngestProcessSettings(BaseSettings):
    flow_max_messages: Annotated[int, Field(gt=0)] = 10
    flow_max_lease_duration: Annotated[int, Field(gt=0)] = 300
    max_workers: Annotated[int, Field(gt=0)] = 4


class GcpSettings(BaseSettings):
    sa_path: Optional[str] = None
    project_id: str
    pubsub_ingestion_subscription: str
    pubsub_ingestion_topic: str
    pubsub_egestion_topic: str
    bucket: str
    dlq_topic: Optional[str] = None

    class Config(CommonMeta):
        env_prefix = "GCP__"


class Settings(BaseSettings):
    """Module Settings for Onboarding Verification Worker.

    Tag:[settings|core]
    """

    service_name: Annotated[
        str, AfterValidator(Validators.not_empty)
    ] = DEFAULT_SERVICE_NAME
    gcp: GcpSettings
    ingest: IngestProcessSettings = IngestProcessSettings()

    # Storage backend selection
    # gcp | gcs (default, Google Cloud Storage) | minio (S3-compatible)
    storage_provider: StorageProviderField = "gcp"
    minio: MinioSettings = Field(default_factory=MinioSettings)

    # Encoder for onboarding reference images (mandatory)
    encoder_backend: Literal["face_recognition", "fdetect"] = "face_recognition"
    fdetect_channel: Optional[str] = None
    encoding_model: Literal["hog", "cnn"] = "hog"
    num_jitters: Annotated[int, Field(ge=1)] = 1
    face_tolerance: Annotated[float, Field(gt=0.0, le=1.0)] = 0.6

    # Logging
    debug_mode: bool = False
    log_format: Literal["text", "json"] = "text"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @model_validator(mode="after")
    def _validate_storage_provider(self) -> "Settings":
        """Require MinIO settings when ``storage_provider=minio``."""
        validate_storage_provider_settings(self.storage_provider, self.minio)
        return self

    class Config(CommonMeta):
        env_file = _DEFAULT_ENV_FILE
        env_file_encoding = "utf-8"
        env_nested_delimiter = "__"
        extra = "ignore"
