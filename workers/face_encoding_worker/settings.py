"""Settings module for face encoding worker."""
import os
from typing import Final, Annotated, Optional, Literal

from pydantic import Field, AfterValidator
from pydantic_settings import BaseSettings

from common.utils.setting_utilities import Validators, CommonMeta


DEFAULT_SERVICE_NAME: Final[str] = "Face Encoding Worker"
# Default env file — used when Settings() is called without an explicit _env_file.
# main.py resolves the actual path from ENV_NAME and passes it as _env_file=...
_DEFAULT_ENV_FILE: Final[str] = os.path.join(os.path.dirname(__file__), "envs/.env")


class IngestProcessSettings(BaseSettings):
    """Control & Configuration Options for Ingestion Process."""

    flow_max_messages: Annotated[int, Field(gt=0)] = 10
    flow_max_lease_duration: Annotated[int, Field(gt=0)] = 120
    flow_max_duration_per_lease_extension: Annotated[int, Field(gt=0)] = 15
    max_workers: Annotated[int, Field(gt=0)] = 4


class GcpSettings(BaseSettings):
    """GCP Resources Specifications."""

    sa_path: Optional[str] = None  # None → use ADC
    project_id: str
    pubsub_ingestion_subscription: str
    pubsub_ingestion_topic: str
    pubsub_egestion_topic: str
    bucket: str
    dlq_topic: Optional[str] = None

    class Config(CommonMeta):
        env_prefix = "GCP__"


class Settings(BaseSettings):
    """Module Settings for Face Encoding Worker.

    - service_name (str): name of the service
    - gcp (GcpSettings): GCP resource settings
    - ingest (IngestProcessSettings): ingestion process settings
    - encoder_backend (str): "face_recognition" | "fdetect"
    - fdetect_channel (str): gRPC channel for fdetect (if backend=fdetect)
    - encoding_model (str): "hog" | "cnn" (face_recognition backend)
    - num_jitters (int): encoding jitters (accuracy vs speed)
    - frame_sample_rate (int): process every Nth frame
    - face_tolerance (float): duplicate face detection threshold
    - debug_mode (bool): enable debug logging
    - log_format (str): "text" | "json"
    - log_level (str): log level

    Tag:[settings|core]
    """

    service_name: Annotated[
        str, AfterValidator(Validators.not_empty)
    ] = DEFAULT_SERVICE_NAME
    gcp: GcpSettings
    ingest: IngestProcessSettings = IngestProcessSettings()

    # Encoder configuration
    encoder_backend: Literal["face_recognition", "fdetect"] = "face_recognition"
    fdetect_channel: Optional[str] = None
    encoding_model: Literal["hog", "cnn"] = "hog"
    num_jitters: Annotated[int, Field(ge=1)] = 1
    frame_sample_rate: Annotated[int, Field(ge=1)] = 30
    face_tolerance: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5

    # Logging
    debug_mode: bool = False
    log_format: Literal["text", "json"] = "text"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: Optional[str] = None  # path to log file; None = stdout only

    class Config(CommonMeta):
        env_file = _DEFAULT_ENV_FILE
        env_file_encoding = "utf-8"
        env_nested_delimiter = "__"
        extra = "ignore"
